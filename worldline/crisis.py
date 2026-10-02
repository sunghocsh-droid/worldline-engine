"""Causal crisis lifecycle: risks, dated effects, reports and political responses.

No historical event calendar, combat result, interface, or external intelligence
service lives here. Structural risks originate in crisis_risk; all material
consequences go through crisis_effects and the existing economic accounting.
"""
import copy
import math
from datetime import timedelta

from .models import canonical_date
from . import crisis_state as cs, crisis_effects as ce, crisis_response as response

VERSION = "1"
CHAIN = ("border_incident", "diplomatic_crisis", "ultimatum", "partial_mobilization", "full_mobilization", "armed_clash", "state_of_war")
NATURAL = set(cs.HAZARDS) | {"typhoon"}
FOOD = {"bad_harvest", "food_shortage", "famine"}
FINANCIAL = {"fiscal_stress", "payment_failure", "financial_crisis", "recession"}
SOCIAL = {"petition", "protest", "strike", "riot", "tax_resistance", "local_uprising_precursor", "separatist_agitation", "autonomy_demand", "discrimination", "religious_restriction", "language_restriction", "cultural_restriction", "local_elite_conflict", "military_insubordination"}
DURATION = {"bad_harvest": 120, "food_shortage": 90, "famine": 120, "epidemic_introduction": 30, "epidemic": 120,
    "fiscal_stress": 180, "payment_failure": 180, "financial_crisis": 180, "recession": 180,
    "petition": 45, "protest": 60, "strike": 60, "riot": 60, "tax_resistance": 90}


def _state(world):
    return cs._state(world)


def _date_after(day, days):
    return (canonical_date(day) + timedelta(days=days)).isoformat()


def initialize(world, config=None, rng=None):
    """Explicit opt-in, taking one draw from the engine's saved game RNG."""
    from .guardrails import require_stabilization
    require_stabilization(world)
    if world.economy.get("version") != 3 or world.npc_state is None or world.npc_state.get("version") != 1:
        raise ValueError("Crisis Engine v1 requires Economy v3 and NPC Decision Engine v1")
    if getattr(world, "crisis_state", None) is not None:
        raise ValueError("Crisis Engine is already enabled")
    if rng is None or not hasattr(rng, "getrandbits"):
        raise ValueError("Crisis activation requires the saved game RNG")
    config = copy.deepcopy(config or {})
    if not isinstance(config, dict) or set(config) - {"regions", "borders", "pressures", "schedules", "player_countries", "settings"}:
        raise ValueError("Unknown or malformed crisis configuration")
    players = config.get("player_countries", [])
    if not isinstance(players, list) or len(set(players)) != len(players) or any(c not in world.countries for c in players):
        raise ValueError("Unknown or duplicate player country")
    candidate = copy.copy(world)
    candidate.schema_version = 4
    candidate.crisis_state = cs.initialize_state({"player_countries": players,
        "settings": config.get("settings", {}), "schedules": config.get("schedules", {})}, root_seed=0)
    if not isinstance(candidate.crisis_state["settings"], dict) or type(candidate.crisis_state["settings"].get("auto_enabled", True)) is not bool:
        raise ValueError("Crisis auto_enabled must be boolean")
    configured = config.get("regions", {})
    if not isinstance(configured, dict) or set(configured) - set(world.regions):
        raise ValueError("Unknown or malformed hazard region")
    for region in sorted(world.economy["regions"]):
        profile = copy.deepcopy(configured.get(region, {}))
        if not isinstance(profile, dict):
            raise ValueError("Hazard region profile must be an object")
        profile.pop("region", None)
        if not cs._number(profile.get("bad_harvest_rate", 0), 0, 20):
            raise ValueError("Invalid bad harvest annual rate")
        cs.configure_region(candidate, region, **profile)
    borders = config.get("borders", {})
    if not isinstance(borders, dict):
        raise ValueError("Crisis borders must be an object")
    for key, raw in sorted(borders.items()):
        raw = copy.deepcopy(raw)
        if not isinstance(raw, dict) or not {"countries", "regions"} <= set(raw):
            raise ValueError("Border requires two countries and their locations")
        raw.pop("border_id", None)
        cs.configure_border(candidate, key, **raw)
    pressures = config.get("pressures", {})
    if not isinstance(pressures, dict):
        raise ValueError("Crisis pressures must be an object")
    for key, raw in sorted(pressures.items()):
        if not isinstance(raw, dict) or not {"category", "value"} <= set(raw):
            raise ValueError("Initial pressure requires category and value")
        raw = {k: copy.deepcopy(v) for k, v in raw.items() if k in {"category", "value", "country", "region", "pair", "causes"}}
        cs.set_pressure(candidate, key=key, **raw)
    issues = cs.validate(candidate)
    if issues:
        raise ValueError("; ".join(issues))
    candidate.crisis_state["root_seed"] = rng.getrandbits(128)
    candidate.crisis_state["schedules"].setdefault("risk_next_due", world.date)
    candidate.crisis_state["schedules"].setdefault("risk_last_review", world.date)
    world.crisis_state, world.schema_version = candidate.crisis_state, 4
    for country in players:
        if country in world.npc_state["governments"]:
            world.npc_state["governments"][country]["autonomous"] = False
    return world.crisis_state


