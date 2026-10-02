"""Real SQLite transactions, immutable history and old-save compatibility."""
import copy
import hashlib
import json
import random
import sqlite3

import pytest

from worldline import Engine, World, Country, Region, Army, npc_state
from worldline.engine import encode
from worldline.audit import AuditError


def initial():
    return World("1897-01-01", scenario_id="fictional-crisis-integration",
        countries={c: Country(c.upper(), population=10000, gdp=4000, treasury=500) for c in "ab"},
        regions={"r" + c: Region(c.upper(), c, 10000) for c in "ab"},
        armies={"army-" + c: Army(c, "r" + c, 100) for c in "ab"})


def government():
    state = npc_state.initialize_state()
    for c in "ab":
        npc_state.add_person(state, c + ":ruler", c, c.upper(), role="ruler",
            authority=["economic_policy", "war_preparation", "mobilize", "fiscal_action", "diplomatic_engagement"])
        npc_state.configure_government(state, c, c + ":ruler", [], autonomous=False)
    return state


def make(path, stage=4, players=("a",)):
    e = Engine.create(path, initial(), seed=71)
    if stage >= 1: e.enable_economy()
    if stage >= 2: e.stabilize_economy()
    if stage >= 3: e.enable_npc(government())
    if stage >= 4: e.enable_crisis({"settings": {"auto_enabled": False}, "player_countries": list(players)})
    return e


@pytest.fixture
def engine(tmp_path):
    e = make(tmp_path / "game.sqlite")
    yield e
    e.close()


def rewrite(source, target, mutate):
    raw = json.loads(source.read_text(encoding="utf-8"))
    mutate(raw["payload"])
    raw["sha256"] = hashlib.sha256(encode(raw["payload"]).encode()).hexdigest()
    target.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("stage,schema", [(0, 2), (1, 2), (2, 3), (3, 4)])
def test_old_world_rules_remain_opt_in_and_load_exactly(tmp_path, stage, schema):
    e = make(tmp_path / "old.sqlite", stage)
    try:
        snapshot = e.save(tmp_path / "old.json")
        assert json.loads(snapshot.read_text(encoding="utf-8"))["payload"]["schema_version"] == schema
        loaded = Engine.load(snapshot, tmp_path / "loaded.sqlite")
        try:
            assert loaded.true_state.to_dict() == e.true_state.to_dict()
            assert loaded.true_state.crisis_state is None
            assert loaded.rng.getstate() == e.rng.getstate()
            assert loaded.ledger() == e.ledger()
            e.advance(1); loaded.advance(1)
            assert loaded.true_state.to_dict() == e.true_state.to_dict()
        finally: loaded.close()
    finally: e.close()


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_crisis_activation_rejects_missing_prerequisites_without_rng_draw(tmp_path, stage):
    e = make(tmp_path / "game.sqlite", stage)
    try:
        before, rng, ledger = e.true_state.to_dict(), e.rng.getstate(), e.ledger()
        with pytest.raises(ValueError): e.enable_crisis()
        assert e.true_state.to_dict() == before and e.rng.getstate() == rng and e.ledger() == ledger
    finally: e.close()


def test_activation_uses_one_saved_draw_and_player_government_remains_player(tmp_path):
    e = make(tmp_path / "game.sqlite", 3)
    try:
        expected = random.Random(); expected.setstate(e.rng.getstate())
        root_seed = expected.getrandbits(128)
        result = e.enable_crisis({"player_countries": ["a"]})
        assert result == {"version": 1, "save_schema_version": 5}
        assert e.rng.getstate() == expected.getstate()
        assert e.true_state.crisis_state["root_seed"] == root_seed
        assert e.true_state.schema_version == 4
        assert not e.true_state.npc_state["governments"]["a"]["autonomous"]
        before = e.true_state.to_dict(), e.rng.getstate()
        with pytest.raises(ValueError): e.enable_crisis()
        assert (e.true_state.to_dict(), e.rng.getstate()) == before
    finally: e.close()


def test_save_load_preserves_active_event_queues_memories_pending_and_next_result(engine, tmp_path):
    event = engine.inject_event("flood", "ra", ["a"], severity=.7, actual_facts={"water": "river"})
    engine.send_event_report(event["event_id"], "a:ruler", delay=1, exaggeration=1.3)
    engine.advance(5)
    assert engine.true_state.crisis_state["events"][event["event_id"]]["ongoing"]
    assert engine.pending_decisions("a")
    assert engine.true_state.npc_state["memories"]["a:ruler"]
    saved = engine.save(tmp_path / "active.json")
    raw = json.loads(saved.read_text(encoding="utf-8"))
    assert raw["payload"]["schema_version"] == 5
    loaded = Engine.load(saved, tmp_path / "restored.sqlite")
    try:
        assert loaded.true_state.to_dict() == engine.true_state.to_dict()
        assert loaded.rng.getstate() == engine.rng.getstate()
        for e in (engine, loaded): e.advance(40)
        assert loaded.true_state.to_dict() == engine.true_state.to_dict()
        assert loaded.rng.getstate() == engine.rng.getstate()
        assert loaded.ledger() == engine.ledger()
    finally: loaded.close()


