"""Fixed-price, monthly game economy. All state lives in World; no external RNG or timers."""
import calendar
import copy
import math
from datetime import timedelta
from .calibration import SECTORS, PROFILES, RANGES, PROFILE_RANGES, MODEL_PARAMETERS, MODEL_PARAMETER_RANGES
from .models import canonical_date
from .projects import INFRA, plan, fund
from .state_runtime import changes_since, version

VERSION = "1"
SHOCKS = ("good_harvest", "bad_harvest", "drought", "flood", "epidemic", "war_disruption",
          "blockade", "resource_discovery", "foreign_investment_boom", "capital_destruction")
EDUCATION = ("literacy", "primary", "secondary", "technical", "university")
SPENDING = ("military", "administration", "education", "infrastructure", "welfare", "other", "subsidy")
SHARES = dict(zip(SECTORS, (.45, .07, .10, .06, .04, .03, .05, .05, .05, .02, .08)))
ENERGY = dict(zip(SECTORS, (.02, .02, .12, .30, .25, .40, 0, .10, .15, .20, .01)))
RAW = dict(zip(SECTORS, (0, 0, .12, .25, .20, .30, .05, .25, 0, .05, .01)))
SKILL = dict(zip(SECTORS, (.01, .03, .06, .10, .18, .15, .10, .05, .05, .08, .04)))
PRICE = {s: .25 if s == "energy" else 1/3 if s == "mining" else 1.0 for s in SECTORS}


def ratio(x, scale=50):
    return x / (x + scale) if x > 0 else 0.0


def parameters(world):
    return {**MODEL_PARAMETERS, **(world.economy.get("model_parameters", {}) if world.economy.get("model_revision", 1) >= 2 else {})}


def initialize(world, profile="early_industrial", region_overrides=None, calibration=None):
    if world.economy is not None:
        raise ValueError("Economy is already initialized")
    if profile not in PROFILES:
        raise ValueError("Unknown calibration profile")
    settings = dict(PROFILES[profile])
    for key, value in (calibration or {}).items():
        if key not in RANGES or type(value) not in (int, float) or not math.isfinite(value) or not PROFILE_RANGES[profile][key][0] <= value <= PROFILE_RANGES[profile][key][1]:
            raise ValueError("Calibration parameter outside supported range")
        settings[key] = value
    countries = {}
    regions = {}
    owners = {r.owner for r in world.regions.values()}
    if not owners:
        raise ValueError("Economy requires populated scenario regions")
    for owner in sorted(owners):
        population = sum(r.population for r in world.regions.values() if r.owner == owner)
        if population != world.countries[owner].population:
            raise ValueError("National population must equal the sum of modeled regions")
        c = world.countries[owner]
        countries[owner] = {"profile": profile, "calibration": dict(settings), "calibration_ranges": copy.deepcopy(PROFILE_RANGES[profile]),
            "policy": {"tax_rate": .12, "tariff_rate": .05, **dict.fromkeys(SPENDING, 0),
                "monetary_rate": .04, "foreign_investment_openness": .5,
                "infrastructure_target": "railways", "subsidy_sector": "machinery"},
            "finance": {"government_debt": c.debt, "foreign_debt": 0, "reserves": c.treasury,
                "deficit_months": 0, "budget_balance": 0, "interest_rate": .04},
            "external_balance": 100000.0, "private_savings": 0.0, "exports": 0.0, "imports": 0.0,
            "foreign_savings": 0.0,
            "trade_partners": [], "transport_capacity": 0.0, "trade_disruption": 0.0,
            "monthly_value_added": 0.0, "gdp_per_capita": 0.0, "sector_shares": {},
            "real_growth_estimate": 0.0, "labour": {}, "report": {}, "price_scale": 1.0}
    for key, region in sorted(world.regions.items()):
        population = region.population
        productive = int(population * .55)
        region.infrastructure = {**dict.fromkeys(INFRA, 0.0), "roads": 10.0, "communications": 5.0,
                                 "sanitation": 5.0, **region.infrastructure}
        regions[key] = {
            "demography": {"total_population": population, "productive_population": productive,
                "productive_share": .55, "birth_rate": settings["birth_rate"], "death_rate": settings["death_rate"],
                "infant_mortality": .12, "urbanization": .2, "migration": 0,
                "health_level": .3, "food_security": 1.0, "war_losses": 0, "epidemic_losses": 0,
                "disaster_losses": 0, "remainders": dict.fromkeys(("births", "deaths"), 0.0), "last_account": {}},
            "education": dict(zip(EDUCATION, (.2, .15, .04, .02, .01))),
            "skilled_share": .06, "professional_share": .01, "labour": {},
            "sectors": {s: {"capital_stock": productive * SHARES[s], "labour": 0.0,
                "initial_capital_stock": productive * SHARES[s],
                "skilled_labour": 0.0, "output": 0.0, "value_added": 0.0, "energy_used": 0.0,
                "raw_materials_used": 0.0, "investment": dict.fromkeys(("private", "government", "foreign"), 0.0),
                "depreciation_losses": 0.0, "destruction_losses": 0.0,
                "technology": {"access": False, "discovery": False, "installed": False, "adoption": 0.0,
                    "diffusion": 0.0, "domestic_capability": 0.0, "foreign_experts": 0.0}, "factors": {}}
                for s in SECTORS},
            "stocks": {"energy": population * .01, "raw_materials": population * .02,
                       "goods": dict.fromkeys(SECTORS, 0.0)},
            "resource_endowment": 1.0, "logistics_cost_index": 1.0, "military_supply_index": 0.0,
            "investment_attractiveness": .5, "inputs": {}, "shock_factors": {}, "pending_losses":
                dict.fromkeys(("war_losses", "epidemic_losses", "disaster_losses"), 0)}
        override = (region_overrides or {}).get(key, {})
        allowed = {"demography", "education", "capital_stock", "infrastructure", "stocks"}
        if set(override) - allowed:
            raise ValueError("Unknown region initialization override")
        for field in ("demography", "education", "stocks"):
            regions[key][field].update(copy.deepcopy(override.get(field, {})))
        region.infrastructure.update(override.get("infrastructure", {}))
        for sector, value in override.get("capital_stock", {}).items():
            if sector not in SECTORS: raise ValueError("Unknown capital sector")
            regions[key]["sectors"][sector]["capital_stock"] = value
            regions[key]["sectors"][sector]["initial_capital_stock"] = value
        if regions[key]["demography"]["total_population"] != population:
            raise ValueError("Initial demographic population must match the region")
    world.economy = {"version": 2, "model_revision": 2, "regions": regions, "countries": countries, "trades": {}, "shocks": {},
                     "last_settlement_date": world.date, "settlements": 0, "next_project_id": 1, "warnings": []}
    if any(world.countries[c].gdp for c in countries):
        reference = copy.deepcopy(world)
        for r in reference.economy["regions"].values():
            r["shock_factors"] = {"harvest": 1, "disruption": 1, "trade": 1, "foreign_investment": 1, "emergency": 0}
        for country in countries:
            labour_and_technology(reference, country, 30)
            production(reference, country, 30)
            annual = sum(v["value_added"] for r in owned_regions(reference, country)
                         for v in reference.economy["regions"][r]["sectors"].values()) * 365 / 30
            if world.countries[country].gdp and annual > 0:
                countries[country]["price_scale"] = world.countries[country].gdp / annual


