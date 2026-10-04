"""General intelligence is frozen, delivered explicitly, and applied once."""
import copy
import json
import sqlite3
from datetime import date, timedelta

import pytest

from worldline import Engine, intelligence, npc_state, state_storage
from worldline.engine import encode
from test_diplomacy_intelligence_foundation import assert_clean, committed, fullstack


SOURCE = {"domain": "trusted-test", "id": "observer"}
PERSON = {"kind": "person", "id": "a:ruler"}
OTHER = {"kind": "person", "id": "b:ruler"}
PLAYER = {"kind": "player", "id": "a:ruler"}
PROVENANCE = {"label": "official bulletin", "confidence": .7, "reliability": .6}


@pytest.fixture
def engine(tmp_path):
    game = fullstack(tmp_path / "intelligence.sqlite")
    game.enable_diplomacy_intelligence({"diplomacy": {"player_bindings": {
        "a:ruler": {"country_id": "a", "representative_person_id": "a:ruler"}}}})
    yield game
    game.close()


def ingest(game, *, observation_id="observation-one", as_of=None, data=None,
           subject="b", topic="readiness", **options):
    return game.ingest_observation(SOURCE, data if data is not None else {"strength": 150},
        subject=subject, topic=topic, as_of=as_of or game.true_state.date,
        observation_id=observation_id, **options)


def queue(game, observation, recipient=PERSON, *, message_id="message-one", **options):
    return game.queue_intelligence_report(observation["id"], recipient,
                                          message_id=message_id, **options)


def day(game, value):
    """Advance only the simulation clock to test an explicit deterministic tick."""
    with game._transaction():
        game._world.date = value


def deliver(game, dispatch):
    day(game, dispatch["delivery_date"])
    return game.tick_intelligence()


def histories(game, recipient=PERSON):
    return game.intelligence_view(recipient)["history"]


def npc_intelligence(game, person="a:ruler"):
    return [report for report in game.true_state.npc_state["reports"][person].values()
            if "intelligence" in report["data"]]


@pytest.mark.parametrize("data, expected_signal", [
    ({"performance": 10 ** 400}, 0.0),
    ({"performance": -(10 ** 400)}, 0.0),
    ({"gdp_before": 0, "gdp_after": 10 ** 400}, 0.0),
    ({"gdp_before": 0, "gdp_after": 10 ** 308}, 1.0),
])
def test_large_json_integers_remain_frozen_and_deliver_to_existing_perception(engine, data, expected_signal):
    observation = ingest(engine, data=data, subject=OTHER["id"])
    dispatch = queue(engine, observation)
    deliver(engine, dispatch)
    report = npc_intelligence(engine)[0]
    assert report["data"]["facts"] == data
    assert report["data"]["intelligence"]["observed_data"] == data
    state = engine.true_state.npc_state
    memory = state["memories"][PERSON["id"]][-1]
    assert memory["observed_facts"] == data
    assert memory["valence"] == expected_signal
    assert histories(engine)[0]["belief_accepted"]
    assert histories(engine)[0]["payload"]["observed_data"] == data
    assert npc_state.evaluate_ruler(state, PERSON["id"], OTHER["id"], engine.true_state.date)
    saved = engine.save(engine.path.parent / "large-integer.json")
    restored = Engine.load(saved, engine.path.parent / "large-integer-restored.sqlite")
    try:
        assert restored.intelligence_view(PERSON) == engine.intelligence_view(PERSON)
        assert restored._payload() == engine._payload()
        assert restored.ledger() == engine.ledger()
    finally:
        restored.close()


def test_trusted_ingestion_freezes_without_truth_or_belief_changes(engine):
    before = engine.true_state.to_dict()
    rng, observed = engine.rng.getstate(), engine.observed
    data = {"strength": 150, "details": {"readiness": [1, 2]}}
    source = copy.deepcopy(SOURCE)
    provenance = copy.deepcopy(PROVENANCE)
    observation = engine.ingest_observation(source, data, subject="b", topic="readiness",
        as_of=before["date"], collector_ref={"domain": "runtime", "id": "collector"},
        provenance=provenance, audit={"accuracy": .1, "notes": "private quality"},
        observation_id="frozen")
    assert observation["id"] == "frozen"
    assert observation["observed_data"] == data
    assert observation["as_of"] == observation["collected_at"] == before["date"]
    data["details"]["readiness"].append(999)
    source["id"] = "caller changed"
    provenance["label"] = "caller changed"
    observation["observed_data"]["strength"] = 999
    stored = engine.true_state.intelligence_state["observations"]["frozen"]
    assert stored["observed_data"] == {"strength": 150, "details": {"readiness": [1, 2]}}
    assert stored["source_ref"] == SOURCE
    assert stored["provenance"] == PROVENANCE
    after = engine.true_state.to_dict()
    after["intelligence_state"] = before["intelligence_state"]
    assert after == before
    assert engine.observed == observed
    assert engine.rng.getstate() == rng
    assert histories(engine) == []


