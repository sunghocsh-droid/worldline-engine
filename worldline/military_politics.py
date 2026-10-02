from .incremental_audit import items as audit_items, json_check as audit_json
"""Military material constraints and received, institution-specific political choices.

This adapter never creates national support or substitutes a constant for willingness.
Quotes are read-only. Only explicit player approval or an authorized NPC's actual
bounded decision can supply a one-use replacement approval. Combat/economy formulas
remain owned by their existing systems.
"""
import copy
import hashlib
import json
import math

from .models import canonical_date
from .military_state import state, stream, emit
from . import npc, npc_state, npc_decision

VERSION = 1
CHOICES = ("replenish", "defer", "reduce_commitment")


def _json(value):
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                   allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _version(world):
    s = state(world)
    if "politics_version" in s and (type(s["politics_version"]) is not int or s["politics_version"] != VERSION):
        raise ValueError("Unsupported military politics version")
    if not isinstance(s.get("political_decisions", {}), dict):
        raise ValueError("Invalid military political decision storage")
    return s


def _request(world, recipient, manpower, duration_days=120, allow_emergency=True,
             instruments=None, transport_mode="land"):
    s = _version(world)
    if not isinstance(recipient, str) or recipient not in s["formations"] or type(manpower) is not int or manpower <= 0:
        raise ValueError("Replacement request needs an existing recipient and positive integer people")
    if type(duration_days) is not int or duration_days < 90:
        raise ValueError("Replacement request needs at least 90 days of actual training")
    if type(allow_emergency) is not bool or transport_mode not in ("land", "sea"):
        raise ValueError("Invalid replacement financing/transport policy")
    if instruments is not None and (not isinstance(instruments, (list, tuple)) or
            any(not isinstance(v, str) or not v for v in instruments) or len(instruments) != len(set(instruments))):
        raise ValueError("Replacement emergency instruments must be unique names")
    f = s["formations"][recipient]
    return {"actor": f["actor"], "country": s["actors"][f["actor"]]["country"], "recipient": recipient, "manpower": manpower,
            "origin_region": f["origin_region"], "duration_days": duration_days,
            "allow_emergency": allow_emergency, "instruments": list(instruments) if instruments is not None else None,
            "transport_mode": transport_mode}


def authority(world, actor, recipient, decision_actor):
    s = _version(world)
    if not isinstance(actor, str) or not isinstance(recipient, str) or actor not in s["actors"] or recipient not in s["formations"] or s["formations"][recipient]["actor"] != actor:
        return {"feasible": False, "reason": "recipient does not belong to this military actor"}
    country = s["actors"][actor]["country"]
    if country is None or world.npc_state is None:
        return {"feasible": False, "reason": "no national replacement policy authority"}
    person = world.npc_state["persons"].get(decision_actor, {})
    scopes = person.get("authority", [])
    permitted = (isinstance(scopes, list) and all(scope in npc_state.SCOPES for scope in scopes)
                 and npc._authority(world.npc_state, decision_actor, country, "mobilize"))
    return {"feasible": permitted, "scope": "national_replacement_policy" if permitted else None,
            "country": country, "decision_actor": decision_actor,
            "reason": "existing own ruler/cabinet mobilization authority" if permitted else "no authorized replacement policy scope"}


