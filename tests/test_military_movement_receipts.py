"""Real SQLite movement accepts physical transport, but rejects invented receipts."""
import copy

import pytest

from worldline import Engine, economy, npc, military
from worldline.models import World, Country, Region, Army


def game(tmp_path, mode, *, cargo=False, multi_leg=False, travel_days=1):
    regions = ('home', 'middle', 'front') if multi_leg else ('home', 'front')
    opening = World('1897-01-01', countries={'a': Country('Synthetic A', population=1000 * len(regions), treasury=10000)},
        regions={key: Region(key, 'a', population=1000, x=index * 100,
            infrastructure={'ports': 50, 'roads': 50, 'railways': 50}) for index, key in enumerate(regions)},
        armies={'force': Army('a', 'home', 100, speed_km_day=20)})
    economy.initialize(opening)
    economy.enable_stabilization(opening)
    npc.initialize(opening)
    for r in opening.economy['regions'].values():
        r['stocks']['goods']['agriculture'] = 100.
    engine = Engine.create(tmp_path / (mode + ('-cargo' if cargo else '') + '.sqlite'), opening, seed=81)
    engine.enable_crisis({'settings': {'auto_enabled': False}})
    equipment = {'ships': {'quantity': 1, 'condition': 1., 'standard': 'sailing', 'troop_capacity': 200}} if mode == 'sea' else {}
    engine.enable_military({'initial_equipment': {'force': equipment}, 'initial_supply': {'force': {'food': 1., 'medical': 1.}}})
    if mode == 'sea':
        with engine._transaction():
            f = engine._world.military_state['formations']['force']
            f['formation_type'], f['service_type'] = 'naval', 'navy'
            f['mobility'] = {'mode': 'sea', 'speed_km_day': 20, 'motorized': False}
            engine._event('military_scenario_parameters', {'basis': 'explicit finite scenario sailing fleet and crew'})
    for index, (origin, destination) in enumerate(zip(regions, regions[1:])):
        engine.add_supply_link('leg-' + str(index), origin, destination, mode=mode, distance_km=100,
            throughput=20000, travel_days=travel_days)
    if cargo:
        engine.add_supply_depot('source', 'a', 'home')
        engine.add_supply_depot('destination', 'a', 'front')
        engine.procure_supply('source', 'food', 10, receipt_id='real-food')
        engine.add_transport_convoy('convoy', 'a', ['force'])
        engine.dispatch_supply('shipment', 'source', 'destination', {'food': 10}, mode='sea', convoy='convoy')
    else:
        engine.plan_operation('real-transfer', 'a', ['force'], 'move', 'front', issuer='a')
        engine.command_operation('real-transfer', 'a', delay=0)
    return engine


@pytest.mark.parametrize('mode', ['rail', 'river', 'sea'])
def test_real_rapid_transport_uses_link_duration_and_preserves_raw_calendar(tmp_path, mode):
    engine = game(tmp_path, mode)
    try:
        engine.advance(1)
        assert engine.true_state.armies['force'].region == 'home'
        engine.advance(1)
        assert engine.true_state.armies['force'].region == 'home'
        engine.advance(1)
        world = engine.true_state
        receipt = world.military_state['movement_receipts']['force']
        assert world.armies['force'].region == world.military_state['formations']['force']['location'] == 'front'
        assert receipt['mode'] == mode and receipt['distance_km'] == 100
        assert receipt['departure'] == '1897-01-02' and receipt['arrival'] == '1897-01-04'
        assert receipt['elapsed_days'] == 2 and receipt['minimum_days'] == 1
        saved = engine.save(tmp_path / (mode + '.json'))
        restored = Engine.load(saved, tmp_path / ('restored-' + mode + '.sqlite'))
        try:
            assert restored.true_state.to_dict() == world.to_dict()
            assert restored.rng.getstate() == engine.rng.getstate()
        finally:
            restored.close()
    finally:
        engine.close()


def test_normal_foot_movement_retains_its_existing_speed_bound(tmp_path):
    engine = game(tmp_path, 'road')
    try:
        engine.advance(10)
        receipt = engine.true_state.military_state['movement_receipts']['force']
        assert receipt['minimum_days'] == 3
        assert receipt['elapsed_days'] >= receipt['minimum_days']
        assert engine.true_state.armies['force'].region == 'front'
    finally:
        engine.close()


