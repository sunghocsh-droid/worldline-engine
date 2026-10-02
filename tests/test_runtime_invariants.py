"""Runtime journaling and sparse audits retain the full-audit contract.

These cases exercise committed observations, immutable receipts, causal cross
references, and failure after SQLite archive writes rather than timing code.
"""
import copy
import json
import sqlite3

import pytest

from test_crisis_integration import make
from worldline import Engine, Report, state_storage
from worldline.audit import AuditError
from worldline.engine import encode
from worldline.state_runtime import Journal, version


def military_game(path, mode="runtime"):
    game = make(path)
    game.validation_mode = mode
    game.enable_military({
        "initial_equipment": {unit: {"personal_weapons": {
            "quantity": 100, "condition": 1.}} for unit in ("army-a", "army-b")},
        "initial_supply": {unit: {"food": 100., "ammunition": 500., "medical": 100.}
                           for unit in ("army-a", "army-b")},
    })
    return game


@pytest.fixture(params=("runtime", "verification"))
def military_engine(tmp_path, request):
    game = military_game(tmp_path / "military.sqlite", request.param)
    yield game
    game.close()


def result(identifier="journal-loss"):
    return {"id": identifier, "actor": "a", "region": "ra", "operation_id": None,
            "casualties": {"army-a": {"battle_casualties": 3}},
            "equipment_losses": {}, "supply_consumption": {"army-a": {"food": 2.}},
            "unit_changes": {}}


def committed(game):
    return encode(game._stored_payload()), game.rng.getstate(), game.ledger()


def physical(game):
    return {table: game.db.execute("SELECT * FROM " + table + " ORDER BY 1,2").fetchall()
            for table in ("current_state", "state_archive", "archive_catalog", "state_heads", "state_components")}


def test_same_connection_pending_sql_component_changes_use_database_truth_then_rollback(military_engine):
    game = military_engine
    before = committed(game)
    row_text = game.db.execute("SELECT payload FROM current_state WHERE id=1").fetchone()[0]
    component_path = encode(["world", "canon"])
    original = game.db.execute("SELECT payload FROM state_components WHERE path=?",
                               (component_path,)).fetchone()[0]
    changed = json.loads(original)
    changed["sql-component-probe"] = {"nested": ["uncommitted SQL source of truth"]}
    game.db.execute("BEGIN")
    try:
        game.db.execute("UPDATE state_components SET payload=? WHERE path=?",
                        (encode(changed), component_path))
        assert not hasattr(game, "_transaction_frame")
        assert game.db.in_transaction
        assert game.db.total_changes != game._committed_changes
        assert not game._database_unchanged(row_text)
        assert game._stored_payload()["world"]["canon"] == changed
        detached = game.true_state
        assert detached.canon == changed
        detached.canon["sql-component-probe"]["nested"].append("inspection mutation")
        assert game.true_state.canon == changed
        assert encode(game._world.canon) == original
    finally:
        game.db.rollback()
    assert committed(game) == before
    assert game.db.execute("SELECT payload FROM state_components WHERE path=?",
                           (component_path,)).fetchone()[0] == original


def test_committed_public_views_cannot_observe_candidate_or_mutate_private_state(military_engine):
    game = military_engine
    game.record_observation("player", "a", Report(game.true_state.date, "rumor", .3,
                                                {"enemy_strength": "unknown"}))
    before = committed(game)
    fog = game.observed_state("player")
    with pytest.raises(RuntimeError, match="candidate rejected"):
        with game._transaction():
            game._world.military_state["formations"]["army-a"]["supply_state"]["stocks"]["food"] -= 1
            game._observed["player"]["a"]["data"]["enemy_strength"] = 99999
            game._rng.random()
            assert committed(game) == before
            assert game.observed_state("player") == fog
            detached = game.true_state
            detached.military_state["formations"]["army-b"]["manpower"] = 99999
            detached.npc_state["persons"].clear()
            assert game._world.military_state["formations"]["army-b"]["manpower"] == 100
            assert game._world.npc_state["persons"]
            raise RuntimeError("candidate rejected")
    assert committed(game) == before
    assert game.observed_state("player") == fog


