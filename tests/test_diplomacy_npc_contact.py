"""Contact advice reuses actual delivered experience and existing government rules."""
import copy

import pytest

from worldline import Engine, npc, npc_contact, npc_decision, npc_state, state_storage
from worldline.engine import encode
from test_diplomacy_intelligence_foundation import assert_clean, committed, fullstack
from test_intelligence_delivery import day


PLAYER = {"kind": "player", "id": "a:ruler"}
PERSON = {"kind": "person", "id": "b:ruler"}
SOURCE = {"domain": "trusted-contact-test", "id": "contact-history"}
PROPOSAL = "npc-contact-one"


def make_contact_engine(path, *, goals=(), rule="executive", minister=False,
                        proposal_delay=1, response_delay=2):
    game = fullstack(path)
    with game._transaction():
        state = game._world.npc_state
        state["persons"]["b:ruler"]["goals"] = list(goals)
        state["governments"]["b"].update(autonomous=True, decision_rule=rule)
        if minister:
            npc_state.add_person(state, "b:minister", "b", "Foreign minister", role="diplomat",
                                 authority=["diplomatic_engagement"], goals=["diplomacy"])
            state["governments"]["b"]["cabinet"] = ["b:minister"]
    game.enable_diplomacy_intelligence({"diplomacy": {
        "player_bindings": {"a:ruler": {"country_id": "a", "representative_person_id": "a:ruler"}},
        "reply_recipients": {"a:ruler": [PLAYER]},
        "proposal_delay_days": proposal_delay, "response_delay_days": response_delay,
    }})
    return game


@pytest.fixture
def contact_engine(tmp_path):
    game = make_contact_engine(tmp_path / "npc-contact.sqlite")
    yield game
    game.close()


def propose(game, *, recipient="b:ruler", proposal_id=PROPOSAL, topic="discussion"):
    return game.propose_contact("request-" + proposal_id, "a", "b", "a:ruler", recipient,
                               {"message": "Begin a discussion.", "topic": topic},
                               principal_ref=PLAYER, proposal_id=proposal_id)


def received_world(game, *, recipient="b:ruler", proposal_id=PROPOSAL):
    from worldline import diplomacy
    propose(game, recipient=recipient, proposal_id=proposal_id)
    world = game.true_state
    dispatch = next(record for record in world.diplomacy_state["dispatches"].values()
                    if record["payload"]["proposal_id"] == proposal_id)
    world.date = dispatch["delivery_date"]
    diplomacy.deliver_due(world)
    return world


def history(game, *, success, subject="a", topic="contact_history", recipient=PERSON,
            observation_id="prior-contact"):
    observation = game.ingest_observation(SOURCE, {"success": success}, subject=subject, topic=topic,
        as_of=game.true_state.date, observation_id=observation_id)
    dispatch = game.queue_intelligence_report(observation["id"], recipient,
                                              message_id="history-" + observation_id)
    day(game, dispatch["delivery_date"])
    game.tick_intelligence()


def decision_view(*, goals=(), active=True):
    return {"person": {"id": "advisor", "country": "b", "role": "ruler", "active": active,
            "abilities": {}, "traits": {}, "biases": {}, "preferences": {}, "goals": list(goals),
            "authority": [], "groups": []}, "date": "1897-01-01", "beliefs": {}, "groups": {},
            "memories": [], "institutional_memories": {}, "relationships": {}, "report_metadata": {}}


def alternatives():
    return [{"id": "accept_contact", "kind": "contact_response", "outcomes": {}, "tags": ["diplomacy"]},
            {"id": "reject_contact", "kind": "contact_response", "outcomes": {}, "tags": ["continuity"]}]


@pytest.mark.parametrize("goals,expected", [(["diplomacy"], "accept_contact"),
    (["continuity"], "reject_contact"), ([], "reject_contact")])
def test_deterministic_rank_reuses_existing_goal_terms_and_id_ties(goals, expected):
    view, options = decision_view(goals=goals), alternatives()
    before = copy.deepcopy((view, options))
    result = npc_decision.decide_deterministic(view, options)
    assert result["selected"] == expected
    assert npc_decision.decide_deterministic(view, list(reversed(options))) == result
    assert (view, options) == before
    assert all(term["factor"] != "bounded_deliberation_variation" for term in result["reason_trace"])


@pytest.mark.parametrize("options", [None, {}, [alternatives()[0], alternatives()[0]], [True]])
def test_deterministic_rank_preserves_option_validation(options):
    with pytest.raises(ValueError):
        npc_decision.decide_deterministic(decision_view(), options)


