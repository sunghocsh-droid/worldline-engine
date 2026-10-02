"""Architecture regressions: persistence boundaries, invariants and resume contracts."""
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from worldline import Engine, Order, Report
from worldline.audit import AuditError


def test_save_uses_one_committed_database_view(engine, tmp_path):
    other = Engine(engine.path)
    other.submit(Order("latest", "a", "produce", {"resource": "grain", "quantity": 3}))
    saved = engine.save(tmp_path / "committed.json")
    restored = Engine.load(saved, tmp_path / "restored.db")
    assert restored._payload() == other._payload()
    assert restored.ledger() == other.ledger()
    other.close()
    restored.close()


def test_public_reads_are_current_and_cannot_mutate_storage(engine):
    other = Engine(engine.path)
    other.submit(Order("latest", "a", "produce", {"resource": "grain", "quantity": 3}))
    other.record_observation("p", "a", Report("1897-01-31", "agent", .5, {"gdp": 7}))
    assert engine.true_state.countries["a"].treasury == 994
    view = engine.orders
    view.clear()
    reports = engine.observed_state("p")
    reports["a"]["data"]["gdp"] = 999
    assert "latest" in engine.orders
    assert engine.observed_state("p")["a"]["data"]["gdp"] == 7
    other.close()


def test_pressure_input_is_detached(engine, tmp_path):
    causes = ["initial"]
    engine.set_pressure("a", "war", 20, causes)
    causes.append("outside mutation")
    assert engine._payload()["world"]["pressures"]["a:war"]["causes"] == ["initial"]
    engine.advance()


def test_replace_cannot_rewrite_ledger(engine):
    original = engine.ledger()
    with pytest.raises(sqlite3.IntegrityError):
        engine.db.execute("INSERT OR REPLACE INTO ledger VALUES(1,'1897-01-31','lie','{}')")
    engine.db.rollback()
    assert engine.ledger() == original


