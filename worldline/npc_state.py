from .incremental_audit import items as audit_items, json_check as audit_json
"""JSON NPC identities, private information and evidence-based ruler judgements.

This module never receives World, Engine, a database, or economic truth. The
orchestrator explicitly delivers reports. Objective records and actor memories
are distinct; actor_view exposes only the named actor's delivered information.
"""
import copy
import hashlib
import json
import math
from datetime import date

VERSION = 1
ABILITIES = ("administration", "diplomacy", "military", "finance", "organization", "persuasion", "intelligence")
TRAITS = ("ambition", "risk_tolerance", "patience", "pragmatism", "ideological_commitment",
          "personal_loyalty", "institutional_loyalty", "self_interest", "prestige_sensitivity",
          "fear_sensitivity", "impulsiveness")
BIASES = ("overconfidence", "optimism", "sunk_cost_tendency", "group_conformity",
          "historical_analogy_bias", "enemy_underestimation", "ally_overestimation")
RELATION_AXES = ("affection", "respect", "trust", "fear", "perceived_legitimacy",
                 "ideological_alignment", "obligation", "grievance", "personal_interest_alignment")
SCOPES = ("economic_policy", "mobilize", "fiscal_action", "war_preparation", "local_action", "diplomatic_engagement")
GROUP_KINDS = ("faction", "institution", "government", "military_branch", "regional_authority")
CATEGORIES = ("administrative_performance", "economic_performance", "war_result", "territory_change",
    "tax_burden", "disaster_response", "promise_kept", "promise_broken", "punishment", "pardon",
    "corruption_response", "class_policy", "religious_policy", "ethnic_policy", "regional_policy",
    "elite_privilege", "military_treatment", "treaty_kept", "treaty_broken", "ally_protected",
    "ally_betrayed", "life_saved", "family_loss", "massacre", "national_victory")
ENDURING = {"life_saved", "family_loss", "massacre", "national_victory"}
BASE_RELATION = dict(zip(RELATION_AXES, (.5, .5, .5, 0, .5, .5, 0, 0, .5)))


def _day(value):
    from .npc_runtime import parsed_day
    return parsed_day(value)


def _number(value, low=0, high=1):
    try:
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        # JSON integers may exceed the numeric range used for NPC signals.
        return False


