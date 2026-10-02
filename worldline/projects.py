"""Common persisted project pipeline. No kind has a separate countdown engine."""
import math
import copy
from datetime import timedelta
from .models import canonical_date

VERSION = "1"
INFRA = ("roads", "railways", "ports", "electricity", "communications", "sanitation")
STATUSES = ("planned", "funded", "under_construction", "in_progress", "commissioning",
            "training", "completed", "cancelled", "disrupted")

def plan_military(world, project_id, country, region, stage, duration_days, cost, requirements=None, metadata=None):
    """Use the same funding/progress/disruption machinery for institutional stages."""
    if getattr(world, 'military_state', None) is None or country not in world.countries or region not in world.regions or world.regions[region].owner != country:
        raise ValueError('Military project requires an enabled owned economy')
    if not isinstance(project_id, str) or not project_id or project_id in world.projects:
        raise ValueError('Unique project ID required')
    if type(duration_days) is not int or duration_days <= 0 or type(cost) not in (int, float) or not math.isfinite(cost) or cost <= 0:
        raise ValueError('Positive military project duration/cost required')
    p = {'id': project_id, 'country': country, 'region': region, 'kind': 'military', 'target': stage,
        'amount': cost, 'source': 'government', 'start_date': world.date,
        'expected_completion_date': (canonical_date(world.date) + timedelta(days=duration_days)).isoformat(),
        'cost': math.ceil(cost), 'status': 'planned', 'progress': 0.,
        'stages': [{'status': 'training' if stage == 'training' else 'in_progress', 'days': duration_days}],
        'stage_index': 0, 'stage_days': 0, 'elapsed_days': 0,
        'requirements': copy.deepcopy(requirements or {'resources': {}, 'minimum_labour': 0, 'economy_enabled': True}),
        'effects': [{'type': 'military', 'target': stage, 'amount': cost}],
        'metadata': copy.deepcopy(metadata or {}), 'funded_date': None, 'completed_date': None,
        'spent': 0, 'disruption': None}
    world.projects[project_id] = p
    return p


def plan(world, project_id, country, region, kind, target, amount, source="government"):
    if project_id in world.projects or not isinstance(project_id, str) or not project_id:
        raise ValueError("Project ID must be unique")
    if country not in world.countries or region not in world.regions or world.regions[region].owner != country:
        raise ValueError("Project requires an owned region")
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
        raise ValueError("Project amount must be positive")
    if source not in ("government", "private", "foreign"):
        raise ValueError("Unknown investment source")
    from .calibration import SECTORS
    if kind == "infrastructure" and target in INFRA:
        stages, cost = [("under_construction", 365), ("commissioning", 30)], max(1, math.ceil(amount * 100 - 1e-9))
        effects = [{"type": "infrastructure", "target": target, "amount": amount}]
    elif kind == "factory" and target in SECTORS:
        stages, cost = [("under_construction", 270), ("commissioning", 90)], math.ceil(amount)
        effects = [{"type": "capital", "target": target, "amount": amount, "source": source}]
    elif kind == "education" and target == "human_capital":
        stages, cost = [("in_progress", 365), ("training", 730)], math.ceil(amount)
        effects = [{"type": "education", "amount": amount}]
    elif kind == "technology" and target in SECTORS:
        stages, cost = [("in_progress", 90), ("under_construction", 90), ("training", 365)], math.ceil(amount * 1000)
        effects = [{"type": "technology", "target": target, "amount": amount}]
    else:
        raise ValueError("Unknown project template/target")
    duration = sum(days for _, days in stages)
    project = {"id": project_id, "country": country, "region": region, "kind": kind, "target": target,
        "amount": amount, "source": source, "start_date": world.date,
        "expected_completion_date": (canonical_date(world.date) + timedelta(days=duration)).isoformat(),
        "cost": cost, "status": "planned", "progress": 0.0, "stages": [
            {"status": status, "days": days} for status, days in stages], "stage_index": 0,
        "stage_days": 0, "elapsed_days": 0, "requirements": {"resources": {}, "minimum_labour": 0},
        "effects": effects, "funded_date": None, "completed_date": None, "spent": 0, "disruption": None}
    project["requirements"]["economy_enabled"] = kind != "infrastructure"
    if world.economy:
        project["requirements"] = {"resources": {"raw_materials": cost * .02}, "minimum_labour": cost * .001, "economy_enabled": True}
    world.projects[project_id] = project
    return project


