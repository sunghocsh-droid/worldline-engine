"""Exact historical semantics, privacy and mutation-safe NPC read projections."""
import copy
import json
import math
import random
from datetime import date

import pytest

from worldline import npc_decision as nd, npc_state as ns, npc_runtime as runtime


def history():
    state = ns.initialize_state()
    for actor, role in (("old", "ruler"), ("ruler", "ruler"), ("observer", "minister"), ("other", "official")):
        ns.add_person(state, actor, "a", actor, role=role)
    ns.add_group(state, "institution", "a", "Treasury", kind="institution", members=["observer", "other"])
    ns.configure_government(state, "a", "ruler", ["observer"], autonomous=False)
    records = (
        ("old-success", "old", "economic_performance", {"performance": .8}, "1890-01-01"),
        ("old-kept", "old", "promise_kept", {}, "1891-01-01"),
        ("new-good", "ruler", "administrative_performance", {"performance": .4, "kindness": .3}, "1897-01-01"),
        ("new-broken", "ruler", "promise_broken", {}, "1897-01-03"),
        ("shared-policy", "ruler", "ethnic_policy", {"performance": -.2, "nested": {"groups": ["x"]}}, "1897-01-02"),
    )
    for identifier, subject, category, facts, day in records:
        ns.record_fact(state, identifier, day, subject, category, facts, "accounts")
        ns.deliver_record(state, "observer", identifier, accuracy=.2, confidence=.9, reliability=.8)
        ns.remember(state, "observer", identifier, reliability=.7, salience=.6, personal_impact=.1, group_impact=-.3)
    ns.remember_group(state, "institution", "shared-policy", "observer", salience=.9, group_impact=-.5)
    ns.configure_legacy(state, "ruler", "old", "observer", expectation=.8, gratitude=.4,
        grievance=.1, projection=.3, day="1897-01-01", dynastic_legitimacy=.8)
    return state


def historical_memories(values, today):
    """Frozen pre-optimization public calculation, independent of runtime cache."""
    result = []
    for memory in values:
        if memory["received_date"] > today:
            continue
        elapsed = max(0, (date.fromisoformat(today) - date.fromisoformat(memory["received_date"])).days)
        retention = .4 + .6 * math.exp(-elapsed / 7300) if memory["enduring"] else math.exp(-elapsed / 730)
        weight = retention * memory["reliability"] * memory["salience"] * memory.get("confidence", 1)
        result.append({**copy.deepcopy(memory), "effective_weight": weight, "age_days": elapsed})
    return result


def historical_view(state, actor, today):
    """Frozen public boundary: old redundant copies and final JSON roundtrip."""
    person = state["persons"][actor]
    reports = {k: v for k, v in state["reports"].get(actor, {}).items() if v["as_of"] <= today}
    metadata = {topic: {"as_of": r["as_of"], "source": r["source"], "confidence": r["confidence"],
        "reliability": r["reliability"], "age_days": (date.fromisoformat(today) - date.fromisoformat(r["as_of"])).days}
        for topic, r in reports.items()}
    relations = copy.deepcopy(state["relationships"].get(actor, {}))
    for axes in relations.values():
        axes["rivalry"] = axes["grievance"]
        axes["loyalty"] = (axes["trust"] + axes["perceived_legitimacy"] + axes["obligation"]) / 3
    legacy = {successor: {"predecessor": entry["predecessor"], **copy.deepcopy(entry["inherited_by"][actor])}
        for successor, entry in state["legacy"].items() if actor in entry["inherited_by"]}
    value = {"actor": actor, "date": today, "person": person,
        "ruler_id": state["governments"].get(person["country"], {}).get("ruler"),
        "groups": {g: state["groups"][g] for g in person["groups"] if g in state["groups"]},
        "beliefs": {topic: r["data"] for topic, r in reports.items()}, "report_metadata": metadata,
        "memories": historical_memories(state["memories"].get(actor, []), today),
        "institutional_memories": {g: historical_memories(state["institutional_memories"].get(g, []), today)
            for g in person["groups"] if g in state["groups"]},
        "relationships": relations, "legacy": legacy}
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


