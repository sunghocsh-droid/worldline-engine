"""Physical transport/supply invariants, including real economic procurement."""
import copy
import json
import random

import pytest

from worldline.models import World, Country, Region
from worldline import economy, crisis, npc_state, military_state, military_logistics as log


@pytest.fixture
def world():
    w = World('1897-01-01', countries={'a': Country('Fictional A', population=3000, treasury=10000)},
        regions={key: Region(key, 'a', population=1000, x=i * 100,
            infrastructure={'roads': 50, 'ports': 50, 'railways': 50}) for i, key in enumerate(('r1', 'r2', 'r3'))})
    economy.initialize(w)
    economy.enable_stabilization(w)
    w.npc_state, w.schema_version = npc_state.initialize_state(), 3
    crisis.initialize(w, rng=random.Random(8))
    military_state.initialize(w, random.Random(9), {'import_armies': False})
    log.initialize(w)
    w.military_state['actors']['a']['stocks'] = {'food': 1000., 'ammunition': 100., 'medical': 10., 'fuel': 100.}
    for r in w.economy['regions'].values():
        for sector in ('agriculture', 'chemicals', 'heavy_industry', 'machinery'):
            r['stocks']['goods'][sector] = 1000.
    return w


def unit(world, key='f', region='r1', **fields):
    result = military_state.formation(key, 'a', 100, region, **fields)
    world.military_state['formations'][key] = result
    return result


def next_day(world, count=1):
    world.date = military_state.after(world.date, count)
    log.tick(world)


def pair(world, *, days=2, mode='road', throughput=1000):
    log.add_link(world, 'l12', 'r1', 'r2', travel_days=days, mode=mode, throughput=throughput)
    log.add_depot(world, 'home', 'a', 'r1', stocks={'food': 100.})
    log.add_depot(world, 'front', 'a', 'r2')


def test_no_implicit_route_or_coordinate_teleport(world):
    assert not log.route_quote(world, 'r1', 'r2', actor='a')['physical_feasibility']['feasible']
    assert world.military_state['links'] == {}


def test_explicit_rail_without_rail_infrastructure_is_unusable(world):
    world.regions['r2'].infrastructure['railways'] = 0
    log.add_link(world, 'rail', 'r1', 'r2', mode='rail')
    assert not log.route_quote(world, 'r1', 'r2', actor='a')['physical_feasibility']['feasible']


def test_ports_are_actual_loading_and_unloading_bottlenecks(world):
    log.add_link(world, 'sea', 'r1', 'r2', mode='sea')
    full = log.edge_capacity(world, 'sea')['capacity']
    world.regions['r2'].infrastructure['ports'] = 1
    assert log.edge_capacity(world, 'sea')['capacity'] < full
    world.regions['r2'].infrastructure['ports'] = 0
    assert not log.route_quote(world, 'r1', 'r2', mode='sea', actor='a')['physical_feasibility']['feasible']


def test_road_quality_weather_and_disruption_are_explained(world):
    log.add_link(world, 'road', 'r1', 'r2')
    original = log.edge_capacity(world, 'road')['capacity']
    world.military_state['links']['road'].update(seasonal_factor=.5, disruption=.5)
    actual = log.edge_capacity(world, 'road')
    assert actual['capacity'] == original * .25
    assert actual['reason_trace']


def test_foot_rate_cannot_exceed_actual_speed_or_progress_distance(world):
    f = unit(world)
    log.add_link(world, 'road', 'r1', 'r2', travel_days=1)
    quote = log.route_quote(world, 'r1', 'r2', actor='a', formation=f)
    assert quote['travel_days'] >= 5
    rate = log.movement_rate(world, f, quote['legs'][0])
    assert rate['speed_km_day'] <= f['mobility']['speed_km_day']
    assert rate['progress_fraction_per_day'] <= 20 / 100


def test_route_choices_are_stable_under_link_dictionary_insertion_order(world):
    log.add_link(world, 'z', 'r1', 'r2', travel_days=1)
    log.add_link(world, 'a', 'r1', 'r2', travel_days=1)
    first = log.route_quote(world, 'r1', 'r2', actor='a')
    world.military_state['links'] = dict(reversed(list(world.military_state['links'].items())))
    assert log.route_quote(world, 'r1', 'r2', actor='a') == first
    assert first['links'] == ['a']