def drain_outbox(world):
    state = _state(world)
    result = state["outbox"]
    state["outbox"] = []
    return result


def _ratio(value, scale=50):
    value = max(0, float(value))
    return value / (value + scale)


def _capacity(world, region):
    country = world.regions[region].owner
    econ = world.economy["regions"][region]
    c = world.countries[country]
    government = world.npc_state["governments"].get(country, {})
    members = [a for a in [government.get("ruler"), *government.get("cabinet", [])] if a in world.npc_state["persons"] and world.npc_state["persons"][a].get("active", True)]
    administration = sum(world.npc_state["persons"][a]["abilities"]["administration"] for a in members) / len(members) if members else .3
    infra = world.regions[region].infrastructure
    roads = _ratio(infra.get("roads", 0) + infra.get("railways", 0))
    sanitation = _ratio(infra.get("sanitation", 0))
    structures = _ratio(infra.get("electricity", 0) + infra.get("communications", 0))
    density = econ["demography"]["total_population"] / max(1, _state(world)["regions"].get(region, {}).get("area_km2", 1000))
    density_factor = .7 + .6 * _ratio(density, 10) + .3 * econ["demography"]["urbanization"]
    finance = world.economy["countries"][country]["finance"]
    reserve_cover = _ratio(c.treasury, max(1, finance.get("total_revenue", max(1, c.gdp * .2 / 12))))
    resilience = 1 + .8 * administration + .6 * roads + .4 * structures + .3 * reserve_cover
    return {"country": country, "administration": administration, "transport": roads,
        "sanitation": sanitation, "health": econ["demography"]["health_level"], "building_resilience": structures,
        "density_factor": density_factor, "reserve_cover": reserve_cover, "resilience": resilience,
        "regional_vulnerability": _state(world)["regions"].get(region, {}).get("vulnerability", .5)}


