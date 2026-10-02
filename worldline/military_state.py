from .incremental_audit import items as audit_items, json_check as audit_json
"""Opt-in formation state; country adapters are separate from military ownership."""
import copy
import hashlib
import math
import random
from contextlib import contextmanager
from datetime import timedelta
from .models import canonical_date

VERSION = 1
LOSS_CAUSES = ('battle_casualties', 'disease', 'hunger', 'exposure', 'desertion',
               'march_attrition', 'naval_loss', 'capture_missing')
EQUIPMENT = ('personal_weapons', 'ammunition', 'artillery', 'horses', 'engineering',
             'transport', 'ships', 'armored_vehicles', 'aircraft', 'communications', 'medical')
DOCTRINE = {'centralized_control': .7, 'subordinate_initiative': .2,
            'mission_oriented_command': .1, 'staff_capability': .3,
            'communication_quality': .3, 'punishment_risk_culture': .5,
            'planning_flexibility': .3, 'adoption': .1}
BUCKETS = ('actors', 'formations', 'depots', 'links', 'shipments', 'convoys',
           'supply_sources', 'transport_reservations', 'route_cache', 'operations',
           'commands', 'delegations', 'theaters', 'controls', 'war_links', 'loss_receipts',
           'replacements', 'innovations', 'equipment_standards', 'fortresses',
           'procurement_receipts', 'food_accounts', 'movement_receipts', 'rng_streams', 'logistics_receipts')

def state(world):
    value = getattr(world, 'military_state', None)
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ValueError('Military & Logistics v1 is not enabled')
    return value

def after(day, days):
    return (canonical_date(day) + timedelta(days=days)).isoformat()

def emit(world, kind, **fields):
    record = {'system': 'military', 'kind': kind, 'date': world.date, **copy.deepcopy(fields)}
    state(world)['outbox'].append(record)
    return record

@contextmanager
def stream(world, key):
    from .persistence import tuples
    s = state(world)
    key = str(key)
    rng = random.Random()
    if key in s['rng_streams']:
        rng.setstate(tuples(s['rng_streams'][key]))
    else:
        seed = hashlib.sha256(f"military:1:{s['root_seed']}:{key}".encode()).digest()
        rng.seed(int.from_bytes(seed, 'big'))
    try:
        yield rng
    finally:
        s['rng_streams'][key] = rng.getstate()

def initialize(world, rng, config=None):
    if getattr(world, 'military_state', None) is not None:
        raise ValueError('Military is already enabled')
    if world.crisis_state is None or world.npc_state is None or not world.economy or world.economy.get('version') != 3:
        raise ValueError('Military v1 requires Economy v3 / NPC v1 / Crisis v1')
    config = copy.deepcopy(config or {})
    if set(config) - {'import_armies', 'actors', 'settings', 'initial_equipment', 'initial_supply', 'initial_fortresses'}:
        raise ValueError('Unknown military configuration')
    s = {name: {} for name in BUCKETS}
    s.update(version=VERSION, root_seed=rng.getrandbits(128), revision=0, outbox=[],
             politics={}, politics_version=1, political_decisions={},
             diplomatic_hooks=[], settings=config.get('settings', {}),
             created_at=world.date, last_tick=None)
    world.military_state, world.schema_version = s, 5
    for country in sorted(world.countries):
        regions = sorted(k for k, r in world.regions.items() if r.owner == country)
        if regions:
            add_actor(world, country, country=country, home_region=regions[0])
    for key, values in sorted(config.get('actors', {}).items()):
        add_actor(world, key, **values)
    if config.get('import_armies', True):
        for key, army in sorted(world.armies.items()):
            origin = world.economy.get('labour_army_origins', {}).get(key, army.region)
            s['formations'][key] = formation(key, army.owner, army.personnel, army.region,
                origin_region=origin, mobility={'mode': 'foot', 'speed_km_day': army.speed_km_day, 'motorized': False},
                service_type='standing', labour_backing={'kind': 'standing', 'legacy_army': key})
    for key, equipment in config.get('initial_equipment', {}).items():
        if key not in s['formations']:
            raise ValueError('Initial equipment requires an existing scenario formation')
        s['formations'][key]['equipment_state'] = copy.deepcopy(equipment)
    for key, supplies in config.get('initial_supply', {}).items():
        if key not in s['formations']:
            raise ValueError('Initial supplies require an existing scenario formation')
        s['formations'][key]['supply_state']['stocks'] = copy.deepcopy(supplies)
    for region, fortress in config.get('initial_fortresses', {}).items():
        # Declared finite scenario assets are admitted only during activation.
        s['fortresses'][region] = {'region': region, 'actor': world.regions[region].owner,
            'strength': 0., 'food': 0., 'garrison': [], 'relief_probability': .2,
            'last_supplied': world.date, 'disease_pressure': 0., 'besieged_since': None, **copy.deepcopy(fortress)}
    return s