def change_policy(world, country, changes):
    policy = world.economy["countries"][country]["policy"]
    for key, value in changes.items():
        if key == "emergency_finance" and world.economy.get("stabilization_version") == 1:
            if type(value) is not bool: raise ValueError("Emergency financing choice must be boolean")
        elif key == "emergency_instruments" and world.economy.get("stabilization_version") == 1:
            from .credit import INSTRUMENTS
            if not isinstance(value, list) or len(value) != len(set(value)) or any(v not in INSTRUMENTS for v in value):
                raise ValueError("Unknown or duplicate emergency instrument")
        elif key in SPENDING:
            if type(value) is not int or value < 0: raise ValueError("Spending must be a nonnegative monthly integer")
        elif key in ("tax_rate", "tariff_rate", "monetary_rate", "foreign_investment_openness"):
            cap = .6 if key in ("tax_rate", "tariff_rate") else .3 if key == "monetary_rate" else 1
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= cap:
                raise ValueError("Policy ratio outside supported range")
        elif key == "infrastructure_target":
            if value not in INFRA: raise ValueError("Unknown infrastructure target")
        elif key == "subsidy_sector":
            if value not in SECTORS: raise ValueError("Unknown subsidy sector")
        else: raise ValueError("Unknown economic policy")
    policy.update(copy.deepcopy(changes))


def enable_stabilization(world):
    """Opt-in rule migration. Loading an earlier game never silently changes its rules."""
    if world.economy and "stabilization_version" in world.economy:
        raise ValueError("Economic stabilization is already enabled")
    if not world.economy or world.economy.get("model_revision") != 2:
        raise ValueError("Stabilization requires calibrated economy revision 2")
    from . import credit, labour
    world.economy["stabilization_version"] = 1
    credit.initialize(world)
    labour.initialize(world)
    world.economy.update(version=3, model_revision=3)


def add_shock(world, shock_id, kind, region, severity, duration_days=90):
    if (kind not in SHOCKS or region not in world.economy["regions"] or shock_id in world.economy["shocks"]
        or type(severity) not in (int, float) or not math.isfinite(severity) or not 0 <= severity <= 1
        or type(duration_days) is not int or duration_days <= 0):
        raise ValueError("Invalid economic shock")
    world.economy["shocks"][shock_id] = {"id": shock_id, "kind": kind, "region": region,
        "severity": severity, "start_date": world.date,
        "end_date": (canonical_date(world.date) + timedelta(days=duration_days)).isoformat(), "applied": False}
    if kind in ("war_disruption", "capital_destruction") and severity > .5:
        for p in world.projects.values():
            if p["region"] == region and p["status"] not in ("planned", "completed", "cancelled", "disrupted"):
                p["disruption"], p["status"], p["shock_id"] = p["status"], "disrupted", shock_id


def add_trade(world, trade_id, exporter, importer, sector, monthly_quantity, mode="land"):
    countries = world.economy["countries"]
    if (trade_id in world.economy["trades"] or exporter == importer or exporter not in countries
        or importer not in countries or sector not in SECTORS or mode not in ("land", "sea")
        or type(monthly_quantity) not in (int, float) or not math.isfinite(monthly_quantity) or monthly_quantity <= 0):
        raise ValueError("Invalid trade route")
    world.economy["trades"][trade_id] = {"id": trade_id, "exporter": exporter, "importer": importer,
        "sector": sector, "monthly_quantity": monthly_quantity, "mode": mode, "exported": 0.0, "imported": 0.0, "factors": {}}


class _OwnedRegions:
    """Private owner index, dependent on both regional maps.

    Economic membership and Region.owner remain authoritative. Nested journal
    changes refresh only their regional keys; rollback or a log gap rebuilds.
    Newly inserted uninstrumented Region records are rechecked until a barrier.
    """
    def __init__(self):
        self.economy_token = self.world_token = None
        self.owners, self.groups, self.untracked = {}, {}, set()

    def refresh(self, economic_regions, regions):
        economic_token, economic_changes = changes_since(economic_regions, self.economy_token)
        world_token, world_changes = changes_since(regions, self.world_token)
        if economic_changes is None or world_changes is None:
            self.owners.clear()
            self.groups.clear()
            self.untracked.clear()
            changed = set(economic_regions)
        else:
            changed = economic_changes | world_changes | self.untracked
        tracker = regions._tracker
        try:
            for key in sorted(changed):
                if key in self.owners:
                    previous = self.owners.pop(key)
                    self.groups[previous].discard(key)
                    if not self.groups[previous]:
                        del self.groups[previous]
                self.untracked.discard(key)
                if key not in economic_regions:
                    continue
                region = regions[key]  # Preserve missing-world-region errors.
                owner = region.owner
                self.groups.setdefault(owner, set()).add(key)
                self.owners[key] = owner
                if getattr(region, '_tracker', None) is not tracker:
                    self.untracked.add(key)
        except Exception:
            # A failed query may leave a partial index; retry from authoritative
            # maps even when the caller has not performed another mutation.
            self.economy_token = self.world_token = None
            raise
        self.economy_token, self.world_token = economic_token, world_token


def owned_regions(world, country):
    economic_regions, regions = world.economy["regions"], world.regions
    if version(economic_regions) is None or version(regions) is None:
        return [key for key in sorted(economic_regions) if regions[key].owner == country]
    try:
        hash(country)
    except TypeError:
        return [key for key in sorted(economic_regions) if regions[key].owner == country]
    caches = getattr(economic_regions, '_active_indexes', None)
    if caches is None:
        caches = economic_regions._active_indexes = {}
    index = caches.get('economic-region-owners')
    if index is None:
        index = caches['economic-region-owners'] = _OwnedRegions()
    try:
        index.refresh(economic_regions, regions)
    except TypeError:
        # Raw invalid/unhashable owners retain the original equality behavior.
        return [key for key in sorted(economic_regions) if regions[key].owner == country]
    return sorted(index.groups.get(country, ()))


