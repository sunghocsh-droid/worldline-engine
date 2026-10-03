"""Query indexes preserve causal order, mutation visibility and saved truth."""
import copy
import hashlib
import json

import pytest

from worldline import crisis, crisis_effects as ce, crisis_response as cr, crisis_risk, crisis_state as cs
from worldline.models import World
from worldline.runtime_queries import first_grouped, grouped_count, grouped_items, latest_by_group, ordered_values
from worldline.state_runtime import Journal
from test_crisis_core import world as crisis_world


def test_grouped_lookup_visits_only_changed_records_and_keeps_sorted_order():
    journal = Journal({**{f'cold-{i}': {'owner': 'cold'} for i in range(1000)},
                       'z': {'owner': 'a'}, 'a': {'owner': 'a'}})
    records, visited = journal.root, []
    def group(value):
        visited.append(value)
        return value['owner']
    assert [key for key, _ in grouped_items(records, 'owners', group, 'a')] == ['a', 'z']
    visited.clear()
    assert grouped_count(records, 'owners', group, 'a') == 2
    assert visited == []
    records['z']['owner'] = 'b'
    assert [key for key, _ in grouped_items(records, 'owners', group, 'a')] == ['a']
    assert visited == [records['z']]
    assert len(records) == 1002


def test_group_membership_tracks_plain_insertion_nested_edits_and_rollback():
    journal = Journal({'old': {'owner': None}, 'other': {'owner': 'b'}})
    records = journal.root
    group = lambda value: value['owner']
    assert grouped_count(records, 'owners', group, None) == 1
    frame = journal.begin()
    inserted = {'owner': None}
    records['new'] = inserted
    assert grouped_count(records, 'owners', group, None) == 2
    inserted['owner'] = 'b'
    assert grouped_count(records, 'owners', group, None) == 1
    journal.barrier()
    records['new']['owner'] = None
    del records['old']
    assert [key for key, _ in grouped_items(records, 'owners', group, None)] == ['new']
    journal.end(frame, rollback=True)
    assert [key for key, _ in grouped_items(records, 'owners', group, None)] == ['old']
    assert len(records) == 2


def test_grouped_lookup_rebuilds_after_journal_gap_and_handles_deletion_reinsertion():
    journal = Journal({'z': {'group': 'a'}, 'a': {'group': 'a'}})
    records, group = journal.root, lambda value: value['group']
    assert first_grouped(records, 'groups', group, 'a') is records['z']
    for i in range(5000):
        records['z']['group'] = 'a' if i % 2 else 'b'
    assert grouped_count(records, 'groups', group, 'a') == 2
    original = records.pop('z')
    records['z'] = original
    assert first_grouped(records, 'groups', group, 'a') is records['a']
    assert first_grouped(records, 'groups', group, 'missing') is None


def test_group_index_sees_raw_edits_of_new_plain_nested_containers_before_barrier():
    journal = Journal({'record': {'data': {'countries': ['a']}}})
    records = journal.root
    group = lambda value: tuple(value['data']['countries'])
    assert grouped_count(records, 'countries', group, ('a',)) == 1
    frame = journal.begin()
    raw = {'countries': ['b']}
    records['record']['data'] = raw
    assert grouped_count(records, 'countries', group, ('b',)) == 1
    raw['countries'].append('c')
    assert grouped_count(records, 'countries', group, ('b',)) == 0
    assert grouped_count(records, 'countries', group, ('b', 'c')) == 1
    journal.barrier()
    records['record']['data']['countries'] = ['d']
    assert grouped_count(records, 'countries', group, ('d',)) == 1
    records['record']['data']['countries'].append('e')
    assert grouped_count(records, 'countries', group, ('d', 'e')) == 1
    journal.end(frame, rollback=True)
    assert grouped_count(records, 'countries', group, ('a',)) == 1


def test_latest_index_sees_raw_observation_group_and_order_edits_before_barrier():
    journal = Journal({'old': {'data': {'event_id': 'one'}, 'order': [1]},
                       'new': {'data': {'event_id': 'one'}, 'order': [2]}})
    records = journal.root
    def latest():
        return latest_by_group(records, 'observations', lambda value: value['data']['event_id'],
                               lambda value: tuple(value['order']), lambda value: True, ['one', 'two'])
    assert latest()['one'] is records['new']
    raw = {'event_id': 'two'}
    records['new']['data'] = raw
    records['new']['order'] = [3]
    assert latest()['one'] is records['old']
    assert latest()['two'] is records['new']
    raw['event_id'] = 'one'
    records['new']['order'][0] = 0
    assert latest() == {'one': records['old']}
    journal.barrier()
    records['new']['order'][0] = 4
    assert latest()['one'] is records['new']


