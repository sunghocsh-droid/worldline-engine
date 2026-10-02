"""The single owner of military resource, manpower and loss accounting."""
import copy
import math
from .military_state import state, emit, formation, LOSS_CAUSES

RECIPES = {'food': {'agriculture': 1}, 'fodder': {'agriculture': 1},
    'ammunition': {'chemicals': .6, 'heavy_industry': .4}, 'medical': {'chemicals': .5, 'light_industry': .5},
    'medical_supplies': {'chemicals': .5, 'light_industry': .5}, 'spare_parts': {'machinery': 1},
    'fuel': {'energy': 1}, 'replacement_weapons': {'machinery': .5, 'heavy_industry': .5},
    'personal_weapons': {'machinery': .5, 'heavy_industry': .5}, 'artillery': {'heavy_industry': .7, 'machinery': .3},
    'engineering': {'construction': .5, 'machinery': .5}, 'transport': {'transport': .5, 'machinery': .5},
    'ships': {'shipping': .7, 'heavy_industry': .3}, 'communications': {'machinery': .7, 'chemicals': .3},
    'armored_vehicles': {'heavy_industry': .5, 'machinery': .5}, 'horses': {'agriculture': 1}, 'animals': {'agriculture': 1}}

def actor_country(world, actor):
    return state(world)['actors'][actor]['country']

def book_interim(world, country, old_treasury, old_debt):
    finance = world.economy['countries'][country]['finance']
    finance['interim_net_cashflow'] = finance.get('interim_net_cashflow', 0) + world.countries[country].treasury - old_treasury
    finance['interim_debt_change'] = finance.get('interim_debt_change', 0) + world.countries[country].debt - old_debt
    finance['reserves'] = world.countries[country].treasury

def _take_goods(world, region, recipe, quantity):
    r = world.economy['regions'][region]
    for key, share in recipe.items():
        pool = r['stocks'] if key in ('energy', 'raw_materials') else r['stocks']['goods']
        if pool.get(key, 0) + 1e-10 < quantity * share:
            raise ValueError('Actual military industrial stock shortage: ' + key)
    used = {}
    for key, share in recipe.items():
        amount = quantity * share
        pool = r['stocks'] if key in ('energy', 'raw_materials') else r['stocks']['goods']
        pool[key] = max(0., pool.get(key, 0) - amount)
        if key in ('energy', 'raw_materials') and 'closing_' + key in r['inputs']:
            r['inputs']['emergency_' + key + '_used'] = r['inputs'].get('emergency_' + key + '_used', 0.) + amount
            r['inputs']['closing_' + key] -= amount
        used[key] = amount
    return used

def consume_goods(world, actor, region, commodity, quantity):
    if type(quantity) not in (int, float) or not math.isfinite(quantity) or quantity < 0:
        raise ValueError('Finite nonnegative supply quantity required')
    country = actor_country(world, actor)
    if country is None:
        pool = state(world)['actors'][actor]['stocks']
        if pool.get(commodity, 0) < quantity:
            raise ValueError('Non-state actor has no accessible supply stock')
        pool[commodity] -= quantity
        return {commodity: quantity}
    if region not in world.economy['regions'] or world.regions[region].owner != country:
        raise ValueError('Military procurement requires own production or explicit arms-supply transfer')
    if commodity.split(':', 1)[0] not in RECIPES:
        raise ValueError('No industrial recipe for commodity ' + commodity)
    return _take_goods(world, region, RECIPES[commodity.split(':', 1)[0]], quantity)

