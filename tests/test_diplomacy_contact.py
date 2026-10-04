"""Contact submission, explicit delivery and replies obey actor boundaries."""
import copy

import pytest

from worldline import Engine, npc_state
from worldline.engine import encode
from test_crisis_integration import make
from test_diplomacy_intelligence_foundation import assert_clean, committed, fullstack


PERSON_A = {"kind": "person", "id": "a:ruler"}
PERSON_B = {"kind": "person", "id": "b:ruler"}
PLAYER_A = {"kind": "player", "id": "a:ruler"}
PLAYER_B = {"kind": "player", "id": "b:ruler"}
CONTENT = {"message": "Let us open a peaceful diplomatic contact.", "topic": "contact"}


def contact_engine(path, *, reply_recipients=None, proposal_delay=2, response_delay=3):
    """Two bound players keep a received proposal pending until a manual reply."""
    game = make(path, players=("a", "b"))
    game.enable_military()
    with game._transaction():
        for country in "ab":
            npc_state.add_person(game._world.npc_state, country + ":observer", country,
                                 country.upper() + " Observer")
    config = {"proposal_delay_days": proposal_delay, "response_delay_days": response_delay,
        "player_bindings": {country + ":ruler": {
            "country_id": country, "representative_person_id": country + ":ruler"}
            for country in "ab"}}
    if reply_recipients is not None:
        config["reply_recipients"] = reply_recipients
    game.enable_diplomacy_intelligence({"diplomacy": config})
    return game


@pytest.fixture
def engine(tmp_path):
    game = contact_engine(tmp_path / "contact.sqlite")
    yield game
    game.close()


def propose(game, *, request_id="proposal-request", proposal_id="contact-one", **overrides):
    values = {"proposer_country_id": "a", "recipient_country_id": "b",
              "sender_person_id": "a:ruler", "recipient_person_id": "b:ruler",
              "content": copy.deepcopy(CONTENT), "principal_ref": copy.deepcopy(PLAYER_A)}
    values.update(overrides)
    return game.propose_contact(request_id, proposal_id=proposal_id, **values)


def respond(game, *, proposal_id="contact-one", request_id="response-request",
            result="accept", **overrides):
    values = {"executing_person_id": "b:ruler", "principal_ref": copy.deepcopy(PLAYER_B)}
    values.update(overrides)
    return game.respond_contact(request_id, proposal_id, accept_or_reject=result, **values)


def day(game, value):
    with game._transaction():
        game._world.date = value


def dispatches(game, receipt):
    state = game.true_state.diplomacy_state
    return [state["dispatches"][identity] for identity in receipt["dispatch_ids"]]


def receive(game, receipt):
    due = max(item["delivery_date"] for item in dispatches(game, receipt))
    day(game, due)
    return game.tick_diplomacy()


def contact(game, recipient, proposal_id="contact-one"):
    return next(item for item in game.diplomacy_view(recipient)["contacts"]
                if item["proposal_id"] == proposal_id)


def reports_for(game, person, proposal_id="contact-one"):
    return [report for report in game.true_state.npc_state["reports"][person].values()
            if proposal_id in encode(report)]


def test_frozen_submission_is_known_only_to_explicit_principal_and_sender(engine):
    before = engine.true_state.to_dict(), engine.observed, engine.rng.getstate()
    content = {"message": "Private peaceful invitation"}
    receipt = propose(engine, content=content)
    assert receipt["status"] == "submitted"
    assert receipt["request_id"] == "proposal-request"
    assert receipt["proposal_id"] == "contact-one"
    assert receipt["date"] == before[0]["date"]
    queued = dispatches(engine, receipt)
    assert len(queued) == 1
    assert queued[0]["recipient"] == PERSON_B
    assert queued[0]["status"] == "queued"
    assert queued[0]["received_at"] is None
    assert queued[0]["delivery_date"] == "1897-01-03"
    assert contact(engine, PLAYER_A)["status"] == "pending"
    assert contact(engine, PERSON_A)["status"] == "pending"
    for recipient in (PERSON_B, PLAYER_B, {"kind": "person", "id": "a:observer"}):
        assert engine.diplomacy_view(recipient)["contacts"] == []
        assert engine.diplomacy_view(recipient)["history"] == []
    assert engine.true_state.npc_state == before[0]["npc_state"]
    assert engine.observed == before[1]
    assert engine.rng.getstate() == before[2]
    content["message"] = "Changed by caller"
    receipt["dispatch_ids"].clear()
    assert "Private peaceful invitation" in encode(engine.true_state.diplomacy_state)
    assert "Changed by caller" not in encode(engine.true_state.diplomacy_state)
    assert len(propose(engine, content={"message": "Private peaceful invitation"})["dispatch_ids"]) == 1


