from .incremental_audit import items as audit_items, json_check as audit_json
"""JSON crisis truth, causal history and independently saved risk streams.

This module knows no CLI, database, wall clock or historical event calendar.
Activation supplies one draw from the saved game RNG. Each risk location then
has its own saved stream so an unrelated hazard cannot shift all later draws.
"""
import copy
import hashlib
import json
import math
import random
from contextlib import contextmanager

from .models import canonical_date
from .runtime_queries import grouped_count

VERSION = 1
CATEGORIES = ("interstate_war", "diplomatic_crisis", "border_tension", "civil_unrest",
    "revolution", "coup", "separatism", "ethnic_or_religious_tension", "financial_crisis",
    "fiscal_crisis", "recession", "famine", "epidemic", "natural_disaster", "labour_unrest",
    "political_legitimacy", "military_insubordination")
HAZARDS = ("drought", "flood", "storm", "earthquake", "fire", "extreme_cold", "extreme_heat")
RESOLUTIONS = ("ongoing", "contained", "resolved", "escalated", "exhausted", "superseded")
EVENT_TYPES = ("border_incident", "diplomatic_crisis", "ultimatum", "partial_mobilization", "full_mobilization",
    "armed_clash", "state_of_war", "bad_harvest", "food_shortage", "famine", *HAZARDS,
    "epidemic_introduction", "epidemic", "fiscal_stress", "payment_failure", "financial_crisis", "recession",
    "petition", "protest", "strike", "riot", "tax_resistance", "local_uprising_precursor", "discrimination",
    "autonomy_demand", "religious_restriction", "cultural_restriction", "local_elite_conflict", "separatist_agitation",
    "local_unilateral_action", "casualty_report", "information_correction", "relief_response", "repression",
    "coup_warning", "revolutionary_agitation", "military_insubordination", "language_restriction", "typhoon", "border_tension",
    "patrol_incident", "interstate_crisis", "civil_unrest", "labour_unrest")


def _number(value, minimum=0, maximum=None):
    try:
        return (type(value) in (int, float) and math.isfinite(value) and value >= minimum
                and (maximum is None or value <= maximum))
    except OverflowError:
        return False


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _tuples(value):
    return tuple(_tuples(v) for v in value) if isinstance(value, list) else value


def _state(world):
    state = getattr(world, "crisis_state", None)
    if not isinstance(state, dict) or type(state.get("version")) is not int or state.get("version") != VERSION:
        raise ValueError("Crisis Engine v1 is not initialized")
    return state


def initialize_state(config=None, root_seed=0):
    if type(root_seed) is not int or root_seed < 0:
        raise ValueError("Crisis root seed must be a nonnegative integer")
    state = {"version": VERSION, "root_seed": root_seed, "events": {}, "pressures": {},
        "regions": {}, "borders": {}, "effects": {}, "messages": {}, "pending_decisions": {},
        "streams": {}, "schedules": {}, "cooldowns": {}, "triggers": [], "outbox": [],
        "next_ids": dict.fromkeys(("event", "effect", "message", "pending"), 1)}
    if config:
        if not isinstance(config, dict):
            raise ValueError("Crisis configuration must be an object")
        allowed = {"regions", "borders", "pressures", "schedules", "player_countries", "settings"}
        if set(config) - allowed:
            raise ValueError("Unknown crisis configuration field")
        state.update(copy.deepcopy(config))
    state.setdefault("player_countries", [])
    state.setdefault("settings", {})
    return state


def next_id(world, kind):
    state = _state(world)
    if kind not in state["next_ids"]:
        raise ValueError("Unknown crisis identifier kind")
    value = state["next_ids"][kind]
    if type(value) is not int or value < 1:
        raise ValueError("Invalid crisis identifier sequence")
    bucket = {"event": "events", "effect": "effects", "message": "messages", "pending": "pending_decisions"}[kind]
    if f"crisis-{kind}-{value}" in state[bucket]:
        raise ValueError("Crisis identifier sequence would overwrite history")
    state["next_ids"][kind] = value + 1
    return f"crisis-{kind}-{value}"