@pytest.mark.parametrize("data", [float("nan"), {"bad": float("inf")}, {1: "key"},
    {"bad": object()}, {"bad": {1, 2}}, {"bad": (1, 2)}, [1, 2]])
def test_ingestion_rejects_non_json_or_non_object_data_atomically(engine, data):
    before = committed(engine)
    with pytest.raises(ValueError):
        ingest(engine, data=data)
    assert_clean(engine, before)


@pytest.mark.parametrize("options", [
    {"source_ref": {"id": "observer"}},
    {"source_ref": {"domain": "trusted-test", "id": "observer", "truth": 1}},
    {"source_ref": {"domain": "", "id": "observer"}},
    {"collector_ref": {"kind": "person", "id": "a:ruler"}},
    {"subject": ""}, {"topic": ""}, {"as_of": "1897-1-1"},
    {"as_of": "1897-02-30"}, {"as_of": "1897-01-02"},
    {"collected_at": "1896-12-31"}, {"collected_at": "1897-01-02"},
    {"provenance": {"label": "source", "confidence": 2, "reliability": .8}},
    {"provenance": {"label": "source", "confidence": .8, "reliability": .8, "actual": 1}},
    {"audit": {"accuracy": -.1}}, {"audit": {"actual_source_truth": {"secret": 1}}},
    {"audit": {"notes": "x" * 3000}}, {"topic": "x" * 300},
    {"observation_id": ""}, {"observation_id": []}, {"observation_id": {}},
])
def test_ingestion_schema_dates_identity_and_metadata_validation(engine, options):
    values = {"source_ref": SOURCE, "observed_data": {"strength": 150},
              "subject": "b", "topic": "readiness", "as_of": "1897-01-01",
              "observation_id": "invalid"}
    values.update(options)
    before = committed(engine)
    with pytest.raises(ValueError):
        engine.ingest_observation(**values)
    assert_clean(engine, before)


def test_ingestion_retry_is_idempotent_and_conflicting_intent_is_rejected(engine):
    observation = ingest(engine)
    before = committed(engine)
    assert ingest(engine) == observation
    assert_clean(engine, before)
    with pytest.raises(ValueError, match="conflict|immutable|different"):
        ingest(engine, data={"strength": 999})
    assert_clean(engine, before)
    day(engine, "1897-01-02")
    before = committed(engine)
    assert ingest(engine, as_of=observation["as_of"]) == observation
    assert_clean(engine, before)


def test_historical_collection_dates_are_preserved_and_ordered(engine):
    observation = ingest(engine, as_of="1896-12-29", collected_at="1896-12-30")
    dispatch = queue(engine, observation)
    assert observation["as_of"] == "1896-12-29"
    assert observation["collected_at"] == "1896-12-30"
    assert dispatch["sent_at"] == "1897-01-01"
    assert dispatch["payload"]["as_of"] == observation["as_of"]
    assert dispatch["payload"]["collected_at"] == observation["collected_at"]


def test_automatic_ids_cannot_overwrite_explicit_generated_namespace_ids(engine):
    explicit = ingest(engine, observation_id="intelligence-observation-2", data={"strength": 150})
    generated = engine.ingest_observation(SOURCE, {"strength": 200}, subject="b", topic="readiness",
                                          as_of=engine.true_state.date)
    assert generated["id"] != explicit["id"]
    assert engine.true_state.intelligence_state["observations"][explicit["id"]] == explicit
    first = queue(engine, explicit, message_id="intelligence-dispatch-2")
    second = engine.queue_intelligence_report(generated["id"], PERSON)
    assert second["id"] != first["id"]
    assert engine.true_state.intelligence_state["dispatches"][first["id"]] == first
    assert len(engine.true_state.intelligence_state["observations"]) == 2
    assert len(engine.true_state.intelligence_state["dispatches"]) == 2


