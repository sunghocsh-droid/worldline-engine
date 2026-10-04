"""Persisted delivery records fail closed, including permanent history evidence."""
import copy

import pytest

from worldline import Engine
from worldline.audit import AuditError
from test_crisis_integration import rewrite
from test_diplomacy_intelligence_foundation import assert_clean, committed
from test_intelligence_delivery import engine, ingest, queue, deliver, PERSON, PLAYER


@pytest.mark.parametrize("field", ["observations", "dispatches", "receipts"])
@pytest.mark.parametrize("operation", ["delete", "modify"])
def test_committed_intelligence_records_are_immutable(engine, field, operation):
    message = queue(engine, ingest(engine))
    deliver(engine, message)
    before = committed(engine)
    with pytest.raises((ValueError, AuditError)):
        with engine._transaction():
            records = engine._world.intelligence_state[field]
            key = next(iter(records))
            if operation == "delete":
                del records[key]
            elif field == "observations":
                records[key]["observed_data"]["strength"] = 999
            elif field == "dispatches":
                records[key]["payload"]["observed_data"]["strength"] = 999
            else:
                records[key]["date"] = "1897-01-01"
    assert_clean(engine, before)


@pytest.mark.parametrize("original,retry", [(True, 1), (1, 1.0), (-0.0, 0.0)])
def test_json_distinct_idempotency_intents_are_conflicts(engine, original, retry):
    ingest(engine, data={"value": original})
    before = committed(engine)
    with pytest.raises(ValueError, match="conflict"):
        ingest(engine, data={"value": retry})
    assert_clean(engine, before)


def test_extreme_audit_quality_is_a_validation_error_without_effects(engine):
    before = committed(engine)
    with pytest.raises(ValueError):
        ingest(engine, audit={"accuracy": 10 ** 400})
    assert_clean(engine, before)


@pytest.mark.parametrize("recipient", [PERSON, PLAYER])
def test_redelivery_cannot_promote_original_over_same_as_of_correction(engine, recipient):
    original = ingest(engine, data={"strength": 100})
    deliver(engine, queue(engine, original, recipient))
    correction = ingest(engine, observation_id="corrected", as_of=original["as_of"], data={"strength": 80})
    deliver(engine, queue(engine, correction, recipient, message_id="correction"))
    before = engine.true_state.npc_state, engine.observed
    deliver(engine, queue(engine, original, recipient, message_id="redelivery"))
    assert (engine.true_state.npc_state, engine.observed) == before
    view = engine.intelligence_view(recipient)
    assert len(view["history"]) == 3
    assert view["latest"][0]["observation_id"] == correction["id"]


def tamper(payload, case):
    state = payload["engine"]["world"]["intelligence_state"]
    observation = next(iter(state["observations"].values()))
    dispatch = next(iter(state["dispatches"].values()))
    if case == "observation_body":
        observation["observed_data"]["strength"] = 999
    elif case == "source_ref":
        observation["source_ref"]["truth"] = True
    elif case == "future_collection":
        observation["collected_at"] = "1999-01-01"
    elif case == "payload_audit":
        dispatch["payload"]["audit"] = {"accuracy": 1}
    elif case == "recipient_namespace":
        dispatch["recipient"]["kind"] = "country"
    elif case == "delivery_date":
        dispatch["delivery_date"] = dispatch["sent_at"]
    elif case == "received_early":
        dispatch["received_at"] = dispatch["sent_at"]
    elif case == "counter":
        state["next_ids"]["sequence"] = dispatch["sequence"]
    elif case == "outbox":
        state["outbox"].append(dispatch["id"])
    elif case == "missing_delivery_receipt":
        del state["receipts"][dispatch["delivery_receipt_id"]]
    elif case == "missing_application_receipt":
        del state["receipts"][dispatch["application_receipt_id"]]
    elif case == "application_flag":
        state["receipts"][dispatch["application_receipt_id"]]["applied"] = False
        state["receipts"][dispatch["application_receipt_id"]]["reason"] = "stale"
    elif case.startswith("missing_ledger_"):
        kind = case.removeprefix("missing_ledger_")
        payload["ledger"][:] = [row for row in payload["ledger"] if not (
            row["kind"] == "system_event" and row["payload"].get("system") == "intelligence"
            and row["payload"].get("kind") == kind)]
        # Keep generic Ledger sequence valid to test missing domain evidence.
        for identity, row in enumerate(payload["ledger"], 1):
            row["id"] = identity
    elif case == "duplicate_ledger_delivery":
        row = copy.deepcopy(next(row for row in payload["ledger"] if row["kind"] == "system_event"
            and row["payload"].get("system") == "intelligence"
            and row["payload"].get("kind") == "report_delivered"))
        row["id"] = max(row["id"] for row in payload["ledger"]) + 1
        payload["ledger"].append(row)
    elif case.startswith("missing_archive_"):
        kind = "intelligence_" + case.removeprefix("missing_archive_")
        payload["state_archive"]["catalog"][:] = [row for row in payload["state_archive"]["catalog"]
                                                  if row["kind"] != kind]
    elif case == "missing_queued_archive_version":
        archive = payload["state_archive"]
        archive["catalog"][:] = [row for row in archive["catalog"] if not (
            row["kind"] == "intelligence_dispatches"
            and archive["objects"][row["digest"]]["status"] == "queued")]
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "observation_body", "source_ref", "future_collection", "payload_audit", "recipient_namespace",
    "delivery_date", "received_early", "counter", "outbox", "missing_delivery_receipt",
    "missing_application_receipt", "application_flag", "missing_ledger_observation_ingested",
    "missing_ledger_report_queued", "missing_ledger_report_delivered", "duplicate_ledger_delivery",
    "missing_archive_observations", "missing_archive_dispatches", "missing_archive_receipts",
    "missing_queued_archive_version",
])
def test_rehashed_stage2_records_or_history_cannot_be_forged(engine, tmp_path, case):
    deliver(engine, queue(engine, ingest(engine)))
    save = engine.save(tmp_path / "valid-stage2.json")
    bad = tmp_path / "tampered-stage2.json"
    rewrite(save, bad, lambda payload: tamper(payload, case))
    destination = tmp_path / "rejected-stage2.sqlite"
    with pytest.raises((ValueError, AuditError)):
        Engine.load(bad, destination)
    assert not destination.exists()


def test_saved_receipt_counter_cannot_reuse_a_generated_id_after_gaps(engine, tmp_path):
    # Foundation permits forward counter gaps before any record is allocated.
    with engine._transaction():
        engine._world.intelligence_state["next_ids"]["receipt"] = 10
    deliver(engine, queue(engine, ingest(engine)))
    assert set(engine.true_state.intelligence_state["receipts"]) == {
        "intelligence-receipt-10", "intelligence-receipt-11"}
    save = engine.save(tmp_path / "valid-gaps.json")
    bad = tmp_path / "reused-counter.json"
    def rewind(payload):
        payload["engine"]["world"]["intelligence_state"]["next_ids"]["receipt"] = 3
    rewrite(save, bad, rewind)
    with pytest.raises((ValueError, AuditError)):
        Engine.load(bad, tmp_path / "rejected-counter.sqlite")
