from .incremental_audit import items as audit_items, json_check as audit_json
"""Evidence-based military innovation using the existing funded project clock.

Ideas are not equipment, and a successful local trial is not national adoption.
This module does not add manpower, civilian skills, weapons or industrial output.
Institutional judgement accepts only an NPC's delivered-information projection.
"""
import copy
import math

from .models import canonical_date
from .military_state import state, emit, stream, DOCTRINE

VERSION = 1
STAGES = ("concept", "feasibility_study", "prototype", "trial", "evaluation",
          "doctrine_development", "training", "limited_adoption",
          "institutional_adoption", "diffusion")
MINIMUM_DAYS = dict(zip(STAGES[1:], (30, 90, 60, 30, 120, 180, 90, 365, 730)))
CAPABILITIES = ("metallurgy", "armor", "engine", "tracks", "transmission", "fuel",
                "precision", "communications", "literacy", "organization", "training")
SOURCES = ("domestic_research", "foreign_observation", "captured_equipment", "battlefield_experience",
           "institutional_reform", "player_proposal")
ADOPTION_LIMITS = {"limited_adoption": .25, "institutional_adoption": .65, "diffusion": 1.0}
EXAMPLES = {
    "staff_planning": {"capabilities": {"literacy": .15, "organization": .2}},
    "dispersed_infantry": {"capabilities": {"training": .25, "communications": .15}},
    "standardized_artillery": {"capabilities": {"metallurgy": .35, "precision": .4}},
    "combined_arms": {"capabilities": {"training": .4, "organization": .4, "communications": .3}},
    "mobile_armoured_force": {"capabilities": {"engine": .55, "metallurgy": .5,
        "armor": .5, "tracks": .5, "transmission": .5, "fuel": .4, "precision": .5}},
}


def _number(value, low=0, high=None):
    return type(value) in (int, float) and math.isfinite(value) and value >= low and (high is None or value <= high)


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def _requirements(value):
    value = copy.deepcopy(value or {})
    if not isinstance(value, dict) or set(value) - {"capabilities", "resources", "minimum_labour"}:
        raise ValueError("Unknown military innovation requirement")
    value.setdefault("capabilities", {})
    value.setdefault("resources", {})
    value.setdefault("minimum_labour", 0.0)
    if not isinstance(value["capabilities"], dict) or any(k not in CAPABILITIES or not _number(v, 0, 1)
            for k, v in value["capabilities"].items()):
        raise ValueError("Invalid technical capability requirement")
    if not isinstance(value["resources"], dict) or any(not _identity(k) or not _number(v)
            for k, v in value["resources"].items()) or not _number(value["minimum_labour"]):
        raise ValueError("Invalid innovation resources/labour")
    return value


def _effects(value):
    value = copy.deepcopy(value or {})
    if not isinstance(value, dict) or set(value) - {"training", "readiness", "doctrine"}:
        raise ValueError("Innovation effects must be training/readiness/doctrine, not free equipment or manpower")
    value.setdefault("training", .1)
    value.setdefault("readiness", 0.0)
    value.setdefault("doctrine", {})
    if any(not _number(value[k], 0, .25) for k in ("training", "readiness")):
        raise ValueError("Innovation quality effects outside local bounded range")
    if not isinstance(value["doctrine"], dict) or any(k not in DOCTRINE or k == "adoption" or not _number(v, -.25, .25)
            for k, v in value["doctrine"].items()):
        raise ValueError("Unknown or excessive doctrine effect")
    return value


def _formation_ids(world, actor, region, values=None):
    formations = state(world)["formations"]
    result = sorted(values if values is not None else (k for k, f in formations.items()
                    if f["actor"] == actor and f["location"] == region and f["manpower"] > 0))
    if len(result) != len(set(result)) or any(k not in formations or formations[k]["actor"] != actor
            or formations[k]["location"] != region or formations[k]["manpower"] <= 0 for k in result):
        raise ValueError("Innovation training requires existing local formations of its actor")
    return result


def propose(world, id, actor, region, concept, *, requirements=None, capability_effects=None,
            proposer=None, observers=None, formations=None, complexity=.5, parent_innovation=None,
            source="player_proposal"):
    """Store an idea without a military, industrial, training or reputation bonus."""
    s = state(world)
    if not _identity(id) or id in s["innovations"] or actor not in s["actors"] or region not in world.regions:
        raise ValueError("Unique innovation ID and valid actor/region required")
    a = s["actors"][actor]
    if a["country"] is not None and world.regions[region].owner != a["country"]:
        raise ValueError("A national innovation requires its owned research region")
    if not _identity(concept) or not _number(complexity, 0, 1):
        raise ValueError("Innovation concept and bounded complexity required")
    if source not in SOURCES:
        raise ValueError("Unknown military innovation provenance source")
    if proposer is not None and (world.npc_state is None or proposer not in world.npc_state["persons"]):
        raise ValueError("Unknown innovation proposer")
    observers = list(observers or [])
    if len(observers) != len(set(observers)) or any(world.npc_state is None or k not in world.npc_state["persons"] for k in observers):
        raise ValueError("Unknown or repeated innovation observer")
    if parent_innovation is not None and (parent_innovation not in s["innovations"] or
            s["innovations"][parent_innovation]["actor"] != actor):
        raise ValueError("Innovation revision requires an existing same-actor parent")
    requirements = _requirements(requirements if requirements is not None else EXAMPLES.get(concept))
    inventory = world.economy["regions"][region]["stocks"] if a["country"] and world.economy else a["stocks"]
    if any(name not in inventory or not _number(inventory[name]) for name in requirements["resources"]):
        raise ValueError("Innovation resources must refer to real scalar regional/actor inventories")
    p = {"id": id, "actor": actor, "country": a["country"], "region": region,
         "concept": concept, "source": source, "created_at": world.date, "stage": "concept", "status": "proposed",
         "complexity": complexity, "requirements": requirements, "capability_effects": _effects(capability_effects),
         "proposer": proposer, "observers": observers, "target_formations": _formation_ids(world, actor, region, formations),
         "parent_innovation": parent_innovation, "stage_projects": {}, "completed_stages": [],
         "receipts": {}, "results": [], "trial_evidence": None, "adoption": {}, "institutions": {},
         "training_cohort": None, "stage_adoption": {}}
    s["innovations"][id] = p
    s["revision"] += 1
    emit(world, "innovation_proposed", innovation_id=id, actor=actor, region=region,
         concept=concept, source=source, proposer=proposer, observers=observers)
    return copy.deepcopy(p)