@pytest.mark.parametrize("message_id", ["", [], {}])
def test_invalid_dispatch_identity_is_rejected_atomically(engine, message_id):
    observation = ingest(engine)
    before = committed(engine)
    with pytest.raises(ValueError):
        queue(engine, observation, message_id=message_id)
    assert_clean(engine, before)


def test_payload_size_and_depth_are_bounded(engine):
    before = committed(engine)
    deep = {"value": 1}
    for _ in range(20):
        deep = {"nested": deep}
    for data in ({"large": "x" * 70000}, deep):
        with pytest.raises(ValueError):
            ingest(engine, data=data)
        assert_clean(engine, before)


def test_configured_source_and_audience_are_explicit_allowlists(tmp_path):
    game = fullstack(tmp_path / "allowlisted.sqlite")
    try:
        game.enable_diplomacy_intelligence({"intelligence": {
            "sources": {"registered-observer": SOURCE}, "audience": [PERSON], "delay_days": 3}})
        before = committed(game)
        with pytest.raises(ValueError):
            game.ingest_observation({"domain": "trusted-test", "id": "other-source"}, {"strength": 150},
                subject="b", topic="readiness", as_of=game.true_state.date)
        assert_clean(game, before)
        observation = ingest(game)
        before = committed(game)
        with pytest.raises(ValueError):
            queue(game, observation, OTHER)
        assert_clean(game, before)
        message = queue(game, observation)
        assert message["delivery_date"] == "1897-01-04"
    finally:
        game.close()


def test_zero_delay_still_requires_explicit_delivery_tick(tmp_path):
    game = fullstack(tmp_path / "zero-delay.sqlite")
    try:
        game.enable_diplomacy_intelligence({"intelligence": {"delay_days": 0}})
        message = queue(game, ingest(game))
        assert message["sent_at"] == message["delivery_date"] == game.true_state.date
        assert histories(game) == []
        game.tick_intelligence()
        assert len(histories(game)) == 1
    finally:
        game.close()


def test_queued_recipient_cannot_see_payload_or_receive_belief(engine):
    observation = ingest(engine)
    before_npc, before_player = engine.true_state.npc_state, engine.observed
    dispatch = queue(engine, observation)
    assert dispatch["observation_id"] == observation["id"]
    assert dispatch["recipient"] == PERSON
    assert dispatch["status"] == "queued"
    assert dispatch["sent_at"] == observation["collected_at"]
    assert date.fromisoformat(dispatch["delivery_date"]) == date.fromisoformat(dispatch["sent_at"]) + timedelta(days=1)
    assert engine.true_state.npc_state == before_npc
    assert engine.observed == before_player
    assert histories(engine) == histories(engine, OTHER) == histories(engine, PLAYER) == []
    before = committed(engine)
    assert engine.tick_intelligence() == []
    assert_clean(engine, before)


@pytest.mark.parametrize("recipient", ["a:ruler", {"kind": "country", "id": "a"},
    {"kind": "group", "id": "a"}, {"kind": "military", "id": "army-a"},
    {"kind": "person", "person_id": "a:ruler"}, {"kind": "person", "id": "missing"},
    {"kind": "player", "id": "unbound"}, {"kind": "person", "id": "a:ruler", "country": "a"}])
def test_dispatch_requires_exact_explicit_person_or_bound_player(engine, recipient):
    observation = ingest(engine)
    before = committed(engine)
    with pytest.raises(ValueError):
        queue(engine, observation, recipient)
    assert_clean(engine, before)


@pytest.mark.parametrize("sent_at", ["1896-12-31", "1897-01-02", "1897-1-1", "not a date"])
def test_dispatch_sent_at_uses_current_simulation_date(engine, sent_at):
    observation = ingest(engine)
    before = committed(engine)
    with pytest.raises(ValueError):
        queue(engine, observation, sent_at=sent_at)
    assert_clean(engine, before)


def test_unknown_observation_dispatch_is_atomic(engine):
    before = committed(engine)
    with pytest.raises(ValueError):
        engine.queue_intelligence_report("missing", PERSON, message_id="failed")
    assert_clean(engine, before)


