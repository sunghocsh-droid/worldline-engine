"""Persisted, local and conserved labour allocation for stabilization revision 1.

Sector jobs are mutually exclusive. Skill and professional counts are subsets,
never additional workers. Job transitions remove people from their old jobs;
they produce nothing while moving/training. No national pool can teleport staff.
"""
import copy
import math
from datetime import timedelta

from .calibration import SECTORS
from .models import canonical_date

VERSION = 1
BASE_SHARES = dict(zip(SECTORS, (.45, .07, .10, .06, .04, .03, .05, .05, .05, .02, .08)))
SKILL_NEED = dict(zip(SECTORS, (.01, .03, .06, .10, .18, .15, .10, .05, .05, .08, .04)))
PROFESSIONAL_NEED = dict(zip(SECTORS, (0, .005, .005, .02, .04, .03, .02, .005, .01, .01, .015)))
TOLERANCE = 1e-6


def enabled(world):
    return bool(world.economy and world.economy.get("stabilization_version") == VERSION)


def owned_regions(world, country):
    return [key for key in sorted(world.economy["regions"]) if world.regions[key].owner == country]


def _ratio(value):
    return value / (value + 50) if value > 0 else 0.0


def _army_counts(world, country):
    """A deployed army remains charged to its recruiting/home population."""
    homes = world.economy.setdefault("labour_army_origins", {})
    regions = owned_regions(world, country)
    counts = dict.fromkeys(regions, 0.0)
    for key, army in sorted(world.armies.items()):
        if army.owner != country:
            continue
        if not regions:
            raise ValueError("Army has no modeled home population")
        if key not in homes or homes[key] not in regions:
            homes[key] = army.region if army.region in regions else regions[0]
        counts[homes[key]] += army.personnel
    return counts


def _base_weights(r):
    agriculture = max(.2, .65 - .4 * r["demography"]["urbanization"] - .1 * r["education"]["literacy"])
    rest = sum(v for s, v in BASE_SHARES.items() if s != "agriculture")
    return {s: agriculture if s == "agriculture" else BASE_SHARES[s] / rest * (1 - agriculture) for s in SECTORS}


def initialize(world):
    """Activate from existing assets and workers without duplicating existing jobs."""
    if not enabled(world):
        raise ValueError("Labour allocation requires stabilization_version 1")
    for country in sorted(world.economy["countries"]):
        standing = _army_counts(world, country)
        for region in owned_regions(world, country):
            r = world.economy["regions"][region]
            if "labour_state" in r:
                continue
            force = float(r["demography"]["productive_population"])
            if standing[region] > force:
                raise ValueError("Military personnel exceed local working-age population")
            previous = r["labour"]
            support = min(force - standing[region], max(0, previous.get("military_support", 0)))
            civilian = force - standing[region] - support
            weights = _base_weights(r)
            jobs = {s: max(0, previous.get(s, 0)) for s in SECTORS}
            total = math.fsum(jobs.values())
            if total:
                jobs = {s: value * min(1, civilian / total) for s, value in jobs.items()}
            else:
                jobs = {s: civilian * weights[s] for s in SECTORS}
            unemployed = max(0, civilian - math.fsum(jobs.values()))
            skilled = min(.75, max(0, r["skilled_share"]))
            professional = min(skilled, max(0, r["professional_share"]))
            r["labour_state"] = {
                "version": VERSION, "working_age_population": force, "labour_force": force,
                "jobs": jobs, "skilled_jobs": {s: jobs[s] * skilled for s in SECTORS},
                "professional_jobs": {s: jobs[s] * professional for s in SECTORS},
                "unemployed": unemployed, "unemployed_skilled": unemployed * skilled,
                "unemployed_professionals": unemployed * professional, "pipeline": [],
                "standing_army": standing[region], "mobilized": 0.0, "military_support": support,
                "military_skilled": (standing[region] + support) * skilled,
                "military_professionals": (standing[region] + support) * professional,
                "eligible_military_share": .5, "next_transition_id": 1,
                "initialization_date": world.date, "last_allocation_date": world.date,
                "pending_moves": [], "last_report": {"moves": [], "returns": [], "training": [],
                    "military_burden": [], "bottlenecks": [], "mobility": 0.0}}
            _write_region(world, region)
        aggregate(world, country)
    return world.economy