def _json(value):
    """Round-trip to guarantee detached finite JSON data and reject Python refs."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("NPC data must contain only finite JSON values") from exc


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def initialize_state():
    return {"version": VERSION, **{key: {} for key in ("persons", "groups", "governments", "reports",
            "memories", "institutional_memories", "relationships", "records", "rulers", "legacy", "schedules")},
            "decisions": [], "actions": [], "next_decision_id": 1}


def _actor(state, actor):
    if actor not in state["persons"]:
        raise ValueError("Unknown NPC actor")
    return state["persons"][actor]


def add_person(state, id, country, name, **overrides):
    if not all(_identifier(v) for v in (id, country, name)) or id in state["persons"] or id in state["groups"]:
        raise ValueError("NPC person requires unique ID, country and name")
    allowed = {"role", "abilities", "traits", "biases", "preferences", "goals", "identities", "groups",
               "authority", "importance", "active", "background", "era_values", "information_level", "region"}
    if set(overrides) - allowed:
        raise ValueError("Unknown NPC person field")
    person = {"id": id, "country": country, "name": name, "kind": "individual", "role": "official",
        "abilities": dict.fromkeys(ABILITIES, .5), "traits": dict.fromkeys(TRAITS, .5),
        "biases": dict.fromkeys(BIASES, 0.0), "preferences": {}, "goals": [], "identities": {},
        "groups": [], "authority": [], "importance": .5, "active": True,
        "background": {}, "era_values": {}, "information_level": .5, "region": None}
    for key, value in overrides.items():
        if key in ("abilities", "traits", "biases"):
            if not isinstance(value, dict) or set(value) - set(person[key]):
                raise ValueError(f"Unknown NPC {key} key")
            person[key].update(_json(value))
        else:
            person[key] = _json(value)
    issues = _validate_person(person)
    if issues:
        raise ValueError("; ".join(issues))
    state["persons"][id] = person
    state["reports"][id], state["memories"][id], state["relationships"][id] = {}, [], {}
    return copy.deepcopy(person)


def add_group(state, id, country, name, kind="faction", **overrides):
    if not all(_identifier(v) for v in (id, country, name)) or id in state["persons"] or id in state["groups"]:
        raise ValueError("NPC group requires unique ID, country and name")
    allowed = {"members", "leadership", "goals", "preferences", "interests", "values", "resources", "influence",
               "institutional_access", "loyalty", "rivals", "cohesion", "region", "active"}
    if kind not in GROUP_KINDS or set(overrides) - allowed:
        raise ValueError("Unknown NPC group kind/field")
    group = {"id": id, "country": country, "name": name, "kind": kind, "members": [], "leadership": [],
        "goals": [], "preferences": {}, "interests": {}, "values": {}, "resources": {}, "influence": .5,
        "institutional_access": .5, "loyalty": .5, "rivals": [], "cohesion": .5, "region": None, "active": True}
    group.update(_json(overrides))
    if "preferences" in overrides and "interests" not in overrides:
        group["interests"] = copy.deepcopy(group["preferences"])
    if isinstance(group["leadership"], str):
        group["leadership"] = [group["leadership"]]
    if not isinstance(group["members"], list) or not isinstance(group["leadership"], list) or any(
            member not in state["persons"] or state["persons"][member]["country"] != country
            for member in group["members"] + group["leadership"]):
        raise ValueError("NPC group requires known same-country members")
    if any(member not in group["members"] for member in group["leadership"]):
        raise ValueError("Group leadership must be among group members")
    if len(set(group["members"])) != len(group["members"]):
        raise ValueError("Duplicate NPC group member")
    if any(not _number(group[k]) for k in ("influence", "institutional_access", "loyalty", "cohesion")):
        raise ValueError("NPC group ratios must lie between 0 and 1")
    if not isinstance(group["resources"], dict) or any(not _number(v, 0, float("inf")) for v in group["resources"].values()):
        raise ValueError("NPC group resources must be finite nonnegative quantities")
    for field in ("preferences", "interests"):
        if not isinstance(group[field], dict) or any(not _number(v, -1, 1) for v in group[field].values()):
            raise ValueError("NPC group preference/interest must be between -1 and 1")
    state["groups"][id] = group
    state["institutional_memories"][id] = []
    for member in group["members"]:
        if id not in state["persons"][member]["groups"]:
            state["persons"][member]["groups"].append(id)
    return copy.deepcopy(group)


def configure_government(state, country, ruler, cabinet=None, regime="monarchy", autonomous=True,
                         decision_rule="cabinet", next_due=None):
    cabinet = list(cabinet or [])
    if not _identifier(country) or not _identifier(regime) or type(autonomous) is not bool or decision_rule not in ("cabinet", "executive"):
        raise ValueError("Invalid NPC government configuration")
    if ruler is not None:
        _actor(state, ruler)
    if any(person not in state["persons"] or state["persons"][person]["country"] != country
           for person in cabinet + ([ruler] if ruler else [])):
        raise ValueError("NPC government requires same-country ruler/cabinet")
    if len(set(cabinet)) != len(cabinet):
        raise ValueError("Duplicate cabinet member")
    if next_due is not None:
        _day(next_due)
    value = {"country": country, "regime": regime, "ruler": ruler, "cabinet": cabinet,
             "autonomous": autonomous, "decision_rule": decision_rule, "next_due": next_due}
    state["governments"][country] = value
    if ruler:
        state["rulers"].setdefault(ruler, {"id": ruler, "country": country,
            "records": [id for id, record in state["records"].items() if record["subject"] == ruler]})
    return copy.deepcopy(value)


def _validate_event_report(data, as_of):
    if {"event_id", "subject", "category", "facts"} <= set(data):
        if (not all(_identifier(data[k]) for k in ("event_id", "subject", "category"))
            or not isinstance(data["facts"], dict)
            or data["category"] not in CATEGORIES and not data["category"].startswith("custom:")):
            raise ValueError("Malformed event-shaped NPC report")
        if "day" in data and _day(data["day"]) > _day(as_of):
            raise ValueError("NPC event report describes a future event")


def receive_report(state, actor, topic, data, as_of, source, accuracy=.8, confidence=.8, reliability=.8):
    """Deliver an observation, including potentially false or outdated beliefs."""
    _actor(state, actor)
    _day(as_of)
    if not _identifier(topic) or not _identifier(source) or not isinstance(data, dict) or any(
            not _number(v) for v in (accuracy, confidence, reliability)):
        raise ValueError("Invalid NPC report")
    _validate_event_report(data, as_of)
    report = {"as_of": as_of, "source": source, "accuracy": accuracy, "confidence": confidence,
              "reliability": reliability, "data": _json(data)}
    state["reports"][actor][topic] = report
    return copy.deepcopy(report)


def _digest(record):
    body = {k: v for k, v in record.items() if k != "sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def record_fact(state, event_id, day, subject, category, facts, source):
    """Write an immutable objective record without informing any actor."""
    _day(day)
    if not all(_identifier(v) for v in (event_id, subject, category, source)) or not isinstance(facts, dict):
        raise ValueError("Invalid NPC objective record")
    if category not in CATEGORIES and not category.startswith("custom:"):
        raise ValueError("Unknown NPC record category (extensions require custom: prefix)")
    record = {"id": event_id, "day": day, "subject": subject, "category": category,
              "facts": _json(facts), "source": source}
    record["sha256"] = _digest(record)
    if event_id in state["records"]:
        if state["records"][event_id] == record:
            return copy.deepcopy(record)
        raise ValueError("Objective NPC record is immutable")
    state["records"][event_id] = record
    if subject in state["persons"] and state["persons"][subject]["role"] in ("ruler", "monarch", "head_of_state"):
        state["rulers"].setdefault(subject, {"id": subject, "country": state["persons"][subject]["country"], "records": []})
    if subject in state["rulers"]:
        state["rulers"][subject]["records"].append(event_id)
    return copy.deepcopy(record)


def deliver_record(state, actor, event_id, as_of=None, accuracy=.8, confidence=.8, reliability=.8):
    if event_id not in state["records"]:
        raise ValueError("Unknown objective NPC record")
    record = state["records"][event_id]
    return receive_report(state, actor, f"event:{event_id}",
        {"event_id": event_id, "day": record["day"], "subject": record["subject"],
         "category": record["category"], "facts": record["facts"]},
        as_of or record["day"], record["source"], accuracy, confidence, reliability)


def _known_events(state, actor):
    events = {}
    for topic, report in state["reports"].get(actor, {}).items():
        data = report["data"]
        if all(k in data for k in ("event_id", "subject", "category", "facts")):
            # Internal read-only index. Public views and recorded memories copy
            # facts at their boundaries, avoiding quadratic history copying.
            events[data["event_id"]] = {**data, "report_as_of": report["as_of"],
                "source": report["source"], "confidence": report["confidence"], "reliability": report["reliability"]}
    return events


def remember(state, actor, event_id, interpretation=None, reliability=.8, salience=.5,
             personal_impact=0, group_impact=0, valence=None, tags=None, enduring=None,
             emotional_valence=None):
    """Remember delivered information, never peek at the objective fact table."""
    _actor(state, actor)
    known = _known_events(state, actor)
    if event_id not in known:
        raise ValueError("Actor cannot remember an event that was never delivered")
    if emotional_valence is not None:
        if valence is not None and valence != emotional_valence:
            raise ValueError("Conflicting NPC memory valence")
        valence = emotional_valence
    if interpretation is not None and not isinstance(interpretation, dict):
        raise ValueError("NPC memory interpretation must be a dictionary")
    if any(not _number(v) for v in (reliability, salience)) or any(
            not _number(v, -1, 1) for v in (personal_impact, group_impact)) or valence is not None and not _number(valence, -1, 1):
        raise ValueError("Invalid NPC memory strengths")
    event = known[event_id]
    if enduring is not None and type(enduring) is not bool or tags is not None and (
            not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)):
        raise ValueError("Invalid NPC memory tags/enduring flag")
    memory = {"event_id": event_id, "day": event.get("day", event["report_as_of"]),
        "received_date": event["report_as_of"], "subject": event["subject"],
        "category": event["category"], "kind": event["category"],
        "observed_facts": copy.deepcopy(event["facts"]), "source": event["source"],
        "interpretation": _json(interpretation if interpretation is not None else {}),
        "reliability": reliability, "confidence": event["confidence"], "salience": salience,
        "personal_impact": personal_impact, "group_impact": group_impact,
        "valence": _signal(event["facts"]) if valence is None else valence,
        "emotional_valence": _signal(event["facts"]) if valence is None else valence,
        "perceived_result": _signal(event["facts"]) if valence is None else valence,
        "tags": list(tags or [event["category"]]),
        "enduring": event["category"] in ENDURING if enduring is None else enduring}
    # A revised interpretation replaces the actor's memory of this one event;
    # it never makes one event count repeatedly as multiple experiences.
    values = state["memories"][actor]
    values[:] = [m for m in values if m["event_id"] != event_id]
    values.append(memory)
    return copy.deepcopy(memory)


def _memory_weight(memory, today):
    from .npc_runtime import memory_weight
    return memory_weight(memory, today)


def _effective_entries(values, today):
    """Call-local read projections; nested observation data is not modified.

    Public APIs detach these entries. Trusted synchronous evaluation can read
    them without first copying every remembered fact and interpretation.
    """
    current = _day(today)
    return [{**memory, "effective_weight": _memory_weight(memory, today),
             "age_days": max(0, (current - _day(memory["received_date"])).days)}
            for memory in values if memory["received_date"] <= today]


def effective_memory(state, actor, today):
    _actor(state, actor)
    # Each entry is detached independently, preserving isolation even when raw
    # caller input happens to share a nested dictionary between two memories.
    return [copy.deepcopy(memory) for memory in _effective_entries(state["memories"].get(actor, []), today)]


def remember_group(state, group, event_id, informant, **kwargs):
    """Commit a member's delivered experience to an institution's own history.

    This is explicit information sharing. Merely belonging to a group never
    reveals every member's private reports or autobiographical memories.
    """
    if group not in state["groups"] or informant not in state["groups"][group]["members"]:
        raise ValueError("Institutional memory requires a group-member informant")
    temporary = {"persons": state["persons"], "reports": state["reports"], "memories": {informant: []}}
    memory = remember(temporary, informant, event_id, **kwargs)
    memory.update(group=group, informant=informant, informant_membership_at_recording=group)
    values = state["institutional_memories"].setdefault(group, [])
    values[:] = [m for m in values if m["event_id"] != event_id]
    values.append(memory)
    return copy.deepcopy(memory)


def effective_group_memory(state, group, today):
    if group not in state["groups"]:
        raise ValueError("Unknown NPC institution")
    return [copy.deepcopy(memory) for memory in _effective_entries(state["institutional_memories"].get(group, []), today)]


def _observer_events(state, actor, today):
    """Personal observations plus explicitly shared institutional histories."""
    events = _known_events(state, actor)
    for group in state["persons"][actor]["groups"]:
        if group not in state["groups"]:
            continue
        for memory in _effective_entries(state["institutional_memories"].get(group, []), today):
            if memory["event_id"] in events:
                continue
            events[memory["event_id"]] = {"event_id": memory["event_id"], "day": memory["day"],
                "subject": memory["subject"], "category": memory["category"],
                "facts": memory["observed_facts"], "report_as_of": memory["received_date"],
                "source": memory["source"], "confidence": memory["confidence"],
                "reliability": memory["reliability"], "institutional_memory": memory, "group": group}
    return events


def _signal(facts):
    for key in ("performance", "outcome", "impact", "kindness", "benefit", "result"):
        value = facts.get(key)
        if _number(value, -1, 1):
            return float(value)
    if type(facts.get("success")) is bool:
        return 1.0 if facts["success"] else -1.0
    if _number(facts.get("gdp_before"), 0, float("inf")) and _number(facts.get("gdp_after"), 0, float("inf")):
        try:
            change = 2 * (facts["gdp_after"] - facts["gdp_before"]) / max(1, facts["gdp_before"])
        except OverflowError:
            # The mathematical ratio exceeds float range and saturates here.
            return 1.0 if facts["gdp_after"] > facts["gdp_before"] else -1.0
        return max(-1, min(1, change))
    return 0.0


def _event_effect(category, facts, memory, person):
    signal = _signal(facts)
    severity = facts.get("severity", 1.0)
    severity = severity if _number(severity) else 1.0
    performance = signal
    effects = {}
    if category in ("administrative_performance", "economic_performance", "disaster_response", "corruption_response"):
        effects = {"respect": .18 * performance, "trust": .06 * performance,
                   "perceived_legitimacy": .10 * performance}
    elif category in ("war_result", "territory_change", "national_victory"):
        if category == "national_victory" and not signal:
            performance = 1.0  # the named event itself records a victory
        prestige = .5 + person["traits"]["prestige_sensitivity"]
        effects = {"respect": .15 * performance * prestige, "perceived_legitimacy": .10 * performance,
                   "trust": .03 * performance, "affection": .04 * performance}
    elif category in ("promise_kept", "treaty_kept", "ally_protected"):
        effects = {"trust": .18, "respect": .06, "obligation": .04, "grievance": -.03}
    elif category in ("promise_broken", "treaty_broken", "ally_betrayed"):
        effects = {"trust": -.22, "respect": -.05, "perceived_legitimacy": -.08, "grievance": .12}
    elif category in ("pardon", "life_saved"):
        effects = {"affection": .22, "obligation": .18, "trust": .05, "grievance": -.05}
    elif category in ("punishment", "family_loss", "massacre"):
        effects = {"fear": .22, "affection": -.18, "grievance": .28, "trust": -.10}
    elif category == "tax_burden":
        burden = facts.get("burden", abs(signal))
        burden = burden if _number(burden) else 1.0
        effects = {"grievance": .16 * burden, "personal_interest_alignment": -.14 * burden,
                   "affection": -.05 * burden}
    elif category in ("class_policy", "religious_policy", "ethnic_policy", "regional_policy", "elite_privilege", "military_treatment"):
        group_signal = memory.get("group_impact", signal) if memory else signal
        effects = {"ideological_alignment": .12 * group_signal,
                   "personal_interest_alignment": .15 * group_signal,
                   "grievance": -.10 * group_signal}
    if "kindness" in facts and _number(facts["kindness"], -1, 1):
        effects["affection"] = effects.get("affection", 0) + .18 * facts["kindness"]
        effects["obligation"] = effects.get("obligation", 0) + .08 * max(0, facts["kindness"])
    if memory:
        personal = memory["personal_impact"]
        group = memory["group_impact"]
        effects["personal_interest_alignment"] = effects.get("personal_interest_alignment", 0) + .12 * personal
        effects["affection"] = effects.get("affection", 0) + .10 * personal
        effects["grievance"] = effects.get("grievance", 0) - .12 * min(0, personal + group)
    return {key: value * severity for key, value in effects.items()}


def configure_legacy(state, successor, predecessor, actor, expectation=0, gratitude=0,
                     grievance=0, projection=0, day=None, dynastic_legitimacy=0):
    """Set this observer's inherited context, not inherited ruler achievements."""
    for person in (successor, predecessor, actor):
        _actor(state, person)
    if successor == predecessor or any(not _number(v) for v in (expectation, gratitude, grievance, projection, dynastic_legitimacy)):
        raise ValueError("Invalid NPC legacy context")
    if day is not None:
        _day(day)
    ancestor = predecessor
    seen = {successor}
    while ancestor in state["legacy"]:
        if ancestor in seen:
            raise ValueError("NPC succession cycle")
        seen.add(ancestor)
        ancestor = state["legacy"][ancestor]["predecessor"]
    if ancestor == successor:
        raise ValueError("NPC succession cycle")
    entry = state["legacy"].setdefault(successor, {"successor": successor, "predecessor": predecessor, "inherited_by": {}})
    if entry["predecessor"] != predecessor:
        raise ValueError("Conflicting predecessor for NPC successor")
    entry["inherited_by"][actor] = {"expectation": expectation, "gratitude": gratitude,
        "grievance": grievance, "projection": projection, "day": day,
        "dynastic_legitimacy": dynastic_legitimacy}
    return copy.deepcopy(entry["inherited_by"][actor])


