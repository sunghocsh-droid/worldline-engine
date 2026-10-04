"""Contact receipts survive all four checkpoints and failed commits atomically."""
import copy
import hashlib
import json
import os
import sqlite3

import pytest

from worldline import Engine, state_storage
from worldline.audit import AuditError
from worldline.engine import encode
from test_crisis_integration import rewrite
from test_diplomacy_intelligence_foundation import assert_clean, committed, fullstack
from test_diplomacy_contact import (PLAYER_A, PLAYER_B, PERSON_A, PERSON_B,
    contact, contact_engine, day, dispatches, propose, receive, respond)


@pytest.fixture
def engine(tmp_path):
    game = contact_engine(tmp_path / "contact-persistence.sqlite")
    yield game
    game.close()


def exact(game):
    return {"stored": game._stored_payload(), "ledger": game.ledger(),
            "archive": state_storage.export_archive(game.db),
            "rng": game.rng.getstate(),
            "views": [game.diplomacy_view(subject)
                      for subject in (PERSON_A, PLAYER_A, PERSON_B, PLAYER_B)]}


@pytest.mark.parametrize("checkpoint", range(4), ids=["proposal-in-transit", "proposal-received",
    "response-in-transit", "response-received"])
@pytest.mark.parametrize("result", ["accept", "reject"])
def test_save_load_continue_exact_at_every_contact_checkpoint(engine, tmp_path, checkpoint, result):
    submission = propose(engine)
    response = None
    if checkpoint >= 1:
        receive(engine, submission)
    if checkpoint >= 2:
        response = respond(engine, result=result)
    if checkpoint >= 3:
        receive(engine, response)
    snapshot = engine.save(tmp_path / "checkpoint.json")
    payload = json.loads(snapshot.read_text(encoding="utf-8"))["payload"]
    assert payload["schema_version"] == 7
    assert payload["engine"]["world"]["schema_version"] == 6
    loaded = Engine.load(snapshot, tmp_path / "resumed.sqlite")
    try:
        assert exact(loaded) == exact(engine)
        assert loaded.save(tmp_path / "loaded-checkpoint.json").read_bytes() == snapshot.read_bytes()
        for game in (engine, loaded):
            assert propose(game) == submission
            if checkpoint < 1:
                receive(game, submission)
            current_response = respond(game, result=result)
            if response is not None:
                assert current_response == response
            if checkpoint < 3:
                receive(game, current_response)
            before = committed(game)
            assert game.tick_diplomacy() == []
            assert respond(game, result=result) == current_response
            assert_clean(game, before)
            game.advance(2)
        assert exact(loaded) == exact(engine)
        assert contact(engine, PLAYER_A)["status"] == {"accept": "accepted", "reject": "rejected"}[result]
        assert loaded.save(tmp_path / "resumed-final.json").read_bytes() == engine.save(tmp_path / "final.json").read_bytes()
    finally:
        loaded.close()


