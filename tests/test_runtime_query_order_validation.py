"""Inactive order validation preserves historical errors without warm scans."""
import copy

import pytest

from worldline import crisis_effects as ce, crisis_state as cs
from worldline.audit import Auditor
from worldline.performance_benchmark import load_baseline_modules
from worldline.runtime_queries import ordered_values
from worldline.state_runtime import Journal
from test_crisis_core import world as crisis_world


def effect_order(value):
    return value['due_date'], value['created_at'], int(value['effect_id'].rsplit('-', 1)[-1])


def unapplied(value):
    return not value['applied']


def record(identifier, applied=True):
    return {'effect_id': identifier, 'applied': applied,
            'due_date': '1897-01-01', 'created_at': '1897-01-01'}


def original_ordered(records, predicate=unapplied, order=effect_order):
    # Historical apply_due evaluates every key before inspecting applied status.
    return [value for value in sorted(records.values(), key=order) if predicate(value)]


def assert_original_error(records, predicate=unapplied, order=effect_order):
    try:
        original_ordered(records, predicate, order)
    except Exception as expected:
        with pytest.raises(type(expected)) as actual:
            ordered_values(records, 'validation', predicate, order)
        assert str(actual.value) == str(expected)
    else:
        pytest.fail('Historical full sort should have raised')


def rename_effect(world, old_id, new_id):
    effect = world.crisis_state['effects'].pop(old_id)
    effect['effect_id'] = new_id
    world.crisis_state['effects'][new_id] = effect
    event = world.crisis_state['events'][effect['event_id']]
    for field in ('immediate_consequences', 'delayed_consequences'):
        event[field] = [new_id if key == old_id else key for key in event[field]]


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('warm', [False, True])
def test_applied_imported_effect_passes_auditor_but_preserves_baseline_sort_error(tracked, warm):
    world = crisis_world()
    event = cs.add_event(world, 'custom:order-validation', 'ra', ['a'])
    applied = ce.enqueue(world, event['event_id'], 'pressure_delta',
                         {'category': 'civil_unrest', 'country': 'a', 'delta': 1})
    ce.apply_due(world)
    if tracked:
        Journal(world)
    if warm:
        assert ce.apply_due(world) == []
    rename_effect(world, applied['effect_id'], 'imported-effect')
    ce.enqueue(world, event['event_id'], 'pressure_delta',
               {'category': 'civil_unrest', 'country': 'a', 'delta': 10})
    Auditor().check(world)
    before = copy.deepcopy(world.to_dict())
    historical = load_baseline_modules().crisis_effects
    expected_world = copy.deepcopy(world)
    with pytest.raises(ValueError) as expected:
        historical.apply_due(expected_world)
    for _ in range(2):
        with pytest.raises(ValueError) as actual:
            ce.apply_due(world)
        assert str(actual.value) == str(expected.value)
        # No pending effect, pressure or saved RNG stream is changed before
        # the original full-sort error, including on a repeated failed query.
        assert world.to_dict() == before == expected_world.to_dict()


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('warm', [False, True])
def test_changed_inactive_identity_is_revalidated_and_repair_or_deletion_recovers(tracked, warm):
    records = {'cold': record('effect-1'), 'live': record('effect-2', False)}
    if tracked:
        records = Journal(records).root
    if warm:
        assert ordered_values(records, 'validation', unapplied, effect_order) == [records['live']]
    records['cold']['effect_id'] = 'imported-effect'
    assert_original_error(records)
    assert_original_error(records)
    records['cold']['effect_id'] = 'effect-3'
    assert ordered_values(records, 'validation', unapplied, effect_order) == original_ordered(records)
    records['cold']['effect_id'] = 'imported-effect'
    assert_original_error(records)
    del records['cold']
    assert ordered_values(records, 'validation', unapplied, effect_order) == [records['live']]
    if tracked:
        assert records._active_indexes['validation'].invalid == set()
        assert not hasattr(records._active_indexes['validation'], '_collection')


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('warm', [False, True])
def test_first_error_follows_original_insertion_order_before_any_filter(tracked, warm):
    # Both records are already applied. Key sorting would report beta first;
    # historical key construction follows mapping insertion and reports alpha.
    records = {'z': record('effect-1'), 'a': record('effect-2')}
    if tracked:
        records = Journal(records).root
    if warm:
        assert ordered_values(records, 'validation', unapplied, effect_order) == []
    records['z']['effect_id'] = 'imported-alpha'
    records['a']['effect_id'] = 'imported-beta'
    assert_original_error(records)
    with pytest.raises(ValueError, match='alpha'):
        ordered_values(records, 'validation', unapplied, effect_order)


@pytest.mark.parametrize('tracked', [False, True])
def test_present_none_record_is_validated_and_deleted_none_key_is_removed(tracked):
    records = {'live': record('effect-1', False)}
    if tracked:
        records = Journal(records).root
    ordered_values(records, 'validation', unapplied, effect_order)
    records['none'] = None
    assert_original_error(records)
    assert_original_error(records)
    del records['none']
    assert ordered_values(records, 'validation', unapplied, effect_order) == [records['live']]
    if tracked:
        assert records._active_indexes['validation'].invalid == set()


