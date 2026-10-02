"""Performance changes must preserve the frozen Crisis v1's political results.

The historical comparator is the verified, portable checkpoint fixture, never
current code relabeled as a baseline. Missing or altered historical source must
fail this comparison rather than silently skipping it.
"""
import copy
import json
import random

import pytest

from worldline import crisis, crisis_response, npc, npc_decision, npc_state
from worldline.crisis_benchmark import CrisisReplay
from worldline.models import World
from worldline.performance_benchmark import load_baseline_modules


@pytest.fixture(scope="module")
def baseline():
    package = load_baseline_modules()
    return {part: getattr(package, part) for part in
        ("npc", "npc_state", "npc_decision", "crisis", "crisis_response", "crisis_benchmark")}


def remembered_world():
    from worldline.npc_benchmark import campaign_world
    world = campaign_world()
    actor = "a:military"
    for index in range(80):
        event = f"performance-memory-{index}"
        npc_state.record_fact(world.npc_state, event, world.date, "a:ruler", "military_treatment",
            {"performance": .2, "unrelated_payload": {"country": "a", "detail": list(range(12))}}, "archived observation")
        npc_state.deliver_record(world.npc_state, actor, event)
        npc_state.remember(world.npc_state, actor, event,
            interpretation={"success": .7 if index % 2 else -.2, "security": .2, "fiscal": -.1,
                "tags": ["war"], "continuation": index % 4 == 0, "committed_resources": index},
            tags=["war"] if index % 3 else ["unrelated"], salience=.8)
    group = next(iter(world.npc_state["persons"][actor]["groups"]))
    for index in range(5):
        npc_state.remember_group(world.npc_state, group, f"performance-memory-{index}", actor,
            tags=["war"], interpretation={"success": -.4, "fiscal": -.3, "tags": ["war"]}, salience=.9)
    npc.sense(world)
    return world, actor


@pytest.mark.parametrize("seed", [0, 19, 771])
def test_prepared_memory_context_preserves_every_baseline_score_trace_reaction_and_rng(baseline, seed):
    world, actor = remembered_world()
    view, options, _ = npc.options_for(world, actor)
    before = copy.deepcopy((view, options))
    old_rng, new_rng = random.Random(seed), random.Random(seed)
    expected = baseline["npc_decision"].decide(view, options, old_rng)
    actual = npc_decision.decide(view, options, new_rng)
    assert actual == expected and new_rng.getstate() == old_rng.getstate()
    for option in options:
        assert npc_decision.reaction(view, option, actual) == baseline["npc_decision"].reaction(view, option, expected)
    assert (view, options) == before
    # Mutating an explanation returned by the pure stack must not mutate a
    # borrowed memory interpretation, group preference or received report.
    state_before = world.to_dict()
    entry = next(t for e in actual["evaluations"] for t in e["reason_trace"] if t["factor"].startswith("memory:"))
    entry["detail"]["interpretation"]["security"] = 999
    assert world.to_dict() == state_before and (view, options) == before


def test_public_and_internal_proposal_views_have_identical_values_without_public_aliases():
    world, actor = remembered_world()
    original = world.to_dict()
    detached = npc.options_for(world, actor)
    internal = npc.options_for(world, actor, _view=npc_state._actor_read_view(world.npc_state, actor, world.date))
    assert internal == detached
    detached[0]["person"]["traits"]["risk_tolerance"] = 999
    detached[0]["memories"][-1]["interpretation"]["security"] = 999
    detached[1][1]["params"]["education"] = 999999
    assert world.to_dict() == original


def test_new_decision_sees_changed_memory_and_new_information_without_stale_context(baseline):
    world, actor = remembered_world()
    view, options, _ = npc.options_for(world, actor)
    original = npc_decision.decide(view, options, random.Random(9))
    for memory in view["memories"]:
        memory["interpretation"].update(security=-.8, fiscal=-.9, success=-.9)
    view["report_metadata"]["economy"]["confidence"] = .2
    expected = baseline["npc_decision"].decide(view, options, random.Random(9))
    updated = npc_decision.decide(view, options, random.Random(9))
    assert updated == expected and updated != original