def shocks_for_period(world, start, end):
    econ = world.economy
    active = []
    for key, shock in sorted(econ["shocks"].items()):
        if shock["start_date"] < end and shock["end_date"] > start:
            active.append(shock)
        if shock["end_date"] <= end:
            for p in world.projects.values():
                if p.get("shock_id") == key and p["status"] == "disrupted":
                    p["status"], p["disruption"] = p["disruption"], None
                    p.pop("shock_id", None)
    for key, r in econ["regions"].items():
        r["shock_factors"] = {"harvest": 1.0, "disruption": 1.0, "trade": 1.0, "foreign_investment": 1.0, "emergency": 0.0}
        for shock in [s for s in active if s["region"] == key]:
            overlap = max(0, (min(canonical_date(end), canonical_date(shock["end_date"])) -
                              max(canonical_date(start), canonical_date(shock["start_date"]))).days)
            period = (canonical_date(end) - canonical_date(start)).days
            strength, kind = shock["severity"] * overlap / max(1, period), shock["kind"]
            if kind == "good_harvest": r["shock_factors"]["harvest"] *= 1 + .5 * strength
            if kind in ("bad_harvest", "drought", "flood"): r["shock_factors"]["harvest"] *= 1 - .8 * strength
            if kind in ("war_disruption", "epidemic", "flood", "capital_destruction"):
                r["shock_factors"]["disruption"] *= 1 - .6 * strength
                r["shock_factors"]["emergency"] += .3 * strength
            if kind in ("war_disruption", "blockade"): r["shock_factors"]["trade"] *= 1 - strength
            if kind == "foreign_investment_boom": r["shock_factors"]["foreign_investment"] *= 1 + 3 * strength
            if not shock["applied"]:
                strength = shock["severity"]  # one-off destruction/losses are not a daily rate
                loss_kind = "war_losses" if kind == "war_disruption" else "epidemic_losses" if kind == "epidemic" else "disaster_losses"
                mortality = .08 if kind == "war_disruption" else .05 if kind == "epidemic" else .01 if kind == "flood" else 0
                r["pending_losses"][loss_kind] += int(r["demography"]["total_population"] * mortality * strength)
                destruction = .35 if kind == "war_disruption" else .7 if kind == "capital_destruction" else .1 if kind == "flood" else 0
                for sector in r["sectors"].values():
                    damage = sector["capital_stock"] * destruction * strength
                    sector["capital_stock"] -= damage
                    sector["destruction_losses"] += damage
                for target in INFRA:
                    world.regions[key].infrastructure[target] *= 1 - destruction * strength
                if kind == "resource_discovery": r["resource_endowment"] += strength
                if kind == "foreign_investment_boom" and econ.get("model_revision", 1) >= 2:
                    owner = world.regions[key].owner
                    country = econ["countries"][owner]
                    # Severity 1 offers equity equal to 20% of regional capital,
                    # subject to policy openness. Commit once, then build normally.
                    inflow = math.fsum(s["capital_stock"] for s in r["sectors"].values()) * .2 * strength * country["policy"]["foreign_investment_openness"]
                    country["foreign_savings"] += inflow
                    shock["foreign_capital_committed"] = inflow
                if kind in ("war_disruption", "capital_destruction") and strength > .5 and shock["end_date"] > world.date:
                    for p in world.projects.values():
                        if p["region"] == key and p["status"] not in ("completed", "cancelled", "planned", "disrupted"):
                            p["disruption"], p["status"], p["shock_id"] = p["status"], "disrupted", shock["id"]
                shock["applied"] = True
    return active


def update_population(world, country, start, days, active):
    receipts = []
    for key in owned_regions(world, country):
        r = world.economy["regions"][key]
        d, policy = r["demography"], world.economy["countries"][country]["policy"]
        before, fraction = d["total_population"], days / 365
        sanitation = ratio(world.regions[key].infrastructure.get("sanitation", 0))
        target_health = min(1, .3 + .4 * sanitation + .15 * min(1, policy["welfare"] / max(1, before * .01)))
        d["health_level"] += (target_health - d["health_level"]) * min(1, fraction * .2)
        effective_births = d["birth_rate"] * (1 - d["infant_mortality"] * (1 - .5 * d["health_level"]))
        effective_deaths = d["death_rate"] * (1 - .35 * d["health_level"] - .15 * sanitation)
        effective_deaths += (1 - d["food_security"]) * .04 + d["urbanization"] * (1 - sanitation) * .002
        flows = {}
        for flow, rate in (("births", effective_births), ("deaths", effective_deaths)):
            exact = before * rate * fraction + d["remainders"][flow]
            flows[flow] = int(exact)
            d["remainders"][flow] = exact - flows[flow]
        flows["migration"] = round(d["migration"] * days / (365/12))
        flows.update(r["pending_losses"])
        after = before + flows["births"] - flows["deaths"] + flows["migration"] - sum(flows[k] for k in ("war_losses", "epidemic_losses", "disaster_losses"))
        if after < 0: raise ValueError("Population losses/migration exceed population")
        sources = ["v1 monthly birth/death model", "explicit monthly migration"] + [s["id"] for s in active if s["region"] == key]
        account = {"period_start": start, "period_end": world.date, "elapsed_days": days,
                   "before": before, "after": after, **flows, "sources": sources}
        d["total_population"] = world.regions[key].population = after
        d["productive_population"] = int(after * d["productive_share"])
        for loss in ("war_losses", "epidemic_losses", "disaster_losses"):
            d[loss] += flows[loss]
            r["pending_losses"][loss] = 0
        d["last_account"] = account
        receipts.append(account)
    aggregate = {"period_start": start, "period_end": world.date, "elapsed_days": days,
        "before": sum(a["before"] for a in receipts), "after": sum(a["after"] for a in receipts),
        **{k: sum(a[k] for a in receipts) for k in ("births", "deaths", "migration", "war_losses", "epidemic_losses", "disaster_losses")},
        "sources": sorted({source for a in receipts for source in a["sources"]})}
    world.countries[country].population = aggregate["after"]
    world.population_accounts[country] = aggregate
    return aggregate


def labour_and_technology(world, country, days):
    e = world.economy["countries"][country]
    totals = dict.fromkeys((*SECTORS, "military", "military_service", "military_support", "skilled", "professionals"), 0.0)
    fraction = days / 365
    regions = owned_regions(world, country)
    for key in regions:
        r = world.economy["regions"][key]
        d, education = r["demography"], r["education"]
        infra = world.regions[key].infrastructure
        logistics = sum(ratio(infra.get(k, 0)) for k in ("roads", "railways", "ports", "communications")) / 4
        urban_target = min(.85, .15 + .4 * education["literacy"] + .3 * logistics)
        d["urbanization"] += (urban_target - d["urbanization"]) * min(1, fraction * .15)
        skill_target = min(.75, .03 + .15 * education["literacy"] + .5 * education["technical"])
        r["skilled_share"] += (skill_target - r["skilled_share"]) * min(1, fraction * parameters(world)["skill_adjustment"])
        professional_target = min(.3, .005 + .3 * education["university"] + .1 * education["technical"])
        r["professional_share"] += (professional_target - r["professional_share"]) * min(1, fraction * .2)
        productive = d["productive_population"]
        actual_army = sum(a.personnel for a in world.armies.values() if a.owner == country and a.region == key)
        if world.economy.get("stabilization_version") == 1:
            actual_army = sum(a.personnel for name, a in world.armies.items() if a.owner == country and
                world.economy["labour_army_origins"].get(name, regions[0]) == key)
        if actual_army > productive: raise ValueError("Military personnel exceed productive population")
        burden = e["policy"]["military"] / max(1, productive * .05)
        military = actual_army + min(productive - actual_army, productive * min(.2, burden * .04))
        civilian = productive - military
        agriculture_share = max(.2, .65 - .4 * d["urbanization"] - .1 * education["literacy"])
        weights = dict(SHARES)
        weights["agriculture"] = agriculture_share
        denominator = sum(SHARES[s] for s in SECTORS if s != "agriculture")
        for s in SECTORS:
            if s != "agriculture": weights[s] = SHARES[s] / denominator * (1 - agriculture_share)
        allocations = {s: civilian * weights[s] for s in SECTORS}
        r["labour"] = {**allocations, "military": military, "military_service": actual_army,
                       "military_support": military - actual_army, "skilled": civilian * r["skilled_share"],
                       "professionals": civilian * r["professional_share"],
                       "manufacturing": sum(allocations[s] for s in ("light_industry", "heavy_industry", "machinery", "chemicals")),
                       "transport_logistics": allocations["transport"] + allocations["shipping"]}
        for s in SECTORS:
            sector = r["sectors"][s]
            sector["labour"] = allocations[s]
            sector["skilled_labour"] = allocations[s] * r["skilled_share"]
            technology = sector["technology"]
            if technology["access"] and technology["installed"] and sector["capital_stock"] > 0 and allocations[s] > 0:
                training = education["technical"] + r["professional_share"] + technology["foreign_experts"]
                technology["adoption"] += (1 - technology["adoption"]) * min(1, fraction * (.05 + training))
                technology["diffusion"] += (technology["adoption"] - technology["diffusion"]) * min(1, fraction * (parameters(world)["diffusion_base"] + education["literacy"] * .2))
                technology["domestic_capability"] += (technology["diffusion"] - technology["domestic_capability"]) * min(1, fraction * (.02 + training * .2))
        r["logistics_cost_index"] = 1 / (1 + 2 * logistics)
        r["military_supply_index"] = min(1, .2 + .8 * logistics)
        r["investment_attractiveness"] = min(1, .2 + .4 * logistics + .4 * education["literacy"])
        for name in totals: totals[name] += r["labour"][name]
    totals["manufacturing"] = sum(totals[s] for s in ("light_industry", "heavy_industry", "machinery", "chemicals"))
    totals["transport_logistics"] = totals["transport"] + totals["shipping"]
    e["labour"] = totals
    if world.economy.get("stabilization_version") == 1:
        from .labour import allocate
        allocate(world, country, days)


