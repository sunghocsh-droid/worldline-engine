"""D&I foundation preserves explicit activation and real SQLite contracts."""
import copy
import json
from pathlib import Path
import sqlite3

import pytest

from worldline import Engine, Order, Report, World
from worldline.audit import AuditError
from worldline.engine import encode
from worldline import guardrails, npc_state, order_execution, state_storage
from test_crisis_integration import make, rewrite


PREFIX = [{"name": name, "version": "1"} for name in (
    "worldline.projects", "worldline.economy", "worldline.npc",
    "worldline.crisis", "worldline.military")]
MANIFEST = PREFIX + [{"name": name, "version": "1"} for name in (
    "worldline.intelligence", "worldline.diplomacy")]
STATE_FIELDS = ("diplomacy_state", "intelligence_state")
RECEIPT_ID = "diplomacy-intelligence-activation-1"
DEFAULT_CONFIG = {
    "diplomacy": {"proposal_delay_days": 1, "response_delay_days": 1,
                  "player_bindings": {}, "reply_recipients": {}},
    "intelligence": {"delay_days": 1, "sources": {}, "audience": []},
}


def fullstack(path):
    game = make(path)
    game.enable_military()
    return game


@pytest.fixture
def engine(tmp_path):
    game = fullstack(tmp_path / "foundation.sqlite")
    yield game
    game.close()


@pytest.fixture
def enabled(engine):
    engine.enable_diplomacy_intelligence()
    return engine


def sql_rows(game):
    """Include physical archive bytes and component pointers in atomicity checks."""
    tables = ("current_state", "ledger", "state_components", "state_archive",
              "archive_catalog", "state_heads")
    return {table: game.db.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in tables}


def committed(game):
    return {
        "stored": encode(game._stored_payload()),
        "private": encode(game._payload()),
        "rng": game._rng.getstate(),
        "ledger": game.ledger(),
        "sql": sql_rows(game),
        "systems": copy.deepcopy(game._system_specs),
        "callbacks": game.systems,
        "revision": game._revision,
    }


def assert_clean(game, before):
    assert committed(game) == before
    assert game._journal.frames == []
    assert not game.db.in_transaction


def assert_feature_off(game):
    world = game.true_state
    for field in STATE_FIELDS:
        assert getattr(world, field) is None
        assert field not in world.to_dict()
        assert field not in game._stored_payload()["world"]
    assert not any(spec["name"] in {"worldline.intelligence", "worldline.diplomacy"}
                   for spec in game._system_specs)


def test_fullstack_creation_and_reopen_do_not_activate(engine):
    assert engine.true_state.schema_version == 5
    assert engine._system_specs == PREFIX
    assert_feature_off(engine)
    reopened = Engine(engine.path)
    try:
        assert_feature_off(reopened)
        assert reopened._stored_payload() == engine._stored_payload()
        reopened.advance(2)
        assert_feature_off(reopened)
    finally:
        reopened.close()


@pytest.mark.parametrize("schema", range(1, 7))
def test_actual_legacy_saves_do_not_become_schema_seven(tmp_path, schema):
    game = None
    if schema == 1:
        save = Path(__file__).parents[1] / "examples" / "initial.json"
        raw = json.loads(save.read_text(encoding="utf-8"))
        expected_schema = 2  # Existing v1 normalization remains unchanged.
    else:
        game = fullstack(tmp_path / "old.sqlite") if schema == 6 else make(
            tmp_path / "old.sqlite", {2: 0, 3: 2, 4: 3, 5: 4}[schema])
        save = game.save(tmp_path / "old.json")
        raw = json.loads(save.read_text(encoding="utf-8"))
        expected_schema = schema
    try:
        assert raw["payload"]["schema_version"] == schema
        loaded = Engine.load(save, tmp_path / "loaded.sqlite")
        try:
            assert_feature_off(loaded)
            if game is not None:
                assert loaded.true_state.to_dict() == game.true_state.to_dict()
                assert loaded.rng.getstate() == game.rng.getstate()
                assert loaded.ledger() == game.ledger()
            loaded.advance(2)
            assert_feature_off(loaded)
            resumed = loaded.save(tmp_path / "resumed.json")
            payload = json.loads(resumed.read_text(encoding="utf-8"))["payload"]
            assert payload["schema_version"] == expected_schema
            assert not set(STATE_FIELDS).intersection(payload["engine"]["world"])
        finally:
            loaded.close()
    finally:
        if game is not None:
            game.close()