def technical_capabilities(world, actor, region):
    """Capability conditions, never historical-year locks or discovery possession alone."""
    s = state(world)
    if actor not in s["actors"] or region not in world.regions:
        raise ValueError("Unknown military technical capability actor/region")
    a, local = s["actors"][actor], world.economy["regions"][region] if world.economy else None
    values = dict.fromkeys(CAPABILITIES, 0.0)
    if local:
        def sector(name):
            item = local["sectors"][name]
            technology = item["technology"]
            # Existing plant and professionals permit rudimentary manufacture;
            # imported access without installation/domestic experience does not.
            plant = item["capital_stock"] / (item["capital_stock"] + 100) if item["capital_stock"] > 0 else 0
            skill = min(1, item.get("professional_labour", 0) / max(1, item["labour"] * .05))
            installed = bool(technology["installed"])
            return min(1, .1 * plant * skill + ( .35 * technology["adoption"] + .55 * technology["domestic_capability"] if installed else 0))
        metal, machine, chemical, energy = (sector(k) for k in ("heavy_industry", "machinery", "chemicals", "energy"))
        values.update(metallurgy=metal, armor=metal, precision=machine, engine=min(machine, energy),
                      tracks=min(machine, metal), transmission=machine, fuel=min(chemical, energy),
                      literacy=local["education"]["literacy"])
        infra = world.regions[region].infrastructure
        communications = infra.get("communications", 0)
        values["communications"] = communications / (communications + 50) if communications > 0 else 0
    forms = [f for f in s["formations"].values() if f["actor"] == actor and f["location"] == region and f["manpower"] > 0]
    if forms:
        values["training"] = math.fsum(f["training"] * f["manpower"] for f in forms) / sum(f["manpower"] for f in forms)
        values["organization"] = math.fsum(f["doctrine"].get("staff_capability", 0) * f["manpower"] for f in forms) / sum(f["manpower"] for f in forms)
    for key, value in a.get("technical_capabilities", {}).items():
        if key not in CAPABILITIES or not _number(value, 0, 1):
            raise ValueError("Invalid configured technical capability")
        values[key] = value
    return values


def _project_requirements(p, cost):
    req = copy.deepcopy(p["requirements"])
    req.pop("capabilities")
    if cost:
        req["resources"].setdefault("raw_materials", cost * .02)
        req["minimum_labour"] = max(req["minimum_labour"], cost * .001)
    req["economy_enabled"] = True
    return req


def assess(world, id, *, perceived=None, cost=0, allow_emergency=True, instruments=None):
    """Return separate experimental, technical and financial constraints.

    A technically dubious idea may still be funded as an experiment. A missing
    resource or inaccessible finance cannot silently become usable capability.
    """
    p, s = state(world)["innovations"][id], state(world)
    if type(cost) is not int or cost < 0:
        raise ValueError("Innovation quote cost must be a nonnegative integer")
    values = technical_capabilities(world, p["actor"], p["region"])
    gaps = {k: {"required": v, "available": values[k]} for k, v in p["requirements"]["capabilities"].items() if values[k] < v}
    stocks = (world.economy["regions"][p["region"]]["stocks"] if world.economy and p["country"] else s["actors"][p["actor"]]["stocks"])
    project_req = _project_requirements(p, cost)
    missing = {k: {"required": v, "available": stocks.get(k, 0)} for k, v in project_req["resources"].items() if stocks.get(k, 0) < v}
    physical = {"feasible": not missing, "missing_resources": missing,
                "experimental_work_feasible": not missing, "operational_concept_feasible": not missing and not gaps,
                "technical_gaps": gaps, "available_capabilities": values,
                "note": "A speculative experiment is distinct from proven usable military capability."}
    reasons = [{"factor": k, "available": values[k], "required": v, "satisfied": values[k] >= v}
               for k, v in sorted(p["requirements"]["capabilities"].items())]
    if p["country"] and world.economy and world.economy.get("stabilization_version") == 1:
        from .credit import assess as credit_assess
        from .guardrails import _stock_reservation_view
        reserved = _stock_reservation_view(world, (p["region"],))
        if not missing:
            for name, quantity in project_req["resources"].items():
                reserved.economy["regions"][p["region"]]["stocks"][name] -= quantity
        quote = credit_assess(reserved, p["country"], cost, allow_emergency=allow_emergency,
                              instruments=instruments, perceived=perceived)
    else:
        treasury = s["actors"][p["actor"]]["treasury"]
        quote = {"normal_financial_feasibility": {"feasible": treasury >= cost, "available": treasury},
                 "emergency_financial_feasibility": {"feasible": treasury >= cost, "available": treasury},
                 "expected_consequences": {"actual_cost": cost, "remaining_actor_treasury": max(0, treasury-cost)},
                 "perceived": copy.deepcopy(perceived)}
    if p["country"] is None:
        reasons.append({"factor": "unavailable_funding_adapter", "satisfied": False,
                        "basis": "Non-state resources are finite, but their common project funding adapter is not implemented in v1."})
    quote.update(innovation_id=id, physical_feasibility=physical, reason_trace=reasons,
                 execution_limitations=["unavailable_funding_adapter"] if p["country"] is None else [],
                 execution_feasibility=p["country"] is not None and not missing and
                    quote["emergency_financial_feasibility"]["feasible"])
    return copy.deepcopy(quote)