def test_person_delivery_reuses_reports_and_memory_with_receipts(engine):
    observation = ingest(engine, provenance=PROVENANCE, audit={"accuracy": .2, "notes": "secret"})
    dispatch = queue(engine, observation)
    rng = engine.rng.getstate()
    delivered = deliver(engine, dispatch)
    assert delivered
    state = engine.true_state.intelligence_state
    received = state["dispatches"][dispatch["id"]]
    assert received["status"] == "delivered"
    assert received["received_at"] == dispatch["delivery_date"]
    assert received["delivery_receipt_id"] in state["receipts"]
    assert received["application_receipt_id"] in state["receipts"]
    reports = npc_intelligence(engine)
    assert len(reports) == 1
    assert reports[0]["data"]["intelligence"]["observation_id"] == observation["id"]
    memories = engine.true_state.npc_state["memories"]["a:ruler"]
    assert len(memories) == 1
    assert memories[0]["observed_facts"] == observation["observed_data"]
    assert engine.observed == {}
    assert histories(engine, OTHER) == histories(engine, PLAYER) == []
    assert "secret" not in encode(engine.intelligence_view(PERSON))
    assert "accuracy" not in encode(engine.intelligence_view(PERSON))
    assert engine.rng.getstate() == rng


def test_player_delivery_reuses_observed_store_in_separate_namespace(engine):
    observation = ingest(engine, provenance=PROVENANCE)
    dispatch = queue(engine, observation, PLAYER)
    npc_before = engine.true_state.npc_state
    rng = engine.rng.getstate()
    deliver(engine, dispatch)
    reports = list(engine.observed_state(PLAYER["id"]).values())
    assert len(reports) == 1
    assert reports[0]["as_of"] == observation["as_of"]
    assert reports[0]["data"]["observation_id"] == observation["id"]
    assert reports[0]["data"]["source_ref"] == SOURCE
    assert reports[0]["data"]["provenance"] == PROVENANCE
    assert engine.true_state.npc_state == npc_before
    assert len(histories(engine, PLAYER)) == 1
    assert histories(engine, PERSON) == histories(engine, OTHER) == []
    assert engine.rng.getstate() == rng


def test_same_identifier_person_and_player_have_independent_application_receipts(engine):
    observation = ingest(engine)
    person_message = queue(engine, observation, PERSON, message_id="person-message")
    player_message = queue(engine, observation, PLAYER, message_id="player-message")
    deliver(engine, person_message)
    state = engine.true_state.intelligence_state
    assert state["dispatches"][person_message["id"]]["application_receipt_id"] != state["dispatches"][player_message["id"]]["application_receipt_id"]
    assert [item["id"] for item in histories(engine, PERSON)] == [person_message["id"]]
    assert [item["id"] for item in histories(engine, PLAYER)] == [player_message["id"]]
    assert len(npc_intelligence(engine)) == 1
    assert len(engine.true_state.npc_state["memories"][PERSON["id"]]) == 1
    assert len(engine.observed_state(PLAYER["id"])) == 1


def test_scheduler_delivers_intelligence_on_simulation_day(engine):
    observation = ingest(engine)
    dispatch = queue(engine, observation)
    engine.advance(1)
    assert engine.true_state.date == dispatch["delivery_date"]
    assert len(histories(engine)) == 1
    assert len(npc_intelligence(engine)) == 1


def test_same_day_delivery_and_latest_tie_follow_domain_sequence(engine):
    observations = [ingest(engine, observation_id="observation-" + str(i), data={"strength": i})
                    for i in range(3)]
    messages = [queue(engine, observation, message_id=name)
                for observation, name in zip(observations, ["z-message", "a-message", "m-message"])]
    assert [item["sequence"] for item in messages] == sorted(item["sequence"] for item in messages)
    events = deliver(engine, messages[0])
    assert [event["dispatch"]["id"] for event in events] == [item["id"] for item in messages]
    assert [item["id"] for item in histories(engine)] == [item["id"] for item in messages]
    assert engine.intelligence_view(PERSON)["latest"][0]["observation_id"] == observations[-1]["id"]
    assert npc_intelligence(engine)[0]["data"]["intelligence"]["observed_data"] == {"strength": 2}
    assert len(engine.true_state.npc_state["memories"]["a:ruler"]) == 1


def test_delivery_date_precedes_sequence_for_due_processing(engine):
    old = queue(engine, ingest(engine, observation_id="old"), message_id="z-old")
    day(engine, old["delivery_date"])
    later = queue(engine, ingest(engine, observation_id="new"), message_id="a-new")
    day(engine, later["delivery_date"])
    events = engine.tick_intelligence()
    assert [event["dispatch"]["id"] for event in events] == [old["id"], later["id"]]
    assert [item["id"] for item in histories(engine)] == [old["id"], later["id"]]