def procure(world, actor, region, commodity, quantity, *, allow_emergency=False, instruments=None, receipt_id=None):
    s = state(world)
    identifier = receipt_id or f"procurement:{len(s['procurement_receipts']) + 1}"
    request = {'actor': actor, 'region': region, 'commodity': commodity, 'quantity': quantity}
    if identifier in s['procurement_receipts']:
        old = s['procurement_receipts'][identifier]
        if any(old[k] != v for k, v in request.items()):
            raise ValueError('Military procurement receipt reused for different goods')
        return copy.deepcopy(old)
    if type(quantity) not in (int, float) or not math.isfinite(quantity) or quantity <= 0:
        raise ValueError('Positive actual procurement quantity required')
    country = actor_country(world, actor)
    cost = max(1, math.ceil(quantity))
    if country is not None:
        control = s['controls'].get(region, {})
        if region not in world.regions or world.regions[region].owner != country or control.get('controller', actor) != actor:
            raise ValueError('Military procurement requires accessible own production')
        if commodity.split(':', 1)[0] not in RECIPES:
            raise ValueError('No implemented physical recipe for commodity')
        from .order_execution import economic_candidate
        from .credit import finance_amount
        candidate = economic_candidate(world)
        used = _take_goods(candidate, region, RECIPES[commodity.split(':', 1)[0]], quantity)
        old_treasury, old_debt = candidate.countries[country].treasury, candidate.countries[country].debt
        finance = finance_amount(candidate, country, cost, allow_emergency=allow_emergency, instruments=instruments)
        book_interim(candidate, country, old_treasury, old_debt)
        # The candidate excludes all NPC/Military history. Commit only economic truth.
        world.economy, world.countries = candidate.economy, candidate.countries
    else:
        a = s['actors'][actor]
        if a['treasury'] < cost or a['stocks'].get(commodity, 0) < quantity:
            raise ValueError('No accessible non-state financing/supplies')
        used = consume_goods(world, actor, region, commodity, quantity)
        a['treasury'] -= cost
        finance = {'cash': cost, 'borrowing': 0, 'arrears_financing': 0}
    receipt = {'id': identifier, **request, 'date': world.date, 'status': 'funded',
               'source_goods': used, 'cost': cost, 'finance': copy.deepcopy(finance)}
    s['procurement_receipts'][identifier] = receipt
    return copy.deepcopy(receipt)

def recruit(world, key, actor, manpower, region, **fields):
    s = state(world)
    if key in s['formations'] or type(manpower) is not int or manpower <= 0:
        raise ValueError('Unique formation / positive manpower required')
    country = actor_country(world, actor)
    f = formation(key, actor, manpower, region, origin_region=region, **fields)
    if f['equipment_state'] or f['supply_state'].get('stocks') or f['experience'] > .1:
        raise ValueError('New troops cannot invent equipment, supplies or battlefield experience')
    f['training'] = min(.3, f['training'])
    if country is not None:
        from . import labour
        l = world.economy['regions'][region]['labour_state']
        labour.mobilize(world, country, region, int(round(l['mobilized'])) + manpower)
        f['labour_backing'] = {'kind': 'mobilized'}
    else:
        a = s['actors'][actor]
        if a['manpower_pool'] < manpower:
            raise ValueError('External actor manpower pool exhausted')
        trained = min(a['trained_reserve'], manpower)
        a['manpower_pool'] -= manpower
        a['trained_reserve'] -= trained
        f['training'] = max(f['training'], trained / manpower * .6)
        f['labour_backing'] = {'kind': 'external'}
    s['formations'][key] = f
    s['politics'][actor]['mobilization_burden'] += manpower
    emit(world, 'formation_raised', actor=actor, region=region, formation_id=key, manpower=manpower)
    return copy.deepcopy(f)

def demobilize(world, formation_id, amount=None):
    s = state(world)
    f = s['formations'][formation_id]
    count = f['manpower'] if amount is None else amount
    if type(count) is not int or not 0 <= count <= f['manpower']:
        raise ValueError('Invalid demobilization cohort')
    country = actor_country(world, f['actor'])
    if country is not None:
        from . import labour
        l = world.economy['regions'][f['origin_region']]['labour_state']
        field = 'standing_army' if f['labour_backing']['kind'] == 'standing' else 'mobilized'
        if field == 'mobilized':
            labour.mobilize(world, country, f['origin_region'], max(0, int(round(l[field])) - count))
        else:
            labour._military_change(world, f['origin_region'], field, max(0, l[field] - count), 'demobilization')
            world.armies[f['labour_backing']['legacy_army']].personnel -= count
            labour._write_region(world, f['origin_region'])
            labour.aggregate(world, country)
    else:
        # Finite non-state pool; no simulated national labour market is invented.
        s['actors'][f['actor']]['manpower_pool'] += count
    _subtract_personnel(f, count)
    emit(world, 'demobilized', actor=f['actor'], region=f['origin_region'], formation_id=formation_id, manpower=count)

def _subtract_personnel(f, count):
    support = min(f['support_manpower'], int(round(count * f['support_manpower'] / max(1, f['manpower']))))
    combat = min(f['combat_manpower'], count - support)
    support = count - combat
    f['manpower'] -= count
    f['support_manpower'] -= support
    f['combat_manpower'] -= combat