def fund(world, project, prepaid=False, *, allow_emergency=False, instruments=None):
    if project["status"] != "planned":
        raise ValueError("Only planned projects can be funded")
    country = world.countries[project["country"]]
    stabilized = world.economy and world.economy.get("stabilization_version") == 1
    if not prepaid and not stabilized and country.treasury < project["cost"]:
        raise ValueError("Insufficient treasury")
    if world.economy:
        r = world.economy["regions"][project["region"]]
        for resource, quantity in project["requirements"]["resources"].items():
            if r["stocks"].get(resource, 0) < quantity:
                raise ValueError("Project resource shortage")
        if stabilized and not prepaid:
            from .credit import assess
            candidate = copy.deepcopy(world)
            reserved = candidate.economy["regions"][project["region"]]["stocks"]
            for resource, quantity in project["requirements"]["resources"].items():
                reserved[resource] -= quantity
            quote = assess(candidate, project["country"], project["cost"], allow_emergency=allow_emergency, instruments=instruments)
            if not quote["emergency_financial_feasibility"]["feasible"]:
                raise ValueError("Insufficient accessible financing after reserving project resources")
        for resource, quantity in project["requirements"]["resources"].items():
            r["stocks"][resource] -= quantity
            if resource == "raw_materials" and "closing_raw_materials" in r["inputs"]:
                r["inputs"]["project_raw_materials_used"] = r["inputs"].get("project_raw_materials_used", 0) + quantity
                r["inputs"]["closing_raw_materials"] -= quantity
        if stabilized and not prepaid:
            from .credit import finance_amount
            financing = finance_amount(world, project["country"], project["cost"], allow_emergency=allow_emergency, instruments=instruments)
            project["financing"] = financing
            project["payment_arrears_fraction"] = financing["arrears_financing"] / project["cost"]
    if not prepaid and not stabilized:
        country.treasury -= project["cost"]
    project.update(status="funded", funded_date=world.date, spent=project["cost"])
    duration = sum(stage["days"] for stage in project["stages"])
    project["expected_completion_date"] = (canonical_date(world.date) + timedelta(days=duration)).isoformat()


def remaining(project):
    return sum(s["days"] for s in project["stages"]) - project["elapsed_days"]


def apply_effects(world, p):
    for effect in p["effects"]:
        if effect['type'] == 'military':
            if p.get('metadata', {}).get('innovation_id'):
                from .military_innovation import on_project_completed
            else:
                from .military_bridge import on_project_completed
            on_project_completed(world, p)
            continue
        if effect["type"] == "infrastructure":
            infra = world.regions[p["region"]].infrastructure
            infra[effect["target"]] = infra.get(effect["target"], 0) + effect["amount"]
            if world.economy:
                sector_name = {"roads": "construction", "railways": "transport", "ports": "shipping",
                    "electricity": "energy", "communications": "transport", "sanitation": "services"}[effect["target"]]
                sector = world.economy["regions"][p["region"]]["sectors"][sector_name]
                capital = effect["amount"] * 50
                sector["capital_stock"] += capital
                sector["investment"][p["source"]] += capital
        else:
            if not world.economy:
                raise ValueError("This project completion requires an initialized economy")
            r = world.economy["regions"][p["region"]]
            if effect["type"] == "capital":
                sector = r["sectors"][effect["target"]]
                sector["capital_stock"] += effect["amount"]
                sector["investment"][effect["source"]] += effect["amount"]
            elif effect["type"] == "education":
                amount = min(.02, effect["amount"] / max(1, r["demography"]["total_population"]) * .5)
                for key, weight in (("literacy", 1), ("primary", .8), ("secondary", .5), ("technical", .3), ("university", .1)):
                    r["education"][key] = min(1, r["education"][key] + amount * weight)
            elif effect["type"] == "technology":
                technology = r["sectors"][effect["target"]]["technology"]
                technology.update(access=True, discovery=True, installed=True)
                technology["foreign_experts"] = min(1, technology["foreign_experts"] + min(.2, effect["amount"] * .02))
            else:
                raise ValueError("Unsupported project effect")