def production(world, country, days):
    e = world.economy["countries"][country]
    profile, policy = e["calibration"], e["policy"]
    fraction = days / 365
    debt_ratio = world.countries[country].debt / max(1, world.countries[country].gdp, world.countries[country].population * .2)
    for key in owned_regions(world, country):
        r = world.economy["regions"][key]
        infra = world.regions[key].infrastructure
        logistical = 1 - r["logistics_cost_index"]
        stocks = r["stocks"]
        for target in INFRA:
            infra[target] *= (1 - .01) ** fraction
        r["inputs"] = {"opening_energy": stocks["energy"], "opening_raw_materials": stocks["raw_materials"]}
        potentials = {}
        for s in SECTORS:
            sector = r["sectors"][s]
            depreciation = sector["capital_stock"] * (1 - (1 - profile["depreciation"]) ** fraction)
            sector["capital_stock"] -= depreciation
            sector["depreciation_losses"] += depreciation
            tech = sector["technology"]
            technology_factor = 1 + parameters(world)["technology_gain"] * tech["adoption"] * tech["diffusion"] * tech["domestic_capability"]
            skilled_factor = .35 + .65 * min(1, sector["skilled_labour"] / max(1e-9, sector["labour"] * SKILL[s]))
            infrastructure_factor = .8 + .4 * ratio(infra.get("roads", 0), parameters(world)["infrastructure_scale"]) + .2 * ratio(infra.get("electricity", 0), parameters(world)["infrastructure_scale"])
            market = max(.25, (.6 + .4 * logistical) * (1 - policy["tax_rate"] * .3) / (1 + debt_ratio * .08))
            military_demand = 1 + min(.5, policy["military"] / max(1, world.countries[country].population * .02)) if s in ("heavy_industry", "machinery") else 1
            construction_demand = 1 + min(.5, (policy["infrastructure"] + policy["subsidy"]) / max(1, world.countries[country].population * .02)) if s == "construction" else 1
            utilization = min(1, market * military_demand * construction_demand)
            harvest = r["shock_factors"]["harvest"] if s == "agriculture" else 1
            resource_factor = min(2, r["resource_endowment"]) if s == "mining" else 1
            multiplier = 4 if s == "energy" else 3 if s == "mining" else 1
            capacity = min(sector["labour"] * profile["productivity"], sector["capital_stock"] * profile["capital_efficiency"])
            output = capacity * fraction * skilled_factor * technology_factor * infrastructure_factor * utilization * r["shock_factors"]["disruption"] * harvest * resource_factor * multiplier
            potentials[s] = output
            sector["factors"] = {"labour_capacity": sector["labour"] * profile["productivity"],
                "capital_capacity": sector["capital_stock"] * profile["capital_efficiency"],
                "binding_capacity": capacity, "skilled_labour": skilled_factor, "technology": technology_factor,
                "infrastructure": infrastructure_factor, "railway_access": ratio(infra.get("railways", 0)),
                "market_access": market, "disruption": r["shock_factors"]["disruption"],
                "logistics_access": logistical, "debt_ratio": debt_ratio,
                "tax_multiplier": 1 - policy["tax_rate"] * .3,
                "capacity_utilization": utilization,
                "construction_demand": construction_demand,
                "harvest": harvest, "military_demand": military_demand, "resource_endowment": resource_factor,
                "unit_multiplier": multiplier, "elapsed_days": days, "unconstrained_output": output,
                "depreciation_amount": depreciation}
        actual = {}
        # Mining uses carried energy; energy uses carried raw materials plus this period's mining.
        mining = min(potentials["mining"], stocks["energy"] / ENERGY["mining"])
        stocks["energy"] -= mining * ENERGY["mining"]
        stocks["raw_materials"] += mining
        actual["mining"] = mining
        energy = min(potentials["energy"], stocks["raw_materials"] / RAW["energy"])
        stocks["raw_materials"] -= energy * RAW["energy"]
        stocks["energy"] += energy
        actual["energy"] = energy
        other = [s for s in SECTORS if s not in ("mining", "energy")]
        energy_need = sum(potentials[s] * ENERGY[s] for s in other)
        raw_need = sum(potentials[s] * RAW[s] for s in other)
        # Shared proportional rationing; no arbitrary priority by dictionary order.
        energy_supply = min(1, stocks["energy"] / max(energy_need, 1e-9))
        raw_supply = min(1, stocks["raw_materials"] / max(raw_need, 1e-9))
        supply_factor = min(energy_supply, raw_supply)
        for s in other:
            # An input with zero requirement cannot constrain that sector.
            # Original revision keeps its uniform rationing for old saves.
            sector_supply = min(energy_supply if ENERGY[s] else 1, raw_supply if RAW[s] else 1) if world.economy.get("model_revision", 1) >= 2 else supply_factor
            actual[s] = potentials[s] * sector_supply
        for s in SECTORS:
            sector = r["sectors"][s]
            output = actual[s]
            sector["output"] = output
            sector["energy_used"], sector["raw_materials_used"] = output * ENERGY[s], output * RAW[s]
            sector["factors"]["input_supply"] = output / potentials[s] if potentials[s] else 1
            sector["factors"]["energy_supply"] = energy_supply if s in other and ENERGY[s] else sector["factors"]["input_supply"] if s == "mining" else 1
            sector["factors"]["raw_material_supply"] = raw_supply if s in other and RAW[s] else sector["factors"]["input_supply"] if s == "energy" else 1
            sector["value_added"] = max(0, output * PRICE[s] - sector["energy_used"] * PRICE["energy"] - sector["raw_materials_used"] * PRICE["mining"]) * e["price_scale"]
            if s in other:
                stocks["energy"] -= sector["energy_used"]
                stocks["raw_materials"] -= sector["raw_materials_used"]
                stocks["goods"][s] += output
        stocks["energy"], stocks["raw_materials"] = max(0, stocks["energy"]), max(0, stocks["raw_materials"])
        r["inputs"].update({"mining_produced": mining, "energy_produced": energy,
            "energy_consumed": math.fsum(r["sectors"][s]["energy_used"] for s in SECTORS),
            "raw_materials_consumed": math.fsum(r["sectors"][s]["raw_materials_used"] for s in SECTORS),
            "closing_energy": stocks["energy"], "closing_raw_materials": stocks["raw_materials"]})
        food_need = r["demography"]["total_population"] * .08 * fraction
        if getattr(world, 'military_state', None) is not None:
            from .military_bridge import civilian_food_need
            food_need = civilian_food_need(world, key, food_need)
        available_food = stocks["goods"]["agriculture"]
        r["demography"]["food_security"] = min(1, available_food / max(food_need, 1e-9))
        stocks["goods"]["agriculture"] -= min(food_need, available_food)
        for s in other: stocks["goods"][s] *= .8  # consumption/spoilage proxy, not a price model