def burden_projection(world, actor):
    """Objective, referenced burdens; no hidden facts are passed to NPC scoring."""
    s = _version(world)
    if not isinstance(actor, str) or actor not in s["actors"]:
        raise ValueError("Unknown military political actor")
    a, p = s["actors"][actor], s["politics"][actor]
    country = a["country"]
    losses = [{"receipt_id": key, "date": receipt["date"], "operation_id": receipt["result"].get("operation_id"),
               "losses": {fid: copy.deepcopy(values) for fid, values in receipt["result"].get("casualties", {}).items()
                          if s["formations"].get(fid, {}).get("actor") == actor},
               "political_hooks": [copy.deepcopy(h) for h in receipt["result"].get("political_hooks", []) if h.get("actor") == actor]}
              for key, receipt in sorted(s["loss_receipts"].items())
              if receipt["result"].get("actor") == actor or any(s["formations"].get(fid, {}).get("actor") == actor
                  for fid in receipt["result"].get("casualties", {}))]
    commanders = [{"operation_id": key, "commander": op.get("commander"), "result_ids": list(op.get("results", []))}
                  for key, op in sorted(s["operations"].items()) if op["actor"] == actor and op.get("commander")]
    controls = [{"region": key, **copy.deepcopy(v)} for key, v in sorted(s["controls"].items())
                if v.get("controller") == actor or country and v.get("legal_owner") == country]
    groups = [{"id": key, "kind": group.get("kind"), "rivals": copy.deepcopy(group.get("rivals", [])),
               "interests": copy.deepcopy(group.get("interests", {})), "influence": group.get("influence")}
              for key, group in sorted((world.npc_state or {}).get("groups", {}).items()) if country and group["country"] == country]
    economic, extraction, hardships = {}, [], []
    if country is not None:
        e = world.economy["countries"][country]
        c = e["credit"]
        economic = {"country": country, "credit_state": c["distress_state"],
                    "arrears": c["arrears"], "debt_service": c.get("debt_service"),
                    "debt_service_ratio": c.get("debt_service_ratio"), "default_risk": c.get("default_risk"),
                    "military_payment_ratio": c.get("military_payment_ratio", c.get("hooks", {}).get("military_payment_ratio")),
                    "finance": copy.deepcopy(e["finance"])}
        extraction = [{"index": i, "receipt": copy.deepcopy(receipt)} for i, receipt in enumerate(c.get("financing_history", []))
                      if any(receipt.get("sources", receipt.get("financing_sources", {})).get(k, 0)
                             for k in ("requisition", "forced_loans", "emergency_taxation"))]
        for region, r in sorted(world.economy["regions"].items()):
            if world.regions[region].owner == country:
                hardships.append({"region": region, "food_security": r["demography"]["food_security"],
                    "labour": copy.deepcopy(r["labour"]), "mobilized": r["labour_state"]["mobilized"],
                    "standing_army": r["labour_state"]["standing_army"]})
    decisions = [copy.deepcopy(v["body"]) for _, v in sorted(s.get("political_decisions", {}).items())
                 if v.get("body", {}).get("request", {}).get("actor") == actor]
    return _json({"actor": actor, "as_of": world.date,
        "recorded_burdens": {k: p.get(k, 0) for k in ("casualty_burden", "mobilization_burden", "victory_prestige", "defeat_grievance", "war_exhaustion")},
        "casualty_and_failure_evidence": losses, "commander_prestige_evidence": commanders,
        "branch_rivalry": groups, "fiscal_strain": economic, "requisition_evidence": extraction,
        "actual_emergency_extraction": copy.deepcopy(world.economy["countries"][country]["credit"].get("emergency_used", {})) if country else {},
        "territorial_control": controls, "civilian_hardship": hardships,
        "institutional_choices": decisions, "assessment_status": "assessed" if decisions else "not_assessed",
        "limitations": ["No single popular support score", "Military control is distinct from legal territorial ownership",
                        "Commander prestige is outcome evidence, not an invented reputation bonus"]})


def willingness(world, actor):
    """Most recent actual choice by institution/person; never national support=.5."""
    s = _version(world)
    if not isinstance(actor, str) or actor not in s["actors"]:
        raise ValueError("Unknown military political actor")
    latest = {}
    for receipt in s.get("political_decisions", {}).values():
        body = receipt["body"]
        if body["request"]["actor"] != actor:
            continue
        institution = body["decision_actor"] or "explicit_player_or_scenario"
        order = (body["date"], body.get("sequence", 0), body["id"])
        if institution not in latest or order > latest[institution][0]:
            latest[institution] = (order, {"decision_actor": body["decision_actor"], "receipt_id": body["id"],
                "date": body["date"], "mode": body["mode"], "selected": body["selected"],
                "recipient": body["request"]["recipient"], "source_report_ids": body["source_report_ids"],
                "reason_trace": (body.get("decision") or {}).get("reason_trace", []),
                "consumed_by": receipt["consumed_by"]})
    choices = [v[1] for _, v in sorted(latest.items())]
    return _json({"version": VERSION, "actor": actor, "as_of": world.date,
        "status": "assessed" if any(c["selected"] is not None for c in choices) else "not_assessed",
        "institutional_choices": choices, "scope": "Replacement policy choices; not a full autonomous war planner",
        "basis": "Recorded player approvals or authorized, received-information NPC decisions; legacy .5 is ignored"})