def _damage_plan(world, event):
    region, kind, severity = event.get("region"), event["event_type"], event["severity"]
    if region is None or region not in world.economy["regions"]:
        return
    factors = _capacity(world, region)
    population = world.economy["regions"][region]["demography"]["total_population"]
    damage = severity * (.25 + .75 * factors["regional_vulnerability"]) * factors["density_factor"] / factors["resilience"]
    event["impact_plan"] = {"vulnerability_index": damage, "factors": factors,
                            "basis": "regional exposure and existing era-appropriate capacity"}
    event_id = event["event_id"]
    duration = DURATION.get(kind, 90)
    if kind in NATURAL:
        capital_rate = {"earthquake": .18, "fire": .12, "flood": .10, "storm": .07, "typhoon": .07,
                        "drought": 0, "extreme_cold": .025, "extreme_heat": .015}[kind]
        if capital_rate:
            ce.enqueue(world, event_id, "economic_shock", {"kind": "capital_destruction", "region": region,
                "severity": min(1, damage * capital_rate), "duration_days": 30})
        if kind in ("flood", "storm", "typhoon", "drought", "extreme_cold", "extreme_heat"):
            ce.enqueue(world, event_id, "economic_shock", {"kind": "drought" if kind == "drought" else "flood" if kind == "flood" else "bad_harvest",
                "region": region, "severity": min(1, damage * .65), "duration_days": duration, "one_off_effects": False})
            ce.enqueue(world, event_id, "food_stock_loss", {"region": region, "fraction": min(1, damage * .20)})
        mortality_rate = .004 if kind == "earthquake" else .001 if kind in ("fire", "flood", "storm", "typhoon") else .0005
        count = int(population * damage * mortality_rate / (1 + factors["health"] + factors["sanitation"]))
        ce.enqueue(world, event_id, "population_loss", {"region": region, "count": count, "loss_kind": "disaster_losses"}, due_date=_date_after(world.date, 14))
        ce.enqueue(world, event_id, "pressure_delta", {"category": "natural_disaster", "delta": damage * 25, "region": region})
    elif kind == "bad_harvest":
        ce.enqueue(world, event_id, "economic_shock", {"kind": "bad_harvest", "region": region, "severity": severity * .65, "duration_days": duration})
        ce.enqueue(world, event_id, "pressure_delta", {"category": "famine", "delta": severity * 8, "region": region})
    elif kind == "food_shortage":
        ce.enqueue(world, event_id, "pressure_delta", {"category": "famine", "delta": severity * 15, "region": region})
    elif kind == "famine":
        # Hunger is a supply outcome, not a second harvest lottery. The risk
        # module has already checked actual food security and supply capacity.
        food_gap = 1 - world.economy["regions"][region]["demography"]["food_security"]
        mortality = int(population * severity * food_gap * .012 / (1 + factors["transport"] + factors["health"]))
        ce.enqueue(world, event_id, "population_loss", {"region": region, "count": mortality, "loss_kind": "disaster_losses"}, due_date=_date_after(world.date, 14))
        ce.enqueue(world, event_id, "economic_shock", {"kind": "bad_harvest", "region": region, "severity": severity * food_gap * .2, "duration_days": duration})
        ce.enqueue(world, event_id, "pressure_delta", {"category": "civil_unrest", "delta": severity * food_gap * 25, "region": region})
    elif kind == "epidemic":
        sanitation, health = factors["sanitation"], factors["health"]
        burden = severity * factors["density_factor"] * (.5 + .5 * _state(world)["regions"].get(region, {}).get("mobility", .5)) / (1 + sanitation + health + .3 * factors["administration"])
        event["impact_plan"]["epidemic_burden"] = burden
        ce.enqueue(world, event_id, "economic_shock", {"kind": "epidemic", "region": region, "severity": min(1, burden * .35), "duration_days": duration, "one_off_effects": False}, due_date=_date_after(world.date, 7))
        ce.enqueue(world, event_id, "population_loss", {"region": region, "count": int(population * burden * .003), "loss_kind": "epidemic_losses"}, due_date=_date_after(world.date, 14))
        ce.enqueue(world, event_id, "pressure_delta", {"category": "epidemic", "delta": burden * 25, "region": region})
    elif kind == "strike":
        ce.enqueue(world, event_id, "economic_shock", {"kind": "blockade", "region": region, "severity": severity * .12, "duration_days": 30})
    elif kind == "riot":
        ce.enqueue(world, event_id, "infrastructure_damage", {"region": region, "fraction": severity * .008})
        ce.enqueue(world, event_id, "pressure_delta", {"category": "civil_unrest", "delta": severity * 12, "region": region})


