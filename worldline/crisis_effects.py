"""Exactly-once, dated crisis consequences through authoritative subsystems.

Economic damage is submitted to the existing monthly shock/accounting model;
receipts explicitly distinguish delivery from realized population/capital loss.
No consequence creates money, bypasses credit, or invents battle outcomes.
"""
import copy
import hashlib

from .models import canonical_date
from . import crisis_state as cs

KINDS = ("economic_shock", "population_loss", "infrastructure_damage", "pressure_delta",
         "diplomacy", "funded_payment", "social_stability", "mitigation", "event_resolution", "trigger", "food_stock_loss")
HARMFUL = ("economic_shock", "population_loss", "infrastructure_damage", "food_stock_loss")
LOSS_KINDS = ("war_losses", "epidemic_losses", "disaster_losses")


def _payload_issues(world, kind, payload):
    errors = []
    if kind not in KINDS or not isinstance(payload, dict):
        return ["crisis: unknown/invalid consequence kind"]
    if kind in ("economic_shock", "population_loss", "infrastructure_damage", "food_stock_loss"):
        if payload.get("region") not in world.regions or not world.economy or payload.get("region") not in world.economy["regions"]:
            errors.append("crisis: consequence requires modeled region")
    if kind == "economic_shock":
        from .economy import SHOCKS
        if payload.get("kind") not in SHOCKS or not cs._number(payload.get("severity"), 0, 1) or type(payload.get("duration_days", 90)) is not int or payload.get("duration_days", 90) <= 0:
            errors.append("crisis: invalid economic shock consequence")
        if type(payload.get("one_off_effects", True)) is not bool:
            errors.append("crisis: one-off economic damage flag must be boolean")
    elif kind == "population_loss":
        if type(payload.get("count")) is not int or payload.get("count", -1) < 0 or payload.get("loss_kind", "disaster_losses") not in LOSS_KINDS:
            errors.append("crisis: invalid demographic consequence")
    elif kind == "infrastructure_damage":
        from .projects import INFRA
        if not cs._number(payload.get("fraction"), 0, 1) or payload.get("target") is not None and payload["target"] not in INFRA:
            errors.append("crisis: invalid infrastructure consequence")
    elif kind == "food_stock_loss":
        if not cs._number(payload.get("fraction"), 0, 1):
            errors.append("crisis: invalid food inventory destruction")
    elif kind == "pressure_delta":
        if payload.get("category") not in cs.CATEGORIES or not cs._number(payload.get("delta"), -1000, 1000):
            errors.append("crisis: invalid pressure consequence")
        if payload.get("country") is not None and payload["country"] not in world.countries or payload.get("region") is not None and payload["region"] not in world.regions:
            errors.append("crisis: unknown pressure consequence scope")
        pair = payload.get("pair")
        if pair is not None and (not isinstance(pair, list) or len(pair) != 2 or len(set(pair)) != 2 or any(c not in world.countries for c in pair)):
            errors.append("crisis: invalid pressure consequence pair")
    elif kind == "diplomacy":
        if payload.get("a") not in world.countries or payload.get("b") not in world.countries or payload.get("a") == payload.get("b"):
            errors.append("crisis: invalid diplomatic participants")
        if any(not cs._number(payload.get(f, 0), -10, 10) for f in ("trust_delta", "dispute_delta")):
            errors.append("crisis: invalid diplomatic delta")
        forbidden = {"treasury", "debt", "resources", "territory", "population", "gdp"}
        if set(payload) & forbidden:
            errors.append("crisis: diplomatic consequence cannot create resources")
    elif kind == "funded_payment":
        if payload.get("country") not in world.countries or type(payload.get("amount")) is not int or payload.get("amount", -1) < 0:
            errors.append("crisis: invalid funded payment")
        if type(payload.get("allow_emergency", False)) is not bool:
            errors.append("crisis: invalid emergency funding flag")
    elif kind == "social_stability":
        if payload.get("country") not in world.countries or not cs._number(payload.get("delta"), -100, 100):
            errors.append("crisis: invalid social stability consequence")
    elif kind == "mitigation":
        if payload.get("event_id") not in cs._state(world)["events"] or not cs._number(payload.get("strength"), 0, 1):
            errors.append("crisis: invalid event mitigation")
    elif kind == "event_resolution":
        if payload.get("event_id") not in cs._state(world)["events"] or payload.get("resolution_state", "resolved") not in cs.RESOLUTIONS or payload.get("resolution_state") == "ongoing":
            errors.append("crisis: invalid event resolution consequence")
    elif kind == "trigger":
        if not isinstance(payload.get("event_type", payload.get("kind")), str):
            errors.append("crisis: invalid subsequent event trigger")
    return errors