def test_plain_nested_order_edits_barrier_rollback_and_log_gap_are_visible():
    journal = Journal({'cold': {'applied': True, 'meta': {'number': '1'}},
                       'live': {'applied': False, 'meta': {'number': '2'}}})
    records = journal.root
    order = lambda value: int(value['meta']['number'])
    assert ordered_values(records, 'validation', unapplied, order) == [records['live']]
    frame = journal.begin()
    raw = {'number': 'invalid'}
    records['cold']['meta'] = raw
    assert_original_error(records, order=order)
    raw['number'] = '1'  # No tracked write before the next barrier.
    assert ordered_values(records, 'validation', unapplied, order) == [records['live']]
    raw['number'] = 'invalid-again'
    assert_original_error(records, order=order)
    journal.barrier()
    records['cold']['meta']['number'] = '3'
    assert ordered_values(records, 'validation', unapplied, order) == [records['live']]
    for index in range(5000):
        records['cold']['meta']['number'] = str(index)
    records['cold']['meta']['number'] = 'invalid-after-log-gap'
    assert_original_error(records, order=order)
    journal.end(frame, rollback=True)
    assert records['cold']['meta']['number'] == '1'
    assert ordered_values(records, 'validation', unapplied, order) == original_ordered(records, order=order)
    assert not hasattr(records._active_indexes['validation'], '_collection')


def test_new_plain_inactive_record_is_rechecked_until_barrier_and_rollback():
    journal = Journal({'live': record('effect-1', False)})
    records = journal.root
    ordered_values(records, 'validation', unapplied, effect_order)
    frame = journal.begin()
    raw = record('imported-effect')
    records['new'] = raw
    assert_original_error(records)
    raw['effect_id'] = 'effect-2'
    assert ordered_values(records, 'validation', unapplied, effect_order) == [records['live']]
    raw['effect_id'] = 'imported-effect-again'
    assert_original_error(records)
    journal.barrier()
    records['new']['effect_id'] = 'effect-3'
    assert ordered_values(records, 'validation', unapplied, effect_order) == [records['live']]
    journal.end(frame, rollback=True)
    assert 'new' not in records
    assert ordered_values(records, 'validation', unapplied, effect_order) == [records['live']]


def test_valid_warm_queries_validate_no_cold_order_keys_and_only_changed_inactive_key():
    journal = Journal({**{str(i): record('effect-' + str(i)) for i in range(1000)},
                       'live': record('effect-1000', False)})
    records, inspected = journal.root, []

    def order(value):
        inspected.append(value['effect_id'])
        return effect_order(value)

    assert ordered_values(records, 'validation', unapplied, order) == [records['live']]
    assert set(inspected) == {value['effect_id'] for value in records.values()}
    inspected.clear()
    assert ordered_values(records, 'validation', unapplied, order) == [records['live']]
    assert inspected and set(inspected) == {'effect-1000'}
    inspected.clear()
    records['8']['effect_id'] = 'effect-2000'
    assert ordered_values(records, 'validation', unapplied, order) == [records['live']]
    assert inspected.count('effect-2000') == 1
    assert set(inspected) == {'effect-2000', 'effect-1000'}


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('warm', [False, True])
def test_equal_active_orders_keep_original_insertion_order(tracked, warm):
    records = {'z-first': record('z-imported-1', False), 'inactive': record('effect-1'),
               'a-second': record('a-imported-1', False)}
    if tracked:
        records = Journal(records).root
    expected = original_ordered(records)
    assert expected == [records['z-first'], records['a-second']]
    if warm:
        assert ordered_values(records, 'validation', unapplied, effect_order) == expected
    assert ordered_values(records, 'validation', unapplied, effect_order) == expected


@pytest.mark.parametrize('failure', ['predicate', 'interrupt'])
def test_unexpected_refresh_failure_resets_token_and_releases_temporary_collection(failure):
    records = Journal({'first': record('effect-1', False), 'second': record('effect-2', False)}).root
    broken = True

    def predicate(value):
        if broken and failure == 'predicate' and value['effect_id'] == 'effect-2':
            raise RuntimeError('predicate unavailable')
        return unapplied(value)

    def order(value):
        if broken and failure == 'interrupt' and value['effect_id'] == 'effect-2':
            raise KeyboardInterrupt('interrupted order validation')
        return effect_order(value)

    with pytest.raises(RuntimeError if failure == 'predicate' else KeyboardInterrupt):
        ordered_values(records, 'validation', predicate, order)
    index = records._active_indexes['validation']
    assert index.token is None
    assert not hasattr(index, '_collection')
    broken = False
    assert ordered_values(records, 'validation', predicate, order) == original_ordered(records)