def add_actor(world, actor_id, *, country=None, kind='state', home_region=None,
              manpower_pool=0, trained_reserve=0, treasury=0, stocks=None,
              equipment_reserve=None, technical_capabilities=None):
    s = state(world)
    if not isinstance(actor_id, str) or not actor_id or actor_id in s['actors']:
        raise ValueError('Unique military actor ID required')
    if country is not None and country not in world.countries or home_region not in world.regions:
        raise ValueError('Unknown actor country/home region')
    if any(type(v) is not int or v < 0 for v in (manpower_pool, trained_reserve, treasury)) or trained_reserve > manpower_pool:
        raise ValueError('Invalid finite actor manpower/treasury')
    a = {'actor_id': actor_id, 'country': country, 'kind': kind, 'home_region': home_region,
         'manpower_pool': manpower_pool, 'trained_reserve': trained_reserve, 'treasury': treasury,
         'stocks': copy.deepcopy(stocks or {}), 'equipment_reserve': copy.deepcopy(equipment_reserve or {}),
         'technical_capabilities': copy.deepcopy(technical_capabilities or {})}
    s['actors'][actor_id] = a
    s['politics'][actor_id] = {'casualty_burden': 0, 'mobilization_burden': 0,
        'requisition_burden': 0, 'victory_prestige': 0, 'defeat_grievance': 0,
        'war_exhaustion': 0, 'fiscal_strain': 0, 'will_to_continue': None}
    s['revision'] += 1
    return a

def formation(key, actor, manpower, location, **fields):
    support = fields.pop('support_manpower', int(manpower * .15))
    f = {'id': key, 'actor': actor, 'formation_type': 'infantry', 'echelon': 'field_force',
        'manpower': manpower, 'combat_manpower': manpower - support, 'support_manpower': support,
        'training': .3, 'experience': .1, 'cohesion': .7, 'morale': .6, 'fatigue': 0., 'readiness': .7,
        'commander': None, 'location': location, 'origin_region': location,
        'mobility': {'mode': 'foot', 'speed_km_day': 20., 'motorized': False},
        'equipment_state': {}, 'supply_state': {'stocks': {}, 'last_ratio': 1., 'consumption': {}, 'requirements': {}},
        'doctrine': copy.deepcopy(DOCTRINE), 'parent': None, 'subordinates': [],
        'current_order': None, 'operational_objective': None, 'service_type': 'mobilized',
        'labour_backing': {'kind': 'external'}, 'adoption': {}, 'journey': None}
    f.update(copy.deepcopy(fields))
    return f