def _civilian_total(state):
    return (math.fsum(state["jobs"].values()) + state["unemployed"] +
            math.fsum(p["workers"] for p in state["pipeline"]))


def _military_total(state):
    return state["standing_army"] + state["mobilized"] + state["military_support"]


def _cohort(state, source, workers):
    """Remove a proportionate typed cohort from exactly one civilian bucket."""
    if source == "unemployed":
        count, skill, professionals = (state["unemployed"], state["unemployed_skilled"], state["unemployed_professionals"])
        share = min(1, workers / max(count, 1e-12))
        take = (count * share, skill * share, professionals * share)
        state["unemployed"] -= take[0]
        state["unemployed_skilled"] -= take[1]
        state["unemployed_professionals"] -= take[2]
    else:
        count = state["jobs"][source]
        share = min(1, workers / max(count, 1e-12))
        take = (count * share, state["skilled_jobs"][source] * share, state["professional_jobs"][source] * share)
        state["jobs"][source] -= take[0]
        state["skilled_jobs"][source] -= take[1]
        state["professional_jobs"][source] -= take[2]
    return take


def _withdraw_civilians(state, workers, reason):
    """Recruit locally; zero duplication, including workers already in transit."""
    if workers > _civilian_total(state) + TOLERANCE:
        raise ValueError("Insufficient local civilian manpower")
    receipts = []
    count, skills, professionals = _cohort(state, "unemployed", workers)
    if count > TOLERANCE:
        receipts.append({"source": "unemployed", "workers": count, "reason": reason})
    remaining = max(0, workers - count)
    total_jobs = math.fsum(state["jobs"].values())
    job_amount = min(remaining, total_jobs)
    if total_jobs:
        for sector in SECTORS:
            take, skill, professional = _cohort(state, sector, job_amount * state["jobs"][sector] / total_jobs)
            count += take
            skills += skill
            professionals += professional
            if take > TOLERANCE:
                receipts.append({"source": sector, "workers": take, "reason": reason})
    remaining = max(0, workers - count)
    if remaining > TOLERANCE:
        pipeline_total = math.fsum(p["workers"] for p in state["pipeline"])
        for pipeline in state["pipeline"]:
            share = min(1, remaining / max(pipeline_total, 1e-12))
            take, skill, professional = (pipeline[k] * share for k in ("workers", "skilled", "professionals"))
            pipeline["workers"] -= take
            pipeline["skilled"] -= skill
            pipeline["professionals"] -= professional
            count += take
            skills += skill
            professionals += professional
            if take > TOLERANCE:
                receipts.append({"source": "transit_training", "workers": take, "reason": reason})
        state["pipeline"] = [p for p in state["pipeline"] if p["workers"] > TOLERANCE]
    return count, skills, professionals, receipts


def _transition(world, state, source, target, cohort, days, reason):
    workers, skilled, professionals = cohort
    if workers <= TOLERANCE:
        return
    item = {"id": state["next_transition_id"], "source": source, "target": target,
        "workers": workers, "skilled": skilled, "professionals": professionals,
        "elapsed_days": 0, "remaining_days": days, "total_days": days,
        "started_date": world.date, "expected_completion_date":
            (canonical_date(world.date) + timedelta(days=days)).isoformat(),
        "stage": "demobilization" if reason == "demobilization" else
            "training" if target in ("heavy_industry", "machinery", "chemicals", "energy") else "relocation",
        "reason": reason}
    state["next_transition_id"] += 1
    state["pipeline"].append(item)