def settle_trade(world, days):
    econ = world.economy
    tariff_revenue = dict.fromkeys(econ["countries"], 0.0)
    capacities = {}
    for country, e in econ["countries"].items():
        e["exports"], e["imports"], e["trade_partners"] = 0.0, 0.0, []
        regions = owned_regions(world, country)
        e["trade_disruption"] = 1 - min(econ["regions"][r]["shock_factors"]["trade"] for r in regions)
        capacities[country] = sum((econ["regions"][r]["sectors"]["transport"]["output"] +
            econ["regions"][r]["sectors"]["shipping"]["output"]) * (1 + ratio(world.regions[r].infrastructure.get("railways", 0))) for r in regions)
        e["transport_capacity"] = capacities[country]
    for trade in [t for _, t in sorted(econ["trades"].items())]:
        a, b, sector = trade["exporter"], trade["importer"], trade["sector"]
        seller, buyer = econ["countries"][a], econ["countries"][b]
        relations = [v for v in world.relations.values() if {v["a"], v["b"]} == {a, b}]
        diplomacy = min((.1 if v.get("status") == "war" else v.get("trade_openness", 1) for v in relations), default=1)
        transport = min(capacities[a], capacities[b])
        base_transport, port_access = transport, 1.0
        if trade["mode"] == "sea":
            ports = min(sum(world.regions[r].infrastructure.get("ports", 0) for r in owned_regions(world, c)) for c in (a, b))
            port_access = ratio(ports)
            transport *= port_access
        disruption = (1 - seller["trade_disruption"]) * (1 - buyer["trade_disruption"])
        tariff = buyer["policy"]["tariff_rate"]
        requested = trade["monthly_quantity"] * days / (365/12) * diplomacy * disruption / (1 + tariff)
        export_regions = owned_regions(world, a)
        stock_key = "energy" if sector == "energy" else "raw_materials" if sector == "mining" else None
        available = sum(econ["regions"][r]["stocks"][stock_key] if stock_key else econ["regions"][r]["stocks"]["goods"][sector] for r in export_regions)
        unit_price = PRICE[sector] * seller["price_scale"]
        quantity = min(requested, transport, available, buyer["external_balance"] / (unit_price * (1 + tariff)))
        for r in export_regions:
            stock = econ["regions"][r]["stocks"]
            inventory = stock if stock_key else stock["goods"]
            name = stock_key or sector
            remove = min(quantity / len(export_regions), inventory[name])
            # Finish any remainder deterministically; avoid removing more than delivered.
            trade.setdefault("_removed", 0.0)
            inventory[name] -= remove
            if stock_key and econ["regions"][r]["inputs"]:
                inputs = econ["regions"][r]["inputs"]
                label = "energy" if stock_key == "energy" else "raw_materials"
                inputs[f"{label}_exports"] = inputs.get(f"{label}_exports", 0) + remove
                inputs[f"closing_{label}"] -= remove
            trade["_removed"] += remove
        missing = quantity - trade.pop("_removed", 0.0)
        for r in export_regions:
            stock = econ["regions"][r]["stocks"]
            inventory = stock if stock_key else stock["goods"]
            name = stock_key or sector
            take = min(max(0, missing), inventory[name])
            inventory[name] -= take
            if stock_key and econ["regions"][r]["inputs"]:
                inputs = econ["regions"][r]["inputs"]
                label = "energy" if stock_key == "energy" else "raw_materials"
                inputs[f"{label}_exports"] = inputs.get(f"{label}_exports", 0) + take
                inputs[f"closing_{label}"] -= take
            missing -= take
        import_regions = owned_regions(world, b)
        for r in import_regions:
            stock = econ["regions"][r]["stocks"]
            inventory = stock if stock_key else stock["goods"]
            inventory[stock_key or sector] += quantity / len(import_regions)
            if stock_key and econ["regions"][r]["inputs"]:
                inputs = econ["regions"][r]["inputs"]
                label = "energy" if stock_key == "energy" else "raw_materials"
                inputs[f"{label}_imports"] = inputs.get(f"{label}_imports", 0) + quantity / len(import_regions)
                inputs[f"closing_{label}"] += quantity / len(import_regions)
        value = quantity * unit_price
        seller["external_balance"] += value
        buyer["external_balance"] -= value * (1 + tariff)
        tariff_revenue[b] += value * tariff
        seller["exports"] += value
        buyer["imports"] += value
        capacities[a] -= quantity
        capacities[b] -= quantity
        seller["trade_partners"] = sorted(set(seller["trade_partners"] + [b]))
        buyer["trade_partners"] = sorted(set(buyer["trade_partners"] + [a]))
        trade.update(exported=quantity, imported=quantity, factors={"diplomacy": diplomacy,
            "disruption": disruption, "tariff": tariff, "transport_capacity": transport, "requested": requested,
            "base_transport_capacity": base_transport, "port_access": port_access,
            "available": available, "value": value})
    return tariff_revenue


def reserve_project(world, country, region, kind, target, amount, source):
    econ = world.economy
    project_id = f"economy-{econ['next_project_id']}"
    econ["next_project_id"] += 1
    p = plan(world, project_id, country, region, kind, target, amount, source)
    p["prepaid"] = True
    if econ.get("stabilization_version") == 1 and source == "government":
        f = econ["countries"][country]["finance"]
        p["payment_arrears_fraction"] = f.get("arrears_financing", 0) / max(1, sum(f.get("paid_spending", {}).values()))
    try: fund(world, p, prepaid=True)
    except ValueError: pass  # funds remain committed; resources can arrive in a later settlement
    return project_id


def split_budget(budget, regions):
    base, remainder = divmod(budget, len(regions))
    return [(region, base + (index < remainder)) for index, region in enumerate(regions)]