def test_open_missing_database_has_no_filesystem_side_effect(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises((ValueError, sqlite3.OperationalError)):
        Engine(path)
    assert not path.exists()


def test_restore_checks_pending_journey(engine, tmp_path):
    engine.submit(Order("move", "a", "move_army", {"army": "army", "destination": "r2"}))
    path = engine.save(tmp_path / "bad.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["engine"]["journeys"]["army"]["remaining"] = -10
    body = json.dumps(raw["payload"], ensure_ascii=False, sort_keys=True, allow_nan=False)
    raw["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        Engine.load(path, tmp_path / "bad.db")
    assert not (tmp_path / "bad.db").exists()


def test_each_system_is_required_after_restore(engine, tmp_path):
    def economic_tick(w, tick, rng):
        w.countries["a"].treasury += rng.randrange(5)
        return [{"system": "economy", "date": tick["date"]}]
    engine.register_system(economic_tick, name="economy", version="1")
    engine.advance(3)
    restored = Engine.load(engine.save(tmp_path / "systems.json"), tmp_path / "systems.db")
    with pytest.raises(ValueError, match="system"):
        restored.advance()
    with pytest.raises(ValueError, match="system"):
        restored.register_system(economic_tick, name="economy", version="2")
    restored.register_system(economic_tick, name="economy", version="1")
    engine.advance(10)
    restored.advance(10)
    assert restored._payload() == engine._payload()
    assert restored.ledger() == engine.ledger()
    restored.close()


def test_daily_systems_cannot_rewrite_clock(engine):
    before = engine._payload()
    engine.register_system(lambda w, tick, rng: setattr(w, "date", "2000-01-01"))
    with pytest.raises(ValueError, match="date|clock"):
        engine.advance()
    assert engine._payload() == before


def test_unknown_production_is_rejected(engine):
    def illegal(w, tick, rng):
        w.production["a:unknown_resource"] = 999
    engine.register_system(illegal)
    with pytest.raises(AuditError):
        engine.advance()


def test_fractional_and_noncanonical_dates_are_rejected(engine):
    before = engine._payload()
    with pytest.raises(ValueError):
        engine.record_observation("p", "bad", Report("18970131", "rumor", .5, {}))
    assert engine._payload() == before


def test_order_canon_checks_apply_outside_advance(engine):
    # A new order handler must not bypass the historical canon check.
    def validator(order):
        return 0, lambda: engine._world.canon.update({"founding": "overwritten"})
    engine._validate_order = validator
    with pytest.raises(AuditError):
        engine.submit(Order("bad", "a", "custom", {}))
    assert "bad" not in engine.orders


def test_original_snapshots_still_load(tmp_path):
    for index, name in enumerate(("initial", "month_one", "autosave")):
        path = Path(__file__).parents[1] / "examples" / f"{name}.json"
        e = Engine.load(path, tmp_path / f"legacy{index}.sqlite")
        assert e.true_state.scenario_id == "korea_1897"
        e.advance()
        e.close()


def test_system_order_chunking_and_rng_resume(world, tmp_path):
    def economy(w, tick, rng):
        w.countries["a"].treasury += rng.randrange(3)
        return [{"name": "economy"}]
    def diplomacy(w, tick, rng):
        return [{"name": "diplomacy", "treasury": w.countries["a"].treasury}]
    a = Engine.create(tmp_path / "one.db", world, 17)
    b = Engine.create(tmp_path / "two.db", world, 17)
    for e in (a, b):
        e.register_system(economy, name="economy")
        e.register_system(diplomacy, name="diplomacy")
        e.set_pressure("a", "war", 100)
    a.advance(28)
    for _ in range(28): b.advance()
    assert a._payload() == b._payload()
    assert a.ledger() == b.ledger()
    first_day = [r["payload"]["name"] for r in a.ledger() if r["kind"] == "system_event"][:2]
    assert first_day == ["economy", "diplomacy"]
    a.close()
    b.close()


def test_failure_on_later_day_restores_rng_orders_and_journey(engine):
    engine.submit(Order("m", "a", "move_army", {"army": "army", "destination": "r2"}))
    before, ledger = copy.deepcopy(engine._payload()), engine.ledger()
    def failing(w, tick, rng):
        rng.random()
        if w.date == "1897-02-02": raise RuntimeError("late failure")
    engine.register_system(failing)
    with pytest.raises(RuntimeError): engine.advance(3)
    assert engine._payload() == before
    assert engine.ledger() == ledger


def test_snapshot_read_is_atomic_during_concurrent_commit(engine, tmp_path):
    engine.db.execute("PRAGMA journal_mode=WAL")
    before, history = copy.deepcopy(engine._payload()), engine.ledger()
    writer = Engine(engine.path)
    original_ledger = engine.ledger
    def interleaved_read():
        writer.advance()
        return original_ledger()
    engine.ledger = interleaved_read
    path = engine.save(tmp_path / "atomic.json")
    restored = Engine.load(path, tmp_path / "atomic.db")
    assert restored._payload() == before
    assert restored.ledger() == history
    assert writer.true_state.date != restored.true_state.date
    restored.close()
    writer.close()


def test_open_unrelated_db_does_not_add_game_tables(tmp_path):
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(id INTEGER)")
    with pytest.raises(sqlite3.DatabaseError): Engine(path)
    with sqlite3.connect(path) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"unrelated"}


def test_rng_inspection_cannot_change_committed_draws(engine):
    before = copy.deepcopy(engine._payload())
    engine.rng.random()
    engine.rng.seed(999)
    assert engine._payload() == before


@pytest.mark.parametrize("invalid", ["high", None, True, [], float("inf")])
def test_malformed_pressure_is_rejected_atomically(engine, invalid):
    before = copy.deepcopy(engine._payload())
    with pytest.raises(ValueError): engine.set_pressure("a", "war", invalid)
    assert engine._payload() == before


def test_null_canon_cannot_be_deleted(engine):
    with engine._transaction():
        engine._world.canon["reserved"] = None
    def corrupt(w, tick, rng):
        del w.canon["reserved"]
    engine.register_system(corrupt)
    with pytest.raises(AuditError): engine.advance()


def test_system_event_payload_must_be_an_object(engine):
    before = copy.deepcopy(engine._payload())
    engine.register_system(lambda w, tick, rng: ["invalid event"])
    with pytest.raises(ValueError): engine.advance()
    assert engine._payload() == before


def test_loaded_ledger_guards_remain_active(engine, tmp_path):
    original = engine.ledger()
    restored = Engine.load(engine.save(tmp_path / "ledger.json"), tmp_path / "ledger.db")
    for sql in ("DELETE FROM ledger", "UPDATE ledger SET kind='lie'",
                "INSERT OR REPLACE INTO ledger VALUES(1,'1897-01-31','lie','{}')"):
        with pytest.raises(sqlite3.IntegrityError): restored.db.execute(sql)
        restored.db.rollback()
    assert restored.ledger() == original
    restored.close()


def test_restoring_into_existing_db_preserves_all_history(engine, tmp_path):
    path = engine.save(tmp_path / "same.json")
    before, history = copy.deepcopy(engine._payload()), engine.ledger()
    with pytest.raises(FileExistsError): Engine.load(path, engine.path)
    assert engine._payload() == before
    assert engine.ledger() == history


def test_seed_order_sequence_and_region_distance_boundaries(world, tmp_path):
    a = Engine.create(tmp_path / "a.db", world, 99)
    b = Engine.create(tmp_path / "b.db", world, 99)
    for e in (a, b):
        e.set_pressure("a", "war", 100)
        e.submit(Order("produce", "a", "produce", {"resource": "grain", "quantity": 4}))
        e.submit(Order("research", "a", "research", {"technology": "basic"}))
        e.submit(Order("move", "a", "move_army", {"army": "army", "destination": "r2"}))
        e.advance(60)
    assert a._payload() == b._payload()
    assert a.ledger() == b.ledger()
    a.close()
    b.close()


def test_snapshot_publication_failure_keeps_existing_backup(engine, tmp_path, monkeypatch):
    import os
    path = engine.save(tmp_path / "autosave.json")
    before = path.read_bytes()
    def failed_replace(*args):
        raise OSError("simulated storage failure")
    monkeypatch.setattr(os, "replace", failed_replace)
    with pytest.raises(OSError): engine.advance(1, autosave_dir=tmp_path)
    assert engine.true_state.date == "1897-02-01"
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_named_snapshot_failure_never_publishes_partial_file(engine, tmp_path, monkeypatch):
    import os
    def failed_link(*args):
        raise OSError("simulated publication failure")
    monkeypatch.setattr(os, "link", failed_link)
    with pytest.raises(OSError): engine.save(tmp_path / "named.json")
    assert not (tmp_path / "named.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_empty_system_manifest_and_v1_defaults_are_compatible(engine, tmp_path):
    path = engine.save(tmp_path / "defaults.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["engine"]["systems"] = []
    del raw["payload"]["engine"]["world"]["production"]
    body = json.dumps(raw["payload"], ensure_ascii=False, sort_keys=True, allow_nan=False)
    raw["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = Engine.load(path, tmp_path / "defaults.db")
    restored.advance()
    assert restored.true_state.date == "1897-02-01"
    restored.close()


def test_registered_systems_cannot_be_removed_via_public_view(engine):
    engine.register_system(lambda w, tick, rng: [], name="retained")
    engine.advance()
    systems = list(engine.systems)
    systems.clear()
    assert len(engine.systems) == 1
    engine.advance()