@pytest.mark.parametrize("today", ["1897-01-01", "1897-01-03", "1900-02-28", "1900-03-01", "1927-01-01"])
def test_public_and_internal_actor_projections_equal_frozen_historical_view(today):
    state = history()
    ns.evaluate_ruler(state, "observer", "ruler", today)
    before = copy.deepcopy(state)
    assert ns.actor_view(state, "observer", today) == historical_view(state, "observer", today)
    assert ns._actor_read_view(state, "observer", today) == historical_view(state, "observer", today)
    assert ns.effective_memory(state, "observer", today) == historical_memories(state["memories"]["observer"], today)
    assert ns.effective_group_memory(state, "institution", today) == historical_memories(state["institutional_memories"]["institution"], today)
    assert state == before


@pytest.mark.parametrize("field,value", [("enduring", True), ("reliability", .1), ("salience", .9),
    ("confidence", .2), ("received_date", "1896-01-01")])
def test_scalar_cache_observes_every_raw_memory_mutation(field, value):
    state = history()
    ns.effective_memory(state, "observer", "1900-01-01")
    state["memories"]["observer"][0][field] = value
    assert ns.effective_memory(state, "observer", "1900-01-01") == historical_memories(state["memories"]["observer"], "1900-01-01")
    assert ns.actor_view(state, "observer", "1900-01-01") == historical_view(state, "observer", "1900-01-01")


def test_raw_actor_report_group_relationship_and_legacy_edits_are_never_stale():
    state = history()
    ns.evaluate_ruler(state, "observer", "ruler", "1900-01-01")
    ns.actor_view(state, "observer", "1900-01-01")
    state["persons"]["observer"]["traits"]["ambition"] = .99
    state["reports"]["observer"]["event:new-good"]["data"]["facts"]["kindness"] = -.4
    state["memories"]["observer"][0]["interpretation"] = {"lesson": "changed"}
    state["groups"]["institution"]["preferences"]["fiscal"] = .85
    state["relationships"]["observer"]["ruler"]["trust"] = .11
    state["legacy"]["ruler"]["inherited_by"]["observer"]["gratitude"] = .8
    assert ns.actor_view(state, "observer", "1900-01-01") == historical_view(state, "observer", "1900-01-01")


def test_relation_observation_index_is_call_local_and_built_once(monkeypatch):
    state = history()
    observations = ns._observer_events
    calls = []

    def tracked(*args):
        calls.append(args[1:])
        return observations(*args)

    monkeypatch.setattr(ns, "_observer_events", tracked)
    first = ns.evaluate_ruler(state, "observer", "ruler", "1900-01-01")
    assert len(calls) == 1  # inherited and own evaluation share this one context
    next(m for m in state["memories"]["observer"] if m["event_id"] == "new-broken")["reliability"] = .1
    second = ns.evaluate_ruler(state, "observer", "ruler", "1900-01-01")
    assert len(calls) == 2 and second["trust"] > first["trust"]
    assert second["causes"] != first["causes"]


def test_public_view_and_memory_outputs_are_independently_detached_at_every_depth():
    state = history()
    ns.evaluate_ruler(state, "observer", "ruler", "1900-01-01")
    before = copy.deepcopy(state)
    view = ns.actor_view(state, "observer", "1900-01-01")
    view["beliefs"]["event:shared-policy"]["facts"]["nested"]["groups"].append("leak")
    view["memories"][-1]["observed_facts"]["nested"]["groups"].append("leak")
    view["relationships"]["ruler"]["causes"][-1]["observed_facts"]["nested"]["groups"].append("leak")
    view["groups"]["institution"]["members"].clear()
    view["legacy"]["ruler"]["gratitude"] = 1
    ns.effective_memory(state, "observer", "1900-01-01")[-1]["observed_facts"]["nested"]["groups"].clear()
    ns.effective_group_memory(state, "institution", "1900-01-01")[0]["observed_facts"]["nested"]["groups"].clear()
    assert state == before


def test_shared_raw_facts_do_not_create_new_public_memory_aliases():
    state = history()
    a, b = state["memories"]["observer"][:2]
    a["observed_facts"] = b["observed_facts"] = {"nested": []}
    memories = ns.effective_memory(state, "observer", "1900-01-01")
    memories[0]["observed_facts"]["nested"].append(1)
    assert memories[1]["observed_facts"] == {"nested": []}
    assert a["observed_facts"] == {"nested": []}


