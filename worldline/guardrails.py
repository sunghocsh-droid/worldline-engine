"""Read-only economic advice; objective constraints never override political intent."""
import copy
import math
from . import credit, projects
from .calibration import SECTORS


def require_stabilization(world):
    if not world.economy or world.economy.get('stabilization_version') != 1:
        raise ValueError('Enable economic stabilization before using policy feasibility interfaces')


def available_labour(world, country, region=None, sector=None):
    require_stabilization(world)
    if country not in world.economy['countries'] or sector is not None and sector not in SECTORS:
        raise ValueError('Unknown country or sector')
    regions = [r for r in sorted(world.economy['regions']) if world.regions[r].owner == country]
    if region is not None:
        if region not in regions: raise ValueError('Labour must come from an owned region')
        regions = [region]
    result = {'country': country, 'region': region, 'sector': sector, 'regions': {}, 'immediately_unemployed': 0.0,
              'employed_in_sector': 0.0, 'transit_training': 0.0,
              'note': 'Regional pools are separate; employed workers require a transfer pipeline.'}
    for key in regions:
        pool = world.economy['regions'][key]['labour']
        local = {'unemployed': pool.get('unemployed', 0.0),
                 'employed_in_sector': pool.get(sector, 0.0) if sector else pool.get('employed', 0.0),
                 'transit_training': pool.get('transit_training', 0.0),
                 'military': pool.get('military', 0.0)}
        result['regions'][key] = local
        result['immediately_unemployed'] += local['unemployed']
        result['employed_in_sector'] += local['employed_in_sector']
        result['transit_training'] += local['transit_training']
    return result


def available_skilled_labour(world, country, region=None, sector=None):
    result = available_labour(world, country, region, sector)
    result['skilled'] = result['professionals'] = 0.0
    for key in result['regions']:
        r = world.economy['regions'][key]
        result['skilled'] += r['sectors'][sector]['skilled_labour'] if sector else r['labour'].get('skilled', 0)
        result['professionals'] += r['sectors'][sector].get('professional_labour', 0) if sector else r['labour'].get('professionals', 0)
    result['note'] = 'Skilled and professional workers are subsets of allocated workers, never additional workers.'
    return result


def can_borrow(world, country, amount, *, allow_emergency=True, instruments=None, perceived=None):
    require_stabilization(world)
    if type(amount) is not int or amount < 0: raise ValueError('Borrowing request must be a nonnegative integer')
    return credit.can_borrow(world, country, amount, allow_emergency=allow_emergency,
                             instruments=instruments, perceived=perceived)


def estimated_fiscal_risk(world, country, amount=0, **kwargs):
    require_stabilization(world)
    return credit.assess(world, country, amount, **kwargs)


def project_resource_constraints(world, project):
    require_stabilization(world)
    if isinstance(project, str): project = world.projects[project]
    region = project['region']
    r = world.economy['regions'][region]
    missing = {}
    for name, needed in project['requirements']['resources'].items():
        available = r['stocks'].get(name, 0)
        if needed > available: missing[name] = {'required': needed, 'available': available}
    construction = r['labour'].get('construction', 0)
    needed_workers = project['requirements']['minimum_labour']
    other_committed = math.fsum(p['requirements']['minimum_labour'] for p in world.projects.values()
        if p['id'] != project['id'] and p['region'] == region and p['status'] not in ('planned', 'completed', 'cancelled', 'disrupted'))
    free_construction = max(0, construction - other_committed)
    # Labour shortages delay the project. They do not cancel a political decision.
    return {'feasible': not missing, 'missing_resources': missing, 'region': region,
            'construction_workers': construction, 'committed_construction_workers': other_committed,
            'available_construction_workers': free_construction, 'required_workers': needed_workers,
            'delayed_by_labour': free_construction < needed_workers,
            'reasons': ['missing regional resources'] if missing else []}