def finance_and_investment(world, country, days, tariffs):
    e, c = world.economy["countries"][country], world.countries[country]
    policy = e["policy"]
    factor = days / (365/12)
    va = e["monthly_value_added"]
    debt_ratio = c.debt / max(1, c.gdp)
    rate = min(.3, policy["monetary_rate"] + min(.2, debt_ratio * .02))
    revenue = {"taxes": round(va * policy["tax_rate"]), "tariffs": round(tariffs),
               "state_enterprises": round(va * .01), "other_revenue": 0}
    spending = {name: round(policy[name] * factor) for name in SPENDING}
    emergency = sum(world.economy["regions"][r]["shock_factors"]["emergency"] for r in owned_regions(world, country))
    spending["other"] += round(va * emergency)
    spending["debt_service"] = round(c.debt * rate * days / 365)
    opening_treasury, opening_debt = c.treasury, c.debt
    if world.economy.get("stabilization_version") == 1:
        from .credit import settle
        settle(world, country, revenue, spending, days,
               allow_emergency=policy.get("emergency_finance", False),
               instruments=policy.get("emergency_instruments"))
        spending = e["finance"]["paid_spending"]
        deficit_months = e["finance"]["deficit_months"]
    else:
        balance = sum(revenue.values()) - sum(spending.values())
        borrowed = max(0, -(c.treasury + balance))
        c.treasury += balance + borrowed
        c.debt += borrowed
        foreign_debt = e["finance"].get("foreign_debt", 0) + round(borrowed * .25 * policy["foreign_investment_openness"])
        deficit_months = e["finance"].get("deficit_months", 0) + 1 if balance < 0 else 0
        e["finance"] = {**revenue, **spending, "other_expenditure": spending["other"], "budget_balance": balance,
            "government_debt": c.debt, "foreign_debt": min(c.debt, foreign_debt), "reserves": c.treasury,
            "interest_rate": rate, "deficit_months": deficit_months, "borrowed": borrowed,
            "opening_treasury": opening_treasury, "opening_debt": opening_debt,
            "total_revenue": sum(revenue.values()), "total_expenditure": sum(spending.values())}
    # Foreign equity raises capacity but part of its value added leaves as profits.
    foreign_profit = 0.0
    for region in owned_regions(world, country):
        for sector in SECTORS:
            state = world.economy["regions"][region]["sectors"][sector]
            financing = state["initial_capital_stock"] + math.fsum(state["investment"].values())
            foreign_profit += state["value_added"] * state["investment"]["foreign"] / max(1, financing) * .2
    remitted = min(e["external_balance"], foreign_profit)
    e["external_balance"] -= remitted
    e["finance"]["foreign_profit_remittances"] = remitted
    pressure = min(100, c.debt / max(1, c.gdp) * 20 + deficit_months * .4)
    if world.economy.get("stabilization_version") != 1:
        world.pressures[f"{country}:financial_crisis"] = {"country": country, "risk": "financial_crisis",
            "value": pressure, "causes": [f"debt/GDP={c.debt / max(1, c.gdp):.4f}", f"deficit_months={deficit_months}"]}
    private = max(0, va - foreign_profit) * e["calibration"]["saving"] * (1 - policy["tax_rate"]) / (1 + debt_ratio * .2 + policy["monetary_rate"] * 2)
    private *= max(.2, 1 - policy["military"] / max(1, va) * .05)
    if world.economy.get("stabilization_version") == 1:
        private *= e["credit"]["hooks"]["civilian_multiplier"]
    e["private_savings"] += private
    # Revision 2 distinguishes replacement of worn equipment from net saving.
    # Both use earned disposable income and wait for the common construction lag.
    # Missing revision denotes the original model, so old saves keep their rules.
    replacement = dict.fromkeys(SECTORS, 0.0)
    if world.economy.get("model_revision", 1) >= 2:
        wear = {s: math.fsum(world.economy["regions"][r]["sectors"][s]["factors"]["depreciation_amount"]
                            for r in owned_regions(world, country)) for s in SECTORS}
        total_wear = math.fsum(wear.values())
        disposable = max(0, va - foreign_profit - revenue["taxes"])
        crowding = max(.2, 1 - policy["military"] / max(1, va) * .05) / (1 + debt_ratio * .2 + policy["monetary_rate"] * 2)
        if world.economy.get("stabilization_version") == 1:
            crowding *= e["credit"]["hooks"]["civilian_multiplier"]
        affordable = min(total_wear * crowding, max(0, disposable - private))
        pool = e.setdefault("replacement_savings", dict.fromkeys(SECTORS, 0.0))
        for s in SECTORS:
            replacement[s] = affordable * wear[s] / total_wear if total_wear else 0
            pool[s] += replacement[s]
        e["finance"].update(private_disposable_income=disposable, net_private_saving=private,
            replacement_reserve=affordable, replacement_need=total_wear)
    regions = owned_regions(world, country)
    projects = []
    for expense, kind, target, conversion in (("education", "education", "human_capital", 1),
        ("infrastructure", "infrastructure", policy["infrastructure_target"], 100),
        ("subsidy", "factory", policy["subsidy_sector"], 1)):
        for region, allocated in split_budget(spending[expense], regions):
            if allocated:
                projects.append(reserve_project(world, country, region, kind, target, allocated / conversion, "government"))
    # Pool small private saving flows; investment waits for construction like public spending.
    if e["private_savings"] >= 10 and (world.economy["settlements"] + 1) % 12 == 0:
        budget = math.floor(e["private_savings"])
        e["private_savings"] -= budget
        allocations = {s: math.floor(budget * SHARES[s]) for s in SECTORS}
        for sector in SECTORS[:budget - sum(allocations.values())]: allocations[sector] += 1
        for sector in SECTORS:
            for region, amount in split_budget(allocations[sector], regions):
                if amount: projects.append(reserve_project(world, country, region, "factory", sector, amount, "private"))
    if (world.economy["settlements"] + 1) % 12 == 0:
        for sector in SECTORS:
            if sector not in e.get("replacement_savings", {}): continue
            savings = e["replacement_savings"][sector]
            budget = math.floor(savings)
            e["replacement_savings"][sector] -= budget
            for region, amount in split_budget(budget, regions):
                if amount: projects.append(reserve_project(world, country, region, "factory", sector, amount, "private"))
    for region in regions:
        r = world.economy["regions"][region]
        foreign = va / len(regions) * .005 * policy["foreign_investment_openness"] * r["investment_attractiveness"] * r["shock_factors"]["foreign_investment"]
        if world.economy.get("stabilization_version") == 1:
            foreign *= e["credit"]["hooks"]["foreign_investment_multiplier"]
        e["foreign_savings"] += foreign
    if e["foreign_savings"] >= len(regions) and (world.economy["settlements"] + 1) % 12 == 0:
        budget = math.floor(e["foreign_savings"])
        e["foreign_savings"] -= budget
        for region, amount in split_budget(budget, regions):
            if amount: projects.append(reserve_project(world, country, region, "factory", "machinery", amount, "foreign"))
    for p in world.projects.values():
        if p["country"] == country and p["status"] == "planned" and p.get("prepaid"):
            try: fund(world, p, prepaid=True)
            except ValueError: pass
    return projects


def bottlenecks(world, country):
    """Diagnostic labels refer to recorded constraints, not prescribed outcomes."""
    result = {}
    for region in owned_regions(world, country):
        result[region] = {}
        for name, state in world.economy["regions"][region]["sectors"].items():
            f, t = state["factors"], state["technology"]
            if not f: continue
            labels = ["capital" if f["capital_capacity"] < f["labour_capacity"] else "labour"]
            if f["skilled_labour"] < .95: labels.append("skills")
            if f.get("energy_supply", 1) < .95: labels.append("energy")
            if f.get("raw_material_supply", 1) < .95: labels.append("raw_materials")
            if f.get("logistics_access", 1) < .1: labels.append("transport")
            if f.get("debt_ratio", 0) > 1: labels.append("debt")
            if t["access"] and t["domestic_capability"] < .2: labels.append("domestic_capability")
            result[region][name] = labels
    return result