def test_local_route_cache_is_detached_and_fresh_public_quote_reads_mutations(world):
    log.add_link(world, 'road', 'r1', 'r2')
    planner = log.RoutePlanner(world, 'a')
    q = planner.quote('r1', 'r2')
    q['legs'][0]['distance_km'] = 99999
    assert planner.quote('r1', 'r2')['distance_km'] == 100
    world.military_state['links']['road']['disruption'] = 1
    assert not log.route_quote(world, 'r1', 'r2', actor='a')['physical_feasibility']['feasible']


def test_foreign_transit_requires_actual_access_not_affection(world):
    world.countries['b'] = Country('B')
    world.regions['r2'].owner = 'b'
    log.add_link(world, 'road', 'r1', 'r2')
    assert not log.route_quote(world, 'r1', 'r2', actor='a')['physical_feasibility']['feasible']
    assert log.route_quote(world, 'r1', 'r2', actor='a', allow_hostile_destination=True)['physical_feasibility']['feasible']
    world.relations['a-b'] = {'a': 'a', 'b': 'b', 'status': 'allied', 'military_access': True}
    assert log.route_quote(world, 'r1', 'r2', actor='a')['physical_feasibility']['feasible']


def test_unit_and_cargo_reservations_share_one_finite_daily_capacity(world):
    log.add_link(world, 'road', 'r1', 'r2')
    cap = log.edge_capacity(world, 'road')['capacity']
    first = log.reserve_capacity(world, 'road', cap * .8, actor='a', reservation_id='unit')
    second = log.reserve_capacity(world, 'road', cap * .8, actor='a', reservation_id='cargo')
    assert first['reserved'] + second['reserved'] == pytest.approx(cap)
    assert second['ratio'] < first['ratio']
    assert log.reserve_capacity(world, 'road', cap * .8, actor='a', reservation_id='unit') == first
    assert log.validate(world) == []


def test_capacity_receipt_cannot_be_reused_for_different_request(world):
    log.add_link(world, 'road', 'r1', 'r2')
    log.reserve_capacity(world, 'road', 10, actor='a', reservation_id='x')
    with pytest.raises(ValueError):
        log.reserve_capacity(world, 'road', 20, actor='a', reservation_id='x')


def test_depot_initialization_transfers_real_actor_stock(world):
    before = world.military_state['actors']['a']['stocks']['food']
    log.add_depot(world, 'd', 'a', 'r1', stocks={'food': 50})
    assert world.military_state['actors']['a']['stocks']['food'] + world.military_state['depots']['d']['stocks']['food'] == before
    with pytest.raises(ValueError):
        log.add_depot(world, 'd2', 'a', 'r1', stocks={'food': before + 1})


def test_warehouse_cannot_appear_with_more_than_actual_storage_capacity(world):
    before = copy.deepcopy(world.military_state)
    with pytest.raises(ValueError):
        log.add_depot(world, 'd', 'a', 'r1', capacity=1, stocks={'food': 2})
    assert world.military_state == before


def test_local_unit_loading_is_conserved_and_idempotent(world):
    f = unit(world)
    log.add_depot(world, 'd', 'a', 'r1', stocks={'food': 50})
    result = log.transfer_to_formation(world, 'd', 'f', {'food': 10}, receipt_id='load')
    assert log.transfer_to_formation(world, 'd', 'f', {'food': 10}, receipt_id='load') == result
    assert f['supply_state']['stocks']['food'] == 10
    assert world.military_state['depots']['d']['stocks']['food'] == 40


def test_remote_unit_cannot_use_depot_instantly(world):
    unit(world, region='r2')
    log.add_depot(world, 'd', 'a', 'r1', stocks={'food': 50})
    with pytest.raises(ValueError):
        log.transfer_to_formation(world, 'd', 'f', {'food': 10}, receipt_id='load')


def test_goods_are_escrowed_until_real_cargo_arrival(world):
    pair(world)
    log.dispatch(world, 'cargo', 'home', 'front', {'food': 10})
    assert world.military_state['depots']['home']['stocks']['food'] == 90
    assert world.military_state['depots']['front']['stocks'] == {}
    next_day(world)
    assert world.military_state['depots']['front']['stocks'] == {}
    next_day(world)
    assert world.military_state['depots']['front']['stocks']['food'] == 10
    assert log.validate(world) == []


