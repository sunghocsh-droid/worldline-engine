"""State-grounded structural pressure and continuous, saved hazard sampling.

This layer discovers risk, never resolves battles, replaces NPC decisions or
assigns GDP. Annual rates are scenario assumptions, not calendar scripts.
"""
import math
from datetime import timedelta

from . import crisis_state as cs
from .models import canonical_date
from .runtime_queries import values as query_values

VERSION = "1"
LEGACY = {"interstate_war": "war", "diplomatic_crisis": "diplomatic_crisis", "civil_unrest": "regional_rebellion",
    "revolution": "revolution", "coup": "coup", "separatism": "regional_rebellion", "financial_crisis": "financial_crisis",
    "recession": "recession", "famine": "famine", "epidemic": "epidemic", "natural_disaster": "natural_disaster"}
SOCIAL = {"petition", "protest", "strike", "riot", "tax_resistance", "local_uprising_precursor"}
INTERNATIONAL = {"border_incident", "diplomatic_crisis", "ultimatum", "partial_mobilization", "full_mobilization", "armed_clash", "state_of_war"}
FINANCE = {"fiscal_stress", "payment_failure", "financial_crisis"}
FOOD = {"food_shortage", "famine"}


def _clip(value, low=0, high=1):
    return max(low, min(high, value))


def _number(value, fallback=0):
    return float(value) if cs._number(value, -1e100, 1e100) else fallback


def _ratio(value, scale):
    return max(0, value) / max(1e-9, max(0, value) + scale)


def probability(annual_hazard, elapsed_days):
    if not cs._number(annual_hazard, 0) or not cs._number(elapsed_days, 0):
        raise ValueError("Hazard rate and elapsed time must be finite and nonnegative")
    return -math.expm1(-annual_hazard * elapsed_days / 365)


def _legacy(world, category, country):
    risk = LEGACY.get(category)
    return max((_number(p.get("value")) for p in world.pressures.values()
                if p.get("country") == country and p.get("risk") == risk), default=0)


def food_state(world, region):
    """Actual remaining food and last production account, never reserve metadata."""
    r = world.economy["regions"][region]
    d = r["demography"]
    country = world.regions[region].owner
    population = d["total_population"]
    monthly_need = max(1e-9, population * .08 / 12)
    food_stock = max(0, _number(r["stocks"]["goods"].get("agriculture")))
    reserve_months = food_stock / monthly_need
    food_security = _clip(_number(d.get("food_security"), 1))
    shortage = (1 - food_security) * (1 - min(1, reserve_months))
    # Imported agriculture is already in goods stocks. Report it for explanation
    # without counting the same food a second time as another protective buffer.
    imports = math.fsum(_number(t.get("imported")) for t in world.economy.get("trades", {}).values()
        if t.get("importer") == country and t.get("sector") == "agriculture")
    finance = world.economy["countries"][country]["finance"]
    paid_welfare = _number(finance.get("paid_spending", {}).get("welfare"))
    country_population = max(1, world.countries[country].population)
    relief = _clip(paid_welfare / max(1, country_population * .005))
    infra = world.regions[region].infrastructure
    transport = (_ratio(_number(infra.get("roads")), 50) + _ratio(_number(infra.get("railways")), 50)
                 + _ratio(_number(infra.get("ports")), 50)) / 3
    return {"food_security": food_security, "actual_food_stock": food_stock, "monthly_need": monthly_need,
        "actual_reserve_months": reserve_months, "food_gap": shortage, "paid_welfare": paid_welfare,
        "relief_capacity": relief, "transport_access": transport, "actual_food_imports": imports,
        "settled": world.economy.get("settlements", 0) > 0}


def _terms(**values):
    # These are measured/weighted risk inputs, not a fabricated counterfactual
    # GDP decomposition. Some formulas clamp or multiply these signals.
    return [{"factor": key, "value": value, "interpretation": "structural risk input"} for key, value in values.items() if value]


