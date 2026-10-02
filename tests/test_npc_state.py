"""Private knowledge, immutable achievements and independent human judgements."""
import copy
import inspect
import json

import pytest

from worldline import npc_state as ns


def configured():
    state = ns.initialize_state()
    ns.add_person(state, "ruler", "a", "Ruler", role="ruler", authority=["economic_policy"])
    ns.add_person(state, "observer", "a", "Observer", role="finance", authority=["fiscal_action"])
    ns.add_person(state, "other", "a", "Other")
    ns.add_person(state, "foreign", "b", "Foreign")
    ns.configure_government(state, "a", "ruler", ["observer", "other"], next_due="1897-02-01")
    return state


def known(state, event_id, category, facts=None, actor="observer", subject="ruler", day="1897-01-01", **memory_kwargs):
    ns.record_fact(state, event_id, day, subject, category, facts or {}, "test ledger")
    ns.deliver_record(state, actor, event_id, accuracy=1, confidence=1, reliability=1)
    return ns.remember(state, actor, event_id, reliability=1, salience=1, **memory_kwargs)


def assert_valid(state, today="1897-01-01"):
    assert ns.validate(state, {"a", "b"}, {"ra", "rb"}, today) == []


def test_person_models_independent_abilities_traits_and_biases():
    state = ns.initialize_state()
    person = ns.add_person(state, "minister", "a", "Minister", role="finance",
        abilities={"finance": .9, "military": .1},
        traits={"ambition": .8, "personal_loyalty": .2, "institutional_loyalty": .9},
        biases={"overconfidence": .7, "optimism": -.4},
        preferences={"prosperity": .9, "war": -.7}, identities={"religion": "historical belief"})
    assert set(person["abilities"]) == set(ns.ABILITIES)
    assert set(person["traits"]) == set(ns.TRAITS)
    assert set(person["biases"]) == set(ns.BIASES)
    assert person["abilities"]["finance"] != person["abilities"]["military"]
    assert person["traits"]["personal_loyalty"] != person["traits"]["institutional_loyalty"]
    assert person["biases"]["optimism"] < 0
    assert_valid(state)


@pytest.mark.parametrize("overrides", [
    {"abilities": {"finance": 1.1}}, {"traits": {"risk_tolerance": True}},
    {"biases": {"optimism": -1.1}}, {"biases": {"sunk_cost_tendency": -1}},
    {"abilities": {"invented_ability": .5}}, {"authority": ["unlimited_access"]},
    {"preferences": {"growth": float("nan")}}, {"importance": 2},
])
def test_invalid_person_fields_rejected_without_partial_insert(overrides):
    state = ns.initialize_state()
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        ns.add_person(state, "bad", "a", "Bad", **overrides)
    assert state == before


def test_groups_are_collective_actors_with_members_and_institutions():
    state = configured()
    group = ns.add_group(state, "finance_office", "a", "Treasury", kind="institution",
        members=["observer", "other"], leadership="observer", preferences={"fiscal": .9},
        influence=.7, institutional_access=.9, resources={"clerks": 40}, cohesion=.8)
    assert group["leadership"] == ["observer"]
    assert group["interests"] == {"fiscal": .9}
    assert state["persons"]["observer"]["groups"] == ["finance_office"]
    assert "finance_office" not in state["persons"]["foreign"]["groups"]
    assert_valid(state)


def test_group_cannot_silently_import_foreign_member_or_negative_resources():
    state = configured()
    for members, resources in ((["foreign"], {}), (["observer"], {"clerks": -1})):
        before = copy.deepcopy(state)
        with pytest.raises(ValueError):
            ns.add_group(state, "bad", "a", "Bad", members=members, resources=resources)
        assert state == before


def test_configured_government_has_regime_and_known_ruler():
    state = configured()
    ns.configure_government(state, "a", "ruler", ["observer"], regime="constitutional_monarchy", decision_rule="executive")
    assert ns.actor_view(state, "observer", "1897-01-01")["ruler_id"] == "ruler"
    with pytest.raises(ValueError, match="same-country"):
        ns.configure_government(state, "a", "foreign")
    assert_valid(state)


