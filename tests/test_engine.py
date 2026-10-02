import copy
import json
import sqlite3
from pathlib import Path
import pytest
from worldline import Engine, Order, World, Report
from worldline.audit import AuditError
from worldline.models import RISKS


def test_calendar_and_callbacks(engine):
    seen = []
    engine.register_system(lambda w, tick, rng: seen.append(tick["date"]))
    assert engine.advance(1, "month") == "1897-02-28"
    assert len(seen) == 28
    assert engine.advance(1, "week") == "1897-03-07"
    assert engine.advance() == "1897-03-08"


def test_leap_year(tmp_path):
    e = Engine.create(tmp_path / "leap.db", World("1904-01-31"))
    assert e.advance(1, "month") == "1904-02-29"
    e.close()


def test_ledger_is_immutable(engine):
    for sql in ("DELETE FROM ledger", "UPDATE ledger SET kind='lie'"):
        with pytest.raises(sqlite3.IntegrityError):
            engine.db.execute(sql)
        engine.db.rollback()
    assert engine.ledger()[0]["kind"] == "game_created"


def test_order_cost_capacity_and_id(engine):
    result = engine.submit(Order("p1", "a", "produce", {"resource": "grain", "quantity": 8}))
    assert result["cost"] == 16
    assert engine.true_state.countries["a"].treasury == 984
    assert engine.true_state.countries["a"].resources["grain"] == 8
    assert engine.submit(Order("p2", "a", "produce", {"resource": "grain", "quantity": 3}))["status"] == "rejected"
    with pytest.raises(ValueError):
        engine.submit(Order("p1", "a", "research", {"technology": "basic"}))
    engine.advance()
    assert engine.submit(Order("p3", "a", "produce", {"resource": "grain", "quantity": 10}))["status"] == "accepted"


def test_research_and_finances(engine):
    assert engine.submit(Order("t1", "a", "research", {"technology": "advanced"}))["status"] == "rejected"
    assert engine.submit(Order("t2", "a", "research", {"technology": "basic"}))["status"] == "accepted"
    assert engine.submit(Order("t3", "a", "research", {"technology": "advanced"}))["status"] == "accepted"
    assert engine.submit(Order("t4", "b", "research", {"technology": "basic"}))["status"] == "rejected"


def test_movement_takes_real_time(engine):
    assert engine.submit(Order("m", "a", "move_army", {"army": "army", "destination": "r2"}))["cost"] == 3
    engine.advance(2)
    assert engine.true_state.armies["army"].region == "r1"
    engine.advance()
    assert engine.true_state.armies["army"].region == "r2"
    assert any(e["kind"] == "army_arrived" for e in engine.ledger())


def test_invalid_orders_are_recorded(engine):
    for index, o in enumerate([
        Order("", "missing", "produce", {}), Order("", "a", "invent_gdp", {}),
        Order("", "b", "move_army", {"army": "army", "destination": "r2"}),
        Order("", "a", "move_army", {"army": "army", "destination": "unknown"}),
        Order("", "a", "produce", {"resource": "grain", "quantity": -1}),
        Order("", "a", "produce", {"resource": "grain", "quantity": 1, "cost": 0})]):
        o.id = str(index)
        assert engine.submit(o)["status"] == "rejected"
    assert len(engine.orders) == 6


def test_save_load_reproducibility_and_autosave(engine, tmp_path):
    for risk in RISKS:
        engine.set_pressure("a", risk, 100, ["synthetic test"])
    engine.submit(Order("move", "a", "move_army", {"army": "army", "destination": "r2"}))
    saved = engine.named_snapshot(tmp_path, "before")
    fork = Engine.load(saved, tmp_path / "fork.db")
    engine.advance(365, autosave_dir=tmp_path / "autosaves")
    fork.advance(365)
    assert engine._payload() == fork._payload()
    assert engine.ledger() == fork.ledger()
    assert any(e["kind"] == "risk_signal" for e in engine.ledger())
    assert (tmp_path / "autosaves/autosave.json").exists()
    fork.close()