def test_one_tick_cargo_voyage_has_real_one_day_elapsed_without_added_day(tmp_path):
    engine = game(tmp_path, 'sea', cargo=True)
    try:
        engine.advance(1)
        w = engine.true_state
        assert w.military_state['shipments']['shipment']['status'] == 'delivered'
        assert w.armies['force'].region == 'front'
        receipt = w.military_state['movement_receipts']['force']
        assert receipt['departure'] == '1897-01-01' and receipt['arrival'] == '1897-01-02'
        assert receipt['elapsed_days'] == receipt['minimum_days'] == 1
    finally:
        engine.close()


def test_each_completed_convoy_leg_matches_its_actual_current_node(tmp_path):
    engine = game(tmp_path, 'sea', cargo=True, multi_leg=True)
    try:
        engine.advance(1)
        first = engine.true_state.military_state['movement_receipts']['force']
        assert engine.true_state.armies['force'].region == 'middle'
        assert first['origin'] == 'home' and first['destination'] == 'middle' and first['elapsed_days'] == 1
        engine.advance(1)
        second = engine.true_state.military_state['movement_receipts']['force']
        assert engine.true_state.armies['force'].region == 'front'
        assert second['origin'] == 'middle' and second['destination'] == 'front' and second['elapsed_days'] == 1
        assert first['date'] < second['date'] and second['link'] == 'leg-1'
    finally:
        engine.close()


@pytest.mark.parametrize('corruption', ['zero_distance', 'stale_date', 'elapsed_mismatch', 'unrelated_link',
    'no_reservation', 'missing_operation', 'no_actual_arrival'])
def test_raw_forged_operation_receipt_cannot_authorize_legacy_relocation(tmp_path, monkeypatch, corruption):
    engine = game(tmp_path, 'rail')
    try:
        engine.advance(2)  # A real received order and real partial physical movement.
        previous, rng, ledger = engine.true_state.to_dict(), engine.rng.getstate(), engine.ledger()
        original = military._legacy_locations
        def forge(world):
            s = world.military_state
            receipts = [r for r in s['movement_receipts'].values() if r.get('date') == world.date and r.get('formation') == 'force']
            assert receipts
            receipt = receipts[-1]
            if corruption == 'zero_distance':
                receipt['distance_km'] = 0
            elif corruption == 'stale_date':
                receipt['date'] = '1897-01-03'
            elif corruption == 'elapsed_mismatch':
                receipt['elapsed_days'] = 50
            elif corruption == 'unrelated_link':
                receipt['link'] = 'no-such-rail'
            elif corruption == 'no_reservation':
                s['transport_reservations'][world.date] = {}
            elif corruption == 'missing_operation':
                receipt['operation_id'] = 'nonexistent-operation'
            else:
                s['outbox'] = [r for r in s['outbox'] if r['kind'] != 'formation_arrived']
            return original(world)
        monkeypatch.setattr(military, '_legacy_locations', forge)
        with pytest.raises(ValueError, match='actual|physical|calendar|route|transport'):
            engine.advance(1)
        assert engine.true_state.to_dict() == previous and engine.rng.getstate() == rng and engine.ledger() == ledger
        monkeypatch.setattr(military, '_legacy_locations', original)
        engine.advance(1)
        assert engine.true_state.armies['force'].region == 'front'
    finally:
        engine.close()


def test_stale_finished_convoy_cannot_supply_a_new_same_day_relocation_receipt(tmp_path):
    engine = game(tmp_path, 'sea', cargo=True)
    try:
        engine.advance(1)
        w = engine.true_state
        w.date = '1897-01-03'
        w.armies['force'].region = 'home'
        for receipt in w.military_state['movement_receipts'].values():
            receipt['date'] = w.date
            receipt['elapsed_days'] = 2
        with pytest.raises(ValueError, match='physically completed today'):
            military._legacy_locations(w)
    finally:
        engine.close()


def test_past_actual_link_duration_is_not_recalculated_from_current_disruption(tmp_path):
    engine = game(tmp_path, 'rail')
    try:
        engine.advance(2)
        original = military._legacy_locations
        def deteriorate(world):
            world.military_state['links']['leg-0']['condition'] = .01
            return original(world)
        from unittest.mock import patch
        with patch.object(military, '_legacy_locations', deteriorate):
            engine.advance(1)
        assert engine.true_state.armies['force'].region == 'front'
        assert engine.true_state.military_state['movement_receipts']['force']['elapsed_days'] == 2
    finally:
        engine.close()