def test_actual_records_have_existing_single_ledger_and_content_addressed_archive(engine):
    submission = propose(engine)
    queued = dispatches(engine, submission)[0]
    receive(engine, submission)
    response = respond(engine)
    receive(engine, response)
    state = engine.true_state.diplomacy_state
    proposal_history = engine.historical_state(kind="diplomacy_proposals", item="contact-one")
    assert proposal_history
    assert len(engine.historical_state(kind="diplomacy_dispatches", item=queued["id"])) >= 2
    for identity in (submission["id"], response["id"]):
        assert engine.historical_state(kind="diplomacy_receipts", item=identity)
        assert identity in encode(engine.ledger())
    assert state["outbox"] == []
    tables = {name for name, in engine.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ledger" in tables
    assert not any("diplomacy" in name for name in tables)


@pytest.mark.parametrize("operation", ["propose", "respond", "proposal-delivery", "response-delivery"])
@pytest.mark.parametrize("table, action", [("ledger", "INSERT"), ("state_archive", "INSERT"),
    ("archive_catalog", "INSERT"), ("state_components", "UPDATE"),
    ("state_heads", "INSERT"), ("current_state", "UPDATE")])
def test_sql_failure_restores_proposal_outbox_receipts_perception_rng_and_archive(engine, operation, table, action):
    if operation != "propose":
        submission = propose(engine)
        if operation == "proposal-delivery":
            day(engine, dispatches(engine, submission)[0]["delivery_date"])
        else:
            receive(engine, submission)
            if operation == "response-delivery":
                response = respond(engine)
                day(engine, dispatches(engine, response)[0]["delivery_date"])
    before = committed(engine)
    engine.db.execute(f"CREATE TRIGGER fail_contact BEFORE {action} ON {table} "
                      "BEGIN SELECT RAISE(ABORT,'contact write failure'); END")
    def execute():
        if operation == "propose":
            return propose(engine)
        if operation == "respond":
            return respond(engine)
        return engine.tick_diplomacy()
    try:
        with pytest.raises(sqlite3.DatabaseError, match="contact write failure"):
            execute()
        assert_clean(engine, before)
    finally:
        engine.db.execute("DROP TRIGGER fail_contact")
    execute()


@pytest.mark.parametrize("operation", ["propose", "respond"])
def test_failure_after_dispatch_creation_restores_request_and_proposal_mutation(engine, monkeypatch, operation):
    from worldline import diplomacy
    if operation == "respond":
        receive(engine, propose(engine))
    before = committed(engine)
    original = diplomacy._dispatch
    def fail_after_dispatch(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after dispatch creation")
    monkeypatch.setattr(diplomacy, "_dispatch", fail_after_dispatch)
    with pytest.raises(RuntimeError, match="after dispatch creation"):
        propose(engine) if operation == "propose" else respond(engine)
    assert_clean(engine, before)


def test_failure_after_person_perception_restores_delivery_and_memory(engine, monkeypatch):
    from worldline import diplomacy_perception
    submission = propose(engine)
    day(engine, dispatches(engine, submission)[0]["delivery_date"])
    before = committed(engine)
    original = diplomacy_perception.apply_person
    def fail_after_person(*args, **kwargs):
        original(*args, **kwargs)
        assert engine._world.npc_state["memories"]["b:ruler"]
        raise RuntimeError("after contact person perception")
    monkeypatch.setattr(diplomacy_perception, "apply_person", fail_after_person)
    with pytest.raises(RuntimeError, match="after contact person perception"):
        engine.tick_diplomacy()
    assert_clean(engine, before)


def test_failure_after_player_perception_restores_received_reply_and_observed(engine, monkeypatch):
    receive(engine, propose(engine))
    response = respond(engine)
    day(engine, dispatches(engine, response)[0]["delivery_date"])
    before = committed(engine)
    original = engine._apply_observation
    def fail_after_player(*args, **kwargs):
        original(*args, **kwargs)
        assert engine._observed[PLAYER_A["id"]]
        assert contact(engine, PLAYER_A)["status"] == "pending"
        raise RuntimeError("after contact player perception")
    monkeypatch.setattr(engine, "_apply_observation", fail_after_player)
    with pytest.raises(RuntimeError, match="after contact player perception"):
        engine.tick_diplomacy()
    assert_clean(engine, before)


def test_candidate_delivery_is_hidden_until_commit_and_rollback_removes_it(engine, monkeypatch):
    from worldline import diplomacy
    submission = propose(engine)
    day(engine, dispatches(engine, submission)[0]["delivery_date"])
    before = committed(engine)
    original = diplomacy.deliver_due
    def fail_after_delivery(*args, **kwargs):
        events = original(*args, **kwargs)
        assert events
        assert engine.diplomacy_view(PERSON_B)["history"] == []
        assert engine.diplomacy_view(PERSON_B)["contacts"] == []
        raise RuntimeError("candidate contact delivery")
    monkeypatch.setattr(diplomacy, "deliver_due", fail_after_delivery)
    with pytest.raises(RuntimeError, match="candidate contact delivery"):
        engine.tick_diplomacy()
    assert_clean(engine, before)


def test_candidate_component_and_current_state_writes_do_not_escape_committed_public_views(engine, monkeypatch):
    receive(engine, propose(engine))
    respond(engine)
    observation = engine.ingest_observation({"domain": "trusted-test", "id": "observer"},
        {"strength": 150}, subject="b", topic="readiness", as_of=engine.true_state.date,
        observation_id="component-observation")
    message = engine.queue_intelligence_report(observation["id"], PLAYER_A, message_id="component-message")
    expected_diplomacy = engine.diplomacy_view(PLAYER_A)
    expected_intelligence = engine.intelligence_view(PLAYER_A)
    expected_observed = engine.observed
    before = committed(engine)
    original = state_storage.write_components
    def fail_after_component_writes(db, *args, **kwargs):
        physical = original(db, *args, **kwargs)
        db.execute("UPDATE current_state SET payload=? WHERE id=1", (encode(physical),))
        assert engine._world.intelligence_state["dispatches"][message["id"]]["status"] == "delivered"
        assert engine._world.diplomacy_state["proposals"]["contact-one"]["status"] == "accepted"
        assert engine._observed[PLAYER_A["id"]]
        assert engine.diplomacy_view(PLAYER_A) == expected_diplomacy
        assert engine.intelligence_view(PLAYER_A) == expected_intelligence
        assert engine.observed == expected_observed
        raise RuntimeError("after contact candidate SQL components")
    monkeypatch.setattr(state_storage, "write_components", fail_after_component_writes)
    with pytest.raises(RuntimeError, match="after contact candidate SQL components"):
        engine.advance(3)
    assert_clean(engine, before)


def test_missing_recipient_during_delivery_rolls_back_prior_due_dispatches(engine, monkeypatch):
    from worldline import diplomacy
    first = propose(engine)
    second = propose(engine, request_id="second", proposal_id="second")
    day(engine, dispatches(engine, second)[0]["delivery_date"])
    before = committed(engine)
    original = diplomacy._recipient
    def unavailable_second(world, reference, *args, **kwargs):
        if engine._world.diplomacy_state["dispatches"][dispatches_from_committed[0]]["status"] == "delivered":
            raise ValueError("contact recipient no longer valid")
        return original(world, reference, *args, **kwargs)
    dispatches_from_committed = first["dispatch_ids"]
    monkeypatch.setattr(diplomacy, "_recipient", unavailable_second)
    with pytest.raises(ValueError, match="contact recipient no longer valid"):
        engine.tick_diplomacy()
    assert_clean(engine, before)


@pytest.mark.parametrize("bucket", ["proposals", "dispatches", "receipts"])
@pytest.mark.parametrize("change", ["remove", "modify"])
def test_committed_contact_bodies_receipts_and_delivered_dispatches_are_immutable(engine, bucket, change):
    receive(engine, propose(engine))
    response = respond(engine)
    receive(engine, response)
    before = committed(engine)
    with pytest.raises((ValueError, AuditError)):
        with engine._transaction():
            records = engine._world.diplomacy_state[bucket]
            key = response["id"] if bucket == "receipts" else next(iter(records))
            if change == "remove":
                del records[key]
            elif bucket == "proposals":
                records[key]["body"]["content"]["message"] = "changed"
            elif bucket == "dispatches":
                records[key]["payload"]["content"]["message"] = "changed"
            else:
                records[key]["result"] = "rejected"
    assert_clean(engine, before)


def tamper_contact(payload, case):
    state = payload["engine"]["world"]["diplomacy_state"]
    proposal = state["proposals"]["contact-one"]
    submission = state["receipts"][proposal["submission_receipt_id"]]
    response = state["receipts"][proposal["response_receipt_id"]]
    dispatched = list(state["dispatches"].values())
    proposal_dispatch = next(item for item in dispatched if item["message_kind"] == "proposal")
    response_dispatch = next(item for item in dispatched if item["message_kind"] == "response")
    if case == "body":
        proposal["body"]["content"]["message"] = "forged body"
    elif case == "source_truth":
        proposal["body"]["source_ref"]["actual_truth"] = {"secret": True}
    elif case == "future_creation":
        proposal["body"]["created_at"] = "1999-01-01"
    elif case == "historical_authorization":
        response["authorization"]["active"] = False
    elif case == "authority_scope":
        submission["authorization"]["authority"] = []
    elif case == "wrong_executor_delivery":
        response["proposal_delivery_receipt_id"] = response_dispatch["delivery_receipt_id"]
    elif case == "response_actual_status":
        proposal["status"] = "rejected"
    elif case == "payload_result":
        response_dispatch["payload"]["result"] = "rejected"
    elif case == "payload_audit":
        proposal_dispatch["payload"]["audit"] = {"accuracy": 1}
    elif case == "recipient_namespace":
        proposal_dispatch["recipient"]["kind"] = "country"
    elif case == "delivery_date":
        response_dispatch["delivery_date"] = response_dispatch["sent_at"]
    elif case == "received_early":
        response_dispatch["received_at"] = response_dispatch["sent_at"]
    elif case == "missing_submission_receipt":
        del state["receipts"][submission["id"]]
    elif case == "missing_response_receipt":
        del state["receipts"][response["id"]]
    elif case == "missing_delivery_receipt":
        del state["receipts"][response_dispatch["delivery_receipt_id"]]
    elif case == "missing_application_receipt":
        del state["receipts"][response_dispatch["application_receipt_id"]]
    elif case == "application_flag":
        state["receipts"][response_dispatch["application_receipt_id"]]["applied"] = False
    elif case == "counter":
        state["next_ids"]["sequence"] = response_dispatch["sequence"]
    elif case == "outbox":
        state["outbox"].append(response_dispatch["id"])
    elif case == "identity_collision":
        del state["proposals"][proposal["id"]]
        proposal["id"] = response["id"]
        state["proposals"][proposal["id"]] = proposal
    elif case.startswith("missing_ledger_"):
        missing = case.removeprefix("missing_ledger_")
        def keep(row):
            event = row["payload"]
            if row["kind"] != "system_event" or event.get("system") != "diplomacy":
                return True
            if missing in ("proposal_submitted", "contact_responded"):
                return event["kind"] != missing
            return not (event["kind"] == "dispatch_delivered" and event["dispatch"]["message_kind"] == missing)
        payload["ledger"][:] = [row for row in payload["ledger"] if keep(row)]
        for index, row in enumerate(payload["ledger"], 1):
            row["id"] = index
    elif case == "second_terminal_ledger":
        duplicate = copy.deepcopy(next(row for row in payload["ledger"] if row["kind"] == "system_event"
            and row["payload"].get("system") == "diplomacy"
            and row["payload"].get("kind") == "contact_responded"))
        duplicate["id"] = max(row["id"] for row in payload["ledger"]) + 1
        payload["ledger"].append(duplicate)
    elif case.startswith("missing_archive_"):
        missing = case.removeprefix("missing_archive_")
        archive = payload["state_archive"]
        def keep(row):
            if row["kind"] != "diplomacy_" + ("proposals" if missing == "pending" else "dispatches"):
                return True
            record = archive["objects"][row["digest"]]
            if missing == "pending":
                return record["status"] != "pending"
            return not (record["status"] == "queued" and record["message_kind"] == missing)
        archive["catalog"][:] = [row for row in archive["catalog"] if keep(row)]
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", ["body", "source_truth", "future_creation", "historical_authorization",
    "authority_scope", "wrong_executor_delivery", "response_actual_status", "payload_result", "payload_audit",
    "recipient_namespace", "delivery_date", "received_early", "missing_submission_receipt", "missing_response_receipt",
    "missing_delivery_receipt", "missing_application_receipt", "application_flag", "counter", "outbox",
    "identity_collision", "missing_ledger_proposal_submitted", "missing_ledger_contact_responded",
    "missing_ledger_proposal", "missing_ledger_response", "second_terminal_ledger",
    "missing_archive_pending", "missing_archive_proposal", "missing_archive_response"])