@contextmanager
def stream(world, key):
    """Use and persist a named RNG; failure leaves its old state unchanged."""
    state = _state(world)
    if not isinstance(key, str) or not key:
        raise ValueError("Crisis stream requires a nonempty key")
    rng = random.Random()
    if key in state["streams"]:
        rng.setstate(_tuples(state["streams"][key]))
    else:
        digest = hashlib.sha256(f"worldline-crisis-v1:{state['root_seed']}:{key}".encode()).digest()
        rng.seed(int.from_bytes(digest, "big"))
    yield rng
    state["streams"][key] = json.loads(json.dumps(rng.getstate()))


def configure_region(world, region, hazard_rates=None, vulnerability=.5, food_reserve_months=0,
                     institutional_vulnerability=.5, epidemic_introduction_rate=0,
                     mobility=.5, groups=None, **extra):
    state = _state(world)
    if region not in world.regions:
        raise ValueError("Unknown crisis region")
    rates = {**dict.fromkeys(HAZARDS, 0.0), **(hazard_rates or {})}
    if (set(rates) - set(HAZARDS) or any(not _number(v, 0, 20) for v in rates.values())
        or not _number(vulnerability, 0, 1) or not _number(institutional_vulnerability, 0, 1)
        or not _number(food_reserve_months, 0, 120) or not _number(epidemic_introduction_rate, 0, 20)
        or not _number(mobility, 0, 1)):
        raise ValueError("Invalid regional hazard or vulnerability profile")
    profile = {"region": region, "hazard_rates": rates, "vulnerability": vulnerability,
        "food_reserve_months": food_reserve_months, "institutional_vulnerability": institutional_vulnerability,
        "epidemic_introduction_rate": epidemic_introduction_rate, "mobility": mobility,
        "groups": copy.deepcopy(groups or {}), **copy.deepcopy(extra)}
    state["regions"][region] = profile
    return profile


def configure_border(world, border_id, countries, regions, tension=0, **extra):
    state = _state(world)
    countries, regions = list(countries), list(regions)
    if (not isinstance(border_id, str) or not border_id or len(countries) != 2
        or len(set(countries)) != 2 or any(c not in world.countries for c in countries)
        or len(regions) != 2 or any(r not in world.regions for r in regions)
        or any(world.regions[r].owner != c for c, r in zip(countries, regions))
        or not _number(tension, 0, 100)):
        raise ValueError("Invalid crisis border")
    border = {"border_id": border_id, "countries": countries, "regions": regions,
        "tension": tension, **copy.deepcopy(extra)}
    state["borders"][border_id] = border
    set_pressure(world, "border_tension", tension, pair=countries)
    return border


def pressure_key(category, country=None, region=None, pair=None, key=None):
    if key is not None:
        if not isinstance(key, str) or not key:
            raise ValueError("Invalid pressure key")
        return key
    scope = "pair:" + ":".join(sorted(pair)) if pair else "region:" + region if region else "country:" + country if country else "world"
    return category + ":" + scope


def set_pressure(world, category, value, country=None, region=None, pair=None, key=None, causes=None):
    state = _state(world)
    if (category not in CATEGORIES or not _number(value, 0, 100)
        or (country is not None and country not in world.countries)
        or (region is not None and region not in world.regions)
        or (pair is not None and (len(pair) != 2 or len(set(pair)) != 2 or any(c not in world.countries for c in pair)))):
        raise ValueError("Invalid crisis pressure")
    identifier = pressure_key(category, country, region, pair, key)
    previous = state["pressures"].get(identifier, {})
    record = {"key": identifier, "category": category, "value": float(value), "country": country,
        "region": region, "pair": sorted(pair) if pair else None, "updated_at": world.date,
        "causes": copy.deepcopy(causes if causes is not None else previous.get("causes", []))}
    state["pressures"][identifier] = record
    return record


def pressure_value(world, category, country=None, region=None, pair=None, key=None):
    return _state(world)["pressures"].get(pressure_key(category, country, region, pair, key), {}).get("value", 0.0)


def adjust_pressure(world, category, delta, country=None, region=None, pair=None, key=None, causes=None):
    if not _number(delta, -1000, 1000):
        raise ValueError("Invalid pressure change")
    value = pressure_value(world, category, country, region, pair, key)
    return set_pressure(world, category, min(100, max(0, value + delta)), country, region, pair, key, causes)


