from .incremental_audit import items as audit_items, json_check as audit_json
"""Finite military supplies and explicit regional transport, independent of UI.

Quantities are the economy's abstract goods units, not fabricated tonnes. Cargo
is withdrawn before departure and remains in transit until every leg completes.
An internal route planner caches only immutable quotes within one call batch.
"""
import copy
import heapq
import math

from .models import canonical_date
from .military_state import state, after, emit
from .military_active import active_shipments

FOOD_PER_PERSON_DAY = .08 / 365
SUPPLY_RECIPES = {
    'food': {'agriculture': 1.0}, 'fodder': {'agriculture': 1.0},
    'ammunition': {'chemicals': .6, 'heavy_industry': .4},
    'medical': {'chemicals': .5, 'light_industry': .5},
    'spare_parts': {'machinery': 1.0}, 'fuel': {'energy': 1.0}}
MODES = ('trail', 'road', 'rail', 'river', 'sea')
EPSILON = 1e-9


def _number(value, minimum=0, maximum=None):
    return (type(value) in (int, float) and math.isfinite(value) and value >= minimum
            and (maximum is None or value <= maximum))


def _identity(value):
    return isinstance(value, str) and bool(value)


def _ratio(value):
    return value / (value + 50) if value > 0 else 0.0


def is_stationary(world, formation):
    """A completed journey remains evidence, rather than ongoing movement."""
    f = state(world)['formations'][formation] if isinstance(formation, str) else formation
    journey = f.get('journey')
    if journey is None:
        return True
    if not isinstance(journey, dict) or not journey.get('arrival'):
        return False
    legs, index = journey.get('legs'), journey.get('leg_index')
    try:
        arrived = canonical_date(journey['arrival']) <= canonical_date(world.date)
    except (TypeError, ValueError):
        return False
    return bool(arrived and isinstance(legs, list) and type(index) is int and index == len(legs)
        and journey.get('destination') == f['location']
        and _number(journey.get('leg_progress_km', 0)) and journey.get('leg_progress_km', 0) <= EPSILON)


def initialize(world):
    s = state(world)
    s.setdefault('logistics_receipts', {})
    s.setdefault('last_logistics_tick', None)
    return s


def _receipts(world):
    return initialize(world)['logistics_receipts']


def _actor_country(world, actor):
    return state(world)['actors'][actor]['country'] if actor is not None else None


def _control(world, region):
    value = state(world).get('controls', {}).get(region, world.regions[region].owner)
    return value.get('controller', world.regions[region].owner) if isinstance(value, dict) else value


def _access(world, region, actor):
    if actor is None:
        return True
    s, country = state(world), _actor_country(world, actor)
    control = _control(world, region)
    if control in (actor, country) or country is None and region == s['actors'][actor]['home_region']:
        return True
    for relation in world.relations.values():
        if {relation.get('a'), relation.get('b')} == {country, control}:
            if relation.get('military_access') is True:
                return True
    return False


def add_link(world, link_id, origin, destination, *, actor=None, mode='road',
             throughput=1000.0, travel_days=1.0, distance_km=None, condition=1.0,
             disruption=0.0, seasonal_factor=1.0, bidirectional=True):
    s = state(world)
    mode = 'road' if mode == 'land' else mode
    if not _identity(link_id) or link_id in s['links'] or origin == destination or origin not in world.regions or destination not in world.regions:
        raise ValueError('Unique link and existing distinct region endpoints required')
    if actor is not None and actor not in s['actors'] or mode not in MODES or type(bidirectional) is not bool:
        raise ValueError('Invalid transport actor/mode/direction')
    if distance_km is None:
        a, b = world.regions[origin], world.regions[destination]
        distance_km = math.hypot(b.x - a.x, b.y - a.y)
    if not _number(throughput) or not _number(travel_days, EPSILON) or not _number(distance_km) or any(not _number(v, 0, 1) for v in (condition, disruption, seasonal_factor)):
        raise ValueError('Invalid finite transport distance/time/capacity/condition')
    link = {'id': link_id, 'actor': actor, 'origin': origin, 'destination': destination,
        'mode': mode, 'throughput': float(throughput), 'travel_days': max(1.0, float(travel_days)),
        'distance_km': float(distance_km), 'condition': float(condition),
        'disruption': float(disruption), 'seasonal_factor': float(seasonal_factor),
        'bidirectional': bidirectional}
    s['links'][link_id] = link
    s['revision'] += 1
    return copy.deepcopy(link)


def edge_capacity(world, link_id, actor=None):
    s = state(world)
    if actor is not None and actor not in s['actors']:
        raise ValueError('Unknown logistics actor')
    link = s['links'][link_id]
    infra = [world.regions[link[k]].infrastructure for k in ('origin', 'destination')]
    mode, access, reasons = link['mode'], 1.0, []
    if mode == 'rail':
        access = min(_ratio(r.get('railways', 0)) for r in infra)
        reasons.append({'factor': 'explicit_rail_connection', 'value': access})
    elif mode == 'road':
        access = min(.25 + .75 * _ratio(r.get('roads', 0)) if r.get('roads', 0) > 0 else 0 for r in infra)
        reasons.append({'factor': 'road_quality', 'value': access})
    elif mode == 'sea':
        access = min(_ratio(r.get('ports', 0)) for r in infra)
        reasons.append({'factor': 'port_loading_unloading', 'value': access})
    blockade = 0.0
    if mode == 'sea':
        try:
            from . import military_operations
            callback = getattr(military_operations, 'blockade_coverage', None)
            if callback:
                coverage = callback(world, link_id, actor)
                blockade = min(1.0, max(0.0, coverage.get('disruption', 0)))
                reasons.extend(coverage.get('reasons', []))
        except ImportError:
            pass
    factors = {'infrastructure': access, 'condition': link['condition'],
        'disruption': 1 - link['disruption'], 'season': link['seasonal_factor'],
        'blockade': 1 - blockade}
    capacity = link['throughput'] * math.prod(factors.values())
    reasons.extend({'factor': key, 'value': value} for key, value in factors.items() if key != 'infrastructure')
    day = s['transport_reservations'].get(world.date, {})
    used = day.get(link_id, {}).get('used', 0.0)
    return {'link': link_id, 'capacity_per_day': capacity, 'capacity': capacity,
        'reserved': used, 'remaining': max(0.0, capacity - used), 'factors': factors,
        'physical_feasibility': {'feasible': capacity > EPSILON}, 'reason_trace': reasons}