def test_same_as_of_correction_uses_later_sent_at_for_latest(engine):
    old = ingest(engine)
    first = queue(engine, old)
    deliver(engine, first)
    correction = ingest(engine, observation_id="correction", as_of=old["as_of"], data={"strength": 120})
    second = queue(engine, correction, message_id="correction-message")
    assert second["sent_at"] > first["sent_at"]
    deliver(engine, second)
    assert engine.intelligence_view(PERSON)["latest"][0]["observation_id"] == correction["id"]
    assert len(engine.true_state.npc_state["memories"]["a:ruler"]) == 1


def test_recipient_subject_and_topic_queries_do_not_union_unrelated_reports(engine):
    readiness = queue(engine, ingest(engine), message_id="readiness")
    trade = queue(engine, ingest(engine, observation_id="trade", subject="a", topic="trade"), message_id="trade")
    foreign = queue(engine, ingest(engine, observation_id="foreign", subject="a", topic="secrets"), OTHER,
                    message_id="foreign")
    deliver(engine, readiness)
    view = engine.intelligence_view(PERSON)
    assert len(view["latest"]) == 2
    assert {entry["id"] for entry in engine.intelligence_view(PERSON, "b")["latest"]} == {readiness["id"]}
    assert {entry["id"] for entry in engine.intelligence_view(PERSON, "trade")["latest"]} == {trade["id"]}
    assert engine.intelligence_view(PERSON, "secrets")["history"] == []
    assert [entry["id"] for entry in histories(engine, OTHER)] == [foreign["id"]]


def test_same_country_members_do_not_implicitly_share_received_information(engine):
    with engine._transaction():
        npc_state.add_person(engine._world.npc_state, "a:minister", "a", "Minister")
    colleague = {"kind": "person", "id": "a:minister"}
    message = queue(engine, ingest(engine))
    deliver(engine, message)
    assert len(histories(engine)) == 1
    assert histories(engine, colleague) == []
    assert engine.true_state.npc_state["reports"][colleague["id"]] == {}


@pytest.mark.parametrize("recipient", [PERSON, PLAYER])
def test_stale_late_report_is_received_without_replacing_latest(engine, recipient):
    latest = ingest(engine, observation_id="newest", data={"strength": 200})
    first = queue(engine, latest, recipient, message_id="first")
    deliver(engine, first)
    old = ingest(engine, observation_id="late-old", as_of="1896-12-31", data={"strength": 10})
    second = queue(engine, old, recipient, message_id="second")
    before_memory = copy.deepcopy(engine.true_state.npc_state["memories"])
    before_reports = npc_intelligence(engine) if recipient == PERSON else engine.observed_state(PLAYER["id"])
    deliver(engine, second)
    view = engine.intelligence_view(recipient)
    assert [entry["observation_id"] for entry in view["history"]] == [latest["id"], old["id"]]
    assert view["latest"][0]["observation_id"] == latest["id"]
    assert engine.true_state.npc_state["memories"] == before_memory
    after_reports = npc_intelligence(engine) if recipient == PERSON else engine.observed_state(PLAYER["id"])
    assert after_reports == before_reports


@pytest.mark.parametrize("recipient", [PERSON, PLAYER])
def test_correction_is_new_frozen_snapshot_and_memory_not_duplicated(engine, recipient):
    original = ingest(engine, data={"strength": 150})
    first = queue(engine, original, recipient)
    deliver(engine, first)
    correction = ingest(engine, observation_id="correction", data={"strength": 120})
    second = queue(engine, correction, recipient, message_id="correction-message")
    deliver(engine, second)
    state = engine.true_state.intelligence_state
    assert state["observations"][original["id"]] == original
    assert state["observations"][correction["id"]] == correction
    view = engine.intelligence_view(recipient, "b")
    assert len(view["history"]) == 2
    assert view["latest"][0]["observation_id"] == correction["id"]
    if recipient == PERSON:
        assert len(engine.true_state.npc_state["memories"]["a:ruler"]) == 1
        assert npc_intelligence(engine)[0]["data"]["intelligence"]["observed_data"] == {"strength": 120}
    else:
        assert list(engine.observed_state(PLAYER["id"]).values())[0]["data"]["observed_data"] == {"strength": 120}