def _valid_payload(world, kind, payload):
    try:
        return _payload_issues(world, kind, payload)
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
        return [f"crisis: malformed consequence payload ({type(exc).__name__}: {exc})"]


def enqueue(world, event_id, kind, payload, due_date=None, idempotency_key=None):
    state = cs._state(world)
    if event_id not in state["events"]:
        raise ValueError("Unknown consequence parent event")
    errors = _valid_payload(world, kind, payload)
    if errors:
        raise ValueError("; ".join(errors))
    due_date = due_date or world.date
    canonical_date(due_date)
    if due_date < world.date:
        raise ValueError("Cannot schedule a consequence in the past")
    fingerprint = idempotency_key or hashlib.sha256(cs._json([event_id, kind, payload, due_date]).encode()).hexdigest()
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("Invalid consequence idempotency key")
    for prior in state["effects"].values():
        if prior.get("idempotency_key") == fingerprint:
            if prior["event_id"] != event_id or prior["kind"] != kind or prior["payload"] != payload or prior["due_date"] != due_date:
                raise ValueError("Consequence idempotency key conflicts with existing history")
            return prior
    event = state["events"][event_id]
    if not event["ongoing"] and kind in HARMFUL:
        raise ValueError("Cannot add repeated damage to a resolved event")
    effect_id = cs.next_id(world, "effect")
    effect = {"effect_id": effect_id, "event_id": event_id, "kind": kind, "payload": copy.deepcopy(payload),
              "created_at": world.date, "due_date": due_date, "applied": False,
              "applied_at": None, "receipt": None, "idempotency_key": fingerprint}
    state["effects"][effect_id] = effect
    field = "immediate_consequences" if due_date == world.date else "delayed_consequences"
    event[field].append(effect_id)
    state["outbox"].append({"system": "crisis", "kind": "effect_queued", "effect_id": effect_id,
        "event_id": event_id, "effect": copy.deepcopy(effect)})
    return effect