def test_deterministic_rank_does_not_convert_inactive_advice_into_a_response():
    assert npc_decision.decide_deterministic(decision_view(active=False), alternatives())["selected"] is None
    assert npc_decision.decide_deterministic(decision_view(), [])["selected"] is None


def government_state(rule):
    state = npc_state.initialize_state()
    npc_state.add_person(state, "ruler", "b", "Ruler", abilities={"persuasion": .5})
    npc_state.add_person(state, "minister", "b", "Minister", abilities={"persuasion": .5})
    npc_state.configure_government(state, "b", "ruler", ["minister"], decision_rule=rule)
    return state


def test_government_choice_preserves_executive_and_cabinet_tie_rules():
    state = government_state("executive")
    advice = {"ruler": {"selected": "reject_contact"}, "minister": {"selected": "accept_contact"}}
    before = copy.deepcopy((state, advice))
    assert npc.government_choice(state, "b", advice)["chosen"] == "reject_contact"
    state["governments"]["b"]["decision_rule"] = "cabinet"
    assert npc.government_choice(state, "b", advice)["chosen"] == "accept_contact"
    state["governments"]["b"]["decision_rule"] = "executive"
    assert (state, advice) == before
    assert npc.government_choice(state, "b", {"minister": advice["minister"]})["chosen"] == "accept_contact"
    assert npc.government_choice(state, "b", {}) is None


@pytest.mark.parametrize("goals,expected", [(["diplomacy"], "accept_contact"),
    (["continuity"], "reject_contact")])
def test_real_received_proposal_has_real_goal_based_advice(contact_engine, goals, expected):
    with contact_engine._transaction():
        contact_engine._world.npc_state["persons"]["b:ruler"]["goals"] = goals
    world = received_world(contact_engine)
    before = encode(world.to_dict())
    advice = npc_contact.recommend(world, PROPOSAL)
    assert advice["chosen"] == expected
    assert advice["members"] == ["b:ruler"]
    assert advice["executor"] == "b:ruler"
    assert encode(world.to_dict()) == before
    assert advice == npc_contact.recommend(world, PROPOSAL)
    assert any(term["factor"].startswith("goal:") for term in advice["recommendations"]["b:ruler"]["reason_trace"])


def test_queued_proposal_cannot_produce_advice(contact_engine):
    propose(contact_engine)
    world = contact_engine.true_state
    assert npc_contact.recommend(world, PROPOSAL) is None
    assert npc_contact.recommend(world, "unknown-proposal") is None


@pytest.mark.parametrize("success,expected", [(True, "accept_contact"), (False, "reject_contact")])
def test_direct_received_contact_history_changes_real_advice(contact_engine, success, expected):
    history(contact_engine, success=success)
    world = received_world(contact_engine)
    advice = npc_contact.recommend(world, PROPOSAL)
    assert advice["chosen"] == expected
    evaluation = next(e for e in advice["recommendations"]["b:ruler"]["evaluations"]
                      if e["option"] == "accept_contact")
    assert any(term["factor"].startswith("memory:") for term in evaluation["reason_trace"])


@pytest.mark.parametrize("subject,topic,recipient", [
    ("b", "contact_history", PERSON), ("a", "unrelated_topic", PERSON),
    ("a", "contact_history", {"kind": "person", "id": "a:ruler"}),
    ("a", "contact_history", PLAYER),
])
def test_unrelated_received_history_cannot_add_an_acceptance_bonus(contact_engine, subject, topic, recipient):
    history(contact_engine, success=True, subject=subject, topic=topic, recipient=recipient)
    advice = npc_contact.recommend(received_world(contact_engine), PROPOSAL)
    assert advice["chosen"] == "reject_contact"
    assert all(not term["factor"].startswith("memory:")
               for term in advice["recommendations"]["b:ruler"]["reason_trace"])


@pytest.mark.parametrize("category", ["promise_broken", "custom:diplomacy"])
def test_native_personal_delivered_memory_remains_a_reason(contact_engine, category):
    with contact_engine._transaction():
        state = contact_engine._world.npc_state
        npc_state.record_fact(state, "prior-broken-promise", contact_engine._world.date,
                             "a:ruler", category, {"success": False}, "known personal experience")
        npc_state.deliver_record(state, "b:ruler", "prior-broken-promise")
        npc_state.remember(state, "b:ruler", "prior-broken-promise", tags=["diplomacy"], salience=.8)
    advice = npc_contact.recommend(received_world(contact_engine), PROPOSAL)
    assert advice["chosen"] == "reject_contact"
    accepted = next(e for e in advice["recommendations"]["b:ruler"]["evaluations"]
                    if e["option"] == "accept_contact")
    assert any(term["factor"] == "memory:prior-broken-promise" for term in accepted["reason_trace"])