@pytest.mark.parametrize("stage", range(5))
def test_legacy_and_light_activation_is_rejected_atomically(tmp_path, stage):
    game = make(tmp_path / "light.sqlite", stage)
    try:
        before = committed(game)
        with pytest.raises(ValueError):
            game.enable_diplomacy_intelligence()
        assert_clean(game, before)
        assert_feature_off(game)
    finally:
        game.close()


def test_custom_saved_system_is_not_an_activation_prefix(engine):
    def custom(_world, _tick, _rng):
        return []
    with engine._transaction():
        engine._required_systems.append({"name": "tests.custom", "version": "1"})
        engine.register_system(custom, name="tests.custom", version="1")
    before = committed(engine)
    with pytest.raises(ValueError):
        engine.enable_diplomacy_intelligence()
    assert_clean(engine, before)
    assert_feature_off(engine)


def test_repeat_activation_is_rejected_atomically(enabled):
    before = committed(enabled)
    with pytest.raises(ValueError):
        enabled.enable_diplomacy_intelligence()
    assert_clean(enabled, before)


def test_activation_is_explicit_empty_and_does_not_draw_rng(engine):
    before = engine.true_state.to_dict()
    rng = engine.rng.getstate()
    npc = copy.deepcopy(engine.true_state.npc_state)
    ledger = engine.ledger()
    result = engine.enable_diplomacy_intelligence()
    assert result == {"version": 1, "save_schema_version": 7, "receipt_id": RECEIPT_ID}
    world = engine.true_state
    assert world.schema_version == 6
    receipt = {"id": RECEIPT_ID, "kind": "activation", "date": world.date,
               "version": 1, "config": DEFAULT_CONFIG}
    assert world.diplomacy_state == {
        "version": 1, "config": DEFAULT_CONFIG["diplomacy"],
        "proposals": {}, "dispatches": {}, "receipts": {RECEIPT_ID: receipt},
        "outbox": [], "next_ids": {"proposal": 1, "dispatch": 1, "receipt": 2, "sequence": 1},
    }
    assert world.intelligence_state == {
        "version": 1, "config": DEFAULT_CONFIG["intelligence"],
        "observations": {}, "dispatches": {}, "receipts": {}, "outbox": [],
        "next_ids": {"observation": 1, "dispatch": 1, "receipt": 1, "sequence": 1},
    }
    unchanged = world.to_dict()
    for field in STATE_FIELDS:
        unchanged.pop(field)
    unchanged["schema_version"] = 5
    assert unchanged == before
    assert engine.rng.getstate() == rng
    assert world.npc_state == npc
    assert engine._system_specs == engine._stored_payload()["systems"] == MANIFEST
    assert engine.ledger()[:-1] == ledger
    assert engine.ledger()[-1]["kind"] == "diplomacy_intelligence_enabled"
    assert engine.ledger()[-1]["payload"] == {"receipt": receipt, "systems": MANIFEST}
    result["receipt_id"] = "caller-change"
    assert RECEIPT_ID in engine.true_state.diplomacy_state["receipts"]


def test_partial_config_is_normalized_detached_and_saved(engine, tmp_path):
    config = {"diplomacy": {"proposal_delay_days": 2, "response_delay_days": 3,
        "player_bindings": {"player": {"country_id": "a", "representative_person_id": "a:ruler"}},
        "reply_recipients": {"player": [{"kind": "person", "id": "a:ruler"},
                                        {"kind": "player", "id": "player"}]}},
        "intelligence": {"delay_days": 4,
                         "audience": [{"kind": "person", "id": "b:ruler"},
                                      {"kind": "player", "id": "player"}]}}
    expected = copy.deepcopy(DEFAULT_CONFIG)
    expected["diplomacy"].update(copy.deepcopy(config["diplomacy"]))
    expected["intelligence"].update(copy.deepcopy(config["intelligence"]))
    engine.enable_diplomacy_intelligence(config)
    config["diplomacy"]["player_bindings"]["player"]["country_id"] = "b"
    world = engine.true_state
    assert world.diplomacy_state["config"] == expected["diplomacy"]
    assert world.intelligence_state["config"] == expected["intelligence"]
    assert world.diplomacy_state["receipts"][RECEIPT_ID]["config"] == expected
    save = engine.save(tmp_path / "custom-config.json")
    loaded = Engine.load(save, tmp_path / "custom-config.sqlite")
    try:
        assert loaded.true_state.to_dict() == world.to_dict()
    finally:
        loaded.close()


