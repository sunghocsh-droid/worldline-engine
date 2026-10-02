"""Derived military active sets preserve history, ordering and daily semantics."""
import copy
import json
import random
import pytest

from worldline import military, military_state as ms, military_operations as ops, military_logistics as logistics
from worldline.military_active import items, active_operations, active_shipments, latest_by_group
from worldline.state_runtime import Journal, changes_since
from worldline.models import World
from worldline.persistence import tuples
from test_military_operations import world as operations_world, planned


def test_changes_log_nested_changes_deletion_and_rollback():
    journal = Journal({'cold': {'status': 'completed'}, 'hot': {'status': 'issued'}})
    records = journal.root
    token, changed = changes_since(records, None)
    assert changed is None
    records['hot']['status'] = 'in_progress'
    token, changed = changes_since(records, token)
    assert changed == {'hot'}
    frame = journal.begin()
    del records['cold']
    records['new'] = {'status': 'issued'}
    token, changed = changes_since(records, token)
    assert changed == {'cold', 'new'}
    journal.end(frame, rollback=True)
    token, changed = changes_since(records, token)
    assert changed is None
    assert set(records) == {'cold', 'hot'}


def test_bounded_change_log_rebuilds_without_losing_changes():
    journal = Journal({'hot': {'status': 'issued'}})
    records = journal.root
    token, _ = changes_since(records, None)
    for index in range(5000):
        records['hot']['elapsed_days'] = index
    assert len(records._changes) == records._changes.maxlen == 4096
    assert changes_since(records, token)[1] is None


def test_cold_operation_history_is_not_revisited_after_nested_hot_change():
    journal = Journal({**{str(i): {'id': str(i), 'status': 'completed'} for i in range(1000)},
                       'hot': {'id': 'hot', 'status': 'issued'}})
    records, inspected = journal.root, []
    def predicate(operation):
        inspected.append(operation['id'])
        return operation['status'] in ops.ACTIVE
    assert [key for key, _ in items(records, 'counted', predicate)] == ['hot']
    inspected.clear()
    records['hot']['status'] = 'paused'
    assert [key for key, _ in items(records, 'counted', predicate)] == ['hot']
    assert inspected == ['hot']
    inspected.clear()
    assert [key for key, _ in items(records, 'counted', predicate)] == ['hot']
    assert inspected == []
    assert len(records) == 1001


def test_plain_new_record_before_barrier_and_cache_rollback_are_exact():
    journal = Journal({'old': {'id': 'old', 'status': 'completed'}})
    records = journal.root
    assert active_operations(records) == []
    frame = journal.begin()
    record = {'id': 'new', 'status': 'issued'}
    records['new'] = record
    assert [op['id'] for op in active_operations(records)] == ['new']
    record['status'] = 'completed'
    assert active_operations(records) == []
    record['status'] = 'in_progress'
    journal.barrier()
    assert [op['id'] for op in active_operations(records)] == ['new']
    records['old']['status'] = 'issued'
    assert [op['id'] for op in active_operations(records)] == ['new', 'old']
    journal.end(frame, rollback=True)
    assert active_operations(records) == []
    assert 'new' not in records


def test_latest_group_index_preserves_order_and_isolates_other_worlds():
    source = {'older': {'group': 'one', 'order': ('1900-01-01', 2), 'accepted': True},
              'newer': {'group': 'one', 'order': ('1900-01-02', 1), 'accepted': True},
              'other': {'group': 'two', 'order': ('1900-01-03', 3), 'accepted': True}}
    left, right = Journal(copy.deepcopy(source)), Journal(copy.deepcopy(source))
    def latest(records):
        return latest_by_group(records, 'groups', lambda v: v['group'], lambda v: v['order'],
                               lambda v: v['accepted'], ['one'])
    assert latest(left.root)['one']['order'] == ('1900-01-02', 1)
    assert latest(right.root) == latest(source)
    frame = left.begin()
    left.root['newer']['accepted'] = False
    assert latest(left.root)['one']['order'] == ('1900-01-01', 2)
    assert latest(right.root)['one']['order'] == ('1900-01-02', 1)
    left.end(frame, rollback=True)
    assert latest(left.root) == latest(right.root) == latest(source)


def test_active_shipment_sets_include_disrupted_and_waiting_cargo():
    records = {status: {'id': status, 'status': status} for status in (
        'delivered', 'cancelled', 'lost', 'in_transit', 'disrupted', 'awaiting_target', 'awaiting_storage')}
    journal = Journal(copy.deepcopy(records))
    expected = ['awaiting_storage', 'awaiting_target', 'disrupted', 'in_transit']
    assert [key for key, _ in active_shipments(records)] == expected
    assert [key for key, _ in active_shipments(journal.root)] == expected
    journal.root['disrupted']['status'] = 'delivered'
    assert [key for key, _ in active_shipments(journal.root)] == [key for key in expected if key != 'disrupted']
    assert len(journal.root) == len(records)