def _observer_reports(world, event):
    event_id, region = event["event_id"], event.get("region")
    persons = world.npc_state["persons"]
    for actor, person in sorted(persons.items()):
        if not person.get("active", True) or person["country"] not in event["countries"]:
            continue
        government = world.npc_state["governments"].get(person["country"], {})
        local = response._at_incident_border(world, event, person)
        official = actor == government.get("ruler") or actor in government.get("cabinet", [])
        if not local and not official:
            continue
        explicit_audience = event.get("audience")
        if explicit_audience is not None and actor not in explicit_audience:
            continue
        if event["visibility"] == "secret" and not local and actor not in event["involved_actors"]:
            continue
        skill = person["abilities"]["intelligence"]
        exaggeration = 1 + .25 * person["biases"]["overconfidence"] if "military" in person["role"] or person["role"] in ("commander", "general", "army") else 1
        distortion = {}
        public_keys = {"hazard_family", "environmental_origin", "introduction", "agricultural_shock", "food_gap",
            "border_id", "military_outcomes_unresolved", "threat", "central_intent", "local_unilateral", "misinformation",
            "combat_result_pending", "political_demand", "warning_only", "government_coercive_response"}
        observed = {k: copy.deepcopy(v) for k, v in event["actual_facts"].items() if k in public_keys}
        if event["event_type"] in CHAIN:
            country = person["country"]
            known_diplomacy = world.npc_state["reports"][actor].get("diplomacy", {}).get("data", {})
            observed["enemy_strength"] = known_diplomacy.get("enemy_strength", "unverified")
            observed["own_strength"] = sum(a.personnel for a in world.armies.values() if a.owner == country)
            observed["information_basis"] = "local incident plus actor's prior intelligence; foreign national mobilization is undisclosed"
        # Incomplete threat reports may misread mobilization; this is explicit
        # news distortion, never fake battle casualties or altered world truth.
        if event["event_type"] in CHAIN:
            with cs.stream(world, f"report:{event['rng_key']}:{actor}") as rng:
                misread = rng.random() < (1 - skill) * .2
                if misread:
                    distortion["threat"] = min(1, event["actual_facts"].get("threat", event["severity"]) + .3)
                    distortion["misinterpretation"] = "reinforcement interpreted as preparation to attack"
        response.queue_report(world, event_id, actor, direct=local,
            source="local eyewitness" if local else "regional administrative report", accuracy=.55 + .4 * skill,
            confidence=.65 + .3 * skill, reliability=.85 if local else .7,
            exaggeration=exaggeration, intentional_distortion=distortion, observed_facts=observed)
        if local and event["event_type"] in CHAIN:
            central_intent = event["actual_facts"].get("central_intent", "await_authorization")
            ruler = government.get("ruler")
            response.queue_command(world, event_id, actor, {"intent": central_intent}, sender=ruler,
                delay=response.communication_delay(world, person["country"], person.get("region")))


def create_event(world, event_type, region=None, countries=(), **fields):
    """Create one causal occurrence and queue its consequences and observations."""
    state = _state(world)
    if region is not None and region not in state["regions"]:
        raise ValueError("Crisis event requires a configured hazard region")
    countries = list(countries) or ([world.regions[region].owner] if region is not None else [])
    if not countries:
        raise ValueError("A crisis event needs involved countries")
    event = cs.add_event(world, event_type, region, countries, **fields)
    event.setdefault("chain_level", CHAIN.index(event_type) if event_type in CHAIN else 0)
    event["escalation_level"] = max(event["escalation_level"], event["chain_level"])
    event.setdefault("next_review", _date_after(world.date, 7))
    event.setdefault("expected_end", _date_after(world.date, DURATION.get(event_type, 90)))
    _damage_plan(world, event)
    if event_type in CHAIN:
        for category, amount in (("border_tension", 5), ("diplomatic_crisis", 4), ("interstate_war", event["chain_level"] * 2)):
            if len(countries) >= 2:
                ce.enqueue(world, event["event_id"], "pressure_delta", {"category": category, "delta": amount, "pair": sorted(countries[:2])})
        if event_type == "state_of_war" and len(countries) >= 2:
            ce.enqueue(world, event["event_id"], "diplomacy", {"a": countries[0], "b": countries[1], "status": "war", "trust_delta": -.3, "dispute_delta": .2})
            # Trade interruption is non-lethal. Population/army war losses remain
            # exclusively for the later Military Engine.
            for r in sorted(world.regions):
                if world.regions[r].owner in countries:
                    ce.enqueue(world, event["event_id"], "economic_shock", {"kind": "blockade", "region": r, "severity": .2 * event["severity"], "duration_days": 90})
    if event_type in FINANCIAL:
        for country in countries:
            credit = world.economy["countries"][country]["credit"]
            ce.enqueue(world, event["event_id"], "pressure_delta", {"category": "civil_unrest", "delta": min(20, credit["default_risk"] * 10 + credit["arrears"] / max(1, credit["annual_revenue"]) * 10), "country": country, "region": region})
    if event_type in {"discrimination", "religious_restriction", "language_restriction", "cultural_restriction", "local_elite_conflict", "separatist_agitation", "autonomy_demand"}:
        ce.enqueue(world, event["event_id"], "pressure_delta", {"category": "ethnic_or_religious_tension", "delta": event["severity"] * 15, "region": region})
        ce.enqueue(world, event["event_id"], "pressure_delta", {"category": "separatism", "delta": event["severity"] * 10, "region": region})
    _observer_reports(world, event)
    # A decision appears when the player government receives the report, not at
    # the hidden regional occurrence. Direct injected pending choices remain a
    # trusted explicitly observed interface in response.schedule_pending.
    return event