@pytest.mark.parametrize("config", [
    [], True, {"unknown": {}}, {"diplomacy": []}, {"intelligence": None},
    {"diplomacy": {"unknown": 1}}, {"intelligence": {"unknown": 1}},
    {"diplomacy": {"proposal_delay_days": True}},
    {"diplomacy": {"response_delay_days": -1}},
    {"intelligence": {"delay_days": 1.5}},
    {"intelligence": {"sources": {"invented": {}}}},
    {"intelligence": {"audience": [{"kind": "person", "id": "missing"}]}},
    {"intelligence": {"audience": [{"kind": "country", "country_id": "a"}]}},
    {"intelligence": {"audience": [{"kind": "player", "id": "unbound"}]}},
    {"intelligence": {"audience": [{"kind": "person", "person_id": "a:ruler"}]}},
    {"diplomacy": {"player_bindings": {"player": {"country_id": "missing", "representative_person_id": "a:ruler"}}}},
    {"diplomacy": {"player_bindings": {"player": {"country_id": "a", "representative_person_id": "b:ruler"}}}},
    {"diplomacy": {"player_bindings": {"player": {"country_id": "b", "representative_person_id": "b:ruler"}}}},
])
def test_invalid_config_and_unauthorized_player_binding_are_atomic(engine, config):
    before = committed(engine)
    with pytest.raises(ValueError):
        engine.enable_diplomacy_intelligence(config)
    assert_clean(engine, before)
    assert_feature_off(engine)


@pytest.mark.parametrize("mode", ["runtime", "verification"])
def test_enabled_skeleton_save_load_and_next_ticks_are_identical(enabled, tmp_path, mode):
    enabled.validation_mode = mode
    enabled.advance(2)
    save = enabled.save(tmp_path / "enabled.json")
    raw = json.loads(save.read_text(encoding="utf-8"))["payload"]
    assert raw["schema_version"] == 7
    assert raw["engine"]["world"]["schema_version"] == 6
    assert raw["engine"]["systems"] == MANIFEST
    loaded = Engine.load(save, tmp_path / "resumed.sqlite", validation_mode=mode)
    try:
        assert loaded._stored_payload() == enabled._stored_payload()
        assert loaded.ledger() == enabled.ledger()
        assert loaded._system_specs == MANIFEST
        for game in (enabled, loaded):
            game.advance(31)
        assert loaded._stored_payload() == enabled._stored_payload()
        assert loaded.ledger() == enabled.ledger()
        assert state_storage.export_archive(loaded.db) == state_storage.export_archive(enabled.db)
    finally:
        loaded.close()


