"""Exact owner indexing and actor-phase query reuse, including raw edits."""
import copy
import json
import random

import pytest

from worldline import economy, npc, npc_state
from worldline.models import Region, World
from worldline.npc_benchmark import campaign_world
from worldline.performance_benchmark import load_baseline_modules
from worldline.state_runtime import Journal
from test_performance_npc_state import history


def query_world():
    return World("1897-01-01", regions={
        "z": Region("Z", "a"), "b": Region("B", "b"),
        "a": Region("A", "a"), "outside": Region("Unmodeled", "a")},
        economy={"regions": {"z": {}, "b": {}, "a": {}}})


def original_owned(world, country):
    return [key for key in sorted(world.economy["regions"]) if world.regions[key].owner == country]


def assert_owned(world):
    for country in ("a", "b", "absent", None, []):
        assert economy.owned_regions(world, country) == original_owned(world, country)


@pytest.mark.parametrize("tracked", [False, True])
def test_owner_query_observes_membership_owner_replacement_and_raw_regions(tracked):
    world = query_world()
    journal = Journal(world) if tracked else None
    assert_owned(world)
    world.regions["z"].owner = "b"
    world.regions["b"].population = 100
    assert_owned(world)
    world.economy["regions"]["outside"] = {}
    del world.economy["regions"]["a"]
    assert_owned(world)
    replacement = Region("Replacement", "b")
    world.regions["outside"] = replacement
    assert_owned(world)
    replacement.owner = "a"  # Not instrumented yet: no dictionary write.
    assert_owned(world)
    if journal:
        journal.barrier()
    replacement.owner = None
    assert_owned(world)
    replacement.owner = "b"
    assert_owned(world)
    replacement.owner = []  # Old equality scans also accept raw unhashable owners.
    assert_owned(world)
    replacement.owner = "a"
    assert_owned(world)
    world.economy["regions"] = {"z": {}}
    assert_owned(world)
    if journal:
        journal.barrier()
    assert_owned(world)


def test_owner_query_missing_region_failure_retries_and_rollback_rebuilds():
    world = query_world()
    journal = Journal(world)
    assert_owned(world)
    frame = journal.begin()
    world.regions["z"].owner = "b"
    world.economy["regions"]["missing"] = {}
    for _ in range(2):
        with pytest.raises(KeyError, match="missing"):
            economy.owned_regions(world, "a")
    del world.economy["regions"]["missing"]
    assert_owned(world)
    del world.regions["a"]
    with pytest.raises(KeyError, match="a"):
        economy.owned_regions(world, "b")
    journal.end(frame, rollback=True)
    assert_owned(world)
    assert economy.owned_regions(world, "a") == ["a", "z"]


def test_owner_index_handles_log_gap_detaches_results_and_stays_out_of_saves():
    world = query_world()
    Journal(world)
    assert_owned(world)
    for index in range(5000):
        world.regions["z"].owner = "a" if index % 2 else "b"
    assert_owned(world)
    result = economy.owned_regions(world, "a")
    result.clear()
    assert economy.owned_regions(world, "a") == ["a", "z"]
    raw = world.to_dict()
    saved = json.dumps(raw, sort_keys=True)
    assert "_active_indexes" not in saved and "_changes" not in saved
    restored = World.from_dict(json.loads(saved))
    Journal(restored)
    assert restored.to_dict() == raw
    assert_owned(restored)


def test_owner_index_rechecks_only_changed_regions_after_warmup():
    reads = []

    class CountingRegion(Region):
        def __getattribute__(self, name):
            if name == "owner":
                reads.append(object.__getattribute__(self, "name"))
            return object.__getattribute__(self, name)

    world = World("1897-01-01", regions={str(i): CountingRegion(str(i), "a" if i % 2 else "b")
        for i in range(1000)}, economy={"regions": {str(i): {} for i in range(1000)}})
    Journal(world)
    economy.owned_regions(world, "a")
    reads.clear()
    economy.owned_regions(world, "a")
    economy.owned_regions(world, "b")
    assert reads == []
    world.regions["1"].owner = "b"
    assert "1" not in economy.owned_regions(world, "a")
    assert reads == ["1"]


def test_labour_origin_fallback_is_queried_once_and_matches_frozen_calculation(monkeypatch):
    world = campaign_world()
    expected = copy.deepcopy(world)
    baseline = load_baseline_modules()
    baseline.economy.labour_and_technology(expected, "a", 30)
    calls = []
    lookup = economy.owned_regions

    def counted(*args):
        calls.append(args[1])
        return lookup(*args)

    monkeypatch.setattr(economy, "owned_regions", counted)
    economy.labour_and_technology(world, "a", 30)
    assert world.to_dict() == expected.to_dict()
    assert calls == ["a"]


@pytest.mark.parametrize("today", ["1897-01-03", "1900-02-28", "1900-03-01", "1927-01-01"])
def test_shared_actor_context_matches_frozen_relationships_and_order(today):
    state, baseline = history(), load_baseline_modules()
    expected = copy.deepcopy(state)
    context = npc_state._relationship_context(state, "observer", today)
    for target in ("old", "ruler", "other", "ruler"):
        assert npc_state.evaluate_ruler(state, "observer", target, today, _context=context) == (
            baseline.npc_state.evaluate_ruler(expected, "observer", target, today))
    assert state == expected


@pytest.mark.parametrize("subject", [[], {"raw": "subject"}])
def test_public_relationship_queries_observe_raw_unhashable_subject_and_next_edit(subject):
    state, baseline = history(), load_baseline_modules()
    state["reports"]["observer"]["event:new-good"]["data"]["subject"] = subject
    expected = copy.deepcopy(state)
    assert npc_state.evaluate_ruler(state, "observer", "ruler", "1900-01-01") == (
        baseline.npc_state.evaluate_ruler(expected, "observer", "ruler", "1900-01-01"))
    state["reports"]["observer"]["event:new-good"]["data"]["subject"] = "ruler"
    state["memories"]["observer"][0]["salience"] = .01
    expected = copy.deepcopy(state)
    assert npc_state.evaluate_ruler(state, "observer", "ruler", "1900-01-01") == (
        baseline.npc_state.evaluate_ruler(expected, "observer", "ruler", "1900-01-01"))


def test_npc_phase_reuses_one_actor_context_with_exact_trace_state_and_rng(monkeypatch):
    world, baseline = campaign_world(), load_baseline_modules()
    # Several targets force the historical actor-by-target history scans.
    for actor in world.npc_state["persons"]:
        country = world.npc_state["persons"][actor]["country"]
        for role in ("ruler", "finance", "military", "education"):
            npc_state.evaluate_ruler(world.npc_state, actor, country + ":" + role, world.date)
    expected = copy.deepcopy(world)
    first_rng, second_rng = random.Random(731), random.Random(731)
    expected_events = baseline.npc.tick(expected, {"days": 1}, second_rng)
    observations, calls = npc_state._observer_events, []

    def counted(state, actor, today):
        calls.append(actor)
        return observations(state, actor, today)

    monkeypatch.setattr(npc_state, "_observer_events", counted)
    events = npc.tick(world, {"days": 1}, first_rng)
    assert events == expected_events
    assert world.to_dict() == expected.to_dict()
    assert first_rng.getstate() == second_rng.getstate()
    assert sorted(calls) == sorted(world.npc_state["persons"])