@pytest.mark.parametrize("recipient", [PERSON, PLAYER])
def test_duplicate_dispatch_and_redelivery_apply_perception_once(engine, recipient):
    observation = ingest(engine)
    first = queue(engine, observation, recipient)
    before = committed(engine)
    assert queue(engine, observation, recipient) == first
    assert_clean(engine, before)
    with pytest.raises(ValueError, match="conflict|different"):
        queue(engine, observation, OTHER)
    assert_clean(engine, before)
    deliver(engine, first)
    before = committed(engine)
    assert queue(engine, observation, recipient) == first
    assert_clean(engine, before)
    second = queue(engine, observation, recipient, message_id="intentional-redelivery")
    before_perception = engine.true_state.npc_state, engine.observed
    deliver(engine, second)
    assert len(histories(engine, recipient)) == 2
    assert (engine.true_state.npc_state, engine.observed) == before_perception
    before = committed(engine)
    assert engine.tick_intelligence() == []
    assert_clean(engine, before)


def test_public_projection_is_detached_scoped_and_uses_received_evidence(engine):
    received = ingest(engine, audit={"accuracy": .3, "notes": "hidden-quality"})
    message = queue(engine, received)
    deliver(engine, message)
    unreceived = ingest(engine, observation_id="unreceived-secret", topic="secrets", data={"secret": "undelivered"})
    queue(engine, unreceived, OTHER, message_id="other-secret")
    before = committed(engine)
    view = engine.intelligence_view(PERSON)
    serialized = encode(view)
    assert "hidden-quality" not in serialized
    assert "accuracy" not in serialized
    assert "unreceived-secret" not in serialized
    assert "undelivered" not in serialized
    assert set(view) == {"recipient", "history", "latest"}
    assert len(view["history"]) == len(view["latest"]) == 1
    view["history"][0]["payload"]["observed_data"]["strength"] = 999
    view["latest"].clear()
    assert_clean(engine, before)
    assert engine.intelligence_view(PERSON, "readiness")["history"]
    assert engine.intelligence_view(PERSON, "absent")["history"] == []


@pytest.mark.parametrize("recipient", [PERSON, PLAYER])
def test_public_query_cannot_see_candidate_delivery_inside_transaction(engine, recipient):
    observation = ingest(engine)
    message = queue(engine, observation, recipient)
    day(engine, message["delivery_date"])
    before = committed(engine)
    with pytest.raises(RuntimeError, match="candidate rollback"):
        with engine._transaction():
            events = intelligence.deliver_due(engine._world)
            assert events
            for event in events:
                engine._apply_intelligence_event(event)
                engine._event("system_event", event)
            assert engine._world.intelligence_state["dispatches"][message["id"]]["status"] == "delivered"
            assert histories(engine, recipient) == []
            assert engine.observed == json.loads(before["stored"])["observed"]
            raise RuntimeError("candidate rollback")
    assert_clean(engine, before)


@pytest.mark.parametrize("delivered", [False, True])
@pytest.mark.parametrize("recipient", [PERSON, PLAYER])
def test_save_load_and_continue_preserve_real_records_exactly(engine, tmp_path, delivered, recipient):
    message = queue(engine, ingest(engine), recipient)
    if delivered:
        deliver(engine, message)
    snapshot = engine.save(tmp_path / "stage2.json")
    raw = json.loads(snapshot.read_text(encoding="utf-8"))["payload"]
    assert raw["schema_version"] == 7
    assert raw["engine"]["world"]["schema_version"] == 6
    loaded = Engine.load(snapshot, tmp_path / "continued.sqlite")
    try:
        assert loaded._stored_payload() == engine._stored_payload()
        assert loaded.ledger() == engine.ledger()
        assert loaded.intelligence_view(recipient) == engine.intelligence_view(recipient)
        for game in (engine, loaded):
            game.advance(2)
        assert loaded._stored_payload() == engine._stored_payload()
        assert loaded.ledger() == engine.ledger()
        assert state_storage.export_archive(loaded.db) == state_storage.export_archive(engine.db)
    finally:
        loaded.close()