def test_failed_activation_storage_commit_restores_every_layer(engine):
    before = committed(engine)
    engine.db.execute("CREATE TRIGGER fail_di BEFORE UPDATE ON current_state "
                      "BEGIN SELECT RAISE(ABORT,'D&I storage failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match="D&I storage failure"):
        engine.enable_diplomacy_intelligence()
    assert_clean(engine, before)
    assert_feature_off(engine)
    engine.db.execute("DROP TRIGGER fail_di")
    engine.enable_diplomacy_intelligence()
    assert engine._system_specs == MANIFEST


def test_second_callback_registration_failure_restores_registry_and_all_state(engine, monkeypatch):
    before = committed(engine)
    original = engine.register_system
    def register(callback, **spec):
        if spec.get("name") == "worldline.diplomacy":
            assert engine._system_specs[-1]["name"] == "worldline.intelligence"
            raise RuntimeError("D&I callback registration failure")
        return original(callback, **spec)
    monkeypatch.setattr(engine, "register_system", register)
    with pytest.raises(RuntimeError, match="D&I callback registration failure"):
        engine.enable_diplomacy_intelligence()
    assert_clean(engine, before)
    assert_feature_off(engine)
    monkeypatch.setattr(engine, "register_system", original)
    engine.enable_diplomacy_intelligence()
    assert engine._system_specs == MANIFEST


def test_failed_activation_ledger_insert_rolls_back_opt_in_index_and_all_state(engine):
    index_name = "diplomacy_intelligence_activation_once"
    def index_count():
        return engine.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
                                 (index_name,)).fetchone()[0]
    assert index_count() == 0
    before = committed(engine)
    engine.db.execute("CREATE TRIGGER fail_di_ledger BEFORE INSERT ON ledger "
        "WHEN NEW.kind='diplomacy_intelligence_enabled' "
        "BEGIN SELECT RAISE(ABORT,'D&I activation ledger failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match="D&I activation ledger failure"):
        engine.enable_diplomacy_intelligence()
    assert index_count() == 0
    assert_clean(engine, before)
    assert_feature_off(engine)
    engine.db.execute("DROP TRIGGER fail_di_ledger")
    engine.enable_diplomacy_intelligence()
    assert index_count() == 1
    assert engine._system_specs == MANIFEST
    assert len([item for item in engine.ledger()
                if item["kind"] == "diplomacy_intelligence_enabled"]) == 1


def test_enabled_world_cannot_be_created_without_saved_activation_evidence(enabled, tmp_path):
    destination = tmp_path / "invented.sqlite"
    with pytest.raises(ValueError):
        Engine.create(destination, enabled.true_state, seed=enabled.seed)
    assert not destination.exists()


def test_enabled_reopen_and_refresh_keep_exact_builtin_order(enabled):
    before = enabled._stored_payload()
    reopened = Engine(enabled.path)
    try:
        assert reopened._stored_payload() == before
        assert reopened._system_specs == MANIFEST
        assert len(reopened.systems) == 7
        for _ in range(2):
            reopened.refresh()
            assert reopened._system_specs == MANIFEST
            assert len(reopened.systems) == 7
        reopened.advance(1)
    finally:
        reopened.close()


def test_enabled_sqlite_reopen_rejects_an_empty_ledger(enabled):
    enabled.db.execute("DROP TRIGGER ledger_no_delete")
    enabled.db.execute("DELETE FROM ledger")
    enabled.db.commit()
    assert enabled.ledger() == []
    with pytest.raises(ValueError, match="[Ll]edger"):
        Engine(enabled.path)


def tamper_enabled(payload, case):
    world = payload["engine"]["world"]
    diplomacy = world["diplomacy_state"]
    intelligence = world["intelligence_state"]
    if case == "missing_diplomacy":
        world.pop("diplomacy_state")
    elif case == "missing_intelligence":
        world.pop("intelligence_state")
    elif case == "null_diplomacy":
        world["diplomacy_state"] = None
    elif case == "null_intelligence":
        world["intelligence_state"] = None
    elif case == "outer_six":
        payload["schema_version"] = 6
    elif case == "outer_future":
        payload["schema_version"] = 8
    elif case == "world_five":
        world["schema_version"] = 5
    elif case == "world_future":
        world["schema_version"] = 7
    elif case == "diplomacy_version":
        diplomacy["version"] = 2
    elif case == "intelligence_version":
        intelligence["version"] = 2
    elif case == "bool_version":
        diplomacy["version"] = True
    elif case == "missing_config":
        intelligence.pop("config")
    elif case == "unknown_state_field":
        diplomacy["root_seed"] = 1
    elif case == "missing_counter":
        intelligence["next_ids"].pop("observation")
    elif case == "bool_counter":
        diplomacy["next_ids"]["proposal"] = True
    elif case == "zero_counter":
        intelligence["next_ids"]["receipt"] = 0
    elif case == "activation_counter_reuse":
        diplomacy["next_ids"]["receipt"] = 1
    elif case == "nonempty_proposals":
        diplomacy["proposals"]["invented"] = {"id": "invented"}
    elif case == "nonempty_observations":
        intelligence["observations"]["invented"] = {"id": "invented"}
    elif case == "nonempty_dispatches":
        intelligence["dispatches"]["invented"] = {"id": "invented"}
    elif case == "nonempty_outbox":
        diplomacy["outbox"].append({"id": "invented"})
    elif case == "intelligence_receipt":
        intelligence["receipts"]["invented"] = {"id": "invented"}
    elif case == "missing_receipt":
        diplomacy["receipts"].clear()
    elif case == "changed_receipt":
        diplomacy["receipts"][RECEIPT_ID]["date"] = "1896-12-31"
    elif case == "changed_activation_config":
        diplomacy["receipts"][RECEIPT_ID]["config"]["intelligence"]["delay_days"] = 2
    elif case == "missing_activation_ledger":
        assert payload["ledger"][-1]["kind"] == "diplomacy_intelligence_enabled"
        payload["ledger"].pop()
    elif case == "changed_activation_ledger":
        payload["ledger"][-1]["payload"]["receipt"]["config"]["intelligence"]["delay_days"] = 2
    elif case == "missing_manifest":
        payload["engine"].pop("systems")
    elif case == "suffix_order":
        payload["engine"]["systems"][5:] = reversed(payload["engine"]["systems"][5:])
    elif case == "prefix_order":
        payload["engine"]["systems"][0:2] = reversed(payload["engine"]["systems"][0:2])
    elif case == "system_version":
        payload["engine"]["systems"][5]["version"] = "2"
    elif case == "manifest_extra":
        payload["engine"]["systems"].append({"name": "tests.extra", "version": "1"})
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "missing_diplomacy", "missing_intelligence", "null_diplomacy", "null_intelligence",
    "outer_six", "outer_future", "world_five", "world_future", "diplomacy_version",
    "intelligence_version", "bool_version", "missing_config", "unknown_state_field",
    "missing_counter", "bool_counter", "zero_counter", "activation_counter_reuse", "nonempty_proposals",
    "nonempty_observations", "nonempty_dispatches", "nonempty_outbox", "intelligence_receipt",
    "missing_receipt", "changed_receipt", "changed_activation_config",
    "missing_activation_ledger", "changed_activation_ledger", "missing_manifest",
    "suffix_order", "prefix_order", "system_version", "manifest_extra",
])
def test_rehashed_malformed_versions_presence_and_history_are_rejected(enabled, tmp_path, case):
    save = enabled.save(tmp_path / "valid.json")
    bad = tmp_path / "tampered.json"
    rewrite(save, bad, lambda payload: tamper_enabled(payload, case))
    destination = tmp_path / "rejected.sqlite"
    with pytest.raises((ValueError, AuditError)):
        Engine.load(bad, destination)
    assert not destination.exists()