def _relationship_context(state, actor, today):
    """Actor-local observations; never retain them across a state operation."""
    observations = _observer_events(state, actor, today)
    memories = {m["event_id"]: m for m in _effective_entries(state["memories"].get(actor, []), today)}
    subjects = {}
    for event_id, event in sorted(observations.items()):
        try:
            subjects.setdefault(event["subject"], []).append((event_id, event))
        except TypeError:
            # Raw public dictionaries can contain unhashable subjects. The
            # original evaluator equality-compared them and skipped them.
            return observations, memories
    return observations, memories, subjects


def evaluate_ruler(state, actor, ruler, today, *, _context=None):
    """Recompute nine independent axes from known events; never stack tick effects."""
    person = _actor(state, actor)
    _actor(state, ruler)
    _day(today)
    # Reuse observation and memory indexes only inside this evaluation. Building
    # a fresh context on each public call also observes arbitrary raw dict edits.
    context = _relationship_context(state, actor, today) if _context is None else _context
    axes = dict(BASE_RELATION)
    causes = []
    inherited = state["legacy"].get(ruler, {}).get("inherited_by", {}).get(actor)
    expectation = 0.0
    predecessor_reference = 0.0
    if inherited:
        expectation = inherited["expectation"]
        predecessor = state["legacy"][ruler]["predecessor"]
        known_predecessor = [e for e in context[0].values() if e["subject"] == predecessor]
        performance_events = [e for e in known_predecessor if e["category"] in (
            "administrative_performance", "economic_performance", "war_result", "disaster_response")]
        weights = [e["confidence"] * e["reliability"] for e in performance_events]
        if sum(weights):
            predecessor_reference = sum(_signal(e["facts"]) * weight for e, weight in zip(performance_events, weights)) / sum(weights)
        if known_predecessor and inherited["projection"]:
            previous = _evaluate_known(state, person, actor, predecessor, today, 0, context=context)[0]
            for axis in RELATION_AXES:
                axes[axis] += (previous[axis] - BASE_RELATION[axis]) * inherited["projection"] * .35
        axes["affection"] += .12 * inherited["gratitude"] - .12 * inherited["grievance"]
        axes["obligation"] += .12 * inherited["gratitude"]
        axes["grievance"] += .15 * inherited["grievance"]
        axes["trust"] -= .06 * inherited["grievance"]
        axes["perceived_legitimacy"] += .12 * inherited.get("dynastic_legitimacy", 0)
        causes.append({"kind": "inherited_context", "predecessor": predecessor,
            "subject": ruler, "context": copy.deepcopy(inherited),
            "note": "predecessor achievements remain separate from successor records"})
    own_axes, own_causes = _evaluate_known(state, person, actor, ruler, today, expectation, predecessor_reference, context=context)
    for axis in RELATION_AXES:
        axes[axis] = max(0, min(1, axes[axis] + own_axes[axis] - BASE_RELATION[axis]))
    causes.extend(own_causes)
    result = {**axes, "actor": actor, "subject": ruler, "as_of": today, "causes": causes}
    state["relationships"][actor][ruler] = result
    return copy.deepcopy(result)


