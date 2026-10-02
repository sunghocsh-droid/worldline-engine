"""Fictional, finite-resource military campaigns using the actual SQLite engine.

The research controller repeats explicit operational objectives. It is not a
new NPC military AI, calendar event script or automatic victory condition.
"""
import argparse
import copy
import csv
import gzip
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path

from . import economy, projects
from .audit import Auditor, AuditError
from .crisis_benchmark import campaign_world as political_world, campaign_config, source_hashes
from .engine import Engine, encode
from .models import Army
from .military_logistics import FOOD_PER_PERSON_DAY, is_stationary, requirements

SEED = 1731
SCENARIOS = {'preindustrial_border': 'preindustrial', 'industrial_front': 'industrial',
             'expeditionary_sea': 'early_industrial'}
INITIAL_MANPOWER = {'preindustrial_border': 500, 'industrial_front': 1000,
                   'expeditionary_sea': 600}
ORDER_DELAY = {'preindustrial_border': 7, 'industrial_front': 1, 'expeditionary_sea': 3}
SUPPLY_DAYS = {'preindustrial_border': 60, 'industrial_front': 60, 'expeditionary_sea': 120}
NAVAL_UNITS = {'b-transport', 'b-transport-navy', 'b-invasion-fleet', 'c-fleet'}


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def scenario_world(scenario):
    if scenario not in SCENARIOS:
        raise ValueError('Unknown fictional military benchmark scenario')
    world = political_world()
    world.scenario_id = 'fictional-military-' + scenario
    profile = SCENARIOS[scenario]
    rank = ('preindustrial', 'early_industrial', 'industrial').index(profile)
    world.economy = None
    # Every soldier and crew member is present before the labour ledger is built.
    for army in world.armies.values():
        army.personnel = INITIAL_MANPOWER[scenario]
    if scenario == 'preindustrial_border':
        world.armies['army-b'].personnel = 300
        world.armies['b-cavalry'] = Army('b', 'rb', 200)
        world.armies['b-transport'] = Army('b', 'rb', 30)
    if scenario == 'expeditionary_sea':
        world.armies['b-transport'] = Army('b', 'rb', 30)
        world.armies['b-transport-navy'] = Army('b', 'rb', 30)
        world.armies['b-invasion-fleet'] = Army('b', 'rb', 80)
        world.armies['c-fleet'] = Army('c', 'rc', 80)
    if scenario in ('preindustrial_border', 'expeditionary_sea'):
        world.relations['b-d'] = {'a': 'b', 'b': 'd', 'status': 'allied',
            'military_access': True, 'trade_openness': .7, 'trust': .6}
    infrastructure = (8, 25, 60)[rank]
    overrides = {region: {'infrastructure': {key: 0 if key == 'railways' and rank == 0 else infrastructure
            for key in projects.INFRA},
        'education': {'literacy': (.12, .3, .65)[rank], 'primary': (.1, .25, .55)[rank],
            'secondary': (.02, .12, .35)[rank], 'technical': (.005, .06, .2)[rank],
            'university': (.001, .02, .08)[rank]},
        'demography': {'urbanization': (.12, .25, .5)[rank]}}
        for region in world.regions}
    economy.initialize(world, profile, overrides)
    economy.enable_stabilization(world)
    for index, country in enumerate(('a', 'b', 'c', 'd')):
        partner = ('a', 'b', 'c', 'd')[(index + 1) % 4]
        economy.add_trade(world, 'trade-' + country, country, partner,
            'light_industry' if index % 2 else 'mining', 60, 'sea')
    Auditor().check(world)
    return world


def military_config(world, scenario):
    initial_equipment, initial_supply = {}, {}
    for key, army in world.armies.items():
        naval = key in NAVAL_UNITS
        transport = key.startswith('b-transport')
        firearms = (scenario == 'industrial_front' or scenario == 'expeditionary_sea' and army.owner != 'd'
                    or scenario == 'preindustrial_border' and key in ('army-b', 'army-c'))
        artillery = 0 if transport else 2 if naval else 10 if scenario == 'industrial_front' else 2 if firearms else 0
        initial_equipment[key] = ({'ships': {'quantity': 4 if transport else 8,
            'condition': 1., 'standard': 'fictional sailing fleet', 'troop_capacity': 100,
            'cargo_capacity': 25, 'crew_per_ship': 5 if transport else 10,
            'uses_ammunition': not transport}, 'artillery': {'quantity': artillery, 'condition': 1., 'standard': 'fictional ship guns'}}
            if naval else {'personal_weapons': {'quantity': army.personnel, 'condition': 1.,
                'standard': 'fictional scenario arms',
                'uses_ammunition': firearms},
                'artillery': {'quantity': artillery,
                'condition': 1., 'standard': 'fictional scenario artillery'}})
        if key == 'b-cavalry':
            initial_equipment[key]['horses'] = {'quantity': army.personnel, 'condition': 1.,
                'standard': 'fictional riding horses'}
        days = max(120 if naval else 0, SUPPLY_DAYS[scenario])
        battle_ammo = (army.personnel * .002 if firearms and not naval else 0) + artillery * .03
        initial_supply[key] = {'food': army.personnel * FOOD_PER_PERSON_DAY * days,
            'ammunition': battle_ammo * 4 + (artillery * .05 * days if scenario == 'industrial_front' else 0),
            'medical': army.personnel * .000005 * days}
        if key == 'b-cavalry':
            initial_supply[key]['fodder'] = army.personnel * FOOD_PER_PERSON_DAY * days
    fortresses = {} if scenario == 'expeditionary_sea' else {
        'rc': {'actor': 'c', 'strength': .8 if scenario == 'industrial_front' else .6,
               'food': world.armies['army-c'].personnel * FOOD_PER_PERSON_DAY * 30,
               'garrison': ['army-c']}}
    return {'initial_equipment': initial_equipment, 'initial_supply': initial_supply,
            'initial_fortresses': fortresses}