def test_objective_record_is_immutable_and_does_not_inform_actors():
    state = configured()
    record = ns.record_fact(state, "hidden", "1897-01-01", "ruler", "economic_performance", {"performance": -1}, "measured result")
    assert ns.actor_view(state, "observer", "1897-01-01")["beliefs"] == {}
    assert ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")["respect"] == .5
    with pytest.raises(ValueError, match="immutable"):
        ns.record_fact(state, "hidden", "1897-01-01", "ruler", "economic_performance", {"performance": 1}, "measured result")
    record["facts"]["performance"] = 1
    assert state["records"]["hidden"]["facts"]["performance"] == -1
    assert_valid(state)


def test_memory_requires_delivered_experience():
    state = configured()
    ns.record_fact(state, "private", "1897-01-01", "ruler", "promise_broken", {}, "hidden archive")
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="never delivered"):
        ns.remember(state, "observer", "private")
    assert state == before


def test_false_report_affects_belief_without_leaking_objective_truth():
    state = configured()
    ns.record_fact(state, "harvest", "1897-01-01", "ruler", "economic_performance", {"performance": -1}, "archive")
    ns.receive_report(state, "observer", "event:harvest", {"event_id": "harvest", "day": "1897-01-01",
        "subject": "ruler", "category": "economic_performance", "facts": {"performance": 1}},
        "1897-01-01", "misleading ministry", accuracy=0, confidence=1, reliability=1)
    memory = ns.remember(state, "observer", "harvest", reliability=1, salience=1,
        interpretation={"lesson": "the ruler improved production"}, emotional_valence=.8)
    assert memory["observed_facts"]["performance"] == 1
    relation = ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")
    assert relation["respect"] > .5
    view = ns.actor_view(state, "observer", "1897-01-01")
    assert "records" not in view and "accuracy" not in view["report_metadata"]["event:harvest"]
    assert view["beliefs"]["event:harvest"]["facts"]["performance"] == 1
    assert state["records"]["harvest"]["facts"]["performance"] == -1
    assert ns.actor_view(state, "other", "1897-01-01")["beliefs"] == {}
    assert_valid(state)


def test_actor_view_and_report_inputs_are_deeply_detached():
    state = configured()
    data = {"treasury": 10, "nested": {"cost": 50}}
    ns.receive_report(state, "observer", "economy", data, "1897-01-01", "accounts")
    data["nested"]["cost"] = 999
    before = copy.deepcopy(state)
    view = ns.actor_view(state, "observer", "1897-01-01")
    view["person"]["abilities"]["finance"] = 1
    view["beliefs"]["economy"]["nested"]["cost"] = 999
    assert state == before
    assert state["reports"]["observer"]["economy"]["data"]["nested"]["cost"] == 50
    assert "world" not in inspect.signature(ns.actor_view).parameters


def test_future_information_is_not_visible_and_dates_are_audited():
    state = configured()
    ns.receive_report(state, "observer", "tomorrow", {"result": 1}, "1897-01-02", "dispatch")
    assert ns.actor_view(state, "observer", "1897-01-01")["beliefs"] == {}
    assert any("invalid delivered report" in error for error in ns.validate(state, {"a", "b"}, {}, "1897-01-01"))


def test_kindness_and_incompetence_can_produce_affection_without_respect():
    state = configured()
    known(state, "kind", "pardon", {"kindness": 1}, personal_impact=.8)
    known(state, "failure", "administrative_performance", {"performance": -1})
    relation = ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")
    assert relation["affection"] > .7
    assert relation["respect"] < .5
    assert relation["obligation"] > 0
    assert relation["perceived_legitimacy"] < .5


def test_competence_does_not_erase_personal_grievance():
    state = configured()
    known(state, "efficient", "economic_performance", {"performance": 1})
    known(state, "family", "family_loss", {"severity": 1}, personal_impact=-1)
    relation = ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")
    assert relation["respect"] > .5
    assert relation["affection"] < .5
    assert relation["grievance"] > .3
    assert relation["fear"] > 0