def _evaluate_known(state, person, actor, ruler, today, expectation, predecessor_reference=0, *, context=None):
    axes = dict(BASE_RELATION)
    causes = []
    if context is None:
        context = _relationship_context(state, actor, today)
    observations, memories = context[:2]
    events = context[2].get(ruler, ()) if len(context) > 2 else sorted(observations.items())
    for event_id, event in events:
        if event["subject"] != ruler or event["report_as_of"] > today:
            continue
        memory = memories.get(event_id, event.get("institutional_memory"))
        if memory:
            weight = memory["effective_weight"]
            facts = memory["observed_facts"]
        else:
            elapsed = max(0, (_day(today) - _day(event["report_as_of"])).days)
            weight = event["confidence"] * event["reliability"] * math.exp(-elapsed / 730)
            facts = event["facts"]
        effects = _event_effect(event["category"], facts, memory, person)
        comparison = 0
        if expectation and "respect" in effects and event["category"] in (
                "administrative_performance", "economic_performance", "war_result", "disaster_response"):
            # Only known predecessor outcomes set the comparison standard. The
            # successor's own absolute record remains unchanged and separately shown.
            comparison = -.12 * expectation * predecessor_reference
            effects["respect"] += comparison
        contribution = {axis: value * weight for axis, value in effects.items()}
        for axis, value in contribution.items():
            axes[axis] += value
        causes.append({"event_id": event_id, "category": event["category"], "source": event["source"],
            "known_as_of": event["report_as_of"], "effective_weight": weight,
            "observed_facts": copy.deepcopy(facts), "axis_contributions": contribution,
            "legacy_comparison": {"known_predecessor_performance": predecessor_reference,
                "expectation_strength": expectation, "respect_contribution": comparison * weight}})
    return {axis: max(0, min(1, value)) for axis, value in axes.items()}, causes