def _desired_stocks(world, formation, scenario):
    days = max(120 if formation['id'] in NAVAL_UNITS else 0, SUPPLY_DAYS[scenario])
    result = requirements(world, formation, days=days)
    equipment = formation['equipment_state']
    battle = (formation['manpower'] * .002
        if equipment.get('personal_weapons', {}).get('uses_ammunition') else 0)
    battle += equipment.get('artillery', {}).get('quantity', 0) * .03
    result['ammunition'] = result.get('ammunition', 0) + battle * 4
    return {key: value for key, value in result.items() if value > 1e-12}


def setup_engine(path, scenario, *, seed=SEED, military_enabled=True, validation_mode='verification'):
    world = scenario_world(scenario)
    engine = Engine.create(path, world, seed=seed, validation_mode=validation_mode)
    try:
        engine.enable_crisis(campaign_config())
        if not military_enabled:
            return engine
        engine.enable_military(military_config(world, scenario))
        # Explicit finite initial assets; these are not policy-generated supplies.
        with engine._transaction():
            s = engine._world.military_state
            for key in NAVAL_UNITS:
                if key in s['formations']:
                    s['formations'][key]['service_type'] = 'navy'
                    s['formations'][key]['formation_type'] = 'naval'
                    s['formations'][key]['mobility'] = {'mode': 'sea', 'speed_km_day': 90., 'motorized': False}
            if 'b-cavalry' in s['formations']:
                s['formations']['b-cavalry']['mobility'] = {'mode': 'horse', 'speed_km_day': 35., 'motorized': False}
            if scenario == 'industrial_front':
                for f in s['formations'].values():
                    # Explicit scenario exercise/maintenance demand, not an engine coefficient.
                    f['supply_state']['requirements']['ammunition'] = .05 * f['equipment_state'].get('artillery', {}).get('quantity', 0)
            buffers = {}
            for country in ('a', 'b', 'c', 'd'):
                stock = {}
                for f in s['formations'].values():
                    if f['actor'] == country:
                        for commodity, amount in _desired_stocks(engine._world, f, scenario).items():
                            stock[commodity] = stock.get(commodity, 0) + amount
                s['actors'][country]['stocks'].update(stock)
                buffers[country] = stock
            engine._event('military_scenario_parameters', {'scenario': scenario,
                'basis': 'finite separate formation rations, fortress reserve and home buffer; manpower charged before economy initialization',
                'initial_manpower': {key: f['manpower'] for key, f in s['formations'].items()},
                'initial_equipment': military_config(world, scenario)['initial_equipment'],
                'formation_stocks': {key: f['supply_state']['stocks'] for key, f in s['formations'].items()},
                'fortress_reserves': s['fortresses'], 'home_buffers': buffers,
                'order_delay': ORDER_DELAY[scenario]})
        mode = 'sea' if scenario == 'expeditionary_sea' else 'road'
        for origin, destination in (('ra', 'rb'), ('rb', 'rc'), ('rc', 'rd'), ('rd', 'ra'), ('rb', 'rd')):
            link_mode = 'rail' if scenario == 'industrial_front' and (origin, destination) == ('rb', 'rc') else mode
            engine.add_supply_link(origin + '-' + destination, origin, destination, mode=link_mode,
                throughput=20 if scenario == 'expeditionary_sea' and (origin, destination) == ('rc', 'rd') else 2500 if mode == 'sea' else 6000,
                travel_days=5 if mode == 'sea' else 3 if scenario == 'preindustrial_border' else 1 if link_mode == 'rail' else 3)
        if scenario == 'preindustrial_border':
            engine.add_supply_link('sea:rb-rd', 'rb', 'rd', mode='sea', throughput=100, travel_days=5)
        for country in ('a', 'b', 'c', 'd'):
            engine.add_supply_depot('home:' + country, country, 'r' + country, stocks=buffers[country])
        if scenario in ('preindustrial_border', 'expeditionary_sea'):
            engine.add_supply_depot('forward:b', 'b', 'rd')
            engine.add_transport_convoy('b-cargo', 'b', ['b-transport'], capacity=100)
        if scenario == 'expeditionary_sea':
            engine.add_transport_convoy('b-naval-cargo', 'b', ['b-transport-navy'], capacity=100)
        return engine
    except BaseException:
        engine.close()
        raise