@pytest.mark.parametrize("case", ["outer_seven", "diplomacy_null", "intelligence_null", "both_states"])
def test_feature_off_snapshot_cannot_smuggle_activation_fields(enabled, tmp_path, case):
    # The enabled fixture is the same game: construct a genuine off save separately.
    off = fullstack(tmp_path / "off.sqlite")
    try:
        save = off.save(tmp_path / "off.json")
        def mutation(payload):
            if case == "outer_seven":
                payload["schema_version"] = 7
            elif case == "diplomacy_null":
                payload["engine"]["world"]["diplomacy_state"] = None
            elif case == "intelligence_null":
                payload["engine"]["world"]["intelligence_state"] = None
            else:
                for field in STATE_FIELDS:
                    payload["engine"]["world"][field] = enabled.true_state.to_dict()[field]
        bad = tmp_path / "smuggled.json"
        rewrite(save, bad, mutation)
        destination = tmp_path / "smuggled.sqlite"
        with pytest.raises((ValueError, AuditError)):
            Engine.load(bad, destination)
        assert not destination.exists()
        assert_feature_off(off)
    finally:
        off.close()


def test_live_noop_stages_preserve_existing_world_and_rng(enabled, tmp_path):
    off = fullstack(tmp_path / "without-di.sqlite")
    try:
        diplomacy = copy.deepcopy(enabled.true_state.diplomacy_state)
        intelligence = copy.deepcopy(enabled.true_state.intelligence_state)
        for game in (enabled, off):
            game.advance(31)
        world = enabled.true_state.to_dict()
        for field in STATE_FIELDS:
            world.pop(field)
        world["schema_version"] = 5
        assert world == off.true_state.to_dict()
        assert enabled.rng.getstate() == off.rng.getstate()
        assert enabled.true_state.diplomacy_state == diplomacy
        assert enabled.true_state.intelligence_state == intelligence
        actual = enabled.ledger()
        activation = next(item for item in actual if item["kind"] == "diplomacy_intelligence_enabled")
        actual = [item for item in actual if item is not activation]
        for item in actual:
            if item["id"] > activation["id"]:
                item["id"] -= 1
        assert actual == off.ledger()
    finally:
        off.close()