def test_public_crisis_options_remain_detached_and_equal_internal_read_projection(baseline):
    old = baseline["crisis_benchmark"].CrisisReplay()
    e = baseline["crisis"].create_event(old.world, "border_incident", "rc", ["c", "b"], severity=.8)
    baseline["crisis_response"].queue_report(old.world, e["event_id"], "c:ruler", delay=0)
    baseline["crisis_response"].deliver_due(old.world)
    world = World.from_dict(old.world.to_dict())
    expected = baseline["crisis_response"].responses_for(old.world, e["event_id"], "c:ruler")
    public = crisis_response.responses_for(world, e["event_id"], "c:ruler")
    internal = crisis_response.responses_for(world, e["event_id"], "c:ruler",
        _view=npc_state._actor_read_view(world.npc_state, "c:ruler", world.date))
    assert public == internal == expected
    before = world.to_dict()
    public[0]["beliefs"]["event:" + e["event_id"]]["facts"]["severity"] = 999
    public[1][0]["outcomes"]["security"] = 999
    public[2]["mobilize"]["expected_consequences"]["default_risk"] = 999
    assert world.to_dict() == before


@pytest.mark.parametrize("condition", ["unrelated", "uninformed", "no_force", "no_authority", "already_evaluated"])
def test_local_early_filters_preserve_baseline_state_side_effects_and_rng_without_view_work(baseline, monkeypatch, condition):
    old = baseline["crisis_benchmark"].CrisisReplay()
    actor = "c:local"
    kind = "flood" if condition == "unrelated" else "border_incident"
    e = baseline["crisis"].create_event(old.world, kind, "rc", ["c", "b"], severity=.8)
    if condition != "uninformed":
        baseline["crisis_response"].queue_report(old.world, e["event_id"], actor, delay=0)
        baseline["crisis_response"].deliver_due(old.world)
    if condition == "no_force":
        old.world.armies = {key: army for key, army in old.world.armies.items() if army.owner != "c"}
    elif condition == "no_authority":
        old.world.npc_state["persons"][actor]["authority"] = []
    elif condition == "already_evaluated":
        e["local_evaluated"] = [actor]
    new = World.from_dict(old.world.to_dict())
    def expensive_view(*args, **kwargs):
        pytest.fail("An ineligible local attempt must not construct a decision view")
    monkeypatch.setattr(npc_state, "_actor_read_view", expensive_view)
    monkeypatch.setattr(npc_state, "actor_view", expensive_view)
    expected = baseline["crisis_response"]._local(old.world, e, actor)
    actual = crisis_response._local(new, new.crisis_state["events"][e["event_id"]], actor)
    assert actual == expected is None
    assert new.to_dict() == old.world.to_dict()


def test_local_active_decision_and_detached_result_match_historical_checkpoint(baseline):
    old = baseline["crisis_benchmark"].CrisisReplay()
    e = baseline["crisis"].create_event(old.world, "border_incident", "rc", ["c", "b"], severity=.9,
        actual_facts={"central_intent": "avoid_conflict", "threat": .9})
    baseline["crisis_response"].queue_report(old.world, e["event_id"], "c:local", delay=0,
        intentional_distortion={"threat": .95})
    baseline["crisis_response"].deliver_due(old.world)
    new = World.from_dict(old.world.to_dict())
    expected = baseline["crisis_response"]._local(old.world, e, "c:local")
    actual = crisis_response._local(new, new.crisis_state["events"][e["event_id"]], "c:local")
    assert actual == expected and new.to_dict() == old.world.to_dict()
    before = new.to_dict()
    actual["decision"]["reason_trace"].append({"factor": "external edit", "contribution": 999})
    assert new.to_dict() == before