def validate(world, previous=None):
    if getattr(world, 'military_state', None) is None:
        return []
    issues = []
    def number(x, maximum=None):
        return type(x) in (int, float) and math.isfinite(x) and x >= 0 and (maximum is None or x <= maximum)
    try:
        s = state(world)
        if any(not isinstance(s.get(k), dict) for k in BUCKETS) or not isinstance(s.get('outbox'), list):
            return ['military: invalid state mappings']
        canonical_date(s['created_at'])
        if type(s['root_seed']) is not int or type(s['revision']) is not int or s['revision'] < 0:
            issues.append('military: invalid revision/seed')
        for key, a in audit_items(s['actors']):
            if a['actor_id'] != key or a['home_region'] not in world.regions or a['country'] is not None and a['country'] not in world.countries:
                issues.append('military: unknown actor origin ' + key)
            if any(not number(a[k]) for k in ('manpower_pool', 'trained_reserve', 'treasury')) or a['trained_reserve'] > a['manpower_pool']:
                issues.append('military: invalid actor resources ' + key)
            if any(not number(v) for v in a['stocks'].values()):
                issues.append('military: negative actor stores ' + key)
            if any(not number(v) for v in a['equipment_reserve'].values()):
                issues.append('military: negative equipment reserve ' + key)
        manpower_pools = {}
        for key, f in audit_items(s['formations']):
            if f['id'] != key or f['actor'] not in s['actors'] or f['location'] not in world.regions or f['origin_region'] not in world.regions:
                issues.append('military: invalid formation references ' + key)
            if any(type(f[k]) is not int or f[k] < 0 for k in ('manpower', 'combat_manpower', 'support_manpower')) or f['manpower'] != f['combat_manpower'] + f['support_manpower']:
                issues.append('military: duplicated formation manpower ' + key)
            if any(not number(f[k], 1) for k in ('training', 'experience', 'cohesion', 'morale', 'fatigue', 'readiness')):
                issues.append('military: invalid formation quality ' + key)
            if not number(f['mobility']['speed_km_day']) or f['mobility']['speed_km_day'] <= 0:
                issues.append('military: invalid mobility ' + key)
            for item in f['equipment_state'].values():
                if not number(item['quantity']) or not number(item.get('condition', 1), 1):
                    issues.append('military: invalid equipment ' + key)
            if set(f['equipment_state']) - set(EQUIPMENT):
                issues.append('military: unknown equipment category ' + key)
            if any(not number(v) for v in f['supply_state']['stocks'].values()):
                issues.append('military: negative formation supplies ' + key)
            if f.get('parent') is not None and f['parent'] not in s['formations']:
                issues.append('military: missing formation parent ' + key)
            seen, node = set(), key
            while node is not None and node in s['formations']:
                if node in seen:
                    issues.append('military: formation hierarchy cycle ' + key)
                    break
                seen.add(node)
                node = s['formations'][node].get('parent')
            commander = f.get('commander')
            if commander is not None and commander not in world.npc_state['persons']:
                issues.append('military: nonexistent commander ' + key)
            if any(not number(f['doctrine'].get(axis), 1) for axis in DOCTRINE):
                issues.append('military: invalid command doctrine ' + key)
            country = s['actors'][f['actor']]['country']
            backing = f['labour_backing']['kind']
            if country is not None:
                if backing not in ('standing', 'mobilized'):
                    issues.append('military: national troops not backed by actual labour ' + key)
                else:
                    pool = (f['origin_region'], backing)
                    manpower_pools[pool] = manpower_pools.get(pool, 0) + f['manpower']
                legacy = f['labour_backing'].get('legacy_army')
                if legacy and (legacy not in world.armies or world.armies[legacy].personnel != f['manpower']):
                    issues.append('military: legacy manpower differs from formation ' + key)
            elif backing != 'external':
                issues.append('military: non-state actor uses nonexistent national labour ' + key)
            if f.get('parent') and s['formations'][f['parent']]['actor'] != f['actor']:
                issues.append('military: unrelated parent authority ' + key)
            if any(child not in s['formations'] or s['formations'][child].get('parent') != key for child in f['subordinates']):
                issues.append('military: inconsistent subordinate links ' + key)
        for (region, backing), count in audit_items(manpower_pools):
            available = world.economy['regions'][region]['labour_state']['standing_army' if backing == 'standing' else 'mobilized']
            if count > available + 1e-6:
                issues.append('military: formations exceed actual charged labour ' + region)
        for key, receipt in audit_items(s['loss_receipts']):
            result = receipt['result']
            expected = {}
            for unit, causes in result.get('casualties', {}).items():
                f = s['formations'][unit]
                deaths = sum(v for cause, v in causes.items() if cause not in ('capture_missing', 'desertion'))
                if deaths and s['actors'][f['actor']]['country'] is not None:
                    expected[f['origin_region']] = expected.get(f['origin_region'], 0) + deaths
            if receipt['id'] != key or result['id'] != key or receipt['deaths'] != expected:
                issues.append('military: loss receipt does not match actual casualties ' + key)
            event_id = receipt.get('event_id')
            if event_id is not None:
                event = world.crisis_state['events'].get(event_id, {})
                facts = event.get('actual_facts', {})
                if facts.get('result') != result or facts.get('deaths') != expected:
                    issues.append('military: military history differs from immutable event ' + key)
                for region, count in audit_items(expected):
                    effects = [e for e in world.crisis_state['effects'].values() if e['idempotency_key'] == 'military:' + key + ':deaths:' + region]
                    if len(effects) != 1 or effects[0]['payload']['count'] != count:
                        issues.append('military: missing or repeated population consequence ' + key)
        for region, control in audit_items(s['controls']):
            if region not in world.regions or control['legal_owner'] != world.regions[region].owner or control['controller'] not in s['actors']:
                issues.append('military: illegal territory/control reference ' + region)
        for region, fortress in audit_items(s['fortresses']):
            if region not in world.regions or fortress['region'] != region or fortress['actor'] not in s['actors'] or any(not number(fortress[k]) for k in ('strength', 'food')) or not number(fortress['relief_probability'], 1):
                issues.append('military: invalid physical fortress ' + region)
            if any(k not in s['formations'] or s['formations'][k]['actor'] != fortress['actor'] for k in fortress['garrison']):
                issues.append('military: nonexistent fortress garrison ' + region)
        for key, item in audit_items(s['replacements']):
            if item['id'] != key or item['cohort'] not in s['formations'] or item['recipient'] not in s['formations'] or item['project'] not in world.projects or item['minimum_duration'] < 90:
                issues.append('military: invalid replacement pipeline ' + key)
        from .persistence import tuples
        for value in s['rng_streams'].values():
            random.Random().setstate(tuples(value))
        if previous and getattr(previous, 'military_state', None):
            for key, value in audit_items(previous.military_state['loss_receipts']):
                now = s['loss_receipts'].get(key, {})
                if any(now.get(k) != value.get(k) for k in ('id', 'date', 'result', 'deaths', 'cargo_loss_receipts')) or value.get('event_id') is not None and now.get('event_id') != value['event_id']:
                    issues.append('military: changed immutable loss receipt ' + key)
        for module in ('military_logistics', 'military_operations', 'military_innovation', 'military_politics'):
            from importlib import import_module
            m = import_module('.' + module, __package__)
            validator = getattr(m, 'validation_issues', getattr(m, 'validate', None))
            if validator:
                issues.extend(validator(world, previous))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        issues.append('military: malformed state ' + str(exc))
    return issues
