"""Committed SQLite remains authoritative with narrow journals and dirty rows."""
import copy
import json
import sqlite3

import pytest
from worldline.engine import Engine, encode
from worldline import military_benchmark as bench, state_storage
from worldline.models import World


@pytest.fixture
def engine(tmp_path):
    engine = bench.setup_engine(tmp_path / 'game.sqlite', 'industrial_front')
    engine.validation_mode = 'runtime'
    yield engine
    engine.close()


def rows(engine):
    return dict(engine.db.execute('SELECT path,payload FROM state_components'))


def test_small_pressure_write_preserves_unrelated_component_rows(engine):
    old = rows(engine)
    statements = []
    engine.db.set_trace_callback(statements.append)
    engine.set_pressure('a', 'war', 17, ['bounded row test'])
    engine.db.set_trace_callback(None)
    after = rows(engine)
    changed = {tuple(json.loads(key)) for key in old if old[key] != after[key]}
    assert changed == {('world', 'pressures')}
    updates = [s for s in statements if s.startswith('INSERT INTO state_components')]
    assert len(updates) == 1
    text = engine.db.execute('SELECT payload FROM current_state WHERE id=1').fetchone()[0]
    assert len(text) < 10000
    assert bench.digest(state_storage.read(engine.db, text)) == bench.digest(engine._payload())


def test_same_connection_component_edit_is_not_hidden_by_read_cache(engine):
    path = encode(['world', 'date'])
    engine.db.execute('UPDATE state_components SET payload=? WHERE path=?', (encode('1897-01-02'), path))
    engine.db.commit()
    assert engine.true_state.date == '1897-01-02'
    with pytest.raises(ValueError, match='another writer'):
        engine.set_pressure('a', 'war', 18)


def test_other_connection_component_edit_is_not_hidden_by_read_cache(engine):
    with sqlite3.connect(engine.path) as writer:
        writer.execute('UPDATE state_components SET payload=? WHERE path=?',
                       (encode('1897-01-02'), encode(['world', 'date'])))
    assert engine.true_state.date == '1897-01-02'
    with pytest.raises(ValueError, match='another writer'):
        engine.set_pressure('a', 'war', 18)


def test_transaction_read_is_committed_and_failed_dirty_graph_is_isolated(engine):
    before, before_rows = bench.logical_result(engine), rows(engine)
    with pytest.raises(RuntimeError):
        with engine._transaction():
            engine._world.countries['a'].treasury += 10
            engine._world.military_state['formations']['army-a']['supply_state']['stocks']['food'] += 9
            engine._world.npc_state['adapter']['pending'].setdefault('a:ruler', []).append('uncommitted')
            engine._rng.random()
            engine._event('journal_test', {'nested': [1, 2]})
            assert bench.logical_result(engine) == before | {'ledger': engine.ledger()}
            raise RuntimeError('rollback all')
    assert bench.logical_result(engine) == before
    assert rows(engine) == before_rows
    engine.advance(1)
    assert engine._journal.frames == []


def test_failure_after_component_write_rolls_back_archive_heads_and_rng(engine):
    before, before_rows = bench.logical_result(engine), rows(engine)
    archive = engine.db.execute('SELECT COUNT(*) FROM state_archive').fetchone()[0]
    engine.db.execute("CREATE TRIGGER fail_current BEFORE UPDATE ON current_state BEGIN SELECT RAISE(ABORT,'test persistence failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match='test persistence failure'):
        engine.advance(2)
    assert bench.logical_result(engine) == before
    assert rows(engine) == before_rows
    assert engine.db.execute('SELECT COUNT(*) FROM state_archive').fetchone()[0] == archive
    engine.db.execute('DROP TRIGGER fail_current')
    engine.advance(2)


def test_no_world_deepcopy_on_daily_or_small_command_path(engine, monkeypatch):
    original = copy.deepcopy
    def checked(value, *args, **kwargs):
        assert not (isinstance(value, World) and any(getattr(value, name) is not None
            for name in ('npc_state', 'crisis_state', 'military_state'))), 'whole historical World copied in hot path'
        return original(value, *args, **kwargs)
    monkeypatch.setattr(copy, 'deepcopy', checked)
    engine.set_pressure('a', 'war', 16)
    engine.advance(3)


@pytest.mark.parametrize('scenario', bench.SCENARIOS)
def test_runtime_full_audit_save_and_resume_preserve_all_results(tmp_path, scenario):
    engines = [bench.setup_engine(tmp_path / (mode + '.sqlite'), scenario) for mode in ('runtime', 'verification')]
    try:
        for engine, mode in zip(engines, ('runtime', 'verification')):
            engine.validation_mode = mode
        actions = [[], []]
        for month in range(1, 3):
            for engine, commands in zip(engines, actions):
                bench.campaign_orders(engine, scenario, month, commands)
                engine.advance(1, 'month')
            assert bench.logical_result(engines[0]) == bench.logical_result(engines[1])
            assert actions[0] == actions[1]
        saved = engines[0].save(tmp_path / 'save.json')
        loaded = Engine.load(saved, tmp_path / 'resumed.sqlite', validation_mode='runtime')
        try:
            for engine in (engines[0], engines[1], loaded):
                engine.advance(2)
            expected = bench.logical_result(engines[0])
            assert bench.logical_result(engines[1]) == bench.logical_result(loaded) == expected
            assert engines[0].historical_state(kind='military_logistics_receipts') == loaded.historical_state(kind='military_logistics_receipts')
        finally:
            loaded.close()
    finally:
        for engine in engines:
            engine.close()


def test_nested_direct_edit_marks_history_dirty_without_losing_prior_version(engine):
    engine.advance(1)
    before = engine.historical_state(kind='military_logistics_receipts')
    assert before
    snapshot = bench.logical_result(engine)
    key = next(k for k, v in engine._world.military_state['logistics_receipts'].items()
               if v.get('kind') == 'supply_consumption')
    with pytest.raises(ValueError):
        with engine._transaction():
            engine._world.military_state['logistics_receipts'][key]['actual']['food'] = -1
    assert bench.logical_result(engine) == snapshot
    assert engine.historical_state(kind='military_logistics_receipts') == before


def test_public_inspection_and_copy_do_not_leak_tracked_mutability(engine):
    snapshot = bench.logical_result(engine)
    public = engine.true_state
    copied = copy.deepcopy(engine._world)
    public.military_state['formations']['army-a']['readiness'] = -1
    copied.military_state['formations']['army-a']['readiness'] = -2
    assert bench.logical_result(engine) == snapshot
    assert not hasattr(copied, '_tracker')