def _close(world, event, resolution="resolved", cooldown_days=90):
    state = _state(world)
    if not event["ongoing"]:
        return
    residual = {key: p["value"] for key, p in state["pressures"].items()
                if p.get("region") == event.get("region") or p.get("country") in event["countries"] or p.get("pair") == sorted(event["countries"][:2])}
    cs.close_event(world, event["event_id"], resolution, residual_pressure=residual)
    if event.get("risk_family_key"):
        family, until = event["risk_family_key"], _date_after(world.date, cooldown_days)
        event["cooldown_until"] = until
        state["cooldowns"][family] = max(state["cooldowns"].get(family, until), until)


def _continuation(world, parent, event_type, *, causes=None, facts=None, severity=None, **extra):
    if any(_state(world)["events"][child]["event_type"] == event_type for child in parent["child_events"]):
        return None
    event = create_event(world, event_type, parent.get("region"), parent["countries"],
        parent_event=parent["event_id"], severity=parent["severity"] if severity is None else severity,
        causes=causes or [{"event_id": parent["event_id"], "mechanism": "unresolved conditions"}],
        actual_facts={**copy.deepcopy(parent["actual_facts"]), **copy.deepcopy(facts or {})},
        risk_family_key=parent.get("risk_family_key"), **extra)
    if event_type in CHAIN or event_type in ("riot", "payment_failure", "financial_crisis", "epidemic"):
        _close(world, parent, "escalated", cooldown_days=0)
    return event


def _military_presence(world, event):
    result = {}
    for country in event["countries"]:
        standing = sum(a.personnel for a in world.armies.values() if a.owner == country)
        mobilized = sum(r["labour_state"]["mobilized"] for rid, r in world.economy["regions"].items() if world.regions[rid].owner == country)
        result[country] = {"standing": standing, "mobilized": mobilized}
    return result


def _process_triggers(world):
    state, result = _state(world), []
    queued, state["triggers"] = state["triggers"], []
    grouped = {}
    for trigger in queued:
        grouped.setdefault(trigger["event_id"], []).append(trigger)
    for event_id, triggers in sorted(grouped.items()):
        parent = state["events"].get(event_id)
        if not parent or not parent["ongoing"]:
            continue
        relevant = [t for t in triggers if t["kind"] in ("local_unilateral_action", "response_escalation", "payment_failure")]
        if not relevant:
            continue
        local = any(t["kind"] == "local_unilateral_action" for t in relevant)
        response_choices = [t["payload"].get("response") for t in relevant]
        causes = [{"event_id": event_id, "trigger_kind": t["kind"], "actor": t["payload"].get("actor"),
                   "response": t["payload"].get("response"), "date": t["date"]} for t in relevant]
        if parent["event_type"] in CHAIN:
            presence = _military_presence(world, parent)
            level = parent.get("chain_level", 0)
            if local and not any(v["standing"] + v["mobilized"] > 0 for v in presence.values()):
                continue
            # Decisions supply the trigger, while a weekly hazard controls the
            # transition. They are not a single accidental-war lottery.
            parent.setdefault("escalation_drivers", []).append({"date": world.date, "local_unilateral": local,
                "choices": response_choices, "causes": causes,
                "misinformation": any(t["payload"].get("misinformation", False) for t in relevant)})
            if level < 2 and any(c == "ultimatum" for c in response_choices):
                child = _continuation(world, parent, CHAIN[level + 1], causes=causes,
                    facts={"political_demand": True, "local_unilateral": local, "misinformation": any(t["payload"].get("misinformation", False) for t in relevant)})
                if child:
                    result.append({"system": "crisis", "kind": "crisis_escalated", "parent_event": event_id, "event_id": child["event_id"], "mechanism": "authorized demand"})
            elif level < 4 and any(c == "mobilize" for c in response_choices) and any(v["mobilized"] > 0 for v in presence.values()):
                target_level = max(1, level + 1)
                child = _continuation(world, parent, CHAIN[target_level], causes=causes,
                    facts={"actual_mobilization": copy.deepcopy(presence), "local_unilateral": local})
                if child:
                    result.append({"system": "crisis", "kind": "crisis_escalated", "parent_event": event_id, "event_id": child["event_id"], "mechanism": "funded manpower mobilization"})
        elif parent["event_type"] in SOCIAL and any(c in ("limited_repression", "military_deployment") for c in response_choices):
            if parent["event_type"] != "riot":
                parent.setdefault("escalation_drivers", []).append({"date": world.date, "choices": response_choices,
                    "coercion": max(t["payload"].get("severity", 0) for t in relevant), "causes": causes})
                result.append({"system": "crisis", "kind": "social_coercion_pressure", "event_id": event_id,
                    "meaning": "coercion increases confrontation risk without predetermining a riot", "causes": causes})
        elif any(t["kind"] == "payment_failure" for t in relevant):
            country = relevant[0]["payload"].get("country", parent["countries"][0])
            child = _continuation(world, parent, "payment_failure", causes=causes,
                facts={"actual_payment_failure": True, "country": country})
            if child:
                result.append({"system": "crisis", "kind": "crisis_escalated", "parent_event": event_id, "event_id": child["event_id"], "mechanism": "failed accessible financing"})
    return result