def record_food_consumption(world, formation_id, amount, days=1):
    f = state(world)['formations'][formation_id]
    if actor_country(world, f['actor']) is None:
        return
    r = world.economy['regions'][f['origin_region']]
    account = state(world)['food_accounts'].setdefault(f['origin_region'], {'consumed': 0., 'credited': 0.})
    daily = account.setdefault('daily', {}).setdefault(formation_id + ':' + world.date, {'credited': 0., 'days': 0})
    daily['days'] = max(days, daily['days'])
    cap = f['manpower'] * .08 / 365 * daily['days']
    credit = min(amount, max(0., cap - daily['credited']))
    daily['credited'] += credit
    r['military_food_credit'] = r.get('military_food_credit', 0.) + credit
    account['consumed'] += amount
    account['credited'] += credit

def civilian_food_need(world, region, normal_need):
    if getattr(world, 'military_state', None) is None:
        return normal_need
    r = world.economy['regions'][region]
    credit = min(normal_need, r.pop('military_food_credit', 0.))
    return normal_need - credit

def _charge_loss(world, f, counts):
    total = sum(counts.values())
    if total > f['manpower']:
        raise ValueError('Military loss exceeds remaining formation manpower')
    # Captured/missing people remain alive but unavailable. Deserters return slowly.
    fatal = sum(v for k, v in counts.items() if k not in ('desertion', 'capture_missing'))
    absent = fatal + counts.get('capture_missing', 0)
    country = actor_country(world, f['actor'])
    if country is not None:
        from . import labour
        r = world.economy['regions'][f['origin_region']]
        l, d = r['labour_state'], r['demography']
        field = 'standing_army' if f['labour_backing']['kind'] == 'standing' else 'mobilized'
        old_military = labour._military_total(l)
        fraction = min(1., total / max(old_military, 1e-12))
        skills, professionals = l['military_skilled'] * fraction, l['military_professionals'] * fraction
        l[field] = max(0., l[field] - total)
        l['military_skilled'] -= skills
        l['military_professionals'] -= professionals
        if counts.get('desertion', 0):
            cohort = counts['desertion']
            labour._transition(world, l, field, 'unemployed', (cohort, skills * cohort / max(1, total), professionals * cohort / max(1, total)), 120, 'demobilization')
        # Selective working-age losses, rather than spreading military deaths over civilians.
        d['productive_population'] = max(0, d['productive_population'] - absent)
        survivors = max(1, d['total_population'] - sum(r['pending_losses'].values()) - fatal)
        d['productive_share'] = min(1., d['productive_population'] / survivors)
        l['labour_force'] = l['working_age_population'] = d['productive_population']
        legacy = f['labour_backing'].get('legacy_army')
        if legacy:
            world.armies[legacy].personnel -= total
        labour._write_region(world, f['origin_region'])
        labour.aggregate(world, country)
    _subtract_personnel(f, total)
    return fatal