def reserve_capacity(world, link_id, amount, *, actor, reservation_id, date=None):
    s = state(world)
    day = date or world.date
    if day != world.date or actor not in s['actors'] or not _number(amount) or not _identity(reservation_id):
        raise ValueError('Capacity reservation requires current day/actor/finite amount/ID')
    quote = edge_capacity(world, link_id, actor)
    bucket = s['transport_reservations'].setdefault(day, {}).setdefault(link_id,
        {'used': 0.0, 'reservations': {}})
    if reservation_id in bucket['reservations']:
        old = bucket['reservations'][reservation_id]
        if old['requested'] != amount or old['actor'] != actor:
            raise ValueError('Capacity reservation ID reused with different request')
        return copy.deepcopy(old)
    quantity = min(amount, quote['remaining'])
    result = {'id': reservation_id, 'date': day, 'link': link_id, 'actor': actor,
        'requested': float(amount), 'reserved': quantity,
        'capacity_at_booking': quote['capacity_per_day'],
        'ratio': quantity / amount if amount else 1.0,
        'remaining': max(0.0, quote['remaining'] - quantity)}
    bucket['used'] += quantity
    bucket['reservations'][reservation_id] = result
    return copy.deepcopy(result)


class RoutePlanner:
    """A call-local batch view; never persisted or reused after world mutation."""
    def __init__(self, world, actor=None):
        self.world, self.actor, self.cache = world, actor, {}
        self.adjacency = {}
        for key, link in sorted(state(world)['links'].items()):
            self.adjacency.setdefault(link['origin'], []).append((link['destination'], key))
            if link.get('bidirectional', True):
                self.adjacency.setdefault(link['destination'], []).append((link['origin'], key))

    def quote(self, origin, destination, mode='land', formation=None, purpose='supply', allow_hostile_destination=False):
        # Only immutable route inputs are cached, within this synchronous batch.
        speed = transport_quote(self.world, formation)['speed_limit_km_day'] if formation else None
        key = (origin, destination, mode, speed, purpose, allow_hostile_destination)
        if key not in self.cache:
            self.cache[key] = self._quote(origin, destination, mode, formation, purpose, allow_hostile_destination)
        return copy.deepcopy(self.cache[key])

    def _quote(self, origin, destination, mode, formation, purpose, allow_hostile_destination):
        world, actor = self.world, self.actor
        if origin not in world.regions or destination not in world.regions or mode not in ('land', 'sea', 'any', *MODES):
            raise ValueError('Unknown route endpoints/mode')
        if actor is not None and actor not in state(world)['actors']:
            raise ValueError('Unknown transport actor')
        if type(allow_hostile_destination) is not bool:
            raise ValueError('Hostile destination permission must be boolean')
        empty = {'path': [], 'links': [], 'legs': [], 'distance_km': 0.0, 'travel_days': 0.0,
            'capacity_per_day': 0.0, 'bottlenecks': [], 'reason_trace': []}
        if origin == destination:
            return {**empty, 'path': [origin], 'physical_feasibility': {'feasible': True}}
        queue, best = [(0.0, (), origin, [])], {origin: (0.0, ())}
        while queue:
            days, link_path, node, legs = heapq.heappop(queue)
            if best.get(node) != (days, link_path):
                continue
            if node == destination:
                path = [origin] + [leg['to'] for leg in legs]
                capacities = [leg['capacity_per_day'] for leg in legs]
                minimum = min(capacities)
                return {'physical_feasibility': {'feasible': True}, 'path': path,
                    'links': list(link_path), 'legs': legs, 'distance_km': math.fsum(l['distance_km'] for l in legs),
                    'travel_days': days, 'capacity_per_day': minimum,
                    'bottlenecks': [{'link': l['link'], 'capacity': l['capacity_per_day']} for l in legs if l['capacity_per_day'] == minimum],
                    'reason_trace': [r for l in legs for r in l['reason_trace']]}
            for target, link_id in self.adjacency.get(node, []):
                link = state(world)['links'][link_id]
                if mode == 'land' and link['mode'] == 'sea' or mode not in ('land', 'any') and link['mode'] != mode:
                    continue
                if not _access(world, target, actor) and not (target == destination and allow_hostile_destination):
                    continue
                q = edge_capacity(world, link_id, actor)
                if q['capacity_per_day'] <= EPSILON:
                    continue
                factor = max(EPSILON, link['condition'] * (1 - link['disruption']) * link['seasonal_factor'])
                base_duration = max(1.0, link['travel_days'])
                if formation and link['mode'] not in ('rail', 'sea', 'river'):
                    physical_speed = transport_quote(world, formation)['speed_limit_km_day']
                    base_duration = max(base_duration, link['distance_km'] / max(EPSILON, physical_speed))
                duration = base_duration / factor
                candidate = (days + duration, link_path + (link_id,))
                if target not in best or candidate < best[target]:
                    nominal = link['distance_km'] / duration if link['distance_km'] else 0.0
                    leg = {'link': link_id, 'from': node, 'to': target, 'origin': node, 'destination': target,
                        'distance_km': link['distance_km'], 'mode': link['mode'],
                        'capacity_per_day': q['capacity_per_day'], 'travel_days': duration,
                        'nominal_speed': nominal, 'reason_trace': q['reason_trace']}
                    best[target] = candidate
                    heapq.heappush(queue, (*candidate, target, legs + [leg]))
        return {**empty, 'physical_feasibility': {'feasible': False,
            'reasons': ['No accessible connected route with real transport/port capacity']},
            'reason_trace': [{'factor': 'disconnected_or_denied_route', 'origin': origin, 'destination': destination}]}