def _military_change(world, region, field, target, reason):
    state = world.economy["regions"][region]["labour_state"]
    delta = target - state[field]
    if delta > TOLERANCE:
        count, skilled, professionals, receipts = _withdraw_civilians(state, delta, reason)
        state["military_skilled"] += skilled
        state["military_professionals"] += professionals
        state["pending_moves"].extend({**row, "target": field} for row in receipts)
    elif delta < -TOLERANCE:
        military = _military_total(state)
        workers = -delta
        skilled = state["military_skilled"] * workers / max(military, 1e-12)
        professionals = state["military_professionals"] * workers / max(military, 1e-12)
        state["military_skilled"] -= skilled
        state["military_professionals"] -= professionals
        _transition(world, state, field, "unemployed", (workers, skilled, professionals), 120, "demobilization")
        state["pending_moves"].append({"source": field, "target": "demobilization", "workers": workers,
                                        "reason": "demobilization"})
    state[field] = target


def mobilize(world, country, region, amount):
    """Set extra reserve manpower; an order is limited by actual local people.

    Standing Army personnel are charged separately. Demobilized people return
    through a 120-day pipeline, then seek local jobs through ordinary mobility.
    """
    if not enabled(world) or country not in world.economy["countries"] or region not in owned_regions(world, country):
        raise ValueError("Mobilization requires an owned stabilized region")
    if type(amount) is not int or amount < 0:
        raise ValueError("Mobilized manpower must be a nonnegative integer")
    state = world.economy["regions"][region]["labour_state"]
    eligible = state["working_age_population"] * state["eligible_military_share"]
    if amount + state["standing_army"] > eligible + TOLERANCE:
        raise ValueError("Mobilization exceeds eligible local age/sex cohort")
    _military_change(world, region, "mobilized", float(amount), "military_mobilization")
    _write_region(world, region)
    aggregate(world, country)
    return copy.deepcopy(state["pending_moves"])


def _reconcile_population(state, new_force):
    difference = new_force - state["labour_force"]
    if difference >= 0:
        # New entrants/migrants have no automatically bestowed profession.
        state["unemployed"] += difference
    else:
        available = state["labour_force"] - state["standing_army"]
        if new_force < state["standing_army"] - TOLERANCE:
            raise ValueError("Standing army exceeds surviving local working-age population")
        factor = max(0, (available + difference) / max(available, 1e-12))
        before_military = _military_total(state)
        for field in ("jobs", "skilled_jobs", "professional_jobs"):
            state[field] = {key: value * factor for key, value in state[field].items()}
        for field in ("unemployed", "unemployed_skilled", "unemployed_professionals", "mobilized", "military_support"):
            state[field] *= factor
        after_military = _military_total(state)
        if before_military:
            state["military_skilled"] *= after_military / before_military
            state["military_professionals"] *= after_military / before_military
        for pipeline in state["pipeline"]:
            for field in ("workers", "skilled", "professionals"):
                pipeline[field] *= factor
    state["working_age_population"] = state["labour_force"] = float(new_force)


def _advance_pipeline(world, state, days, report):
    remaining = []
    for item in state["pipeline"]:
        # An order late in the accounting month has existed for only a few
        # days. Never credit the full settlement period to that new cohort.
        actually_elapsed = (canonical_date(world.date) - canonical_date(item["started_date"])).days
        elapsed = min(days, item["remaining_days"], max(0, actually_elapsed - item["elapsed_days"]))
        item["elapsed_days"] += elapsed
        item["remaining_days"] -= elapsed
        if item["remaining_days"]:
            remaining.append(item)
            continue
        target = item["target"]
        if target == "unemployed":
            state["unemployed"] += item["workers"]
            state["unemployed_skilled"] += item["skilled"]
            state["unemployed_professionals"] += item["professionals"]
        else:
            state["jobs"][target] += item["workers"]
            state["skilled_jobs"][target] += item["skilled"]
            state["professional_jobs"][target] += item["professionals"]
        report["returns"].append({"source": item["source"], "target": target,
            "workers": item["workers"], "skilled": item["skilled"], "reason": item["reason"]})
    state["pipeline"] = remaining


