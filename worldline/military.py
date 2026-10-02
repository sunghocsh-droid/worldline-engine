"""Military scheduler and adapters to the existing causal Event pipeline."""
import copy
import math
from .models import canonical_date
from .military_state import state, initialize, stream, LOSS_CAUSES
from . import military_bridge as bridge
from .military_active import executing_operations

VERSION = '1'

def _audience(world, actor, region=None):
    country = bridge.actor_country(world, actor) if actor in state(world)['actors'] else None
    government = world.npc_state['governments'].get(country, {})
    values = [government.get('ruler'), *government.get('cabinet', [])]
    values.extend(f.get('commander') for f in state(world)['formations'].values()
                  if f['actor'] == actor and (region is None or f['location'] == region))
    return sorted({key for key in values if key in world.npc_state['persons']})

def publish(world):
    """Important military facts are existing Crisis events, not a second ledger."""
    s = state(world)
    pending, s['outbox'] = s['outbox'], []
    from . import crisis, crisis_state, crisis_effects, crisis_response, npc_state
    records = []
    for record in pending:
        # Routine maintenance/progress remain ordinary system ledger entries.
        if record['kind'] not in ('operation_result', 'innovation_stage_completed', 'formation_raised', 'demobilized', 'military_local_action'):
            records.append(record)
            continue
        actor, region = record.get('actor'), record.get('region')
        country = bridge.actor_country(world, actor) if actor in s['actors'] else None
        if region is None and record.get('locations'):
            region = record['locations'][0]
        if region is None:
            region = s['actors'].get(actor, {}).get('home_region')
        if region not in world.crisis_state['regions']:
            records.append(record)
            continue
        involved = [country] if country else [world.regions[region].owner]
        result = record.get('result', {})
        enemy = result.get('opponent')
        enemy_country = bridge.actor_country(world, enemy) if enemy in s['actors'] else None
        if enemy_country and enemy_country not in involved:
            involved.append(enemy_country)
        operation = s['operations'].get(result.get('operation_id') or record.get('operation_id'), {})
        parent = operation.get('params', {}).get('crisis_event')
        if parent not in world.crisis_state['events']:
            parent = None
        event = crisis.create_event(world, 'custom:military_' + record['kind'], region, involved,
            causes=['military formation/operation funded and physically executed'], parent_event=parent,
            actual_facts=record, severity=.25 if record['kind'] != 'operation_result' else .5)
        event_id = event['event_id']
        if record['kind'] == 'military_local_action' and parent and world.crisis_state['events'][parent]['ongoing']:
            crisis_effects.enqueue(world, parent, 'trigger', {'kind': 'local_unilateral_action',
                'actor': record.get('commander'), 'operation_id': record['operation_id'],
                'misinformation': record.get('observed_facts', {}).get('misinformation', False),
                'central_intent': record.get('central_received_intent')},
                idempotency_key='military-local:' + event_id)
        if record['kind'] == 'operation_result':
            receipt = s['loss_receipts'][record['result_id']]
            receipt['event_id'] = event_id
            for origin, count in sorted(receipt['deaths'].items()):
                crisis_effects.enqueue(world, event_id, 'population_loss', {'region': origin, 'count': count, 'loss_kind': 'war_losses'},
                                       idempotency_key='military:' + record['result_id'] + ':deaths:' + origin)
            for owner in involved:
                total = sum(sum(x.values()) for key, x in result.get('casualties', {}).items()
                    if bridge.actor_country(world, s['formations'][key]['actor']) == owner)
                crisis_effects.enqueue(world, event_id, 'pressure_delta', {'category': 'civil_unrest',
                    'country': owner, 'delta': min(10, total / max(1, world.countries[owner].population) * 1000)})
            if operation.get('kind') in ('blockade', 'naval_attack'):
                crisis_effects.enqueue(world, event_id, 'economic_shock', {'region': region, 'kind': 'blockade',
                    'severity': min(1., result.get('objective_completion', 0)), 'duration_days': 30, 'one_off_effects': False})
        innovation = s['innovations'].get(record.get('innovation_id'), {})
        evidence = innovation.get('trial_evidence') if record.get('stage') == 'trial' else None
        subject = innovation.get('proposer') if innovation else None
        if not subject and country:
            subject = world.npc_state['governments'].get(country, {}).get('ruler')
        fact_id = None
        if subject and (evidence is not None or result.get('tactical_result') is not None):
            fact_id = 'military-evidence:' + event_id
            success = evidence['successful'] if evidence is not None else result.get('tactical_result') == 'success'
            npc_state.record_fact(world.npc_state, fact_id, world.date, subject,
                'administrative_performance' if evidence is not None else 'war_result',
                {'event_id': event_id, 'result': .6 if success else -.6,
                 'innovation': innovation.get('id'), 'innovation_id': innovation.get('id'),
                 'trial_success': success if evidence is not None else None,
                 'trial_evidence': evidence,
                 'operational_objective_completion': result.get('objective_completion'),
                 'basis': 'actual completed trial/operation; proposal alone earns no reputation'}, 'military measured outcome')
        audience = list(record.get('observers', _audience(world, actor, region)))
        if operation.get('commander'):
            audience.append(operation['commander'])
        for person in sorted(set(audience)):
            if person not in world.npc_state['persons']:
                continue
            local = world.npc_state['persons'][person].get('region') == region
            crisis_response.queue_report(world, event_id, person, record_id=fact_id,
                observed_facts={'military_result': result.get('tactical_result'),
                    'objective_completion': result.get('objective_completion'),
                    'own_casualties': sum(sum(v.values()) for k, v in result.get('casualties', {}).items()
                        if s['formations'][k]['actor'] == actor), 'innovation_trial': evidence},
                source='direct military observation' if local else 'military dispatch',
                direct=local, salience=.85 if local else .4,
                personal_impact=-.4 if local and record['kind'] == 'operation_result' else 0,
                emotional_valence=.5 if evidence and evidence['successful'] else -.4 if evidence else 0,
                interpretation={'lesson': 'measured trial success' if evidence and evidence['successful'] else 'measured trial failure' if evidence else 'reported operational outcome'})
        if record['kind'] == 'operation_result':
            sides = {s['formations'][key]['actor'] for key in result.get('casualties', {})}
            for side in sorted(sides - {actor}):
                side_country = bridge.actor_country(world, side)
                ruler = world.npc_state['governments'].get(side_country, {}).get('ruler')
                side_fact = None
                own_outcome = 'setback' if result.get('tactical_result') == 'success' else 'success' if result.get('tactical_result') == 'setback' else None
                if ruler and own_outcome:
                    side_fact = 'military-evidence:' + event_id + ':' + side
                    npc_state.record_fact(world.npc_state, side_fact, world.date, ruler, 'war_result',
                        {'event_id': event_id, 'result': .6 if own_outcome == 'success' else -.6,
                         'basis': 'own force after-action report; battle outcome is not the political war aim'}, 'own military measured outcome')
                own_casualties = sum(sum(counts.values()) for key, counts in result.get('casualties', {}).items() if s['formations'][key]['actor'] == side)
                for person in _audience(world, side, region):
                    local = world.npc_state['persons'][person].get('region') == region
                    crisis_response.queue_report(world, event_id, person, record_id=side_fact,
                        observed_facts={'military_result': own_outcome, 'own_casualties': own_casualties},
                        source='own unit after-action dispatch', direct=local, salience=.85 if local else .4,
                        personal_impact=-.4 if local and own_casualties else 0,
                        interpretation={'lesson': 'own force reported losses and outcome; enemy inventories are unknown'})
        records.extend(crisis_effects.apply_due(world))
        # Outcome facts are completed occurrences, not political choices imposed on a player.
        crisis_state.close_event(world, event_id, 'resolved', residual_pressure={})
    records.extend(crisis_response.deliver_due(world))
    return records + crisis.drain_outbox(world)