@pytest.mark.parametrize("recipient", [PERSON, PLAYER])
def test_save_load_continue_preserves_shared_application_receipt_for_redelivery(engine, tmp_path, recipient):
    observation = ingest(engine)
    first = queue(engine, observation, recipient)
    deliver(engine, first)
    second = queue(engine, observation, recipient, message_id="second-delivery")
    deliver(engine, second)
    state = engine.true_state.intelligence_state
    assert state["dispatches"][first["id"]]["application_receipt_id"] == state["dispatches"][second["id"]]["application_receipt_id"]
    assert state["dispatches"][first["id"]]["delivery_receipt_id"] != state["dispatches"][second["id"]]["delivery_receipt_id"]
    third = queue(engine, observation, recipient, message_id="third-in-transit")
    before_perception = engine.true_state.npc_state, engine.observed
    save = engine.save(tmp_path / "redelivery.json")
    loaded = Engine.load(save, tmp_path / "redelivery.sqlite")
    try:
        for game in (engine, loaded):
            deliver(game, third)
        assert loaded._stored_payload() == engine._stored_payload()
        assert loaded.ledger() == engine.ledger()
        assert state_storage.export_archive(loaded.db) == state_storage.export_archive(engine.db)
        assert (engine.true_state.npc_state, engine.observed) == before_perception
        assert len(histories(engine, recipient)) == 3
    finally:
        loaded.close()


def test_created_observation_has_immutable_archive_and_ledger_evidence(engine):
    observation = ingest(engine)
    message = queue(engine, observation)
    assert engine.historical_state(kind="intelligence_observations", item=observation["id"])[0]["value"] == observation
    deliver(engine, message)
    state = engine.true_state.intelligence_state
    assert len(engine.historical_state(kind="intelligence_dispatches", item=message["id"])) >= 2
    for receipt_id in (state["dispatches"][message["id"]]["delivery_receipt_id"],
                       state["dispatches"][message["id"]]["application_receipt_id"]):
        assert engine.historical_state(kind="intelligence_receipts", item=receipt_id)
    serialized = encode(engine.ledger())
    assert observation["id"] in serialized and message["id"] in serialized