def test_multi_leg_cargo_cannot_skip_intermediate_regions(world):
    pair(world, days=1)
    log.add_link(world, 'l23', 'r2', 'r3', travel_days=1)
    log.add_depot(world, 'far', 'a', 'r3')
    log.dispatch(world, 'cargo', 'home', 'far', {'food': 10})
    next_day(world)
    assert world.military_state['shipments']['cargo']['location'] == 'r2'
    assert world.military_state['depots']['far']['stocks'] == {}
    next_day(world)
    assert world.military_state['depots']['far']['stocks']['food'] == 10


def test_insufficient_destination_storage_keeps_cargo_physically_present(world):
    pair(world, days=1)
    world.military_state['depots']['front']['capacity'] = 2
    log.dispatch(world, 'cargo', 'home', 'front', {'food': 10})
    next_day(world)
    item = world.military_state['shipments']['cargo']
    assert item['status'] == 'awaiting_storage'
    assert item['cargo']['food'] == 8
    assert item['delivered']['food'] == 2
    next_day(world)
    assert item['cargo']['food'] == 8
    assert log.validate(world) == []


def test_moving_target_cannot_receive_supplies_from_old_location(world):
    pair(world, days=1)
    f = unit(world, region='r2')
    log.dispatch(world, 'cargo', 'home', 'f', {'food': 10}, destination_kind='formation')
    f['location'] = 'r3'
    next_day(world)
    assert world.military_state['shipments']['cargo']['status'] == 'awaiting_target'
    assert f['supply_state']['stocks'] == {}


def test_supply_route_can_recover_after_disruption_without_permanent_penalty(world):
    pair(world, days=1)
    log.dispatch(world, 'cargo', 'home', 'front', {'food': 10})
    world.military_state['links']['l12']['disruption'] = 1
    next_day(world)
    assert world.military_state['shipments']['cargo']['status'] == 'disrupted'
    world.military_state['links']['l12']['disruption'] = 0
    next_day(world)
    assert world.military_state['shipments']['cargo']['status'] == 'delivered'


def test_repeated_same_day_tick_cannot_deliver_twice(world):
    pair(world, days=1)
    log.dispatch(world, 'cargo', 'home', 'front', {'food': 10})
    next_day(world)
    before = copy.deepcopy(world.military_state)
    assert log.tick(world) == []
    assert world.military_state == before


def naval(world, key='fleet', region='r1'):
    return unit(world, key, region, formation_type='naval', service_type='navy', mobility={'mode': 'sea', 'speed_km_day': 100, 'motorized': False},
        equipment_state={'ships': {'quantity': 1, 'condition': 1, 'standard': 'sailing'}})


def test_convoy_cannot_duplicate_real_ship_capacity(world):
    naval(world)
    log.add_convoy(world, 'c', 'a', ['fleet'])
    with pytest.raises(ValueError):
        log.add_convoy(world, 'c2', 'a', ['fleet'])


def test_zero_ships_and_imaginary_capacity_are_rejected(world):
    f = naval(world)
    with pytest.raises(ValueError):
        log.add_convoy(world, 'c', 'a', ['fleet'], capacity=101)
    f['equipment_state']['ships']['quantity'] = 0
    with pytest.raises(ValueError):
        log.add_convoy(world, 'c', 'a', ['fleet'])


def test_sea_cargo_requires_actual_idle_convoy_at_departure_port(world):
    pair(world, days=1, mode='sea')
    naval(world, region='r2')
    log.add_convoy(world, 'c', 'a', ['fleet'])
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')


def test_oversized_sea_cargo_cannot_fit_into_one_finite_ship(world):
    pair(world, days=1, mode='sea')
    naval(world)
    log.add_convoy(world, 'c', 'a', ['fleet'], capacity=5)
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')


def test_convoy_and_cargo_both_travel_to_real_port_before_reuse(world):
    pair(world, days=2, mode='sea')
    f = naval(world)
    log.add_convoy(world, 'c', 'a', ['fleet'])
    log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')
    next_day(world)
    assert f['location'] == 'r1'
    next_day(world)
    assert f['location'] == 'r2'
    assert world.military_state['convoys']['c']['shipment'] is None
    assert log.validate(world) == []


def test_real_supply_consumption_is_limited_and_not_repeated(world):
    f = unit(world)
    f['supply_state']['stocks']['food'] = 1
    old = 1
    first = log.consume_supply(world, 'f', {'food': .5}, receipt_id='eaten')
    assert log.consume_supply(world, 'f', {'food': .5}, receipt_id='eaten') == first
    assert f['supply_state']['stocks']['food'] == old - .5
    deficit = log.consume_supply(world, 'f', {'food': 2}, receipt_id='next')
    assert deficit['actual']['food'] == .5
    assert deficit['shortages']['food'] == 1.5