def apply_result(world, result):
    s = state(world)
    identifier = result['id']
    if identifier in s['loss_receipts']:
        if s['loss_receipts'][identifier]['result'] != result:
            raise ValueError('Military result ID reused with different losses')
        return copy.deepcopy(s['loss_receipts'][identifier])
    # Validate everything before any damage; Engine transaction is the outer atomic boundary.
    for key, raw in result.get('casualties', {}).items():
        if key not in s['formations'] or set(raw) - set(LOSS_CAUSES) or any(type(v) is not int or v < 0 for v in raw.values()) or sum(raw.values()) > s['formations'][key]['manpower']:
            raise ValueError('Invalid military casualties')
    for key, raw in result.get('equipment_losses', {}).items():
        for category, count in raw.items():
            if count < 0 or count > s['formations'][key]['equipment_state'].get(category, {}).get('quantity', 0):
                raise ValueError('Military equipment destruction exceeds actual stock')
    cargo_losses = []
    for convoy_id, convoy in s['convoys'].items():
        opening = sum(s['formations'][key]['equipment_state'].get('ships', {}).get('quantity', 0) for key in convoy['formations'])
        destroyed = sum(result.get('equipment_losses', {}).get(key, {}).get('ships', 0) for key in convoy['formations'])
        if opening > 0 and destroyed:
            fraction = min(1., destroyed / opening)
            from .military_logistics import lose_cargo
            for shipment_id, shipment in sorted(s['shipments'].items()):
                if shipment.get('convoy') == convoy_id and shipment['status'] not in ('delivered', 'cancelled', 'lost'):
                    cargo_losses.append(lose_cargo(world, shipment_id, {k: q * fraction for k, q in shipment['cargo'].items()},
                        cause='adjudicated transport-ship destruction', receipt_id=identifier + ':' + shipment_id))
    deaths = {}
    for key, counts in sorted(result.get('casualties', {}).items()):
        f = s['formations'][key]
        count = _charge_loss(world, f, counts)
        if count and actor_country(world, f['actor']) is not None:
            deaths[f['origin_region']] = deaths.get(f['origin_region'], 0) + count
        p = s['politics'][f['actor']]
        p['casualty_burden'] += sum(counts.values())
        p['war_exhaustion'] += sum(counts.values()) / max(1, f['manpower'] + sum(counts.values()))
    for region, count in deaths.items():
        r = world.economy['regions'][region]
        d = r['demography']
        survivors = max(1, d['total_population'] - sum(r['pending_losses'].values()) - count)
        d['productive_share'] = min(1., d['productive_population'] / survivors)
    for key, raw in result.get('equipment_losses', {}).items():
        for category, count in raw.items():
            s['formations'][key]['equipment_state'][category]['quantity'] -= count
    for key, raw in result.get('unit_changes', {}).items():
        f = s['formations'][key]
        for field in ('cohesion', 'morale', 'fatigue', 'experience'):
            f[field] = min(1., max(0., f[field] + raw.get(field + '_delta', 0.)))
    for key, raw in result.get('supply_consumption', {}).items():
        pool = s['formations'][key]['supply_state']['stocks']
        for commodity, amount in raw.items():
            taken = min(pool.get(commodity, 0), max(0, amount))
            pool[commodity] = pool.get(commodity, 0) - taken
    control = result.get('control_change')
    if control:
        region = control.get('region', result.get('region'))
        s['controls'][region] = {'legal_owner': world.regions[region].owner,
            'controller': control.get('controller', result['actor']), 'occupied': True,
            'contested': control.get('contested', False), 'besieged': control.get('besieged', False)}
    s['diplomatic_hooks'].extend(copy.deepcopy(result.get('diplomatic_hooks', [])))
    for hook in result.get('political_hooks', []):
        p = s['politics'].get(hook.get('actor'))
        if p is not None:
            if hook['kind'] == 'victory_prestige': p['victory_prestige'] += hook.get('value', 0)
            elif hook['kind'] == 'military_failure': p['defeat_grievance'] += 1 - hook.get('value', 0)
    receipt = {'id': identifier, 'date': world.date, 'result': copy.deepcopy(result), 'deaths': deaths, 'cargo_loss_receipts': cargo_losses, 'event_id': None}
    s['loss_receipts'][identifier] = receipt
    emit(world, 'operation_result', actor=result.get('actor'), region=result.get('region'), result_id=identifier,
        result=copy.deepcopy(result), deaths=deaths)
    return copy.deepcopy(receipt)

def reconcile(world):
    """Monthly demographic changes cannot leave more formations than charged workers."""
    s = state(world)
    from . import labour
    pools = {}
    for f in s['formations'].values():
        country = actor_country(world, f['actor'])
        if country is not None and f['labour_backing']['kind'] == 'mobilized':
            pools.setdefault(f['origin_region'], []).append(f)
    for region, units in sorted(pools.items()):
        l = world.economy['regions'][region]['labour_state']
        total = sum(f['manpower'] for f in units)
        # Natural demographic reconciliation can scale reserves fractionally.
        shortage = max(0, total - int(math.floor(l['mobilized'] + 1e-7)))
        if shortage:
            for f in sorted(units, key=lambda x: x['id']):
                take = min(shortage, f['manpower'])
                _subtract_personnel(f, take)
                shortage -= take
                if not shortage: break
            emit(world, 'manpower_reconciled', region=region, reduction=total - sum(f['manpower'] for f in units),
                 basis='existing monthly population/labour settlement; no duplicate deaths')


def delegate(world, person, actor, *, formations=None, regions=None, scopes=None, expires=None):
    """Trusted sovereign/scenario grants specific authority; NPC opinions do not."""
    s = state(world)
    if person not in world.npc_state['persons'] or actor not in s['actors']:
        raise ValueError('Unknown delegating actor/person')
    ids, places = list(formations or []), list(regions or [])
    if any(k not in s['formations'] or s['formations'][k]['actor'] != actor for k in ids) or any(k not in world.regions for k in places):
        raise ValueError('Delegation must identify own formations and real regions')
    if expires is not None:
        from .models import canonical_date
        canonical_date(expires)
    s['delegations'][person] = {'actor': actor, 'formations': ids, 'regions': places,
        'scopes': list(scopes or ['movement', 'local']), 'expires': expires}
    return copy.deepcopy(s['delegations'][person])