def _train_in_place(r, days, report):
    """Education graduates enter actual skill subsets gradually, never overnight."""
    state, education = r["labour_state"], r["education"]
    civilian = _civilian_total(state)
    current = (math.fsum(state["skilled_jobs"].values()) + state["unemployed_skilled"] +
               math.fsum(p["skilled"] for p in state["pipeline"]))
    budget = min(max(0, civilian * r["skilled_share"] - current),
                 civilian * (.005 + .03 * education["technical"] + .005 * education["literacy"]) * days / 365)
    # Training opportunities follow occupational skill needs, but no person is
    # transferred here; their own local job is upgraded after gradual training.
    needs = {s: max(0, min(state["jobs"][s], state["jobs"][s] * max(SKILL_NEED[s], r["skilled_share"])) - state["skilled_jobs"][s]) for s in SECTORS}
    total = math.fsum(needs.values())
    for sector in SECTORS:
        gain = min(needs[sector], budget * needs[sector] / max(total, 1e-12))
        state["skilled_jobs"][sector] += gain
        if gain > TOLERANCE:
            report["training"].append({"sector": sector, "new_skilled": gain})
    professional_total = (math.fsum(state["professional_jobs"].values()) + state["unemployed_professionals"] +
                          math.fsum(p["professionals"] for p in state["pipeline"]))
    professional_budget = min(max(0, civilian * min(r["professional_share"], r["skilled_share"]) - professional_total),
        civilian * (.001 + .008 * education["university"]) * days / 365)
    professional_needs = {s: max(0, min(state["skilled_jobs"][s], state["jobs"][s] * max(PROFESSIONAL_NEED[s], r["professional_share"])) - state["professional_jobs"][s]) for s in SECTORS}
    total = math.fsum(professional_needs.values())
    for sector in SECTORS:
        gain = min(professional_needs[sector], professional_budget * professional_needs[sector] / max(total, 1e-12))
        state["professional_jobs"][sector] += gain


def _desired_jobs(world, country, region):
    r = world.economy["regions"][region]
    state = r["labour_state"]
    civilian = _civilian_total(state)
    profile = world.economy["countries"][country]["calibration"]
    capacities = {s: max(0, r["sectors"][s]["capital_stock"] * profile["capital_efficiency"] / profile["productivity"]) for s in SECTORS}
    # Physical facilities create demand. Subsistence agriculture has a social
    # floor; education/urbanization lower it over decades rather than one month.
    agriculture_floor = max(.18, .55 - .35 * r["demography"]["urbanization"] - .10 * r["education"]["literacy"])
    capacities["agriculture"] = max(capacities["agriculture"], civilian * agriculture_floor)
    total = math.fsum(capacities.values())
    if total <= TOLERANCE:
        return dict.fromkeys(SECTORS, 0.0)
    # Capacity above the workforce changes job shares, not the number of people.
    employed_target = min(civilian, total)
    return {s: capacities[s] / total * employed_target for s in SECTORS}