def test_medical_shortage_is_not_identical_to_starvation(world):
    f = unit(world)
    f['supply_state']['stocks']['food'] = 1
    result = log.consume_supply(world, 'f', {'food': .1, 'medical': .1}, receipt_id='ration')
    assert result['ratio'] == 1
    assert result['component_ratios']['medical'] == 0
    assert result['shortages']['medical'] == .1


def test_food_civilian_credit_does_not_duplicate_distinct_extra_meals(world):
    f = unit(world)
    f['supply_state']['stocks']['food'] = 1
    daily = f['manpower'] * log.FOOD_PER_PERSON_DAY
    log.consume_supply(world, 'f', {'food': daily}, receipt_id='meal1')
    log.consume_supply(world, 'f', {'food': daily}, receipt_id='meal2')
    assert world.economy['regions']['r1']['military_food_credit'] == pytest.approx(daily)


def test_procurement_consumes_real_economy_goods_and_funding_once(world):
    log.add_depot(world, 'd', 'a', 'r1')
    before_goods = world.economy['regions']['r1']['stocks']['goods']['agriculture']
    before_cash = world.countries['a'].treasury
    result = log.procure_depot(world, 'd', 'food', 10, receipt_id='purchase')
    assert log.procure_depot(world, 'd', 'food', 10, receipt_id='purchase') == result
    assert world.military_state['depots']['d']['stocks']['food'] == 10
    assert world.economy['regions']['r1']['stocks']['goods']['agriculture'] == before_goods - 10
    assert world.countries['a'].treasury == before_cash - 10


def test_fuel_comes_from_real_energy_pool_with_matching_input_receipt(world):
    log.add_depot(world, 'd', 'a', 'r1')
    r = world.economy['regions']['r1']
    initial = r['stocks']['energy']
    r['inputs'] = {'opening_energy': initial, 'energy_produced': 0, 'energy_consumed': 0,
                   'opening_raw_materials': r['stocks']['raw_materials'], 'mining_produced': 0,
                   'raw_materials_consumed': 0, 'closing_energy': initial}
    log.procure_depot(world, 'd', 'fuel', 5, receipt_id='fuel')
    r = world.economy['regions']['r1']
    assert r['stocks']['energy'] == initial - 5
    assert r['inputs']['emergency_energy_used'] == 5
    assert r['inputs']['closing_energy'] == initial - 5
    assert world.military_state['depots']['d']['stocks']['fuel'] == 5


def test_logistics_audit_detects_duplicated_cargo_and_bad_dates(world):
    pair(world, days=1)
    log.dispatch(world, 'cargo', 'home', 'front', {'food': 10})
    world.military_state['shipments']['cargo']['cargo']['food'] += 1
    assert any('conservation' in error for error in log.validate(world))


def test_world_save_load_preserves_transit_progress_and_next_delivery(world):
    pair(world, days=3)
    log.dispatch(world, 'cargo', 'home', 'front', {'food': 10})
    next_day(world)
    clone = World.from_dict(json.loads(json.dumps(world.to_dict())))
    next_day(world)
    next_day(clone)
    next_day(world)
    next_day(clone)
    assert json.dumps(world.to_dict(), sort_keys=True) == json.dumps(clone.to_dict(), sort_keys=True)
    assert log.validate(clone) == []


def test_depot_transfer_receipt_is_immutable_and_detached_from_live_inventory(world):
    pair(world)
    log.dispatch(world, 's', 'home', 'front', {'food': 10})
    assert world.military_state['logistics_receipts']['depot:home']['quantity']['food'] == 100
    assert world.military_state['depots']['home']['stocks']['food'] == 90


def test_auditor_preserves_previous_resource_receipts(world):
    pair(world)
    previous = World.from_dict(world.to_dict())
    world.military_state['logistics_receipts']['depot:home']['quantity']['food'] -= 1
    assert any('immutable' in error for error in log.validate(world, previous))


def test_cargo_loss_is_finite_idempotent_and_keeps_conservation(world):
    pair(world, days=2)
    log.dispatch(world, 's', 'home', 'front', {'food': 10})
    first = log.lose_cargo(world, 's', {'food': 3}, cause='externally adjudicated interception', receipt_id='hit')
    assert log.lose_cargo(world, 's', {'food': 3}, cause='externally adjudicated interception', receipt_id='hit') == first
    with pytest.raises(ValueError):
        log.lose_cargo(world, 's', {'food': 8}, cause='duplicate loss', receipt_id='hit2')
    next_day(world, 1)
    next_day(world, 1)
    assert world.military_state['depots']['front']['stocks']['food'] == 7
    assert world.military_state['shipments']['s']['losses']['food'] == 3
    assert log.validate(world) == []