def test_snapshot_diff_and_safety(engine, tmp_path):
    before = engine.named_snapshot(tmp_path, "before")
    engine.advance()
    after = engine.named_snapshot(tmp_path, "after")
    assert any(c["path"] == "/engine/world/date" for c in Engine.diff(before, after))
    with pytest.raises(FileExistsError):
        engine.named_snapshot(tmp_path, "before")
    with pytest.raises(ValueError):
        engine.named_snapshot(tmp_path, "../escape")
    with pytest.raises(FileExistsError):
        Engine.load(before, engine.path)
    raw = json.loads(after.read_text(encoding="utf-8"))
    raw["payload"]["engine"]["seed"] = 42
    after.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        Engine.load(after, tmp_path / "corrupt.db")


def test_observed_state_is_independent(engine, tmp_path):
    engine.record_observation("player", "a", Report("1897-01-30", "rumor", .2, {"treasury": 9999}))
    assert engine.observed_state("player")["a"]["data"]["treasury"] == 9999
    assert engine.true_state.countries["a"].treasury == 1000
    assert engine.observed_state("other") == {}
    view = engine.true_state
    view.countries["a"].treasury = 0
    assert engine.true_state.countries["a"].treasury == 1000
    snapshot = engine.save(tmp_path / "fog.json")
    restored = Engine.load(snapshot, tmp_path / "fog.db")
    assert restored.observed_state("player") == engine.observed_state("player")
    restored.close()


@pytest.mark.parametrize("case", ["population", "territory", "movement", "finance", "production", "tech", "canon", "npc", "relation"])
def test_audit_framework(world, case):
    from worldline.audit import Auditor
    candidate = copy.deepcopy(world)
    if case == "population": candidate.countries["a"].population *= 2
    if case == "territory": candidate.armies["army"].region = "missing"
    if case == "movement": candidate.armies["army"].region = "r2"
    if case == "finance": candidate.countries["a"].treasury = -1
    if case == "production": candidate.production["a:grain"] = 11
    if case == "tech": candidate.countries["a"].technologies = ["advanced"]
    if case == "canon": candidate.canon["founding"] = "changed"
    if case == "npc": candidate.factions["f"].npc.faction_power = 101
    if case == "relation": candidate.relations["x"] = {"a": "a", "b": "missing"}
    with pytest.raises(AuditError):
        Auditor().check(candidate, world)


def test_transaction_rolls_back_state_rng_and_ledger(engine, tmp_path):
    before = copy.deepcopy(engine._payload())
    ledger = engine.ledger()
    def broken(w, tick, rng):
        rng.random()
        w.countries["a"].treasury = -1
    engine.register_system(broken)
    with pytest.raises(AuditError):
        engine.advance(3)
    assert engine._payload() == before
    assert engine.ledger() == ledger
    reopened = Engine(engine.path)
    assert reopened._payload() == before
    reopened.close()


def test_pressure_limits_and_no_zero_pressure_signals(engine):
    for invalid in (-1, 101, float("nan")):
        with pytest.raises(AuditError): engine.set_pressure("a", "war", invalid)
    engine.set_pressure("a", "war", 0)
    engine.advance(100)
    assert not any(e["kind"] == "risk_signal" for e in engine.ledger())


def test_custom_validator(engine):
    engine.auditor.register(lambda w, old: ["custom prohibition"] if w.date.endswith("02-01") else [])
    with pytest.raises(AuditError): engine.advance()
    assert engine.true_state.date == "1897-01-31"


def test_empty_scenario(tmp_path):
    scenario = Path(__file__).parents[1] / "scenarios/korea_1897.json"
    w = World.from_dict(json.loads(scenario.read_text(encoding="utf-8")))
    assert w.countries == {}
    e = Engine.create(tmp_path / "korea.db", w)
    assert e.advance() == "1897-10-13"
    e.close()


def test_stale_writer_cannot_overwrite(engine):
    other = Engine(engine.path)
    engine.advance()
    with pytest.raises(ValueError, match="another writer"):
        other.advance()
    assert engine.true_state.date == "1897-02-01"
    other.close()