def test_same_callback_aliases_survive_barrier_and_next_transaction_rolls_back(military_engine):
    game = military_engine
    fresh = {"nested": [{"trace": [1]}]}
    with game._transaction():
        game._world.canon["alias-one"] = fresh
        game._world.canon["alias-two"] = fresh
        fresh["nested"][0]["trace"].extend([2, 3])
        assert game._world.canon["alias-one"] is fresh
        assert game._world.canon["alias-two"]["nested"][0]["trace"] == [1, 2, 3]
    assert game._world.canon["alias-one"] is game._world.canon["alias-two"]
    before = committed(game)
    with pytest.raises(RuntimeError):
        with game._transaction():
            game._world.canon["alias-one"]["nested"][0]["trace"] *= 2
            game._world.canon["alias-two"]["nested"].reverse()
            raise RuntimeError("undo nested aliases")
    assert committed(game) == before
    assert game._world.canon["alias-one"] is game._world.canon["alias-two"]


def test_journal_dirty_propagation_does_not_include_unchanged_history():
    journal = Journal({"hot": {"stock": 10}, "history": [{"reason_trace": ["keep", {"cost": 7}]}]})
    history = journal.root["history"]
    token = version(history)
    frame = journal.begin()
    journal.root["hot"]["stock"] -= 1
    assert id(history) not in frame.states
    assert version(history) == token
    assert journal.view(journal.root, frame)["hot"]["stock"] == 10
    journal.end(frame, rollback=True)
    assert journal.root["hot"]["stock"] == 10
    assert journal.root["history"] is history
    assert history[0]["reason_trace"] == ["keep", {"cost": 7}]


def test_failure_after_archive_writes_rolls_back_then_retry_consumes_once(military_engine, monkeypatch):
    game = military_engine
    before, rows = committed(game), physical(game)
    original = state_storage.compact
    def fail_after_archive(db, payload, cache=None):
        original(db, payload, cache)
        assert db.execute("SELECT count(*) FROM state_archive").fetchone()[0] > len(rows["state_archive"])
        raise RuntimeError("post-archive failure")
    with monkeypatch.context() as patch:
        patch.setattr(state_storage, "compact", fail_after_archive)
        with pytest.raises(RuntimeError, match="post-archive failure"):
            game._military_call("military_bridge", "apply_result", result())
    assert committed(game) == before
    assert physical(game) == rows
    assert not game._storage_cache
    game._military_call("military_bridge", "apply_result", result())
    after = game.true_state
    assert after.armies["army-a"].personnel == 97
    assert after.military_state["formations"]["army-a"]["supply_state"]["stocks"]["food"] == 98.
    assert after.economy["regions"]["ra"]["pending_losses"]["war_losses"] == 3
    game._military_call("military_bridge", "apply_result", result())
    assert game.true_state.to_dict() == after.to_dict()
    assert len([entry for entry in game.ledger() if entry["kind"] == "system_event"
                and entry["payload"].get("kind") == "effect_applied"
                and entry["payload"].get("effect_id") in
                after.crisis_state["events"][after.military_state["loss_receipts"]["journal-loss"]["event_id"]]["immediate_consequences"]]) == 2


def test_hot_only_commit_reuses_archive_rows_and_reopen_matches(military_engine):
    game = military_engine
    game._military_call("military_bridge", "apply_result", result())
    before = physical(game)
    sql = []
    game.db.set_trace_callback(sql.append)
    try:
        with game._transaction():
            game._world.countries["a"].resources["synthetic-grain"] = 7
    finally:
        game.db.set_trace_callback(None)
    after = physical(game)
    assert after["state_archive"] == before["state_archive"]
    assert after["archive_catalog"] == before["archive_catalog"]
    assert after["state_heads"] == before["state_heads"]
    changed_components = {path for path, value in after["state_components"]
                          if dict(before["state_components"]).get(path) != value}
    assert changed_components == {encode(["world", "countries"])}
    assert not any("INSERT OR IGNORE INTO state_archive" in statement for statement in sql)
    physical_payload = json.loads(game.db.execute("SELECT payload FROM current_state").fetchone()[0])
    restored = state_storage.read(game.db, encode(physical_payload))
    assert encode(restored) == encode(game._stored_payload())
    reopened = Engine(game.path, validation_mode=game.validation_mode)
    try:
        assert committed(reopened) == committed(game)
    finally:
        reopened.close()