def test_one_month_full_production_campaign_matches_checkpoint_state_all_emissions_and_rng(baseline):
    old = baseline["crisis_benchmark"].CrisisReplay()
    new = CrisisReplay()
    for world, module in ((old.world, baseline["crisis"]), (new.world, crisis)):
        module.create_event(world, "flood", "ra", ["a"], severity=.7)
        module.create_event(world, "border_incident", "rb", ["b", "c"], severity=.8,
            actual_facts={"central_intent": "avoid_conflict", "threat": .8})
    assert new.world.to_dict() == old.world.to_dict()
    old.month()
    new.month()
    assert new.world.to_dict() == old.world.to_dict()
    assert new.rng.getstate() == old.rng.getstate()
    assert new.trace.to_dict() == old.trace.to_dict()
    assert new.trace.count > 0 and new.trace.kinds["npc_decision"] > 0


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"))


@pytest.mark.parametrize("arrival", [("z", "a"), ("a", "z")])
def test_duplicate_report_and_command_channels_preserve_checkpoint_first_match_and_local_rng(baseline, arrival):
    """An internal read view must preserve the old sorted-topic first match.

    Two legitimate channels can describe one incident differently. Arrival order
    must not silently replace the historical lexical-topic selection rule when
    replacing the detached JSON projection with a borrowed read projection.
    """
    old = baseline["crisis_benchmark"].CrisisReplay()
    actor = "c:local"
    event = baseline["crisis"].create_event(old.world, "border_incident", "rc", ["c", "b"],
        severity=.9, actual_facts={"central_intent": "avoid_conflict", "threat": .9})
    event_id = event["event_id"]
    for channel in arrival:
        npc_state.receive_report(old.world.npc_state, actor, channel + "-incident",
            {"event_id": event_id, "day": old.world.date, "subject": "crisis:" + event_id,
             "category": "custom:crisis_event", "facts": {"event_type": "border_incident",
                 "severity": .9 if channel == "a" else .1,
                 "threat": .95 if channel == "a" else .05}},
            old.world.date, "independent field observation", confidence=.3 if channel == "a" else .9)
        npc_state.receive_report(old.world.npc_state, actor, channel + "-command",
            {"event_id": event_id + ":command", "day": old.world.date, "subject": "crisis:" + event_id,
             "category": "custom:crisis_command", "facts": {"crisis_event_id": event_id,
                 "central_intent": "hold_fire" if channel == "a" else "patrol"}},
            old.world.date, "received command channel", confidence=.4 if channel == "a" else .8)
    new = World.from_dict(old.world.to_dict())
    untouched = copy.deepcopy(new.npc_state)
    expected_view = baseline["npc_state"].actor_view(old.world.npc_state, actor, old.world.date)
    actual_view = npc_state._actor_read_view(new.npc_state, actor, new.date)
    assert list(actual_view["beliefs"]) == list(expected_view["beliefs"])
    assert list(actual_view["report_metadata"]) == list(expected_view["report_metadata"])
    assert crisis_response._known(actual_view, event_id) == baseline["crisis_response"]._known(expected_view, event_id)
    assert crisis_response._known(actual_view, event_id)["threat"] == .95
    assert crisis_response._known_command(actual_view, event_id) == baseline["crisis_response"]._known_command(expected_view, event_id)
    assert crisis_response._known_command(actual_view, event_id)["central_intent"] == "hold_fire"
    _, expected_options, _ = baseline["crisis_response"].responses_for(old.world, event_id, actor)
    _, actual_options, _ = crisis_response.responses_for(new, event_id, actor, _view=actual_view)
    expected_rng, actual_rng = random.Random(37), random.Random(37)
    expected = baseline["npc_decision"].decide(expected_view, expected_options, expected_rng)
    actual = npc_decision.decide(actual_view, actual_options, actual_rng)
    assert _canonical(actual) == _canonical(expected)
    assert actual_rng.getstate() == expected_rng.getstate()
    assert new.npc_state == untouched
    # Exercise the production local adapter as well as its pure political score.
    expected_local = baseline["crisis_response"]._local(old.world, event, actor)
    actual_local = crisis_response._local(new, new.crisis_state["events"][event_id], actor)
    assert expected_local is not None
    assert _canonical(actual_local) == _canonical(expected_local)
    assert _canonical(new.to_dict()) == _canonical(old.world.to_dict())