def register_fortress(world, region, actor, *, strength=50., food=0., garrison=None, relief_probability=.2):
    s = state(world)
    if region not in world.regions or actor not in s['actors'] or region in s['fortresses']:
        raise ValueError('Unique real fortress region/actor required')
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (strength, food, relief_probability)) or relief_probability > 1:
        raise ValueError('Invalid fortress physical conditions')
    ids = list(garrison or [])
    if any(k not in s['formations'] or s['formations'][k]['actor'] != actor or s['formations'][k]['location'] != region for k in ids):
        raise ValueError('Fortress garrison must physically exist')
    if strength > world.regions[region].infrastructure.get('fortifications', 0):
        raise ValueError('A fortress needs an actual existing fortification or a funded construction project')
    if food:
        procure(world, actor, region, 'food', food, receipt_id='fortress-provision:' + region)
    s['fortresses'][region] = {'region': region, 'actor': actor, 'strength': strength, 'food': food,
        'garrison': ids, 'relief_probability': relief_probability, 'last_supplied': world.date,
        'disease_pressure': 0., 'besieged_since': None}
    return copy.deepcopy(s['fortresses'][region])


def build_fortress(world, project_id, actor, region, strength, *, duration_days=180, allow_emergency=False):
    from . import projects
    s = state(world)
    country = actor_country(world, actor)
    if country is None or region not in world.regions or world.regions[region].owner != country or type(duration_days) is not int or duration_days < 90 or type(strength) not in (int, float) or not math.isfinite(strength) or strength <= 0:
        raise ValueError('National fortification requires owned land, actual investment and construction time')
    if s['controls'].get(region, {}).get('controller', actor) != actor or world.economy['regions'][region]['stocks']['goods'].get('construction', 0) < strength:
        raise ValueError('No accessible construction output for fortifications')
    p = projects.plan_military(world, project_id, country, region, 'fortification', duration_days, strength,
        requirements={'resources': {}, 'minimum_labour': strength / duration_days * .05, 'economy_enabled': True},
        metadata={'fortress_production': {'actor': actor, 'strength': strength, 'receipt': None}})
    cash, debt = world.countries[country].treasury, world.countries[country].debt
    projects.fund(world, p, allow_emergency=allow_emergency)
    book_interim(world, country, cash, debt)
    _take_goods(world, region, {'construction': 1}, strength)
    return copy.deepcopy(p)


def _production_conditions(world, actor, region, category, family):
    from .military_innovation import technical_capabilities
    from .military_state import EQUIPMENT
    s = state(world)
    if category not in EQUIPMENT or category == 'aircraft':
        raise ValueError('Equipment category needs an implemented physical production recipe')
    country = actor_country(world, actor)
    if country is None or world.regions[region].owner != country or s['controls'].get(region, {}).get('controller', actor) != actor:
        raise ValueError('National production needs an accessible owned industry')
    if family is not None and family not in s['equipment_standards']:
        raise ValueError('Unknown equipment family')
    capabilities = technical_capabilities(world, actor, region)
    if category == 'armored_vehicles':
        keys = ('engine', 'metallurgy', 'armor', 'tracks', 'transmission', 'precision', 'fuel')
        if min(capabilities.get(k, 0.) for k in keys) < .35:
            raise ValueError('Practical armored production lacks demonstrated physical capabilities; propose/trial first')
    if family is not None:
        standard = s['equipment_standards'][family]
        if standard['equipment_type'] != category or capabilities.get('precision', 0.) < standard['precision'] or standard['interchangeability'] > capabilities.get('precision', 0.) + .15:
            raise ValueError('Administrative standardization cannot create precision manufacturing')
    return capabilities


def produce_equipment(world, project_id, actor, region, category, quantity, *, duration_days=90, family=None, uses_ammunition=False,
                      allow_emergency=False, instruments=None):
    from . import projects
    s = state(world)
    if project_id in world.projects or type(quantity) is not int or quantity <= 0 or type(duration_days) is not int or duration_days < 30:
        raise ValueError('Unique finite production batch with actual manufacturing time required')
    capabilities = _production_conditions(world, actor, region, category, family)
    if type(uses_ammunition) is not bool:
        raise ValueError('Weapon ammunition requirement must be explicit')
    if family and s['equipment_standards'][family]['ammunition_family']:
        uses_ammunition = True
    recipe = RECIPES.get(category)
    if recipe is None:
        raise ValueError('No equipment production recipe')
    r = world.economy['regions'][region]
    for name, share in recipe.items():
        pool = r['stocks'] if name in ('energy', 'raw_materials') else r['stocks']['goods']
        if pool.get(name, 0.) < quantity * share:
            raise ValueError('Industrial output cannot cover equipment production')
    country = actor_country(world, actor)
    cost = quantity
    p = projects.plan_military(world, project_id, country, region, 'equipment_production', duration_days, cost,
        requirements={'resources': {}, 'minimum_labour': quantity / duration_days * .05, 'economy_enabled': True},
        metadata={'equipment_production': {'actor': actor, 'category': category, 'quantity': quantity,
            'family': family, 'capabilities': capabilities, 'source_goods': {k: quantity * v for k, v in recipe.items()},
            'uses_ammunition': uses_ammunition, 'minimum_duration': 30, 'receipt': None}})
    old_cash, old_debt = world.countries[country].treasury, world.countries[country].debt
    projects.fund(world, p, allow_emergency=allow_emergency, instruments=instruments)
    book_interim(world, country, old_cash, old_debt)
    consume_goods(world, actor, region, category, quantity)
    emit(world, 'equipment_production_started', actor=actor, region=region, project_id=project_id, category=category, quantity=quantity)
    return copy.deepcopy(p)