def _link_crises(world):
    s = state(world)
    for key, event in world.crisis_state['events'].items():
        if key in s['war_links']:
            s['theaters'][s['war_links'][key]]['status'] = 'active' if event['ongoing'] else 'closed'
            continue
        if event['event_type'] not in ('armed_clash', 'state_of_war'):
            continue
        theater = 'theater:' + key
        s['war_links'][key] = theater
        s['theaters'][theater] = {'id': theater, 'crisis_event': key, 'actors': list(event['countries']),
            'region': event['region'], 'status': 'active' if event['ongoing'] else 'closed',
            'war_aims': {}, 'operations': [], 'created_at': world.date,
            'basis': 'real Crisis occurrence; national offensive still requires authorized orders'}

def _physical_arrival(world, key, formation, receipt, arrived):
    """Validate the actual scheduler's leg, rather than infer transport from foot speed."""
    s = state(world)
    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    if receipt.get('formation') != key or receipt.get('date') != world.date:
        raise ValueError('Military arrival has no current physical receipt')
    departure, arrival = canonical_date(receipt['departure']), canonical_date(receipt['date'])
    elapsed = (arrival - departure).days
    if elapsed < 1 or type(receipt.get('elapsed_days')) is not int or receipt['elapsed_days'] != elapsed:
        raise ValueError('Military arrival elapsed days do not match the actual calendar')
    link = s['links'].get(receipt.get('link'))
    if not link or receipt.get('mode') != link['mode'] or not (
        (receipt['origin'], receipt['destination']) == (link['origin'], link['destination']) or
        link.get('bidirectional', True) and (receipt['origin'], receipt['destination']) == (link['destination'], link['origin'])):
        raise ValueError('Military arrival uses no actual matching route link')
    distance = receipt.get('distance_km')
    if not number(distance) or not math.isclose(distance, link['distance_km'], rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError('Military arrival distance differs from the actual completed route')
    operation_id, shipment_id = receipt.get('operation_id'), receipt.get('shipment')
    expected_reservations = set()
    if operation_id is not None:
        operation = s['operations'].get(operation_id)
        if not operation or key not in operation['formations'] or operation['execution_started_at'] is None or (
            canonical_date(operation['execution_started_at']) > departure):
            raise ValueError('Military arrival has no physically executing operation')
        legs = operation['route_quote'].get(key, {}).get('legs', [])
        matching = [i for i, leg in enumerate(legs) if leg['link'] == receipt['link'] and
            leg['from'] == receipt['origin'] and leg['to'] == receipt['destination'] and
            leg['mode'] == receipt['mode'] and math.isclose(leg['distance_km'], distance, rel_tol=1e-9, abs_tol=1e-9)]
        matching = [i for i in matching if receipt.get('id') == f"movement:{operation_id}:{key}:{receipt.get('phase', 'advance')}:{i}"]
        if not matching or not any(r.get('operation_id') == operation_id and r.get('origin') == receipt['origin'] and
            r.get('destination') == receipt['destination'] for r in arrived.get(key, [])):
            raise ValueError('Military arrival has no actual completed operation leg/arrival')
        command = s['commands'].get(operation.get('latest_command'), {})
        if command.get('message_kind') != 'command' or command.get('status') != 'delivered' or not command.get('belief_accepted') or (
            canonical_date(command['received_at']) > arrival):
            raise ValueError('Military movement started without a received actual command')
        for index in matching:
            expected_reservations.add(f"movement:{operation_id}:{key}:{index}")
            for origin, group in operation.get('embarkation', {}).items():
                if key in group['fleets'] + group['troops']:
                    expected_reservations.add(f"amphibious:{operation_id}:{origin}:{receipt.get('phase', 'advance')}:{index}")
    elif shipment_id is not None:
        shipment, convoy = s['shipments'].get(shipment_id), s['convoys'].get(receipt.get('convoy'))
        if not shipment or not convoy or shipment.get('convoy') != convoy['id'] or key not in convoy['formations'] + convoy.get('escorts', []) or (
            shipment['actor'] != formation['actor'] or convoy['actor'] != formation['actor']):
            raise ValueError('Military arrival has no actual assigned convoy/shipment')
        legs = shipment['route']['legs']
        matching = [i for i, leg in enumerate(legs) if leg['link'] == receipt['link'] and
            leg['from'] == receipt['origin'] and leg['to'] == receipt['destination'] and
            leg['mode'] == receipt['mode'] and math.isclose(leg['distance_km'], distance, rel_tol=1e-9, abs_tol=1e-9)]
        matching = [i for i in matching if receipt.get('id') == f"convoy-movement:{shipment_id}:{key}:{i}"]
        if not matching or shipment['last_tick'] != world.date or shipment.get('leg_departure') != world.date or (
            shipment['location'] != receipt['destination'] or shipment['leg_index'] <= matching[0] or shipment['leg_progress'] != 0):
            raise ValueError('Military convoy receipt is not a leg physically completed today')
        expected_reservations.add('cargo:' + shipment_id + ':' + world.date)
    else:
        raise ValueError('Military arrival has neither actual operation nor convoy')
    bookings = s['transport_reservations'].get(world.date, {}).get(receipt['link'], {}).get('reservations', {})
    if not any(r.get('id') in expected_reservations and r.get('date') == world.date and r.get('link') == receipt['link'] and
        r.get('actor') == formation['actor'] and number(r.get('reserved')) and r['reserved'] > 0 and
        number(r.get('requested')) and r['requested'] > 0 for r in bookings.values()):
        raise ValueError('Military arrival lacks its actual positive transport capacity reservation')
    # Rail, rivers and ships use their declared connection's nominal duration.
    # Reversing today's disruption/fatigue into yesterday's speed would reject
    # a legitimate journey after conditions changed. Foot bounds stay unchanged.
    minimum = max(1, math.ceil(link['travel_days'])) if link['mode'] in ('rail', 'river', 'sea') else max(
        1, math.ceil(distance / max(1, formation['mobility']['speed_km_day'] * 2)))
    if elapsed < minimum:
        raise ValueError('Military arrival precedes the actual route minimum duration')
    return minimum


def _legacy_locations(world):
    s = state(world)
    changes = [(key, f, f['labour_backing']['legacy_army']) for key, f in s['formations'].items()
        if f['labour_backing'].get('legacy_army') and world.armies[f['labour_backing']['legacy_army']].region != f['location']]
    if not changes:
        return
    # One call-local index replaces repeated scans of every past arrival.
    receipts, arrived = {}, {}
    for r in s['movement_receipts'].values():
        if r.get('date') == world.date:
            receipts.setdefault(r.get('formation'), []).append(r)
    for record in s['outbox']:
        if record.get('kind') == 'formation_arrived' and record.get('date') == world.date:
            arrived.setdefault(record.get('formation'), []).append(record)
    for key, f, legacy in changes:
        old = world.armies[legacy].region
        matches = [r for r in receipts.get(key, []) if r.get('destination') == f['location'] and r.get('origin') == old]
        if not matches:
            raise ValueError('No actual completed military route for legacy army relocation')
        r = matches[-1]
        minimum = _physical_arrival(world, key, f, r, arrived)
        s['movement_receipts'][legacy] = {**r, 'arrival': world.date,
            'minimum_days': minimum}
        world.armies[legacy].region = f['location']

def _upkeep(world):
    from . import military_logistics as logistics
    s = state(world)
    active = {key for op in executing_operations(s['operations']) for key in op['formations']}
    for key, f in sorted(s['formations'].items()):
        if not f['manpower']:
            continue
        fortress = s['fortresses'].get(f['location'], {})
        if key in fortress.get('garrison', []):
            needed = logistics.requirements(world, f).get('food', 0)
            pool = f['supply_state']['stocks']
            transfer = min(fortress['food'], max(0, needed - pool.get('food', 0)))
            fortress['food'] -= transfer
            pool['food'] = pool.get('food', 0) + transfer
        logistics.consume_supply(world, key, receipt_id='daily-supply:' + key + ':' + world.date)
        supply = f['supply_state']['last_ratio']
        f['readiness'] = min(1., max(0., f['readiness'] + (.002 if supply >= .9 else -.01 * (1 - supply))))
        if key not in active:
            f['fatigue'] = max(0., f['fatigue'] - .01 * supply)
        # Count non-combat losses weekly; quiet formations use only cheap upkeep.
        if key not in active and supply >= .5:
            continue
        if world.date < s.setdefault('attrition_due', {}).get(key, world.date):
            continue
        from .military_state import after
        s['attrition_due'][key] = after(world.date, 7)
        r = world.economy['regions'].get(f['origin_region'], {})
        health = r.get('demography', {}).get('health_level', .2)
        medical = f['supply_state'].get('component_ratios', {}).get('medical', 0.)
        rates = {'disease': .0003 * (1 - health) * (1 - .5 * medical),
                 'hunger': .002 * (1 - supply), 'exposure': .0004 * f['fatigue'],
                 'desertion': .0005 * (1 - f['morale']) * (1 - supply),
                 'march_attrition': .0004 * f['fatigue'] if not logistics.is_stationary(world, f) else 0.}
        counts = dict.fromkeys(LOSS_CAUSES, 0)
        with stream(world, 'attrition:' + key) as rng:
            for cause, rate in rates.items():
                expected = f['manpower'] * rate
                counts[cause] = math.floor(expected) + int(rng.random() < expected % 1)
        if sum(counts.values()) > f['manpower']:
            counts = dict.fromkeys(LOSS_CAUSES, 0)
            counts['hunger'] = f['manpower']
        if any(counts.values()):
            bridge.apply_result(world, {'id': 'attrition:' + key + ':' + world.date, 'actor': f['actor'],
                'region': f['location'], 'operation_id': None, 'casualties': {key: counts},
                'equipment_losses': {}, 'supply_consumption': {}, 'unit_changes': {},
                'reason_trace': {'rates': rates, 'supply': supply, 'health': health, 'basis': 'weekly non-combat loss cohorts'}})

def tick(world, tick_event, rng):
    s = state(world)
    if s['last_tick'] == world.date:
        return []
    from . import military_logistics as logistics, military_operations as operations, military_innovation as innovation
    bridge.reconcile(world)
    _link_crises(world)
    logistics.tick(world, tick_event, rng)
    _upkeep(world)
    operations.tick(world, tick_event, rng)
    innovation.tick(world, tick_event, rng)
    _legacy_locations(world)
    s['last_tick'] = world.date
    return publish(world)

def constraints(world, actor, formation_ids=None, amount=0, *, allow_emergency=True, perceived=None):
    from .military_politics import burden_projection, willingness
    s = state(world)
    country = bridge.actor_country(world, actor)
    ids = formation_ids if formation_ids is not None else [k for k, f in s['formations'].items() if f['actor'] == actor]
    if not isinstance(ids, (list, tuple)) or any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)):
        raise ValueError('Military constraints require unique formation IDs')
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
        raise ValueError('Military funding estimate must be finite and nonnegative')
    if any(key not in s['formations'] or s['formations'][key]['actor'] != actor for key in ids):
        raise ValueError('Military constraints may expose only formations belonging to this actor')
    units = [s['formations'][k] for k in ids]
    actual = {'manpower': sum(f['manpower'] for f in units),
              'ready_manpower': sum(f['combat_manpower'] * f['readiness'] * f['supply_state']['last_ratio'] for f in units),
              'supply_ratios': {f['id']: f['supply_state']['last_ratio'] for f in units},
              'political_burdens': burden_projection(world, actor)}
    if country:
        from .credit import assess
        quote = assess(world, country, amount, allow_emergency=allow_emergency, perceived=perceived)
    else:
        enough = s['actors'][actor]['treasury'] >= amount
        quote = {'physical_feasibility': {'possible': actual['manpower'] > 0, 'feasible': actual['manpower'] > 0},
            'normal_financial_feasibility': {'possible': enough, 'feasible': enough}, 'emergency_financial_feasibility': {'possible': enough, 'feasible': enough},
            'expected_consequences': {'treasury_after': s['actors'][actor]['treasury'] - amount}}
    return {'actual': actual, 'perceived': copy.deepcopy(perceived or {}), 'finance': quote,
        'political_will': willingness(world, actor),
        'basis': 'capacity and political willingness are distinct; risk does not cancel authorized choices'}