def allocate(world, country, days):
    """One elapsed-period step; old economic skill/urban targets are inputs."""
    if not enabled(world):
        return None
    if type(days) is not int or days <= 0:
        raise ValueError("Labour allocation requires positive elapsed days")
    if any("labour_state" not in world.economy["regions"][r] for r in owned_regions(world, country)):
        initialize(world)
    standing = _army_counts(world, country)
    for region in owned_regions(world, country):
        r = world.economy["regions"][region]
        state = r["labour_state"]
        report = {"moves": state["pending_moves"], "returns": [], "training": [],
                  "military_burden": [], "bottlenecks": [], "mobility": 0.0}
        state["pending_moves"] = []
        _military_change(world, region, "standing_army", standing[region], "standing_army_recruitment")
        _reconcile_population(state, r["demography"]["productive_population"])
        _advance_pipeline(world, state, days, report)
        policy = world.economy["countries"][country]["policy"]
        force = state["labour_force"]
        burden = policy["military"] / max(1, force * .05)
        support = min(max(0, force - state["standing_army"] - state["mobilized"]),
                      force * min(.2, burden * .04))
        _military_change(world, region, "military_support", support, "military_supply_support")
        report["moves"].extend(state["pending_moves"])
        state["pending_moves"] = []
        _train_in_place(r, days, report)
        infra = world.regions[region].infrastructure
        logistics = math.fsum(_ratio(infra.get(key, 0)) for key in ("roads", "railways", "ports", "communications")) / 4
        education, urban = r["education"], r["demography"]["urbanization"]
        disruption = r.get("shock_factors", {}).get("disruption", 1)
        stability = max(0, min(1, world.countries[country].stability / 100))
        mobility = (.025 + .06 * urban + .08 * logistics + .05 * education["literacy"] + .06 * education["technical"]) * disruption * (.4 + .6 * stability)
        budget = force * min(.20, mobility * days / 365)
        report["mobility"] = mobility
        desired = _desired_jobs(world, country, region)
        incoming = {s: math.fsum(p["workers"] for p in state["pipeline"] if p["target"] == s) for s in SECTORS}
        gaps = {s: max(0, desired[s] - state["jobs"][s] - incoming[s]) for s in SECTORS}
        # Stable sorted demand, never dictionary insertion or random priority.
        targets = sorted(SECTORS, key=lambda s: (-gaps[s], s))
        for target in targets:
            gap = gaps[target]
            if gap <= TOLERANCE or budget <= TOLERANCE:
                continue
            sources = ["unemployed"] + sorted(SECTORS, key=lambda s: (-(state["jobs"][s] - desired[s]), s))
            for source in sources:
                if source == target:
                    continue
                available = state["unemployed"] if source == "unemployed" else max(0, state["jobs"][source] - desired[source])
                count = min(available, gap, budget)
                if count <= TOLERANCE:
                    continue
                cohort = _cohort(state, source, count)
                duration = max(90, min(365, round(180 / (.5 + urban + logistics))))
                if target in ("heavy_industry", "machinery", "chemicals", "energy"):
                    duration += 180  # equipment/safety training, without granting instant skills
                _transition(world, state, source, target, cohort, duration, "sector_reallocation")
                report["moves"].append({"source": source, "target": target, "workers": cohort[0],
                    "skilled": cohort[1], "remaining_days": duration, "reason": "local_industry_demand"})
                budget -= cohort[0]
                gap -= cohort[0]
        for sector in SECTORS:
            target = desired[sector]
            skill_need = state["jobs"][sector] * SKILL_NEED[sector]
            if target > state["jobs"][sector] + 1:
                report["bottlenecks"].append({"sector": sector, "kind": "labour_shortage",
                    "available": state["jobs"][sector], "desired": target,
                    "in_pipeline": incoming[sector], "reason": "local_workers_and_reallocation_time"})
            if skill_need > state["skilled_jobs"][sector] + 1:
                report["bottlenecks"].append({"sector": sector, "kind": "skilled_labour_shortage",
                    "available": state["skilled_jobs"][sector], "required": skill_need,
                    "reason": "education_and_training_take_time"})
        report["military_burden"] = [row for row in report["moves"] if row.get("target") in ("standing_army", "mobilized", "military_support")]
        state["last_report"] = report
        state["last_allocation_date"] = world.date
        _write_region(world, region)
    return aggregate(world, country)