def test_audit_checks_actual_edge_identity_not_only_connected_region_names(world):
    pair(world)
    log.add_link(world, 'other', 'r1', 'r3')
    log.dispatch(world, 's', 'home', 'front', {'food': 10})
    world.military_state['shipments']['s']['route']['legs'][0]['link'] = 'other'
    assert any('unrelated' in error for error in log.validate(world))


def test_escort_cannot_teleport_to_departure_convoy(world):
    pair(world, mode='sea')
    naval(world)
    naval(world, 'escort', 'r2')
    log.add_convoy(world, 'c', 'a', ['fleet'], escorts=['escort'])
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')
    assert world.military_state['depots']['home']['stocks']['food'] == 100


def test_convoy_cannot_duplicate_port_blockade_commitment(world):
    pair(world, mode='sea')
    f = naval(world)
    from worldline.military_operations import plan
    plan(world, 'block', 'a', ['fleet'], 'blockade', target='r1', issuer='a')
    f['current_order'] = 'block'
    world.military_state['operations']['block']['status'] = 'in_progress'
    log.add_convoy(world, 'c', 'a', ['fleet'])
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')


def test_ships_cannot_follow_land_supply_route(world):
    pair(world)
    naval(world)
    log.add_convoy(world, 'c', 'a', ['fleet'])
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'front', {'food': 10}, convoy='c')


def test_mixed_land_sea_cargo_requires_physical_depot_transshipment(world):
    pair(world)
    log.add_link(world, 'sea', 'r2', 'r3', mode='sea')
    log.add_depot(world, 'end', 'a', 'r3')
    naval(world)
    log.add_convoy(world, 'c', 'a', ['fleet'])
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'end', {'food': 10}, mode='any', convoy='c')


def standard(world, key='rifle', family='rifle-A', parts='parts-A', quantity=100):
    from worldline.military_innovation import register_standard
    register_standard(world, key, 'personal_weapons', ammunition_family=family, parts_family=parts)
    f = world.military_state['formations'].get('f') or unit(world)
    f['equipment_state'][key] = {'quantity': quantity, 'condition': 1, 'standard_id': key,
                                'equipment_type': 'personal_weapons'}
    return f


def test_generic_ammunition_cannot_supply_declared_incompatible_family(world):
    f = standard(world)
    f['supply_state']['stocks']['ammunition'] = 100
    result = log.consume_supply(world, 'f', {'ammunition': 10}, receipt_id='battle')
    assert result['actual']['ammunition:rifle-A'] == 0
    assert result['shortages']['ammunition:rifle-A'] == 10
    assert f['supply_state']['stocks']['ammunition'] == 100


def test_mixed_weapon_families_divide_required_ammunition_without_conversion(world):
    f = standard(world, quantity=75)
    standard(world, 'other', 'rifle-B', 'parts-B', quantity=25)
    f['supply_state']['stocks'].update({'ammunition:rifle-A': 20, 'ammunition:rifle-B': 1})
    result = log.consume_supply(world, 'f', {'ammunition': 20}, receipt_id='battle')
    assert result['requested'] == {'ammunition:rifle-A': 15, 'ammunition:rifle-B': 5}
    assert result['actual'] == {'ammunition:rifle-A': 15, 'ammunition:rifle-B': 1}
    assert result['shortages'] == {'ammunition:rifle-B': 4}
    assert log.explain(world, 'f')['equipment_compatibility']['ammunition_compatible'] is False


def test_parts_require_declared_family_not_generic_or_other_parts(world):
    f = standard(world)
    f['supply_state']['requirements']['spare_parts'] = 2
    f['supply_state']['stocks'].update({'spare_parts': 100, 'spare_parts:parts-B': 10})
    result = log.consume_supply(world, 'f', receipt_id='maintenance')
    assert result['actual']['spare_parts:parts-A'] == 0
    assert result['shortages']['spare_parts:parts-A'] == 2
    assert f['supply_state']['stocks']['spare_parts'] == 100