def test_rehashed_contact_records_and_history_fail_closed(engine, tmp_path, case):
    receive(engine, propose(engine))
    receive(engine, respond(engine))
    snapshot = engine.save(tmp_path / "valid-contact.json")
    forged = tmp_path / "forged-contact.json"
    rewrite(snapshot, forged, lambda payload: tamper_contact(payload, case))
    destination = tmp_path / "rejected-contact.sqlite"
    with pytest.raises((ValueError, AuditError)):
        Engine.load(forged, destination)
    assert not destination.exists()


@pytest.mark.parametrize("loss", ["inactive", "scope", "government_role"])
def test_valid_terminal_contact_loads_after_current_representative_authority_changes(engine, tmp_path, loss):
    receive(engine, propose(engine))
    receive(engine, respond(engine))
    with engine._transaction():
        state = engine._world.npc_state
        if loss == "inactive":
            state["persons"]["b:ruler"]["active"] = False
        elif loss == "scope":
            state["persons"]["b:ruler"]["authority"].remove("diplomatic_engagement")
        else:
            state["governments"]["b"]["ruler"] = "b:observer"
    snapshot = engine.save(tmp_path / "historical-authority.json")
    loaded = Engine.load(snapshot, tmp_path / "historical-authority.sqlite")
    try:
        assert exact(loaded) == exact(engine)
        assert contact(loaded, PLAYER_A)["status"] == "accepted"
        before = committed(loaded)
        assert respond(loaded)["result"] == "accepted"
        assert loaded.tick_diplomacy() == []
        assert_clean(loaded, before)
    finally:
        loaded.close()