@pytest.mark.parametrize("metadata", [None, "native extension", {}, {"proposal_id": []}])
def test_generic_contact_metadata_does_not_break_or_discard_native_personal_experience(contact_engine, metadata):
    with contact_engine._transaction():
        state = contact_engine._world.npc_state
        npc_state.receive_report(state, "b:ruler", "event:native-extension",
            {"event_id": "native-extension", "day": contact_engine._world.date, "subject": "a:ruler",
             "category": "custom:diplomacy", "facts": {"success": False}, "diplomacy": metadata},
            contact_engine._world.date, "native known experience")
        npc_state.remember(state, "b:ruler", "native-extension", tags=["diplomacy"],
                           interpretation={"contact": metadata})
    advice = npc_contact.recommend(received_world(contact_engine), PROPOSAL)
    accepted = next(e for e in advice["recommendations"]["b:ruler"]["evaluations"]
                    if e["option"] == "accept_contact")
    assert any(term["factor"] == "memory:native-extension" for term in accepted["reason_trace"])


@pytest.mark.parametrize("trust,expected", [(.2, "reject_contact"), (.5, "reject_contact"), (.8, "accept_contact")])
def test_only_actual_known_centered_sender_relationship_influences_advice(contact_engine, trust, expected):
    with contact_engine._transaction():
        state = contact_engine._world.npc_state
        npc_state.evaluate_ruler(state, "b:ruler", "a:ruler", contact_engine._world.date)
        state["relationships"]["b:ruler"]["a:ruler"]["trust"] = trust
    advice = npc_contact.recommend(received_world(contact_engine), PROPOSAL)
    assert advice["chosen"] == expected
    if trust == .5:
        assert not any(term["factor"] == "social:expected_support"
                       for term in advice["recommendations"]["b:ruler"]["reason_trace"])


def test_hidden_world_truth_does_not_change_same_delivered_advice(contact_engine):
    history(contact_engine, success=True)
    world = received_world(contact_engine)
    hidden = copy.deepcopy(world)
    hidden.countries["a"].treasury = 999999
    hidden.armies["army-a"].personnel = 888888
    hidden.relations["a:b"] = {"a": "a", "b": "b", "status": "war", "secret": "unobserved"}
    npc_state.record_fact(hidden.npc_state, "hidden-foreign-outcome", hidden.date,
                         "a:ruler", "custom:hidden", {"success": False}, "secret source truth")
    assert npc_contact.recommend(world, PROPOSAL) == npc_contact.recommend(hidden, PROPOSAL)


@pytest.mark.parametrize("change", ["autonomous", "player_country", "inactive", "authority", "government_member"])
def test_no_automatic_response_without_current_policy_activity_authority_and_membership(contact_engine, change):
    world = received_world(contact_engine)
    state = world.npc_state
    if change == "autonomous":
        state["governments"]["b"]["autonomous"] = False
    elif change == "player_country":
        world.crisis_state["player_countries"].append("b")
    elif change == "inactive":
        state["persons"]["b:ruler"]["active"] = False
    elif change == "authority":
        state["persons"]["b:ruler"]["authority"] = []
    else:
        state["governments"]["b"]["ruler"] = None
    before = encode(world.to_dict())
    assert npc_contact.recommend(world, PROPOSAL) is None
    assert encode(world.to_dict()) == before


def test_cabinet_never_votes_or_executes_as_an_unreceived_member(tmp_path):
    game = make_contact_engine(tmp_path / "cabinet.sqlite", goals=["continuity"], rule="cabinet", minister=True)
    try:
        world = received_world(game, recipient="b:minister")
        advice = npc_contact.recommend(world, PROPOSAL)
        assert advice["members"] == ["b:minister"]
        assert advice["coalition"] == {"b:minister": "accept_contact"}
        assert advice["executor"] == "b:minister"
        assert "b:ruler" not in advice["recommendations"]
    finally:
        game.close()