@pytest.mark.parametrize("result, status", [("accept", "accepted"), ("reject", "rejected")])
def test_full_manual_round_trip_reveals_result_only_after_reply_delivery(engine, result, status):
    submission = propose(engine)
    before = committed(engine)
    assert engine.tick_diplomacy() == []
    assert_clean(engine, before)
    with pytest.raises(ValueError):
        respond(engine, result=result)
    assert_clean(engine, before)
    receive(engine, submission)
    received = contact(engine, PERSON_B)
    assert received["status"] == "pending"
    assert received["proposal_received_at"] == "1897-01-03"
    assert len(reports_for(engine, PERSON_B["id"])) == 1
    assert len(engine.true_state.npc_state["memories"][PERSON_B["id"]]) == 1
    assert engine.diplomacy_view(PLAYER_B)["history"] == []
    assert engine.diplomacy_view(PLAYER_B)["contacts"] == []
    response = respond(engine, result=result)
    assert response["result"] == status
    assert response["executing_person_id"] == PERSON_B["id"]
    assert len(dispatches(engine, response)) == 1
    assert dispatches(engine, response)[0]["recipient"] == PLAYER_A
    assert dispatches(engine, response)[0]["delivery_date"] == "1897-01-06"
    assert contact(engine, PERSON_B)["status"] == status
    assert contact(engine, PLAYER_B)["status"] == status
    assert contact(engine, PLAYER_A)["status"] == "pending"
    assert contact(engine, PLAYER_A)["response"] is None
    assert contact(engine, PERSON_A)["status"] == "pending"
    assert engine.observed_state(PLAYER_A["id"]) == {}
    receive(engine, response)
    sender = contact(engine, PLAYER_A)
    assert sender["status"] == status
    assert sender["response_received_at"] == "1897-01-06"
    assert sender["response_executed_at"] == "1897-01-03"
    assert sender["response"]["result"] == status
    assert engine.observed_state(PLAYER_A["id"])
    assert contact(engine, PERSON_A)["status"] == "pending"
    before = committed(engine)
    assert engine.tick_diplomacy() == []
    assert propose(engine) == submission
    assert respond(engine, result=result) == response
    assert_clean(engine, before)


def test_configured_person_reply_does_not_update_same_id_player(tmp_path):
    game = contact_engine(tmp_path / "person-route.sqlite",
                          reply_recipients={PLAYER_A["id"]: [PERSON_A]})
    try:
        receive(game, propose(game))
        response = respond(game)
        receive(game, response)
        assert contact(game, PERSON_A)["status"] == "accepted"
        assert contact(game, PLAYER_A)["status"] == "pending"
        assert game.observed_state(PLAYER_A["id"]) == {}
        assert len(reports_for(game, PERSON_A["id"])) == 1
        assert game.diplomacy_view({"kind": "person", "id": "a:observer"})["contacts"] == []
    finally:
        game.close()


def test_explicit_two_reply_recipients_apply_once_each(tmp_path):
    game = contact_engine(tmp_path / "two-routes.sqlite",
                          reply_recipients={PLAYER_A["id"]: [PERSON_A, PLAYER_A]})
    try:
        receive(game, propose(game))
        response = respond(game)
        assert [item["recipient"] for item in dispatches(game, response)] == [PERSON_A, PLAYER_A]
        receive(game, response)
        assert contact(game, PERSON_A)["status"] == contact(game, PLAYER_A)["status"] == "accepted"
        assert len(reports_for(game, PERSON_A["id"])) == 1
        assert len(game.true_state.npc_state["memories"][PERSON_A["id"]]) == 1
        before = committed(game)
        assert game.tick_diplomacy() == []
        assert respond(game) == response
        assert_clean(game, before)
    finally:
        game.close()