def test_rollback_after_npc_report_application_restores_every_layer(engine, monkeypatch):
    dispatch = queue(engine, ingest(engine))
    day(engine, dispatch["delivery_date"])
    before = committed(engine)
    original = npc_state.receive_report
    def fail_after_report(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after NPC perception")
    monkeypatch.setattr(npc_state, "receive_report", fail_after_report)
    with pytest.raises(RuntimeError, match="after NPC perception"):
        engine.tick_intelligence()
    assert_clean(engine, before)


def test_rollback_after_player_perception_application_restores_every_layer(engine, monkeypatch):
    dispatch = queue(engine, ingest(engine), PLAYER)
    day(engine, dispatch["delivery_date"])
    before = committed(engine)
    original = engine._apply_observation
    def fail_after_report(*args, **kwargs):
        original(*args, **kwargs)
        assert engine._observed[PLAYER["id"]]
        raise RuntimeError("after player perception")
    monkeypatch.setattr(engine, "_apply_observation", fail_after_report)
    with pytest.raises(RuntimeError, match="after player perception"):
        engine.tick_intelligence()
    assert_clean(engine, before)


def test_delivery_recipient_validation_failure_restores_every_layer(engine, monkeypatch):
    dispatch = queue(engine, ingest(engine))
    day(engine, dispatch["delivery_date"])
    before = committed(engine)
    with monkeypatch.context() as context:
        def unavailable_recipient(*args, **kwargs):
            raise ValueError("recipient no longer valid")
        context.setattr(intelligence, "_recipient", unavailable_recipient)
        with pytest.raises(ValueError, match="recipient no longer valid"):
            engine.tick_intelligence()
    assert_clean(engine, before)


@pytest.mark.parametrize("operation", ["ingest", "dispatch", "person_delivery", "player_delivery"])
@pytest.mark.parametrize("table, action", [("ledger", "INSERT"), ("state_archive", "INSERT"),
    ("state_components", "UPDATE"), ("current_state", "UPDATE")])
def test_sql_failure_rolls_back_observation_dispatch_receipt_perception_and_archive(engine, operation, table, action):
    observation = ingest(engine) if operation != "ingest" else None
    recipient = PLAYER if operation == "player_delivery" else PERSON
    message = queue(engine, observation, recipient) if "delivery" in operation else None
    if message:
        day(engine, message["delivery_date"])
    before = committed(engine)
    engine.db.execute(f"CREATE TRIGGER fail_stage2 BEFORE {action} ON {table} "
        "BEGIN SELECT RAISE(ABORT,'intelligence write failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match="intelligence write failure"):
        if operation == "ingest":
            ingest(engine)
        elif operation == "dispatch":
            queue(engine, observation)
        else:
            engine.tick_intelligence()
    assert_clean(engine, before)
    engine.db.execute("DROP TRIGGER fail_stage2")
    if operation == "ingest":
        ingest(engine)
    elif operation == "dispatch":
        queue(engine, observation)
    else:
        engine.tick_intelligence()


def test_query_sees_external_committed_delivery_and_stale_engine_cannot_write(engine):
    observation = ingest(engine)
    message = queue(engine, observation)
    assert histories(engine) == []
    other = Engine(engine.path)
    try:
        deliver(other, message)
        assert len(histories(engine)) == 1
        before = committed(engine)
        with pytest.raises(ValueError, match="another writer"):
            ingest(engine, observation_id="stale-writer")
        assert_clean(engine, before)
        engine.refresh()
        assert engine.tick_intelligence() == []
    finally:
        other.close()


def test_ingest_queue_tick_and_queries_do_not_draw_rng(engine):
    rng = engine.rng.getstate()
    message = queue(engine, ingest(engine))
    assert engine.rng.getstate() == rng
    assert engine.tick_intelligence() == []
    deliver(engine, message)
    for recipient in (PERSON, OTHER, PLAYER):
        engine.intelligence_view(recipient)
    assert engine.rng.getstate() == rng
    before = committed(engine)
    assert engine.tick_intelligence() == []
    assert_clean(engine, before)


def test_feature_off_ingestion_dispatch_and_tick_are_rejected_without_activation(tmp_path):
    game = fullstack(tmp_path / "off.sqlite")
    try:
        before = committed(game)
        with pytest.raises(ValueError):
            ingest(game)
        with pytest.raises(ValueError):
            game.queue_intelligence_report("missing", PERSON)
        with pytest.raises(ValueError):
            game.tick_intelligence()
        with pytest.raises(ValueError):
            game.intelligence_view(PERSON)
        assert_clean(game, before)
        assert game.true_state.intelligence_state is None
    finally:
        game.close()


def test_existing_crisis_and_military_reports_do_not_automatically_relay_to_intelligence(engine, tmp_path):
    control = fullstack(tmp_path / "legacy-active-reports.sqlite")
    intelligence_before = copy.deepcopy(engine.true_state.intelligence_state)
    try:
        receipts = []
        for game in (engine, control):
            event = game.inject_event("flood", "ra", ["a"], severity=.7,
                                      actual_facts={"water": "river", "damage": 12})
            crisis_report = game.send_event_report(event["event_id"], "a:ruler", delay=2,
                observed_facts={"water": "river", "damage": 9}, source="legacy crisis observer")
            game.plan_operation("legacy-defensive-report", "a", ["army-a"], "defensive", "ra", issuer="a")
            military_report = game.send_military_report("legacy-defensive-report", "a:ruler",
                {"threat": .6, "enemy_strength": 50}, delay=1, source="legacy quartermaster")
            receipts.append((event["event_id"], crisis_report["message_id"], military_report["id"]))
            game.advance(2)
        assert receipts[0] == receipts[1]
        enabled_world, legacy_world = engine.true_state, control.true_state
        assert enabled_world.crisis_state == legacy_world.crisis_state
        assert enabled_world.military_state == legacy_world.military_state
        assert enabled_world.npc_state == legacy_world.npc_state
        assert engine.observed == control.observed
        assert engine.rng.getstate() == control.rng.getstate()
        event_id, crisis_id, military_id = receipts[0]
        assert enabled_world.crisis_state["messages"][crisis_id]["status"] == "delivered"
        assert enabled_world.military_state["commands"][military_id]["status"] == "delivered"
        reports = enabled_world.npc_state["reports"][PERSON["id"]]
        assert reports["event:" + event_id]["data"]["facts"]["damage"] == 9
        assert reports["military-operation:legacy-defensive-report"]["data"]["facts"] == {"threat": .6, "enemy_strength": 50}
        memories = enabled_world.npc_state["memories"][PERSON["id"]]
        assert any(memory["event_id"] == event_id for memory in memories)
        assert any(memory["event_id"] == "military-observation:legacy-defensive-report" for memory in memories)
        assert enabled_world.intelligence_state == intelligence_before
        assert histories(engine) == []
        assert not any(row["kind"] == "system_event" and row["payload"].get("system") == "intelligence"
                       for row in engine.ledger())
    finally:
        control.close()
