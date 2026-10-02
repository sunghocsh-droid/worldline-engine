"""Formation/front adjudication, not tactical units or individual weapon HP."""
import math
from .military_state import state, stream, LOSS_CAUSES

def _equipment(f, key):
    e = f['equipment_state'].get(key, {})
    return e.get('quantity', 0.) * e.get('condition', 1.)

def _commander(world, f, operation_commander=None):
    p = world.npc_state['persons'].get(operation_commander or f.get('commander'), {})
    abilities = p.get('abilities', {})
    return .5 * abilities.get('military', .3) + .3 * abilities.get('organization', .3) + .2 * abilities.get('intelligence', .3)

def command_doctrine(formation, communication_delay_days=0):
    """Institutionalized initiative limits coordination loss, not message travel."""
    d = formation['doctrine']
    delay = max(0., float(communication_delay_days))
    educated_staff = min(formation['training'], d['staff_capability'])
    initiative = d['mission_oriented_command'] * d['adoption'] * educated_staff
    initiative *= d['subordinate_initiative'] * (1 - d['punishment_risk_culture'])
    initiative *= .5 + .5 * (1 - d['centralized_control'])
    # The bounded coordination penalty is separate from physical delivery.
    # A concept alone, with no training/organization, cannot remove it.
    burden = delay / (delay + 7) if delay else 0.
    communication = d['communication_quality']
    penalty = .5 * burden * (1 - .5 * communication) * (1 - initiative)
    return {'factor': 1 - penalty, 'effective_initiative': initiative,
        'communication_delay_days': delay, 'educated_staff': educated_staff,
        'communication_quality': communication, 'doctrine_axes': dict(d),
        'basis': 'trained and adopted local initiative mitigates coordination loss; orders still arrive at their actual received_at'}


def capability(world, formation_id, *, terrain=1., fortification=0., weather=1., intelligence=.5, concentration=1., naval=False, operation_commander=None, communication_delay_days=0):
    f = state(world)['formations'][formation_id]
    n = f['combat_manpower']
    weapons = min(1., _equipment(f, 'personal_weapons') / max(1, n))
    artillery = min(1., _equipment(f, 'artillery') * 40 / max(1, n))
    mobility = min(1., _equipment(f, 'horses') / max(1, n))
    engineer = min(1., _equipment(f, 'engineering') * 20 / max(1, n))
    supply = min(1., max(0., f['supply_state'].get('last_ratio', 0.)))
    ratios = f['supply_state'].get('component_ratios', {})
    ammunition = min((value for key, value in ratios.items() if key.split(':', 1)[0] == 'ammunition'), default=supply)
    ammunition_demand = .002 * n if f['equipment_state'].get('personal_weapons', {}).get('uses_ammunition', False) else 0.
    ammunition_demand += .03 * _equipment(f, 'artillery')
    ammunition_requests = {}
    if ammunition_demand:
        from .military_logistics import _expand_requests
        ammunition_requests = _expand_requests(world, f, {'ammunition': ammunition_demand})
        ammunition = min(ammunition, min(f['supply_state']['stocks'].get(key, 0) / amount for key, amount in ammunition_requests.items()))
        ammunition = min(1., max(0., ammunition))
    doctrine = f['doctrine']
    adoption = doctrine.get('adoption', 0.)
    command = _commander(world, f, operation_commander)
    control = command_doctrine(f, communication_delay_days)
    factors = {'training': .3 + .7 * f['training'], 'experience': .75 + .25 * f['experience'],
        'equipment': .35 + .5 * weapons * (ammunition if f['equipment_state'].get('personal_weapons', {}).get('uses_ammunition', False) else 1.) + .35 * artillery * ammunition + .1 * mobility,
        'organization': .5 + .3 * f['cohesion'] + .2 * f['readiness'],
        'morale': .4 + .6 * f['morale'], 'fatigue': 1 - .7 * f['fatigue'],
        'supply': .1 + .9 * supply, 'command': .75 + .5 * command,
        'doctrine': 1 + .25 * adoption * (.5 * doctrine.get('staff_capability', .3) + .5 * doctrine.get('planning_flexibility', .3)),
        'communication_and_initiative': control['factor'],
        'intelligence': .75 + .5 * intelligence, 'terrain': terrain,
        'fortification': 1 + fortification * (.5 + engineer), 'weather': weather,
        'concentration': min(1.5, max(.3, concentration))}
    base = float(n)
    if naval:
        base = _equipment(f, 'ships') * math.sqrt(max(1, f['manpower']))
        factors['crew'] = .4 + .6 * f['training']
    power = base
    for value in factors.values():
        power *= value
    return {'formation': formation_id, 'base': base, 'effective_capacity': power,
            'factors': factors, 'engineer_capability': engineer, 'artillery': artillery,
            'supply_ratio': supply, 'ammunition_ratio': ammunition, 'ammunition_requests': ammunition_requests,
            'command_doctrine_trace': control}