def route_quote(world, origin, destination, mode='land', actor=None, *, formation=None,
                purpose='supply', allow_hostile_destination=False):
    return RoutePlanner(world, actor).quote(origin, destination, mode, formation, purpose, allow_hostile_destination)


def _vehicle_capacity(formation):
    result = 0.0
    for category, default_seats in (('transport', 20.), ('armored_vehicles', 5.)):
        item = formation['equipment_state'].get(category, {})
        seats = item.get('troop_capacity', default_seats)
        if not _number(seats):
            raise ValueError('Actual vehicle passenger capacity must be finite')
        result += item.get('quantity', 0) * item.get('condition', 1) * seats
    return result


def transport_quote(world, formation):
    if isinstance(formation, str):
        formation = state(world)['formations'][formation]
    mobility, manpower = formation['mobility'], formation['manpower']
    requested, capacity = mobility['speed_km_day'], math.inf
    reasons = []
    if mobility.get('motorized', False):
        capacity = _vehicle_capacity(formation)
        fuel_need = min(manpower, capacity) * .00005
        fuel_available = formation['supply_state']['stocks'].get('fuel', 0)
        usable = capacity + EPSILON >= manpower and (fuel_need <= EPSILON or fuel_available + EPSILON >= fuel_need)
        requested = requested if usable else min(20., requested)
        reasons.append({'factor': 'actual_motor_transport', 'capacity': capacity, 'required': manpower,
            'fuel_required': fuel_need, 'fuel_available': fuel_available,
            'full_formation_motorized': usable, 'fallback': None if usable else 'remaining contingent walks'})
    elif mobility.get('mode') in ('horse', 'mounted', 'cavalry'):
        horses = formation['equipment_state'].get('horses', {})
        capacity = horses.get('quantity', 0) * horses.get('condition', 1) * horses.get('troop_capacity', 1)
        usable = capacity + EPSILON >= manpower
        requested = requested if usable else min(20., requested)
        reasons.append({'factor': 'actual_mounts', 'capacity': capacity, 'required': manpower,
            'fully_mounted': usable})
    return {'speed_limit_km_day': requested, 'physical_seats': None if math.isinf(capacity) else capacity,
        'manpower': manpower, 'reason_trace': reasons}


def movement_rate(world, formation, leg):
    if isinstance(formation, str):
        formation = state(world)['formations'][formation]
    supply = min(1.0, max(0.0, formation['supply_state'].get('last_ratio', 1)))
    fatigue, readiness = formation.get('fatigue', 0), formation.get('readiness', 1)
    rate = (.25 + .75 * supply) * (.5 + .5 * readiness) * max(.1, 1 - .6 * fatigue)
    physical_transport = transport_quote(world, formation)
    q = edge_capacity(world, leg['link'], formation['actor'])
    link = state(world)['links'][leg['link']]
    current = link['condition'] * (1 - link['disruption']) * link['seasonal_factor']
    base = max(1.0, link['travel_days'])
    nominal = link['distance_km'] / base
    if link['mode'] not in ('rail', 'sea', 'river'):
        nominal = min(nominal, physical_transport['speed_limit_km_day'])
    if q['capacity_per_day'] <= EPSILON:
        rate = 0.0
    speed = nominal * current * rate
    return {'speed_km_day': speed,
        'progress_fraction_per_day': speed / link['distance_km'] if link['distance_km'] else current * rate / base,
        'ratio': rate, 'reasons': [{'factor': 'supply', 'value': supply},
            {'factor': 'fatigue', 'value': fatigue}, {'factor': 'readiness', 'value': readiness},
            {'factor': 'route_condition', 'value': current}, *physical_transport['reason_trace']]}


def add_depot(world, depot_id, actor, region, *, capacity=1e9, stocks=None):
    s = state(world)
    stocks = copy.deepcopy(stocks or {})
    if not _identity(depot_id) or depot_id in s['depots'] or actor not in s['actors'] or region not in world.regions or not _access(world, region, actor):
        raise ValueError('Depot requires unique ID, actor and accessible region')
    if not _number(capacity) or any(not _identity(k) or not _number(v) for k, v in stocks.items()) or math.fsum(stocks.values()) > capacity:
        raise ValueError('Invalid depot stock/capacity')
    source = s['actors'][actor]['stocks']
    if any(source.get(k, 0) + EPSILON < v for k, v in stocks.items()):
        raise ValueError('Depot initial stock exceeds finite actor inventory')
    for key, value in stocks.items():
        source[key] = max(0.0, source.get(key, 0) - value)
    depot = {'id': depot_id, 'actor': actor, 'region': region, 'capacity': float(capacity),
        'stocks': stocks, 'created_at': world.date, 'active': True}
    s['depots'][depot_id] = depot
    _receipts(world)['depot:' + depot_id] = {'kind': 'depot_transfer', 'date': world.date,
        'actor': actor, 'depot': depot_id, 'quantity': copy.deepcopy(stocks)}
    emit(world, 'military_depot_created', depot=depot_id, actor=actor, region=region, transferred=stocks)
    return copy.deepcopy(depot)