def stage_authority(world, id, decision_actor=None):
    """Separate conceptual proposal rights from paid institutional execution."""
    p, s = state(world)["innovations"][id], state(world)
    if decision_actor is None:
        return {"feasible": True, "scope": "trusted_player_or_scenario", "decision_actor": None}
    person = (world.npc_state or {}).get("persons", {}).get(decision_actor)
    if person is None or not person.get("active", True):
        return {"feasible": False, "reason": "unknown or inactive innovation decision maker"}
    government = world.npc_state["governments"].get(p["country"], {})
    if p["country"] is not None and person["country"] == p["country"] and government.get("ruler") == decision_actor:
        return {"feasible": True, "scope": "government_ruler", "decision_actor": decision_actor}
    delegation = s["delegations"].get(decision_actor)
    if not isinstance(delegation, dict) or delegation.get("actor") != p["actor"] or "innovation" not in delegation.get("scopes", []):
        return {"feasible": False, "reason": "no delegated innovation budget/execution authority"}
    if delegation.get("expires") is not None and delegation["expires"] < world.date:
        return {"feasible": False, "reason": "innovation delegation expired"}
    if delegation.get("regions") and p["region"] not in delegation["regions"]:
        return {"feasible": False, "reason": "innovation research region outside delegation"}
    from .military_operations import _covered
    if not all(_covered(s, delegation.get("formations", []), key) for key in p["target_formations"]):
        return {"feasible": False, "reason": "innovation trainees outside delegated formation hierarchy"}
    return {"feasible": True, "scope": "delegated_innovation", "decision_actor": decision_actor}


def _surviving_cohort(world, p):
    cohort = p.get("training_cohort")
    if cohort is None:
        return {key: f["manpower"] for key in p["target_formations"]
                if (f := state(world)["formations"].get(key)) and f["actor"] == p["actor"] and f["location"] == p["region"]}
    result = dict(cohort["formations"])
    prior = set(cohort["prior_loss_receipts"])
    # Conservatively charge later losses against the original class; new people
    # cannot replace a lost trained person for free. Individual identities are
    # outside v1; this cap may understate survivors when replacements suffer.
    for key, receipt in state(world)["loss_receipts"].items():
        if key in prior or receipt["date"] < cohort["funded_date"]:
            continue
        for formation_id, losses in receipt["result"].get("casualties", {}).items():
            if formation_id in result:
                result[formation_id] = max(0, result[formation_id] - sum(losses.values()))
    for key in result:
        f = state(world)["formations"].get(key)
        result[key] = min(result[key], f["manpower"] if f and f["actor"] == p["actor"] else 0)
    return result


def _advanced_training(p):
    return any(p["requirements"]["capabilities"].get(k, 0) > 0
               for k in ("engine", "armor", "tracks", "transmission", "precision"))


def training_cost(world, id, stage, duration_days):
    """Instructor/material floor, proportional to real eligible people and time."""
    p = state(world)["innovations"][id]
    cohorts = _surviving_cohort(world, p) if stage in ("training", *ADOPTION_LIMITS) else {}
    advanced = _advanced_training(p)
    rate = .04 if advanced else .02
    headcount = sum(cohorts.values())
    return {"cohorts": cohorts, "manpower": headcount, "duration_days": duration_days,
            "annual_cost_per_trainee": rate, "advanced": advanced,
            "minimum_cost": math.ceil(headcount * duration_days / 365 * rate),
            "basis": "existing local military cohort; instructors and training materials over the actual stage window"}


def stage_duration(world, id, stage, nominal_days):
    """Bounded transition friction, priced before the common project is funded.

    Opinions must already have been received and explicitly reviewed. They can
    slow implementation, rather than secretly cancel a political choice.
    Mission command additionally needs an actual change in command culture.
    """
    p = state(world)["innovations"][id]
    reasons, reviews, speed = [], [], 1.0
    if stage in ("training", *ADOPTION_LIMITS):
        reviews = [copy.deepcopy(opinion) for _, opinion in sorted(p["institutions"].items())]
        if reviews:
            score = math.fsum(max(-.5, min(.5, item["score"])) for item in reviews) / len(reviews)
            political_speed = .75 + .5 * score
            speed = min(speed, political_speed)
            reasons.append({"factor": "received_institutional_support", "average_bounded_score": score,
                "execution_speed": political_speed, "observers": [item["actor"] for item in reviews],
                "basis": "explicit received proposal reviews; opposition delays but does not cancel"})
        mission = p["concept"] in ("mission_command", "mission_oriented_command", "auftragstaktik") or \
            p["capability_effects"]["doctrine"].get("mission_oriented_command", 0) > 0
        if mission:
            forms = [state(world)["formations"][key] for key in p["target_formations"]
                     if state(world)["formations"][key]["manpower"] > 0]
            heads = sum(f["manpower"] for f in forms)
            def quality(key):
                return math.fsum((f["training"] if key == "training" else f["doctrine"][key]) * f["manpower"]
                    for f in forms) / max(1, heads)
            values = {key: quality(key) for key in ("training", "staff_capability", "subordinate_initiative",
                "centralized_control", "punishment_risk_culture")}
            values["literacy"] = world.economy["regions"][p["region"]]["education"]["literacy"]
            readiness = (.2 * values["training"] + .2 * values["staff_capability"] +
                .1 * values["literacy"] + .25 * values["subordinate_initiative"] +
                .1 * (1-values["centralized_control"]) + .15 * (1-values["punishment_risk_culture"]))
            culture_speed = .5 + .5 * readiness
            speed = min(speed, culture_speed)
            reasons.append({"factor": "mission_command_transition", "actual_conditions": values,
                "readiness": readiness, "execution_speed": culture_speed,
                "basis": "training/staff/literacy and delegation culture; nominal declaration is insufficient"})
    speed = max(.5, min(1., speed))
    return {"nominal_days": nominal_days, "effective_days": math.ceil(nominal_days / speed),
        "execution_speed": speed, "reason_trace": reasons, "institutional_reviews": reviews,
        "basis": "bounded implementation friction; unchanged project clock and prepaid cohort cost"}