def test_failed_index_build_is_rebuilt_even_when_collection_token_does_not_change():
    journal = Journal({'a': {'group': 'one'}, 'b': {'group': 'two'}})
    broken = True
    def group(value):
        if broken and value['group'] == 'two':
            raise ValueError('descriptor unavailable')
        return value['group']
    with pytest.raises(ValueError, match='descriptor unavailable'):
        grouped_count(journal.root, 'groups', group, 'one')
    broken = False
    assert grouped_count(journal.root, 'groups', group, 'two') == 1
    assert grouped_count(journal.root, 'groups', group, 'one') == 1


def test_unhashable_raw_group_preserves_equality_scan_and_recovers_after_repair():
    journal = Journal({'bad': {'group': []}, 'good': {'group': 'a'}})
    records, group = journal.root, lambda value: value['group']
    assert grouped_count(records, 'groups', group, 'a') == 1
    assert [key for key, _ in grouped_items(records, 'groups', group, [])] == ['bad']
    records['bad']['group'] = 'a'
    assert grouped_count(records, 'groups', group, 'a') == 2


def test_unapplied_selection_does_not_rescan_cold_history_on_warm_queries():
    journal = Journal({**{str(i): {'applied': True, 'order': i} for i in range(1000)},
                       'future': {'applied': False, 'order': 2000}})
    records, inspected = journal.root, []
    def predicate(value):
        inspected.append(value)
        return not value['applied']
    assert ordered_values(records, 'unapplied', predicate, lambda value: value['order']) == [records['future']]
    inspected.clear()
    assert ordered_values(records, 'unapplied', predicate, lambda value: value['order']) == [records['future']]
    assert inspected == []
    records['future']['applied'] = True
    assert ordered_values(records, 'unapplied', predicate, lambda value: value['order']) == []
    assert inspected == [records['future']]


def test_crisis_scope_rng_sequence_includes_closed_history_and_refreshes_nested_scope():
    plain = crisis_world()
    tracked = copy.deepcopy(plain)
    journal = Journal(tracked)
    for w in (plain, tracked):
        first = cs.add_event(w, 'custom:scope', 'ra', ['b', 'a'])
        cs.close_event(w, first['event_id'])
        cs.add_event(w, 'custom:other', 'ra', ['a'])
        second = cs.add_event(w, 'custom:scope', 'ra', ['a', 'b'])
        expected_key = 'event:' + hashlib.sha256(cs._json([['custom:scope', 'ra', ['a', 'b']], 2]).encode()).hexdigest()
        assert second['rng_key'] == expected_key
    assert tracked.to_dict() == plain.to_dict()
    journal.barrier()
    # The index itself must observe even invalid external scope mutation;
    # validation remains responsible for rejecting rewritten event history.
    first_key = next(iter(tracked.crisis_state['events']))
    tracked.crisis_state['events'][first_key]['countries'].append('c')
    third = cs.add_event(tracked, 'custom:scope', 'ra', ['a', 'b'])
    assert third['rng_key'] == expected_key


def test_effect_idempotency_keeps_original_first_match_and_conflict_check():
    w = crisis_world()
    event = cs.add_event(w, 'custom:effects', 'ra', ['a'])
    effect = ce.enqueue(w, event['event_id'], 'pressure_delta',
                        {'category': 'civil_unrest', 'country': 'a', 'delta': 2}, idempotency_key='same')
    duplicate = copy.deepcopy(effect)
    duplicate['effect_id'] = 'a-imported-1'
    effect['effect_id'] = 'z-imported-1'
    w.crisis_state['effects'] = {effect['effect_id']: effect, duplicate['effect_id']: duplicate}
    journal = Journal(w)
    assert ce.enqueue(w, event['event_id'], 'pressure_delta', effect['payload'], idempotency_key='same') is w.crisis_state['effects']['z-imported-1']
    with pytest.raises(ValueError, match='conflicts'):
        ce.enqueue(w, event['event_id'], 'pressure_delta',
                   {'category': 'civil_unrest', 'country': 'a', 'delta': 3}, idempotency_key='same')
    assert any('duplicate consequence idempotency' in issue for issue in cs.validate(w))
    assert len(w.crisis_state['effects']) == 2


def test_effect_delivery_keeps_numeric_order_due_date_and_rollback():
    plain = crisis_world()
    event = cs.add_event(plain, 'custom:effects', 'ra', ['a'])
    for i in range(12):
        ce.enqueue(plain, event['event_id'], 'pressure_delta',
                   {'category': 'civil_unrest', 'country': 'a', 'delta': i + 1}, idempotency_key=str(i))
    future = ce.enqueue(plain, event['event_id'], 'pressure_delta',
                        {'category': 'civil_unrest', 'country': 'a', 'delta': -100},
                        due_date='1897-01-04', idempotency_key='future')
    tracked = copy.deepcopy(plain)
    journal = Journal(tracked)
    before = tracked.to_dict()
    frame = journal.begin()
    expected, actual = ce.apply_due(plain), ce.apply_due(tracked)
    assert actual == expected
    assert [entry['effect_id'] for entry in actual] == [f'crisis-effect-{i}' for i in range(1, 13)]
    assert tracked.to_dict() == plain.to_dict()
    assert ce.apply_due(tracked) == []
    assert not tracked.crisis_state['effects'][future['effect_id']]['applied']
    journal.end(frame, rollback=True)
    assert tracked.to_dict() == before
    assert ce.apply_due(tracked) == expected