def procure_depot(world, depot_id, commodity, quantity, *, allow_emergency=False,
                  instruments=None, receipt_id=None):
    s, receipts = state(world), _receipts(world)
    depot = s['depots'][depot_id]
    base, separator, family = commodity.partition(':') if isinstance(commodity, str) else ('', '', '')
    if not _identity(receipt_id) or base not in SUPPLY_RECIPES or not _number(quantity, EPSILON):
        raise ValueError('Procurement requires known supply, positive quantity and receipt ID')
    if separator:
        field = 'ammunition_family' if base == 'ammunition' else 'parts_family'
        if base not in ('ammunition', 'spare_parts') or not _identity(family) or not any(
                value.get(field) == family for value in s['equipment_standards'].values()):
            raise ValueError('Family-labelled supply requires its actual registered equipment standard')
    key = 'procurement:' + receipt_id
    if key in receipts:
        old = receipts[key]
        if old['depot'] != depot_id or old['commodity'] != commodity or old['quantity'] != quantity:
            raise ValueError('Procurement receipt reused with different request')
        return copy.deepcopy(old)
    if math.fsum(depot['stocks'].values()) + quantity > depot['capacity'] + EPSILON:
        raise ValueError('Depot lacks actual storage capacity')
    from .military_bridge import procure
    purchase = procure(world, depot['actor'], depot['region'], commodity, quantity,
        allow_emergency=allow_emergency, instruments=instruments, receipt_id=receipt_id)
    if purchase['status'] not in ('funded', 'delivered') or purchase['quantity'] != quantity:
        raise ValueError('Goods must be actually delivered before depot stock is credited')
    depot['stocks'][commodity] = depot['stocks'].get(commodity, 0.0) + quantity
    result = {'id': receipt_id, 'kind': 'procurement_to_depot', 'date': world.date,
        'depot': depot_id, 'commodity': commodity, 'quantity': quantity, 'purchase': copy.deepcopy(purchase)}
    receipts[key] = result
    emit(world, 'military_supplies_procured', receipt=result)
    return copy.deepcopy(result)


def _convoy_capacity(world, convoy):
    s = state(world)
    total = 0.0
    for key in convoy['formations']:
        f = s['formations'][key]
        ships = f['equipment_state'].get('ships', {})
        crew_per_ship = ships.get('crew_per_ship', 1)
        if not _number(crew_per_ship, EPSILON):
            raise ValueError('Ships require a finite positive minimum crew')
        hold = ships.get('cargo_capacity', 100)
        if not _number(hold):
            raise ValueError('Ships require finite nonnegative cargo capacity')
        crew_ratio = min(1., f['manpower'] / max(EPSILON, ships.get('quantity', 0) * crew_per_ship))
        total += ships.get('quantity', 0) * ships.get('condition', 1) * hold * f['readiness'] * f['supply_state'].get('last_ratio', 1) * crew_ratio
    return min(convoy['capacity'], total)


def _convoy_hold_capacity(world, convoy):
    """Today's remaining hull space, independent of operational sailing speed."""
    total = 0.
    for key in convoy['formations']:
        ships = state(world)['formations'][key]['equipment_state'].get('ships', {})
        hold = ships.get('cargo_capacity', 100)
        if not _number(hold):
            raise ValueError('Ships require finite nonnegative cargo capacity')
        total += ships.get('quantity', 0) * ships.get('condition', 1) * hold
    return min(convoy['capacity'], total)


def _convoy_departure_ready(world, convoy, origin):
    """One physical assignment for each actual transport ship and escort."""
    s = state(world)
    active = {'planned', 'issued', 'received', 'in_progress', 'paused', 'disrupted'}
    for key in convoy['formations'] + convoy.get('escorts', []):
        f = s['formations'][key]
        ships = f['equipment_state'].get('ships', {})
        if f['location'] != origin or not is_stationary(world, f) or f['manpower'] <= 0 or ships.get('quantity', 0) <= 0:
            return False
        operation = s['operations'].get(f.get('current_order'), {})
        if operation.get('status') in active:
            same_escort = (key in convoy.get('escorts', []) and operation.get('kind') == 'escort'
                and operation.get('params', {}).get('convoy') == convoy['id']
                and operation.get('status') == 'in_progress')
            if not same_escort:
                return False
    return True


def add_convoy(world, convoy_id, actor, formations, *, capacity=None, escorts=None):
    s = state(world)
    formations, escorts = list(formations), list(escorts or [])
    if not _identity(convoy_id) or convoy_id in s['convoys'] or actor not in s['actors'] or not formations or len(set(formations)) != len(formations):
        raise ValueError('Convoy requires unique ID, actor and actual naval formations')
    if len(set(formations + escorts)) != len(formations + escorts) or any(key not in s['formations'] or s['formations'][key]['actor'] != actor or s['formations'][key]['mobility']['mode'] != 'sea' for key in formations + escorts):
        raise ValueError('Convoy/escort must use owned actual naval formations')
    if any(set(formations + escorts) & set(c['formations'] + c.get('escorts', [])) for c in s['convoys'].values() if c.get('active', True)):
        raise ValueError('A fleet cannot provide duplicate convoy capacity')
    ships = [s['formations'][key]['equipment_state'].get('ships', {}) for key in formations]
    if any(not _number(item.get('cargo_capacity', 100)) for item in ships):
        raise ValueError('Ships require finite nonnegative cargo capacity')
    maximum = sum(item.get('quantity', 0) * item.get('condition', 1) * item.get('cargo_capacity', 100) for item in ships)
    capacity = maximum if capacity is None else capacity
    if not _number(capacity, EPSILON) or capacity > maximum + EPSILON:
        raise ValueError('Convoy capacity exceeds actual ship equipment')
    convoy = {'id': convoy_id, 'actor': actor, 'formations': formations, 'escorts': escorts,
        'capacity': float(capacity), 'active': True, 'created_at': world.date}
    s['convoys'][convoy_id] = convoy
    return copy.deepcopy(convoy)