def _source_digest(source):
    return _digest({k: source.get(k) for k in ("owner", "topic", "data", "metadata", "received_at")})


def _check_source_snapshot(source, decision_date):
    """A matching checksum does not make malformed reported information valid."""
    fields = {"topic", "data", "metadata", "facts", "source_id", "owner",
              "data_sha256", "received_at", "content_reference"}
    if not isinstance(source, dict) or set(source) != fields or any(
            not isinstance(source[k], str) or not source[k]
            for k in ("topic", "source_id", "owner", "data_sha256", "content_reference")):
        raise ValueError("Malformed political source snapshot")
    metadata = source["metadata"]
    if (not isinstance(metadata, dict)
            or set(metadata) != {"as_of", "source", "confidence", "reliability", "age_days"}
            or not isinstance(metadata["source"], str) or not metadata["source"]
            or type(metadata["age_days"]) is not int or metadata["age_days"] < 0):
        raise ValueError("Malformed political report metadata")
    for key in ("confidence", "reliability"):
        value = metadata[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Political report confidence/reliability must be between 0 and 1")
    observed = canonical_date(metadata["as_of"])
    if observed > decision_date or metadata["age_days"] != (decision_date - observed).days:
        raise ValueError("Political receipt cites undelivered/future information or invalid report age")
    received = source["received_at"]
    # Legacy direct observations lack a reception timestamp; unknown stays None.
    if received is not None and not observed <= canonical_date(received) <= decision_date:
        raise ValueError("Political receipt source was not received before decision")
    data, facts = source["data"], source["facts"]
    if not isinstance(data, dict) or not isinstance(facts, dict) or facts != data.get("facts", data):
        raise ValueError("Malformed political source report/facts")
    npc_state._validate_event_report(data, metadata["as_of"])
    if source["source_id"] != data.get("event_id", source["topic"]):
        raise ValueError("Political source ID does not match its report")


def _report(view, request, report_topic=None):
    candidates = []
    for topic, data in view["beliefs"].items():
        if report_topic is not None and topic != report_topic:
            continue
        if not isinstance(data, dict):
            continue
        facts = data.get("facts", data)
        # Legacy personal dispatches may use an ordinary prose 'facts' field.
        # They remain valid reports, but do not contain replacement forecasts.
        if not isinstance(facts, dict):
            continue
        given = facts.get("replacement_request", {})
        if not isinstance(given, dict) or given.get("recipient") != request["recipient"] or given.get("manpower") != request["manpower"]:
            continue
        if any(key in given and given[key] != request[key] for key in request):
            continue
        metadata = view["report_metadata"][topic]
        candidates.append((metadata["as_of"], topic, facts, data, metadata))
    if not candidates:
        return None
    _, topic, facts, data, metadata = max(candidates, key=lambda x: (x[0], x[1]))
    outcomes = facts.get("outcomes")
    if not isinstance(outcomes, dict) or set(outcomes) != set(CHOICES):
        return None
    for values in outcomes.values():
        if not isinstance(values, dict) or set(values) - set(npc_decision.DIMENSIONS) or any(
                type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in values.values()):
            raise ValueError("Replacement forecasts must use bounded existing NPC outcome dimensions")
    source = {"topic": topic, "data": copy.deepcopy(data), "metadata": copy.deepcopy(metadata),
            "facts": copy.deepcopy(facts), "source_id": data.get("event_id", topic),
            "owner": view["actor"], "data_sha256": _digest(data), "received_at": None}
    source["content_reference"] = _source_digest(source)
    return source


def replacement_quote(world, recipient, manpower, *, observer=None, duration_days=120,
                      allow_emergency=True, instruments=None, transport_mode="land", report_topic=None):
    """Training-now quote, later real equipment/transport requirements, separate beliefs.

    This is not a fixed forecast of a whole war. Equipment and transport snapshots
    can change before the trained cohort arrives; their actual schedulers recheck.
    """
    request = _request(world, recipient, manpower, duration_days, allow_emergency, instruments, transport_mode)
    s, f = _version(world), state(world)["formations"][recipient]
    actor, country, origin = request["actor"], s["actors"][request["actor"]]["country"], request["origin_region"]
    from .guardrails import can_fund_war
    from .military_logistics import route_quote, is_stationary, _access
    from .military_operations import _transport_capacity
    if country:
        finance = can_fund_war(world, country, manpower, region=origin, manpower=manpower,
            allow_emergency=allow_emergency, instruments=instruments)
    else:
        a = s["actors"][actor]
        finance = {"physical_feasibility": {"feasible": a["manpower_pool"] >= manpower},
            "normal_financial_feasibility": {"feasible": a["treasury"] >= manpower},
            "emergency_financial_feasibility": {"feasible": a["treasury"] >= manpower},
            "expected_consequences": {"actual_cost": manpower}, "execution_feasibility": False}
    live = f["manpower"] > 0 and is_stationary(world, f)
    route = route_quote(world, origin, f["location"], mode=transport_mode, actor=actor,
        purpose="movement", allow_hostile_destination=live and not _access(world, f["location"], actor))
    fleets = []
    for key, fleet in sorted(s["formations"].items()):
        if fleet["actor"] != actor or fleet["location"] != origin or not is_stationary(world, fleet) or fleet.get("current_order"):
            continue
        if any(key in v.get("formations", []) + v.get("escorts", []) and v.get("shipment") is not None for v in s["convoys"].values()):
            continue
        capacity = _transport_capacity(fleet)
        if capacity:
            fleets.append({"formation": key, "capacity": capacity})
    ready = [{"id": key, "cohort": item["cohort"], "manpower": s["formations"][item["cohort"]]["manpower"],
              "location": s["formations"][item["cohort"]]["location"]}
             for key, item in sorted(s["replacements"].items()) if item["recipient"] == recipient and item["status"] == "ready"]
    actual = {"as_of": world.date, "training_cost": manpower, "training_time_days": duration_days,
        "finance": finance, "equipment_reserve": copy.deepcopy(s["actors"][actor]["equipment_reserve"]),
        "ready_replacements": ready, "recipient_live_stationary": live, "transport": route,
        "sea_seats": sum(v["capacity"] for v in fleets), "available_fleets": fleets,
        "execution_limitations": ["Non-state replacement training adapter is unavailable"] if country is None else [],
        "cost_scope": "Training only; later equipment production, logistics, rations and upkeep incur separate real costs",
        "quote_is_guaranteed_future_delivery": False}
    perceived, willingness = None, {"status": "not_assessed", "observer": observer, "selected": None}
    if observer is not None:
        if observer not in (world.npc_state or {}).get("persons", {}) or world.npc_state["persons"][observer]["country"] != country:
            raise ValueError("Replacement observer must be in the actor's own polity")
        view = npc_state.actor_view(world.npc_state, observer, world.date)
        source = _report(view, request, report_topic)
        if source:
            # Reception is metadata of this actor's already delivered report,
            # never an accuracy/hidden-truth read. Older direct-report APIs may
            # not record a reception timestamp; represent that as unknown.
            source["received_at"] = world.npc_state["reports"][observer][source["topic"]].get("received_at")
            source["content_reference"] = _source_digest(source)
            declared = source["facts"].get("perceived", {})
            if not isinstance(declared, dict):
                raise ValueError("Perceived replacement financing must be a mapping")
            cost = declared.get("cost", source["facts"]["replacement_request"]["manpower"])
            if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                raise ValueError("Perceived replacement cost must be finite and nonnegative")
            financial = npc._perceived_finance(view, finance, cost)
            for k in ("cost", "risk", "cost_scale"):
                if k in declared:
                    v = declared[k]
                    if type(v) not in (int, float) or not math.isfinite(v) or v < 0 or k == "risk" and v > 1:
                        raise ValueError("Invalid perceived replacement cost/risk")
                    financial["perceived_state"][k] = v
            perceived = {"source": source, "financial": financial, "outcomes": copy.deepcopy(source["facts"]["outcomes"])}
            willingness["status"] = "awaiting_decision"
    return _json({"request": request, "actual": actual, "perceived": perceived,
                  "political_willingness": willingness})


def decide_replacement(world, receipt_id, recipient, manpower, *, decision_actor=None,
                       player_approval=False, report_topic=None, **kwargs):
    if not isinstance(receipt_id, str) or not receipt_id or type(player_approval) is not bool:
        raise ValueError("Political decision needs a stable ID and explicit approval mode")
    if player_approval and decision_actor is not None or not player_approval and decision_actor is None:
        raise ValueError("Separate explicit player approval from a named NPC's own decision")
    request = _request(world, recipient, manpower, **kwargs)
    s = _version(world)
    existing = s.get("political_decisions", {}).get(receipt_id)
    intent = {"request": request, "decision_actor": decision_actor, "player_approval": player_approval, "report_topic": report_topic}
    if existing:
        if existing["body"]["intent"] != intent:
            raise ValueError("Political decision ID reused with a different request")
        _check_receipt(world, receipt_id, existing)
        return copy.deepcopy(existing)
    permission = {"feasible": True, "scope": "explicit_player_or_scenario"} if player_approval else authority(world, request["actor"], recipient, decision_actor)
    if not permission["feasible"]:
        raise ValueError(permission["reason"])
    quote = replacement_quote(world, recipient, manpower, observer=decision_actor, report_topic=report_topic, **kwargs)
    source, decision = None, None
    selected, mode = "replenish", "explicit_player_approval"
    if not player_approval:
        perception = quote["perceived"]
        selected, mode = None, "not_assessed"
        if perception:
            source = perception["source"]
            view = npc_state.actor_view(world.npc_state, decision_actor, world.date)
            options = []
            for choice in CHOICES:
                option = {"id": choice, "kind": "military_expansion" if choice == "replenish" else "status_quo",
                    "tags": ["replacement", "military", "war", "war_result"] if choice == "replenish" else ["replacement", choice],
                    "outcomes": copy.deepcopy(perception["outcomes"][choice]),
                    # Actual own-polity scope was checked above; the pure NPC
                    # evaluator must not reinterpret 'all' or alias scopes.
                    "institutional_feasibility": {"feasible": True}, "required_authority": []}
                if choice == "replenish":
                    option["financial"] = copy.deepcopy(perception["financial"])
                options.append(option)
            with stream(world, "political-replacement:" + receipt_id + ":" + decision_actor) as rng:
                decision = npc_decision.decide(view, options, rng)
            selected, mode = decision["selected"], "authorized_npc_decision"
    perception_snapshot = None if quote["perceived"] is None else {
        "financial": quote["perceived"]["financial"], "outcomes": quote["perceived"]["outcomes"],
        "source_reference": quote["perceived"]["source"]["content_reference"]}
    body = _json({"version": VERSION, "id": receipt_id, "date": world.date, "sequence": s["revision"] + 1,
        "intent": intent, "request": request, "decision_actor": decision_actor,
        "mode": mode, "selected": selected, "permission": permission, "source_snapshot": source,
        "source_report_ids": [source["source_id"]] if source else [],
        "actual_quote": quote["actual"], "perceived": perception_snapshot, "decision": decision,
        "approved": selected == "replenish", "basis": "explicit political choice; financial risk alone never vetoes it"})
    receipt = {"body": body, "sha256": _digest(body), "consumed_by": None, "consumed_at": None}
    s.setdefault("political_decisions", {})[receipt_id] = receipt
    s["politics_version"] = VERSION
    s["revision"] += 1
    emit(world, "military_replacement_political_decision", receipt=receipt)
    return copy.deepcopy(receipt)


def _check_receipt(world, key, receipt):
    s = _version(world)
    if not isinstance(receipt, dict) or set(receipt) != {"body", "sha256", "consumed_by", "consumed_at"}:
        raise ValueError("Malformed military political receipt")
    b = receipt["body"]
    if type(b.get("version")) is not int or b["version"] != VERSION or b.get("id") != key or receipt["sha256"] != _digest(b):
        raise ValueError("Invalid military political receipt version/immutable digest")
    date = canonical_date(b["date"])
    if date > canonical_date(world.date) or b["request"] != b["intent"]["request"]:
        raise ValueError("Political receipt has a future or mismatched request")
    req = b["request"]
    checked = _request(world, req["recipient"], req["manpower"], req["duration_days"], req["allow_emergency"], req["instruments"], req["transport_mode"])
    if req != checked or b["decision_actor"] != b["intent"]["decision_actor"] or type(b["approved"]) is not bool or b["approved"] != (b["selected"] == "replenish") or type(b["sequence"]) is not int or b["sequence"] <= 0:
        raise ValueError("Political receipt has invalid actor/recipient/approval")
    if b["mode"] == "explicit_player_approval":
        if b["decision_actor"] is not None or not b["intent"]["player_approval"] or b["selected"] != "replenish" or b["source_snapshot"] is not None or b["decision"] is not None:
            raise ValueError("Invalid explicit player political approval")
    elif b["mode"] in ("authorized_npc_decision", "not_assessed"):
        person = (world.npc_state or {}).get("persons", {}).get(b["decision_actor"])
        country = req["country"]
        permission = b["permission"]
        if person is None or country not in world.countries or permission.get("country") != country or permission.get("scope") != "national_replacement_policy" or permission.get("decision_actor") != b["decision_actor"] or permission.get("feasible") is not True or b["intent"]["player_approval"]:
            raise ValueError("Political receipt names an unrelated NPC")
        if b["mode"] == "not_assessed":
            if b["selected"] is not None or b["decision"] is not None or b["source_snapshot"] is not None:
                raise ValueError("Unassessed political receipt cannot approve anything")
        else:
            src, dec = b["source_snapshot"], b["decision"]
            if not isinstance(src, dict) or not isinstance(dec, dict) or dec["actor"] != b["decision_actor"] or dec["selected"] != b["selected"] or dec["basis"]["decision_version"] != npc_decision.VERSION:
                raise ValueError("Political receipt lacks its actual NPC decision")
            if b["selected"] not in (*CHOICES, None) or dec["basis"]["date"] != b["date"]:
                raise ValueError("Political receipt has an unknown choice or decision date")
            evaluations = dec["evaluations"]
            if not isinstance(evaluations, list) or [v["option"] for v in evaluations] != sorted(CHOICES):
                raise ValueError("Political decision does not evaluate each choice exactly once")
            for evaluation in evaluations:
                trace, score = evaluation["reason_trace"], evaluation["score"]
                if not isinstance(trace, list) or type(evaluation["eligible"]) is not bool or type(evaluation["execution_eligible"]) is not bool or evaluation["execution_eligible"] != evaluation["eligible"] or type(score) not in (int, float) or not math.isfinite(score):
                    raise ValueError("Political evaluation has invalid eligibility or score")
                if any(type(t["contribution"]) not in (int, float) or not math.isfinite(t["contribution"]) for t in trace) or score != math.fsum(t["contribution"] for t in trace):
                    raise ValueError("Political evaluation score conflicts with its reason trace")
            eligible = [v for v in evaluations if v["eligible"]]
            chosen = max(eligible, key=lambda v: (v["score"], v["option"])) if eligible else None
            if dec["selected"] != (chosen["option"] if chosen else None) or dec["reason_trace"] != (chosen["reason_trace"] if chosen else []) or dec["perceived"] != (chosen["perceived"] if chosen else None):
                raise ValueError("Political decision selection conflicts with its actual evaluations")
            _check_source_snapshot(src, date)
            if b["source_report_ids"] != [src["source_id"]]:
                raise ValueError("Political receipt cites undelivered/future information")
            if src["owner"] != b["decision_actor"] or src["data_sha256"] != _digest(src["data"]):
                raise ValueError("Political receipt has a different observer or changed source report")
            if src["content_reference"] != _source_digest(src):
                raise ValueError("Political receipt has a changed dated report reference")
            if b["perceived"]["source_reference"] != src["content_reference"]:
                raise ValueError("Political receipt has a different perceived information source")
            if src["topic"] not in dec["basis"]["report_metadata"] or src["metadata"] != dec["basis"]["report_metadata"][src["topic"]]:
                raise ValueError("Political receipt report does not match the decision's information")
            facts = src["data"].get("facts", src["data"])
            given = facts.get("replacement_request", {})
            if given.get("recipient") != req["recipient"] or given.get("manpower") != req["manpower"] or facts != src["facts"]:
                raise ValueError("Political receipt has unrelated replacement evidence")
    else:
        raise ValueError("Unknown political decision mode")
    if receipt["consumed_by"] is None:
        if receipt["consumed_at"] is not None:
            raise ValueError("Unconsumed political approval has an execution date")
    else:
        if not b["approved"] or not isinstance(receipt["consumed_by"], str) or not receipt["consumed_by"] or canonical_date(receipt["consumed_at"]) < date or canonical_date(receipt["consumed_at"]) > canonical_date(world.date):
            raise ValueError("Invalid political approval consumption")
        if receipt["consumed_by"] not in s["replacements"]:
            raise ValueError("Consumed political approval has no actual replacement cohort")
        item = s["replacements"][receipt["consumed_by"]]
        if item["recipient"] != req["recipient"] or item["manpower"] != req["manpower"] or item["started_at"] != receipt["consumed_at"] or item.get("political_decision_id") != key:
            raise ValueError("Consumed approval names an unrelated replacement cohort")


def verify_approval(world, receipt_id, recipient, manpower, *, decision_actor=None,
                    duration_days=120, allow_emergency=True, instruments=None,
                    transport_mode="land"):
    """Read-only preflight, before actual recruitment, funding or training."""
    s = _version(world)
    receipt = s.get("political_decisions", {}).get(receipt_id)
    if receipt is None:
        raise ValueError("NPC replacement request has no political approval receipt")
    _check_receipt(world, receipt_id, receipt)
    b = receipt["body"]
    req = _request(world, recipient, manpower, duration_days, allow_emergency, instruments, transport_mode)
    if b["request"] != req or b["decision_actor"] != decision_actor:
        raise ValueError("Political approval does not match the actual replacement policy")
    if not b["approved"] or b["date"] != world.date:
        raise ValueError("Political approval is unapproved or stale; seek a current political choice")
    if decision_actor is not None and not authority(world, req["actor"], recipient, decision_actor)["feasible"]:
        raise ValueError("Replacement policy authority expired before execution")
    return copy.deepcopy(receipt)


def consume_approval(world, receipt_id, recipient, manpower, *, replacement_id,
                     decision_actor=None, duration_days=120, allow_emergency=True,
                     instruments=None, transport_mode="land"):
    """Use within the Engine transaction, after the actual cohort is created.

    A failed funding/training operation rolls back the whole transaction. Matching
    IDs are idempotent; approval cannot be re-used for another cohort or policy.
    """
    verify_approval(world, receipt_id, recipient, manpower, decision_actor=decision_actor,
        duration_days=duration_days, allow_emergency=allow_emergency, instruments=instruments, transport_mode=transport_mode)
    s = _version(world)
    receipt = s["political_decisions"][receipt_id]
    if not isinstance(replacement_id, str) or not replacement_id:
        raise ValueError("Political approval needs an actual replacement ID")
    if receipt["consumed_by"] is not None:
        if receipt["consumed_by"] == replacement_id:
            return copy.deepcopy(receipt)
        raise ValueError("Political approval was already consumed by another cohort")
    item = s["replacements"].get(replacement_id)
    if item is None or item["recipient"] != recipient or item["manpower"] != manpower or item["started_at"] != world.date or item.get("political_decision_id") != receipt_id:
        raise ValueError("Political approval must bind an actual matching cohort")
    receipt.update(consumed_by=replacement_id, consumed_at=world.date)
    s["revision"] += 1
    emit(world, "military_replacement_approval_consumed", receipt_id=receipt_id, replacement_id=replacement_id)
    return copy.deepcopy(receipt)


def validation_issues(world, previous=None):
    if getattr(world, "military_state", None) is None:
        return []
    try:
        s = _version(world)
        issues = []
        receipts = s.get("political_decisions", {})
        if receipts and s.get("politics_version") != VERSION:
            issues.append("military politics: receipts require an explicit supported version")
        for key, receipt in audit_items(receipts):
            try:
                _check_receipt(world, key, receipt)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                issues.append("military politics: invalid receipt " + str(key) + ": " + str(exc))
        if previous is not None and getattr(previous, "military_state", None):
            for key, old in previous.military_state.get("political_decisions", {}).items():
                new = receipts.get(key)
                if new is None or old["body"] != new["body"] or old["sha256"] != new["sha256"]:
                    issues.append("military politics: immutable decision changed " + key)
                elif old["consumed_by"] is not None and old != new:
                    issues.append("military politics: consumed approval changed " + key)
        return issues
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return ["military politics: " + str(exc)]