@pytest.mark.parametrize("content", [None, "hello", [], {}, {"message": ""},
    {"message": "   "}, {"message": 1}, {"message": "x" * 4097},
    {"message": "hello", "topic": ""}, {"message": "hello", "topic": " "},
    {"message": "hello", "topic": 1}, {"message": "hello", "topic": "x" * 129},
    {"message": "hello", "treaty": {}}, {"message": "hello", "kind": "alliance"},
    {"message": "hello", "secret": float("nan")}])
def test_content_validation_rejects_unsupported_or_unbounded_intent_atomically(engine, content):
    before = committed(engine)
    with pytest.raises(ValueError):
        propose(engine, content=content)
    assert_clean(engine, before)


def test_content_limits_and_default_topic_are_supported(engine):
    receipt = propose(engine, content={"message": "x" * 4096, "topic": "y" * 128})
    receive(engine, receipt)
    assert "x" * 4096 in encode(contact(engine, PERSON_B)["proposal"])
    second = propose(engine, request_id="defaults", proposal_id="defaults", content={"message": "hello"})
    receive(engine, second)
    assert '"topic": "contact"' in encode(contact(engine, PERSON_B, "defaults")["proposal"])


@pytest.mark.parametrize("reference", [None, "a:ruler", {}, [], {"kind": "country", "id": "a"},
    {"kind": "group", "id": "a:ruler"}, {"kind": "military", "id": "a:ruler"},
    {"kind": "player", "id": "unbound"}, {"kind": "player", "id": []},
    {"kind": "player", "id": "a:ruler", "country": "a"}, PLAYER_B, PERSON_A])
def test_principal_requires_typed_control_and_authority(engine, reference):
    before = committed(engine)
    with pytest.raises(ValueError):
        propose(engine, principal_ref=reference)
    assert_clean(engine, before)


@pytest.mark.parametrize("field, value", [
    ("proposer_country_id", "missing"), ("recipient_country_id", "missing"),
    ("recipient_country_id", "a"), ("proposer_country_id", []),
    ("sender_person_id", "missing"), ("recipient_person_id", "missing"),
    ("sender_person_id", "b:ruler"), ("recipient_person_id", "a:ruler"),
    ("sender_person_id", "a:observer"),
    ("recipient_person_id", {}), ("sender_person_id", None)])
def test_country_person_and_execution_roles_are_validated(engine, field, value):
    before = committed(engine)
    with pytest.raises(ValueError):
        propose(engine, **{field: value})
    assert_clean(engine, before)


@pytest.mark.parametrize("field", ["request_id", "proposal_id"])
@pytest.mark.parametrize("value", ["", " ", [], {}, 0, True])
def test_malformed_request_and_explicit_proposal_ids_are_atomic(engine, field, value):
    before = committed(engine)
    with pytest.raises(ValueError):
        propose(engine, **{field: value})
    assert_clean(engine, before)


@pytest.mark.parametrize("loss", ["inactive", "scope", "government_role"])
def test_submission_checks_sender_current_active_scope_and_government_role(engine, loss):
    person = "a:ruler"
    with engine._transaction():
        state = engine._world.npc_state
        if loss == "inactive":
            state["persons"][person]["active"] = False
        elif loss == "scope":
            state["persons"][person]["authority"].remove("diplomatic_engagement")
        else:
            state["governments"][person[0]]["ruler"] = person[0] + ":observer"
    before = committed(engine)
    with pytest.raises(ValueError):
        propose(engine)
    assert_clean(engine, before)