def test_tracked_and_plain_daily_campaign_world_rng_reports_and_resume_equal():
    plain = operations_world.__wrapped__()
    plain.military_state['formations']['a-force']['supply_state']['stocks'] = {'food': 100., 'medical': 10.}
    plain.military_state['actors']['a']['stocks']['food'] = 100.
    planned(plain)
    ops.send_command(plain, 'march', 'a', delay=1)
    ops.queue_report(plain, 'march', 'a:military', {'threat': .9}, delay=14)
    logistics.add_depot(plain, 'home', 'a', 'ra', stocks={'food': 100.})
    logistics.add_depot(plain, 'front', 'a', 'ra2')
    logistics.dispatch(plain, 'cargo', 'home', 'front', {'food': 20.})
    tracked = copy.deepcopy(plain)
    journal = Journal(tracked)
    plain_rng, tracked_rng = random.Random(19), random.Random(19)
    for day in range(1, 41):
        frame = journal.begin()
        for world in (plain, tracked):
            if day == 3:
                ops.send_command(world, 'march', 'a', command={'intent': 'hold'}, delay=1)
                ops.queue_report(world, 'march', 'a:military', {'threat': .2}, delay=1)
            if day == 8:
                ops.send_command(world, 'march', 'a', command={'intent': 'continue'}, delay=1)
            previous = world.date
            world.date = ms.after(previous, 1)
            rng = plain_rng if world is plain else tracked_rng
            records = military.tick(world, {'days': 1, 'previous_date': previous, 'date': world.date}, rng)
            if world is plain:
                expected_records = records
            else:
                assert records == expected_records
        journal.barrier()
        journal.end(frame)
        assert json.dumps(tracked.to_dict(), sort_keys=True) == json.dumps(plain.to_dict(), sort_keys=True), day
        assert {key: tuples(value) for key, value in tracked.military_state['rng_streams'].items()} == {
            key: tuples(value) for key, value in plain.military_state['rng_streams'].items()}
        assert tracked_rng.getstate() == plain_rng.getstate()
        if day == 20:
            # Private caches/logs never enter exports; resumed indexing starts clean.
            saved = json.dumps(tracked.to_dict(), sort_keys=True)
            assert '_active_indexes' not in saved and '_changes' not in saved
            tracked = World.from_dict(json.loads(saved))
            journal = Journal(tracked)
    assert plain.military_state['operations']['march']['status'] == 'completed'
    assert plain.military_state['shipments']['cargo']['status'] == 'delivered'
    assert len(plain.military_state['commands']) == 5
    assert len(plain.military_state['logistics_receipts']) >= 42
    before = copy.deepcopy(tracked.to_dict())
    assert military.tick(tracked, {'days': 1}, tracked_rng) == []
    assert tracked.to_dict() == before


@pytest.mark.parametrize('kind', ('command', 'report'))
def test_imported_equal_observation_order_keeps_original_stable_delivery_order(kind):
    plain = operations_world.__wrapped__()
    planned(plain, 'hold')
    if kind == 'command':
        first = ops.send_command(plain, 'march', 'a', command={'intent': 'hold_fire'}, delay=0)
        second = ops.send_command(plain, 'march', 'a', command={'intent': 'execute'}, delay=0)
    else:
        first = ops.queue_report(plain, 'march', 'a:military', {'threat': .9}, delay=0)
        second = ops.queue_report(plain, 'march', 'a:military', {'threat': .1}, delay=0)
    first['id'], second['id'] = 'z-first-inserted', 'a-second-inserted'
    second['sequence'] = first['sequence']
    plain.military_state['commands'] = {first['id']: first, second['id']: second}
    # The existing schema accepts imported equal sequence/observation orders.
    assert ops.validation_issues(plain) == []
    original_due = [value for value in plain.military_state['commands'].values()
                    if value['status'] == 'queued' and value['expected_received_at'] <= plain.date]
    expected = [message['id'] for message in sorted(original_due,
        key=lambda message: (message['expected_received_at'], ops._observation_order(message)))]
    assert expected == ['z-first-inserted', 'a-second-inserted']
    tracked = copy.deepcopy(plain)
    journal = Journal(tracked)
    frame = journal.begin()
    plain_records, tracked_records = ops.deliver_due(plain), ops.deliver_due(tracked)
    assert [entry['message_id'] for entry in tracked_records] == expected
    assert [entry['belief_accepted'] for entry in tracked_records] == [True, False]
    assert plain_records == tracked_records
    assert plain.to_dict() == tracked.to_dict()
    if kind == 'command':
        assert tracked.military_state['operations']['march']['standing_intent'] == 'hold_fire'
    else:
        assert tracked.npc_state['reports']['a:military']['military-operation:march']['data']['facts'] == {'threat': .9}
    journal.end(frame)


def test_latest_equal_order_keeps_mapping_insertion_order_after_changes_and_rollback():
    source = {'z-first': {'group': 'one', 'order': 1, 'accepted': True},
              'a-second': {'group': 'one', 'order': 1, 'accepted': True}}
    journal = Journal(copy.deepcopy(source))
    def selected(records):
        return latest_by_group(records, 'ties', lambda value: value['group'], lambda value: value['order'],
                               lambda value: value['accepted'], ['one'])['one']
    assert selected(journal.root) is journal.root['z-first']
    frame = journal.begin()
    # Delete/reinsert changes stable max() tie ordering without changing either
    # cached descriptor. The fallback observes the actual current map order.
    first = journal.root.pop('z-first')
    journal.root['z-first'] = first
    assert selected(journal.root) is journal.root['a-second']
    journal.root['a-second']['order'] = 2
    assert selected(journal.root) is journal.root['a-second']
    journal.root['z-first']['order'] = 2
    assert selected(journal.root) is journal.root['a-second']
    journal.root['a-second']['accepted'] = False
    assert selected(journal.root) is journal.root['z-first']
    journal.end(frame, rollback=True)
    assert selected(journal.root) is journal.root['z-first']
    assert selected(source) == source['z-first']
