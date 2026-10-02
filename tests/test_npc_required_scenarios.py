"""Requested faction, mobilization feedback, and loyal dissent scenarios."""
import random

import pytest

from worldline import Engine, World, Country, Region, Army
from worldline import npc, npc_decision, npc_state
from worldline.audit import Auditor
from worldline.npc_benchmark import campaign_world, verify_decision


def _evaluation(decision, option):
    return next(item for item in decision["evaluations"] if item["option"] == option)


def _world():
    return World("1897-01-01",
        countries={"a": Country("Fictional A", population=10000, gdp=4000, treasury=50),
                   "b": Country("Fictional B", population=10000, gdp=4000, treasury=500)},
        regions={"ra": Region("Home region", "a", 10000),
                 "rb": Region("Foreign region", "b", 10000)},
        armies={"army-a": Army("a", "ra", 100), "army-b": Army("b", "rb", 100)})


def _government():
    state = npc_state.initialize_state()
    npc_state.add_person(state, "leader", "a", "Ruler", role="ruler",
        authority=["economic_policy", "mobilize", "war_preparation"],
        traits={"risk_tolerance": .6}, preferences={"security": .8, "fiscal": .8})
    npc_state.configure_government(state, "a", "leader", autonomous=False)
    return state


def test_one_cabinet_army_navy_treasury_and_foreign_factions_recommend_different_policies():
    world = campaign_world()
    state = world.npc_state
    # Roles and interests are explicit scenario data, not country-name bonuses.
    officials = (
        ("army", "military", {"security": 1, "prestige": .8, "prosperity": 0,
                              "education": 0, "fiscal": .1},
         ["military_expansion"], {"security": 1, "prestige": .8}, "military_expansion"),
        ("navy", "military", {"security": 0, "prestige": .3, "prosperity": 1,
                              "education": 1, "fiscal": .2},
         ["development"], {"prosperity": 1, "education": 1}, "development"),
        ("treasury", "finance", {"fiscal": 1, "prosperity": .2, "security": 0, "prestige": 0},
         ["fiscal_consolidation"], {"fiscal": 1}, "fiscal_consolidation"),
        ("foreign", "diplomat", {"security": .6, "prosperity": .5, "fiscal": 1, "prestige": 0},
         ["diplomatic_engagement"], {"fiscal": 1, "security": .6}, "diplomatic_engagement"),
    )
    for actor, role, preferences, goals, interests, _ in officials:
        npc_state.add_person(state, actor, "a", actor, role=role,
                             preferences=preferences, goals=goals)
        npc_state.add_group(state, "group:" + actor, "a", actor,
            kind="military_branch" if actor in ("army", "navy") else "institution",
            members=[actor], interests=interests)
        npc_state.receive_report(state, actor, "diplomacy",
            {"target": "b", "own_strength": 100, "enemy_strength": 100, "threat": .2},
            world.date, "shared foreign report", confidence=.9, reliability=.9)
    npc_state.configure_government(state, "a", "a:ruler",
        cabinet=[official[0] for official in officials], autonomous=False)
    npc.sense(world)
    Auditor().check(world)

    choices, option_ids = {}, set()
    for actor, _, _, _, _, expected in officials:
        view, options, actual_quotes = npc.options_for(world, actor)
        assert view["person"]["country"] == "a"
        assert set(view["groups"]) == {"group:" + actor}
        assert view["beliefs"]["economy"]["country"] == "a"
        ids = {option["id"] for option in options}
        if option_ids:
            assert ids == option_ids  # all four officials assess the same choices
        option_ids = ids
        result = npc_decision.decide(view, options, random.Random(19))
        verify_decision(result)
        assert result["selected"] == expected
        assert any(term["factor"].startswith("group:group:" + actor + ":")
                   for term in result["reason_trace"])
        assert "true_state" not in next(o for o in options if o["id"] == expected).get("financial", {})
        assert actual_quotes  # true execution quotes stay outside the decision input
        choices[actor] = result["selected"]
    assert len(set(choices.values())) == 4


