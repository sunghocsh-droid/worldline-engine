"""Temporary shallow copies borrow data without becoming fake live ancestors."""
import copy
from dataclasses import dataclass, field

from worldline.models import Country, World
from worldline.state_runtime import Journal, Record, version


@dataclass
class CustomRecord(Record):
    name: str = 'live'
    resources: dict = field(default_factory=lambda: {'grain': 10})


def test_record_shallow_copy_preserves_declared_and_custom_data_but_not_journal_metadata():
    record = CustomRecord()
    record.custom = {'note': ['original']}
    record._custom_flag = ['caller metadata']
    journal = Journal({'record': record})
    live = journal.root['record']
    original, root_version = version(live), version(journal.root)
    frame = journal.begin()
    candidate = copy.copy(live)
    assert type(candidate) is type(live)
    assert candidate.resources is live.resources
    assert candidate.custom is live.custom
    assert candidate._custom_flag is live._custom_flag
    assert not any(name in candidate.__dict__ for name in (
        '_tracker', '_parents', '_version', '_changes', '_active_indexes'))
    candidate.name = 'projection'
    candidate.resources = {'grain': 1}
    candidate.custom = {'note': ['replacement']}
    del candidate.name
    assert live.name == 'live'
    assert live.resources['grain'] == 10
    assert live.custom == {'note': ['original']}
    assert version(live) == original and version(journal.root) == root_version
    assert frame.states == {} and frame.keys == {} and journal.pending == {}
    journal.end(frame)


def test_record_shared_nested_mutation_still_tracks_only_live_original_and_rolls_back():
    world = World('1900-01-01', countries={'a': Country('A', resources={'grain': 10})})
    journal = Journal(world)
    live = world.countries['a']
    frame = journal.begin()
    projected_world, projected_country = copy.copy(world), copy.copy(live)
    assert projected_world.countries is world.countries
    assert projected_country.resources is live.resources
    projected_country.resources['grain'] -= 3
    assert live.resources['grain'] == 7
    assert id(live) in frame.states and id(world) in frame.states
    assert id(projected_world) not in frame.states and id(projected_country) not in frame.states
    assert journal.view(world, frame).countries['a'].resources['grain'] == 10
    journal.end(frame, rollback=True)
    assert live.resources['grain'] == 10


def test_tracked_dictionary_copy_is_plain_and_only_nested_mutations_touch_live_journal():
    journal = Journal({'live': {'resource': {'grain': 10}, 'status': 'active'}})
    live = journal.root['live']
    token = version(live)
    frame = journal.begin()
    candidate = copy.copy(live)
    assert type(candidate) is dict and candidate['resource'] is live['resource']
    candidate['status'] = 'temporary'
    candidate['new'] = {'projection': True}
    candidate.pop('status')
    assert live['status'] == 'active' and 'new' not in live
    assert version(live) == token and frame.states == {}
    candidate['resource']['grain'] -= 4
    assert live['resource']['grain'] == 6 and id(live) in frame.states
    assert id(candidate) not in frame.states
    journal.end(frame, rollback=True)
    assert live['resource']['grain'] == 10


def test_tracked_list_copy_is_plain_and_retains_nested_alias_rollback():
    journal = Journal([{'resource': {'grain': 10}}, 'original'])
    live = journal.root
    token = version(live)
    frame = journal.begin()
    candidate = copy.copy(live)
    assert type(candidate) is list and candidate[0] is live[0]
    candidate[1] = 'projection'
    candidate.append({'new': True})
    candidate.reverse()
    assert len(live) == 2 and live[1] == 'original'
    assert version(live) == token and frame.states == {}
    candidate[-1]['resource']['grain'] = 3
    assert live[0]['resource']['grain'] == 3
    assert id(live) in frame.states and id(candidate) not in frame.states
    journal.end(frame, rollback=True)
    assert live[0]['resource']['grain'] == 10


def test_shallow_projection_inserted_as_live_data_gets_instrumented_at_barrier():
    journal = Journal({'record': CustomRecord()})
    frame = journal.begin()
    candidate = copy.copy(journal.root['record'])
    candidate.name = 'new live alias'
    journal.root['new'] = candidate
    journal.barrier()
    assert candidate._tracker is journal
    candidate.resources['grain'] = 4
    assert journal.root['record'].resources is candidate.resources
    assert id(candidate) in frame.states and id(journal.root['record']) in frame.states
    journal.end(frame, rollback=True)
    assert 'new' not in journal.root and journal.root['record'].resources['grain'] == 10