def test_imported_equal_effect_orders_preserve_original_mapping_insertion():
    plain = crisis_world()
    event = cs.add_event(plain, 'custom:effects', 'ra', ['a'])
    first = ce.enqueue(plain, event['event_id'], 'pressure_delta',
                       {'category': 'civil_unrest', 'country': 'a', 'delta': 80}, idempotency_key='first')
    second = ce.enqueue(plain, event['event_id'], 'pressure_delta',
                        {'category': 'civil_unrest', 'country': 'a', 'delta': -80}, idempotency_key='second')
    first['effect_id'], second['effect_id'] = 'z-imported-1', 'a-imported-1'
    plain.crisis_state['effects'] = {first['effect_id']: first, second['effect_id']: second}
    event['immediate_consequences'] = [first['effect_id'], second['effect_id']]
    assert cs.validate(plain) == []
    tracked = copy.deepcopy(plain)
    journal = Journal(tracked)
    assert ce.apply_due(tracked) == ce.apply_due(plain)
    assert cs.pressure_value(tracked, 'civil_unrest', country='a') == 0
    assert tracked.to_dict() == plain.to_dict()


def test_report_correction_stale_delivery_and_save_continue_preserve_exact_truth():
    plain = crisis_world()
    event = cs.add_event(plain, 'custom:reports', 'ra', ['a'], actual_facts={'threat': .9})
    cr.queue_report(plain, event['event_id'], 'a:ruler', delay=3)
    cr.queue_report(plain, event['event_id'], 'a:ruler', delay=0, observed_facts={'threat': .2})
    tracked = copy.deepcopy(plain)
    journal = Journal(tracked)
    expected_accepted = [[True], [], [True], [False], []]
    for day in range(5):
        frame = journal.begin()
        if day == 2:
            for w in (plain, tracked):
                cr.queue_report(w, event['event_id'], 'a:ruler', delay=0, observed_facts={'threat': .1})
        expected, actual = cr.deliver_due(plain), cr.deliver_due(tracked)
        assert actual == expected
        assert [entry['belief_accepted'] for entry in actual] == expected_accepted[day]
        assert tracked.to_dict() == plain.to_dict()
        journal.barrier()
        journal.end(frame)
        if day == 1:
            saved = json.dumps(tracked.to_dict(), sort_keys=True)
            assert '_active_indexes' not in saved and '_changes' not in saved
            tracked = World.from_dict(json.loads(saved))
            journal = Journal(tracked)
        for w in (plain, tracked):
            w.date = crisis._date_after(w.date, 1)
    assert tracked.npc_state['reports']['a:ruler']['event:' + event['event_id']]['data']['facts']['threat'] == .1


def test_imported_equal_message_orders_keep_first_inserted_belief():
    plain = crisis_world()
    event = cs.add_event(plain, 'custom:reports', 'ra', ['a'])
    first = cr.queue_report(plain, event['event_id'], 'a:ruler', delay=0, observed_facts={'threat': .9})
    second = cr.queue_report(plain, event['event_id'], 'a:ruler', delay=0, observed_facts={'threat': .1})
    first['message_id'], second['message_id'] = 'z-first', 'a-second'
    plain.crisis_state['messages'] = {first['message_id']: first, second['message_id']: second}
    assert cr.validation_issues(plain) == []
    tracked = copy.deepcopy(plain)
    journal = Journal(tracked)
    actual = cr.deliver_due(tracked)
    assert actual == cr.deliver_due(plain)
    assert [entry['message_id'] for entry in actual] == ['z-first', 'a-second']
    assert [entry['belief_accepted'] for entry in actual] == [True, False]
    assert tracked.to_dict() == plain.to_dict()


def test_eligibility_refreshes_same_day_closure_and_cooldown_without_rng_draw():
    plain = crisis_world()
    event = cs.add_event(plain, 'custom:risk', 'ra', ['a'], risk_family_key='risk:ra')
    journal = Journal(plain)
    assert not crisis_risk._eligible(plain, 'risk', 'ra', {'custom:risk'})
    cs.close_event(plain, event['event_id'])
    assert crisis_risk._eligible(plain, 'risk', 'ra', {'custom:risk'})
    plain.crisis_state['cooldowns']['risk:ra'] = '1897-01-03'
    assert not crisis_risk._eligible(plain, 'risk', 'ra', {'custom:risk'})
    plain.date = '1897-01-03'
    assert crisis_risk._eligible(plain, 'risk', 'ra', {'custom:risk'})
    before = copy.deepcopy(plain.crisis_state['streams'])
    assert crisis_risk._sample(plain, crisis.create_event, 'custom:risk', 'risk', 'ra', 0, 1,
                              'ra', ['a'], []) is None
    assert plain.crisis_state['streams'] == before