def _write_region(world, region):
    r = world.economy["regions"][region]
    state = r["labour_state"]
    military = _military_total(state)
    jobs = state["jobs"]
    skilled = math.fsum(state["skilled_jobs"].values()) + state["unemployed_skilled"]
    professionals = math.fsum(state["professional_jobs"].values()) + state["unemployed_professionals"]
    r["labour"] = {**jobs, "military": military, "military_service": state["standing_army"] + state["mobilized"],
        "military_support": state["military_support"], "mobilized": state["mobilized"],
        "skilled": skilled, "professionals": professionals,
        "working_age_population": state["working_age_population"], "labour_force": state["labour_force"],
        "employed": math.fsum(jobs.values()), "unemployed": state["unemployed"],
        "underemployed": math.fsum(max(0, jobs[s] - r["sectors"][s]["capital_stock"] *
            world.economy["countries"][world.regions[region].owner]["calibration"]["capital_efficiency"] /
            world.economy["countries"][world.regions[region].owner]["calibration"]["productivity"]) for s in SECTORS),
        "transit_training": math.fsum(p["workers"] for p in state["pipeline"]),
        "manufacturing": math.fsum(jobs[s] for s in ("light_industry", "heavy_industry", "machinery", "chemicals")),
        "transport_logistics": jobs["transport"] + jobs["shipping"]}
    for sector in SECTORS:
        r["sectors"][sector]["labour"] = jobs[sector]
        r["sectors"][sector]["skilled_labour"] = state["skilled_jobs"][sector]
        r["sectors"][sector]["professional_labour"] = state["professional_jobs"][sector]


def aggregate(world, country):
    totals = {}
    for region in owned_regions(world, country):
        for field, value in world.economy["regions"][region]["labour"].items():
            totals[field] = totals.get(field, 0.0) + value
    world.economy["countries"][country]["labour"] = totals
    return totals


def available(world, country, region=None):
    """Read-only local availability. Totals remain explicitly split by region."""
    regions = [region] if region in owned_regions(world, country) else owned_regions(world, country) if region is None else []
    if not regions:
        raise ValueError("Labour query requires an owned region")
    rows = {}
    for key in regions:
        state = world.economy["regions"][key]["labour_state"]
        rows[key] = {"immediately_unemployed": state["unemployed"],
            "immediately_unemployed_skilled": state["unemployed_skilled"],
            "immediately_unemployed_professionals": state["unemployed_professionals"],
            "employed_by_sector": copy.deepcopy(state["jobs"]),
            "skilled_by_sector": copy.deepcopy(state["skilled_jobs"]),
            "professionals_by_sector": copy.deepcopy(state["professional_jobs"]),
            "in_transit_training": math.fsum(p["workers"] for p in state["pipeline"]),
            "military_manpower": _military_total(state),
            "constraint": "workers_are_local; occupied_workers_require_delayed_reallocation"}
    return {"country": country, "regions": rows, "cross_region_instant_transfer": False}


def explain(world, country, region=None):
    keys = [region] if region in owned_regions(world, country) else owned_regions(world, country) if region is None else []
    if not keys:
        raise ValueError("Labour explanation requires an owned region")
    return {"country": country, "regions": {key: copy.deepcopy(world.economy["regions"][key]["labour_state"]["last_report"]) for key in keys},
            "availability": available(world, country, region)}