def tick(world, tick_event, rng):
    if world.economy is None or canonical_date(world.date).day != 1:
        return []
    econ = world.economy
    start = econ["last_settlement_date"]
    days = (canonical_date(world.date) - canonical_date(start)).days
    if days <= 0: return []
    old_reports = {c: copy.deepcopy(e["report"]) for c, e in econ["countries"].items()}
    active = shocks_for_period(world, start, world.date)
    receipts = {}
    for country in sorted(econ["countries"]):
        receipts[country] = update_population(world, country, start, days, active)
        labour_and_technology(world, country, days)
        production(world, country, days)
        e = econ["countries"][country]
        va = {s: sum(econ["regions"][r]["sectors"][s]["value_added"] for r in owned_regions(world, country)) for s in SECTORS}
        e["monthly_value_added"] = sum(va.values())
        gdp = round(e["monthly_value_added"] * 365 / days)
        previous_gdp = world.countries[country].gdp
        world.countries[country].gdp = gdp
        e["gdp_per_capita"] = gdp / max(1, world.countries[country].population)
        e["real_growth_estimate"] = gdp / previous_gdp - 1 if previous_gdp else 0
        e["sector_shares"] = {s: value / max(1e-9, e["monthly_value_added"]) for s, value in va.items()}
    tariffs = settle_trade(world, days)
    events, warnings = [], []
    for country in sorted(econ["countries"]):
        projects = finance_and_investment(world, country, days, tariffs[country])
        e = econ["countries"][country]
        sectors = {s: sum(econ["regions"][r]["sectors"][s]["value_added"] for r in owned_regions(world, country)) * 365 / days for s in SECTORS}
        previous = old_reports[country].get("sector_value_added", {})
        warning = []
        if e["finance"]["deficit_months"] >= 6: warning.append("persistent budget deficit")
        if world.countries[country].debt > world.countries[country].gdp * 2: warning.append("high public debt")
        for r in owned_regions(world, country):
            if econ["regions"][r]["demography"]["food_security"] < .8: warning.append(f"food shortage: {r}")
        if any(p["country"] == country and p.get("condition_delay_days", 0) >= 30 and p["status"] not in ("completed", "cancelled") for p in world.projects.values()):
            warning.append("project conditions delay completion")
        warnings.extend({"severity": "warning", "country": country, "message": message} for message in warning)
        e["report"] = {"date": world.date, "period_start": start, "elapsed_days": days,
            "gdp": world.countries[country].gdp, "gdp_per_capita": e["gdp_per_capita"],
            "real_growth_estimate": e["real_growth_estimate"], "sector_value_added": sectors,
            "sector_changes": {s: value - previous.get(s, value) for s, value in sectors.items()},
            "sector_shares": e["sector_shares"], "population": receipts[country], "finance": copy.deepcopy(e["finance"]),
            "labour": dict(e["labour"]), "trade": {"exports": e["exports"], "imports": e["imports"], "partners": e["trade_partners"]},
            "production_factors": {r: {s: dict(v["factors"]) for s, v in econ["regions"][r]["sectors"].items()} for r in owned_regions(world, country)},
            "bottlenecks": bottlenecks(world, country),
            "trade_routes": {key: copy.deepcopy(t["factors"]) for key, t in econ["trades"].items() if country in (t["exporter"], t["importer"])},
            "shock_ids": [s["id"] for s in active if world.regions[s["region"]].owner == country],
            "new_projects": projects, "warnings": warning,
            "explanation_kind": "sector value-added differences and production constraints; not an additive causal decomposition"}
        if econ.get("stabilization_version") == 1:
            e["report"]["credit"] = copy.deepcopy(e["credit"])
            e["report"]["labour_movement"] = {r: copy.deepcopy(econ["regions"][r]["labour_state"]["last_report"]) for r in owned_regions(world, country)}
        events.append({"type": "economy_settlement", "country": country, "report": copy.deepcopy(e["report"])})
    econ["warnings"], econ["last_settlement_date"] = warnings, world.date
    econ["settlements"] += 1
    return events