def test_zero_delay_autonomous_round_trip_retains_every_frozen_archive_version(tmp_path):
    from test_diplomacy_npc_contact import make_contact_engine, propose as npc_propose, PROPOSAL
    game = make_contact_engine(tmp_path / "zero-delay.sqlite", goals=["diplomacy"],
                               proposal_delay=0, response_delay=0)
    loaded = None
    try:
        submission = npc_propose(game)
        assert contact(game, PLAYER_A, PROPOSAL)["status"] == "pending"
        assert game.diplomacy_view(PERSON_B)["history"] == []
        rng = game.rng.getstate()
        game.tick_diplomacy()
        assert contact(game, PLAYER_A, PROPOSAL)["status"] == "accepted"
        assert contact(game, PLAYER_A, PROPOSAL)["response_received_at"] == game.true_state.date
        assert game.rng.getstate() == rng
        proposal_versions = game.historical_state(kind="diplomacy_proposals", item=PROPOSAL)
        assert {row["value"]["status"] for row in proposal_versions} == {"pending", "accepted"}
        for dispatch in game.true_state.diplomacy_state["dispatches"].values():
            versions = game.historical_state(kind="diplomacy_dispatches", item=dispatch["id"])
            assert {row["value"]["status"] for row in versions} == {"queued", "delivered"}
        snapshot = game.save(tmp_path / "zero-delay.json")
        loaded = Engine.load(snapshot, tmp_path / "zero-delay-loaded.sqlite")
        assert loaded._stored_payload() == game._stored_payload()
        assert loaded.ledger() == game.ledger()
        assert state_storage.export_archive(loaded.db) == state_storage.export_archive(game.db)
        before = committed(loaded)
        assert loaded.tick_diplomacy() == []
        assert npc_propose(loaded) == submission
        assert_clean(loaded, before)
        def omit_queued_reply(payload):
            archive = payload["state_archive"]
            archive["catalog"][:] = [row for row in archive["catalog"] if not (
                row["kind"] == "diplomacy_dispatches"
                and archive["objects"][row["digest"]]["message_kind"] == "response"
                and archive["objects"][row["digest"]]["status"] == "queued")]
        bad = tmp_path / "zero-delay-forged.json"
        rewrite(snapshot, bad, omit_queued_reply)
        with pytest.raises((ValueError, AuditError)):
            Engine.load(bad, tmp_path / "zero-delay-rejected.sqlite")
    finally:
        if loaded is not None:
            loaded.close()
        game.close()