def _stock_reservation_view(world, regions):
    """Copy only inventories changed by a read-only financing reservation.

    credit.assess reads the remaining world and makes its own projected credit
    copy. Projects, political memories, trades and sector histories are not
    mutated here, so cloning them for every query is unnecessary. Each affected
    inventory, including its nested goods map, remains fully detached.
    """
    view = copy.copy(world)
    view.economy = dict(world.economy)
    view.economy['regions'] = dict(world.economy['regions'])
    for region in regions:
        original = world.economy['regions'][region]
        local = dict(original)
        local['stocks'] = copy.deepcopy(original['stocks'])
        view.economy['regions'][region] = local
    return view


def can_afford_project(world, project, *, allow_emergency=True, instruments=None, perceived=None):
    if isinstance(project, str): project = world.projects[project]
    physical = project_resource_constraints(world, project)
    reserved_world = _stock_reservation_view(world, (project['region'],))
    if physical['feasible']:
        stocks = reserved_world.economy['regions'][project['region']]['stocks']
        for resource, quantity in project['requirements']['resources'].items(): stocks[resource] -= quantity
    quote = estimated_fiscal_risk(reserved_world, project['country'], project['cost'], allow_emergency=allow_emergency,
                       instruments=instruments, perceived=perceived)
    quote['physical_feasibility'] = physical
    quote['project_id'] = project['id']
    quote['execution_feasibility'] = quote['physical_feasibility']['feasible'] and (
        quote['normal_financial_feasibility']['feasible'] or quote['emergency_financial_feasibility']['feasible'])
    return quote


def can_fund_war(world, country, amount, *, region=None, manpower=0, resources=None, perceived=None, **kwargs):
    pool = available_labour(world, country, region)
    regions = pool['regions']
    civilian = sum(world.economy['regions'][r]['labour'].get('employed', 0) +
                   world.economy['regions'][r]['labour'].get('unemployed', 0) +
                   world.economy['regions'][r]['labour'].get('transit_training', 0) for r in regions)
    eligible = sum(max(0, min(
        world.economy['regions'][r]['labour_state']['working_age_population'] *
        world.economy['regions'][r]['labour_state']['eligible_military_share'] -
        world.economy['regions'][r]['labour_state']['standing_army'] -
        world.economy['regions'][r]['labour_state']['mobilized'],
        world.economy['regions'][r]['labour']['employed'] +
        world.economy['regions'][r]['labour']['unemployed'] +
        world.economy['regions'][r]['labour']['transit_training'])) for r in regions)
    if type(manpower) not in (int, float) or not math.isfinite(manpower) or manpower < 0:
        raise ValueError('Invalid required manpower')
    shortages = {}
    reserved_world = _stock_reservation_view(world, regions)
    for name, needed in (resources or {}).items():
        if type(needed) not in (int, float) or not math.isfinite(needed) or needed < 0:
            raise ValueError('Invalid resource quantity')
        present = sum(world.economy['regions'][r]['stocks'].get(name, 0) for r in regions)
        if present < needed: shortages[name] = {'required': needed, 'available': present}
        else:
            remaining = needed
            for r in sorted(regions):
                inventory = reserved_world.economy['regions'][r]['stocks']
                take = min(remaining, inventory.get(name, 0))
                if name in inventory: inventory[name] -= take
                remaining -= take
    quote = estimated_fiscal_risk(reserved_world, country, amount, purpose='war', perceived=perceived, **kwargs)
    quote['physical_feasibility'] = {'feasible': eligible >= manpower and not shortages,
        'required_manpower': manpower, 'available_civilian_manpower': civilian,
        'eligible_additional_recruits': eligible,
        'missing_resources': shortages, 'region': region,
        'note': 'War cost is advisory; political intent is never cancelled by risk.'}
    quote['execution_feasibility'] = quote['physical_feasibility']['feasible'] and (
        quote['normal_financial_feasibility']['feasible'] or quote['emergency_financial_feasibility']['feasible'])
    return copy.deepcopy(quote)