def _pressure(world, category, target, causes, elapsed_days, country=None, region=None, pair=None):
    state = cs._state(world)
    key = cs.pressure_key(category, country, region, pair)
    components = state.setdefault("risk_components", {})
    target = _clip(target, 0, 100)
    current = cs.pressure_value(world, category, country, region, pair)
    previous = components.get(key)
    if previous is None:
        value = max(current, target)
    else:
        # Changes to real causes act now. Persistent unresolved causes rebuild
        # pressure over months, so neither a response nor a cooldown erases risk.
        change = target - previous["target"]
        alpha = -math.expm1(-elapsed_days / 90)
        value = current + change + (target - current) * alpha
    value = _clip(value, 0, 100)
    cs.set_pressure(world, category, value, country, region, pair, causes=causes)
    components[key] = {"target": target, "date": world.date, "causes": causes}
    return {"key": key, "category": category, "country": country, "region": region, "pair": pair,
            "previous_pressure": current, "pressure": value, "structural_target": target, "causes": causes}


def update_pressures(world, elapsed_days=7):
    if type(elapsed_days) is not int or elapsed_days <= 0:
        raise ValueError("Pressure update requires positive whole elapsed days")
    state = cs._state(world)
    results = []
    country_signals = {}
    for country, e in sorted(world.economy["countries"].items()):
        c, credit, finance = world.countries[country], e["credit"], e["finance"]
        revenue = max(1, _number(finance.get("total_revenue"), c.gdp * .13 / 12))
        service = _clip(_number(credit.get("debt_service_ratio")))
        arrears = _clip(_number(credit.get("arrears")) / (revenue * 3))
        default = float(credit.get("distress_state") in ("default", "restructuring"))
        confidence_loss = 1 - _clip(_number(credit.get("creditworthiness"), 75) / 100)
        deficits = _clip(_number(finance.get("deficit_months")) / 12)
        stress_state = {"normal": 0, "stressed": .25, "severe_stress": .55, "arrears": .7,
                        "default": 1, "restructuring": .8}.get(credit.get("distress_state"), 0)
        instability = 1 - _clip(c.stability / 100)
        fiscal = 100 * _clip(.3 * service + .3 * arrears + .2 * default + .2 * stress_state)
        financial = 100 * _clip(.45 * arrears + .25 * default + .15 * confidence_loss + .15 * deficits)
        growth = _number(e.get("real_growth_estimate")) if world.economy.get("settlements", 0) else 0
        negative_growth = _clip(-growth * 5)
        extraction = _clip(_number(credit.get("social_cost_stock")) * .25 + _number(credit.get("civilian_extraction")))
        excessive_tax = _clip((_number(e["policy"].get("tax_rate"), .12) - .15) / .45)
        country_signals[country] = {"fiscal": fiscal, "financial": financial, "arrears": arrears,
            "instability": instability, "extraction": extraction, "tax": excessive_tax,
            "growth": growth, "negative_growth": negative_growth}
        for category, target, causes in (
            ("fiscal_crisis", fiscal, _terms(debt_service=service * 30, unpaid_obligations=arrears * 30, default=default * 20, fiscal_distress=stress_state * 20)),
            ("financial_crisis", financial, _terms(unpaid_obligations=arrears * 45, default=default * 25, credit_deterioration=confidence_loss * 15, persistent_deficits=deficits * 15)),
            ("recession", negative_growth * 100, _terms(actual_negative_growth=negative_growth * 100)),
            ("political_legitimacy", instability * 65 + arrears * 20 + extraction * 15, _terms(instability=instability * 65, unpaid_obligations=arrears * 20, forced_extraction=extraction * 15)),
        ):
            inherited = _legacy(world, category, country)
            if inherited > target:
                causes.append({"factor": "legacy_pressure", "value": inherited, "contribution": inherited - target})
            results.append(_pressure(world, category, max(target, inherited), causes, elapsed_days, country=country))
    for region, r in sorted(world.economy["regions"].items()):
        country = world.regions[region].owner
        s, food = country_signals[country], food_state(world, region)
        config = state["regions"].get(region, {})
        labour = r.get("labour", {})
        labour_state = r.get("labour_state", {})
        force = max(1, _number(labour_state.get("labour_force")))
        unused = _clip((_number(labour.get("unemployed")) + _number(labour.get("underemployed"))) / force)
        mobilized = _clip(_number(labour_state.get("mobilized")) / force)
        vulnerability = _clip(_number(config.get("institutional_vulnerability"), .5))
        groups = config.get("groups", {})
        grievances = []
        for group, data in sorted(groups.items()) if isinstance(groups, dict) else []:
            value = _clip(_number(data.get("grievance"), 0)) if isinstance(data, dict) else _clip(_number(data))
            grievances.append(value)
            key = f"ethnic_or_religious_tension:region:{region}:group:{group}"
            cs.set_pressure(world, "ethnic_or_religious_tension", value * 100, region=region, key=key,
                causes=[{"factor": "group_grievance", "group": group, "value": value}])
        grievance = max(grievances, default=0)
        famine = food["food_gap"] * 100 if food["settled"] else 0
        civil = 100 * _clip(.35 * food["food_gap"] + .2 * unused + .15 * s["tax"] + .15 * s["extraction"]
                           + .1 * s["arrears"] + .1 * mobilized + .1 * grievance
                           + max(0, s["instability"] - .35) * .35 - food["relief_capacity"] * .2)
        labour_risk = 100 * _clip(.4 * unused + .25 * mobilized + .2 * s["arrears"] + .15 * s["extraction"])
        legitimacy = cs.pressure_value(world, "political_legitimacy", country=country) / 100
        coup = 100 * _clip(max(0, legitimacy - .5) * .5 + s["arrears"] * .2 + mobilized * vulnerability * .3)
        revolution = 100 * _clip(civil / 100 * .65 + max(0, legitimacy - .5) * .35)
        military_discontent = 100 * _clip(s["arrears"] * .55 + mobilized * .25 + s["extraction"] * .2)
        epidemic_exposure = 0.0
        for existing in state["events"].values():
            if existing["ongoing"] and existing["event_type"] in ("epidemic_introduction", "epidemic"):
                if existing.get("region") == region:
                    epidemic_exposure = max(epidemic_exposure, existing["severity"])
                    continue
                connected = any(region in b["regions"] and existing.get("region") in b["regions"] for b in state["borders"].values())
                traded = any(t.get("importer") == country and t.get("exporter") in existing["countries"] and t.get("imported", 0) > 0 for t in world.economy["trades"].values())
                if connected or traded:
                    epidemic_exposure = max(epidemic_exposure, existing["severity"])
        sanitation = _ratio(_number(world.regions[region].infrastructure.get("sanitation")), 50)
        health = _clip(_number(r["demography"].get("health_level"), .3))
        crowding = _clip(_number(r["demography"].get("urbanization")))
        epidemic = epidemic_exposure * (1 - .5 * sanitation - .3 * health) * (30 + 70 * crowding)
        targets = (
            ("famine", famine, _terms(uncovered_food_gap=famine)),
            ("civil_unrest", civil, _terms(food_gap=food["food_gap"] * 35, unemployment_underutilization=unused * 20,
                excess_tax=s["tax"] * 15, forced_extraction=s["extraction"] * 15, arrears=s["arrears"] * 10,
                mobilization=mobilized * 10, group_grievance=grievance * 10, paid_relief=-food["relief_capacity"] * 20)),
            ("labour_unrest", labour_risk, _terms(unemployment_underutilization=unused * 40, mobilization=mobilized * 25, arrears=s["arrears"] * 20, forced_extraction=s["extraction"] * 15)),
            ("ethnic_or_religious_tension", grievance * 100, _terms(group_grievance=grievance * 100)),
            ("separatism", grievance * vulnerability * 100, _terms(group_grievance=grievance * 100, institutional_vulnerability=vulnerability)),
            ("revolution", revolution, _terms(unrest=civil, legitimacy_crisis=legitimacy * 100)),
            ("coup", coup, _terms(legitimacy_crisis=legitimacy * 100, military_arrears=s["arrears"] * 20, mobilization=mobilized * 100)),
            ("military_insubordination", military_discontent, _terms(unpaid_obligations=s["arrears"] * 55, mobilization=mobilized * 25, forced_extraction=s["extraction"] * 20)),
            ("epidemic", epidemic, _terms(known_physical_exposure=epidemic_exposure * 100, sanitation=sanitation, health_capacity=health, urban_density=crowding)),
            ("natural_disaster", min(100, math.fsum(config.get("hazard_rates", {}).values()) * 20),
                [{"factor": "configured_environmental_hazard", "value": config.get("hazard_rates", {})}]),
        )
        for category, target, causes in targets:
            inherited = _legacy(world, category, country)
            results.append(_pressure(world, category, max(target, inherited), causes, elapsed_days, country=country, region=region))
    for border_id, border in sorted(state["borders"].items()):
        countries, regions = border["countries"], border["regions"]
        relations = [rel for rel in world.relations.values() if {rel.get("a"), rel.get("b")} == set(countries)]
        dispute = max((_number(rel.get("dispute")) for rel in relations), default=0)
        hostile = float(any(rel.get("status") == "war" for rel in relations))
        distrust = max((max(0, -_number(rel.get("trust"))) for rel in relations), default=0)
        mob = max((_number(world.economy["regions"][region]["labour_state"].get("mobilized")) /
                   max(1, world.economy["regions"][region]["labour_state"]["labour_force"]) for region in regions), default=0)
        legacy = max((_legacy(world, "interstate_war", country) for country in countries), default=0)
        tension = max(_number(border.get("tension")), dispute * 65 + distrust * 15 + mob * 25 + hostile * 50, legacy)
        causes = _terms(configured_border_dispute=_number(border.get("tension")), diplomatic_dispute=dispute * 65,
                       distrust=distrust * 15, actual_mobilization=mob * 25, existing_war=hostile * 50, legacy_pressure=legacy)
        for category, target in (("border_tension", tension), ("diplomatic_crisis", tension * .8), ("interstate_war", tension * (.35 + mob))):
            results.append(_pressure(world, category, target, causes, elapsed_days, pair=countries))
    # All category types have a stable slot even in a landlocked single-country
    # scenario; zero slots never produce events simply because they exist.
    for category in cs.CATEGORIES:
        if not any(p["category"] == category for p in state["pressures"].values()):
            results.append(_pressure(world, category, 0, [], elapsed_days))
    return results