def resolve(world, operation):
    """Execution adjudicator may read physical truth; political prediction may not."""
    s = state(world)
    attack_ids = [k for k in operation['formations'] if k in s['formations'] and s['formations'][k]['manpower'] > 0]
    region = operation.get('target') or (s['formations'][attack_ids[0]]['location'] if attack_ids else None)
    params = operation.get('params', {})
    opponent = operation.get('opponent') or params.get('opponent')
    defenders = params.get('defenders')
    if defenders is None:
        defenders = sorted(k for k, f in s['formations'].items() if f['location'] == region and f['actor'] != operation['actor']
            and (opponent is None or f['actor'] == opponent) and f['manpower'] > 0)
    defenders = [k for k in defenders if k in s['formations'] and s['formations'][k]['manpower'] > 0
                 and s['formations'][k]['location'] == region and s['formations'][k]['actor'] != operation['actor']
                 and (opponent is None or s['formations'][k]['actor'] == opponent)]
    attack_ids = [k for k in attack_ids if s['formations'][k]['location'] == region]
    if not opponent and defenders:
        opponent = s['formations'][defenders[0]]['actor']
    kind = operation['kind']
    naval = kind in ('naval_attack', 'naval_interception', 'interception', 'blockade', 'escort', 'convoy_escort')
    if naval:
        attack_ids = [key for key in attack_ids if _equipment(s['formations'][key], 'ships') > 0]
        defenders = [key for key in defenders if _equipment(s['formations'][key], 'ships') > 0]
    elif kind in ('amphibious', 'amphibious_operation'):
        # Transport crew and landed troops are different manpower cohorts.
        attack_ids = [key for key in attack_ids if _equipment(s['formations'][key], 'ships') == 0]
        defenders = [key for key in defenders if _equipment(s['formations'][key], 'ships') == 0]
    fortress = s['fortresses'].get(region, {})
    strength = max(0., fortress.get('strength', 0.))
    weather = min(1., max(.2, params.get('weather_factor', 1.)))
    terrain = min(1.5, max(.3, params.get('terrain_factor', 1.)))
    command_message = s['commands'].get(operation.get('latest_command'), {})
    coordination_delay = command_message.get('communication_delay', 0) if command_message.get('received_at') else 0
    attacking = [capability(world, k, terrain=terrain, weather=weather,
        intelligence=params.get('actual_intelligence_quality', .5),
        concentration=params.get('concentration', 1.), naval=naval, operation_commander=operation.get('commander'),
        communication_delay_days=coordination_delay) for k in attack_ids]
    defending = [capability(world, k, terrain=1 / terrain, weather=weather,
        fortification=strength / (strength + 100), naval=naval) for k in defenders]
    a = sum(x['effective_capacity'] for x in attacking)
    d = sum(x['effective_capacity'] for x in defending)
    siege_days = operation.get('investment_days', operation.get('engagement_days', operation.get('elapsed_days', 1)))
    assault = params.get('assault', False) or params.get('siege_style') == 'assault'
    if kind == 'siege':
        capability_siege = sum(x['engineer_capability'] + x['artillery'] * x['ammunition_ratio'] for x in attacking)
        # A fortress can be worn down by supplied investment; relief still matters.
        isolation = min(1., max(0., params.get('blockade', .5)))
        relief = min(1., max(0., fortress.get('relief_probability', .2)))
        erosion = siege_days * (.005 + .008 * capability_siege) * isolation * (1 - relief)
        a /= 1 + strength / max(1, a) * (1.5 if not assault else .6)
        d *= max(.2, 1 - erosion)
        # Stored fortress food and supplied relief matter independently of walls.
        garrison = [s['formations'][k] for k in fortress.get('garrison', []) if k in s['formations'] and s['formations'][k]['manpower']]
        if garrison:
            nourishment = sum(f['supply_state']['last_ratio'] for f in garrison) / len(garrison)
            d *= .3 + .7 * nourishment
    identifier = f"military-result:{operation['id']}:{len(operation.get('results', [])) + 1}"
    with stream(world, 'combat:' + operation['id']) as rng:
        uncertainty = .85 + .3 * rng.random()
        a_real = a * uncertainty
        tactical_win = a_real > d
        share = a_real / max(1e-9, a_real + d)
        supply = sum(x['supply_ratio'] for x in attacking) / max(1, len(attacking))
        progress = share * supply * min(1., max(0., params.get('objective_reach', 1.)))
        if not defenders:
            tactical_win = a > 0
            progress = supply * params.get('objective_reach', 1.) if a > 0 else 0
        if kind in ('withdraw', 'defend', 'hold', 'relief') and params.get('war_aim') == 'delay':
            progress = min(1., siege_days / max(1, params.get('required_delay_days', 7)))
        casualties, equipment, supplies, changes = {}, {}, {}, {}
        for role, ids in (('attacker', attack_ids), ('defender', defenders)):
            opposing = d if role == 'attacker' else a_real
            own = a_real if role == 'attacker' else d
            intensity = min(.08, .006 + .024 * opposing / max(1, opposing + own)) if opposing else 0.
            if kind == 'siege':
                intensity *= 2 if assault else .35
            if kind in ('raid', 'withdraw', 'relief'): intensity *= .5
            for key in ids:
                f = s['formations'][key]
                expected = f['manpower'] * intensity * (.8 + .4 * rng.random())
                killed = min(f['manpower'], math.floor(expected) + int(rng.random() < expected % 1))
                losses = dict.fromkeys(LOSS_CAUSES, 0)
                losses['naval_loss' if naval else 'battle_casualties'] = killed
                if role == 'attacker' and not tactical_win and f['cohesion'] < .35:
                    losses['capture_missing'] = min(f['manpower'] - killed, int(f['manpower'] * .005))
                casualties[key] = losses
                equipment[key] = {category: min(item['quantity'], item['quantity'] * killed / max(1, f['manpower']))
                                  for category, item in f['equipment_state'].items()}
                supplies[key] = {commodity: min(quantity, quantity * .1) for commodity, quantity in f['supply_state']['stocks'].items()
                                 if commodity.split(':', 1)[0] in ('ammunition', 'fuel', 'spare_parts')}
                view = capability(world, key, naval=naval)
                for commodity, amount in view['ammunition_requests'].items():
                    supplies[key][commodity] = min(f['supply_state']['stocks'].get(commodity, 0), amount)
                good = tactical_win == (role == 'attacker')
                changes[key] = {'cohesion_delta': .015 if good else -.04, 'morale_delta': .04 if good else -.05,
                                'fatigue_delta': .08 if assault else .03, 'experience_delta': .01 * (1 - f['fatigue']) if opposing else 0}
    won_objective = progress >= params.get('objective_threshold', .65)
    control = {'region': region, 'controller': operation['actor'], 'contested': False} if tactical_win and won_objective and kind in ('attack', 'offensive', 'siege', 'amphibious', 'relief') else None
    return {'id': identifier, 'operation_id': operation['id'], 'date': world.date, 'actor': operation['actor'],
        'opponent': opponent, 'region': region, 'objective_completion': min(1., max(0., progress)),
        'tactical_result': 'success' if tactical_win else 'setback', 'strategic_objective_achieved': won_objective,
        'control_change': control, 'casualties': casualties, 'equipment_losses': equipment,
        'supply_consumption': supplies, 'unit_changes': changes, 'momentum': (share - .5) * supply,
        'retreat': not tactical_win and share < .35, 'disorganization': 1 - share if not tactical_win else 0.,
        'intelligence': {'contact': bool(defenders), 'confidence': .7 if defenders else .2,
                         'observed_enemy_range': [int(sum(s['formations'][k]['manpower'] for k in defenders) * .7), int(sum(s['formations'][k]['manpower'] for k in defenders) * 1.3)]},
        'political_hooks': [{'kind': 'victory_prestige' if tactical_win else 'military_failure', 'actor': operation['actor'], 'value': progress}],
        'diplomatic_hooks': [{'kind': 'peace_pressure', 'actor': operation['actor'], 'opponent': opponent,
                             'military_result': 'success' if tactical_win else 'setback', 'war_aim': params.get('war_aim')}],
        'reason_trace': {'attacking_formations': attacking, 'defending_formations': defending,
                        'attacker_capacity': a, 'defender_capacity': d, 'uncertainty_factor': uncertainty,
                        'supply': supply, 'terrain': terrain, 'weather': weather,
                        'fortress_strength': strength, 'fortress_food': fortress.get('food'), 'relief_probability': fortress.get('relief_probability'), 'siege_days': siege_days, 'assault': assault,
                        'objective_completion': progress, 'basis': 'physical adjudication; no political objective automatically granted'}}