def begin_stage(world, id, stage, *, cost=None, duration_days=None, allow_emergency=False, instruments=None,
                decision_actor=None):
    """Authorize one paid common-pipeline stage; never create an innovation timer."""
    p = state(world)["innovations"][id]
    permission = stage_authority(world, id, decision_actor)
    if not permission["feasible"]:
        raise ValueError(permission["reason"])
    if stage not in STAGES[1:] or stage in p["stage_projects"]:
        raise ValueError("Unknown or already funded innovation stage")
    required = list(STAGES[1:STAGES.index(stage)])
    if p["completed_stages"] != required:
        raise ValueError("Innovation requires completed preceding stages")
    if STAGES.index(stage) > STAGES.index("evaluation") and not (p.get("trial_evidence") or {}).get("successful", False):
        raise ValueError("Unproven concept cannot receive doctrine/training/adoption effects")
    if p["country"] is None:
        raise ValueError("Non-state innovation requires an explicit finite-resource project funding adapter")
    duration = MINIMUM_DAYS[stage] if duration_days is None else duration_days
    if type(duration) is not int or duration < MINIMUM_DAYS[stage]:
        raise ValueError("Innovation stage requires its minimum real development/training time")
    duration_info = stage_duration(world, id, stage, duration)
    duration = duration_info["effective_days"]
    cost = max(1, math.ceil(25 + 100 * p["complexity"])) if cost is None else cost
    if type(cost) is not int or cost <= 0:
        raise ValueError("Innovation stage requires positive paid cost")
    proposed_cost = cost
    cohort_cost = training_cost(world, id, stage, duration)
    if stage in ("training", *ADOPTION_LIMITS) and not cohort_cost["manpower"]:
        raise ValueError("Military training/adoption requires an existing eligible local cohort")
    cost = max(cost, cohort_cost["minimum_cost"])
    from . import projects
    quote = assess(world, id, cost=cost, allow_emergency=allow_emergency, instruments=instruments)
    if not quote["execution_feasibility"]:
        raise ValueError("Innovation has unavailable physical resources or accessible funding")
    project_id = "military-innovation:" + id + ":" + stage
    req = _project_requirements(p, cost)
    project = projects.plan_military(world, project_id, p["country"], p["region"], stage,
        duration, cost, requirements=req, metadata={"innovation_id": id, "actor": p["actor"], "stage": stage,
                                                  "decision_actor": decision_actor, "permission": permission,
                                                  "proposed_cost": proposed_cost, "training_cost": cohort_cost,
                                                  "stage_duration": duration_info})
    # Funding and physical reservation use the same ordinary project API. The
    # orchestrator transaction rolls back both records if financing fails.
    c = world.countries[p["country"]]
    old_cash, old_debt = c.treasury, c.debt
    projects.fund(world, project, allow_emergency=allow_emergency, instruments=instruments)
    from .military_bridge import book_interim
    book_interim(world, p["country"], old_cash, old_debt)
    if stage == "training":
        p["training_cohort"] = {"project_id": project_id, "funded_date": world.date,
            "formations": copy.deepcopy(cohort_cost["cohorts"]),
            "prior_loss_receipts": sorted(state(world)["loss_receipts"])}
    p["stage_projects"][stage] = project_id
    p.update(stage=stage, status="in_progress")
    state(world)["revision"] += 1
    emit(world, "innovation_stage_started", innovation_id=id, project_id=project_id, stage=stage,
         actual_cost=cost, expected_completion_date=project["expected_completion_date"], quote=quote,
         decision_actor=decision_actor, permission=permission, training_cost=cohort_cost,
         proposed_cost=proposed_cost, stage_duration=duration_info)
    return copy.deepcopy(project)


def _trial(world, p, project):
    quote = assess(world, p["id"])
    values, req = quote["physical_feasibility"]["available_capabilities"], p["requirements"]["capabilities"]
    ratios = [min(1, values[k] / needed) for k, needed in sorted(req.items()) if needed > 0]
    technical_match = min(ratios, default=1.0)
    probability = max(0, min(.98, .3 + .35 * technical_match + .2 * values["training"] + .15 * values["organization"] - .2 * p["complexity"]))
    gaps = quote["physical_feasibility"]["technical_gaps"]
    with stream(world, "innovation:" + p["id"] + ":trial") as rng:
        draw = rng.random()
    successful = not gaps and draw < probability
    return {"project_id": project["id"], "date": world.date, "successful": successful,
            "technical_match": technical_match, "technical_gaps": gaps,
            "probability": probability, "draw": draw,
            "reason_trace": quote["reason_trace"] + [
                {"factor": "training", "value": values["training"], "contribution": .2 * values["training"]},
                {"factor": "organization", "value": values["organization"], "contribution": .15 * values["organization"]},
                {"factor": "complexity", "value": p["complexity"], "contribution": -.2 * p["complexity"]}],
            "interpretation": "locally_proven" if successful else "technical_gap" if gaps else "trial_failure"}