def _eligible(world, family, scope, types):
    state = cs._state(world)
    family_key = f"{family}:{scope}"
    if state["cooldowns"].get(family_key, "") > world.date:
        return False
    for event in query_values(state["events"], "crisis-ongoing-events", lambda e: e["ongoing"]):
        if event.get("risk_family_key") == family_key:
            return False
        if scope in state["borders"] and event["event_type"] in types and set(event["countries"]) == set(state["borders"][scope]["countries"]):
            return False
        if event["event_type"] in types and (event.get("region") == scope or scope in event.get("countries", [])):
            return False
    return True


def _parent(world, types, region=None, country=None, residual_days=90):
    today = canonical_date(world.date)
    events = [e for e in cs._state(world)["events"].values() if e["event_type"] in types and
              (region is None or e.get("region") == region) and (country is None or country in e["countries"])
              and (e["ongoing"] or e.get("end_date") is not None and
                   0 <= (today - canonical_date(e["end_date"])).days <= residual_days)]
    return max(events, key=lambda e: (e["start_date"], int(e["event_id"].rsplit("-", 1)[-1])), default={}).get("event_id")


def _sample(world, create_event, event_type, family, scope, annual_hazard, days, region, countries,
            causes, severity_factor=.5, parent_event=None, actual_facts=None, types=None, **extra):
    if annual_hazard <= 0 or not _eligible(world, family, scope, types or {event_type}):
        return None
    with cs.stream(world, f"hazard:{family}:{scope}") as rng:
        chance = probability(annual_hazard, days)
        draw = rng.random()
        if draw >= chance:
            return None
        severity = _clip(.1 + .55 * severity_factor + .35 * rng.random(), .05, 1)
    return create_event(world, event_type, region=region, countries=list(countries), causes=causes,
        actual_facts=actual_facts or {}, severity=severity, parent_event=parent_event,
        triggering_conditions={"annual_hazard": annual_hazard, "elapsed_days": days,
            "occurrence_probability": chance, "draw": draw, "model": "continuous state-grounded hazard"},
        risk_family_key=f"{family}:{scope}", **extra)