def dispatch(world, shipment_id, origin_depot, destination, commodities, *,
             destination_kind='depot', mode='land', convoy=None):
    s = state(world)
    if not _identity(shipment_id) or shipment_id in s['shipments'] or destination_kind not in ('depot', 'formation'):
        raise ValueError('Unique shipment ID and valid destination kind required')
    origin = s['depots'][origin_depot]
    target = s['depots'][destination] if destination_kind == 'depot' else s['formations'][destination]
    location = target['region'] if destination_kind == 'depot' else target['location']
    if target['actor'] != origin['actor'] or origin['region'] == location or not origin.get('active', True) or not _access(world, origin['region'], origin['actor']):
        raise ValueError('Shipment requires separate owned accessible target')
    if not isinstance(commodities, dict) or not commodities or any(not _identity(k) or not _number(v, EPSILON) for k, v in commodities.items()):
        raise ValueError('Positive finite cargo required')
    if any(origin['stocks'].get(k, 0) + EPSILON < v for k, v in commodities.items()):
        raise ValueError('Shipment cannot exceed actual depot supplies')
    field_camp = destination_kind == 'formation' and target['manpower'] > 0 and is_stationary(world, target)
    quote = route_quote(world, origin['region'], location, mode, origin['actor'],
        allow_hostile_destination=field_camp)
    if not quote['physical_feasibility']['feasible']:
        raise ValueError('Shipment lacks a connected transport route')
    sea_legs = [leg for leg in quote['legs'] if leg['mode'] == 'sea']
    if convoy is not None and not sea_legs:
        raise ValueError('A naval convoy cannot travel on inland transport links')
    if sea_legs:
        if len(sea_legs) != len(quote['legs']):
            raise ValueError('Multimodal cargo must transfer at actual intermediate depots')
        if convoy not in s['convoys'] or s['convoys'][convoy]['actor'] != origin['actor'] or _convoy_capacity(world, s['convoys'][convoy]) <= EPSILON:
            raise ValueError('Sea cargo requires actual finite ready convoy ships')
        if not _convoy_departure_ready(world, s['convoys'][convoy], origin['region']):
            raise ValueError('Convoy ships/escorts must be co-located, crewed and free of other missions')
        if any(item.get('convoy') == convoy and item['status'] not in ('delivered', 'cancelled', 'lost') for item in s['shipments'].values()):
            raise ValueError('Convoy is already carrying another shipment')
        if math.fsum(commodities.values()) > _convoy_hold_capacity(world, s['convoys'][convoy]) + EPSILON:
            raise ValueError('Cargo cannot exceed physical ship hold capacity')
    for key, value in commodities.items():
        origin['stocks'][key] = max(0.0, origin['stocks'].get(key, 0) - value)
    shipment = {'id': shipment_id, 'actor': origin['actor'], 'origin_depot': origin_depot,
        'origin': origin['region'], 'destination': destination, 'destination_kind': destination_kind,
        'target_region': location, 'cargo': copy.deepcopy(commodities), 'opening_cargo': copy.deepcopy(commodities),
        'delivered': {}, 'losses': {}, 'status': 'in_transit', 'created_at': world.date,
        'departed_at': world.date, 'leg_departure': world.date, 'arrived_at': None, 'route': quote, 'leg_index': 0,
        'leg_progress': 0.0, 'location': origin['region'], 'convoy': convoy,
        'last_tick': world.date, 'reason_trace': []}
    s['shipments'][shipment_id] = shipment
    if convoy is not None:
        s['convoys'][convoy]['shipment'] = shipment_id
    _receipts(world)['dispatch:' + shipment_id] = {'kind': 'dispatch', 'date': world.date,
        'shipment': shipment_id, 'source': origin_depot, 'quantity': copy.deepcopy(commodities)}
    emit(world, 'military_supply_departed', shipment=shipment_id, cargo=commodities, route=quote)
    return copy.deepcopy(shipment)


def _family_weights(world, formation, commodity):
    field = 'ammunition_family' if commodity == 'ammunition' else 'parts_family'
    result = {}
    for category, item in sorted(formation['equipment_state'].items()):
        if item.get('quantity', 0) <= 0 or item.get('standard_id') is None:
            continue
        standard = state(world)['equipment_standards'].get(item['standard_id'])
        if standard is None or standard['equipment_type'] != item.get('equipment_type', category):
            raise ValueError('Equipment must reference its actual matching standard')
        family = standard.get(field)
        if family is not None:
            result[family] = result.get(family, 0.0) + item['quantity']
    return result


def _expand_requests(world, formation, quantities):
    """A generic request may divide demand, but never converts inventory."""
    result = {}
    for commodity, amount in quantities.items():
        families = _family_weights(world, formation, commodity) if commodity in ('ammunition', 'spare_parts') else {}
        if families:
            total = math.fsum(families.values())
            for family, weight in sorted(families.items()):
                key = commodity + ':' + family
                result[key] = result.get(key, 0.0) + amount * weight / total
        else:
            result[commodity] = result.get(commodity, 0.0) + amount
    return result


def requirements(world, formation, days=1):
    if isinstance(formation, str):
        formation = state(world)['formations'][formation]
    if not _number(days, EPSILON):
        raise ValueError('Positive supply interval required')
    equipment = formation['equipment_state']
    horses = equipment.get('horses', {}).get('quantity', 0)
    motorized = formation['mobility'].get('motorized', False)
    default = {'food': formation['manpower'] * FOOD_PER_PERSON_DAY,
        'fodder': horses * FOOD_PER_PERSON_DAY, 'ammunition': 0.0,
        'fuel': min(formation['manpower'], _vehicle_capacity(formation)) * .00005 if motorized else 0.0,
        'medical': formation['manpower'] * .000005,
        'spare_parts': min(formation['manpower'], _vehicle_capacity(formation)) * .00001 if motorized else 0.0}
    configured = formation['supply_state'].get('requirements', {})
    if any(not _identity(k) or not _number(v) for k, v in configured.items()):
        raise ValueError('Invalid configured daily supplies')
    return _expand_requests(world, formation,
        {key: value * days for key, value in {**default, **configured}.items() if value > 0})