def test_family_acquisition_uses_finite_real_recipe_once(world):
    standard(world)
    log.add_depot(world, 'd', 'a', 'r1')
    before = world.economy['regions']['r1']['stocks']['goods']['heavy_industry']
    result = log.procure_depot(world, 'd', 'ammunition:rifle-A', 10, receipt_id='family')
    assert result['commodity'] == 'ammunition:rifle-A'
    assert world.economy['regions']['r1']['stocks']['goods']['heavy_industry'] == before - 4
    assert world.military_state['depots']['d']['stocks'] == {'ammunition:rifle-A': 10}


def test_unknown_family_cannot_be_invented_by_stock_procurement(world):
    log.add_depot(world, 'd', 'a', 'r1')
    with pytest.raises(ValueError):
        log.procure_depot(world, 'd', 'ammunition:imaginary', 10, receipt_id='unknown')


def test_explicit_incompatible_ammo_request_does_not_bypass_matching(world):
    f = standard(world)
    f['supply_state']['stocks']['ammunition:rifle-B'] = 100
    result = log.consume_supply(world, 'f', {'ammunition:rifle-B': 10}, receipt_id='bad-family')
    assert result['actual']['ammunition:rifle-B'] == 0
    assert f['supply_state']['stocks']['ammunition:rifle-B'] == 100


def test_captured_depot_is_not_available_to_previous_owner(world):
    pair(world)
    unit(world)
    world.military_state['controls']['r1'] = 'hostile-authority'
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'front', {'food': 10})
    with pytest.raises(ValueError):
        log.transfer_to_formation(world, 'home', 'f', {'food': 10}, receipt_id='captured')


def test_existing_field_camp_receives_supplies_without_free_regional_control(world):
    pair(world, days=1)
    f = unit(world, region='r2')
    world.military_state['controls']['r2'] = 'hostile-authority'
    log.dispatch(world, 's', 'home', 'f', {'food': 10}, destination_kind='formation')
    next_day(world)
    assert f['supply_state']['stocks']['food'] == 10
    assert world.military_state['controls']['r2'] == 'hostile-authority'
    assert world.military_state['depots']['front']['stocks'] == {}
    with pytest.raises(ValueError):
        log.dispatch(world, 'warehouse', 'home', 'front', {'food': 10})


def test_field_camp_exception_does_not_open_hostile_intermediate_region(world):
    pair(world)
    log.add_link(world, 'next', 'r2', 'r3')
    unit(world, region='r3')
    world.military_state['controls']['r2'] = 'hostile-authority'
    with pytest.raises(ValueError):
        log.dispatch(world, 's', 'home', 'f', {'food': 10}, destination_kind='formation')


def test_actual_control_receipt_shape_is_used_after_combat(world):
    pair(world, days=1)
    world.military_state['controls']['r2'] = {'region': 'r2', 'controller': 'a', 'contested': False}
    log.dispatch(world, 's', 'home', 'front', {'food': 10})
    next_day(world)
    assert world.military_state['depots']['front']['stocks']['food'] == 10


def test_fuel_and_motorized_label_cannot_create_nonexistent_vehicles(world):
    f = unit(world, mobility={'mode': 'motorized', 'speed_km_day': 100, 'motorized': True})
    f['supply_state']['stocks']['fuel'] = 100
    log.add_link(world, 'road', 'r1', 'r2', travel_days=1)
    q = log.route_quote(world, 'r1', 'r2', actor='a', formation=f)
    assert q['travel_days'] >= 5
    actual = log.movement_rate(world, f, q['legs'][0])
    assert actual['speed_km_day'] <= 20
    assert log.transport_quote(world, f)['physical_seats'] == 0
    assert 'fuel' not in log.requirements(world, f)


def test_one_truck_cannot_move_entire_field_force_at_motor_speed(world):
    f = unit(world, mobility={'mode': 'motorized', 'speed_km_day': 100, 'motorized': True})
    f['equipment_state']['transport'] = {'quantity': 1, 'condition': 1, 'troop_capacity': 20}
    f['supply_state']['stocks']['fuel'] = 100
    assert log.transport_quote(world, f)['speed_limit_km_day'] == 20
    assert log.transport_quote(world, f)['physical_seats'] == 20
    f['equipment_state']['transport']['quantity'] = 5
    assert log.transport_quote(world, f)['speed_limit_km_day'] == 100


def test_enough_trucks_without_fuel_fall_back_to_walking_not_motor_speed(world):
    f = unit(world, mobility={'mode': 'motorized', 'speed_km_day': 100, 'motorized': True})
    f['equipment_state']['transport'] = {'quantity': 5, 'condition': 1, 'troop_capacity': 20}
    assert log.transport_quote(world, f)['speed_limit_km_day'] == 20
    f['supply_state']['stocks']['fuel'] = .005
    assert log.transport_quote(world, f)['speed_limit_km_day'] == 100