def test_enabled_economic_scratch_candidate_is_valid_detached_and_existing_orders_continue(enabled):
    before = committed(enabled)
    candidate = order_execution.economic_candidate(enabled._world)
    raw = candidate.to_dict()
    assert candidate.schema_version == raw["schema_version"] == 2
    assert candidate.npc_state is candidate.crisis_state is candidate.military_state is None
    assert candidate.diplomacy_state is candidate.intelligence_state is None
    assert not set(STATE_FIELDS).intersection(raw)
    assert World.from_dict(raw).to_dict() == raw
    quote = guardrails.estimated_fiscal_risk(candidate, "a", 10)
    assert "normal_financial_feasibility" in quote
    assert "expected_consequences" in quote
    assert enabled.policy_constraints("a", 10) == quote
    candidate.countries["a"].treasury += 10
    candidate.economy["countries"]["a"]["policy"]["education"] += 1
    assert_clean(enabled, before)
    old_world = enabled.true_state
    spending = old_world.economy["countries"]["a"]["policy"]["education"] + 1
    result = enabled.submit(Order("continued-economic-policy", "a", "economic_policy",
                                  {"education": spending}))
    assert result["status"] == "accepted"
    live = enabled.true_state
    assert live.schema_version == 6
    assert live.economy["countries"]["a"]["policy"]["education"] == spending
    assert live.diplomacy_state == old_world.diplomacy_state
    assert live.intelligence_state == old_world.intelligence_state
    assert enabled._system_specs == MANIFEST


def test_activation_receipt_has_a_detached_immutable_archive(enabled, tmp_path):
    receipt = enabled.true_state.diplomacy_state["receipts"][RECEIPT_ID]
    row = enabled.db.execute("SELECT owner,item,country,region,day,active,parent "
        "FROM state_heads WHERE kind=?", ("diplomacy_receipts",)).fetchall()
    assert row == [("", RECEIPT_ID, None, None, receipt["date"], None, None)]
    history = enabled.historical_state(kind="diplomacy_receipts", item=RECEIPT_ID)
    assert len(history) == 1
    assert history[0]["value"] == receipt
    history[0]["value"]["config"]["intelligence"]["delay_days"] = 999
    assert enabled.historical_state(kind="diplomacy_receipts", item=RECEIPT_ID)[0]["value"] == receipt
    assert enabled.historical_state(kind="intelligence_receipts") == []
    save = enabled.save(tmp_path / "receipt.json")
    payload = json.loads(save.read_text(encoding="utf-8"))["payload"]
    archive = payload["state_archive"]
    matching = [item for item in archive["catalog"] if item["kind"] == "diplomacy_receipts"]
    assert len(matching) == 1
    assert archive["objects"][matching[0]["digest"]] == receipt


@pytest.mark.parametrize("case", ["logical_body", "owner", "item", "missing_catalog"])
def test_archived_activation_identity_and_body_cannot_disagree_with_logical_state(enabled, tmp_path, case):
    save = enabled.save(tmp_path / "archive-valid.json")
    def mutation(payload):
        row = next(item for item in payload["state_archive"]["catalog"]
                   if item["kind"] == "diplomacy_receipts")
        if case == "logical_body":
            # State and ledger agree with each other, but retain the old archive.
            state = payload["engine"]["world"]["diplomacy_state"]
            state["config"]["proposal_delay_days"] = 2
            state["receipts"][RECEIPT_ID]["config"]["diplomacy"]["proposal_delay_days"] = 2
            payload["ledger"][-1]["payload"]["receipt"]["config"]["diplomacy"]["proposal_delay_days"] = 2
        elif case == "missing_catalog":
            payload["state_archive"]["catalog"].remove(row)
        else:
            row[case] = "forged"
    bad = tmp_path / "archive-inconsistent.json"
    rewrite(save, bad, mutation)
    destination = tmp_path / "archive-rejected.sqlite"
    with pytest.raises(ValueError, match="archive"):
        Engine.load(bad, destination)
    assert not destination.exists()