def test_borrowed_decision_reads_are_private_and_cannot_leak_through_saved_trace():
    state = history()
    ns.evaluate_ruler(state, "observer", "ruler", "1900-01-01")
    before = copy.deepcopy(state)
    private = ns._actor_read_view(state, "observer", "1900-01-01")
    assert "records" not in private and "accuracy" not in json.dumps(private)
    assert "event:new-good" not in ns.actor_view(state, "other", "1900-01-01")["beliefs"]
    options = [{"id": "relief", "kind": "crisis_response", "tags": ["shared-policy"],
        "outcomes": {"fiscal": -.1, "prosperity": .3}},
        {"id": "ignore", "kind": "crisis_response", "outcomes": {"fiscal": .1}}]
    first, second = random.Random(73), random.Random(73)
    decision = nd.decide(private, options, first)
    assert decision == nd.decide(historical_view(state, "observer", "1900-01-01"), options, second)
    assert first.getstate() == second.getstate() and state == before
    decision["basis"]["report_metadata"].clear()
    decision["evaluations"][0]["reason_trace"].clear()
    assert state == before


# Values generated from the archived crisis-v1-stable module, before any
# optimization. Exact equality includes legacy comparison, event ordering,
# decay and leap-day behavior, without importing the archive at test runtime.
RELATION_ORACLES = {
    "1897-01-01": [0.5602236589730767, 0.49847595525735855, 0.5034840878571121, 0,
        0.6112156203989507, .5, 0.057150825692831475, 0.024088862265760506, 0.504573990767692],
    "1897-01-03": [0.5677122090765496, 0.4795801250078118, 0.42029813961652973, 0,
        0.58093399082058, 0.4864106283338445, 0.05712578924966663, 0.09888005285230309, 0.4966405524095744],
    "1900-02-28": [0.5425534355417965, 0.4957801635890511, 0.4787692606286855, 0,
        0.5928865581142231, 0.4971917102636183, 0.049885875294002596, 0.03233409735925788, 0.499305758763549],
    "1900-03-01": [0.5425444643788702, 0.49578594022936806, 0.47879011037131547, 0,
        0.5928908201832157, 0.49719555460215015, 0.049883293671826964, 0.032310368276916385, 0.49930670912785824],
    "1927-01-01": [0.5360000096478283, 0.4999999937876466, 0.49399997757762937, 0,
        0.595999995416457, 0.49999999586569555, 0.0480000027763455, 0.015000025518889484, 0.4999999989779528],
}


@pytest.mark.parametrize("today", list(RELATION_ORACLES))
def test_exact_relationship_axes_match_checkpoint_oracle(today):
    state = history()
    relation = ns.evaluate_ruler(state, "observer", "ruler", today)
    assert [relation[axis] for axis in ns.RELATION_AXES] == RELATION_ORACLES[today]
    original = copy.deepcopy(state)
    relation["causes"].clear()
    assert state == original


def test_day_changes_and_backwards_queries_do_not_reuse_stale_decay():
    state = history()
    for today in ("1900-02-28", "1900-03-01", "1897-01-03", "1900-02-28"):
        assert ns.effective_memory(state, "observer", today) == historical_memories(state["memories"]["observer"], today)


@pytest.mark.parametrize("invalid", [None, [], date(1900, 1, 1), "19000101", "1900-02-29", "nonsense"])
def test_cached_dates_keep_canonical_input_validation(invalid):
    with pytest.raises(ValueError):
        ns._day(invalid)


def test_runtime_caches_are_bounded_scalar_only_and_not_serialized():
    state = history()
    ns.actor_view(state, "observer", "1900-01-01")
    assert runtime._parsed_day.cache_info().maxsize == 8192
    assert runtime._decay.cache_info().maxsize == 65536
    assert not any("cache" in key or "runtime" in key for key in state)
    assert ns.actor_view(json.loads(json.dumps(state)), "observer", "1900-01-01") == historical_view(state, "observer", "1900-01-01")


@pytest.mark.parametrize("field", ["reliability", "salience", "confidence"])
@pytest.mark.parametrize("enduring", [False, True])
def test_valid_signed_zero_memory_edits_keep_exact_original_weight(field, enduring):
    memory = {"received_date": "1900-01-01", "enduring": enduring,
              "reliability": 1.0, "salience": 1.0, "confidence": 1.0}
    elapsed = (date(1901, 1, 1) - date(1900, 1, 1)).days
    retention = .4 + .6 * math.exp(-elapsed / 7300) if enduring else math.exp(-elapsed / 730)
    runtime._decay.cache_clear()
    for value in (0.0, -0.0, 0.0, -0.0):
        memory[field] = value
        original = retention * memory["reliability"] * memory["salience"] * memory["confidence"]
        assert ns._memory_weight(memory, "1901-01-01").hex() == original.hex()