def test_inactive_recipient_is_rejected_but_active_nonexecutive_can_receive(engine):
    with engine._transaction():
        engine._world.npc_state["persons"]["b:ruler"]["active"] = False
    before = committed(engine)
    with pytest.raises(ValueError):
        propose(engine)
    assert_clean(engine, before)
    receipt = propose(engine, recipient_person_id="b:observer")
    receive(engine, receipt)
    assert contact(engine, {"kind": "person", "id": "b:observer"})["proposal_received_at"]
    before = committed(engine)
    with pytest.raises(ValueError):
        respond(engine, executing_person_id="b:observer")
    assert_clean(engine, before)


@pytest.mark.parametrize("loss", ["scope", "government_role"])
def test_recipient_execution_authority_is_checked_at_response_not_delivery(engine, loss):
    with engine._transaction():
        if loss == "scope":
            engine._world.npc_state["persons"]["b:ruler"]["authority"].remove("diplomatic_engagement")
        else:
            engine._world.npc_state["governments"]["b"]["ruler"] = "b:observer"
    receipt = propose(engine)
    receive(engine, receipt)
    assert contact(engine, PERSON_B)["proposal_received_at"]
    before = committed(engine)
    with pytest.raises(ValueError):
        respond(engine)
    assert_clean(engine, before)


@pytest.mark.parametrize("loss", ["inactive", "scope", "government_role"])
def test_response_rechecks_authority_after_proposal_was_received(engine, loss):
    receive(engine, propose(engine))
    with engine._transaction():
        state = engine._world.npc_state
        if loss == "inactive":
            state["persons"]["b:ruler"]["active"] = False
        elif loss == "scope":
            state["persons"]["b:ruler"]["authority"].remove("diplomatic_engagement")
        else:
            state["governments"]["b"]["ruler"] = "b:observer"
    before = committed(engine)
    with pytest.raises(ValueError):
        respond(engine)
    assert_clean(engine, before)
    assert contact(engine, PLAYER_A)["status"] == "pending"


@pytest.mark.parametrize("result", [None, True, [], {}, "accepted", "counteroffer", "withdraw", "Accept"])
def test_response_choice_is_narrow_and_atomic(engine, result):
    receive(engine, propose(engine))
    before = committed(engine)
    with pytest.raises(ValueError):
        respond(engine, result=result)
    assert_clean(engine, before)


@pytest.mark.parametrize("overrides", [
    {"principal_ref": PLAYER_A}, {"principal_ref": PERSON_B},
    {"executing_person_id": "a:ruler"}, {"executing_person_id": "b:observer"},
    {"executing_person_id": []}, {"proposal_id": "unknown"}, {"proposal_id": []},
    {"request_id": ""}, {"request_id": []}])
def test_response_requires_current_recipient_execution_and_binding(engine, overrides):
    receive(engine, propose(engine))
    before = committed(engine)
    with pytest.raises(ValueError):
        respond(engine, **overrides)
    assert_clean(engine, before)


def test_request_retries_and_conflicts_do_not_duplicate_effects(engine):
    submission = propose(engine)
    before = committed(engine)
    assert propose(engine) == submission
    assert_clean(engine, before)
    for changed in ({"content": {"message": "different"}}, {"proposal_id": "different"}):
        with pytest.raises(ValueError, match="conflict|different"):
            propose(engine, **changed)
        assert_clean(engine, before)
    with pytest.raises(ValueError):
        propose(engine, request_id="different-request")
    assert_clean(engine, before)
    receive(engine, submission)
    response = respond(engine)
    before = committed(engine)
    assert respond(engine) == response
    assert propose(engine) == submission
    assert_clean(engine, before)
    with pytest.raises(ValueError, match="conflict|different"):
        respond(engine, result="reject")
    assert_clean(engine, before)
    with pytest.raises(ValueError):
        respond(engine, request_id="second-execution")
    assert_clean(engine, before)


def test_same_request_text_is_scoped_to_explicit_principal(engine):
    first = propose(engine, request_id="shared-request")
    second = propose(engine, request_id="shared-request", proposal_id="reverse-contact",
        proposer_country_id="b", recipient_country_id="a", sender_person_id="b:ruler",
        recipient_person_id="a:ruler", principal_ref=PLAYER_B)
    assert first["id"] != second["id"]
    assert first["proposal_id"] != second["proposal_id"]
    assert len(engine.true_state.diplomacy_state["proposals"]) == 2