def test_recommendation_is_detached_and_does_not_change_rng_or_schedules(contact_engine):
    with contact_engine._transaction():
        contact_engine._world.npc_state["persons"]["b:ruler"]["goals"] = ["diplomacy"]
    world = received_world(contact_engine)
    before = encode(world.to_dict()), contact_engine.rng.getstate()
    first = npc_contact.recommend(world, PROPOSAL)
    original = copy.deepcopy(first)
    first["recommendations"]["b:ruler"]["reason_trace"][0]["detail"]["outside"] = True
    assert npc_contact.recommend(world, PROPOSAL) == original
    assert (encode(world.to_dict()), contact_engine.rng.getstate()) == before


def test_other_incoming_proposals_do_not_dilute_received_experience_or_overwrite_reports(contact_engine):
    from worldline import diplomacy
    history(contact_engine, success=True)
    first = received_world(contact_engine)
    baseline = npc_contact.recommend(first, PROPOSAL)
    for index in range(12):
        propose(contact_engine, proposal_id="other-contact-" + str(index))
    together = contact_engine.true_state
    together.date = first.date
    diplomacy.deliver_due(together)
    recommendation = npc_contact.recommend(together, PROPOSAL)
    assert recommendation["recommendations"] == baseline["recommendations"]
    reports = [report for report in together.npc_state["reports"]["b:ruler"].values()
               if report["data"].get("message_kind") == "proposal"]
    assert len(reports) == 13
    assert {report["data"]["diplomacy"]["proposal_id"] for report in reports} == {
        PROPOSAL, *("other-contact-" + str(index) for index in range(12))}
    assert "diplomacy" not in together.npc_state["reports"]["b:ruler"]


@pytest.mark.parametrize("result,expected", [("accept", "accept_contact"), ("reject", "reject_contact")])
def test_prior_received_response_is_retained_as_learned_counterpart_history(contact_engine, result, expected):
    from worldline import diplomacy
    contact_engine.propose_contact("past-request", "b", "a", "b:ruler", "a:ruler",
        {"message": "Previous discussion.", "topic": "discussion"},
        principal_ref=PERSON, proposal_id="past-contact")
    past_dispatch = next(dispatch for dispatch in contact_engine.true_state.diplomacy_state["dispatches"].values()
                         if dispatch["proposal_id"] == "past-contact")
    day(contact_engine, past_dispatch["delivery_date"])
    contact_engine.tick_diplomacy()
    contact_engine.respond_contact("past-response", "past-contact", "a:ruler", result, principal_ref=PLAYER)
    response = next(dispatch for dispatch in contact_engine.true_state.diplomacy_state["dispatches"].values()
                    if dispatch["proposal_id"] == "past-contact" and dispatch["message_kind"] == "response")
    day(contact_engine, response["delivery_date"])
    contact_engine.tick_diplomacy()
    world = received_world(contact_engine)
    recommendation = npc_contact.recommend(world, PROPOSAL)
    assert recommendation["chosen"] == expected
    response_memory = next(memory for memory in world.npc_state["memories"]["b:ruler"]
                           if memory.get("interpretation", {}).get("contact", {}).get("proposal_id") == "past-contact")
    evaluation = next(e for e in recommendation["recommendations"]["b:ruler"]["evaluations"]
                      if e["option"] == "accept_contact")
    assert any(term["factor"] == "memory:" + response_memory["event_id"] for term in evaluation["reason_trace"])
    assert diplomacy.received_proposal(world, "b:ruler", PROPOSAL) is not None


@pytest.mark.parametrize("goals,status", [(["diplomacy"], "accepted"), (["continuity"], "rejected")])
def test_engine_advance_runs_real_response_and_in_transit_save_load(tmp_path, goals, status):
    game = make_contact_engine(tmp_path / "advance.sqlite", goals=goals)
    try:
        propose(game)
        assert game.diplomacy_view(PLAYER)["contacts"][0]["status"] == "pending"
        game.advance(1)
        assert game.true_state.diplomacy_state["proposals"][PROPOSAL]["status"] == status
        assert game.diplomacy_view(PLAYER)["contacts"][0]["status"] == "pending"
        save = game.save(tmp_path / "npc-response-in-transit.json")
        loaded = Engine.load(save, tmp_path / "npc-restored.sqlite")
        try:
            game.advance(2)
            loaded.advance(2)
            assert encode(game._stored_payload()) == encode(loaded._stored_payload())
            assert game.ledger() == loaded.ledger()
            assert game.diplomacy_view(PLAYER) == loaded.diplomacy_view(PLAYER)
            assert game.diplomacy_view(PLAYER)["contacts"][0]["status"] == status
            memories = game.true_state.npc_state["memories"]["b:ruler"]
            assert len({memory["event_id"] for memory in memories}) == len(memories)
            before = encode(game._stored_payload()), game.ledger(), game.rng.getstate()
            assert game.tick_diplomacy() == []
            assert (encode(game._stored_payload()), game.ledger(), game.rng.getstate()) == before
        finally:
            loaded.close()
    finally:
        game.close()