def update_relationship(state, actor, ruler, today):
    return evaluate_ruler(state, actor, ruler, today)


def _actor_read_view(state, actor, today):
    """Internal, call-local projection for trusted synchronous read-only decisions.

    Never return this borrowed projection through an interface. Public actor_view
    detaches once at its boundary. Objective records and accuracy stay excluded.
    It is freshly composed on every call, without cached mutable actor state.
    """
    person = _actor(state, actor)
    _day(today)
    # The original public JSON view sorted report topics. Preserve its first-match
    # and metadata iteration order here without changing stored arrival order.
    reports = {k: v for k, v in sorted(state["reports"].get(actor, {}).items()) if v["as_of"] <= today}
    metadata = {topic: {"as_of": r["as_of"], "source": r["source"], "confidence": r["confidence"],
                       "reliability": r["reliability"], "age_days": (_day(today) - _day(r["as_of"])).days}
                for topic, r in reports.items()}
    relations = {target: dict(axes) for target, axes in state["relationships"].get(actor, {}).items()}
    for target, axes in relations.items():
        axes["rivalry"] = axes["grievance"]
        axes["loyalty"] = (axes["trust"] + axes["perceived_legitimacy"] + axes["obligation"]) / 3
    legacy = {successor: {"predecessor": entry["predecessor"], **entry["inherited_by"][actor]}
              for successor, entry in state["legacy"].items() if actor in entry["inherited_by"]}
    return {"actor": actor, "date": today, "person": person,
        "ruler_id": state["governments"].get(person["country"], {}).get("ruler"),
        "groups": {group: state["groups"][group] for group in person["groups"] if group in state["groups"]},
        "beliefs": {topic: report["data"] for topic, report in reports.items()},
        "report_metadata": metadata, "memories": _effective_entries(state["memories"].get(actor, []), today),
        "institutional_memories": {group: _effective_entries(state["institutional_memories"].get(group, []), today) for group in person["groups"] if group in state["groups"]},
        "relationships": relations, "legacy": legacy}