def test_schema_seven_snapshot_requires_the_complete_archive(enabled, tmp_path):
    save = enabled.save(tmp_path / "archive-required.json")
    bad = tmp_path / "archive-removed.json"
    rewrite(save, bad, lambda payload: payload.pop("state_archive"))
    destination = tmp_path / "archive-removed.sqlite"
    with pytest.raises(ValueError, match="archive"):
        Engine.load(bad, destination)
    assert not destination.exists()


def test_enabled_sqlite_reopen_rejects_forged_archive_catalog_metadata(enabled):
    archive_before = enabled.db.execute("SELECT digest,codec,payload,raw_bytes "
                                       "FROM state_archive ORDER BY digest").fetchall()
    enabled.db.execute("DROP TRIGGER catalog_no_update")
    changed = enabled.db.execute("UPDATE archive_catalog SET country=? WHERE kind=? AND item=?",
                                 ("a", "diplomacy_receipts", RECEIPT_ID))
    assert changed.rowcount == 1
    enabled.db.commit()
    assert enabled.db.execute("SELECT digest,codec,payload,raw_bytes "
                              "FROM state_archive ORDER BY digest").fetchall() == archive_before
    with pytest.raises(ValueError, match="archive"):
        Engine(enabled.path)


def test_candidate_failure_restores_state_outbox_receipt_beliefs_metadata_ids_and_rng(enabled):
    before = committed(enabled)
    with pytest.raises(RuntimeError, match="candidate rejected"):
        with enabled._transaction():
            world = enabled._world
            world.diplomacy_state["next_ids"]["proposal"] += 10
            world.intelligence_state["next_ids"]["observation"] += 10
            world.diplomacy_state["config"]["proposal_delay_days"] += 3
            world.diplomacy_state["proposals"]["candidate"] = {"id": "candidate"}
            world.intelligence_state["dispatches"]["candidate"] = {"id": "candidate"}
            world.diplomacy_state["outbox"].append({"id": "candidate"})
            world.intelligence_state["outbox"].append({"id": "candidate"})
            world.diplomacy_state["receipts"][RECEIPT_ID]["config"]["intelligence"]["delay_days"] += 1
            world.intelligence_state["receipts"]["candidate"] = {"id": "candidate"}
            npc_state.receive_report(world.npc_state, "a:ruler", "candidate",
                                     {"known": [1]}, world.date, "candidate report")
            enabled._observed["candidate"] = {"belief": {"data": [1]}}
            enabled._orders["candidate"] = {"id": "candidate"}
            enabled._journeys["candidate"] = {"remaining": 1}
            enabled._rng.random()
            enabled._event("candidate_received", {"id": "candidate"})
            assert enabled.true_state.to_dict() == json.loads(before["stored"])["world"]
            assert enabled.observed == json.loads(before["stored"])["observed"]
            raise RuntimeError("candidate rejected")
    assert_clean(enabled, before)


