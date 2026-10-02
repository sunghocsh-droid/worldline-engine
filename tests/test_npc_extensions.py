"""Boundary regressions and required succession/cognition examples."""
import copy
import math
import random
import pytest
from worldline import npc, npc_state as ns, npc_decision as nd
from worldline.audit import Auditor
from worldline.npc_benchmark import CampaignReplay, campaign_world


def known(state, actor, event, subject, category, facts):
    ns.record_fact(state, event, "1897-01-01", subject, category, facts, "external outcome stub")
    ns.deliver_record(state, actor, event, confidence=1, reliability=1)
    ns.remember(state, actor, event, salience=1, reliability=1)


def succession(predecessor_performance, expectation=1):
    state = ns.initialize_state()
    for actor in ("observer", "old", "new"):
        ns.add_person(state, actor, "a", actor)
    known(state, "observer", "old-record", "old", "administrative_performance", {"performance": predecessor_performance})
    known(state, "observer", "new-record", "new", "administrative_performance", {"performance": .3})
    ns.configure_legacy(state, "new", "old", "observer", expectation=expectation)
    return state


def test_popular_predecessor_creates_a_higher_standard_without_altering_facts():
    demanding, ordinary = succession(1), succession(1, expectation=0)
    a = ns.evaluate_ruler(demanding, "observer", "new", "1897-01-01")
    b = ns.evaluate_ruler(ordinary, "observer", "new", "1897-01-01")
    assert a["respect"] < b["respect"]
    assert demanding["records"]["new-record"] == ordinary["records"]["new-record"]
    assert next(c for c in a["causes"] if c.get("event_id") == "new-record")["legacy_comparison"]["respect_contribution"] < 0


def test_tyrants_successor_can_receive_positive_relative_evaluation():
    relieved, neutral = succession(-1), succession(-1, expectation=0)
    a = ns.evaluate_ruler(relieved, "observer", "new", "1897-01-01")
    b = ns.evaluate_ruler(neutral, "observer", "new", "1897-01-01")
    assert a["respect"] > b["respect"] > .5
    assert next(c for c in a["causes"] if c.get("event_id") == "new-record")["legacy_comparison"]["known_predecessor_performance"] == -1


def test_secret_predecessor_success_cannot_set_observers_expectations():
    state = succession(0)
    del state["reports"]["observer"]["event:old-record"]
    state["memories"]["observer"] = [m for m in state["memories"]["observer"] if m["event_id"] != "old-record"]
    state["records"]["old-record"]["facts"]["performance"] = 1
    # The decision sees only delivered information even if hidden truth changes.
    assert ns.evaluate_ruler(state, "observer", "new", "1897-01-01")["respect"] == pytest.approx(.554)


def test_sunk_cost_memories_are_limited_by_attention_too():
    view = {"date": "1897-01-01", "person": {"id": "a", "abilities": {"intelligence": .5},
        "biases": {"sunk_cost_tendency": 1}}, "memories": [
        {"event_id": str(n), "tags": ["war"], "effective_weight": 1,
         "interpretation": {"continuation": True, "committed_resources": 1000}}
        for n in range(1000)]}
    option = {"id": "war", "kind": "war_preparation", "tags": ["war"], "outcomes": {}}
    result = nd.decide(view, [option], random.Random(2))
    cost = math.fsum(r["contribution"] for r in result["reason_trace"] if r["factor"] == "bias:sunk_cost_tendency")
    assert 0 < cost <= 1.2


def test_existing_scenario_record_ids_do_not_collide_with_automatic_records():
    world = campaign_world()
    ns.record_fact(world.npc_state, "record-1", world.date, "a:ruler", "promise_kept", {}, "scenario")
    original = copy.deepcopy(world.npc_state["records"]["record-1"])
    event = npc.record_outcome(world, "a:ruler", "promise_broken", {})
    assert event["record_id"] != "record-1"
    assert world.npc_state["records"]["record-1"] == original
    Auditor().check(world)


def test_manual_npc_action_ids_are_unique_without_a_time_step():
    world = campaign_world()
    option = {"country": "a", "kind": "economic_policy", "params": {"education": 2}}
    a, b = (npc.execute_intent(world, "a:ruler", option) for _ in range(2))
    assert a["status"] == b["status"] == "accepted"
    assert a["order"]["id"] != b["order"]["id"]


def test_knowledge_can_change_a_foreign_ruler_evaluation_without_global_reputation():
    world = campaign_world()
    state = world.npc_state
    known(state, "a:finance", "foreign-breach", "b:ruler", "treaty_broken", {})
    informed = ns.evaluate_ruler(state, "a:finance", "b:ruler", world.date)
    uninformed = ns.evaluate_ruler(state, "a:education", "b:ruler", world.date)
    assert informed["trust"] < uninformed["trust"]
    assert informed["respect"] != informed["affection"]
    assert "event:foreign-breach" not in ns.actor_view(state, "a:education", world.date)["beliefs"]