def test_failure_after_npc_response_traces_rolls_back_delivery_decisions_and_action(tmp_path, monkeypatch):
    from worldline import diplomacy
    from test_diplomacy_npc_contact import make_contact_engine, propose as npc_propose
    game = make_contact_engine(tmp_path / "npc-rollback.sqlite", goals=["diplomacy"])
    try:
        submission = npc_propose(game)
        day(game, dispatches(game, submission)[0]["delivery_date"])
        before = committed(game)
        original = diplomacy.respond
        def fail_after_response(*args, **kwargs):
            original(*args, **kwargs)
            assert game._world.npc_state["decisions"]
            assert game._world.npc_state["actions"]
            assert game._world.npc_state["memories"][PERSON_B["id"]]
            raise RuntimeError("after NPC contact execution traces")
        monkeypatch.setattr(diplomacy, "respond", fail_after_response)
        with pytest.raises(RuntimeError, match="after NPC contact execution traces"):
            game.tick_diplomacy()
        assert_clean(game, before)
    finally:
        game.close()


@pytest.mark.parametrize("kind", ["decision", "action"])
@pytest.mark.parametrize("change", ["missing_archive", "altered_archive", "altered_current"])
def test_npc_contact_trace_evidence_cannot_be_rehashed_or_removed(tmp_path, kind, change):
    from test_diplomacy_npc_contact import make_contact_engine, propose as npc_propose
    game = make_contact_engine(tmp_path / "trace.sqlite", goals=["diplomacy"])
    try:
        submission = npc_propose(game)
        receive(game, submission)
        snapshot = game.save(tmp_path / "trace.json")
        def tamper(payload):
            archive = payload["state_archive"]
            if change == "missing_archive":
                archive["catalog"][:] = [row for row in archive["catalog"] if row["kind"] != kind]
            elif change == "altered_archive":
                row = next(row for row in archive["catalog"] if row["kind"] == kind)
                old_digest = row["digest"]
                record = archive["objects"].pop(old_digest)
                if kind == "decision":
                    record["reason_trace"].append({"factor": "forged", "contribution": 999})
                else:
                    record["result"]["contact_result"] = "rejected"
                new_digest = digest(record)
                archive["objects"][new_digest] = record
                for catalog_row in archive["catalog"]:
                    if catalog_row["digest"] == old_digest:
                        catalog_row["digest"] = new_digest
            else:
                record = payload["engine"]["world"]["npc_state"][kind + "s"][0]
                if kind == "decision":
                    record["reason_trace"].append({"factor": "forged", "contribution": 999})
                else:
                    record["result"]["contact_result"] = "rejected"
        bad = tmp_path / "trace-forged.json"
        rewrite(snapshot, bad, tamper)
        destination = tmp_path / "trace-rejected.sqlite"
        with pytest.raises((ValueError, AuditError)):
            Engine.load(bad, destination)
        assert not destination.exists()
    finally:
        game.close()