def tick(world, tick_event, rng):
    events = []
    labour_budgets = {key: r["labour"].get("construction", r["demography"]["productive_population"] * .05)
                      for key, r in world.economy["regions"].items()} if world.economy else {}
    for key, p in sorted(world.projects.items()):
        if p["status"] in ("planned", "completed", "cancelled"):
            continue
        if p["status"] == "disrupted" and p.get("shock_id") and world.economy:
            shock = world.economy["shocks"][p["shock_id"]]
            if shock["end_date"] <= world.date:
                p["status"], p["disruption"] = p["disruption"], None
                p.pop("shock_id", None)
        enough_labour = not p["requirements"].get("economy_enabled") or world.economy is not None
        if world.economy:
            region = world.economy["regions"][p["region"]]
            enough_labour = labour_budgets[p["region"]] >= p["requirements"]["minimum_labour"]
        if p['kind'] == 'military':
            if p.get('metadata', {}).get('innovation_id'):
                from .military_innovation import project_conditions
            else:
                from .military_bridge import project_conditions
            condition = project_conditions(world, p)
            enough_labour = enough_labour and condition['ready']
            p['military_conditions'] = condition
        fiscal_ready = True
        if world.economy and world.economy.get("stabilization_version") == 1 and p["source"] == "government":
            credit = world.economy["countries"][p["country"]]["credit"]
            # New policy shortfalls do not freeze a fully prepaid earlier contract.
            # Only the project's own deferred supplier payments slow its work.
            speed = 1 - p.get("payment_arrears_fraction", 0) if credit["arrears"] else 1.0
            p["fiscal_work_remainder"] = p.get("fiscal_work_remainder", 0.0) + speed
            fiscal_ready = p["fiscal_work_remainder"] >= 1
        if p["status"] == "disrupted" or not enough_labour or not fiscal_ready:
            p["condition_delay_days"] = p.get("condition_delay_days", 0) + 1
            p["expected_completion_date"] = (canonical_date(world.date) + timedelta(days=remaining(p))).isoformat()
            continue
        if world.economy:
            labour_budgets[p["region"]] -= p["requirements"]["minimum_labour"]
            if world.economy.get("stabilization_version") == 1 and p["source"] == "government":
                p["fiscal_work_remainder"] -= 1
        p["elapsed_days"] += 1
        p["stage_days"] += 1
        p["status"] = p["stages"][p["stage_index"]]["status"]
        p["progress"] = p["elapsed_days"] / sum(s["days"] for s in p["stages"])
        if p["stage_days"] >= p["stages"][p["stage_index"]]["days"]:
            p["stage_index"] += 1
            p["stage_days"] = 0
            if p["stage_index"] == len(p["stages"]):
                apply_effects(world, p)
                p.update(status="completed", completed_date=world.date, progress=1)
                events.append({"project": key, "status": "completed", "effects": p["effects"]})
            else:
                p["status"] = p["stages"][p["stage_index"]]["status"]
                events.append({"project": key, "status": p["status"]})
    return events