@pytest.mark.parametrize("damage", ("formation-supply", "actor-resource", "formation-reference",
                                   "loss-body", "loss-removed", "loss-resource", "rng-stream"))
def test_transaction_invariants_reject_immediately_and_leave_exact_committed_state(military_engine, damage):
    game = military_engine
    game._military_call("military_bridge", "apply_result", result())
    before, rows = committed(game), physical(game)
    with pytest.raises((AuditError, ValueError)):
        with game._transaction():
            state = game._world.military_state
            if damage == "formation-supply":
                state["formations"]["army-a"]["supply_state"]["stocks"]["food"] = -1
            elif damage == "actor-resource":
                state["actors"]["a"]["treasury"] = -1
            elif damage == "formation-reference":
                state["formations"]["army-a"]["actor"] = "missing-actor"
            elif damage == "loss-body":
                state["loss_receipts"]["journal-loss"]["deaths"]["ra"] += 1
            elif damage == "loss-removed":
                del state["loss_receipts"]["journal-loss"]
            elif damage == "loss-resource":
                state["loss_receipts"]["journal-loss"]["result"]["supply_consumption"]["army-a"]["food"] += 1
            else:
                state["rng_streams"]["invalid"] = [3, [0], None]
            game._rng.random()
    assert committed(game) == before
    assert physical(game) == rows


def test_sparse_audit_detects_duplicate_effect_fingerprint_against_unchanged_history(military_engine):
    game = military_engine
    game.inject_event("flood", "ra", ["a"], severity=.6)
    effects = game._world.crisis_state["effects"]
    first, second = tuple(effects)[:2]
    before = committed(game)
    with pytest.raises(AuditError, match="duplicate consequence idempotency key"):
        with game._transaction():
            effects[second]["idempotency_key"] = effects[first]["idempotency_key"]
    assert committed(game) == before


def test_operation_reference_change_revalidates_unchanged_command(military_engine):
    game = military_engine
    game.plan_operation("hold", "a", ["army-a"], "hold", issuer="a")
    game.command_operation("hold", "a", delay=3)
    before = committed(game)
    with pytest.raises(AuditError, match="command has nonexistent issuer/recipient"):
        with game._transaction():
            operation = game._world.military_state["operations"]["hold"]
            operation["actor"] = "b"
            operation["formations"] = ["army-b"]
    assert committed(game) == before


def test_replacing_reference_bucket_revalidates_unchanged_command(military_engine):
    game = military_engine
    game.plan_operation("hold", "a", ["army-a"], "hold", issuer="a")
    game.command_operation("hold", "a", delay=3)
    before = committed(game)
    with pytest.raises(AuditError, match="invalid communicated message"):
        with game._transaction():
            game._world.military_state["operations"] = {}
    assert committed(game) == before


@pytest.mark.parametrize("shock_change", ("severity", "kind", "region", "start-date", "removed"))
def test_economy_shock_change_revalidates_unchanged_crisis_receipt(military_engine, shock_change):
    game = military_engine
    game.inject_event("flood", "ra", ["a"], severity=.6)
    game.advance(1)
    effect = next(effect for effect in game._world.crisis_state["effects"].values()
                  if effect["kind"] == "economic_shock" and effect["applied"])
    shock_id = effect["receipt"]["shock_id"]
    before = committed(game)
    with pytest.raises(AuditError, match="delivered shock facts conflict with receipt|lost delivered economic shock"):
        with game._transaction():
            shock = game._world.economy["shocks"][shock_id]
            if shock_change == "severity":
                shock["severity"] = .1
            elif shock_change == "kind":
                shock["kind"] = "drought"
            elif shock_change == "region":
                shock["region"] = "rb"
            elif shock_change == "start-date":
                shock["start_date"] = "1897-01-01"
            else:
                del game._world.economy["shocks"][shock_id]
    assert committed(game) == before


