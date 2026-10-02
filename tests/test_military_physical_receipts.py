"""Cross-module conservation checks for replacement stock and transport loss."""
import copy
import pytest
from test_military_logistics import world, pair
from worldline import military_bridge as bridge, military_logistics as log, military_state as ms


def test_actual_sunk_transport_loses_cargo_once(world):
    pair(world, mode='sea', days=2)
    bridge.recruit(world, 'fleet', 'a', 100, 'r1')
    f = world.military_state['formations']['fleet']
    f.update(service_type='navy', mobility={'mode': 'sea', 'speed_km_day': 100, 'motorized': False}, equipment_state={'ships': {'quantity': 2, 'condition': 1, 'troop_capacity': 50}})
    log.add_convoy(world, 'c', 'a', ['fleet'], capacity=20)
    log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')
    result = {'id': 'naval-real', 'actor': 'a', 'region': 'r1', 'operation_id': None,
        'casualties': {}, 'equipment_losses': {'fleet': {'ships': 1}}, 'supply_consumption': {}, 'unit_changes': {}}
    bridge.apply_result(world, result)
    assert world.military_state['shipments']['s']['cargo']['food'] == 5
    assert world.military_state['shipments']['s']['losses']['food'] == 5
    saved = copy.deepcopy(world.military_state)
    bridge.apply_result(world, result)
    assert world.military_state == saved
    assert log.validate(world) == []


def test_reserve_transfer_preserves_quantity_and_wear(world):
    for key in ('field', 'reserve'):
        bridge.recruit(world, key, 'a', 50, 'r1')
    world.military_state['formations']['field']['equipment_state']['personal_weapons'] = {'quantity': 50, 'condition': .4}
    bridge.transfer_equipment(world, 'field', 'reserve', 'personal_weapons', 20, receipt_id='generation-one')
    assert world.military_state['formations']['field']['equipment_state']['personal_weapons']['quantity'] == 30
    assert world.military_state['formations']['reserve']['equipment_state']['personal_weapons'] == {'quantity': 20, 'condition': .4}
    with pytest.raises(ValueError): bridge.transfer_equipment(world, 'field', 'reserve', 'personal_weapons', 31, receipt_id='invent')


def test_equipment_generation_cannot_restore_condition_by_copying(world):
    for key in ('new', 'old'):
        bridge.recruit(world, key, 'a', 50, 'r1')
    world.military_state['formations']['new']['equipment_state']['personal_weapons'] = {'quantity': 10, 'condition': 1}
    world.military_state['formations']['old']['equipment_state']['personal_weapons'] = {'quantity': 10, 'condition': .2}
    bridge.transfer_equipment(world, 'new', 'old', 'personal_weapons', 10, receipt_id='average')
    assert world.military_state['formations']['old']['equipment_state']['personal_weapons']['condition'] == pytest.approx(.6)


def test_completed_route_does_not_keep_killing_stationary_siege_troops(world, monkeypatch):
    from worldline import military
    ms.add_actor(world, 'band', kind='regional_force', home_region='r1', manpower_pool=100000, treasury=200000)
    bridge.recruit(world, 'camp', 'band', 100000, 'r1')
    f = world.military_state['formations']['camp']
    f['fatigue'] = 1.
    f['supply_state']['stocks'] = {'food': 1000, 'medical': 1000}
    f['journey'] = {'arrival': world.date, 'destination': 'r1', 'legs': [], 'leg_index': 0, 'leg_progress_km': 0.}
    world.military_state['operations']['siege'] = {'status': 'in_progress', 'formations': ['camp']}
    results = []
    monkeypatch.setattr(bridge, 'apply_result', lambda w, result: results.append(copy.deepcopy(result)))
    military._upkeep(world)
    assert len(results) == 1  # Exposure/disease still apply; this keeps the audit active.
    assert results[0]['reason_trace']['rates']['march_attrition'] == 0
    assert results[0]['casualties']['camp']['march_attrition'] == 0
    assert results[0]['reason_trace']['rates']['exposure'] > 0