def _permute_mappings(value, permutation):
    """Reorder JSON object keys only; historical list order stays unchanged."""
    if isinstance(value, dict):
        keys = sorted(value)
        if permutation == "reverse":
            keys.reverse()
        elif permutation == "rotate" and keys:
            keys = keys[1:] + keys[:1]
        return {key: _permute_mappings(value[key], permutation) for key in keys}
    if isinstance(value, list):
        return [_permute_mappings(item, permutation) for item in value]
    return copy.deepcopy(value)


@pytest.mark.parametrize("permutation", ["sorted", "reverse", "rotate"])
@pytest.mark.parametrize("seed", [0, 771])
def test_nested_mapping_and_metadata_fallback_orders_preserve_checkpoint_scores_traces_and_rng(baseline, permutation, seed):
    world, actor = remembered_world()
    state = world.npc_state
    # Multiple affiliations exercise group mapping order independently of the
    # deliberately preserved membership and institutional memory list order.
    state["persons"][actor]["groups"].append("a:development")
    state["groups"]["a:development"]["members"].append(actor)
    state["persons"][actor]["preferences"].update(prosperity=.73, fiscal=-.41, education=.38)
    for index in range(3):
        npc_state.remember_group(state, "a:development", f"performance-memory-{index}", actor,
            tags=["war"], interpretation={"fiscal": -.3, "security": .2, "success": -.4,
                "tags": ["war"], "lesson": "inherited institutional warning"}, salience=.9)
    _, options, _ = npc.options_for(world, actor)
    # Both default quality and missing-topic fallback must retain the original
    # sorted metadata accumulation sequence, not the raw report arrival order.
    for topic in ("economy", "military", "diplomacy"):
        state["reports"][actor].pop(topic, None)
    for index in (9, 1, 7, 0, 8, 2):
        npc_state.receive_report(state, actor, f"quality-{index}",
            {"analysis": {"z": index, "a": [index, {"z": .2, "a": .8}]}},
            world.date, "separate observed channel", confidence=(index + 1) / 11,
            reliability=(11 - index) / 12)
    world.npc_state = _permute_mappings(state, permutation)
    options = _permute_mappings(options, permutation)
    untouched = copy.deepcopy((world.npc_state, options))
    expected_view = baseline["npc_state"].actor_view(world.npc_state, actor, world.date)
    actual_view = npc_state._actor_read_view(world.npc_state, actor, world.date)
    assert list(actual_view["report_metadata"]) == list(expected_view["report_metadata"])
    assert npc_decision._information_quality(actual_view) == baseline["npc_decision"]._information_quality(expected_view)
    assert npc_decision._information_quality(actual_view, ("missing-topic",)) == baseline["npc_decision"]._information_quality(expected_view, ("missing-topic",))
    expected_rng, actual_rng = random.Random(seed), random.Random(seed)
    expected = baseline["npc_decision"].decide(expected_view, options, expected_rng)
    actual = npc_decision.decide(actual_view, options, actual_rng)
    assert _canonical(actual) == _canonical(expected)
    assert actual["selected"] == expected["selected"]
    assert actual_rng.getstate() == expected_rng.getstate()
    for option in options:
        assert _canonical(npc_decision.reaction(actual_view, option, actual)) == _canonical(
            baseline["npc_decision"].reaction(expected_view, option, expected))
    assert (world.npc_state, options) == untouched