def actor_view(state, actor, today):
    """Private, detached decision input. Audit accuracy and objective records stay out."""
    return _json(_actor_read_view(state, actor, today))


def _validate_person(person):
    issues = []
    if person["kind"] != "individual" or not all(_identifier(person[k]) for k in ("id", "country", "name", "role")):
        issues.append("npc: invalid person identity/role")
    for field, keys in (("abilities", ABILITIES), ("traits", TRAITS), ("biases", BIASES)):
        if not isinstance(person[field], dict) or set(person[field]) != set(keys) or any(not _number(v, -1 if field == "biases" and k == "optimism" else 0, 1) for k, v in person[field].items()):
            issues.append(f"npc: invalid person {field}")
    if not isinstance(person["preferences"], dict) or any(not _identifier(k) or not _number(v, -1, 1) for k, v in person["preferences"].items()):
        issues.append("npc: invalid person preferences")
    if not isinstance(person["goals"], list) or any(not isinstance(v, (str, dict)) for v in person["goals"]):
        issues.append("npc: invalid person goals")
    if not isinstance(person["identities"], dict) or not isinstance(person["groups"], list) or not all(_identifier(v) for v in person["groups"]):
        issues.append("npc: invalid person identities/groups")
    if not isinstance(person["authority"], list) or any(v not in SCOPES for v in person["authority"]):
        issues.append("npc: invalid person authority")
    if type(person["active"]) is not bool or not _number(person["importance"]) or not _number(person["information_level"]):
        issues.append("npc: invalid person activity/importance/information")
    return issues