def validate(world, old=None):
    issues = []
    from .calibration import SECTORS
    for key, p in world.projects.items():
        try:
            duration = sum(s["days"] for s in p["stages"])
            if (p["id"] != key or p["country"] not in world.countries or p["region"] not in world.regions
                or p["status"] not in STATUSES or type(p["cost"]) is not int or p["cost"] <= 0
                or not 0 <= p["progress"] <= 1 or not 0 <= p["elapsed_days"] <= duration
                or duration <= 0 or any(type(s["days"]) is not int or s["days"] <= 0 for s in p["stages"])
                or canonical_date(p["expected_completion_date"]) < canonical_date(p["start_date"])):
                issues.append(f"project: invalid {key}")
            minimum_duration = {"infrastructure": 395, "factory": 360, "education": 1095, "technology": 545, "military": 1}
            if p['kind'] == 'military' and getattr(world, 'military_state', None) is None:
                issues.append(f'project: military project without the military system {key}')
            if p["kind"] not in minimum_duration or duration < minimum_duration.get(p["kind"], 1):
                issues.append(f"project: invalid minimum duration {key}")
            if p["source"] not in ("government", "private", "foreign") or not 0 <= p["stage_index"] <= len(p["stages"]):
                issues.append(f"project: invalid source/stage {key}")
            if not math.isclose(p["progress"], p["elapsed_days"] / duration):
                issues.append(f"project: progress mismatch {key}")
            if p["stage_index"] < len(p["stages"]):
                expected_elapsed = sum(s["days"] for s in p["stages"][:p["stage_index"]]) + p["stage_days"]
                if expected_elapsed != p["elapsed_days"] or not 0 <= p["stage_days"] < p["stages"][p["stage_index"]]["days"]:
                    issues.append(f"project: stage progress mismatch {key}")
            if any(s["status"] not in STATUSES for s in p["stages"]):
                issues.append(f"project: invalid stage status {key}")
            if p["funded_date"] is not None:
                funded = canonical_date(p["funded_date"])
                if funded < canonical_date(p["start_date"]) or (canonical_date(world.date) - funded).days < p["elapsed_days"]:
                    issues.append(f"project: inconsistent funding/progress dates {key}")
            for effect in p["effects"]:
                if effect["type"] not in ("infrastructure", "capital", "education", "technology", "military") or not math.isfinite(effect["amount"]) or effect["amount"] <= 0:
                    issues.append(f"project: invalid effect {key}")
                if effect["type"] == "infrastructure" and effect["target"] not in INFRA:
                    issues.append(f"project: invalid infrastructure {key}")
                if effect["type"] in ("capital", "technology") and effect["target"] not in SECTORS:
                    issues.append(f"project: unknown sector effect {key}")
                expected_type = {"infrastructure": "infrastructure", "factory": "capital", "education": "education", "technology": "technology", "military": "military"}.get(p["kind"])
                if effect["type"] != expected_type or effect["amount"] != p["amount"]:
                    issues.append(f"project: effect differs from funded template {key}")
                if effect["type"] != "education" and effect["target"] != p["target"]:
                    issues.append(f"project: inconsistent effect target {key}")
            scale = 100 if p["kind"] == "infrastructure" else 1000 if p["kind"] == "technology" else 1
            if len(p["effects"]) != 1 or p["cost"] != max(1, math.ceil(p["amount"] * scale - 1e-9)):
                issues.append(f"project: inconsistent cost/effect template {key}")
            for quantity in p["requirements"]["resources"].values():
                if not math.isfinite(quantity) or quantity < 0: issues.append(f"project: invalid resources {key}")
            if p["status"] not in ("planned", "cancelled") and (p["funded_date"] is None or p["spent"] != p["cost"]):
                # Disrupted planning is valid but must not pretend to have been funded.
                if p["status"] != "disrupted" or p["disruption"] != "planned":
                    issues.append(f"project: missing funding {key}")
            if p["status"] == "completed":
                if p["elapsed_days"] != duration or p["spent"] != p["cost"] or p["progress"] != 1:
                    issues.append(f"project: premature completion {key}")
                if (canonical_date(p["completed_date"]) - canonical_date(p["funded_date"])).days < duration:
                    issues.append(f"project: missing construction/training period {key}")
            if old and key in old.projects:
                before = old.projects[key]
                elapsed = (canonical_date(world.date) - canonical_date(old.date)).days
                if p["elapsed_days"] > before["elapsed_days"] + elapsed:
                    issues.append(f"project: accelerated progress {key}")
                if before["status"] in ("completed", "cancelled") and p != before:
                    issues.append(f"project: changed terminal record {key}")
        except (KeyError, TypeError, ValueError):
            issues.append(f"project: malformed {key}")
    return issues