def consume_supply(world, formation_id, quantities=None, *, days=1, receipt_id):
    s, receipts = state(world), _receipts(world)
    f = s['formations'][formation_id]
    quantities = requirements(world, f, days) if quantities is None else copy.deepcopy(quantities)
    if not _identity(receipt_id) or not isinstance(quantities, dict) or not _number(days, EPSILON) or any(not _identity(k) or not _number(v) for k, v in quantities.items()):
        raise ValueError('Finite supply consumption and unique receipt required')
    quantities = _expand_requests(world, f, quantities)
    key = 'consumption:' + receipt_id
    if key in receipts:
        old = receipts[key]
        if old['formation'] != formation_id or old['requested'] != quantities or old['days'] != days:
            raise ValueError('Supply receipt reused with different request')
        return copy.deepcopy(old)
    stores = f['supply_state']['stocks']
    if any(not _number(stores.get(k, 0)) for k in quantities):
        raise ValueError('Actual supply inventory must be finite and nonnegative')
    actual = {}
    for commodity, amount in quantities.items():
        base, separator, family = commodity.partition(':')
        compatible = not separator or base not in ('ammunition', 'spare_parts') or family in _family_weights(world, f, base)
        actual[commodity] = min(stores.get(commodity, 0), amount) if compatible else 0.0
    for commodity, quantity in actual.items():
        stores[commodity] = max(0.0, stores.get(commodity, 0) - quantity)
        consumed = f['supply_state'].setdefault('consumption', {})
        consumed[commodity] = consumed.get(commodity, 0.0) + quantity
    component_ratios = {k: actual[k] / value if value else 1.0 for k, value in quantities.items()}
    critical = {'food', 'fuel', 'fodder'}
    ratio = min((value for key, value in component_ratios.items() if key in critical), default=1.0)
    f['supply_state']['component_ratios'] = component_ratios
    f['supply_state']['last_ratio'] = ratio
    result = {'id': receipt_id, 'kind': 'supply_consumption', 'date': world.date,
        'formation': formation_id, 'requested': quantities, 'actual': actual,
        'shortages': {k: quantities[k] - actual[k] for k in quantities if quantities[k] > actual[k] + EPSILON},
        'ratio': ratio, 'component_ratios': component_ratios, 'days': days}
    receipts[key] = result
    if actual.get('food', 0):
        from .military_bridge import record_food_consumption
        record_food_consumption(world, formation_id, actual['food'], days=days)
    emit(world, 'military_supply_consumed', receipt=result)
    return copy.deepcopy(result)


def transfer_to_formation(world, depot_id, formation_id, commodities, *, receipt_id):
    """Same-place loading only; remote stock requires an actual shipment."""
    s, receipts = state(world), _receipts(world)
    depot, f = s['depots'][depot_id], s['formations'][formation_id]
    if not _identity(receipt_id):
        raise ValueError('Loading requires a stable receipt ID')
    key = 'local_loading:' + receipt_id
    if key in receipts:
        old = receipts[key]
        if old['depot'] != depot_id or old['formation'] != formation_id or old['quantity'] != commodities:
            raise ValueError('Loading receipt reused with different request')
        return copy.deepcopy(old)
    if depot['actor'] != f['actor'] or depot['region'] != f['location'] or not is_stationary(world, f) or not _access(world, depot['region'], depot['actor']):
        raise ValueError('Depot cannot instantly load a remote/transiting formation')
    if not isinstance(commodities, dict) or any(not _identity(k) or not _number(v) or depot['stocks'].get(k, 0) + EPSILON < v for k, v in commodities.items()):
        raise ValueError('Loading exceeds actual finite depot supplies')
    for commodity, quantity in commodities.items():
        depot['stocks'][commodity] = max(0.0, depot['stocks'].get(commodity, 0) - quantity)
        f['supply_state']['stocks'][commodity] = f['supply_state']['stocks'].get(commodity, 0) + quantity
    result = {'kind': 'local_loading', 'date': world.date, 'depot': depot_id,
        'formation': formation_id, 'quantity': copy.deepcopy(commodities)}
    receipts[key] = result
    return copy.deepcopy(result)


def lose_cargo(world, shipment_id, quantities, *, cause, receipt_id):
    """Apply externally adjudicated loss once; no blockade lottery or free loss."""
    s, receipts = state(world), _receipts(world)
    if not _identity(receipt_id) or not _identity(cause) or not isinstance(quantities, dict) or not quantities or any(
            not _identity(k) or not _number(v) for k, v in quantities.items()):
        raise ValueError('Cargo loss requires finite quantities, cause and stable receipt')
    key, shipment = 'cargo_loss:' + receipt_id, s['shipments'][shipment_id]
    if key in receipts:
        old = receipts[key]
        if old['shipment'] != shipment_id or old['quantity'] != quantities or old['cause'] != cause:
            raise ValueError('Cargo-loss receipt reused with different consequence')
        return copy.deepcopy(old)
    if shipment['status'] in ('delivered', 'cancelled', 'lost') or any(
            quantity > shipment['cargo'].get(commodity, 0) + EPSILON for commodity, quantity in quantities.items()):
        raise ValueError('Loss cannot exceed undelivered physical cargo')
    for commodity, quantity in quantities.items():
        shipment['cargo'][commodity] = max(0.0, shipment['cargo'].get(commodity, 0) - quantity)
        shipment['losses'][commodity] = shipment['losses'].get(commodity, 0) + quantity
    if math.fsum(shipment['cargo'].values()) <= EPSILON:
        shipment['status'] = 'lost'
        if shipment.get('convoy') is not None:
            s['convoys'][shipment['convoy']]['shipment'] = None
    receipt = {'kind': 'cargo_loss', 'date': world.date, 'shipment': shipment_id,
        'quantity': copy.deepcopy(quantities), 'cause': cause}
    receipts[key] = receipt
    emit(world, 'military_cargo_lost', receipt=receipt)
    return copy.deepcopy(receipt)


def _arrival(world, shipment):
    s, receipts = state(world), _receipts(world)
    target = s['depots'][shipment['destination']] if shipment['destination_kind'] == 'depot' else s['formations'][shipment['destination']]
    location = target['region'] if shipment['destination_kind'] == 'depot' else target['location']
    if location != shipment['target_region'] or target['actor'] != shipment['actor'] or shipment['destination_kind'] == 'formation' and not is_stationary(world, target):
        shipment['status'] = 'awaiting_target'
        return
    field_camp = shipment['destination_kind'] == 'formation' and target['manpower'] > 0
    if not _access(world, location, shipment['actor']) and not field_camp:
        shipment['status'] = 'disrupted'
        return
    inventory = target['stocks'] if shipment['destination_kind'] == 'depot' else target['supply_state']['stocks']
    free = max(0.0, target['capacity'] - math.fsum(inventory.values())) if shipment['destination_kind'] == 'depot' else math.fsum(shipment['cargo'].values())
    total = math.fsum(shipment['cargo'].values())
    ratio = min(1.0, free / total) if total else 1.0
    for commodity in sorted(shipment['cargo']):
        quantity = shipment['cargo'][commodity] * ratio
        inventory[commodity] = inventory.get(commodity, 0) + quantity
        shipment['cargo'][commodity] -= quantity
        shipment['delivered'][commodity] = shipment['delivered'].get(commodity, 0) + quantity
    if math.fsum(shipment['cargo'].values()) > EPSILON:
        shipment['status'] = 'awaiting_storage'
        return
    shipment['status'], shipment['arrived_at'] = 'delivered', world.date
    if shipment['convoy'] is not None:
        convoy = s['convoys'][shipment['convoy']]
        for key in convoy['formations'] + convoy['escorts']:
            s['formations'][key]['location'] = location
        convoy['shipment'] = None
    receipts['arrival:' + shipment['id']] = {'kind': 'shipment_arrival', 'date': world.date,
        'shipment': shipment['id'], 'destination': shipment['destination'],
        'quantity': copy.deepcopy(shipment['delivered'])}
    emit(world, 'military_supply_arrived', shipment=shipment['id'], delivered=shipment['delivered'])