def _apply(world, effect):
    state = cs._state(world)
    event, kind, p = state["events"][effect["event_id"]], effect["kind"], effect["payload"]
    mitigation = 1 - event.get("response_mitigation", 0) if kind in HARMFUL else 1
    receipt = {"kind": kind, "date": world.date, "status": "applied", "mitigation_factor": mitigation}
    if kind == "economic_shock":
        from .economy import add_shock
        identifier = "crisis-shock:" + effect["effect_id"]
        severity = p["severity"] * mitigation
        if identifier in world.economy["shocks"]:
            raise ValueError("Crisis shock was already delivered without its receipt")
        add_shock(world, identifier, p["kind"], p["region"], severity, p.get("duration_days", 90))
        if not p.get("one_off_effects", True):
            # Crisis owns explicit dated death/asset receipts. Existing Economy
            # still calculates the continuing disruption, without repeating its
            # legacy implicit one-off casualties and destruction.
            world.economy["shocks"][identifier]["applied"] = True
            world.economy["shocks"][identifier]["one_off_owner"] = "crisis_pipeline"
        receipt.update(status="delivered_to_monthly_economy", shock_id=identifier, region=p["region"],
                       base_severity=p["severity"], effective_severity=severity,
                       one_off_owner="economy" if p.get("one_off_effects", True) else "crisis_pipeline",
                       realized_population_loss=0, realized_capital_damage=0)
    elif kind == "population_loss":
        r = world.economy["regions"][p["region"]]
        count = int(p["count"] * mitigation)
        available = r["demography"]["total_population"] - sum(r["pending_losses"].values())
        if count > max(0, available):
            raise ValueError("Crisis population losses exceed remaining population")
        loss = p.get("loss_kind", "disaster_losses")
        before = r["pending_losses"][loss]
        r["pending_losses"][loss] += count
        receipt.update(status="delivered_to_monthly_demography", region=p["region"], loss_kind=loss,
            base_count=p["count"], count=count, pending_before=before, pending_after=before + count)
    elif kind == "infrastructure_damage":
        from .projects import INFRA
        infra = world.regions[p["region"]].infrastructure
        targets = [p["target"]] if p.get("target") else list(INFRA)
        fraction = p["fraction"] * mitigation
        changes = {}
        for target in targets:
            before = infra.get(target, 0)
            infra[target] = before * (1 - fraction)
            changes[target] = {"before": before, "after": infra[target], "damage": before - infra[target]}
        receipt.update(region=p["region"], fraction=fraction, changes=changes)
    elif kind == "food_stock_loss":
        stocks = world.economy["regions"][p["region"]]["stocks"]["goods"]
        before = stocks.get("agriculture", 0)
        destroyed = before * p["fraction"] * mitigation
        stocks["agriculture"] = before - destroyed
        receipt.update(region=p["region"], opening=before, destroyed=destroyed, closing=stocks["agriculture"])
    elif kind == "pressure_delta":
        kwargs = {f: p[f] for f in ("country", "region", "pair", "key") if f in p}
        before = cs.pressure_value(world, p["category"], **kwargs)
        result = cs.adjust_pressure(world, p["category"], p["delta"], **kwargs,
            causes=p.get("causes", [{"event_id": event["event_id"], "effect_id": effect["effect_id"]}]))
        receipt.update(key=result["key"], before=before, after=result["value"])
    elif kind == "diplomacy":
        a, b = p["a"], p["b"]
        keys = [key for key, value in world.relations.items() if {value.get("a"), value.get("b")} == {a, b}]
        key = sorted(keys)[0] if keys else "-".join(sorted((a, b)))
        relation = world.relations.setdefault(key, {"a": a, "b": b, "status": "neutral"})
        before = copy.deepcopy(relation)
        for field in ("status", "war", "contact", "trade_disruption", "last_contact"):
            if field in p:
                relation[field] = copy.deepcopy(p[field])
        for field in ("trust", "dispute"):
            if field + "_delta" in p:
                lower, upper = (-1, 1) if field == "trust" else (0, 1)
                relation[field] = max(lower, min(upper, relation.get(field, 0) + p[field + "_delta"]))
        receipt.update(relation_key=key, before=before, after=copy.deepcopy(relation))
    elif kind == "funded_payment":
        from .credit import finance_amount
        country = world.countries[p["country"]]
        old_cash, old_debt = country.treasury, country.debt
        try:
            financing = finance_amount(world, p["country"], p["amount"],
                p.get("allow_emergency", False), p.get("instruments"), purpose=p.get("purpose", "crisis_response"))
            finance = world.economy["countries"][p["country"]]["finance"]
            finance["interim_net_cashflow"] = finance.get("interim_net_cashflow", 0) + country.treasury - old_cash
            finance["interim_debt_change"] = finance.get("interim_debt_change", 0) + country.debt - old_debt
            finance["reserves"] = country.treasury
            receipt.update(country=p["country"], amount=p["amount"], financing=financing)
        except ValueError as exc:
            # A failed intention creates no money and does not silently disappear.
            receipt.update(status="unfunded", country=p["country"], requested=p["amount"], reason=str(exc))
            state["triggers"].append({"event_id": event["event_id"], "kind": "payment_failure",
                "payload": {"country": p["country"], "requested": p["amount"], "reason": str(exc)}, "date": world.date})
    elif kind == "social_stability":
        country = world.countries[p["country"]]
        before = country.stability
        country.stability = max(0, min(100, before + p["delta"]))
        receipt.update(country=p["country"], before=before, after=country.stability)
    elif kind == "mitigation":
        target = state["events"][p["event_id"]]
        before = target.get("response_mitigation", 0)
        target["response_mitigation"] = max(before, p["strength"])
        target["updated_at"] = world.date
        receipt.update(target_event_id=p["event_id"], before=before, after=target["response_mitigation"], response=p.get("response"))
    elif kind == "event_resolution":
        target = cs.close_event(world, p["event_id"], p.get("resolution_state", "resolved"),
            p.get("end_date"), p.get("residual_pressure"))
        receipt.update(target_event_id=target["event_id"], resolution_state=target["resolution_state"])
    elif kind == "trigger":
        trigger = {"event_id": event["event_id"], "kind": p.get("event_type", p.get("kind")),
            "payload": copy.deepcopy(p), "date": world.date}
        state["triggers"].append(trigger)
        receipt["trigger"] = trigger
    return receipt