def validate(world, old=None):
    """Errors stop commit; warnings describe modeled risks without hiding them."""
    econ = world.economy
    if econ is None: return []
    from .demography import validate_account
    issues = []
    try:
        if "stabilization_version" in econ and (type(econ["stabilization_version"]) is not int or econ["stabilization_version"] != 1):
            return ["economy: unsupported stabilization version"]
        revision = econ.get("model_revision", 1)
        if type(econ["version"]) is not int or type(revision) is not int or (econ["version"], revision) not in ((1, 1), (2, 2), (3, 3)):
            return ["economy: unsupported model version/revision"]
        if (revision == 3) != (econ.get("stabilization_version") == 1):
            return ["economy: stabilization and model revision mismatch"]
        if not isinstance(econ.get("model_parameters", {}), dict): return ["economy: malformed model parameter map"]
        if revision == 1 and econ.get("model_parameters"): issues.append("economy: legacy model parameters cannot be overridden")
        for key, value in econ.get("model_parameters", {}).items():
            if key not in MODEL_PARAMETER_RANGES or not scalar(value, *MODEL_PARAMETER_RANGES[key]):
                issues.append("economy: invalid model parameter")
        if canonical_date(econ["last_settlement_date"]) > canonical_date(world.date):
            issues.append("economy: future settlement")
        for country, e in econ["countries"].items():
            c = world.countries[country]
            regions = owned_regions(world, country)
            if not regions or c.population != sum(world.regions[r].population for r in regions):
                issues.append(f"economy: national population mismatch {country}")
            if country in world.population_accounts and world.population_accounts[country]["after"] != c.population:
                issues.append("economy: population differs from its settlement account")
            candidate = copy.copy(e["policy"])
            # Reuse policy boundary checks without mutating actual state.
            for key, value in candidate.items():
                if key in SPENDING and (type(value) is not int or value < 0): issues.append("economy: invalid spending")
                if key in ("tax_rate", "tariff_rate", "monetary_rate", "foreign_investment_openness") and not scalar(value, 0, 1):
                    issues.append("economy: invalid policy ratio")
            if e["profile"] not in PROFILES or set(e["calibration"]) != set(PROFILES[e["profile"]]):
                issues.append("economy: invalid calibration profile")
            for key, value in e["calibration"].items():
                if not scalar(value, *RANGES[key]): issues.append(f"economy: invalid calibration {key}")
            for key in ("external_balance", "private_savings", "foreign_savings", "price_scale"):
                if not scalar(e[key], 0): issues.append(f"economy: invalid {key}")
            if "replacement_savings" in e and (set(e["replacement_savings"]) != set(SECTORS) or any(not scalar(v, 0) for v in e["replacement_savings"].values())):
                issues.append("economy: invalid replacement savings")
            if e["price_scale"] <= 0: issues.append("economy: invalid price scale")
            f = e["finance"]
            if not 0 <= f["foreign_debt"] <= c.debt or f["government_debt"] != c.debt or f["reserves"] != c.treasury:
                issues.append("economy: finance mirrors disagree")
            if "total_revenue" in f:
                for name in ("private_disposable_income", "net_private_saving", "replacement_reserve", "replacement_need"):
                    if name in f and not scalar(f[name], 0): issues.append("economy: invalid private investment account")
                if f.get("replacement_reserve", 0) > f.get("replacement_need", float("inf")) + 1e-6:
                    issues.append("economy: replacement exceeds worn capital")
                if f.get("replacement_reserve", 0) + f.get("net_private_saving", 0) > f.get("private_disposable_income", float("inf")) + 1e-6:
                    issues.append("economy: private investment exceeds disposable income")
                if f["budget_balance"] != f["total_revenue"] - f["total_expenditure"]:
                    issues.append("economy: budget balance mismatch")
                if c.debt != f["opening_debt"] + f["borrowed"] + f.get("interim_debt_change", 0):
                    issues.append("economy: unexplained debt")
                if c.treasury != f["opening_treasury"] + f["budget_balance"] + f["borrowed"] + f.get("emergency_non_debt_financing", 0) + f.get("arrears_financing", 0) + f.get("interim_net_cashflow", 0):
                    issues.append("economy: unexplained reserves")
                if f["deficit_months"] >= 6:
                    issues.append({"severity": "warning", "message": f"persistent deficit: {country}"})
            if e["report"]:
                va = math.fsum(econ["regions"][r]["sectors"][s]["value_added"] for r in regions for s in SECTORS)
                if not close(e["monthly_value_added"], va) or c.gdp != round(va * 365 / e["report"]["elapsed_days"]):
                    issues.append("economy: GDP is not sector value-added")
        for region, r in econ["regions"].items():
            d, sectors = r["demography"], r["sectors"]
            if world.regions[region].owner not in econ["countries"]: issues.append("economy: unknown region owner")
            if d["total_population"] != world.regions[region].population or not 0 <= d["productive_population"] <= d["total_population"]:
                issues.append("economy: invalid productive/total population")
            for key in ("productive_share", "infant_mortality", "urbanization", "health_level", "food_security"):
                if not scalar(d[key], 0, 1): issues.append(f"economy: invalid demographic ratio {key}")
            if not scalar(d["birth_rate"], 0, .08) or not scalar(d["death_rate"], 0, .12) or type(d["migration"]) is not int:
                issues.append("economy: invalid demographic rate/migration")
            if d["last_account"]: issues.extend(validate_account(d["last_account"], world.date))
            if d["last_account"] and d["last_account"]["after"] != d["total_population"]:
                issues.append("economy: regional population differs from settlement")
            if old and old.economy and region in old.economy["regions"]:
                old_d = old.economy["regions"][region]["demography"]
                if d["total_population"] != old_d["total_population"]:
                    if d["last_account"] == old_d["last_account"] or d["last_account"].get("before") != old_d["total_population"] or d["last_account"].get("period_end") != world.date:
                        issues.append("economy: population transition lacks a new settlement")
            for value in r["education"].values():
                if not scalar(value, 0, 1): issues.append("economy: invalid education")
            if set(sectors) != set(SECTORS): issues.append("economy: unknown/missing sector")
            workers = sum(s["labour"] for s in sectors.values()) + r["labour"].get("military", 0)
            if workers > d["productive_population"] + 1e-6: issues.append("economy: labour exceeds productive population")
            for name, sector in sectors.items():
                for key in ("capital_stock", "initial_capital_stock", "labour", "skilled_labour", "output", "value_added", "energy_used", "raw_materials_used"):
                    if not scalar(sector[key], 0): issues.append(f"economy: invalid {name}.{key}")
                capital = sector["initial_capital_stock"] + sum(sector["investment"].values()) - sector["depreciation_losses"] - sector["destruction_losses"]
                if not close(sector["capital_stock"], capital): issues.append(f"economy: capital accounting mismatch {name}")
                production_labour = sector.get("factors", {}).get("labour_capacity", sector["labour"]) if econ.get("stabilization_version") == 1 else sector["labour"]
                if sector["skilled_labour"] > sector["labour"] or sector["output"] > 0 and (sector["capital_stock"] <= 0 or production_labour <= 0):
                    issues.append("economy: output without capital/labour")
                factors = sector["factors"]
                if factors:
                    if sector["output"] > factors["unconstrained_output"] + 1e-6: issues.append("economy: output exceeds capacity")
                    if not scalar(factors["capacity_utilization"], 0, 1) or not scalar(factors["input_supply"], 0, 1):
                        issues.append("economy: utilization/input supply exceeds physical capacity")
                    if not close(sector["energy_used"], sector["output"] * ENERGY[name]) or not close(sector["raw_materials_used"], sector["output"] * RAW[name]):
                        issues.append("economy: incorrect intermediate input demand")
                    if world.regions[region].infrastructure.get("railways", 0) == 0 and factors["railway_access"] != 0:
                        issues.append("economy: nonexistent railway productivity")
                tech = sector["technology"]
                for key in ("adoption", "diffusion", "domestic_capability", "foreign_experts"):
                    if not scalar(tech[key], 0, 1): issues.append("economy: invalid technology level")
                if old and old.economy and region in old.economy["regions"]:
                    old_tech = old.economy["regions"][region]["sectors"][name]["technology"]
                    days = (canonical_date(world.date) - canonical_date(old.date)).days
                    if econ["last_settlement_date"] != old.economy["last_settlement_date"] and d["last_account"].get("period_end") == world.date:
                        days = d["last_account"]["elapsed_days"]
                    if any(tech[key] - old_tech[key] > days / 365 * .6 + 1e-9 for key in ("adoption", "diffusion", "domestic_capability")):
                        issues.append("economy: implausibly rapid technology diffusion")
            for key in ("energy", "raw_materials"):
                if not scalar(r["stocks"][key], 0): issues.append("economy: negative/nonfinite resource stock")
            if any(not scalar(v, 0) for v in r["stocks"]["goods"].values()): issues.append("economy: invalid goods inventory")
            inputs = r["inputs"]
            if inputs:
                expected_energy = inputs["opening_energy"] + inputs["energy_produced"] - inputs["energy_consumed"] + inputs.get("energy_imports", 0) - inputs.get("energy_exports", 0) - inputs.get("emergency_energy_used", 0)
                expected_raw = inputs["opening_raw_materials"] + inputs["mining_produced"] - inputs["raw_materials_consumed"] - inputs.get("project_raw_materials_used", 0) + inputs.get("raw_materials_imports", 0) - inputs.get("raw_materials_exports", 0) - inputs.get("emergency_raw_materials_used", 0)
                if not close(r["stocks"]["energy"], expected_energy) or not close(r["stocks"]["raw_materials"], expected_raw):
                    issues.append("economy: energy/raw-material conservation failure")
            if old and old.economy and region in old.economy["regions"]:
                for target, value in world.regions[region].infrastructure.items():
                    increase = value - old.regions[region].infrastructure.get(target, 0)
                    completed = sum(effect["amount"] for pid, p in world.projects.items()
                        if p["region"] == region and p["status"] == "completed" and old.projects.get(pid, {}).get("status") != "completed"
                        for effect in p["effects"] if effect["type"] == "infrastructure" and effect["target"] == target)
                    if increase > completed + 1e-9: issues.append("economy: infrastructure without a completed project")
        for t in econ["trades"].values():
            if t["exporter"] not in econ["countries"] or t["importer"] not in econ["countries"] or t["sector"] not in SECTORS or not close(t["exported"], t["imported"]):
                issues.append("economy: trade counterpart mismatch")
        for shock in econ["shocks"].values():
            if shock["kind"] not in SHOCKS or shock["region"] not in econ["regions"] or not scalar(shock["severity"], 0, 1):
                issues.append("economy: invalid shock")
        if econ.get("stabilization_version") == 1:
            from . import credit, labour
            issues.extend(credit.validate(world, old))
            issues.extend(labour.validate(world, old))
        return issues
    except (KeyError, TypeError, ValueError, OverflowError):
        return issues + ["economy: malformed state"]


def scalar(value, low, high=None):
    return type(value) in (int, float) and math.isfinite(value) and value >= low and (high is None or value <= high)


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