def add_event(world, event_type, region=None, countries=(), *, causes=None, actual_facts=None,
              severity=.5, parent_event=None, involved_actors=None, visibility="public",
              related_pressures=None, triggering_conditions=None, **extra):
    state = _state(world)
    countries = list(countries)
    actors = list(involved_actors or [])
    known_actors = set(world.countries) | set((world.npc_state or {}).get("persons", {})) | set((world.npc_state or {}).get("groups", {}))
    if (not isinstance(event_type, str) or event_type not in EVENT_TYPES and not event_type.startswith("custom:")
        or region is not None and region not in world.regions
        or any(c not in world.countries for c in countries) or len(set(countries)) != len(countries)
        or any(a not in known_actors for a in actors) or not _number(severity, 0, 1)
        or visibility not in ("public", "restricted", "secret", "local")
        or parent_event is not None and parent_event not in state["events"]
        or actual_facts is not None and not isinstance(actual_facts, dict)):
        raise ValueError("Invalid crisis event")
    forbidden = {"event_id", "event_type", "region", "countries", "start_date", "end_date", "parent_event", "child_events", "actual_facts"}
    if set(extra) & forbidden:
        raise ValueError("Cannot override event identity or creation facts")
    event_id = next_id(world, "event")
    # Stable local numbering avoids shifting event decision streams when an
    # unrelated event is inserted elsewhere in the world.
    scope = [event_type, region, sorted(countries)]
    sequence = 1 + grouped_count(state["events"], "crisis-event-scope",
        lambda e: (e["event_type"], e["region"], tuple(sorted(e["countries"]))),
        (event_type, region, tuple(sorted(countries))))
    rng_key = "event:" + hashlib.sha256(_json([scope, sequence]).encode()).hexdigest()
    event = {"event_id": event_id, "event_type": event_type, "start_date": world.date,
        "end_date": None, "ongoing": True, "region": region, "countries": countries,
        "involved_actors": actors, "causes": copy.deepcopy(causes or []),
        "triggering_conditions": copy.deepcopy(triggering_conditions or {}), "severity": float(severity),
        "visibility": visibility, "actual_facts": copy.deepcopy(actual_facts or {}),
        "immediate_consequences": [], "delayed_consequences": [], "related_pressures": copy.deepcopy(related_pressures or []),
        "parent_event": parent_event, "child_events": [], "escalation_level": 0,
        "resolution_state": "ongoing", "created_at": world.date, "updated_at": world.date,
        "requires_player_decision": False, "deadline": None, "available_responses": [],
        "consequences_if_ignored": [], "responses": [], "response_mitigation": 0.0,
        "rng_key": rng_key,
        **copy.deepcopy(extra)}
    _json(event)
    state["events"][event_id] = event
    if parent_event:
        state["events"][parent_event]["child_events"].append(event_id)
    state["outbox"].append({"system": "crisis", "kind": "event_started", "event_id": event_id, "event": copy.deepcopy(event)})
    return event


def close_event(world, event_id, resolution_state="resolved", end_date=None, residual_pressure=None):
    state = _state(world)
    if event_id not in state["events"] or resolution_state not in RESOLUTIONS or resolution_state == "ongoing":
        raise ValueError("Invalid crisis resolution")
    event = state["events"][event_id]
    end_date = end_date or world.date
    canonical_date(end_date)
    if end_date < event["start_date"] or end_date > world.date:
        raise ValueError("Invalid event end date")
    if not event["ongoing"]:
        if event["end_date"] == end_date and event["resolution_state"] == resolution_state:
            return event
        raise ValueError("Resolved event history cannot be rewritten")
    event.update(end_date=end_date, ongoing=False, resolution_state=resolution_state, updated_at=world.date)
    if residual_pressure is not None:
        event["residual_pressure"] = copy.deepcopy(residual_pressure)
    state["outbox"].append({"system": "crisis", "kind": "event_resolved", "event_id": event_id, "event": copy.deepcopy(event)})
    return event