def tick(world, tick_event=None, rng=None):
    """Only active cargo, deterministic ordering, at most one leg per day."""
    s = initialize(world)
    if s['last_logistics_tick'] == world.date:
        return []
    outbox_start = len(s['outbox'])
    for key, shipment in active_shipments(s['shipments']):
        if shipment['status'] in ('delivered', 'cancelled', 'lost') or shipment['last_tick'] >= world.date:
            continue
        shipment['last_tick'] = world.date
        if shipment['leg_index'] >= len(shipment['route']['legs']):
            _arrival(world, shipment)
            continue
        leg = shipment['route']['legs'][shipment['leg_index']]
        f = s['formations'].get(shipment['destination']) if shipment['destination_kind'] == 'formation' else None
        field_camp = (f is not None and f['actor'] == shipment['actor'] and f['manpower'] > 0
            and f['location'] == shipment['target_region'] and is_stationary(world, f)
            and shipment['leg_index'] == len(shipment['route']['legs']) - 1)
        if not _access(world, leg['to'], shipment['actor']) and not field_camp:
            shipment['status'] = 'disrupted'
            continue
        quote = edge_capacity(world, leg['link'], shipment['actor'])
        convoy_capacity = _convoy_capacity(world, s['convoys'][shipment['convoy']]) if shipment['convoy'] else math.inf
        total = math.fsum(shipment['cargo'].values())
        request = min(total, quote['remaining'], convoy_capacity)
        reservation = reserve_capacity(world, leg['link'], request, actor=shipment['actor'],
            reservation_id='cargo:' + key + ':' + world.date)
        capacity_ratio = min(1.0, reservation['reserved'] / total) if total else 1.0
        link = s['links'][leg['link']]
        rate = link['condition'] * (1 - link['disruption']) * link['seasonal_factor'] / max(1.0, link['travel_days'])
        shipment['leg_progress'] = min(1.0, shipment['leg_progress'] + rate * capacity_ratio)
        shipment['reason_trace'].append({'date': world.date, 'link': leg['link'],
            'capacity_ratio': capacity_ratio, 'route_rate': rate, 'progress': shipment['leg_progress']})
        shipment['status'] = 'in_transit' if capacity_ratio > 0 and rate > 0 else 'disrupted'
        if shipment['leg_progress'] >= 1.0 - EPSILON:
            shipment['location'] = leg['to']
            if shipment['convoy'] is not None:
                convoy = s['convoys'][shipment['convoy']]
                for fleet in convoy['formations'] + convoy['escorts']:
                    formation = s['formations'][fleet]
                    departure = shipment.get('leg_departure', shipment['departed_at'])
                    receipt_id = f"convoy-movement:{shipment['id']}:{fleet}:{shipment['leg_index']}"
                    s['movement_receipts'][receipt_id] = {'id': receipt_id,
                        'shipment': shipment['id'], 'convoy': convoy['id'], 'formation': fleet,
                        'date': world.date, 'origin': formation['location'], 'destination': leg['to'],
                        'departure': departure, 'distance_km': leg['distance_km'],
                        'link': leg['link'], 'mode': leg['mode'],
                        'elapsed_days': (canonical_date(world.date) - canonical_date(departure)).days,
                        'phase': 'cargo_voyage'}
                    s['formations'][fleet]['location'] = leg['to']
            shipment['leg_index'] += 1
            shipment['leg_progress'] = 0.0
            shipment['leg_departure'] = world.date
            if shipment['leg_index'] >= len(shipment['route']['legs']):
                _arrival(world, shipment)
    s['last_logistics_tick'] = world.date
    return copy.deepcopy(s['outbox'][outbox_start:])


def explain(world, subject):
    s = state(world)
    if subject in s['shipments']:
        item = s['shipments'][subject]
        return {'subject': subject, 'kind': 'shipment', 'status': item['status'],
            'location': item['location'], 'cargo': copy.deepcopy(item['cargo']),
            'delivered': copy.deepcopy(item['delivered']), 'route': copy.deepcopy(item['route']),
            'reason_trace': copy.deepcopy(item['reason_trace'])}
    if subject in s['formations']:
        f = s['formations'][subject]
        from .military_innovation import equipment_compatibility
        return {'subject': subject, 'kind': 'formation', 'requirements': requirements(world, f),
            'actual_stocks': copy.deepcopy(f['supply_state']['stocks']),
            'supply_ratio': f['supply_state']['last_ratio'],
            'equipment_compatibility': equipment_compatibility(world, subject),
            'receipts': [copy.deepcopy(v) for v in s.get('logistics_receipts', {}).values() if v.get('formation') == subject]}
    if subject in s['links']:
        return edge_capacity(world, subject)
    raise ValueError('Unknown logistics subject')