def test_actual_mobilization_and_military_budget_feedback_reduce_war_preparation_support(tmp_path):
    engine = Engine.create(tmp_path / "feedback.sqlite", _world(), 51)
    try:
        engine.enable_economy()
        engine.stabilize_economy()
        engine.enable_npc(_government())
        engine.advance(1, "month")

        def opinion():
            # Keep the foreign message fresh and unchanged: domestic feedback,
            # rather than intelligence correction or report aging, causes the change.
            engine.npc_report("leader", "diplomacy",
                {"target": "b", "own_strength": 300, "enemy_strength": 100, "threat": .7},
                source="unchanged foreign estimate", confidence=.9, reliability=.8)
            view, options, _ = npc.options_for(engine.true_state, "leader")
            alternatives = [o for o in options if o["id"] in ("status_quo", "war_preparation")]
            result = npc_decision.decide(view, alternatives, random.Random(1))
            verify_decision(result)
            return view, _evaluation(result, "war_preparation")

        before, supported = opinion()
        assert supported["eligible"] and supported["score"] > 0
        assert engine.mobilize("a", "ra", 1800)["status"] == "accepted"
        assert engine.set_policy("a", {"military": 220})["status"] == "accepted"
        engine.advance(1, "month")
        after, reconsidered = opinion()
        old, new = before["beliefs"]["economy"], after["beliefs"]["economy"]
        assert new["civilian_labour"] < old["civilian_labour"]
        assert new["gdp"] < old["gdp"] and new["revenue"] < old["revenue"]
        assert new["debt"] > old["debt"] and new["fiscal_risk"] > old["fiscal_risk"]
        assert after["report_metadata"]["economy"]["age_days"] == 0
        assert before["beliefs"]["diplomacy"] == after["beliefs"]["diplomacy"]
        assert reconsidered["eligible"]  # higher risk does not become a political veto
        assert reconsidered["score"] < supported["score"]
        earlier_risk = next(t for t in supported["reason_trace"] if t["factor"] == "perceived_risk")
        later_risk = next(t for t in reconsidered["reason_trace"] if t["factor"] == "perceived_risk")
        assert later_risk["contribution"] < earlier_risk["contribution"]
        earlier_balance = next(t for t in supported["reason_trace"] if t["factor"] == "belief:military_balance")
        later_balance = next(t for t in reconsidered["reason_trace"] if t["factor"] == "belief:military_balance")
        assert later_balance["contribution"] == pytest.approx(earlier_balance["contribution"])
        assert any(report.get("category") == "economic_performance"
                   for report in after["beliefs"].values())
        Auditor().check(engine.true_state)
    finally:
        engine.close()


def test_loyal_delegated_minister_dissents_but_passively_executes_preserved_player_command(tmp_path):
    state = _government()
    npc_state.add_person(state, "minister", "a", "Loyal treasury minister", role="finance",
        authority=["mobilize"], preferences={"fiscal": .95, "security": 0},
        traits={"personal_loyalty": 1, "institutional_loyalty": 1, "ideological_commitment": .2})
    npc_state.configure_government(state, "a", "leader", ["minister"], autonomous=False)
    # Established trust is derived from delivered, distinct historical events.
    for index, category in enumerate(("promise_kept", "promise_kept", "promise_kept",
                                      "life_saved", "life_saved", "administrative_performance",
                                      "economic_performance")):
        event = "known-background-" + str(index)
        npc_state.record_fact(state, event, "1896-12-31", "leader", category,
                              {"performance": 1}, "fictional known history")
        npc_state.deliver_record(state, "minister", event, as_of="1896-12-31",
                                  confidence=1, reliability=1)
    relationship = npc_state.evaluate_ruler(state, "minister", "leader", "1897-01-01")
    assert relationship["trust"] > .9 and relationship["perceived_legitimacy"] > .65

    engine = Engine.create(tmp_path / "loyal-dissent.sqlite", _world(), 51)
    try:
        engine.enable_economy()
        engine.stabilize_economy()
        engine.enable_npc(state)
        result = engine.mobilize("a", "ra", 100, order_id="player-mobilization")
        assert result["status"] == "accepted"
        action = next(item for item in engine.true_state.npc_state["actions"]
                      if item["actor"] == "minister" and item["order_id"] == "player-mobilization")
        response = action["response"]
        assert response["policy_score"] < -.15
        assert not response["relationships_used"]["ideological_opposition"]
        assert response["relationships_used"]["loyalty"] > .7
        responses = {item["kind"]: item for item in response["actions"]}
        assert {"dissent", "reconsider", "passive_execution"} <= set(responses)
        assert responses["passive_execution"]["can_execute"] is True
        assert response["execution_authorized"] is True
        assert action["implemented_command_preserved"] is True
        assert engine.orders["player-mobilization"]["status"] == "accepted"
        assert engine.true_state.economy["regions"]["ra"]["labour_state"]["mobilized"] == 100
        assert any(row["kind"] == "system_event" and
                   row["payload"].get("kind") == "npc_reaction" and
                   row["payload"]["action"]["actor"] == "minister" for row in engine.ledger())
        Auditor().check(engine.true_state)
    finally:
        engine.close()