@pytest.mark.parametrize("cohort_change", ("manpower", "recipient", "started-at", "approval-reference", "removed"))
def test_replacement_change_revalidates_unchanged_consumed_political_receipt(military_engine, cohort_change):
    game = military_engine
    game.train_replacements("approved-cohort", "army-a", 5)
    before = committed(game)
    with pytest.raises(AuditError, match="Consumed approval names an unrelated replacement cohort|Consumed political approval has no actual replacement cohort"):
        with game._transaction():
            item = game._world.military_state["replacements"]["approved-cohort"]
            if cohort_change == "manpower":
                item["manpower"] += 1
            elif cohort_change == "recipient":
                item["recipient"] = "army-b"
            elif cohort_change == "started-at":
                item["started_at"] = "1896-12-31"
            elif cohort_change == "approval-reference":
                item["political_decision_id"] = "missing-approval"
            else:
                del game._world.military_state["replacements"]["approved-cohort"]
    assert committed(game) == before


def test_replacing_economy_component_revalidates_unchanged_crisis_receipt(military_engine):
    game = military_engine
    game.inject_event("flood", "ra", ["a"], severity=.6)
    game.advance(1)
    effect = next(effect for effect in game._world.crisis_state["effects"].values()
                  if effect["kind"] == "economic_shock" and effect["applied"])
    before = committed(game)
    with pytest.raises(AuditError, match="delivered shock facts conflict with receipt"):
        with game._transaction():
            replacement = copy.deepcopy(game._world.economy)
            replacement["shocks"][effect["receipt"]["shock_id"]]["severity"] = .1
            game._world.economy = replacement
    assert committed(game) == before


def test_player_control_change_revalidates_unchanged_pending_decision(military_engine):
    game = military_engine
    event = game.inject_event("flood", "ra", ["a"], severity=.6)
    game.send_event_report(event["event_id"], "a:ruler", delay=1)
    game.advance(5)
    assert game.pending_decisions("a")
    before = committed(game)
    with pytest.raises(AuditError, match="pending decision has unknown event/player country"):
        with game._transaction():
            game._world.crisis_state["player_countries"].clear()
    assert committed(game) == before


@pytest.mark.parametrize("unchanged_status", ("planned", "issued", "paused"))
def test_active_operations_are_checked_against_unchanged_allocations(military_engine, unchanged_status):
    game = military_engine
    game.plan_operation("one", "a", ["army-a"], "hold", issuer="a")
    # A previously completed operation may reuse a formation now allocated to
    # another operation. Reactivating that cold record must compare both.
    with game._transaction():
        previous = copy.deepcopy(game._world.military_state["operations"]["one"])
        previous.update(id="two", status="completed")
        game._world.military_state["operations"]["two"] = previous
        game._world.military_state["operations"]["one"]["status"] = unchanged_status
    before = committed(game)
    with pytest.raises(AuditError, match="multiple active operations"):
        with game._transaction():
            game._world.military_state["operations"]["two"]["status"] = "planned"
    assert committed(game) == before


def test_removed_record_field_is_restored_on_callback_failure(military_engine):
    game = military_engine
    before = committed(game)
    with pytest.raises(RuntimeError, match="callback failed"):
        with game._transaction():
            del game._world.countries["a"].treasury
            raise RuntimeError("callback failed")
    assert game._world.countries["a"].treasury == 500
    assert committed(game) == before


@pytest.mark.parametrize("metadata", ("seed", "rng", "system-manifest"))
def test_private_metadata_drift_cannot_leak_into_committed_reads_or_save(military_engine, tmp_path, metadata):
    game = military_engine
    before = committed(game)
    if metadata == "seed":
        game.seed += 100
    elif metadata == "rng":
        game._rng.random()
    else:
        game._required_systems.append({"name": "uncommitted-test-system", "version": "1"})
    assert committed(game) == before
    saved = game.save(tmp_path / "committed-metadata.json")
    payload = json.loads(saved.read_text(encoding="utf-8"))["payload"]
    assert encode(payload["engine"]) == before[0]
    assert payload["ledger"] == before[2]


def test_seed_changed_inside_failed_transaction_is_restored(military_engine):
    game = military_engine
    seed = game.seed
    before = committed(game)
    with pytest.raises(RuntimeError, match="failed metadata transaction"):
        with game._transaction():
            game.seed += 100
            game._rng.random()
            raise RuntimeError("failed metadata transaction")
    assert game.seed == seed
    assert committed(game) == before