def validate(world, old=None):
    if not enabled(world):
        return []
    issues = []
    try:
        for region, r in world.economy["regions"].items():
            state = r["labour_state"]
            force = state["labour_force"]
            if state["version"] != VERSION:
                issues.append(f"labour: unsupported allocation version {region}")
            if force != state["working_age_population"] or force != r["demography"]["productive_population"]:
                issues.append(f"labour: working-age accounting mismatch {region}")
            if not math.isclose(_civilian_total(state) + _military_total(state), force, abs_tol=TOLERANCE):
                issues.append(f"labour: workers duplicated or lost {region}")
            if set(state["jobs"]) != set(SECTORS) or set(state["skilled_jobs"]) != set(SECTORS) or set(state["professional_jobs"]) != set(SECTORS):
                issues.append(f"labour: invalid sector allocation {region}")
            for sector in SECTORS:
                jobs, skilled, professionals = state["jobs"][sector], state["skilled_jobs"][sector], state["professional_jobs"][sector]
                if not all(type(v) in (int, float) and math.isfinite(v) and v >= -TOLERANCE for v in (jobs, skilled, professionals)) or not -TOLERANCE <= professionals <= skilled + TOLERANCE <= jobs + 2 * TOLERANCE:
                    issues.append(f"labour: invalid skill subsets {region}:{sector}")
                if not math.isclose(r["sectors"][sector]["labour"], jobs, abs_tol=TOLERANCE) or not math.isclose(r["sectors"][sector]["skilled_labour"], skilled, abs_tol=TOLERANCE):
                    issues.append(f"labour: sector mirror mismatch {region}:{sector}")
            for field in ("unemployed", "unemployed_skilled", "unemployed_professionals", "standing_army", "mobilized", "military_support", "military_skilled", "military_professionals"):
                value = state[field]
                if type(value) not in (int, float) or not math.isfinite(value) or value < -TOLERANCE:
                    issues.append(f"labour: invalid {region}.{field}")
            if not state["unemployed_professionals"] <= state["unemployed_skilled"] + TOLERANCE <= state["unemployed"] + 2 * TOLERANCE:
                issues.append(f"labour: unemployed subset mismatch {region}")
            if not state["military_professionals"] <= state["military_skilled"] + TOLERANCE <= _military_total(state) + 2 * TOLERANCE:
                issues.append(f"labour: military subset mismatch {region}")
            for item in state["pipeline"]:
                if item["source"] not in (*SECTORS, "unemployed", "standing_army", "mobilized", "military_support") or item["target"] not in (*SECTORS, "unemployed"):
                    issues.append(f"labour: unknown transition endpoint {region}")
                if not 0 <= item["professionals"] <= item["skilled"] + TOLERANCE <= item["workers"] + 2 * TOLERANCE:
                    issues.append(f"labour: transition subset mismatch {region}")
                if item["elapsed_days"] + item["remaining_days"] != item["total_days"] or item["total_days"] < 90 or item["remaining_days"] <= 0:
                    issues.append(f"labour: instant or malformed transition {region}")
                if canonical_date(item["expected_completion_date"]) <= canonical_date(item["started_date"]):
                    issues.append(f"labour: invalid transition date {region}")
                actual_days = (canonical_date(world.date) - canonical_date(item["started_date"])).days
                if item["elapsed_days"] > actual_days or actual_days < 0:
                    issues.append(f"labour: progress exceeds actual elapsed dates {region}")
                if canonical_date(item["expected_completion_date"]) != canonical_date(item["started_date"]) + timedelta(days=item["total_days"]):
                    issues.append(f"labour: completion date differs from transition duration {region}")
            for field in ("employed", "unemployed", "transit_training", "military"):
                expected = {"employed": math.fsum(state["jobs"].values()), "unemployed": state["unemployed"],
                    "transit_training": math.fsum(p["workers"] for p in state["pipeline"]), "military": _military_total(state)}[field]
                if not math.isclose(r["labour"][field], expected, abs_tol=TOLERANCE):
                    issues.append(f"labour: aggregate mirror mismatch {region}:{field}")
        for country in world.economy["countries"]:
            actual = sum(army.personnel for army in world.armies.values() if army.owner == country)
            modeled = math.fsum(world.economy["regions"][r]["labour_state"]["standing_army"] for r in owned_regions(world, country))
            if not math.isclose(actual, modeled, abs_tol=TOLERANCE):
                issues.append(f"labour: standing army is free or counted twice {country}")
        return issues
    except (KeyError, TypeError, ValueError, OverflowError):
        return issues + ["labour: malformed allocation state"]