def _border_review(world, event, days):
    level = event.get("chain_level", 0)
    if level >= len(CHAIN) - 1:
        return []
    pair = sorted(event["countries"][:2])
    pressure = max(cs.pressure_value(world, "border_tension", pair=pair), cs.pressure_value(world, "interstate_war", pair=pair)) / 100
    drivers = event.get("escalation_drivers", [])
    bold = any(v.get("local_unilateral") or any(c in ("ultimatum", "mobilize") for c in v.get("choices", [])) for v in drivers)
    if not bold:
        return []
    presence = _military_presence(world, event)
    if level >= 2 and not any(v["mobilized"] > 0 for v in presence.values()):
        return []
    if level >= 4 and not all(v["standing"] + v["mobilized"] > 0 for v in presence.values()):
        return []
    misinformation = any(v.get("misinformation") for v in drivers) or event["actual_facts"].get("misinformation", False)
    mobilization = sum(v["mobilized"] for v in presence.values()) / max(1, sum(world.countries[c].population for c in pair))
    annual_hazard = 2.5 * pressure ** 3 * (1 + min(1, mobilization * 50) + .4 * misinformation + .3 * any(v.get("local_unilateral") for v in drivers))
    probability = -math.expm1(-annual_hazard * days / 365)
    with cs.stream(world, f"escalation:{event['rng_key']}") as rng:
        draw = rng.random()
    if draw >= probability:
        return []
    child = _continuation(world, event, CHAIN[level + 1],
        causes=[{"event_id": event["event_id"], "mechanism": "unresolved pressure with actor actions", "hazard": annual_hazard, "probability": probability, "draw": draw}],
        facts={"misinformation": misinformation, "actual_mobilization": presence, "combat_result_pending": level >= 4})
    return [{"system": "crisis", "kind": "crisis_escalated", "parent_event": event["event_id"],
        "event_id": child["event_id"], "mechanism": "continuous escalation hazard"}] if child else []


def _social_review(world, event, days):
    drivers = [d for d in event.get("escalation_drivers", []) if d.get("coercion")]
    if not drivers or event["event_type"] == "riot":
        return []
    region = event.get("region")
    country = world.regions[region].owner
    pressure = max(cs.pressure_value(world, "civil_unrest", country=country, region=region),
                   cs.pressure_value(world, "labour_unrest", country=country, region=region)) / 100
    exposure = max(pressure, event["severity"] * .4)
    coercion = max(d["coercion"] for d in drivers)
    hazard = 3 * exposure ** 3 * (.5 + event["severity"]) * (.5 + coercion)
    probability = -math.expm1(-hazard * days / 365)
    with cs.stream(world, f"social-escalation:{event['rng_key']}") as rng:
        draw = rng.random()
    if draw >= probability:
        return []
    child = _continuation(world, event, "riot", severity=min(1, event["severity"] + .1),
        causes=[{"event_id": event["event_id"], "mechanism": "coercion in an existing collective grievance", "pressure": pressure,
                 "hazard": hazard, "probability": probability, "draw": draw}], facts={"government_coercive_response": True})
    return [{"system": "crisis", "kind": "crisis_escalated", "parent_event": event["event_id"],
             "event_id": child["event_id"], "mechanism": "continuous social confrontation hazard"}] if child else []