def apply_due(world):
    state = cs._state(world)
    result = []
    # Preserve creation order numerically rather than lexical 1,10,2 sorting.
    effects = sorted(state["effects"].values(), key=lambda e: (e["due_date"], e["created_at"], int(e["effect_id"].rsplit("-", 1)[-1])))
    for effect in effects:
        if effect["applied"] or effect["due_date"] > world.date:
            continue
        errors = validate_effect(world, effect["effect_id"], effect)
        if errors:
            raise ValueError("; ".join(errors))
        receipt = _apply(world, effect)
        effect.update(applied=True, applied_at=world.date, receipt=receipt)
        result.append({"system": "crisis", "kind": "effect_applied", "effect_id": effect["effect_id"],
            "event_id": effect["event_id"], "receipt": copy.deepcopy(receipt)})
    return result


def validate_effect(world, key, effect):
    if not isinstance(effect, dict):
        return ["crisis: invalid consequence " + key]
    errors = _valid_payload(world, effect.get("kind"), effect.get("payload"))
    state = cs._state(world)
    if effect.get("effect_id") != key or effect.get("event_id") not in state["events"]:
        errors.append("crisis: invalid consequence identity/reference " + key)
    if not isinstance(effect.get("idempotency_key"), str) or not effect.get("idempotency_key"):
        errors.append("crisis: missing consequence idempotency key " + key)
    if type(effect.get("applied")) is not bool:
        errors.append("crisis: invalid consequence receipt status " + key)
    for field in ("created_at", "due_date"):
        try:
            canonical_date(effect.get(field))
        except (ValueError, TypeError):
            errors.append("crisis: invalid consequence date " + key)
    if isinstance(effect.get("created_at"), str) and isinstance(effect.get("due_date"), str) and effect["due_date"] < effect["created_at"]:
        errors.append("crisis: consequence scheduled before creation " + key)
    if effect.get("applied"):
        if not isinstance(effect.get("receipt"), dict) or effect.get("applied_at") is None:
            errors.append("crisis: missing applied consequence receipt " + key)
        else:
            try:
                canonical_date(effect["applied_at"])
                if effect["applied_at"] < effect.get("due_date", "") or effect["applied_at"] > world.date:
                    errors.append("crisis: inconsistent application date " + key)
            except (ValueError, TypeError):
                errors.append("crisis: invalid consequence application date " + key)
        if effect.get("kind") == "economic_shock" and isinstance(effect.get("receipt"), dict):
            shock_id = effect["receipt"].get("shock_id")
            if shock_id not in world.economy["shocks"]:
                errors.append("crisis: lost delivered economic shock " + key)
            else:
                shock, payload, receipt = world.economy["shocks"][shock_id], effect["payload"], effect["receipt"]
                if (shock.get("kind") != payload.get("kind") or shock.get("region") != payload.get("region")
                    or shock.get("severity") != receipt.get("effective_severity") or shock.get("start_date") != effect.get("applied_at")):
                    errors.append("crisis: delivered shock facts conflict with receipt " + key)
                if not payload.get("one_off_effects", True) and (not shock.get("applied") or shock.get("one_off_owner") != "crisis_pipeline"):
                    errors.append("crisis: implicit shock damage would duplicate crisis pipeline " + key)
    elif effect.get("receipt") is not None or effect.get("applied_at") is not None:
        errors.append("crisis: unapplied consequence has an applied receipt " + key)
    return errors