def test_first_play_slice_engine_only_contact_acceptance_survives_both_transit_saves(tmp_path):
    from worldline.diplomacy_perception import perception_key

    game = make_contact_engine(tmp_path / "first-play.sqlite", goals=["diplomacy"],
                               proposal_delay=2, response_delay=3)
    games = [game]

    def exact(engine):
        return (encode(engine._stored_payload()), engine.ledger(),
                state_storage.export_archive(engine.db), engine.rng.getstate(),
                engine.diplomacy_view(PLAYER), engine.diplomacy_view(PERSON))

    try:
        submission = propose(game)
        assert submission["kind"] == "submission"
        assert submission["status"] == "submitted"
        assert submission["proposal_id"] == PROPOSAL
        assert game.diplomacy_view(PERSON)["contacts"] == []
        assert game.diplomacy_view(PERSON)["history"] == []
        assert game.diplomacy_view(PLAYER)["contacts"][0]["status"] == "pending"

        proposal_save = game.save(tmp_path / "first-play-proposal-in-transit.json")
        proposal_loaded = Engine.load(proposal_save, tmp_path / "first-play-proposal-restored.sqlite")
        games.append(proposal_loaded)
        assert exact(proposal_loaded) == exact(game)
        for engine in games:
            engine.advance(2)
            state = engine.true_state.diplomacy_state
            direct = next(dispatch for dispatch in state["dispatches"].values()
                          if dispatch["proposal_id"] == PROPOSAL and dispatch["message_kind"] == "proposal")
            assert direct["recipient"] == PERSON and direct["status"] == "delivered"
            response = state["receipts"][state["proposals"][PROPOSAL]["response_receipt_id"]]
            assert response["result"] == "accepted" and response["executing_person_id"] == "b:ruler"
            assert response["proposal_delivery_receipt_id"] == direct["delivery_receipt_id"]
            assert response["authorization"]["decision_rule"] == "executive"
            assert "diplomatic_engagement" in response["authorization"]["authority"]
            decision = next(item for item in engine.true_state.npc_state["decisions"]
                            if item["id"] in response["decision_ids"])
            assert decision["actor"] == "b:ruler" and decision["selected"] == "accept_contact"
            assert decision["context"]["proposal_delivery_receipt_id"] == direct["delivery_receipt_id"]
            assert any(term["factor"] == "goal:diplomacy" for term in decision["reason_trace"])
            assert engine.diplomacy_view(PERSON)["contacts"][0]["proposal_received_at"] == direct["received_at"]
            pending = engine.diplomacy_view(PLAYER)["contacts"][0]
            assert pending["status"] == "pending" and pending["response"] is None
            assert not any(report["data"].get("message_kind") == "response"
                           for report in engine.observed_state(PLAYER["id"]).values())
        assert exact(proposal_loaded) == exact(game)

        response_save = game.save(tmp_path / "first-play-response-in-transit.json")
        response_loaded = Engine.load(response_save, tmp_path / "first-play-response-restored.sqlite")
        games.append(response_loaded)
        assert exact(response_loaded) == exact(game)
        for engine in games:
            engine.advance(3)
            contact = engine.diplomacy_view(PLAYER)["contacts"][0]
            assert contact["status"] == "accepted" and contact["response_received_at"] is not None
            assert contact["response"]["result"] == "accepted"
            observed = engine.observed_state(PLAYER["id"])[perception_key(contact["response"], "response")]
            assert observed["data"] == {"message_kind": "response", "contact": contact["response"]}
            before = committed(engine)
            for _ in range(2):
                assert engine.diplomacy_view(PLAYER)["contacts"][0] == contact
                engine.diplomacy_view(PERSON)
                assert engine.tick_diplomacy() == []
            assert_clean(engine, before)
        assert all(exact(engine) == exact(game) for engine in games[1:])
        saves = [engine.save(tmp_path / ("first-play-final-" + str(index) + ".json"))
                 for index, engine in enumerate(games)]
        assert len({path.read_bytes() for path in saves}) == 1
    finally:
        for engine in reversed(games):
            engine.close()