def test_council_scheduling_respects_a_leap_february():
    world = campaign_world()
    world.date = "1904-01-01"
    world.economy["last_settlement_date"] = world.date
    world.npc_state["adapter"]["last_settlement"] = world.date
    for government in world.npc_state["governments"].values():
        government["next_due"] = world.date
    replay = CampaignReplay(world)
    for _ in range(3):
        replay.month()
    for country in "abcd":
        months = [e["action"]["date"][:7] for e in replay.events if e["kind"] == "npc_action" and e["action"]["country"] == country]
        assert {"1904-01", "1904-02", "1904-03"} <= set(months)
        assert len(months) == len(set(months))


def test_delegated_local_actions_are_explicit_hooks_with_regional_limits():
    world = campaign_world()
    ns.add_person(world.npc_state, "local-a", "a", "Regional authority", role="governor",
                  region="ra", authority=["local_action"])
    option = {"country": "a", "kind": "local_action", "params": {
        "region": "ra", "action": "command_delay", "details": {"request": "reconsider supplies"}}}
    before = copy.deepcopy(world.economy)
    accepted = npc.execute_intent(world, "local-a", option)
    assert accepted["status"] == "accepted" and accepted["hook_only"] is True
    assert world.economy == before
    option["params"]["region"] = "rb"
    assert npc.execute_intent(world, "local-a", option)["status"] == "rejected"
    option = {"country": "a", "kind": "economic_policy", "params": {"military": 1000}}
    assert npc.execute_intent(world, "local-a", option)["status"] == "rejected"


def test_saved_npc_world_requires_the_runtime_adapter_explicitly():
    world = campaign_world()
    del world.npc_state["adapter"]
    with pytest.raises(ValueError, match="adapter"):
        Auditor().check(world)


@pytest.mark.parametrize("bucket", ["reports", "memories", "relationships"])
def test_missing_private_storage_bucket_is_a_clear_validation_error(bucket):
    world = campaign_world()
    del world.npc_state[bucket]["a:finance"]
    with pytest.raises(ValueError, match=f"missing personal {bucket}"):
        Auditor().check(world)


def test_inactive_person_cannot_execute_a_national_policy_or_claim_execution():
    world = campaign_world()
    world.npc_state["persons"]["a:ruler"]["active"] = False
    Auditor().check(world)
    option = {"id": "military", "country": "a", "kind": "economic_policy", "params": {"military": 1234}}
    assert npc.execute_intent(world, "a:ruler", option)["status"] == "rejected"
    view = ns.actor_view(world.npc_state, "a:ruler", world.date)
    assert nd.reaction(view, option)["execution_authorized"] is False
    assert nd.decide(view, [option], random.Random(1))["selected"] is None


def test_malformed_event_report_is_rejected_but_false_typed_reports_are_supported():
    world = campaign_world()
    state = world.npc_state
    malformed = {"event_id": "rumour", "subject": "a:ruler", "category": "promise_broken", "facts": "unknown"}
    with pytest.raises(ValueError, match="Malformed event"):
        ns.receive_report(state, "a:finance", "rumour", malformed, world.date, "rumour")
    assert "rumour" not in state["reports"]["a:finance"]
    malformed["facts"] = {"alleged_broken_promises": 10}
    ns.receive_report(state, "a:finance", "rumour", malformed, world.date, "rumour")
    assert ns.evaluate_ruler(state, "a:finance", "a:ruler", world.date)["trust"] < .5
    assert "rumour" not in state["records"]


def test_malformed_event_shape_cannot_be_smuggled_through_a_save():
    world = campaign_world()
    ns.receive_report(world.npc_state, "a:finance", "rumour", {"event_id": "rumour",
        "subject": "a:ruler", "category": "promise_broken", "facts": {}}, world.date, "source")
    world.npc_state["reports"]["a:finance"]["rumour"]["data"]["facts"] = []
    with pytest.raises(ValueError, match="malformed persisted state"):
        Auditor().check(world)


@pytest.mark.parametrize("region", ["unknown-region", "rb"])
def test_false_local_knowledge_yields_an_infeasible_option_instead_of_crashing(region):
    world = campaign_world()
    npc.sense(world)
    data = copy.deepcopy(world.npc_state["reports"]["a:ruler"]["economy"]["data"])
    data.update(regions=[region], fiscal_risk="unknown", revenue="unknown", reserves="unknown")
    ns.receive_report(world.npc_state, "a:ruler", "economy", data, world.date, "unreliable accounts")
    view, options, quotes = npc.options_for(world, "a:ruler")
    decision = nd.decide(view, options, random.Random(1))
    war = next(e for e in decision["evaluations"] if e["option"] == "war_preparation")
    assert not war["eligible"] and "physical_resources_missing" in war["eligibility_reasons"]
    assert quotes["war_preparation"]["physical_feasibility"]["feasible"] is False
    Auditor().check(world)