def _adopt(world, p, limit, authorized_cohorts):
    applied = {}
    surviving = _surviving_cohort(world, p)
    for key in p["target_formations"]:
        f = state(world)["formations"].get(key)
        if not f or f["actor"] != p["actor"] or f["location"] != p["region"] or f["manpower"] <= 0:
            continue  # No distant unit inherits locally trained skills by teleportation.
        paid = min(authorized_cohorts.get(key, 0), surviving.get(key, 0), f["manpower"])
        coverage_limit = limit * paid / f["manpower"]
        before = p["adoption"].get(key, 0.0)
        prior_stage = p["stage_adoption"].get(key, 0.0)
        delta = max(0, limit - prior_stage) * paid / f["manpower"]
        if not delta and not paid:
            continue
        change = {"adoption_before": before, "adoption_after": coverage_limit, "paid_cohort": paid,
                  "formation_manpower": f["manpower"], "stage_adoption_before": prior_stage,
                  "stage_adoption_after": limit, "training_before": f["training"],
                  "readiness_before": f["readiness"], "doctrine_before": copy.deepcopy(f["doctrine"])}
        for name in ("training", "readiness"):
            f[name] = min(1, f[name] + p["capability_effects"][name] * delta)
        for name, amount in p["capability_effects"]["doctrine"].items():
            f["doctrine"][name] = min(1, max(0, f["doctrine"].get(name, DOCTRINE[name]) + amount * delta))
        f["adoption"][p["id"]] = coverage_limit
        f["doctrine"]["adoption"] = max(f["doctrine"].get("adoption", 0), coverage_limit)
        p["adoption"][key] = coverage_limit
        p["stage_adoption"][key] = limit
        change.update(training_after=f["training"], readiness_after=f["readiness"], doctrine_after=copy.deepcopy(f["doctrine"]))
        applied[key] = change
    return applied


def on_project_completed(world, project):
    """Exactly-once stage evidence, invoked by the common project effect pipeline."""
    metadata = project.get("metadata", {})
    if "innovation_id" not in metadata:
        return []  # Other approved military project families use their own hook.
    p = state(world)["innovations"].get(metadata["innovation_id"])
    if p is None:
        raise ValueError("Military project references a missing innovation")
    stage = metadata.get("stage")
    if stage not in STAGES[1:] or p["stage_projects"].get(stage) != project["id"]:
        raise ValueError("Innovation project/stage ownership mismatch")
    if project["id"] in p["receipts"]:
        return []
    duration = sum(s["days"] for s in project["stages"])
    if (project["country"] != p["country"] or project["region"] != p["region"] or
            project["kind"] != "military" or project["target"] != stage or not project["funded_date"] or
            project["spent"] != project["cost"] or project["elapsed_days"] != duration or
            duration < MINIMUM_DAYS[stage] or
            (canonical_date(world.date) - canonical_date(project["funded_date"])).days < duration):
        raise ValueError("Innovation effect needs actual funded common-project completion")
    if p["completed_stages"] != list(STAGES[1:STAGES.index(stage)]):
        raise ValueError("Innovation completion skips required preceding evidence")
    if stage in ("training", *ADOPTION_LIMITS):
        cost_info = metadata.get("training_cost", {})
        cohorts = cost_info.get("cohorts", {})
        if (not isinstance(cohorts, dict) or not cohorts or
                any(key not in p["target_formations"] or type(count) is not int or count < 0 for key, count in cohorts.items()) or
                cost_info.get("duration_days") != duration or cost_info.get("annual_cost_per_trainee") != (.04 if _advanced_training(p) else .02) or
                cost_info.get("manpower") != sum(cohorts.values()) or
                cost_info.get("minimum_cost") != math.ceil(sum(cohorts.values()) * duration / 365 * cost_info["annual_cost_per_trainee"]) or
                project["cost"] < cost_info["minimum_cost"] or p["training_cohort"] is None):
            raise ValueError("Innovation effect requires its priced, existing training cohort")
    receipt = {"project_id": project["id"], "innovation_id": p["id"], "stage": stage,
               "date": world.date, "actual_cost": project["cost"], "elapsed_days": duration, "unit_changes": {},
               "training_cost": copy.deepcopy(metadata.get("training_cost", {}))}
    if stage == "trial":
        result = _trial(world, p, project)
        p["trial_evidence"] = copy.deepcopy(result)
        p["results"].append(result)
        receipt["result"] = copy.deepcopy(result)
    elif stage in ADOPTION_LIMITS:
        if not (p.get("trial_evidence") or {}).get("successful", False) or "training" not in p["completed_stages"]:
            raise ValueError("Local training and successful trial required before adoption")
        cohort_cost = metadata.get("training_cost", {})
        if not cohort_cost or project["cost"] < cohort_cost["minimum_cost"]:
            raise ValueError("Adoption needs its actual paid training cohort")
        receipt["unit_changes"] = _adopt(world, p, ADOPTION_LIMITS[stage], cohort_cost["cohorts"])
    p["receipts"][project["id"]] = receipt
    p["completed_stages"].append(stage)
    p["stage"], p["status"] = stage, "completed" if stage == "diffusion" else "stage_completed"
    state(world)["revision"] += 1
    record = emit(world, "innovation_stage_completed", innovation_id=p["id"], actor=p["actor"],
                  region=p["region"], stage=stage, receipt=receipt,
                  proposer=p["proposer"], observers=p["observers"])
    # The coordinator routes this objective evidence to explicit reports. A
    # failed trial is not magically known to every officer or held against a
    # ruler merely for proposing an idea.
    return [record]


def tick(world, tick_event, rng):
    """Project tick owns all time progression; there is no parallel timer here."""
    return []


def project_conditions(world, project):
    """Read-only conditions for the common project clock's existing delay path."""
    from .military_logistics import is_stationary
    metadata = project.get("metadata", {})
    if "innovation_id" not in metadata:
        return {"ready": True, "reasons": []}
    p = state(world)["innovations"].get(metadata["innovation_id"])
    if p is None:
        return {"ready": False, "reasons": ["missing innovation"]}
    stage = metadata.get("stage")
    if stage not in ("training", "limited_adoption", "institutional_adoption", "diffusion"):
        return {"ready": True, "reasons": []}
    reasons = []
    for key in p["target_formations"]:
        f = state(world)["formations"].get(key)
        if f is None or f["manpower"] <= 0 or f["actor"] != p["actor"]:
            reasons.append("missing existing trainee formation:" + key)
        elif f["location"] != p["region"] or not is_stationary(world, f):
            reasons.append("trainees outside local training site:" + key)
        elif f.get("current_order") is not None and f.get("operational_objective") not in (None, "training"):
            reasons.append("trainees committed to another operation:" + key)
    return {"ready": not reasons, "reasons": reasons}