def validate(state, countries, regions, today, previous=None):
    """Return explicit audit errors; all dates, ranges and references are checked."""
    issues = []
    try:
        _day(today)
        audit_json(state)
        expected = set(initialize_state())
        if set(state) - expected - {"adapter"} or expected - set(state) or type(state["version"]) is not int or state["version"] != VERSION:
            return ["npc: unsupported or malformed state schema"]
        for key in expected - {"version", "decisions", "actions", "next_decision_id"}:
            if not isinstance(state[key], dict):
                return [f"npc: malformed {key} map"]
        for key in ("reports", "memories", "relationships"):
            for actor in state["persons"]:
                if actor not in state[key]:
                    issues.append(f"npc: missing personal {key} bucket {actor}")
        if type(state["next_decision_id"]) is not int or state["next_decision_id"] <= 0 or not isinstance(state["decisions"], list) or not isinstance(state["actions"], list):
            issues.append("npc: invalid decision/action queues")
        if "adapter" in state:
            adapter = state["adapter"]
            if not isinstance(adapter, dict) or set(adapter) != {"last_sensing_date", "last_settlement", "record_sequence", "pending"} or type(adapter["record_sequence"]) is not int or adapter["record_sequence"] <= 0 or not isinstance(adapter["pending"], dict):
                issues.append("npc: malformed engine adapter state")
            else:
                for field in ("last_sensing_date", "last_settlement"):
                    if adapter[field] is not None:
                        _day(adapter[field])
                        if adapter[field] > today:
                            issues.append("npc: future adapter date")
                if any(actor not in state["persons"] or not isinstance(values, list) or not all(isinstance(v, str) for v in values) for actor, values in adapter["pending"].items()):
                    issues.append("npc: invalid pending information queue")
        for id, person in audit_items(state["persons"]):
            issues.extend(_validate_person(person))
            if person["id"] != id or person["country"] not in countries or person["region"] is not None and person["region"] not in regions:
                issues.append(f"npc: unknown person country/region {id}")
            if any(group not in state["groups"] or id not in state["groups"][group]["members"] for group in person["groups"]):
                issues.append(f"npc: inconsistent person membership {id}")
        for id, group in audit_items(state["groups"]):
            if group["id"] != id or group["country"] not in countries or group["kind"] not in GROUP_KINDS or type(group["active"]) is not bool:
                issues.append(f"npc: invalid group identity {id}")
            if group["region"] is not None and group["region"] not in regions:
                issues.append(f"npc: invalid group region {id}")
            if any(not _number(group[k]) for k in ("influence", "institutional_access", "loyalty", "cohesion")):
                issues.append(f"npc: invalid group ratios {id}")
            if not isinstance(group["resources"], dict) or any(not _number(v, 0, float("inf")) for v in group["resources"].values()):
                issues.append(f"npc: invalid group resources {id}")
            for field in ("preferences", "interests"):
                if not isinstance(group[field], dict) or any(not _number(v, -1, 1) for v in group[field].values()):
                    issues.append(f"npc: invalid group {field} {id}")
            if not isinstance(group["goals"], list) or not isinstance(group["values"], dict):
                issues.append(f"npc: invalid group goals/values {id}")
            if len(set(group["members"])) != len(group["members"]) or any(member not in state["persons"] or state["persons"][member]["country"] != group["country"] or id not in state["persons"][member]["groups"] for member in group["members"]):
                issues.append(f"npc: inconsistent group membership {id}")
            if any(member not in group["members"] for member in group["leadership"]) or any(rival not in state["groups"] or rival == id for rival in group["rivals"]):
                issues.append(f"npc: invalid group leader/rival {id}")
        for country, government in audit_items(state["governments"]):
            people = government["cabinet"] + ([government["ruler"]] if government["ruler"] else [])
            if country not in countries or government["country"] != country or not _identifier(government["regime"]) or government["decision_rule"] not in ("cabinet", "executive") or type(government["autonomous"]) is not bool:
                issues.append(f"npc: invalid government {country}")
            if any(person not in state["persons"] or state["persons"][person]["country"] != country for person in people) or len(set(government["cabinet"])) != len(government["cabinet"]):
                issues.append(f"npc: invalid cabinet/ruler {country}")
            if government["next_due"] is not None:
                _day(government["next_due"])
        for actor, schedule in audit_items(state["schedules"]):
            if actor not in state["persons"] or not isinstance(schedule, dict) or set(schedule) != {"next_due", "last_evaluated"}:
                issues.append("npc: invalid actor evaluation schedule")
                continue
            for field in ("next_due", "last_evaluated"):
                if schedule[field] is not None:
                    _day(schedule[field])
                    if field == "last_evaluated" and schedule[field] > today:
                        issues.append("npc: future actor evaluation")
        for event_id, record in audit_items(state["records"]):
            if record["id"] != event_id or record["day"] > today or not _identifier(record["subject"]) or not _identifier(record["source"]) or record["category"] not in CATEGORIES and not record["category"].startswith("custom:"):
                issues.append(f"npc: invalid objective record {event_id}")
            _day(record["day"])
            if not isinstance(record["facts"], dict) or record["sha256"] != _digest(record):
                issues.append(f"npc: changed objective record {event_id}")
        for actor, reports in audit_items(state["reports"]):
            if actor not in state["persons"]:
                issues.append("npc: unknown report observer")
            for topic, report in audit_items(reports):
                _day(report["as_of"])
                if report["as_of"] > today or not _identifier(topic) or not _identifier(report["source"]) or not isinstance(report["data"], dict) or any(not _number(report[k]) for k in ("accuracy", "confidence", "reliability")):
                    issues.append(f"npc: invalid delivered report {actor}:{topic}")
                _validate_event_report(report["data"], report["as_of"])
        known_cache = {}
        for actor, memories in audit_items(state["memories"]):
            if actor not in state["persons"] or not isinstance(memories, list):
                issues.append("npc: unknown memory observer")
                continue
            events = _known_events(state, actor)
            known_cache[actor] = events
            if len({m["event_id"] for m in memories}) != len(memories):
                issues.append("npc: duplicate memory event")
            for memory in memories:
                _day(memory["day"])
                _day(memory["received_date"])
                if memory["event_id"] not in events or memory["received_date"] > today or memory["day"] > memory["received_date"] or memory["subject"] != events.get(memory["event_id"], {}).get("subject"):
                    issues.append(f"npc: memory lacks delivered experience {actor}")
                if any(not _number(memory[k]) for k in ("reliability", "salience", "confidence")) or any(not _number(memory[k], -1, 1) for k in ("personal_impact", "group_impact", "valence", "perceived_result")) or type(memory["enduring"]) is not bool:
                    issues.append(f"npc: invalid memory strengths {actor}")
                if not isinstance(memory["observed_facts"], dict) or not isinstance(memory["interpretation"], dict) or not isinstance(memory["tags"], list) or not all(isinstance(t, str) for t in memory["tags"]):
                    issues.append(f"npc: invalid memory contents {actor}")
        for group, memories in audit_items(state["institutional_memories"]):
            if group not in state["groups"] or not isinstance(memories, list):
                issues.append("npc: unknown institutional memory owner")
                continue
            if len({m["event_id"] for m in memories}) != len(memories):
                issues.append("npc: duplicate institutional memory event")
            for memory in memories:
                informant = memory["informant"]
                if informant not in known_cache:
                    known_cache[informant] = _known_events(state, informant)
                if memory["group"] != group or informant not in state["persons"] or memory["informant_membership_at_recording"] != group or memory["event_id"] not in known_cache[informant]:
                    issues.append("npc: institutional memory lacks informed group member")
                _day(memory["received_date"])
                _day(memory["day"])
                if memory["received_date"] > today or memory["day"] > memory["received_date"] or not isinstance(memory["observed_facts"], dict) or not isinstance(memory["interpretation"], dict):
                    issues.append("npc: invalid institutional memory facts/dates")
                if any(not _number(memory[k]) for k in ("reliability", "confidence", "salience")) or any(not _number(memory[k], -1, 1) for k in ("personal_impact", "group_impact", "valence", "perceived_result")) or type(memory["enduring"]) is not bool:
                    issues.append("npc: invalid institutional memory strength")
        for actor, relationships in audit_items(state["relationships"]):
            if actor not in state["persons"]:
                issues.append("npc: unknown relationship observer")
            for ruler, value in audit_items(relationships):
                if ruler not in state["persons"] or value["actor"] != actor or value["subject"] != ruler or value["as_of"] > today or any(not _number(value[k]) for k in RELATION_AXES):
                    issues.append("npc: invalid ruler relationship")
                _day(value["as_of"])
        for ruler, entry in audit_items(state["rulers"]):
            if ruler not in state["persons"] or entry["id"] != ruler or entry["country"] != state["persons"][ruler]["country"] or any(event not in state["records"] or state["records"][event]["subject"] != ruler for event in entry["records"]):
                issues.append("npc: invalid ruler record index")
        for successor, entry in audit_items(state["legacy"]):
            if successor not in state["persons"] or entry["successor"] != successor or entry["predecessor"] not in state["persons"] or entry["predecessor"] == successor:
                issues.append("npc: invalid succession legacy")
            for actor, context in audit_items(entry["inherited_by"]):
                if actor not in state["persons"] or any(not _number(context[k]) for k in ("expectation", "gratitude", "grievance", "projection")) or not _number(context.get("dynastic_legitimacy", 0)):
                    issues.append("npc: invalid observer legacy context")
                if context["day"] is not None:
                    _day(context["day"])
                    if context["day"] > today:
                        issues.append("npc: future legacy context")
            seen, ancestor = {successor}, entry["predecessor"]
            while ancestor in state["legacy"]:
                if ancestor in seen:
                    issues.append("npc: succession cycle")
                    break
                seen.add(ancestor)
                ancestor = state["legacy"][ancestor]["predecessor"]
        if previous:
            for event_id, record in previous.get("records", {}).items():
                if state["records"].get(event_id) != record:
                    issues.append(f"npc: removed or rewritten historical record {event_id}")
        return issues
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return issues + ["npc: malformed persisted state"]