def _try(actions, engine, kind, callback):
    try:
        result = callback()
        actions.append({'date': engine.true_state.date, 'kind': kind, 'status': 'executed'})
        return result
    except AuditError:
        raise
    except ValueError as exc:
        actions.append({'date': engine.true_state.date, 'kind': kind, 'status': 'constrained', 'reason': str(exc)})
        return None


def campaign_orders(engine, scenario, period, actions):
    """Explicit benchmark orders, bounded by the very same public execution APIs."""
    world = engine.true_state
    s = world.military_state
    if s is None:
        return
    if scenario == 'industrial_front':
        _innovation_orders(engine, period, actions)
    _replacement_orders(engine, scenario, period, actions)
    _supply_orders(engine, scenario, period, actions)
    if scenario in ('preindustrial_border', 'expeditionary_sea'):
        _convoy_orders(engine, scenario, period, actions)
    world, s = engine.true_state, engine.true_state.military_state
    if scenario == 'expeditionary_sea':
        fleet = s['formations']['c-fleet']
        if fleet['manpower'] > 0 and not fleet.get('current_order') and is_stationary(world, fleet):
            _plan_and_send(engine, actions, f'blockade:{period}', 'c', ['c-fleet'], 'blockade', 'rc',
                {'opponent': 'b', 'targets': ['b'], 'duration_days': 90}, ORDER_DELAY[scenario])
        own = s['formations']['b-invasion-fleet']
        if own['location'] != 'rb' and not own.get('current_order') and is_stationary(world, own):
            if (own['manpower'] > 0 and own['equipment_state'].get('ships', {}).get('quantity', 0) > 0
                and fleet['manpower'] > 0 and fleet['equipment_state'].get('ships', {}).get('quantity', 0) > 0
                and own['location'] == fleet['location'] and is_stationary(world, fleet)):
                _plan_and_send(engine, actions, f'naval-contest:{period}', 'b', [own['id']],
                    'naval_attack', fleet['location'], {'opponent': 'c', 'defenders': [fleet['id']],
                    'duration_days': 30, 'war_aim': 'sea_control'}, ORDER_DELAY[scenario])
            else:
                actions.append({'date': world.date, 'kind': 'naval_contest', 'status': 'constrained',
                    'reason': 'No actual live co-located opposing fleets; no battle is fabricated'})
    ids = ['army-b', 'b-invasion-fleet'] if scenario == 'expeditionary_sea' and s['formations']['army-b']['location'] == 'rb' else ['army-b']
    if scenario == 'preindustrial_border':
        ids += ['b-cavalry']
    if any(s['formations'][key]['manpower'] <= 0 or s['formations'][key].get('current_order') for key in ids):
        return
    kind = ('amphibious' if len(ids) > 1 else 'attack') if scenario == 'expeditionary_sea' else 'siege'
    _plan_and_send(engine, actions, 'campaign-operation:' + str(period), 'b', ids, kind, 'rc',
        {'opponent': 'c', 'war_aim': 'regional_security', 'duration_days': 90,
         'investment_days': 14 if kind == 'siege' else 1}, ORDER_DELAY[scenario])


def _plan_and_send(engine, actions, key, actor, ids, kind, target, params, delay):
    result = _try(actions, engine, 'plan:' + kind, lambda: engine.plan_operation(key, actor, ids,
        kind, target=target, issuer=actor, params=params))
    if result is not None:
        _try(actions, engine, 'communicate_order', lambda: engine.command_operation(key, actor, delay=delay))
    return result


def _transit(s, destination, commodity, kind='formation'):
    return math.fsum(item['cargo'].get(commodity, 0) for item in s['shipments'].values()
        if item['destination_kind'] == kind and item['destination'] == destination
        and item['status'] not in ('delivered', 'cancelled', 'lost'))