def register_standard(world, id, equipment_type, *, ammunition_family=None, parts_family=None,
                      precision=.5, interchangeability=.5):
    s = state(world)
    from .military_state import EQUIPMENT
    if not _identity(id) or id in s["equipment_standards"] or equipment_type not in EQUIPMENT:
        raise ValueError("Unique known equipment standard required")
    if any(v is not None and not _identity(v) for v in (ammunition_family, parts_family)) or any(
            not _number(v, 0, 1) for v in (precision, interchangeability)):
        raise ValueError("Invalid ammunition/parts/precision standard")
    item = {"id": id, "equipment_type": equipment_type, "ammunition_family": ammunition_family,
            "parts_family": parts_family, "precision": precision, "interchangeability": interchangeability,
            "created_at": world.date}
    s["equipment_standards"][id] = item
    s["revision"] += 1
    emit(world, "equipment_standard_registered", standard=copy.deepcopy(item))
    return copy.deepcopy(item)


def equipment_compatibility(world, formation_id):
    """Separate ammunition compatibility from precision and interchangeable parts."""
    s, ammo, parts, standards = state(world), set(), set(), []
    standard_quantities, ammo_equipment, parts_equipment = {}, {}, {}
    f = s["formations"][formation_id]
    unstandardized = 0
    for key, item in sorted(f["equipment_state"].items()):
        if item.get("quantity", 0) <= 0:
            continue
        standard_id = item.get("standard_id")
        if standard_id is None:
            unstandardized += 1
            continue
        if standard_id not in s["equipment_standards"]:
            raise ValueError("Formation has a nonexistent equipment standard")
        standard = s["equipment_standards"][standard_id]
        if standard["equipment_type"] != key and item.get("equipment_type", key) != standard["equipment_type"]:
            raise ValueError("Equipment type differs from its standard")
        standards.append(standard)
        standard_quantities[standard_id] = standard_quantities.get(standard_id, 0) + item["quantity"]
        if standard["ammunition_family"]:
            ammo.add(standard["ammunition_family"])
            family = standard["ammunition_family"]
            ammo_equipment[family] = ammo_equipment.get(family, 0) + item["quantity"]
        if standard["parts_family"]:
            parts.add(standard["parts_family"])
            family = standard["parts_family"]
            parts_equipment[family] = parts_equipment.get(family, 0) + item["quantity"]
    diversity = max(0, len(ammo)-1) + max(0, len(parts)-1)
    interchangeability = min((v["interchangeability"] for v in standards), default=1.0 if not unstandardized else 0.0)
    precision = min((v["precision"] for v in standards), default=0.0)
    return {"formation_id": formation_id, "ammunition_families": sorted(ammo), "parts_families": sorted(parts),
            "per_standard_quantities": standard_quantities, "ammunition_family_equipment": ammo_equipment,
            "parts_family_equipment": parts_equipment,
            "ammunition_compatible": len(ammo) <= 1, "parts_interchangeability": interchangeability,
            "manufacturing_precision": precision, "unstandardized_types": unstandardized,
            "training_load": 1 + .1 * diversity + .05 * unstandardized,
            "maintenance_load": 1 + .15 * diversity + .3 * (1-interchangeability) + .05 * unstandardized,
            "reason_trace": [{"factor": "family_diversity", "value": diversity},
                             {"factor": "interchangeable_parts", "value": interchangeability},
                             {"factor": "precision", "value": precision}]}


def institutional_response(actor_view, proposal_report):
    """Institutional resistance/support from perceived evidence, not world truth."""
    person = actor_view["person"]
    perceived = proposal_report.get("facts", proposal_report)
    if not isinstance(perceived, dict):
        raise ValueError("Innovation opinion requires observed proposal facts")
    novelty = perceived.get("novelty", .5)
    burden = perceived.get("training_burden", .5)
    expected = perceived.get("expected_benefit", .5)
    if any(not _number(v, 0, 1) for v in (novelty, burden, expected)):
        raise ValueError("Invalid perceived innovation quantities")
    entries = []
    def term(factor, amount, basis):
        entries.append({"factor": factor, "contribution": amount, "basis": copy.deepcopy(basis)})
    traits = person["traits"]
    term("perceived_capability_benefit", expected * (.2 + .3 * traits["pragmatism"]), expected)
    term("training_and_transition_burden", -burden * (.15 + .2 * (1-traits["patience"])), burden)
    term("institutional_attachment", -novelty * .25 * traits["institutional_loyalty"], traits["institutional_loyalty"])
    term("personal_prestige_interest", novelty * .2 * traits["ambition"] * traits["prestige_sensitivity"], traits["ambition"])
    ruler = actor_view.get("ruler_id")
    relation = actor_view.get("relationships", {}).get(ruler)
    if relation is not None:
        axes = {key: relation[key] for key in ("trust", "respect", "perceived_legitimacy")}
        if any(not _number(value, 0, 1) for value in axes.values()):
            raise ValueError("Invalid observed ruler relationship")
        confidence = .4 * axes["trust"] + .3 * axes["respect"] + .3 * axes["perceived_legitimacy"]
        term("observed_ruler_trust_respect_legitimacy", .3 * (confidence-.5),
             {"ruler": ruler, "axes": axes, "as_of": relation.get("as_of")})
    for group_id, group in sorted(actor_view.get("groups", {}).items()):
        preference = group.get("preferences", {}).get("military_innovation", 0)
        interest = group.get("interests", {}).get("military_innovation", preference)
        if not _number(interest, -1, 1):
            raise ValueError("Invalid perceived institutional innovation interest")
        term("institutional_interest:" + group_id, .2 * interest * group["influence"], interest)
    memories = list(actor_view.get("memories", []))
    for group_id, items in sorted(actor_view.get("institutional_memories", {}).items()):
        memories.extend(items)
    for memory in memories:
        interpretation = memory.get("interpretation", {})
        if not isinstance(interpretation, dict):
            continue
        observed = memory.get("observed_facts", {})
        observed_trial = observed.get("trial_success") if isinstance(observed, dict) else None
        # A specific personal/institutional interpretation overrides this modest
        # experiential default. The fact table is never consulted here.
        lesson = interpretation.get("innovation_lesson", 1 if observed_trial is True else -1 if observed_trial is False else 0)
        if lesson and _number(lesson, -1, 1):
            term("observed_innovation_memory:" + str(memory.get("event_id", "")),
                 .3 * lesson * memory.get("effective_weight", 0), interpretation)
    score = math.fsum(t["contribution"] for t in entries)
    return {"actor": actor_view["actor"], "score": score,
            "position": "support" if score > .05 else "oppose" if score < -.05 else "reserve_judgement",
            "reason_trace": entries, "information_basis": "delivered_actor_information"}