def test_generated_proposal_ids_do_not_overwrite_explicit_ids(engine):
    explicit = propose(engine, proposal_id="diplomacy-proposal-2")
    generated = propose(engine, request_id="generated", proposal_id=None)
    another = propose(engine, request_id="another", proposal_id=None)
    assert len({explicit["proposal_id"], generated["proposal_id"], another["proposal_id"]}) == 3
    assert len(engine.true_state.diplomacy_state["proposals"]) == 3


@pytest.mark.parametrize("identity", ["diplomacy-receipt-2", "diplomacy-dispatch-1", "diplomacy-receipt-20"])
def test_explicit_proposal_ids_reserve_all_domain_identity_namespaces(engine, identity):
    submission = propose(engine, proposal_id=identity)
    assert identity != submission["id"]
    assert identity not in submission["dispatch_ids"]
    # Reach the future receipt number as well as the immediately reserved IDs.
    for number in range(21):
        propose(engine, request_id="later-" + str(number), proposal_id=None)
    state = engine.true_state.diplomacy_state
    identities = [set(state[bucket]) for bucket in ("proposals", "dispatches", "receipts")]
    assert identities[0].isdisjoint(identities[1])
    assert identities[0].isdisjoint(identities[2])
    assert identities[1].isdisjoint(identities[2])
    before = committed(engine)
    for allocated in (submission["id"], submission["dispatch_ids"][0]):
        with pytest.raises(ValueError):
            propose(engine, request_id="collision-" + allocated, proposal_id=allocated)
        assert_clean(engine, before)


def test_due_dispatches_follow_date_sequence_then_id_and_queries_draw_no_rng(engine):
    first = propose(engine, request_id="one", proposal_id="z-contact")
    second = propose(engine, request_id="two", proposal_id="a-contact")
    first_dispatch, second_dispatch = dispatches(engine, first)[0], dispatches(engine, second)[0]
    assert first_dispatch["sequence"] < second_dispatch["sequence"]
    assert first_dispatch["delivery_date"] == second_dispatch["delivery_date"]
    day(engine, "1897-01-02")
    before = committed(engine)
    assert engine.tick_diplomacy() == []
    engine.diplomacy_view(PLAYER_A)
    engine.diplomacy_view(PERSON_B)
    assert_clean(engine, before)
    rng = engine.rng.getstate()
    day(engine, "1897-01-03")
    engine.tick_diplomacy()
    assert [item["proposal_id"] for item in engine.diplomacy_view(PERSON_B)["history"]] == ["z-contact", "a-contact"]
    assert engine.rng.getstate() == rng
    assert len(engine.true_state.npc_state["memories"]["b:ruler"]) == 2
    assert len(reports_for(engine, "b:ruler", "z-contact")) == 1
    assert len(reports_for(engine, "b:ruler", "a-contact")) == 1


def test_person_reports_use_contact_identity_without_overwriting_legacy_topic(engine):
    engine.npc_report("b:ruler", "diplomacy", {"country": "a", "trust": .3},
                      as_of=engine.true_state.date)
    original = copy.deepcopy(engine.true_state.npc_state["reports"]["b:ruler"]["diplomacy"])
    receive(engine, propose(engine))
    receive(engine, propose(engine, request_id="two", proposal_id="contact-two"))
    state = engine.true_state.npc_state
    assert state["reports"]["b:ruler"]["diplomacy"] == original
    assert len(reports_for(engine, "b:ruler")) == 1
    assert len(reports_for(engine, "b:ruler", "contact-two")) == 1


def test_public_view_is_detached_and_never_discloses_queued_reply_or_foreign_contacts(engine):
    receipt = propose(engine)
    receive(engine, receipt)
    response = respond(engine)
    secret = propose(engine, request_id="foreign", proposal_id="foreign-secret",
        proposer_country_id="b", recipient_country_id="a", sender_person_id="b:ruler",
        recipient_person_id="a:ruler", content={"message": "Unreceived private foreign contact"},
        principal_ref=PLAYER_B)
    before = committed(engine)
    view = engine.diplomacy_view(PLAYER_A)
    assert set(view) == {"subject", "contacts", "history", "receipts"}
    serialized = encode(view)
    assert "foreign-secret" not in serialized
    assert "Unreceived private foreign contact" not in serialized
    assert '"result": "accepted"' not in serialized
    assert response["id"] not in serialized
    assert dispatches(engine, secret)[0]["id"] not in serialized
    assert "archive" not in serialized and "ledger" not in serialized
    view["contacts"][0]["proposal"]["content"]["message"] = "caller changed"
    view["contacts"].clear()
    view["receipts"].clear()
    assert_clean(engine, before)