@pytest.mark.parametrize("rewind", [False, True])
def test_saved_contact_npc_decision_counter_preserves_gaps_and_rejects_reuse(tmp_path, rewind):
    from test_diplomacy_npc_contact import make_contact_engine, propose as npc_propose
    game = make_contact_engine(tmp_path / "decision-counter.sqlite", goals=["diplomacy"])
    try:
        with game._transaction():
            game._world.npc_state["next_decision_id"] = 10
        receive(game, npc_propose(game))
        response = next(receipt for receipt in game.true_state.diplomacy_state["receipts"].values()
                        if receipt["kind"] == "response")
        assert response["decision_ids"] == ["decision-10"]
        # Existing NPC history may trim its hot window; receipt identity and
        # immutable archive still prohibit reusing the historical decision ID.
        with game._transaction():
            game._world.npc_state["decisions"] = []
            game._world.npc_state["actions"] = []
        snapshot = game.save(tmp_path / "decision-counter.json")
        if rewind:
            bad = tmp_path / "decision-counter-forged.json"
            def reset(payload):
                payload["engine"]["world"]["npc_state"]["next_decision_id"] = 1
            rewrite(snapshot, bad, reset)
            with pytest.raises((ValueError, AuditError)):
                Engine.load(bad, tmp_path / "decision-counter-rejected.sqlite")
        else:
            loaded = Engine.load(snapshot, tmp_path / "decision-counter-loaded.sqlite")
            try:
                assert loaded._stored_payload() == game._stored_payload()
                assert loaded.ledger() == game.ledger()
                assert loaded.true_state.npc_state["next_decision_id"] == 11
            finally:
                loaded.close()
    finally:
        game.close()