def test_batch_route_quotes_do_not_alias_different_physical_transports(world):
    f = unit(world, mobility={'mode': 'motorized', 'speed_km_day': 100, 'motorized': True})
    g = unit(world, 'g', mobility={'mode': 'motorized', 'speed_km_day': 100, 'motorized': True})
    g['equipment_state']['transport'] = {'quantity': 5, 'condition': 1, 'troop_capacity': 20}
    g['supply_state']['stocks']['fuel'] = .005
    log.add_link(world, 'road', 'r1', 'r2', travel_days=1)
    planner = log.RoutePlanner(world, 'a')
    assert planner.quote('r1', 'r2', formation=f)['travel_days'] >= 5
    assert planner.quote('r1', 'r2', formation=g)['travel_days'] == 1


def test_route_estimate_applies_bad_condition_to_full_marching_duration(world):
    f = unit(world)
    log.add_link(world, 'road', 'r1', 'r2', travel_days=1, condition=.5)
    q = log.route_quote(world, 'r1', 'r2', actor='a', formation=f)
    assert q['travel_days'] == 10
    assert log.movement_rate(world, f, q['legs'][0])['speed_km_day'] <= 10


def test_mounted_force_needs_enough_actual_horses(world):
    f = unit(world, mobility={'mode': 'horse', 'speed_km_day': 60, 'motorized': False})
    f['equipment_state']['horses'] = {'quantity': 20, 'condition': 1}
    assert log.transport_quote(world, f)['speed_limit_km_day'] == 20
    f['equipment_state']['horses']['quantity'] = 100
    assert log.transport_quote(world, f)['speed_limit_km_day'] == 60


def test_uncrewed_ships_cannot_keep_shipping_after_actual_losses(world):
    pair(world, mode='sea', days=2)
    f = naval(world)
    log.add_convoy(world, 'c', 'a', ['fleet'])
    log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')
    f['manpower'] = f['combat_manpower'] = f['support_manpower'] = 0
    next_day(world)
    assert world.military_state['shipments']['s']['leg_progress'] == 0
    assert f['location'] == 'r1'


def test_convoy_records_each_real_transport_and_escort_leg_arrival(world):
    pair(world, mode='sea', days=2)
    fleet = naval(world)
    escort = naval(world, 'escort')
    log.add_convoy(world, 'c', 'a', ['fleet'], escorts=['escort'])
    from worldline.military_operations import plan, send_command
    mission = plan(world, 'escort-order', 'a', ['escort'], 'escort', 'r2', params={'convoy': 'c'}, issuer='a')
    send_command(world, mission['id'], 'a', delay=0)
    from worldline.military_operations import tick as operations_tick
    previous = world.date
    world.date = military_state.after(world.date, 1)
    operations_tick(world, {'date': world.date, 'previous_date': previous, 'days': 1})
    assert world.military_state['operations'][mission['id']]['status'] == 'in_progress'
    log.dispatch(world, 's', 'home', 'front', {'food': 10}, mode='sea', convoy='c')
    next_day(world)
    assert world.military_state['movement_receipts'] == {}
    next_day(world)
    for key in ('fleet', 'escort'):
        receipt = world.military_state['movement_receipts']['convoy-movement:s:' + key + ':0']
        assert receipt['formation'] == key
        assert receipt['origin'] == 'r1' and receipt['destination'] == 'r2'
        assert receipt['elapsed_days'] == 2
        assert receipt['departure'] == '1897-01-02'
        assert receipt['link'] == 'l12' and receipt['mode'] == 'sea'
    assert fleet['location'] == escort['location'] == 'r2'