def test_repeated_broken_promises_reduce_trust_without_repeated_tick_penalty():
    state = configured()
    known(state, "first", "promise_broken")
    first = ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")
    for index in range(1, 4):
        known(state, f"broken-{index}", "promise_broken")
    after = ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")
    assert after["trust"] < first["trust"]
    assert after["grievance"] > first["grievance"]
    assert ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01") == after
    assert len(after["causes"]) == 4


def test_major_memories_endure_while_routine_news_fades_over_decades():
    state = configured()
    known(state, "routine", "promise_broken")
    known(state, "enduring", "life_saved", personal_impact=1)
    memories = {m["event_id"]: m for m in ns.effective_memory(state, "observer", "1927-01-01")}
    assert memories["routine"]["effective_weight"] < .001
    assert memories["enduring"]["effective_weight"] > .4
    assert memories["enduring"]["enduring"]
    assert state["memories"]["observer"][1]["observed_facts"] == {}


def test_reinterpreting_one_event_does_not_duplicate_experience():
    state = configured()
    known(state, "promise", "promise_broken", interpretation={"lesson": "unreliable"})
    ns.remember(state, "observer", "promise", interpretation={"lesson": "misunderstanding"}, salience=.1)
    assert len(state["memories"]["observer"]) == 1
    assert state["memories"]["observer"][0]["interpretation"] == {"lesson": "misunderstanding"}
    assert_valid(state)


def test_institutional_history_requires_explicit_member_information_sharing():
    state = configured()
    ns.add_group(state, "navy", "a", "Navy", kind="military_branch", members=["observer", "other"])
    known(state, "defeat", "war_result", {"performance": -1})
    assert ns.actor_view(state, "other", "1897-01-01")["institutional_memories"]["navy"] == []
    ns.remember_group(state, "navy", "defeat", "observer", interpretation={"lesson": "supply failed"}, salience=1, reliability=1)
    view = ns.actor_view(state, "other", "1897-01-01")
    assert view["memories"] == []
    assert view["institutional_memories"]["navy"][0]["observed_facts"]["performance"] == -1
    assert ns.evaluate_ruler(state, "other", "ruler", "1897-01-01")["respect"] < .5
    assert ns.actor_view(state, "foreign", "1897-01-01")["institutional_memories"] == {}
    with pytest.raises(ValueError, match="group-member"):
        ns.remember_group(state, "navy", "defeat", "foreign")
    assert_valid(state)


def test_institution_retains_history_after_informant_retires_or_leaves():
    state = configured()
    ns.add_group(state, "office", "a", "Office", kind="institution", members=["observer", "other"])
    known(state, "betrayal", "ally_betrayed")
    ns.remember_group(state, "office", "betrayal", "observer", salience=1, reliability=1, enduring=True)
    state["persons"]["observer"]["active"] = False
    state["persons"]["observer"]["groups"] = []
    state["groups"]["office"]["members"] = ["other"]
    assert ns.actor_view(state, "other", "1907-01-01")["institutional_memories"]["office"]
    assert ns.actor_view(state, "observer", "1907-01-01")["institutional_memories"] == {}
    assert_valid(state, "1907-01-01")


def test_successor_does_not_automatically_inherit_predecessor_achievements():
    state = configured()
    ns.add_person(state, "successor", "a", "Successor", role="ruler")
    known(state, "old-success", "economic_performance", {"performance": 1})
    predecessor = ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")
    successor = ns.evaluate_ruler(state, "observer", "successor", "1897-01-01")
    assert predecessor["respect"] > .5 and successor["respect"] == .5
    ns.configure_legacy(state, "successor", "ruler", "observer", expectation=.9, gratitude=.5, projection=.5)
    biased = ns.evaluate_ruler(state, "observer", "successor", "1897-01-01")
    assert .5 < biased["respect"] < predecessor["respect"]
    assert state["records"]["old-success"]["subject"] == "ruler"
    assert "old-success" not in state["rulers"].get("successor", {}).get("records", [])
    known(state, "new-failure", "economic_performance", {"performance": -1}, subject="successor")
    actual = ns.evaluate_ruler(state, "observer", "successor", "1897-01-01")
    assert actual["respect"] < .5
    assert_valid(state)


