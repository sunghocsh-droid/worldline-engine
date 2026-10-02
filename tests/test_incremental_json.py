"""Dirty JSON validity keeps nested, new-identity and full-audit safeguards."""
import json
from contextlib import contextmanager

import pytest

from worldline import incremental_audit as audit
from worldline.models import World
from worldline.state_runtime import Journal


def setup():
    world = World('1900-01-01', canon={
        'cold': [{'trace': ['preserve', {'value': index}]} for index in range(1000)],
        'hot': {'nested': {'value': 1}}, 'list': [{'value': 2}],
    })
    journal = Journal(world)
    baseline = audit.json_check(world.canon)
    return world, journal, baseline


@contextmanager
def scope(world, journal, frame, enabled=True):
    journal.barrier()
    previous = journal.view(world, frame)
    with audit.changed(world, previous, frame, enabled):
        yield


def test_unchanged_cold_json_is_not_rebuilt_or_serialized(monkeypatch):
    world, journal, _ = setup()
    frame = journal.begin()
    world.canon['hot']['nested']['value'] = 3
    original, encoded = json.dumps, []
    def capture(value, **options):
        encoded.append(value)
        return original(value, **options)
    monkeypatch.setattr(audit.json, 'dumps', capture)
    with scope(world, journal, frame):
        audit.json_check(world.canon)
    assert encoded == [[3]]
    assert len(world.canon['cold']) == 1000
    journal.end(frame)


@pytest.mark.parametrize('damage', (
    'nested-nan', 'nested-infinity', 'nested-object', 'new-subtree',
    'replacement-subtree', 'mixed-key-shape', 'dirty-list', 'dict-cycle', 'list-cycle',
))
def test_invalid_dirty_fragments_and_new_subtrees_rejected_then_rollback(damage):
    world, journal, baseline = setup()
    frame = journal.begin()
    if damage == 'nested-nan':
        world.canon['cold'][500]['trace'][1]['value'] = float('nan')
    elif damage == 'nested-infinity':
        world.canon['hot']['nested']['value'] = float('inf')
    elif damage == 'nested-object':
        world.canon['hot']['nested']['value'] = object()
    elif damage == 'new-subtree':
        world.canon['new'] = {'nested': [{'value': float('nan')}]}
    elif damage == 'replacement-subtree':
        world.canon['hot'] = {'nested': {'value': float('inf')}}
    elif damage == 'mixed-key-shape':
        world.canon['hot']['nested'][1] = 3
    elif damage == 'dirty-list':
        world.canon['list'][0]['value'] = float('nan')
    elif damage == 'dict-cycle':
        cyclic = {}
        cyclic['self'] = cyclic
        world.canon['cycle'] = cyclic
    else:
        cyclic = []
        cyclic.append(cyclic)
        world.canon['cycle'] = cyclic
    with scope(world, journal, frame):
        with pytest.raises((TypeError, ValueError)):
            audit.json_check(world.canon)
    journal.end(frame, rollback=True)
    assert audit.json_check(world.canon) == baseline


def test_new_plain_alias_mutated_before_barrier_is_checked_whole():
    world, journal, baseline = setup()
    frame = journal.begin()
    fresh = {'inner': []}
    world.canon['alias-one'] = world.canon['alias-two'] = fresh
    fresh['inner'].append({'value': float('nan')})
    with scope(world, journal, frame):
        assert world.canon['alias-one'] is world.canon['alias-two']
        with pytest.raises(ValueError):
            audit.json_check(world.canon)
    journal.end(frame, rollback=True)
    assert audit.json_check(world.canon) == baseline


def test_newly_wrapped_world_bucket_identity_is_not_trusted():
    world, journal, baseline = setup()
    frame = journal.begin()
    world.canon = {'value': float('inf')}
    with scope(world, journal, frame):
        assert id(world.canon) not in frame.states
        with pytest.raises(ValueError):
            audit.json_check(world.canon)
    journal.end(frame, rollback=True)
    assert audit.json_check(world.canon) == baseline


def test_list_reordering_replacement_and_nested_tuple_are_conservative():
    world, journal, baseline = setup()
    frame = journal.begin()
    world.canon['list'].insert(0, {'valid': [1, 2]})
    world.canon['list'].reverse()
    world.canon['tuple'] = ({'value': float('nan')},)
    with scope(world, journal, frame):
        with pytest.raises(ValueError):
            audit.json_check(world.canon)
    journal.end(frame, rollback=True)
    assert audit.json_check(world.canon) == baseline


def test_unknown_root_and_full_mode_keep_full_json_validation():
    world, journal, baseline = setup()
    frame = journal.begin()
    # An unknown standalone root has no committed-identity proof.
    with scope(world, journal, frame):
        with pytest.raises(ValueError):
            audit.json_check({'value': float('nan')})
    world.canon['cold'][0]['trace'][1]['value'] = float('inf')
    with scope(world, journal, frame, enabled=False):
        with pytest.raises(ValueError):
            audit.json_check(world.canon)
    journal.end(frame, rollback=True)
    assert audit.json_check(world.canon) == baseline


def test_nested_daily_frames_and_rollback_do_not_reuse_invalid_certification():
    world, journal, baseline = setup()
    outer = journal.begin()
    day = journal.begin()
    world.canon['hot']['nested']['value'] = 3
    with scope(world, journal, day):
        audit.json_check(world.canon)
    journal.end(day)
    day = journal.begin()
    world.canon['hot']['nested']['value'] = float('nan')
    with scope(world, journal, day):
        with pytest.raises(ValueError):
            audit.json_check(world.canon)
    journal.end(day, rollback=True)
    day = journal.begin()
    with scope(world, journal, day):
        audit.json_check(world.canon)
    journal.end(day)
    journal.end(outer, rollback=True)
    assert audit.json_check(world.canon) == baseline
