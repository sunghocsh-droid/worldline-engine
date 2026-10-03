"""Narrow committed reads retain isolation, rollback and SQLite authority."""
import json
import sqlite3

import pytest

from worldline import Engine, Report
from worldline.engine import encode
from worldline import engine as engine_module


@pytest.mark.parametrize('field', ['orders', 'observed', 'journeys', 'rng'])
def test_metadata_reads_do_not_project_or_serialize_world(engine, monkeypatch, field):
    expected = engine._stored_payload()[field]
    def reject_world(_world):
        raise AssertionError('metadata query projected the entire World')
    monkeypatch.setattr(engine_module, 'projection', reject_world)
    actual = getattr(engine, field)
    if field == 'rng':
        actual = json.loads(encode(actual.getstate()))
    assert actual == expected


def test_metadata_reads_are_sorted_detached_and_committed_during_rollback(engine):
    engine.record_observation('player', 'rumor', Report(engine._world.date, 'scout', .4,
                                                    {'z': [2], 'a': [1]}))
    before = engine._stored_payload()
    with pytest.raises(RuntimeError):
        with engine._transaction():
            engine._observed['player']['rumor']['data']['a'].append(9)
            engine._orders['candidate'] = {'nested': [8]}
            engine._journeys['candidate'] = {'nested': [7]}
            engine._rng.random()
            for field in ('orders', 'observed', 'journeys'):
                assert getattr(engine, field) == before[field]
            assert json.loads(encode(engine.rng.getstate())) == before['rng']
            detached = engine.observed
            detached['player']['rumor']['data']['a'].append(99)
            assert engine.observed == before['observed']
            assert list(engine.observed['player']['rumor']['data']) == ['a', 'z']
            raise RuntimeError('rollback')
    assert engine._stored_payload() == before


@pytest.mark.parametrize('separate_connection', [False, True])
def test_metadata_query_detects_sql_component_edit(engine, separate_connection):
    expected = {'sql': {'nested': ['committed']}}
    if separate_connection:
        with sqlite3.connect(engine.path) as writer:
            writer.execute('UPDATE state_components SET payload=? WHERE path=?',
                           (encode(expected), encode(['observed'])))
    else:
        engine.db.execute('UPDATE state_components SET payload=? WHERE path=?',
                          (encode(expected), encode(['observed'])))
        engine.db.commit()
    assert engine.observed == expected
    engine.observed['sql']['nested'].append('caller change')
    assert engine.observed == expected


def test_private_world_change_keeps_metadata_on_validated_database_path(engine, monkeypatch):
    before = engine.observed
    engine._world.canon['private'] = ['candidate']
    original = engine_module.state_storage.read
    calls = []
    def read(db, text):
        calls.append(text)
        return original(db, text)
    monkeypatch.setattr(engine_module.state_storage, 'read', read)
    assert engine.observed == before
    assert len(calls) == 1


def test_narrow_world_read_equals_complete_payload(engine):
    assert encode(engine.true_state.to_dict()) == encode(engine._stored_payload()['world'])


def test_root_bucket_replacement_still_reads_committed_nested_reference(engine):
    engine.record_observation('player', 'rumor', Report(engine._world.date, 'scout', .4,
                                                    {'facts': [1]}))
    before = engine.observed
    with pytest.raises(RuntimeError):
        with engine._transaction():
            engine._observed['player']['rumor']['data']['facts'].append(2)
            engine._journal.root['observed'] = {'replacement': {'facts': [3]}}
            assert engine.observed == engine._stored_payload()['observed'] == before
            raise RuntimeError('rollback replacement')
    assert engine.observed == before