def test_contact_cannot_reuse_legacy_archived_npc_trace_identity_and_rolls_back_every_layer(tmp_path):
    from test_diplomacy_npc_contact import make_contact_engine, propose as npc_propose
    game = make_contact_engine(tmp_path / "legacy-trace-collision.sqlite", goals=["diplomacy"])
    try:
        game.advance(1)
        legacy = game.historical_state(kind="decision", item="decision-1")
        assert legacy
        with game._transaction():
            game._world.npc_state["decisions"] = []
            game._world.npc_state["actions"] = []
            game._world.npc_state["next_decision_id"] = 1
        submission = npc_propose(game)
        day(game, dispatches(game, submission)[0]["delivery_date"])
        before = committed(game)
        with pytest.raises(ValueError, match="archive|identity"):
            game.tick_diplomacy()
        assert_clean(game, before)
        assert game.historical_state(kind="decision", item="decision-1") == legacy
    finally:
        game.close()


# Frozen against the committed Stage 2 implementation, seed 71 and the tiny
# fullstack fixture at 50216e7fce8329a091115577d6567152bd96986b.
# Both newline encodings are retained because Engine.save
# writes native text newlines. No baseline source/artifact is read by the tests.
STAGE2_DIGESTS = {
    "di_off": {
        "opening": {
            "world": "2ec8ea6a5de35671e5f010fc8b78ece3c5eb13ff808945a6bd9696c77204d205",
            "rng": "9e4e23946aaa7f396ace02c0d8ce614484d9b2a4d258f2ffc3c4c63ac869274a",
            "ledger": "24e2f20fbaeb8918add5450169d62a81a02f28665802e6f0ecf2b9342c5c695b",
            "archive": "74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b",
            "save_lf": "9f956044de09254152338f0bace4ea2aa439ab731fb695ed4300b5062516b921",
            "save_crlf": "1815aaf78c6c8e1d29682f2c11a1fd20b440a4dd2c6c9ae54d2495150130db4f"},
        "delivered": {
            "world": "351d906e5505afd9896d586066ac7b91bbac8699d8aa131e1e3837c32f1a7fc8",
            "rng": "f84295cefa6798e9425b7c261d5be59f354a630a1a7dc28f6d3f4ee6806439a6",
            "ledger": "fbca7996994d5fde7ee0e943aaddcf270c9759c82109964d3403e99707ed30ef",
            "archive": "43c7cef3b4f0ed923eecd2d2a7e673f6c805e4925690df72776a43f757b38440",
            "save_lf": "ec1d94c210429b9a5ccc9b6dd60ed3e1a505905381cb9490cc286fb5943802c2",
            "save_crlf": "d4e27122207050dd4ee2ee752ebb5a9fb5dfee88239e9f2b7f863bdc0beffe2d"},
        "closing": {
            "world": "fe3e57635e885cf07911897e9cf3396832b6585a1063b42beed38605cdbf7c75",
            "rng": "f84295cefa6798e9425b7c261d5be59f354a630a1a7dc28f6d3f4ee6806439a6",
            "ledger": "439c23afbcba6c0fc124236b14ad8f823f65571ac419ea8a87aa16c642d7656f",
            "archive": "fad9a274821358be2ba3ea0f7e0ac18b0934942d8351d43078c7bc9dc38f6177",
            "save_lf": "8a95fedde34f2d3a24c8bd8e0671b4501cbfa3b3dcd81824e31dce8106fb4c90",
            "save_crlf": "b99957805338f92ea6cfd16e426361cd2dffa3e62dbac6e1a204d6d9b2219d53"}},
    "contact_empty": {
        "opening": {
            "world": "1b812b366513e1b12e6a85d3fed881965fcd10036addf955e1f986f9899fcfa2",
            "rng": "9e4e23946aaa7f396ace02c0d8ce614484d9b2a4d258f2ffc3c4c63ac869274a",
            "ledger": "67bea449b9eb9c6d70c67f8d3c9da2456f413851058752eb5d06899c13798a86",
            "archive": "355647f3e1b4dd32cda693410fb07bc06087649839f375bd53c855e99731f935",
            "save_lf": "f06f170de585f9c42354a8d9b513fe663702a58b7a96501014cfc5b1ce47dfa6",
            "save_crlf": "7788ad58ee7447738f376b6d3f901549c4d122ca2b089cd289612ef6569545bd"},
        "delivered": {
            "world": "246ea7a97b11c8d9f45847551162f24b8d525376d041058ffa86d462c7560d0c",
            "rng": "f84295cefa6798e9425b7c261d5be59f354a630a1a7dc28f6d3f4ee6806439a6",
            "ledger": "7bc3be2d669276ed012ba680b680654e6bac70fc5834e2b314cffd3f98431074",
            "archive": "791206e357cbeaa820aebc1b11586ad348211513bd0b1f75f440e771a005ddad",
            "save_lf": "8065eb5f0d8df56a2bf844b0a698d2263e8d83f2a543dbb4576e156c51d9d579",
            "save_crlf": "3841cf095185d53b110693364dcda4d750cb984d22e9a729aa8efc372228904f"},
        "closing": {
            "world": "4908167e657e52bad188835a3931f58f5c35e8c0ab61c1cb4b625fea66a4a2cb",
            "rng": "f84295cefa6798e9425b7c261d5be59f354a630a1a7dc28f6d3f4ee6806439a6",
            "ledger": "5a9ece3f828e26eff23f8f00a9c61525fc1b4290e027d82806f1b3e035076d43",
            "archive": "f5a6439d16b9ce4ebc9f58743cc8bfecb8d66ce971abafa788d247d9e3a503f3",
            "save_lf": "ee13074ae3517d908d7d1b456901e956c9304a6a0afd11ea98280289ba613ac4",
            "save_crlf": "d8091c7e7049845f77b241c8230ab8421984bc5b798224663545b7b6358e459d"}}}


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def assert_stage2_checkpoint(game, path, expected):
    assert digest(game.true_state.to_dict()) == expected["world"]
    assert digest(game.rng.getstate()) == expected["rng"]
    assert digest(game.ledger()) == expected["ledger"]
    assert digest(state_storage.export_archive(game.db)) == expected["archive"]
    save = game.save(path)
    raw = save.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected["save_crlf" if os.linesep == "\r\n" else "save_lf"]
    return save