def issue_equipment(world, actor, formation_id, category, quantity, *, family=None, uses_ammunition=False):
    from .military_logistics import is_stationary
    s = state(world)
    f, a = s['formations'][formation_id], s['actors'][actor]
    if f['actor'] != actor or f['location'] != a['home_region'] or not is_stationary(world, f):
        raise ValueError('Equipment reserve must physically meet own formation at home depot')
    if family and s['equipment_standards'][family]['ammunition_family']:
        uses_ammunition = True
    reserve_key = category + (':' + family if family else ':firearm' if uses_ammunition else '')
    stock = a['equipment_reserve'].get(reserve_key, 0)
    if type(quantity) is not int or quantity <= 0 or quantity > stock:
        raise ValueError('No finite equipment reserve')
    item = f['equipment_state'].get(category)
    if item and (item.get('family', item.get('standard_id')) != family or bool(item.get('uses_ammunition', False)) != uses_ammunition):
        raise ValueError('Conversion requires withdrawing incompatible equipment first')
    a['equipment_reserve'][reserve_key] -= quantity
    item = f['equipment_state'].setdefault(category, {'quantity': 0, 'condition': 1., 'family': family, 'standard_id': family, 'uses_ammunition': uses_ammunition})
    item['quantity'] += quantity
    emit(world, 'equipment_issued', actor=actor, region=f['location'], formation_id=formation_id, category=category, quantity=quantity)
    return copy.deepcopy(item)


def transfer_equipment(world, source, destination, category, quantity, *, receipt_id):
    from .military_logistics import is_stationary
    """A retired generation can move to a co-located reserve, preserving wear."""
    s = state(world)
    key = 'equipment-transfer:' + receipt_id
    request = {'source': source, 'destination': destination, 'category': category, 'quantity': quantity}
    if key in s['logistics_receipts']:
        old = s['logistics_receipts'][key]
        if old['request'] != request:
            raise ValueError('Equipment transfer receipt reused')
        return copy.deepcopy(old)
    a, b = s['formations'][source], s['formations'][destination]
    if source == destination or a['actor'] != b['actor'] or a['location'] != b['location'] or not is_stationary(world, a) or not is_stationary(world, b):
        raise ValueError('Equipment transfer needs separate co-located own formations')
    item = a['equipment_state'].get(category)
    if item is None or type(quantity) is not int or quantity <= 0 or quantity > item['quantity']:
        raise ValueError('Transfer exceeds actual equipment')
    target = b['equipment_state'].get(category)
    signature = lambda v: (v.get('family', v.get('standard_id')), bool(v.get('uses_ammunition', False)))
    if target and signature(target) != signature(item):
        raise ValueError('Incompatible equipment requires an explicit conversion programme')
    target = b['equipment_state'].setdefault(category, {**copy.deepcopy(item), 'quantity': 0})
    old = target['quantity']
    target['condition'] = (old * target.get('condition', 1) + quantity * item.get('condition', 1)) / (old + quantity)
    target['quantity'] += quantity
    item['quantity'] -= quantity
    receipt = {'kind': 'equipment_transfer', 'id': key, 'date': world.date, 'request': request, 'condition': target['condition']}
    s['logistics_receipts'][key] = receipt
    emit(world, 'equipment_transferred', actor=a['actor'], region=a['location'], receipt=receipt)
    return copy.deepcopy(receipt)


