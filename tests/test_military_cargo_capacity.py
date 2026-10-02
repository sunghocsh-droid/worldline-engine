"""An explicitly declared ship hold cannot be replaced by default capacity."""
import copy

import pytest

from tests.test_military_logistics import world, unit, next_day
from worldline import military_logistics as log
from worldline.models import World


def fleet(world, *, hold=25, crew=40, ships=4, condition=1.):
    f = unit(world, 'transport', service_type='navy', formation_type='naval',
             equipment_state={'ships': {'quantity': ships, 'condition': condition,
                                       'crew_per_ship': 10, 'cargo_capacity': hold}},
             mobility={'mode': 'sea', 'speed_km_day': 90., 'motorized': False},
             readiness=1.)
    f['manpower'] = crew
    f['combat_manpower'] = crew
    f['support_manpower'] = 0
    return f


def test_explicit_hold_limits_created_and_operational_capacity(world):
    f = fleet(world)
    convoy = log.add_convoy(world, 'cargo', 'a', ['transport'])
    assert convoy['capacity'] == 100
    assert log._convoy_capacity(world, convoy) == 100
    f['equipment_state']['ships']['quantity'] = 2
    assert log._convoy_capacity(world, convoy) == 50
    f['equipment_state']['ships']['condition'] = .5
    assert log._convoy_capacity(world, convoy) == 25


def test_undeclared_hold_keeps_existing_default(world):
    f = fleet(world)
    f['equipment_state']['ships'].pop('cargo_capacity')
    convoy = log.add_convoy(world, 'cargo', 'a', ['transport'])
    assert convoy['capacity'] == 400
    assert log._convoy_capacity(world, convoy) == 400


def test_claimed_convoy_capacity_cannot_exceed_explicit_physical_hold(world):
    fleet(world)
    with pytest.raises(ValueError, match='exceeds actual ship equipment'):
        log.add_convoy(world, 'cargo', 'a', ['transport'], capacity=101)
    assert world.military_state['convoys'] == {}


def test_crew_readiness_and_supply_limit_the_same_real_hold(world):
    f = fleet(world, crew=20)
    convoy = log.add_convoy(world, 'cargo', 'a', ['transport'])
    assert log._convoy_capacity(world, convoy) == 50
    f['readiness'] = .8
    f['supply_state']['last_ratio'] = .5
    assert log._convoy_capacity(world, convoy) == 20


@pytest.mark.parametrize('invalid', [-1, float('inf'), float('nan'), True, '25'])
def test_invalid_declared_hold_is_rejected_by_api_and_auditor(world, invalid):
    fleet(world, hold=invalid)
    with pytest.raises(ValueError, match='cargo capacity'):
        log.add_convoy(world, 'cargo', 'a', ['transport'])
    assert any('explicit ship cargo capacity' in issue for issue in log.validate(world))


def test_zero_hold_is_valid_equipment_but_cannot_generate_transport_capacity(world):
    fleet(world, hold=0)
    assert not log.validate(world)
    with pytest.raises(ValueError, match='exceeds actual ship equipment'):
        log.add_convoy(world, 'cargo', 'a', ['transport'])


def test_actual_cargo_dispatch_and_restore_keep_the_explicit_hold(world):
    fleet(world)
    log.add_link(world, 'l12', 'r1', 'r2', travel_days=2, mode='sea', throughput=1000)
    log.add_depot(world, 'home', 'a', 'r1', stocks={'food': 150.})
    log.add_depot(world, 'front', 'a', 'r2')
    convoy = log.add_convoy(world, 'cargo', 'a', ['transport'])
    with pytest.raises(ValueError, match='ship hold capacity'):
        log.dispatch(world, 'too-large', 'home', 'front', {'food': 100.001}, mode='sea', convoy='cargo')
    original_food = world.military_state['depots']['home']['stocks']['food']
    log.dispatch(world, 'cargo-100', 'home', 'front', {'food': 100.}, mode='sea', convoy='cargo')
    clone = World.from_dict(copy.deepcopy(world.to_dict()))
    assert clone.military_state['formations']['transport']['equipment_state']['ships']['cargo_capacity'] == 25
    for _ in range(12):
        next_day(world)
        next_day(clone)
    assert clone.to_dict() == world.to_dict()
    shipment = world.military_state['shipments']['cargo-100']
    assert shipment['status'] == 'delivered'
    assert world.military_state['depots']['front']['stocks']['food'] == 100.
    assert world.military_state['depots']['home']['stocks']['food'] + shipment['delivered']['food'] == original_food
    assert log._convoy_capacity(world, convoy) == 100


@pytest.mark.parametrize('ships,condition,attempt', [(1, 1., 50.), (4, .5, 60.)])
def test_new_loading_uses_remaining_hulls_not_original_convoy_capacity(world, ships, condition, attempt):
    f = fleet(world)
    log.add_link(world, 'l12', 'r1', 'r2', travel_days=2, mode='sea', throughput=1000)
    log.add_depot(world, 'home', 'a', 'r1', stocks={'food': 150.})
    log.add_depot(world, 'front', 'a', 'r2')
    convoy = log.add_convoy(world, 'cargo', 'a', ['transport'])
    f['equipment_state']['ships'].update(quantity=ships, condition=condition)
    opening = copy.deepcopy(world.to_dict())
    with pytest.raises(ValueError, match='ship hold capacity'):
        log.dispatch(world, 'too-large', 'home', 'front', {'food': attempt}, mode='sea', convoy='cargo')
    assert world.to_dict() == opening
    actual_hold = ships * condition * 25
    log.dispatch(world, 'fits', 'home', 'front', {'food': actual_hold}, mode='sea', convoy='cargo')
    assert world.military_state['depots']['home']['stocks']['food'] == 150 - actual_hold
    assert convoy['capacity'] == 100  # Historical assignment remains unchanged.


def test_low_readiness_slows_full_hold_without_inventing_a_smaller_hull(world):
    f = fleet(world)
    log.add_link(world, 'l12', 'r1', 'r2', travel_days=2, mode='sea', throughput=1000)
    log.add_depot(world, 'home', 'a', 'r1', stocks={'food': 100.})
    log.add_depot(world, 'front', 'a', 'r2')
    convoy = log.add_convoy(world, 'cargo', 'a', ['transport'])
    f['readiness'] = .25
    assert log._convoy_capacity(world, convoy) == 25
    log.dispatch(world, 'slow-full-hold', 'home', 'front', {'food': 100.}, mode='sea', convoy='cargo')
    assert world.military_state['shipments']['slow-full-hold']['cargo']['food'] == 100