def detect(world, create_event, elapsed_days=7):
    if type(elapsed_days) is not int or elapsed_days <= 0 or not callable(create_event):
        raise ValueError("Risk detection requires positive elapsed days and an event factory")
    state = cs._state(world)
    created = []
    def emit(*args, **kwargs):
        event = _sample(world, create_event, *args, days=elapsed_days, **kwargs)
        if event is not None:
            created.append(event)
        return event
    for region, config in sorted(state["regions"].items()):
        country = world.regions[region].owner
        vulnerability = _clip(_number(config.get("vulnerability"), .5))
        for kind, annual_rate in sorted(config.get("hazard_rates", {}).items()):
            emit(kind, "natural:" + kind, region, _number(annual_rate), region=region, countries=[country],
                causes=[{"factor": "regional_hazard_profile", "kind": kind, "annual_rate": annual_rate}],
                severity_factor=vulnerability, actual_facts={"hazard_family": kind, "environmental_origin": True})
        harvest_rate = _number(config.get("bad_harvest_rate"))
        emit("bad_harvest", "harvest", region, harvest_rate, region=region, countries=[country],
            causes=[{"factor": "configured_agricultural_weather_risk", "annual_rate": harvest_rate}],
            severity_factor=vulnerability, actual_facts={"agricultural_shock": True})
        exposure = cs.pressure_value(world, "epidemic", country=country, region=region) / 100
        introduction = _number(config.get("epidemic_introduction_rate")) + .5 * exposure ** 2
        emit("epidemic_introduction", "epidemic", region, introduction, region=region, countries=[country],
            causes=[{"factor": "configured_introduction_risk", "annual_rate": config.get("epidemic_introduction_rate", 0)},
                    {"factor": "connected_existing_epidemic", "exposure": exposure}],
            severity_factor=vulnerability, types={"epidemic", "epidemic_introduction"}, actual_facts={"introduction": True})
    for region in sorted(world.economy["regions"]):
        country = world.regions[region].owner
        food = food_state(world, region)
        food_gap = food["food_gap"] if food["settled"] else 0
        parent = _parent(world, {"bad_harvest", "drought", "flood", "blockade"}, region=region)
        if food_gap > 0:
            shortage = emit("food_shortage", "food_shortage", region, 1.2 * food_gap ** 2,
                region=region, countries=[country], parent_event=parent,
                causes=[{"factor": "uncovered_food_supply_failure", **food}], severity_factor=food_gap,
                actual_facts={"food_security": food["food_security"], "actual_reserve_months": food["actual_reserve_months"]})
            if shortage is not None:
                parent = shortage["event_id"]
            else:
                parent = _parent(world, {"food_shortage"}, region=region) or parent
            # Supply failure is a prerequisite; a weather event or reserve label
            # alone never turns into famine. Severe gaps accelerate, not guarantee.
            if parent and food["food_security"] < .7:
                emit("famine", "famine", region, .9 * food_gap ** 3 * (1 - .5 * food["relief_capacity"]),
                    region=region, countries=[country], parent_event=parent, severity_factor=food_gap,
                    causes=[{"factor": "sustained_food_supply_failure", **food}], actual_facts={"food_gap": food_gap})
        civil = cs.pressure_value(world, "civil_unrest", country=country, region=region) / 100
        labour = cs.pressure_value(world, "labour_unrest", country=country, region=region) / 100
        if civil > 0 or labour > 0:
            kind = "strike" if labour > civil * 1.2 else "protest" if civil > .5 else "petition"
            pressure = max(civil, labour)
            emit(kind, "social", region, 1.3 * pressure ** 3, region=region, countries=[country],
                causes=state["pressures"].get(cs.pressure_key("civil_unrest", country, region), {}).get("causes", []),
                severity_factor=pressure, parent_event=_parent(world, FOOD | FINANCE, region=region), types=SOCIAL,
                actual_facts={"civil_unrest_pressure": civil * 100, "labour_pressure": labour * 100})
        for category, kind, rate in (("separatism", "separatist_agitation", .6), ("revolution", "revolutionary_agitation", .35),
            ("coup", "coup_warning", .4), ("military_insubordination", "military_insubordination", .45)):
            pressure = cs.pressure_value(world, category, country=country, region=region) / 100
            emit(kind, category, region, rate * pressure ** 4, region=region, countries=[country],
                causes=state["pressures"].get(cs.pressure_key(category, country, region), {}).get("causes", []),
                severity_factor=pressure, actual_facts={"warning_only": True, "structural_pressure": pressure * 100})
    for country, e in sorted(world.economy["countries"].items()):
        regions = sorted(r for r in world.economy["regions"] if world.regions[r].owner == country)
        if not regions:
            continue
        credit = e["credit"]
        pressure = cs.pressure_value(world, "fiscal_crisis", country=country) / 100
        has_arrears = _number(credit.get("arrears")) > 0
        default = credit.get("distress_state") in ("default", "restructuring")
        distress = credit.get("distress_state") not in ("normal", None)
        if has_arrears or default or distress:
            kind = "payment_failure" if has_arrears or default else "fiscal_stress"
            emit(kind, "finance", country, 2 * max(pressure, .3 if default else .1) ** 2,
                region=regions[0], countries=[country], types=FINANCE, severity_factor=pressure,
                causes=[{"factor": "actual_government_credit", "arrears": credit.get("arrears", 0),
                    "distress_state": credit.get("distress_state"), "debt_service_ratio": credit.get("debt_service_ratio", 0),
                    "reserves": world.countries[country].treasury}], actual_facts={"arrears": credit.get("arrears", 0),
                        "default": default, "creditworthiness": credit.get("creditworthiness", 75)})
        recession = cs.pressure_value(world, "recession", country=country) / 100
        if e.get("real_growth_estimate", 0) < 0 and world.economy.get("settlements", 0) > 0:
            emit("recession", "recession", country, .8 * recession ** 3, region=regions[0], countries=[country],
                severity_factor=recession, causes=[{"factor": "observed_negative_real_growth", "value": e["real_growth_estimate"]}],
                actual_facts={"real_growth_estimate": e["real_growth_estimate"]})
    for border_id, border in sorted(state["borders"].items()):
        countries, regions = border["countries"], border["regions"]
        if any(set((relation.get("a"), relation.get("b"))) == set(countries) and relation.get("status") == "war"
               for relation in world.relations.values()):
            # An existing war belongs to the Military/Event lifecycle, not a new
            # series of peacetime patrol incidents after every cooldown expires.
            continue
        pressure = cs.pressure_value(world, "border_tension", pair=countries) / 100
        mob = max(_number(world.economy["regions"][r]["labour_state"].get("mobilized")) /
                  max(1, world.economy["regions"][r]["labour_state"]["labour_force"]) for r in regions)
        local_fear = 0.0
        for actor, person in world.npc_state["persons"].items():
            if person.get("region") not in regions or "local_action" not in person.get("authority", []):
                continue
            for report in world.npc_state.get("reports", {}).get(actor, {}).values():
                data = report.get("data", {})
                if isinstance(data, dict):
                    age = max(0, (canonical_date(world.date) - canonical_date(report.get("as_of", world.date))).days)
                    attention = _clip(_number(report.get("confidence"), .8)) * _clip(_number(report.get("reliability"), .8)) / (1 + age / 365)
                    # Accuracy is audit truth unavailable to the recipient. A
                    # confident but false report can still influence a patrol.
                    local_fear = max(local_fear, _clip(_number(data.get("threat", data.get("enemy_aggression", 0)))) * attention)
        rate = .6 * pressure ** 3 * (1 + 3 * mob + local_fear)
        # A geographic dispute can start a local incident. It cannot bypass the
        # later reports, decisions, delay and escalation chain into state of war.
        emit("border_incident", "international", border_id, rate, region=regions[0], countries=countries,
            severity_factor=pressure, types=INTERNATIONAL,
            causes=[{"factor": "border_tension", "value": pressure * 100},
                    {"factor": "actual_border_mobilization", "value": mob},
                    {"factor": "local_observed_threat", "value": local_fear}],
            actual_facts={"border_id": border_id, "military_outcomes_unresolved": True,
                "mobilization_share": mob, "local_threat_reports": local_fear}, border_id=border_id)
    return created
