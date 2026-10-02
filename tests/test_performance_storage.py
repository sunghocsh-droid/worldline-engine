"""Physical archival layout preserves all original logical save semantics."""
import copy
import json
import sqlite3

import pytest

from worldline import Engine
from worldline import npc_state, state_storage
from worldline.crisis_benchmark import campaign_world
from worldline.engine import encode


@pytest.fixture
def database():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    state_storage.ensure_schema(db)
    yield db
    db.close()


def logical():
    world = campaign_world()
    npc_state.receive_report(world.npc_state, "a:ruler", "test", {"estimate": [1, 2]}, world.date, "local observer")
    return {"world": world.to_dict(), "seed": 941, "rng": [1, 2], "observed": {}, "orders": {}, "journeys": {}}


def test_compact_roundtrip_is_lossless_and_detached(database):
    raw = logical()
    prior = copy.deepcopy(raw)
    physical = state_storage.compact(database, raw)
    assert raw == prior
    restored = state_storage.read(database, encode(physical))
    assert restored == raw
    restored["world"]["npc_state"]["reports"]["a:ruler"]["test"]["data"]["estimate"].append(9)
    assert state_storage.read(database, encode(physical)) == prior


def test_original_sqlite_payload_needs_no_archive(database):
    raw = logical()
    assert state_storage.read(database, encode(raw)) == raw


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_unsupported_physical_version_is_explicit(database, version):
    with pytest.raises(ValueError, match="storage version"):
        state_storage.read(database, encode({**logical(), state_storage.MARKER: version}))


def test_missing_archive_object_is_not_silently_ignored(database):
    raw = logical()
    raw[state_storage.MARKER] = 1
    raw["world"]["npc_state"]["reports"]["a:ruler"]["test"] = {state_storage.REFERENCE: "0" * 64}
    with pytest.raises(ValueError, match="Missing"):
        state_storage.read(database, encode(raw))


def test_user_content_resembling_reference_is_never_reinterpreted(database):
    raw = logical()
    marker = {state_storage.REFERENCE: "0" * 64}
    raw["world"]["canon"]["user_content"] = marker
    raw["world"]["npc_state"]["reports"]["a:ruler"]["test"]["data"]["literal"] = marker
    physical = state_storage.compact(database, raw)
    assert state_storage.read(database, encode(physical)) == raw


def test_archive_corruption_is_detected_on_read(database):
    physical = state_storage.compact(database, logical())
    database.execute("DROP TRIGGER archive_no_update")
    digest, = database.execute("SELECT digest FROM state_archive LIMIT 1").fetchone()
    database.execute("UPDATE state_archive SET raw_bytes=raw_bytes+1 WHERE digest=?", (digest,))
    with pytest.raises(ValueError, match="checksum"):
        state_storage.read(database, encode(physical))


@pytest.mark.parametrize("operation", ["update", "delete", "replace"])
def test_archive_objects_cannot_be_rewritten(database, operation):
    state_storage.compact(database, logical())
    row = database.execute("SELECT * FROM state_archive LIMIT 1").fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        if operation == "update":
            database.execute("UPDATE state_archive SET raw_bytes=raw_bytes+1 WHERE digest=?", (row[0],))
        elif operation == "delete":
            database.execute("DELETE FROM state_archive WHERE digest=?", (row[0],))
        else:
            database.execute("INSERT OR REPLACE INTO state_archive VALUES(?,?,?,?)", (row[0], row[1], b"corrupt", row[3]))


def test_revised_report_retains_original_immutable_version(database):
    raw, cache = logical(), {}
    state_storage.compact(database, raw, cache)
    first = state_storage.history(database, kind="report", owner="a:ruler", item="test")[0]
    raw["world"]["npc_state"]["reports"]["a:ruler"]["test"]["data"]["estimate"].append(3)
    physical = state_storage.compact(database, raw, cache)
    versions = state_storage.history(database, kind="report", owner="a:ruler", item="test")
    assert len(versions) == 2
    assert first in versions
    assert first["value"]["data"]["estimate"] == [1, 2]
    assert state_storage.read(database, encode(physical)) == raw


@pytest.mark.parametrize("before,after", [(1, True), (1, 1.0), (0, False), (-0.0, 0.0), (0.0, -0.0)])
def test_archive_cache_preserves_json_types_and_signed_zero(database, before, after):
    raw, cache = logical(), {}
    report = raw["world"]["npc_state"]["reports"]["a:ruler"]["test"]
    report["data"]["estimate"] = before
    state_storage.compact(database, raw, cache)
    report["data"]["estimate"] = after
    physical = state_storage.compact(database, raw, cache)
    assert encode(state_storage.read(database, encode(physical))) == encode(raw)
    assert len(state_storage.history(database, kind="report", owner="a:ruler", item="test")) == 2


def test_unchanged_objects_do_not_generate_new_archive_rows(database):
    raw, cache = logical(), {}
    state_storage.compact(database, raw, cache)
    counts = tuple(database.execute("SELECT count(*) FROM " + name).fetchone()[0]
                   for name in ("state_archive", "archive_catalog", "state_heads"))
    for _ in range(3):
        state_storage.compact(database, raw, cache)
    assert tuple(database.execute("SELECT count(*) FROM " + name).fetchone()[0]
                 for name in ("state_archive", "archive_catalog", "state_heads")) == counts