@pytest.mark.parametrize("mutation", ["missing", "order", "version", "outer", "world"])
def test_version_and_execution_order_fail_explicitly(engine, tmp_path, mutation):
    saved = engine.save(tmp_path / "valid.json")
    def change(payload):
        if mutation == "missing": payload["engine"].pop("systems")
        elif mutation == "order": payload["engine"]["systems"][2:4] = reversed(payload["engine"]["systems"][2:4])
        elif mutation == "version": payload["engine"]["systems"][3]["version"] = "999"
        elif mutation == "outer": payload["schema_version"] = 4
        else: payload["engine"]["world"]["schema_version"] = 3
    rewrite(saved, tmp_path / "invalid.json", change)
    with pytest.raises((ValueError, AuditError)): Engine.load(tmp_path / "invalid.json", tmp_path / "bad.sqlite")
    assert not (tmp_path / "bad.sqlite").exists()


def test_systems_are_sequential_and_external_failure_rolls_back_all_layers(engine):
    assert [s["name"] for s in engine._system_specs] == ["worldline.projects", "worldline.economy", "worldline.npc", "worldline.crisis"]
    engine.inject_event("flood", "ra", ["a"], severity=.8)
    def late_failure(world, tick, rng):
        assert any(e["applied"] for e in world.crisis_state["effects"].values())
        rng.random()
        raise ValueError("late subsystem failed")
    # A new system is explicitly added to the committed manifest for this test.
    with engine._transaction():
        engine._required_systems.append({"name": "test.late", "version": "1"})
        engine.register_system(late_failure, name="test.late", version="1")
    before, rng, ledger = engine.true_state.to_dict(), engine.rng.getstate(), engine.ledger()
    with pytest.raises(ValueError, match="late subsystem"): engine.advance(1)
    assert engine.true_state.to_dict() == before
    assert engine.rng.getstate() == rng and engine.ledger() == ledger


@pytest.mark.parametrize("mutation", ["lost_event", "facts", "receipt", "queue", "duplicate"])
def test_rehashed_but_inconsistent_crisis_history_is_rejected(engine, tmp_path, mutation):
    event = engine.inject_event("flood", "ra", ["a"], severity=.6)
    engine.advance(1)
    saved = engine.save(tmp_path / "valid.json")
    def change(payload):
        state = payload["engine"]["world"]["crisis_state"]
        if mutation == "lost_event": state["events"].pop(event["event_id"])
        elif mutation == "facts": state["events"][event["event_id"]]["actual_facts"]["invented"] = True
        elif mutation == "receipt":
            effect = next(v for v in state["effects"].values() if v["applied"])
            effect["receipt"]["invented"] = True
        elif mutation == "queue":
            effect = next(v for v in state["effects"].values() if v["kind"] == "population_loss")
            effect["payload"]["count"] += 1
        else:
            log = copy.deepcopy(next(v for v in payload["ledger"] if v["payload"].get("kind") == "event_started"))
            log.update(id=len(payload["ledger"]) + 1, day=payload["engine"]["world"]["date"])
            payload["ledger"].append(log)
    rewrite(saved, tmp_path / "invalid.json", change)
    with pytest.raises((ValueError, AuditError)): Engine.load(tmp_path / "invalid.json", tmp_path / "bad.sqlite")


def test_sqlite_refuses_duplicate_crisis_creation_even_before_save(engine):
    engine.inject_event("flood", "ra", ["a"])
    record = next(v["payload"] for v in engine.ledger() if v["payload"].get("kind") == "event_started")
    before = engine.ledger()
    with pytest.raises(sqlite3.IntegrityError):
        with engine._transaction(): engine._event("system_event", record)
    assert engine.ledger() == before


def test_state_and_event_query_results_cannot_modify_committed_truth(engine):
    event = engine.inject_event("flood", "ra", ["a"])
    event["actual_facts"]["invented"] = True
    state = engine.true_state
    state.crisis_state["events"].clear()
    assert "invented" not in engine.true_state.crisis_state["events"][event["event_id"]]["actual_facts"]
    assert not engine.pending_decisions("a")  # No report has arrived yet.


def test_unknown_location_rolls_back_event_ids_and_ledger(engine):
    before = engine.true_state.to_dict(), engine.ledger(), engine.rng.getstate()
    with pytest.raises((ValueError, KeyError)): engine.inject_event("flood", "missing", ["a"])
    assert (engine.true_state.to_dict(), engine.ledger(), engine.rng.getstate()) == before


def test_opening_sqlite_detects_state_rewrite_against_immutable_creation(engine):
    event = engine.inject_event("flood", "ra", ["a"])
    payload = engine._stored_payload()
    payload["world"]["crisis_state"]["events"][event["event_id"]]["actual_facts"]["rewritten"] = True
    with engine.db:
        engine.db.execute("UPDATE current_state SET payload=? WHERE id=1", (encode(payload),))
    with pytest.raises(ValueError, match="immutable ledger"): Engine(engine.path)


def test_measured_response_history_cannot_disappear_on_restore(engine, tmp_path):
    event = engine.inject_event("flood", "ra", ["a"], severity=.8)
    engine.send_event_report(event["event_id"], "a:ruler", delay=0)
    engine.advance(1)
    assert engine.respond_to_event(event["event_id"], "a", "relief")["status"] == "accepted"
    engine.advance(40)
    assert engine.true_state.crisis_state["events"][event["event_id"]]["measured_response_outcomes"]
    saved = engine.save(tmp_path / "actual.json")
    rewrite(saved, tmp_path / "missing.json", lambda p:
        p["engine"]["world"]["crisis_state"]["events"][event["event_id"]].update(measured_response_outcomes={}))
    with pytest.raises(ValueError, match="measured outcomes"):
        Engine.load(tmp_path / "missing.json", tmp_path / "bad.sqlite")