def train_replacements(world, replacement_id, recipient, manpower, *, duration_days=120,
                       allow_emergency=False, instruments=None, decision_actor=None, approval_receipt=None,
                       transport_mode='land'):
    from . import projects
    s = state(world)
    f = s['formations'][recipient]
    country = actor_country(world, f['actor'])
    key = 'replacement-cohort:' + replacement_id
    if replacement_id in s['replacements'] or type(duration_days) is not int or duration_days < 90 or country is None:
        raise ValueError('New national replacement cohort requires at least 90 days of training')
    from . import military_politics
    if decision_actor is not None and approval_receipt is None:
        raise ValueError('NPC replacements require an explicit political approval receipt')
    if approval_receipt is None:
        approval_receipt = 'replacement-approval:' + replacement_id
        military_politics.decide_replacement(world, approval_receipt, recipient, manpower,
            player_approval=True, duration_days=duration_days, allow_emergency=allow_emergency,
            instruments=instruments, transport_mode=transport_mode)
    approval = military_politics.verify_approval(world, approval_receipt, recipient, manpower,
        decision_actor=decision_actor, duration_days=duration_days, allow_emergency=allow_emergency,
        instruments=instruments, transport_mode=transport_mode)
    if approval['consumed_by'] is not None:
        raise ValueError('Political replacement approval was already consumed')
    cost = max(1, manpower)
    cohort = recruit(world, key, f['actor'], manpower, f['origin_region'], training=.1, service_type='reserve')
    cohort = s['formations'][key]
    cohort['current_order'] = 'training:' + replacement_id
    country = actor_country(world, f['actor'])
    project_id = 'replacement-training:' + replacement_id
    p = projects.plan_military(world, project_id, country, f['origin_region'], 'training', duration_days, cost,
        requirements={'resources': {}, 'minimum_labour': 0, 'economy_enabled': True},
        metadata={'replacement_id': replacement_id})
    old_cash, old_debt = world.countries[country].treasury, world.countries[country].debt
    projects.fund(world, p, allow_emergency=allow_emergency, instruments=instruments)
    book_interim(world, country, old_cash, old_debt)
    s['replacements'][replacement_id] = {'id': replacement_id, 'recipient': recipient, 'cohort': key,
        'project': project_id, 'status': 'training', 'started_at': world.date, 'completed_at': None,
        'manpower': manpower, 'minimum_duration': 90, 'receipt': None,
        'political_decision_id': approval_receipt}
    military_politics.consume_approval(world, approval_receipt, recipient, manpower,
        replacement_id=replacement_id, decision_actor=decision_actor, duration_days=duration_days,
        allow_emergency=allow_emergency, instruments=instruments, transport_mode=transport_mode)
    return copy.deepcopy(s['replacements'][replacement_id])


def project_conditions(world, project):
    s = state(world)
    meta = project.get('metadata', {})
    replacement = s['replacements'].get(meta.get('replacement_id'))
    if replacement:
        f = s['formations'][replacement['cohort']]
        from .military_logistics import is_stationary
        ready = f['location'] == f['origin_region'] and is_stationary(world, f) and f['manpower'] > 0 and f['supply_state']['last_ratio'] >= .5
        return {'ready': ready, 'reason': 'replacements need actual supplied training at their recruitment center'}
    production = meta.get('equipment_production') or meta.get('fortress_production')
    if production:
        actor = production['actor']
        ready = s['controls'].get(project['region'], {}).get('controller', actor) == actor
        return {'ready': ready, 'reason': 'reserved physical production can continue only at an accessible plant'}
    return {'ready': False, 'reason': 'unknown military project completion adapter'}