def _validate_state(world, previous=None):
    """Check save shape, referenced truth, causal DAG and monotonic receipts."""
    state = getattr(world, "crisis_state", None)
    if state is None:
        return []
    errors = []
    if not isinstance(state, dict) or type(state.get("version")) is not int or state.get("version") != VERSION:
        return ["crisis: unsupported state version"]
    mappings = ("events", "pressures", "regions", "borders", "effects", "messages", "pending_decisions", "streams", "schedules", "cooldowns", "next_ids")
    for name in mappings:
        if not isinstance(state.get(name), dict):
            errors.append("crisis: missing/invalid " + name)
    if any(not isinstance(state.get(n), list) for n in ("triggers", "outbox")):
        errors.append("crisis: invalid consequence queue")
    if not _number(state.get("root_seed")) or type(state.get("root_seed")) is not int:
        errors.append("crisis: invalid root seed")
    if errors:
        return errors
    if any(type(state["next_ids"].get(k)) is not int or state["next_ids"].get(k, 0) < 1 for k in ("event", "effect", "message", "pending")):
        errors.append("crisis: invalid identifier sequence")
    else:
        for kind, bucket in (("event", "events"), ("effect", "effects"), ("message", "messages"), ("pending", "pending_decisions")):
            prefix = f"crisis-{kind}-"
            used = [int(k[len(prefix):]) for k in state[bucket] if isinstance(k, str) and k.startswith(prefix) and k[len(prefix):].isdigit()]
            if used and state["next_ids"][kind] <= max(used):
                errors.append("crisis: identifier sequence would overwrite history " + kind)
    try:
        audit_json(state)
    except (ValueError, TypeError):
        errors.append("crisis: state must contain finite JSON values")
    today = world.date
    if not isinstance(state.get("settings", {}), dict) or type(state.get("settings", {}).get("auto_enabled", True)) is not bool:
        errors.append("crisis: invalid scheduler settings")
    for key, value in audit_items(state["schedules"]):
        try:
            if not isinstance(key, str) or not key:
                raise ValueError("key")
            canonical_date(value)
            if key in ("risk_last_review", "aftermath_last_settlement") and value > today:
                errors.append("crisis: future completed scheduler review " + key)
        except (ValueError, TypeError):
            errors.append("crisis: invalid saved schedule " + str(key))
    for key, value in audit_items(state["cooldowns"]):
        try:
            if not isinstance(key, str) or not key:
                raise ValueError("key")
            canonical_date(value)
        except (ValueError, TypeError):
            errors.append("crisis: invalid saved event cooldown " + str(key))
    components = state.get("risk_components", {})
    if not isinstance(components, dict):
        errors.append("crisis: invalid structural risk components")
    else:
        for key, item in audit_items(components):
            if (key not in state["pressures"] or not isinstance(item, dict)
                or not _number(item.get("target"), 0, 100) or not isinstance(item.get("causes"), list)):
                errors.append("crisis: invalid structural risk component " + str(key))
                continue
            try:
                canonical_date(item.get("date"))
                if item["date"] > today:
                    errors.append("crisis: future structural risk component " + str(key))
            except (ValueError, TypeError):
                errors.append("crisis: invalid structural risk component date " + str(key))
    actors = set(world.countries) | set((world.npc_state or {}).get("persons", {})) | set((world.npc_state or {}).get("groups", {}))
    for key, border in audit_items(state["borders"]):
        if not isinstance(border, dict):
            errors.append("crisis: invalid border " + str(key))
            continue
        countries, regions = border.get("countries"), border.get("regions")
        if (not isinstance(countries, list) or len(countries) != 2 or any(not isinstance(c, str) or c not in world.countries for c in countries)
            or len(set(countries)) != 2 or not isinstance(regions, list) or len(regions) != 2
            or any(not isinstance(r, str) or r not in world.regions for r in regions)):
            errors.append("crisis: invalid border participant/location " + str(key))
        elif any(world.regions[r].owner != c for c, r in zip(countries, regions)):
            errors.append("crisis: border ownership mismatch " + str(key))
        if not _number(border.get("tension", 0), 0, 100):
            errors.append("crisis: invalid saved border tension " + str(key))
    for key, profile in audit_items(state["regions"]):
        if key not in world.regions or not isinstance(profile, dict):
            errors.append("crisis: unknown/invalid regional profile " + key)
            continue
        rates = profile.get("hazard_rates", {})
        if not isinstance(rates, dict) or set(rates) - set(HAZARDS) or any(not _number(v, 0, 20) for v in rates.values()):
            errors.append("crisis: invalid hazard rates " + key)
        for field in ("vulnerability", "institutional_vulnerability", "mobility"):
            if not _number(profile.get(field, .5), 0, 1):
                errors.append("crisis: invalid regional " + field)
        if (not _number(profile.get("food_reserve_months", 0), 0, 120)
            or not _number(profile.get("epidemic_introduction_rate", 0), 0, 20)
            or not _number(profile.get("bad_harvest_rate", 0), 0, 20)):
            errors.append("crisis: invalid regional reserve/introduction rate")
        if "area_km2" in profile and not _number(profile["area_km2"], .001):
            errors.append("crisis: invalid regional area " + key)
        groups = profile.get("groups", {})
        if not isinstance(groups, dict) or any(not isinstance(group, str) or not group for group in groups):
            errors.append("crisis: invalid regional group metadata " + key)
        else:
            for group, item in audit_items(groups):
                grievance = item.get("grievance", 0) if isinstance(item, dict) else item
                if not _number(grievance, 0, 1):
                    errors.append("crisis: invalid group grievance " + key + ":" + group)
    for key, p in audit_items(state["pressures"]):
        if not isinstance(p, dict) or p.get("key") != key or p.get("category") not in CATEGORIES or not _number(p.get("value"), 0, 100):
            errors.append("crisis: invalid pressure " + key)
            continue
        if p.get("country") is not None and p["country"] not in world.countries or p.get("region") is not None and p["region"] not in world.regions:
            errors.append("crisis: unknown pressure scope " + key)
        if p.get("pair") is not None and (not isinstance(p["pair"], list) or len(p["pair"]) != 2 or len(set(p["pair"])) != 2 or any(c not in world.countries for c in p["pair"])):
            errors.append("crisis: invalid pressure pair " + key)
    events = state["events"]
    for key, event in audit_items(events):
        if not isinstance(event, dict):
            errors.append("crisis: invalid event " + key)
            continue
        if event.get("event_id") != key or not isinstance(event.get("event_type"), str) or event.get("event_type") not in EVENT_TYPES and not event.get("event_type", "").startswith("custom:"):
            errors.append("crisis: invalid event identity " + key)
        countries, participants = event.get("countries"), event.get("involved_actors")
        if (event.get("region") is not None and (not isinstance(event["region"], str) or event["region"] not in world.regions)
            or not isinstance(countries, list) or any(not isinstance(c, str) or c not in world.countries for c in (countries or []) if isinstance(countries, list))
            or not isinstance(participants, list) or any(not isinstance(a, str) or a not in actors for a in (participants or []) if isinstance(participants, list))):
            errors.append("crisis: unknown event region/country/actor " + key)
        for field in ("start_date", "created_at", "updated_at"):
            try:
                canonical_date(event.get(field))
                if event[field] > today:
                    errors.append("crisis: future event history " + key)
            except (ValueError, TypeError):
                errors.append("crisis: invalid event date " + key)
        for field in ("next_review", "expected_end", "last_review", "deadline"):
            value = event.get(field)
            if value is None:
                continue
            try:
                canonical_date(value)
                if value < event.get("start_date", "") or field == "last_review" and value > today:
                    errors.append("crisis: invalid lifecycle chronology " + key + ":" + field)
            except (ValueError, TypeError):
                errors.append("crisis: invalid lifecycle date " + key + ":" + field)
        if event.get("end_date") is not None:
            try:
                canonical_date(event["end_date"])
                if event["end_date"] < event.get("start_date", "") or event["end_date"] > today:
                    errors.append("crisis: event end precedes start/future " + key)
            except (ValueError, TypeError):
                errors.append("crisis: invalid event end " + key)
        if type(event.get("ongoing")) is not bool or event.get("ongoing") != (event.get("end_date") is None) or event.get("resolution_state") not in RESOLUTIONS:
            errors.append("crisis: inconsistent event resolution " + key)
        if not _number(event.get("severity"), 0, 1) or not _number(event.get("response_mitigation", 0), 0, 1) or type(event.get("escalation_level")) is not int or event.get("escalation_level", -1) < 0:
            errors.append("crisis: invalid severity/escalation " + key)
        if not isinstance(event.get("actual_facts"), dict):
            errors.append("crisis: invalid actual facts " + key)
        outcomes = event.get("measured_response_outcomes", {})
        if not isinstance(outcomes, dict):
            errors.append("crisis: invalid measured response outcomes " + key)
        else:
            for country, measured in audit_items(outcomes):
                errors.extend(_validate_measured_outcome(world, event, country, measured))
        parent, children = event.get("parent_event"), event.get("child_events")
        if parent is not None and (not isinstance(parent, str) or parent not in events or not isinstance(events[parent], dict) or key not in events[parent].get("child_events", [])):
            errors.append("crisis: broken parent reference " + key)
        if not isinstance(children, list) or any(not isinstance(c, str) for c in (children or []) if isinstance(children, list)):
            errors.append("crisis: duplicate/invalid child events " + key)
        elif len(set(children)) != len(children):
            errors.append("crisis: duplicate/invalid child events " + key)
        elif any(child not in events or events[child].get("parent_event") != key for child in children):
            errors.append("crisis: broken child reference " + key)
        for field in ("immediate_consequences", "delayed_consequences"):
            links = event.get(field)
            if not isinstance(links, list) or any(not isinstance(e, str) for e in (links or []) if isinstance(links, list)):
                errors.append("crisis: invalid effect links " + key)
            elif len(set(links)) != len(links) or any(e not in state["effects"] or state["effects"][e].get("event_id") != key for e in links):
                errors.append("crisis: invalid effect links " + key)
    # Walk ancestor chains iteratively so a malformed deep save cannot recurse forever.
    for key in events:
        seen, current = set(), key
        while isinstance(current, str) and current in events and isinstance(events[current], dict):
            if current in seen:
                errors.append("crisis: causal event cycle " + key)
                break
            seen.add(current)
            current = events[current].get("parent_event")
    from .crisis_effects import validate_effect
    fingerprints = set()
    for key, effect in audit_items(state["effects"]):
        errors.extend(validate_effect(world, key, effect))
    # Uniqueness is a global invariant: a changed row can collide with an
    # unchanged historical consequence even when only that row needs validation.
    for key, effect in state['effects'].items():
        if isinstance(effect, dict) and effect.get("idempotency_key"):
            if effect["idempotency_key"] in fingerprints:
                errors.append("crisis: duplicate consequence idempotency key " + key)
            fingerprints.add(effect["idempotency_key"])
    for key, stored in audit_items(state["streams"]):
        try:
            if not isinstance(key, str) or not key:
                raise ValueError("key")
            random.Random().setstate(_tuples(stored))
        except (ValueError, TypeError, OverflowError):
            errors.append("crisis: invalid saved random stream " + str(key))
    old = getattr(previous, "crisis_state", None) if previous is not None else None
    if old:
        for key, prior in old.get("events", {}).items():
            current = events.get(key)
            if current is None:
                errors.append("crisis: event history removed " + key)
                continue
            immutable = ("event_id", "event_type", "start_date", "created_at", "region", "countries", "parent_event", "actual_facts", "causes", "triggering_conditions", "rng_key")
            if any(current.get(f) != prior.get(f) for f in immutable):
                errors.append("crisis: creation facts rewritten " + key)
            if not prior.get("ongoing", True) and (current.get("ongoing") or current.get("end_date") != prior.get("end_date") or current.get("resolution_state") != prior.get("resolution_state")):
                errors.append("crisis: resolved event reopened/rewritten " + key)
            if not set(prior.get("child_events", [])) <= set(current.get("child_events", [])):
                errors.append("crisis: causal history removed " + key)
            for country, result in prior.get("measured_response_outcomes", {}).items():
                if current.get("measured_response_outcomes", {}).get(country) != result:
                    errors.append("crisis: measured response outcome removed/rewritten " + key + ":" + country)
        for key, prior in old.get("effects", {}).items():
            current = state["effects"].get(key)
            if current is None or any(current.get(f) != prior.get(f) for f in ("effect_id", "event_id", "kind", "payload", "created_at", "due_date")):
                errors.append("crisis: effect history removed/rewritten " + key)
            elif prior.get("applied") and (not current.get("applied") or current.get("receipt") != prior.get("receipt")):
                errors.append("crisis: applied consequence receipt rewritten " + key)
            if prior.get("kind") == "economic_shock" and prior.get("applied"):
                shock_id = prior.get("receipt", {}).get("shock_id")
                before_shock = (previous.economy or {}).get("shocks", {}).get(shock_id, {})
                after_shock = (world.economy or {}).get("shocks", {}).get(shock_id, {})
                if before_shock.get("applied") and not after_shock.get("applied"):
                    errors.append("crisis: realized economic damage reset for duplicate application " + key)
        if state["root_seed"] != old.get("root_seed"):
            errors.append("crisis: random stream root rewritten")
        for kind, counter in old.get("next_ids", {}).items():
            if state["next_ids"].get(kind, 0) < counter:
                errors.append("crisis: identifier sequence moved backwards " + kind)
    from .crisis_response import validation_issues
    errors.extend(validation_issues(world, previous))
    return errors


