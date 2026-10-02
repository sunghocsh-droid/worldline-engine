"""Validators receive candidate truth and previous committed truth."""
from datetime import date
from math import hypot, isfinite
from .models import RISKS, canonical_date


def numeric(value, minimum, maximum=None):
    return (type(value) in (int, float) and isfinite(value) and value >= minimum
            and (maximum is None or value <= maximum))


class AuditError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join(issues))


class Auditor:
    def __init__(self):
        self.validators = [core_validator]
        self.warnings = []

    def register(self, validator):
        self.validators.append(validator)

    def check(self, world, previous=None, *, history_previous=None):
        issues = []
        self.warnings = []
        for validator in self.validators:
            result = (validator(world, previous, history_previous=history_previous)
                      if validator is core_validator else validator(world, previous))
            for issue in result:
                if isinstance(issue, dict) and issue.get("severity") == "warning":
                    self.warnings.append(issue)
                else:
                    issues.append(issue["message"] if isinstance(issue, dict) else issue)
        if issues:
            raise AuditError(issues)
        return issues


def core_validator(w, old, *, history_previous=None):
    issues = []
    canonical_date(w.date)
    if (w.schema_version not in (2, 3, 4, 5) or (w.schema_version in (3, 4, 5)) != (w.npc_state is not None)
        or (w.schema_version in (4, 5)) != (w.crisis_state is not None)
        or (w.schema_version == 5) != (w.military_state is not None)):
        issues.append("world: unsupported schema")
    if w.crisis_state is not None:
        from .crisis_state import validate as validate_crisis
        issues.extend(validate_crisis(w, history_previous if history_previous is not None else old))
    if w.military_state is not None:
        from .military_state import validate as validate_military
        issues.extend(validate_military(w, history_previous if history_previous is not None else old))
    days = (date.fromisoformat(w.date) - date.fromisoformat(old.date)).days if old else 0
    if days < 0:
        issues.append("clock: backwards")
    for key, c in w.countries.items():
        for name, value in [("population", c.population), ("gdp", c.gdp),
                            ("treasury", c.treasury), ("debt", c.debt),
                            *c.resources.items(), *c.industries.items()]:
            if type(value) is not int or value < 0:
                issues.append(f"finance/demography: {key}.{name} must be a nonnegative integer")
        if not numeric(c.stability, 0, 100):
            issues.append(f"stability: {key}")
        if old and key in old.countries:
            cap = old.countries[key].population * (1.05 ** (max(days, 0) / 365))
            from .demography import explains_change
            if c.population > cap + 1 and not explains_change(w, old, key):
                issues.append(f"population: implausible growth in {key}")
            if c.population < old.countries[key].population * .95 and not explains_change(w, old, key):
                issues.append(f"population: unexplained collapse in {key}")
        for tech in c.technologies:
            if tech not in w.tech_requirements or not set(w.tech_requirements[tech]) <= set(c.technologies):
                issues.append(f"technology: missing definition/prerequisite {tech}")
        for name, value in c.infrastructure.items():
            if not numeric(value, 0):
                issues.append(f"infrastructure: invalid {key}:{name}")
        for resource, capacity in c.industries.items():
            if w.production.get(f"{key}:{resource}", 0) > capacity:
                issues.append(f"production: capacity exceeded {key}:{resource}")
    capacities = {f"{country}:{resource}": capacity for country, c in w.countries.items()
                  for resource, capacity in c.industries.items()}
    for key, value in w.production.items():
        if key not in capacities or type(value) is not int or not 0 <= value <= capacities[key]:
            issues.append(f"production: unknown/invalid capacity {key}")
    totals = {}
    for key, r in w.regions.items():
        for name, value in r.infrastructure.items():
            if not numeric(value, 0):
                issues.append(f"infrastructure: invalid region {key}:{name}")
        if r.owner not in w.countries or type(r.population) is not int or r.population < 0:
            issues.append(f"territory: invalid region {key}")
        if not numeric(r.x, -float("inf")) or not numeric(r.y, -float("inf")):
            issues.append(f"territory: invalid coordinates {key}")
        totals[r.owner] = totals.get(r.owner, 0) + r.population
    for owner, total in totals.items():
        if owner in w.countries and total > w.countries[owner].population:
            issues.append(f"population: regional total exceeds {owner}")
    soldiers = {}
    for key, a in w.armies.items():
        if a.owner not in w.countries or a.region not in w.regions:
            issues.append(f"army: nonexistent owner/territory {key}")
        if type(a.personnel) is not int or a.personnel < 0 or not numeric(a.speed_km_day, 0) or a.speed_km_day == 0:
            issues.append(f"army: invalid strength/speed {key}")
        soldiers[a.owner] = soldiers.get(a.owner, 0) + a.personnel
        if old and key in old.armies and a.region != old.armies[key].region:
            start = old.regions.get(old.armies[key].region)
            end = w.regions.get(a.region)
            authorized = False
            if w.military_state is not None:
                receipt = w.military_state.get('movement_receipts', {}).get(key)
                authorized = bool(receipt and receipt.get('arrival') == w.date and receipt.get('origin') == old.armies[key].region
                    and receipt.get('destination') == a.region and receipt.get('elapsed_days', 0) >= receipt.get('minimum_days', 1))
            if start and end and not authorized and hypot(end.x-start.x, end.y-start.y) > max(days, 0) * old.armies[key].speed_km_day:
                issues.append(f"army: unrealistic movement {key}")
    for owner, count in soldiers.items():
        if owner in w.countries and count > w.countries[owner].population:
            issues.append(f"army: strength exceeds population {owner}")
    for key, f in w.factions.items():
        if f.country not in w.countries:
            issues.append(f"faction: nonexistent country {key}")
    for npc in [c.npc for c in w.countries.values()] + [f.npc for f in w.factions.values()]:
        if not all(numeric(n, 0, 100) for n in (npc.risk_tolerance, npc.faction_power)):
            issues.append("npc: invalid power/risk tolerance")
    for key, relation in w.relations.items():
        if relation.get("a") not in w.countries or relation.get("b") not in w.countries:
            issues.append(f"diplomacy: nonexistent actor {key}")
    for key, p in w.pressures.items():
        if p.get("risk") not in RISKS or p.get("country") not in w.countries or not numeric(p.get("value"), 0, 100):
            issues.append(f"pressure: invalid {key}")
        if key != f"{p.get('country')}:{p.get('risk')}" or not isinstance(p.get("causes", []), list) or not all(isinstance(cause, str) for cause in p.get("causes", [])):
            issues.append(f"pressure: invalid key/causes {key}")
    if old:
        for key, value in old.canon.items():
            if key not in w.canon or w.canon[key] != value:
                issues.append(f"canon: conflict {key}")
    from .demography import validate_account
    from .projects import validate as validate_projects
    for country, account in w.population_accounts.items():
        if country not in w.countries:
            issues.append(f"population: unknown account owner {country}")
        issues.extend(validate_account(account, w.date))
    issues.extend(validate_projects(w, old))
    if w.economy is not None:
        from .economy import validate as validate_economy
        issues.extend(validate_economy(w, old))
    if w.npc_state is not None:
        if not w.economy or w.economy.get("model_revision") != 3:
            issues.append("npc: decisions require stabilized economy revision 3")
        if "adapter" not in w.npc_state:
            issues.append("npc: missing scheduler/report adapter; initialize through the NPC adapter")
        if w.economy and any(p.get("active", True) and p.get("country") not in w.economy["countries"]
                             for p in w.npc_state.get("persons", {}).values()):
            issues.append("npc: active actors require a modeled domestic economy")
        if w.economy and any(c not in w.economy["countries"] for c in w.npc_state.get("governments", {})):
            issues.append("npc: government requires a modeled economy")
        from .npc_state import validate as validate_npc
        issues.extend(validate_npc(w.npc_state, w.countries, w.regions, w.date,
                                  previous=(history_previous.npc_state if history_previous is not None else old.npc_state if old else None)))
    return issues