def _review_events(world):
    state, logs = _state(world), []
    for event_id, event in list(sorted(state["events"].items())):
        if not event["ongoing"] or event.get("next_review", world.date) > world.date:
            continue
        last_review = event.get("last_review", event["start_date"])
        days = max(1, (canonical_date(world.date) - canonical_date(last_review)).days)
        event.update(last_review=world.date, next_review=_date_after(world.date, 7))
        kind, region = event["event_type"], event.get("region")
        if kind in CHAIN:
            logs.extend(_border_review(world, event, days))
            if event["ongoing"] and world.date >= event["expected_end"] and kind != "state_of_war":
                calm = all(r["response"] in ("mediate", "withdraw", "negotiate") for r in event["responses"]) and bool(event["responses"])
                pressure = cs.pressure_value(world, "border_tension", pair=event["countries"][:2])
                if calm or pressure < 15:
                    _close(world, event, "contained", 180)
        elif kind == "epidemic_introduction":
            factors = _capacity(world, region)
            introduction_age = (canonical_date(world.date) - canonical_date(event["start_date"])).days
            spread_hazard = 4 * event["severity"] * (1 + factors["density_factor"] + factors["transport"]) / (1 + factors["sanitation"] + factors["health"] + 2 * event.get("response_mitigation", 0))
            with cs.stream(world, f"epidemic-spread:{event['rng_key']}") as rng:
                draw = rng.random()
            probability = -math.expm1(-spread_hazard * days / 365)
            if draw < probability:
                _continuation(world, event, "epidemic", causes=[{"event_id": event_id, "mechanism": "introduction spreads through existing connectivity", "hazard": spread_hazard, "probability": probability, "draw": draw}],
                    facts={"introduction_event": event_id, "sanitation": factors["sanitation"], "health": factors["health"]})
            elif introduction_age >= 30:
                _close(world, event, "contained", 60)
        elif kind in FINANCIAL:
            unresolved = any(world.economy["countries"][c]["credit"]["distress_state"] != "normal" for c in event["countries"])
            if not unresolved:
                _close(world, event, "resolved", 90)
            elif kind == "fiscal_stress" and any(world.economy["countries"][c]["credit"]["arrears"] for c in event["countries"]):
                _continuation(world, event, "payment_failure", facts={"arrears": {c: world.economy["countries"][c]["credit"]["arrears"] for c in event["countries"]}})
            elif kind == "payment_failure" and world.date >= event["expected_end"]:
                _continuation(world, event, "financial_crisis", facts={"market_panic_hook": True, "no_bank_model": True})
        elif kind in FOOD:
            security = world.economy["regions"][region]["demography"]["food_security"]
            if world.date >= event["expected_end"] and (kind == "bad_harvest" or security >= .9):
                _close(world, event, "resolved", 90)
        elif kind in SOCIAL:
            logs.extend(_social_review(world, event, days))
            if event["ongoing"] and world.date >= event["expected_end"]:
                _close(world, event, "exhausted", 90)
        elif world.date >= event["expected_end"]:
            _close(world, event, "resolved", 90)
    # Responses may have closed events through the pipeline without this helper;
    # preserve the same family cooldown and actual remaining pressure.
    for event in state["events"].values():
        if not event["ongoing"] and event.get("risk_family_key") and event["resolution_state"] not in ("escalated", "superseded"):
            family = event["risk_family_key"]
            until = event.get("cooldown_until", _date_after(event["end_date"], 90))
            state["cooldowns"][family] = max(state["cooldowns"].get(family, until), until)
    return logs