def on_project_completed(world, project):
    s = state(world)
    meta = project.get('metadata', {})
    duration = sum(stage['days'] for stage in project['stages'])
    if project['spent'] != project['cost'] or not project['funded_date'] or project['elapsed_days'] < duration:
        raise ValueError('Unfunded or instant military project completion')
    production = meta.get('equipment_production')
    if production:
        if production['receipt'] is not None:
            return
        actor, category = production['actor'], production['category']
        key = category + (':' + production['family'] if production['family'] else ':firearm' if production['uses_ammunition'] else '')
        a = s['actors'][actor]
        # Completed equipment is local; home-depot reserve is a deliberately narrow v1 adapter.
        if project['region'] != a['home_region']:
            depot_id = 'equipment-plant:' + project['region'] + ':' + actor
            from .military_logistics import add_depot
            if depot_id not in s['depots']:
                add_depot(world, depot_id, actor, project['region'])
            pool = s['depots'][depot_id]['stocks']
            pool['replacement_equipment:' + key] = pool.get('replacement_equipment:' + key, 0) + production['quantity']
        else:
            a['equipment_reserve'][key] = a['equipment_reserve'].get(key, 0) + production['quantity']
        production['receipt'] = {'project': project['id'], 'date': world.date, 'quantity': production['quantity']}
        emit(world, 'equipment_production_completed', actor=actor, region=project['region'], receipt=production['receipt'])
    elif meta.get('fortress_production'):
        building = meta['fortress_production']
        if building['receipt'] is not None:
            return
        actor, region = building['actor'], project['region']
        world.regions[region].infrastructure['fortifications'] = world.regions[region].infrastructure.get('fortifications', 0) + building['strength']
        if region not in s['fortresses']:
            register_fortress(world, region, actor, strength=world.regions[region].infrastructure['fortifications'])
        else:
            s['fortresses'][region]['strength'] += building['strength']
        building['receipt'] = {'project': project['id'], 'date': world.date, 'strength': building['strength']}
        emit(world, 'fortress_completed', actor=actor, region=region, receipt=building['receipt'])
    elif meta.get('replacement_id') in s['replacements']:
        item = s['replacements'][meta['replacement_id']]
        if item['receipt'] is not None:
            return
        f = s['formations'][item['cohort']]
        education = world.economy['regions'][f['origin_region']]['education']
        f['training'] = min(.65, .35 + .15 * education['literacy'] + .15 * education['technical'])
        f['current_order'] = None
        item.update(status='ready', completed_at=world.date, receipt={'date': world.date, 'cohort': f['id'], 'trained_manpower': f['manpower']})
        emit(world, 'replacements_trained', actor=f['actor'], region=f['origin_region'], replacement_id=item['id'], manpower=f['manpower'])
    else:
        raise ValueError('Unknown military completion adapter')


def incorporate_replacements(world, replacement_id):
    from .military_logistics import is_stationary
    from . import labour
    s = state(world)
    item = s['replacements'][replacement_id]
    if item['status'] == 'incorporated':
        return copy.deepcopy(item)
    f, cohort = s['formations'][item['recipient']], s['formations'][item['cohort']]
    if item['status'] != 'ready' or f['location'] != cohort['location'] or not is_stationary(world, cohort) or not is_stationary(world, f) or cohort['current_order']:
        raise ValueError('Trained replacements must actually arrive and be free of another operation')
    if f['origin_region'] != cohort['origin_region']:
        raise ValueError('Mixed-region cohorts must remain separate formations in v1')
    count = cohort['manpower']
    if count <= 0 or cohort['equipment_state'].get('personal_weapons', {}).get('quantity', 0) < cohort['combat_manpower']:
        raise ValueError('Trained replacements also require real weapons')
    for category, equipment in cohort['equipment_state'].items():
        if category in f['equipment_state'] and (f['equipment_state'][category].get('family', f['equipment_state'][category].get('standard_id')) != equipment.get('family', equipment.get('standard_id')) or bool(f['equipment_state'][category].get('uses_ammunition', False)) != bool(equipment.get('uses_ammunition', False))):
            raise ValueError('Incompatible replacement equipment family')
    old = f['manpower']
    for quality in ('training', 'experience', 'cohesion', 'morale', 'fatigue'):
        f[quality] = (f[quality] * old + cohort[quality] * count) / (old + count)
    for name in ('manpower', 'combat_manpower', 'support_manpower'):
        f[name] += cohort[name]
        cohort[name] = 0
    for category, equipment in list(cohort['equipment_state'].items()):
        target = f['equipment_state'].setdefault(category, {**equipment, 'quantity': 0})
        old_quantity = target['quantity']
        target['condition'] = (old_quantity * target.get('condition', 1.) + equipment['quantity'] * equipment.get('condition', 1.)) / max(1e-12, old_quantity + equipment['quantity'])
        target['quantity'] += equipment['quantity']
    cohort['equipment_state'] = {}
    for commodity, quantity in cohort['supply_state']['stocks'].items():
        pool = f['supply_state']['stocks']
        pool[commodity] = pool.get(commodity, 0) + quantity
    cohort['supply_state']['stocks'] = {}
    legacy = f['labour_backing'].get('legacy_army')
    if legacy:
        r = world.economy['regions'][f['origin_region']]['labour_state']
        r['mobilized'] -= count
        r['standing_army'] += count
        world.armies[legacy].personnel += count
        labour._write_region(world, f['origin_region'])
        labour.aggregate(world, actor_country(world, f['actor']))
    item['status'] = 'incorporated'
    item['incorporated_at'] = world.date
    emit(world, 'replacements_incorporated', actor=f['actor'], region=f['location'], replacement_id=replacement_id, manpower=count)
    return copy.deepcopy(item)