@pytest.mark.parametrize("mode", ["runtime", "verification"])
def test_failed_component_write_restores_counter_rng_ledger_and_archive(enabled, mode):
    enabled.validation_mode = mode
    before = committed(enabled)
    enabled.db.execute("CREATE TRIGGER fail_di_commit BEFORE UPDATE ON current_state "
                       "BEGIN SELECT RAISE(ABORT,'D&I final commit failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match="D&I final commit failure"):
        with enabled._transaction():
            enabled._world.diplomacy_state["next_ids"]["proposal"] += 2
            enabled._world.intelligence_state["next_ids"]["observation"] += 2
            npc_state.receive_report(enabled._world.npc_state, "a:ruler", "candidate",
                {"known": [1]}, enabled._world.date, "candidate report")
            enabled._rng.random()
            enabled._event("candidate_received", {"id": "candidate"})
    assert_clean(enabled, before)
    enabled.db.execute("DROP TRIGGER fail_di_commit")
    enabled.advance(1)


def test_new_state_public_copies_are_detached_and_private_caches_are_not_saved(enabled, tmp_path):
    before = enabled._stored_payload()
    public = enabled.true_state
    internal_copy = copy.deepcopy(enabled._world)
    for world in (public, internal_copy):
        world.diplomacy_state["config"]["proposal_delay_days"] += 9
        world.intelligence_state["next_ids"]["observation"] += 9
        world.diplomacy_state["receipts"][RECEIPT_ID]["date"] = "1900-01-01"
        assert not hasattr(world, "_tracker")
    assert enabled._stored_payload() == before
    save = enabled.save(tmp_path / "no-caches.json")
    payload = json.loads(save.read_text(encoding="utf-8"))["payload"]
    forbidden = {"_tracker", "_parents", "_version", "_changes", "_active_indexes",
                 "_storage_cache", "_component_cache", "_journal"}
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()
    assert not keys(payload).intersection(forbidden)
    assert payload["engine"] == before


def test_positive_counter_gaps_survive_save_and_cannot_be_reused(enabled, tmp_path):
    rng = enabled.rng.getstate()
    with enabled._transaction():
        enabled._world.diplomacy_state["next_ids"]["proposal"] = 7
        enabled._world.intelligence_state["next_ids"]["observation"] = 9
    assert enabled.rng.getstate() == rng
    before = committed(enabled)
    with pytest.raises(AuditError):
        with enabled._transaction():
            enabled._world.diplomacy_state["next_ids"]["proposal"] = 6
    assert_clean(enabled, before)
    save = enabled.save(tmp_path / "counter-gaps.json")
    loaded = Engine.load(save, tmp_path / "counter-gaps.sqlite")
    try:
        assert loaded.true_state.diplomacy_state["next_ids"]["proposal"] == 7
        assert loaded.true_state.intelligence_state["next_ids"]["observation"] == 9
        loaded.advance(1)
        assert loaded.true_state.intelligence_state["next_ids"]["observation"] == 9
    finally:
        loaded.close()


@pytest.mark.parametrize("mutation", ["receipt", "states"])
def test_authoritative_activation_cannot_be_changed_or_removed(enabled, mutation):
    before = committed(enabled)
    with pytest.raises(AuditError):
        with enabled._transaction():
            if mutation == "receipt":
                state = enabled._world.diplomacy_state
                state["config"]["proposal_delay_days"] += 1
                state["receipts"][RECEIPT_ID]["config"]["diplomacy"]["proposal_delay_days"] += 1
            else:
                enabled._world.diplomacy_state = None
                enabled._world.intelligence_state = None
                enabled._world.schema_version = 5
    assert_clean(enabled, before)


@pytest.mark.parametrize("separate_connection", [False, True])
def test_external_new_state_component_edit_keeps_reads_authoritative_and_requires_refresh(enabled, separate_connection):
    before = enabled.true_state.intelligence_state["next_ids"]
    counters = {**before, "observation": before["observation"] + 4}
    enabled.true_state  # Warm logical expansion and component caches.
    statement = "UPDATE state_components SET payload=? WHERE path=?"
    values = (encode(counters), encode(["world", "intelligence_state", "next_ids"]))
    if separate_connection:
        with sqlite3.connect(enabled.path) as writer:
            assert writer.execute(statement, values).rowcount == 1
    else:
        assert enabled.db.execute(statement, values).rowcount == 1
        enabled.db.commit()
    assert enabled.true_state.intelligence_state["next_ids"] == counters
    snapshot = committed(enabled)
    with pytest.raises(ValueError, match="another writer"):
        enabled.advance(1)
    assert_clean(enabled, snapshot)
    enabled.refresh()
    assert enabled.true_state.intelligence_state["next_ids"] == counters
    assert enabled._system_specs == MANIFEST
    enabled.advance(1)
    assert enabled.true_state.intelligence_state["next_ids"] == counters


@pytest.mark.parametrize("separate_connection", [False, True])
def test_external_writer_is_visible_and_stale_activation_is_rejected(engine, separate_connection):
    engine.true_state  # Warm committed read paths before a foreign edit.
    path = encode(["world", "date"])
    statement = "UPDATE state_components SET payload=? WHERE path=?"
    values = (encode("1897-01-02"), path)
    if separate_connection:
        with sqlite3.connect(engine.path) as writer:
            assert writer.execute(statement, values).rowcount == 1
    else:
        assert engine.db.execute(statement, values).rowcount == 1
        engine.db.commit()
    assert engine.true_state.date == "1897-01-02"
    before = committed(engine)
    with pytest.raises(ValueError, match="another writer"):
        engine.enable_diplomacy_intelligence()
    assert_clean(engine, before)
    assert_feature_off(engine)