@pytest.mark.parametrize("case", ["di_off", "contact_empty"])
def test_feature_off_and_existing_intelligence_equal_committed_stage2_bytes(tmp_path, case):
    game = fullstack(tmp_path / "baseline.sqlite")
    loaded = None
    try:
        if case == "contact_empty":
            game.enable_diplomacy_intelligence({"diplomacy": {"player_bindings": {
                "a:ruler": {"country_id": "a", "representative_person_id": "a:ruler"}}}})
            observation = game.ingest_observation({"domain": "trusted-test", "id": "observer"},
                {"strength": 150}, subject="b", topic="readiness", as_of=game.true_state.date,
                observation_id="baseline-observation")
            for recipient, message_id in ((PERSON_A, "baseline-person"), (PLAYER_A, "baseline-player")):
                game.queue_intelligence_report(observation["id"], recipient, message_id=message_id)
        expected = STAGE2_DIGESTS[case]
        assert_stage2_checkpoint(game, tmp_path / "opening.json", expected["opening"])
        game.advance(1)
        delivered = assert_stage2_checkpoint(game, tmp_path / "delivered.json", expected["delivered"])
        loaded = Engine.load(delivered, tmp_path / "loaded.sqlite")
        for current in (game, loaded):
            current.advance(1)
        assert_stage2_checkpoint(game, tmp_path / "closing.json", expected["closing"])
        assert_stage2_checkpoint(loaded, tmp_path / "resumed-closing.json", expected["closing"])
        assert loaded._stored_payload() == game._stored_payload()
        assert loaded.ledger() == game.ledger()
        assert state_storage.export_archive(loaded.db) == state_storage.export_archive(game.db)
    finally:
        if loaded is not None:
            loaded.close()
        game.close()