@pytest.mark.parametrize("reference", ["a:ruler", [], {}, None,
    {"kind": "country", "id": "a"}, {"kind": "group", "id": "a:observer"},
    {"kind": "player", "id": "missing"}, {"kind": "person", "id": []},
    {"kind": "person", "id": "missing"}, {"kind": "player", "id": "a:ruler", "extra": 1}])
def test_public_query_requires_explicit_existing_typed_subject(engine, reference):
    before = committed(engine)
    with pytest.raises(ValueError):
        engine.diplomacy_view(reference)
    assert_clean(engine, before)


def test_feature_off_contact_apis_reject_without_activating(tmp_path):
    game = fullstack(tmp_path / "legacy.sqlite")
    try:
        before = committed(game)
        for operation in (lambda: propose(game), lambda: respond(game),
                          lambda: game.tick_diplomacy(), lambda: game.diplomacy_view(PLAYER_A)):
            with pytest.raises(ValueError):
                operation()
            assert_clean(game, before)
        assert game.true_state.diplomacy_state is None
    finally:
        game.close()


def test_queries_read_committed_submission_during_candidate_mutation(engine, monkeypatch):
    from worldline import diplomacy
    before = committed(engine)
    original = diplomacy.propose
    def fail_after_proposal(*args, **kwargs):
        result = original(*args, **kwargs)
        assert engine._world.diplomacy_state["proposals"]
        assert engine.diplomacy_view(PLAYER_A)["contacts"] == []
        assert engine.diplomacy_view(PERSON_A)["contacts"] == []
        raise RuntimeError("candidate submission")
    monkeypatch.setattr(diplomacy, "propose", fail_after_proposal)
    with pytest.raises(RuntimeError, match="candidate submission"):
        propose(engine)
    assert_clean(engine, before)


def test_query_observes_external_committed_reply_and_stale_engine_cannot_write(engine):
    submission = propose(engine)
    receive(engine, submission)
    response = respond(engine)
    assert contact(engine, PLAYER_A)["status"] == "pending"
    other = Engine(engine.path)
    try:
        receive(other, response)
        assert contact(engine, PLAYER_A)["status"] == "accepted"
        with pytest.raises(ValueError, match="stale|changed|writer"):
            propose(engine, request_id="stale", proposal_id="stale")
        assert engine._stored_payload() == other._stored_payload()
    finally:
        other.close()


def test_contact_mutations_and_explicit_ticks_preserve_objective_domains_and_rng(engine):
    initial = engine.true_state.to_dict()
    rng = engine.rng.getstate()
    receive(engine, propose(engine))
    receive(engine, respond(engine))
    after = engine.true_state.to_dict()
    for key in ("date", "diplomacy_state", "npc_state"):
        after[key] = initial[key]
    assert after == initial
    assert engine.rng.getstate() == rng
    assert engine.true_state.intelligence_state == initial["intelligence_state"]


def test_scheduler_delivers_proposal_and_reply_with_positive_delays(engine):
    submission = propose(engine)
    engine.advance(1)
    assert engine.diplomacy_view(PERSON_B)["contacts"] == []
    engine.advance(1)
    assert contact(engine, PERSON_B)["proposal_received_at"] == "1897-01-03"
    response = respond(engine)
    engine.advance(2)
    assert contact(engine, PLAYER_A)["status"] == "pending"
    engine.advance(1)
    assert contact(engine, PLAYER_A)["status"] == "accepted"
    assert all(item["status"] == "delivered" for item in dispatches(engine, submission) + dispatches(engine, response))