def _measured_aftermath(world):
    """Record measured response consequences once, after monthly realization.

    A queued or delivered loss is not a realized death. Matching the economic
    population account proves the loss reached the monthly accounting. There is
    no ruler evidence for an occurrence without a government's actual response.
    """
    state, logs = _state(world), []
    settlement = world.economy["last_settlement_date"]
    if state["schedules"].get("aftermath_last_settlement") == settlement:
        return logs
    state["schedules"]["aftermath_last_settlement"] = settlement
    for event_id, event in sorted(state["events"].items()):
        if not event["responses"]:
            continue
        effects = [state["effects"][key] for key in event["immediate_consequences"] + event["delayed_consequences"]]
        losses = [effect for effect in effects if effect["kind"] == "population_loss"]
        recorded = event.setdefault("measured_response_outcomes", {})
        for choice in event["responses"]:
            country = choice["country"]
            if country in recorded:
                continue
            account = world.population_accounts.get(country, {})
            local_losses = [loss for loss in losses if world.regions[loss["payload"]["region"]].owner == country]
            if local_losses:
                if not account or any(not loss["applied"] or not account["period_start"] <= loss["applied_at"] < account["period_end"] for loss in local_losses):
                    continue
                realized = sum(loss["receipt"]["count"] for loss in local_losses)
                unmitigated = sum(loss["receipt"]["base_count"] for loss in local_losses)
                kinds = {loss["payload"].get("loss_kind", "disaster_losses") for loss in local_losses}
                if sum(account[kind] for kind in kinds) < realized:
                    continue
                prevented = unmitigated - realized
                population = max(1, account["before"])
                performance = min(1, prevented / population * 100) if prevented else -min(1, realized / population * 100) if choice["response"] == "ignore" else 0
                evidence = {"realized_population_loss": realized, "prevented_population_loss": prevented,
                    "actual_loss_realized": True, "prevention_is_model_estimate": True,
                    "unmitigated_loss_plan": unmitigated, "loss_effects": [loss["effect_id"] for loss in local_losses],
                    "population_account": copy.deepcopy(account), "basis": "actual losses matched to monthly accounting; prevention is the model's unmitigated counterfactual"}
            elif not event["ongoing"]:
                if event["resolution_state"] == "escalated" and event["event_type"] in SOCIAL:
                    performance = -.25 * event["severity"]
                elif event["resolution_state"] in ("resolved", "contained"):
                    performance = .2 if choice["response"] in ("mediate", "withdraw", "negotiate", "concession", "quarantine", "relief") else 0
                else:
                    performance = 0
                evidence = {"resolution_state": event["resolution_state"], "child_events": list(event["child_events"]),
                    "basis": "observed event resolution, with no fabricated combat or financial outcome"}
            else:
                continue
            record = response.record_response_outcome(world, event_id, country, performance, facts=evidence)
            if record:
                recorded[country] = {"record_id": record["record_id"], "date": world.date, "facts": copy.deepcopy(record["facts"])}
                logs.append(record)
                logs.append({"system": "crisis", "kind": "response_outcome_measured", "event_id": event_id,
                             "country": country, "record_id": record["record_id"], "facts": copy.deepcopy(record["facts"])})
    return logs


def tick(world, tick_event, rng):
    """Cheap daily queues; weekly risk evaluations and relevant active reviews."""
    state = _state(world)
    logs = ce.apply_due(world)
    logs.extend(response.process_due(world, rng))
    logs.extend(ce.apply_due(world))
    logs.extend(_process_triggers(world))
    logs.extend(ce.apply_due(world))
    schedule = state["schedules"]
    risk_due = schedule.get("risk_next_due", world.date) <= world.date and state["settings"].get("auto_enabled", True)
    if risk_due:
        from . import crisis_risk
        elapsed = max(1, (canonical_date(world.date) - canonical_date(schedule.get("risk_last_review", world.date))).days)
        traces = crisis_risk.update_pressures(world, elapsed)
        if traces:
            logs.append({"system": "crisis", "kind": "pressure_review", "date": world.date, "causes": traces})
        schedule.update(risk_last_review=world.date, risk_next_due=_date_after(world.date, 7))
    logs.extend(_review_events(world))
    if risk_due:
        # Review closures/cooldowns before testing new roots for the same cause.
        crisis_risk.detect(world, create_event, elapsed)
    logs.extend(ce.apply_due(world))
    logs.extend(_measured_aftermath(world))
    logs.extend(response.deliver_due(world))
    logs.extend(drain_outbox(world))
    return logs