def validate(world, previous=None):
    """Malformed save data yields audit errors rather than a runtime crash."""
    try:
        return _validate_state(world, previous)
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
        return [f"crisis: malformed saved state ({type(exc).__name__}: {exc})"]


def _validate_measured_outcome(world, event, country, measured):
    """A paid response estimate becomes measured evidence only after settlement."""
    errors = []
    prefix = "crisis: measured response outcome " + event["event_id"] + ":" + str(country)
    if (country not in world.countries or country not in event["countries"]
        or not any(r.get("country") == country for r in event["responses"])
        or not isinstance(measured, dict) or not isinstance(measured.get("facts"), dict)):
        return [prefix + " lacks country/response/facts"]
    try:
        canonical_date(measured.get("date"))
        if measured["date"] < event["start_date"] or measured["date"] > world.date:
            errors.append(prefix + " has invalid measurement date")
    except (ValueError, TypeError):
        errors.append(prefix + " has invalid measurement date")
    record = (world.npc_state or {}).get("records", {}).get(measured.get("record_id"))
    facts = measured["facts"]
    if (not isinstance(record, dict) or record.get("facts") != facts or record.get("day", record.get("date")) != measured.get("date")
        or facts.get("event_id") != event["event_id"] or facts.get("measured_aftermath") is not True):
        errors.append(prefix + " conflicts with permanent NPC evidence")
    if "realized_population_loss" not in facts:
        return errors
    counts = ("realized_population_loss", "prevented_population_loss", "unmitigated_loss_plan")
    if any(type(facts.get(field)) is not int or facts.get(field, -1) < 0 for field in counts):
        return errors + [prefix + " has invalid realized loss units"]
    if facts["realized_population_loss"] + facts["prevented_population_loss"] != facts["unmitigated_loss_plan"]:
        errors.append(prefix + " has inconsistent loss reconciliation")
    account = facts.get("population_account")
    from .demography import validate_account
    if not isinstance(account, dict) or validate_account(account, world.date):
        return errors + [prefix + " lacks a valid realized monthly population account"]
    ids = facts.get("loss_effects")
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        return errors + [prefix + " has invalid realized loss effect links"]
    realized, base, kinds = 0, 0, set()
    effects = _state(world)["effects"]
    links = event["immediate_consequences"] + event["delayed_consequences"]
    for identifier in ids:
        loss = effects.get(identifier)
        if (identifier not in links or not isinstance(loss, dict) or loss.get("kind") != "population_loss"
            or loss.get("event_id") != event["event_id"] or not loss.get("applied")
            or world.regions[loss["payload"]["region"]].owner != country
            or not account["period_start"] <= loss["applied_at"] < account["period_end"]):
            errors.append(prefix + " treats pending/nonlocal losses as realized deaths")
            continue
        realized += loss["receipt"]["count"]
        base += loss["receipt"]["base_count"]
        kinds.add(loss["payload"].get("loss_kind", "disaster_losses"))
    if (realized != facts["realized_population_loss"] or base != facts["unmitigated_loss_plan"]
        or sum(account[kind] for kind in kinds) < realized or measured.get("date", "") < account["period_end"]):
        errors.append(prefix + " does not match realized monthly demographic accounting")
    return errors