def test_multiday_failure_restores_closed_daily_frames_rng_and_resume(military_engine):
    game = military_engine
    before, rows = committed(game), physical(game)
    original = game._systems[-1]
    calls = []
    def fail_on_third_day(world, tick, rng):
        events = original(world, tick, rng)
        calls.append(tick["date"])
        world.countries["a"].resources["rollback-proof"] = len(calls)
        rng.random()
        if len(calls) == 3:
            raise RuntimeError("third-day failure")
        return events
    game._systems[-1] = fail_on_third_day
    try:
        with pytest.raises(RuntimeError, match="third-day failure"):
            game.advance(5)
    finally:
        game._systems[-1] = original
    assert len(calls) == 3
    assert committed(game) == before
    assert physical(game) == rows
    # Independent replay avoids sharing one file between two advancing writers.
    replay_path = game.path.with_name("rollback-replay.sqlite")
    destination = sqlite3.connect(replay_path)
    try:
        game.db.backup(destination)
    finally:
        destination.close()
    reopened = Engine(replay_path, validation_mode=game.validation_mode)
    try:
        game.advance(3)
        reopened.advance(3)
        assert committed(game) == committed(reopened)
    finally:
        reopened.close()


def test_failed_current_state_write_rolls_back_dirty_rows_and_archive(military_engine):
    game = military_engine
    before, rows = committed(game), physical(game)
    game.db.execute("CREATE TRIGGER reject_current BEFORE UPDATE ON current_state "
                    "BEGIN SELECT RAISE(ABORT,'reject candidate'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="reject candidate"):
            game._military_call("military_bridge", "apply_result", result())
    finally:
        game.db.execute("DROP TRIGGER reject_current")
    assert committed(game) == before
    assert physical(game) == rows
    game._military_call("military_bridge", "apply_result", result())
    resumed = Engine(game.path, validation_mode=game.validation_mode)
    try:
        assert committed(resumed) == committed(game)
        assert resumed.true_state.armies["army-a"].personnel == 97
    finally:
        resumed.close()


def test_runtime_and_full_audit_campaign_save_resume_preserve_pending_fog_and_all_history(tmp_path):
    games = [military_game(tmp_path / (mode + ".sqlite"), mode)
             for mode in ("runtime", "verification")]
    loaded = []
    try:
        for game in games:
            game.inject_event("state_of_war", "ra", ["a", "b"], severity=.6)
            game.record_observation("player", "b", Report(game.true_state.date, "uncertain scout", .2,
                                                        {"troops": "unknown", "supplies": "unknown"}))
            game.add_supply_link("front-road", "ra", "rb", mode="road", throughput=5000.,
                                 travel_days=3, distance_km=50.)
            game.plan_operation("raid", "a", ["army-a"], "raid", "rb", issuer="a")
            game.command_operation("raid", "a", delay=2)
            game.advance(35)
            game._military_call("military_bridge", "apply_result", result("explicit-loss"))
        assert committed(games[0]) == committed(games[1])
        assert games[0].pending_decisions("a") == games[1].pending_decisions("a")
        assert games[0].observed_state("player") == games[1].observed_state("player")
        archives = [state_storage.export_archive(game.db) for game in games]
        assert archives[0] == archives[1]
        for index, game in enumerate(games):
            saved = game.save(tmp_path / ("save-" + str(index) + ".json"))
            restored = Engine.load(saved, tmp_path / ("resumed-" + str(index) + ".sqlite"))
            restored.validation_mode = game.validation_mode
            loaded.append(restored)
            assert committed(restored) == committed(game)
            assert state_storage.export_archive(restored.db) == archives[index]
        for game in games + loaded:
            game.advance(40)
        assert all(committed(game) == committed(games[0]) for game in games + loaded)
        assert all(state_storage.export_archive(game.db) == state_storage.export_archive(games[0].db)
                   for game in games + loaded)
        assert games[0].observed_state("player")["b"]["data"]["troops"] == "unknown"
    finally:
        for game in games + loaded:
            game.close()