def test_cold_decision_remains_queryable_after_hot_window_removal(database):
    raw, cache = logical(), {}
    trace = [{"factor": "report", "detail": {"known_estimate": [1, 2]}, "contribution": 0.2}]
    raw["world"]["npc_state"]["decisions"] = [{"id": "old", "actor": "a:ruler", "date": "1897-01-01", "reason_trace": trace}]
    state_storage.compact(database, raw, cache)
    raw["world"]["npc_state"]["decisions"] = []
    state_storage.compact(database, raw, cache)
    assert not database.execute("SELECT 1 FROM state_heads WHERE kind='decision'").fetchone()
    old = state_storage.history(database, kind="decision", owner="a:ruler", item="old")
    assert old[0]["value"]["reason_trace"] == trace
    old[0]["value"]["reason_trace"].clear()
    assert state_storage.history(database, kind="decision", owner="a:ruler", item="old")[0]["value"]["reason_trace"] == trace


@pytest.mark.parametrize("query,params,index", [
    ("SELECT digest FROM archive_catalog WHERE owner=? AND kind=? AND day>=?", ("a:ruler", "report", "1897-01-01"), "archive_actor_date"),
    ("SELECT digest FROM state_heads WHERE kind=? AND active=? AND country=?", ("event", 1, "a"), "heads_active_country"),
    ("SELECT digest FROM state_heads WHERE kind=? AND parent=?", ("event", "parent"), "heads_parent"),
])
def test_measured_query_shapes_use_targeted_indexes(database, query, params, index):
    state_storage.compact(database, logical())
    plans = " ".join(row[3] for row in database.execute("EXPLAIN QUERY PLAN " + query, params))
    assert "SEARCH" in plans and index in plans


def test_archive_transaction_rolls_back_all_inserted_objects(database):
    raw = logical()
    database.commit()
    database.execute("BEGIN")
    state_storage.compact(database, raw)
    database.rollback()
    for table in ("state_archive", "archive_catalog", "state_heads"):
        assert database.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


def test_engine_old_and_compact_sqlite_save_load_are_logically_identical(tmp_path):
    engine = Engine.create(tmp_path / "game.sqlite", campaign_world(), seed=941)
    try:
        engine.advance(4)
        snapshot = engine.save(tmp_path / "save.json")
        original = engine._stored_payload()
        physical = json.loads(engine.db.execute("SELECT payload FROM current_state").fetchone()[0])
        assert physical[state_storage.MARKER] == 1
        assert encode(original) == encode(engine._payload())
        restored = Engine.load(snapshot, tmp_path / "restored.sqlite")
        try:
            assert restored._stored_payload() == original
            engine.advance(3)
            restored.advance(3)
            assert encode(engine._payload()) == encode(restored._payload())
            assert engine.ledger() == restored.ledger()
            assert engine.rng.getstate() == restored.rng.getstate()
        finally:
            restored.close()
    finally:
        engine.close()


def test_failed_commit_clears_archive_cache_before_retry(tmp_path):
    engine = Engine.create(tmp_path / "game.sqlite", campaign_world(), seed=941)
    try:
        before = engine._stored_payload()
        engine.db.execute("CREATE TRIGGER reject_state BEFORE UPDATE ON current_state BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="test failure"):
            with engine._transaction():
                npc_state.receive_report(engine._world.npc_state, "a:ruler", "fresh", {"test": 2}, engine._world.date, "test")
        assert engine._stored_payload() == before
        assert not engine._storage_cache
        engine.db.execute("DROP TRIGGER reject_state")
        with engine._transaction():
            npc_state.receive_report(engine._world.npc_state, "a:ruler", "fresh", {"test": 2}, engine._world.date, "test")
        assert engine.true_state.npc_state["reports"]["a:ruler"]["fresh"]["data"] == {"test": 2}
    finally:
        engine.close()


def test_json_save_load_retains_superseded_reports_and_historical_trace(tmp_path):
    engine = Engine.create(tmp_path / "game.sqlite", campaign_world(), seed=941)
    try:
        for estimate in (1, True, 9):
            with engine._transaction():
                npc_state.receive_report(engine._world.npc_state, "a:ruler", "past", {"estimate": estimate}, engine._world.date, "observer")
        history = engine.historical_state(kind="report", owner="a:ruler", item="past")
        assert len(history) == 3
        snapshot = engine.save(tmp_path / "save.json")
        raw = json.loads(snapshot.read_text(encoding="utf-8"))["payload"]
        assert raw["schema_version"] == 4 and raw["state_archive"]["version"] == 1
        restored = Engine.load(snapshot, tmp_path / "restored.sqlite")
        try:
            assert restored.historical_state(kind="report", owner="a:ruler", item="past") == history
            assert encode(restored._payload()) == encode(engine._payload())
            engine.advance(2)
            restored.advance(2)
            assert encode(restored._payload()) == encode(engine._payload())
            assert restored.ledger() == engine.ledger()
        finally:
            restored.close()
    finally:
        engine.close()


@pytest.mark.parametrize("change", ["version", "checksum", "reference", "duplicate"])
def test_historical_archive_validation_rejects_corruption(database, change):
    state_storage.compact(database, logical())
    archive = state_storage.export_archive(database)
    if change == "version": archive["version"] = 2
    elif change == "checksum": archive["objects"][next(iter(archive["objects"]))]["rewritten"] = True
    elif change == "reference": archive["catalog"][0]["digest"] = "0" * 64
    else: archive["catalog"].append(copy.deepcopy(archive["catalog"][0]))
    with pytest.raises(ValueError): state_storage.validate_archive(archive)