def validate(world, previous=None):
    if getattr(world, 'military_state', None) is None:
        return []
    errors = []
    try:
        s = state(world)
        for key, link in audit_items(s['links']):
            if link['id'] != key or link['origin'] not in world.regions or link['destination'] not in world.regions or link['mode'] not in MODES:
                errors.append('military logistics: nonexistent link endpoints/mode ' + key)
            if any(not _number(link[name]) for name in ('throughput', 'distance_km')) or not _number(link['travel_days'], 1) or any(not _number(link[name], 0, 1) for name in ('condition', 'disruption', 'seasonal_factor')):
                errors.append('military logistics: invalid transport capacity/duration ' + key)
        for key, depot in audit_items(s['depots']):
            if depot['id'] != key or depot['actor'] not in s['actors'] or depot['region'] not in world.regions or not _number(depot['capacity']):
                errors.append('military logistics: invalid depot ' + key)
            if any(not _number(v) for v in depot['stocks'].values()) or math.fsum(depot['stocks'].values()) > depot['capacity'] + EPSILON:
                errors.append('military logistics: depot supply/capacity contradiction ' + key)
        for key, f in audit_items(s['formations']):
            ships = f.get('equipment_state', {}).get('ships', {})
            if 'cargo_capacity' in ships and not _number(ships['cargo_capacity']):
                errors.append('military logistics: invalid explicit ship cargo capacity ' + key)
        assigned = set()
        for key, convoy in audit_items(s['convoys']):
            if convoy['id'] != key or convoy['actor'] not in s['actors'] or not _number(convoy['capacity'], EPSILON) or any(f not in s['formations'] for f in convoy['formations'] + convoy['escorts']):
                errors.append('military logistics: nonexistent convoy ships ' + key)
            if convoy.get('active', True):
                if assigned.intersection(convoy['formations'] + convoy['escorts']):
                    errors.append('military logistics: duplicate convoy ships ' + key)
                assigned.update(convoy['formations'] + convoy['escorts'])
        for day, links in audit_items(s['transport_reservations']):
            canonical_date(day)
            if day > world.date:
                errors.append('military logistics: future capacity reservation')
            for key, reservation in audit_items(links):
                if key not in s['links'] or not _number(reservation['used']) or not math.isclose(reservation['used'], math.fsum(r['reserved'] for r in reservation['reservations'].values()), abs_tol=EPSILON):
                    errors.append('military logistics: invalid transport reservation total')
                for identity, booking in audit_items(reservation['reservations']):
                    if booking['id'] != identity or booking['date'] != day or booking['link'] != key or booking['actor'] not in s['actors'] or any(
                            not _number(booking[n]) for n in ('requested', 'reserved', 'capacity_at_booking')) or booking['reserved'] > booking['requested'] + EPSILON:
                        errors.append('military logistics: impossible capacity reservation ' + identity)
                maximum = max((v['capacity_at_booking'] for v in reservation['reservations'].values()), default=0)
                if reservation['used'] > maximum + EPSILON:
                    errors.append('military logistics: transport overbooked beyond actual capacity')
        for key, shipment in audit_items(s['shipments']):
            if shipment['id'] != key or shipment['actor'] not in s['actors'] or shipment['origin_depot'] not in s['depots'] or shipment['location'] not in world.regions:
                errors.append('military logistics: invalid shipment identity ' + key)
            canonical_date(shipment['created_at'])
            canonical_date(shipment['departed_at'])
            canonical_date(shipment['last_tick'])
            if shipment['status'] not in ('in_transit', 'disrupted', 'awaiting_target', 'awaiting_storage', 'delivered', 'cancelled', 'lost') or shipment['last_tick'] > world.date or shipment['departed_at'] < shipment['created_at'] or not _number(shipment['leg_progress'], 0, 1):
                errors.append('military logistics: invalid cargo lifecycle/progress ' + key)
            if shipment['arrived_at'] is not None and canonical_date(shipment['arrived_at']) <= canonical_date(shipment['departed_at']):
                errors.append('military logistics: instantaneous/backward cargo arrival ' + key)
            for name in set(shipment['opening_cargo']) | set(shipment['cargo']) | set(shipment['delivered']) | set(shipment['losses']):
                values = [shipment[bucket].get(name, 0) for bucket in ('opening_cargo', 'cargo', 'delivered', 'losses')]
                if any(not _number(value) for value in values) or not math.isclose(values[0], math.fsum(values[1:]), rel_tol=1e-9, abs_tol=EPSILON):
                    errors.append('military logistics: cargo conservation failure ' + key)
            if shipment['status'] == 'delivered' and (shipment['arrived_at'] is None or math.fsum(shipment['cargo'].values()) > EPSILON):
                errors.append('military logistics: false shipment completion ' + key)
            legs, previous_node = shipment['route']['legs'], shipment['origin']
            for leg in legs:
                if leg['link'] not in s['links'] or leg['from'] != previous_node or leg['to'] not in world.regions:
                    errors.append('military logistics: nonexistent/disconnected cargo route ' + key)
                else:
                    link = s['links'][leg['link']]
                    if (leg['from'], leg['to']) != (link['origin'], link['destination']) and not (
                            link.get('bidirectional', True) and (leg['from'], leg['to']) == (link['destination'], link['origin'])):
                        errors.append('military logistics: cargo route uses unrelated physical link ' + key)
                previous_node = leg['to']
            if previous_node != shipment['target_region']:
                errors.append('military logistics: cargo destination differs from route ' + key)
            index = shipment['leg_index']
            if type(index) is not int or not 0 <= index <= len(legs) or shipment['location'] != (shipment['origin'] if index == 0 else legs[index - 1]['to']):
                errors.append('military logistics: cargo location/progress contradiction ' + key)
        for key, receipt in s.get('logistics_receipts', {}).items():
            canonical_date(receipt['date'])
            if receipt['date'] > world.date:
                errors.append('military logistics: future resource receipt ' + key)
        if previous is not None and getattr(previous, 'military_state', None):
            for key, receipt in previous.military_state.get('logistics_receipts', {}).items():
                if s.get('logistics_receipts', {}).get(key) != receipt:
                    errors.append('military logistics: changed immutable resource receipt ' + key)
    except (KeyError, TypeError, ValueError, AttributeError):
        errors.append('military logistics: malformed persisted logistics state')
    return errors