def test_inherited_gratitude_and_grievance_are_observer_specific():
    state = configured()
    ns.add_person(state, "heir", "a", "Heir", role="ruler")
    ns.configure_legacy(state, "heir", "ruler", "observer", gratitude=1)
    ns.configure_legacy(state, "heir", "ruler", "other", grievance=1)
    admirer = ns.evaluate_ruler(state, "observer", "heir", "1897-01-01")
    critic = ns.evaluate_ruler(state, "other", "heir", "1897-01-01")
    assert admirer["affection"] > critic["affection"]
    assert admirer["grievance"] < critic["grievance"]
    assert ns.actor_view(state, "foreign", "1897-01-01")["legacy"] == {}


def test_dynastic_legitimacy_does_not_award_unearned_competence():
    state = configured()
    ns.add_person(state, "heir", "a", "Heir", role="ruler")
    ns.configure_legacy(state, "heir", "ruler", "observer", dynastic_legitimacy=.9)
    relation = ns.evaluate_ruler(state, "observer", "heir", "1897-01-01")
    assert relation["perceived_legitimacy"] > .5
    assert relation["respect"] == .5
    assert relation["trust"] == .5
    assert not state["records"]


def test_succession_cycles_are_rejected_without_mutating_existing_context():
    state = configured()
    ns.add_person(state, "heir", "a", "Heir")
    ns.configure_legacy(state, "heir", "ruler", "observer")
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="cycle"):
        ns.configure_legacy(state, "ruler", "heir", "observer")
    assert state == before


def test_json_resume_preserves_information_memory_and_ruler_evaluation():
    left = configured()
    known(left, "family", "family_loss", personal_impact=-1, interpretation={"lesson": "avoid royal pledges"})
    ns.evaluate_ruler(left, "observer", "ruler", "1897-01-01")
    right = json.loads(json.dumps(left))
    assert left == right
    assert ns.actor_view(left, "observer", "1907-01-01") == ns.actor_view(right, "observer", "1907-01-01")
    assert ns.evaluate_ruler(left, "observer", "ruler", "1907-01-01") == ns.evaluate_ruler(right, "observer", "ruler", "1907-01-01")
    assert_valid(right, "1907-01-01")


def test_validator_detects_record_tampering_and_historical_deletion():
    state = configured()
    known(state, "promise", "promise_kept")
    before = copy.deepcopy(state)
    state["records"]["promise"]["facts"]["performance"] = -1
    errors = ns.validate(state, {"a", "b"}, {}, "1897-01-01", before)
    assert any("changed objective record" in error for error in errors)
    assert any("rewritten historical" in error for error in errors)
    state = copy.deepcopy(before)
    del state["records"]["promise"]
    assert any("removed or rewritten" in error for error in ns.validate(state, {"a", "b"}, {}, "1897-01-01", before))


def test_validator_detects_orphaned_information_and_invalid_schedule():
    state = configured()
    state["reports"]["ghost"] = {}
    state["schedules"]["observer"] = {"next_due": "1897-02-01", "last_evaluated": "1898-01-01"}
    errors = ns.validate(state, {"a", "b"}, {}, "1897-01-01")
    assert any("unknown report observer" in error for error in errors)
    assert any("future actor evaluation" in error for error in errors)


def test_empty_performance_report_does_not_create_unearned_respect():
    state = configured()
    known(state, "unknown-performance", "economic_performance", {"note": "data unavailable"})
    relation = ns.evaluate_ruler(state, "observer", "ruler", "1897-01-01")
    assert relation["respect"] == .5 and relation["trust"] == .5
    assert relation["causes"][0]["axis_contributions"]["respect"] == 0


def test_custom_categories_require_explicit_extension_prefix():
    state = configured()
    with pytest.raises(ValueError, match="custom:"):
        ns.record_fact(state, "typo", "1897-01-01", "ruler", "unknown", {}, "hook")
    ns.record_fact(state, "extension", "1897-01-01", "ruler", "custom:religious_ceremony", {}, "hook")
    assert_valid(state)