def _supply_orders(engine, scenario, period, actions):
    world, s = engine.true_state, engine.true_state.military_state
    # One paid procurement request per country/commodity, then explicit deliveries.
    needed_home = {}
    for key, f in sorted(s['formations'].items()):
        if f['manpower'] <= 0:
            continue
        country, home = f['actor'], 'home:' + f['actor']
        for commodity, desired in _desired_stocks(world, f, scenario).items():
            transit = _transit(s, key, commodity)
            needed = max(0., desired - f['supply_state']['stocks'].get(commodity, 0) - transit)
            needed_home[(home, commodity)] = needed_home.get((home, commodity), 0) + needed
    for (home, commodity), need in sorted(needed_home.items()):
        s = engine.true_state.military_state
        available = s['depots'][home]['stocks'].get(commodity, 0)
        # Existing forward/escrow inventory remains real stock; don't procure it twice.
        if home == 'home:b' and 'forward:b' in s['depots']:
            available += s['depots']['forward:b']['stocks'].get(commodity, 0) + _transit(s, 'forward:b', commodity, 'depot')
        if need > available + 1e-9:
            _try(actions, engine, 'procure:' + commodity, lambda: engine.procure_supply(home, commodity,
                need - available, receipt_id=f'bench:{period}:{home}:{commodity}', allow_emergency=True))
    for key in sorted(engine.true_state.military_state['formations']):
        world = engine.true_state
        s, f = world.military_state, world.military_state['formations'][key]
        if f['manpower'] <= 0 or not is_stationary(world, f):
            continue
        depot = 'home:' + f['actor'] if f['location'] == 'r' + f['actor'] else 'forward:b' if f['actor'] == 'b' and f['location'] == 'rd' and 'forward:b' in s['depots'] else None
        cargo = {commodity: min(s['depots'][depot]['stocks'].get(commodity, 0),
            max(0., desired - f['supply_state']['stocks'].get(commodity, 0)))
            for commodity, desired in _desired_stocks(world, f, scenario).items()} if depot else {}
        cargo = {k: v for k, v in cargo.items() if v > 1e-9}
        if cargo:
            _try(actions, engine, 'local_supply', lambda: engine.load_formation_supplies(depot, key, cargo,
                receipt_id=f'load:{period}:{key}'))
        if depot is not None or scenario == 'expeditionary_sea':
            continue
        origin = 'forward:b' if scenario == 'preindustrial_border' and f['actor'] == 'b' else 'home:' + f['actor']
        cargo = {commodity: min(s['depots'][origin]['stocks'].get(commodity, 0), max(0., desired
                - f['supply_state']['stocks'].get(commodity, 0) - _transit(s, key, commodity)))
            for commodity, desired in _desired_stocks(world, f, scenario).items()}
        cargo = {k: v for k, v in cargo.items() if v > 1e-9}
        if cargo:
            _try(actions, engine, 'field_supply', lambda: engine.dispatch_supply(f'field:{period}:{key}',
                origin, key, cargo, destination_kind='formation'))


def _convoy_orders(engine, scenario, period, actions):
    for convoy_id in sorted(engine.true_state.military_state['convoys']):
        world = engine.true_state
        s, convoy = world.military_state, world.military_state['convoys'][convoy_id]
        fleet = s['formations'][convoy['formations'][0]]
        if fleet['manpower'] <= 0 or fleet.get('current_order') or not is_stationary(world, fleet):
            continue
        if any(v.get('convoy') == convoy_id and v['status'] not in ('delivered', 'cancelled', 'lost') for v in s['shipments'].values()):
            continue
        target_id = 'b-invasion-fleet' if convoy_id == 'b-naval-cargo' else 'army-b'
        target = s['formations'][target_id]
        if fleet['location'] == 'rb':
            cargo = {}
            target_ids = ['army-b', 'b-cavalry'] if scenario == 'preindustrial_border' else [target_id]
            for item in target_ids:
                f = s['formations'][item]
                if f['manpower'] <= 0:
                    continue
                for commodity, desired in _desired_stocks(world, f, scenario).items():
                    missing = max(0., desired - f['supply_state']['stocks'].get(commodity, 0) - _transit(s, item, commodity))
                    cargo[commodity] = cargo.get(commodity, 0) + missing
            cargo = {k: min(s['depots']['home:b']['stocks'].get(k, 0), max(0., v
                - s['depots']['forward:b']['stocks'].get(k, 0) - _transit(s, 'forward:b', k, 'depot'))) for k, v in cargo.items()}
            cargo = {k: v for k, v in cargo.items() if v > 1e-9}
            if cargo:
                _try(actions, engine, 'convoy_to_forward', lambda: engine.dispatch_supply(
                    f'convoy-forward:{period}:{convoy_id}', 'home:b', 'forward:b', cargo, mode='sea', convoy=convoy_id))
        elif fleet['location'] == 'rd' and scenario == 'expeditionary_sea' and target['manpower'] > 0 and target['location'] != 'rb' and is_stationary(world, target):
            cargo = {commodity: min(s['depots']['forward:b']['stocks'].get(commodity, 0), max(0., desired
                - target['supply_state']['stocks'].get(commodity, 0) - _transit(s, target_id, commodity)))
                for commodity, desired in _desired_stocks(world, target, scenario).items()}
            cargo = {k: v for k, v in cargo.items() if v > 1e-9}
            if cargo:
                _try(actions, engine, 'convoy_to_field', lambda: engine.dispatch_supply(
                    f'convoy-field:{period}:{convoy_id}', 'forward:b', target_id, cargo,
                    destination_kind='formation', mode='sea', convoy=convoy_id))
            else:
                _plan_and_send(engine, actions, f'convoy-return:{period}:{convoy_id}', 'b', [fleet['id']],
                    'withdraw', 'rb', {}, ORDER_DELAY[scenario])
        else:
            _plan_and_send(engine, actions, f'convoy-return:{period}:{convoy_id}', 'b', [fleet['id']],
                'withdraw', 'rb', {}, ORDER_DELAY[scenario])