def review_institution(world, id, observer):
    """Store an opinion only after this observer actually receives the proposal.

    The political opinion can change the duration of a subsequently funded
    training/adoption stage. It never cancels a programme or grants capability.
    """
    p = state(world)["innovations"][id]
    from .npc_state import actor_view
    view = actor_view(world.npc_state, observer, world.date)
    reports = [(view["report_metadata"][topic]["as_of"], topic, r)
               for topic, r in view["beliefs"].items() if isinstance(r, dict) and
               isinstance(r.get("facts"), dict) and r["facts"].get("innovation_id") == id]
    if not reports:
        raise ValueError("Observer has not received this innovation proposal")
    report = max(reports, key=lambda entry: (entry[0], entry[1]))[2]
    response = institutional_response(view, report)
    response.update(date=world.date, source_event=report.get("event_id"))
    p["institutions"][observer] = copy.deepcopy(response)
    state(world)["revision"] += 1
    emit(world, "innovation_institutional_opinion", innovation_id=id, observer=observer, response=response)
    return copy.deepcopy(response)


def validation_issues(world, previous=None):
    if getattr(world, "military_state", None) is None:
        return []
    issues, s = [], state(world)
    for id, p in audit_items(s["innovations"]):
        try:
            if (p["id"] != id or p["actor"] not in s["actors"] or p["country"] != s["actors"][p["actor"]]["country"]
                    or p["region"] not in world.regions or p["stage"] not in STAGES or p["created_at"] > world.date
                    or p["status"] not in ("proposed", "in_progress", "stage_completed", "completed") or not _number(p["complexity"], 0, 1)):
                issues.append("military innovation: invalid identity/state " + id)
            canonical_date(p["created_at"])
            _requirements(p["requirements"])
            _effects(p["capability_effects"])
            if p.get("source", "player_proposal") not in SOURCES:
                issues.append("military innovation: invalid provenance source " + id)
            completed = p["completed_stages"]
            if completed != list(STAGES[1:1+len(completed)]):
                issues.append("military innovation: missing preceding stages " + id)
            if len(p["target_formations"]) != len(set(p["target_formations"])) or any(k not in s["formations"] or s["formations"][k]["actor"] != p["actor"] for k in p["target_formations"]):
                issues.append("military innovation: invalid target formation " + id)
            for stage, project_id in audit_items(p["stage_projects"]):
                project = world.projects.get(project_id)
                if stage not in STAGES[1:] or project is None or project.get("kind") != "military" or project.get("target") != stage or project.get("metadata", {}).get("innovation_id") != id:
                    issues.append("military innovation: invalid project link " + id)
                elif "stage_duration" in project["metadata"]:
                    duration_info = project["metadata"]["stage_duration"]
                    if (type(duration_info["nominal_days"]) is not int or duration_info["nominal_days"] < MINIMUM_DAYS[stage] or
                            not _number(duration_info["execution_speed"], .5, 1) or
                            duration_info["effective_days"] != math.ceil(duration_info["nominal_days"] / duration_info["execution_speed"]) or
                            sum(item["days"] for item in project["stages"]) != duration_info["effective_days"]):
                        issues.append("military innovation: invalid implementation duration " + id)
                if project is not None and stage in ("training", *ADOPTION_LIMITS):
                    info = project["metadata"]["training_cost"]
                    cohorts = info["cohorts"]
                    duration = sum(item["days"] for item in project["stages"])
                    rate = .04 if _advanced_training(p) else .02
                    if (not cohorts or any(key not in p["target_formations"] or type(count) is not int or count < 0 for key, count in cohorts.items()) or
                            info["duration_days"] != duration or info["manpower"] != sum(cohorts.values()) or
                            info["annual_cost_per_trainee"] != rate or
                            info["minimum_cost"] != math.ceil(sum(cohorts.values()) * duration / 365 * rate) or
                            project["cost"] < info["minimum_cost"]):
                        issues.append("military innovation: underfunded training cohort " + id)
            if set(p["receipts"]) != {p["stage_projects"].get(k) for k in completed}:
                issues.append("military innovation: completion receipt mismatch " + id)
            for project_id, receipt in audit_items(p["receipts"]):
                project = world.projects[project_id]
                if (receipt["project_id"] != project_id or receipt["innovation_id"] != id or receipt["stage"] != project["target"] or
                        receipt["date"] > world.date or project["status"] != "completed" or receipt["date"] != project["completed_date"] or
                        receipt["actual_cost"] != project["cost"] or receipt["elapsed_days"] != project["elapsed_days"]):
                    issues.append("military innovation: invalid receipt " + id)
                canonical_date(receipt["date"])
                if receipt["stage"] == "trial" and receipt.get("result") != p["trial_evidence"]:
                    issues.append("military innovation: trial differs from completion evidence " + id)
            if p["trial_evidence"] is not None:
                trial = p["trial_evidence"]
                if ("trial" not in completed or trial not in p["results"] or type(trial["successful"]) is not bool or
                        not _number(trial["probability"], 0, 1) or not _number(trial["draw"], 0, 1) or
                        trial["successful"] != (not trial["technical_gaps"] and trial["draw"] < trial["probability"])):
                    issues.append("military innovation: inconsistent trial evidence " + id)
            elif "trial" in completed:
                issues.append("military innovation: missing trial evidence " + id)
            cohort = p.get("training_cohort")
            if cohort is not None:
                canonical_date(cohort["funded_date"])
                training_project = world.projects[cohort["project_id"]]
                if (p["stage_projects"].get("training") != cohort["project_id"] or
                        training_project["funded_date"] != cohort["funded_date"] or
                        training_project.get("metadata", {}).get("training_cost", {}).get("cohorts") != cohort["formations"] or
                        any(key not in p["target_formations"] or type(count) is not int or count < 0 for key, count in cohort["formations"].items()) or
                        len(cohort["prior_loss_receipts"]) != len(set(cohort["prior_loss_receipts"])) or
                        any(key not in s["loss_receipts"] for key in cohort["prior_loss_receipts"])):
                    issues.append("military innovation: invalid paid training cohort " + id)
            elif "training" in p["stage_projects"]:
                issues.append("military innovation: missing paid training cohort " + id)
            for observer, opinion in audit_items(p["institutions"]):
                canonical_date(opinion["date"])
                if (world.npc_state is None or observer not in world.npc_state["persons"] or
                        opinion["actor"] != observer or opinion["date"] > world.date or
                        type(opinion["score"]) not in (int, float) or not math.isfinite(opinion["score"]) or
                        opinion["score"] != math.fsum(t["contribution"] for t in opinion["reason_trace"]) or
                        opinion["position"] not in ("support", "oppose", "reserve_judgement")):
                    issues.append("military innovation: invalid institutional opinion " + id)
                if not any(report["as_of"] <= opinion["date"] and
                           report["data"].get("event_id") == opinion["source_event"] and
                           report["data"].get("facts", {}).get("innovation_id") == id
                           for report in world.npc_state["reports"].get(observer, {}).values()):
                    issues.append("military innovation: opinion lacks delivered proposal evidence " + id)
            if p["adoption"] and ("training" not in completed or not (p.get("trial_evidence") or {}).get("successful", False)):
                issues.append("military innovation: untrained/unproven adoption " + id)
            limit = max((ADOPTION_LIMITS.get(stage, 0) for stage in completed), default=0)
            for formation_id, adoption in audit_items(p["adoption"]):
                if formation_id not in p["target_formations"] or not _number(adoption, 0, limit) or s["formations"][formation_id]["adoption"].get(id) != adoption:
                    issues.append("military innovation: unsupported adoption " + id)
                if not _number(p["stage_adoption"].get(formation_id), 0, limit) or adoption > p["stage_adoption"][formation_id]:
                    issues.append("military innovation: unsupported cohort adoption stage " + id)
            if set(p["stage_adoption"]) != set(p["adoption"]):
                issues.append("military innovation: adoption cohort mismatch " + id)
            if previous and getattr(previous, "military_state", None) and id in previous.military_state["innovations"]:
                old = previous.military_state["innovations"][id]
                for name in ("id", "actor", "country", "region", "concept", "created_at", "complexity", "requirements", "capability_effects", "target_formations", "parent_innovation", "proposer", "observers"):
                    if p[name] != old[name]:
                        issues.append("military innovation: changed original proposal " + id)
                if p.get("source", "player_proposal") != old.get("source", "player_proposal"):
                    issues.append("military innovation: changed original provenance source " + id)
                for key, receipt in audit_items(old["receipts"]):
                    if p["receipts"].get(key) != receipt:
                        issues.append("military innovation: changed immutable evidence " + id)
                if old.get("training_cohort") is not None and p.get("training_cohort") != old["training_cohort"]:
                    issues.append("military innovation: changed paid training cohort " + id)
                if p["results"][:len(old["results"])] != old["results"] or p["completed_stages"][:len(old["completed_stages"])] != old["completed_stages"]:
                    issues.append("military innovation: erased trial/stage history " + id)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            issues.append("military innovation: malformed " + str(id))
    for id, item in audit_items(s["equipment_standards"]):
        try:
            from .military_state import EQUIPMENT
            if item["id"] != id or item["equipment_type"] not in EQUIPMENT or not _number(item["precision"], 0, 1) or not _number(item["interchangeability"], 0, 1):
                issues.append("military innovation: invalid standard " + id)
            canonical_date(item["created_at"])
            if any(item[k] is not None and not _identity(item[k]) for k in ("ammunition_family", "parts_family")):
                issues.append("military innovation: invalid standard family " + id)
            if item["created_at"] > world.date:
                issues.append("military innovation: future standard " + id)
            if previous and getattr(previous, "military_state", None) and id in previous.military_state["equipment_standards"] and item != previous.military_state["equipment_standards"][id]:
                issues.append("military innovation: changed immutable standard " + id)
        except (KeyError, TypeError, ValueError):
            issues.append("military innovation: malformed standard " + str(id))
    if previous and getattr(previous, "military_state", None):
        if set(previous.military_state["innovations"]) - set(s["innovations"]) or set(previous.military_state["equipment_standards"]) - set(s["equipment_standards"]):
            issues.append("military innovation: erased innovation/standard history")
    return issues