def test_imported_legacy_navy_convoy_arrival_is_accepted_by_actual_sqlite_engine(tmp_path):
    from worldline.models import Army
    from worldline.engine import Engine
    w = World('1897-01-01', countries={'a': Country('Fictional A', population=2000, treasury=10000)},
        regions={key: Region(key, 'a', population=1000, x=i * 100,
            infrastructure={'ports': 50, 'roads': 50}) for i, key in enumerate(('r1', 'r2'))},
        armies={'fleet': Army('a', 'r1', 20)})
    economy.initialize(w)
    economy.enable_stabilization(w)
    from worldline import npc
    npc.initialize(w)
    for r in w.economy['regions'].values():
        r['stocks']['goods']['agriculture'] = 100.
    engine = Engine.create(tmp_path / 'fleet.sqlite', w, seed=81)
    try:
        engine.enable_crisis()
        engine.enable_military({'initial_equipment': {'fleet': {'ships': {'quantity': 1, 'condition': 1, 'standard': 'sailing'}}},
            'initial_supply': {'fleet': {'food': .1}}})
        with engine._transaction():
            f = engine._world.military_state['formations']['fleet']
            f['formation_type'], f['service_type'] = 'naval', 'navy'
            f['mobility'] = {'mode': 'sea', 'speed_km_day': 100, 'motorized': False}
            engine._event('military_scenario_parameters', {'basis': 'finite scenario fleet'})
        engine.add_supply_link('sea', 'r1', 'r2', mode='sea', travel_days=2)
        engine.add_supply_depot('home', 'a', 'r1')
        engine.add_supply_depot('front', 'a', 'r2')
        engine.procure_supply('home', 'food', 10, receipt_id='food')
        engine.add_transport_convoy('c', 'a', ['fleet'])
        engine.dispatch_supply('s', 'home', 'front', {'food': 10}, mode='sea', convoy='c')
        engine.advance(2, 'day')
        result = engine.true_state
        assert result.military_state['shipments']['s']['status'] == 'delivered'
        assert result.armies['fleet'].region == result.military_state['formations']['fleet']['location'] == 'r2'
        assert result.military_state['movement_receipts']['fleet']['elapsed_days'] == 2
        engine.save(tmp_path / 'save.json')
    finally:
        engine.close()
    loaded = Engine.load(tmp_path / 'save.json', tmp_path / 'restored.sqlite')
    try:
        assert loaded.true_state.to_dict() == result.to_dict()
    finally:
        loaded.close()


def completed_journey(formation, date='1897-01-01'):
    return {'arrival': date, 'destination': formation['location'], 'leg_index': 1,
        'legs': [{'from': 'r1', 'to': formation['location']}], 'leg_progress_km': 0.}


def test_completed_march_evidence_does_not_block_actual_depot_supply(world):
    pair(world)
    f = unit(world)
    f['journey'] = completed_journey(f)
    assert log.is_stationary(world, f)
    log.transfer_to_formation(world, 'home', 'f', {'food': 10}, receipt_id='arrived-supply')
    assert f['supply_state']['stocks']['food'] == 10
    assert world.military_state['depots']['home']['stocks']['food'] == 90
    assert f['journey']['arrival'] == world.date  # Historical evidence is retained.


def test_arrived_siege_formation_receives_real_field_cargo(world):
    pair(world, days=1)
    f = unit(world, region='r2')
    f['journey'] = completed_journey(f)
    world.military_state['controls']['r2'] = 'hostile-authority'
    log.dispatch(world, 'arrived-camp', 'home', 'f', {'food': 10}, destination_kind='formation')
    next_day(world)
    assert f['supply_state']['stocks']['food'] == 10
    assert world.military_state['shipments']['arrived-camp']['status'] == 'delivered'
    assert world.military_state['controls']['r2'] == 'hostile-authority'


@pytest.mark.parametrize('override', [
    {'arrival': None}, {'arrival': '1897-01-02'}, {'leg_index': 0},
    {'destination': 'r2'}, {'leg_progress_km': 1.}])
def test_incomplete_future_or_inconsistent_journey_still_blocks_local_supply(world, override):
    pair(world)
    f = unit(world)
    f['journey'] = {**completed_journey(f), **override}
    assert not log.is_stationary(world, f)
    before = copy.deepcopy(world.military_state['depots']['home']['stocks'])
    with pytest.raises(ValueError):
        log.transfer_to_formation(world, 'home', 'f', {'food': 10}, receipt_id='not-arrived')
    assert world.military_state['depots']['home']['stocks'] == before
    assert f['supply_state']['stocks'] == {}


def test_completed_journey_accepts_actual_co_located_equipment_reserve(world):
    from worldline import military_bridge
    f = unit(world)
    f['journey'] = completed_journey(f)
    world.military_state['actors']['a']['equipment_reserve']['personal_weapons'] = 10
    military_bridge.issue_equipment(world, 'a', 'f', 'personal_weapons', 10)
    assert f['equipment_state']['personal_weapons']['quantity'] == 10
    assert world.military_state['actors']['a']['equipment_reserve']['personal_weapons'] == 0