def _innovation_orders(engine, period, actions):
    from .military_innovation import STAGES
    s = engine.true_state.military_state
    key = 'benchmark-staff-training'
    if key not in s['innovations']:
        _try(actions, engine, 'innovation_proposal', lambda: engine.propose_military_innovation(
            key, 'a', 'ra', 'staff_planning', proposer='a:education', observers=['a:ruler', 'a:military'],
            formations=['army-a'], complexity=.2))
    p = engine.true_state.military_state['innovations'].get(key)
    if p is None:
        return
    completed = len(p['completed_stages'])
    if completed >= len(STAGES) - 1:
        return
    stage = STAGES[completed + 1]
    if stage not in p['stage_projects']:
        _try(actions, engine, 'fund_innovation:' + stage,
            lambda: engine.advance_military_innovation(key, stage, allow_emergency=True))


def _replacement_orders(engine, scenario, period, actions):
    for key in sorted(engine.true_state.military_state['replacements']):
        s = engine.true_state.military_state
        cohort = s['replacements'][key]
        f = s['formations'][cohort['cohort']]
        armed_type = bool(s['formations'][cohort['recipient']]['equipment_state'].get('personal_weapons', {}).get('uses_ammunition', False))
        production = 'replacement-arms:' + key
        if f['manpower'] > 0 and cohort['status'] != 'incorporated' and production not in engine.true_state.projects:
            _try(actions, engine, 'produce_replacement_weapons', lambda: engine.produce_military_equipment(
                production, f['actor'], f['origin_region'], 'personal_weapons', cohort['manpower'],
                duration_days=90, uses_ammunition=armed_type, allow_emergency=True))
            s = engine.true_state.military_state
            cohort = s['replacements'][key]
            f = s['formations'][cohort['cohort']]
        if cohort['status'] != 'ready' or f['manpower'] <= 0 or f.get('current_order'):
            continue
        weapons = f['equipment_state'].get('personal_weapons', {}).get('quantity', 0)
        needed = max(0, f['combat_manpower'] - int(weapons))
        reserve = s['actors'][f['actor']]['equipment_reserve'].get('personal_weapons:firearm' if armed_type else 'personal_weapons', 0)
        if needed and f['location'] == f['origin_region'] and reserve >= needed:
            _try(actions, engine, 'issue_replacement_arms', lambda: engine.issue_military_equipment(
                f['actor'], f['id'], 'personal_weapons', needed, uses_ammunition=armed_type))
            s = engine.true_state.military_state
            f = s['formations'][cohort['cohort']]
            weapons = f['equipment_state'].get('personal_weapons', {}).get('quantity', 0)
        recipient = s['formations'][cohort['recipient']]
        if recipient['manpower'] <= 0 or not is_stationary(engine.true_state, recipient):
            continue
        if f['location'] == recipient['location']:
            _try(actions, engine, 'incorporate_replacements', lambda: engine.incorporate_replacements(key))
        elif weapons >= f['combat_manpower']:
            operation = 'replacement-march:' + key
            if operation not in s['operations']:
                ids = [f['id']]
                params = {'recipient_formation': recipient['id']}
                if scenario == 'expeditionary_sea':
                    free_fleets = [v['id'] for v in s['formations'].values() if v['id'].startswith('b-transport')
                        and v['manpower'] > 0 and v['location'] == f['location'] and not v.get('current_order')
                        and is_stationary(engine.true_state, v) and not any(v['id'] in s['convoys'].get(sh.get('convoy'), {}).get('formations', [])
                            for sh in s['shipments'].values() if sh['status'] not in ('delivered', 'cancelled', 'lost'))]
                    if not free_fleets:
                        actions.append({'date': engine.true_state.date, 'kind': 'replacement_sea_transport',
                            'status': 'constrained', 'reason': 'No actual free co-located transport fleet'})
                        continue
                    ids += [sorted(free_fleets)[0]]
                    params['transport_mode'] = 'sea'
                result = _try(actions, engine, 'replacement_march', lambda: engine.plan_operation(
                    operation, f['actor'], ids, 'reinforce', target=recipient['location'], issuer=f['actor'], params=params))
                if result is not None:
                    _try(actions, engine, 'communicate_replacement_march',
                        lambda: engine.command_operation(operation, f['actor'], delay=2))
    s = engine.true_state.military_state
    targets = {'army-b': 300, 'b-cavalry': 200} if scenario == 'preindustrial_border' else {'army-b': INITIAL_MANPOWER[scenario]}
    for recipient_id, initial in targets.items():
        s = engine.true_state.military_state
        recipient = s['formations'][recipient_id]
        unfinished = [v for v in s['replacements'].values() if v['recipient'] == recipient_id and v['status'] != 'incorporated'
            and s['formations'][v['cohort']]['manpower'] > 0]
        missing = max(0, initial - recipient['manpower'])
        if not missing or unfinished or recipient['manpower'] <= 0:
            continue
        key = f'benchmark-replacement:{period}:{recipient_id}'
        count = min(max(1, initial // 20), missing)
        result = _try(actions, engine, 'train_replacements', lambda: engine.train_replacements(
            key, recipient_id, count, duration_days=120, allow_emergency=True))
        if result is not None:
            _try(actions, engine, 'produce_replacement_weapons', lambda: engine.produce_military_equipment(
                'replacement-arms:' + key, 'b', 'rb', 'personal_weapons', count,
                duration_days=90, uses_ammunition=bool(recipient['equipment_state'].get('personal_weapons', {}).get('uses_ammunition', False)),
                allow_emergency=True))


def measure(engine, period):
    world, ledger_count = engine.true_state, engine.db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]
    s = world.military_state
    countries = {}
    for key, country in world.countries.items():
        e = world.economy['countries'][key]
        countries[key] = {'population': country.population, 'gdp': country.gdp,
            'treasury': country.treasury, 'debt': country.debt, 'arrears': e['credit']['arrears'],
            'creditworthiness': e['credit']['creditworthiness'], 'distress_state': e['credit']['distress_state'],
            'military_labour': e['labour']['military'], 'exports': e['exports'], 'imports': e['imports'],
            'war_losses': sum(world.economy['regions'][r]['demography']['war_losses']
                for r in economy.owned_regions(world, key))}
    losses = Counter()
    if s:
        for receipt in s['loss_receipts'].values():
            for causes in receipt['result'].get('casualties', {}).values():
                losses.update(causes)
    return {'month': period, 'date': world.date, 'countries': countries, 'ledger_entries': ledger_count,
        'npc_memories': sum(len(v) for v in world.npc_state['memories'].values()),
        'npc_received_reports': sum(len(v) for v in world.npc_state['reports'].values()),
        'crisis_events': len(world.crisis_state['events']),
        'military': None if s is None else {'formations': len(s['formations']),
            'surviving_manpower': sum(f['manpower'] for f in s['formations'].values()),
            'supply_ratios': {k: f['supply_state']['last_ratio'] for k, f in s['formations'].items()},
            'losses_by_cause': dict(losses), 'loss_receipts': len(s['loss_receipts']),
            'operations': len(s['operations']), 'active_operations': sum(o['status'] in
                ('in_progress', 'received', 'issued', 'planned', 'paused', 'disrupted') for o in s['operations'].values()),
            'shipments': len(s['shipments']), 'delivered_shipments': sum(v['status'] == 'delivered' for v in s['shipments'].values()),
            'procurement_cost': sum(r['cost'] for r in s['procurement_receipts'].values()),
            'replacements': copy.deepcopy(s['replacements']), 'diplomatic_hooks': len(s['diplomatic_hooks']),
            'politics': copy.deepcopy(s['politics'])}}


def logical_result(engine):
    return {'world': engine.true_state.to_dict(), 'rng': engine.rng.getstate(), 'ledger': engine.ledger()}


def run_campaign(directory, scenario, months=36, *, seed=SEED, military_enabled=True,
                 checkpoint_month=None, restore=False, progress=None, validation_mode='verification'):
    if type(months) is not int or months <= 0 or checkpoint_month is not None and not 0 < checkpoint_month < months:
        raise ValueError('Positive campaign duration and internal checkpoint month required')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    opening_sources, started = source_hashes(), time.perf_counter()
    engine = setup_engine(directory / 'game.sqlite', scenario, seed=seed, military_enabled=military_enabled,
                          validation_mode=validation_mode)
    actions, rows, timings = [], [measure(engine, 0)], []
    try:
        for period in range(1, months + 1):
            if source_hashes() != opening_sources:
                raise RuntimeError('Campaign source changed during execution')
            tick_start = time.perf_counter()
            if military_enabled:
                campaign_orders(engine, scenario, period, actions)
            engine.advance(1, 'month')
            timings.append({'month': period, 'seconds': time.perf_counter() - tick_start})
            rows.append(measure(engine, period))
            if checkpoint_month == period:
                engine.save(directory / 'midpoint.json')
                if restore:
                    engine.close()
                    engine = Engine.load(directory / 'midpoint.json', directory / 'continued.sqlite',
                                         validation_mode=validation_mode)
            if progress:
                progress(rows[-1], timings[-1])
        if source_hashes() != opening_sources:
            raise RuntimeError('Campaign source changed before final evidence export')
        final = logical_result(engine)
        result = {'military_benchmark_schema': 1, 'scenario': scenario, 'months': months, 'seed': seed,
            'military_enabled': military_enabled, 'source_sha256': opening_sources,
            'validation_mode': engine.validation_mode,
            'method': 'Actual SQLite Engine; ' + ('daily full Auditor' if engine.validation_mode == 'verification'
                else 'daily local/incremental invariants and monthly full Auditor') + '; ordinary dated orders and finite paid procurement',
            'total_seconds': time.perf_counter() - started, 'month_timings': timings,
            'rows': rows, 'actions': actions, 'final_logical_sha256': digest(final), 'final_logical': final,
            'database_bytes': engine.path.stat().st_size}
        with gzip.open(directory / 'result.json.gz', 'wt', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False)
        flat = []
        for row in rows:
            for country, values in row['countries'].items():
                flat.append({'month': row['month'], 'date': row['date'], 'country': country, **values})
        with (directory / 'countries.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
        return result
    finally:
        engine.close()


def compare_campaigns(reference, replay):
    differences = [key for key in ('world', 'rng', 'ledger')
        if digest(reference['final_logical'][key]) != digest(replay['final_logical'][key])]
    return {'equal': not differences, 'differences': differences,
        'reference_sha256': reference['final_logical_sha256'], 'replay_sha256': replay['final_logical_sha256']}


def run_performance_pair(directory, months=3, *, seed=SEED):
    """Measure the new feature's cost; its real effects are intentionally enabled."""
    directory = Path(directory)
    results = {}
    for label, enabled in (('disabled', False), ('enabled', True)):
        result = run_campaign(directory / label, 'industrial_front', months, seed=seed,
                              military_enabled=enabled)
        results[label] = {key: result[key] for key in ('total_seconds', 'month_timings', 'database_bytes')}
        results[label]['final'] = result['rows'][-1]
    result = {'schema_version': 1, 'countries': 4, 'persons': 20, 'months': months, 'seed': seed,
        'source_sha256': source_hashes(), 'runs': results,
        'elapsed_ratio': results['enabled']['total_seconds'] / max(1e-9, results['disabled']['total_seconds']),
        'method': 'Sequential actual SQLite Engine, daily full auditing; same initial economies and actors. '
                  'Military activation, supplies, combat, training and resulting NPC/Crisis responses have real costs; '
                  'this is feature overhead, not an identical-outcome optimization comparison.'}
    (directory / 'performance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def write_report(results, path, *, performance=None, replay=None, tests=None):
    lines = ['# Military & Logistics v1 — 실제 3년 캠페인 검사', '',
        '가상 4개국과 20명의 NPC를 실제 SQLite 엔진으로 진행했다. 매일 기존 Auditor를 실행했고, '
        '물자·재정·사람을 실제 저장 상태에서 차감했다. 벤치마크가 월별 작전 목표를 상신하는 것은 '
        '검사용 명령 경로이며 새로운 군사 NPC AI가 아니다. 사건·승리·GDP를 특정 날짜에 강제하지 않았다.', '',
        '| 초기 조건 | 기간 | 실제 작전 | 교전/손실 결산 | 누적 사망·실종·탈영 | 최종 병력 | 보급 구매 비용 | 경제·정보 연결 | 시간 |',
        '|---|---:|---:|---:|---:|---:|---:|---|---:|']
    for scenario, result in results.items():
        row = result['rows'][-1]
        m = row['military']
        lines.append(f"| {scenario} ({SCENARIOS[scenario]}) | {result['months']}개월 | {m['operations']} | "
            f"{m['loss_receipts']} | {sum(m['losses_by_cause'].values())} | {m['surviving_manpower']} | "
            f"{m['procurement_cost']} | 사건 {row['crisis_events']}, 기억 {row['npc_memories']}, 보고 {row['npc_received_reports']} | "
            f"{result['total_seconds']:.2f}초 |")
    lines += ['', '## 경제·인구와 손실', '',
        '아래 GDP는 매월 산업 생산으로 계산된 값이다. 초기 0은 첫 결산 전 표시이므로 성장률 기준으로 사용하지 않았다. '
        '손실 결산에는 교전 외 질병·굶주림·행군 손실도 포함된다. 사망과 실종·탈영은 원시자료에서 별도로 보존한다.', '',
        '| 캠페인 | 국가 | 1개월 GDP | 3년 GDP | 인구 | 부채 | 준비자산 | 재정 상태 |',
        '|---|---|---:|---:|---:|---:|---:|---|']
    for scenario, result in results.items():
        for country, values in result['rows'][-1]['countries'].items():
            first = result['rows'][1]['countries'][country]
            lines.append(f"| {scenario} | {country} | {first['gdp']} | {values['gdp']} | {values['population']} | "
                f"{values['debt']} | {values['treasury']} | {values['distress_state']} |")
    lines += ['', '## 시간과 실제 보급', '',
        '병력과 보급품은 지역 그래프의 각 연결을 실제 통과한다. 차량·말·선박이 없는 고속 이동은 허용하지 않는다. '
        '포구 처리 능력과 단일 수송로 용량을 부대와 화물이 함께 사용한다. 수송량은 게임의 추상 물자/인원 단위이며 실제 톤수 환산은 아니다. '
        '육지에서 배가 이동하는 방식의 혼합 운송은 만들지 않았다. 실제 중간 창고에서 육상·해상 구간을 나눠야 한다.', '',
        '보충병은 최소 90일(이 실험은 120일) 교육, 별도의 실제 무기 생산, 현장 이동, 같은 장소에서의 편입을 거친다. '
        '계획 즉시 기존 부대의 경험·무기·병력을 복구하지 않는다. 공장·훈련·보급 조건이 부족하면 실제 제약 기록이 남는다.']
    if replay is not None:
        lines += ['', '## 저장 복원 검사', '',
            f"동일 경로의 중간 저장·복원 결과: 전체 World/RNG/Event Ledger 동등성 **{replay['equal']}**. "
            f"차이 항목: {replay['differences']}. 이는 별도의 실제 저장·복원 재실행이며 캐시의 출처만 바꾼 결과가 아니다."]
    if performance is not None:
        off, on = (performance['runs'][key] for key in ('disabled', 'enabled'))
        lines += ['', '## 실측 성능', '',
            f"같은 4개국·20명, {performance['months']}개월의 실제 SQLite 실행: 군사 미사용 {off['total_seconds']:.2f}초, "
            f"군사 사용 {on['total_seconds']:.2f}초 ({performance['elapsed_ratio']:.2f}배). "
            '군사 사용은 실제 교전·재정·보고·훈련을 추가하므로 결과가 다른 기능 비용 비교이다. '
            '경제·NPC 결과가 같은 상태의 최적화 속도비로 해석하면 안 된다. 월별 시간과 DB 크기를 JSON에 함께 저장했다.']
    if tests is not None:
        lines += ['', '## 실제 테스트', '', str(tests)]
    lines += ['', '## 범위와 한계', '',
        '야전 부대/전선 단위의 판정이며 병사 개개인, 함포 탄도, 상세 군사 AI, 실제 점령 사회, 평화조약 영토 분배를 구현하지 않는다. '
        '과거 기억과 현재 인식은 보고가 도착한 뒤 NPC에 연결된다. 판정기는 물리적 진실을 읽지만 정치 판단에 비공개 적군 정답을 전달하지 않는다.', '',
        '3년 결과는 이 가상 초기조건과 명령 경로의 관측이다. 모든 시대·국가·지형의 균형을 검증했다는 뜻은 아니다. '
        '원시 JSON.gz에 전체 최종 World/RNG/Ledger, 월별 경제·보급·전투 원인·편입 상태, 코드 해시를 보존했고 CSV는 국가별 월 결산이다.', '']
    Path(path).write_text('\n'.join(lines), encoding='utf-8')
    return Path(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Actual finite-resource military/SQLite research campaign')
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--scenario', choices=SCENARIOS, required=True)
    parser.add_argument('--months', type=int, default=36)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--checkpoint-month', type=int)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--disabled', action='store_true')
    parser.add_argument('--validation-mode', choices=('verification', 'runtime'), default='verification')
    args = parser.parse_args(argv)
    result = run_campaign(args.directory, args.scenario, args.months, seed=args.seed,
        military_enabled=not args.disabled, checkpoint_month=args.checkpoint_month, restore=args.restore,
        validation_mode=args.validation_mode,
        progress=lambda row, timing: print(json.dumps({'scenario': args.scenario, 'month': row['month'],
            'date': row['date'], 'seconds': timing['seconds'], 'military': row['military']}, ensure_ascii=False), flush=True))
    print(json.dumps({'seconds': result['total_seconds'], 'sha256': result['final_logical_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
