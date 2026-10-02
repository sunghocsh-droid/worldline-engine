"""Authoritative order validation shared by human and NPC interfaces.

Callers supply their transaction and actor authority checks. This module never
decides political intent and does not provide privileged NPC financing.
"""
import copy
import math
from dataclasses import asdict


def economic_view(world):
    """Read-only economic adapter excludes political logs from scratch clones.

    All real economic fields still refer to the same truth. Simulation operations
    mutate only a subsequent deep copy, never this shallow view.
    """
    if world.npc_state is None:
        return world
    view = copy.copy(world)
    view.npc_state, view.crisis_state, view.schema_version = None, None, 2
    view.military_state = None
    return view


def economic_candidate(world):
    return copy.deepcopy(economic_view(world))


def execute(world, order, journeys=None, required_systems=None, emit=None):
    emit = emit or (lambda kind, payload: None)
    journeys = {} if journeys is None else journeys
    required_systems = [] if required_systems is None else required_systems
    try:
        cost, operation = validate_order(world, order, journeys, required_systems, emit)
    except (ValueError, KeyError, TypeError) as exc:
        return {"status": "rejected", "reason": str(exc), "order": asdict(order)}
    world.countries[order.actor].treasury -= cost
    operation()
    return {"status": "accepted", "cost": cost, "order": asdict(order)}


def validate_order(world, o, journeys, required_systems, emit):
    if o.actor not in world.countries:
        raise ValueError("Unknown actor")
    c, p = world.countries[o.actor], o.params
    if not isinstance(p, dict):
        raise ValueError("Parameters must be an object")
    if world.economy and o.kind in ("produce", "research"):
        raise ValueError("Economy-enabled games use production constraints and the project pipeline")
    if o.kind == "war_preparation":
        from .guardrails import can_fund_war, require_stabilization
        from .credit import finance_amount
        from .labour import mobilize
        require_stabilization(world)
        if set(p) != {"target", "region", "amount", "manpower", "resources", "allow_emergency"}:
            raise ValueError("Preparation requires a target, region, cost, manpower, resources and financing choice")
        if p["target"] not in world.countries or p["target"] == o.actor:
            raise ValueError("Unknown or domestic military target")
        if p["region"] not in world.regions or world.regions[p["region"]].owner != o.actor:
            raise ValueError("Preparation requires an owned recruitment region")
        if type(p["manpower"]) is not int or p["manpower"] < 0 or type(p["allow_emergency"]) is not bool or not isinstance(p["resources"], dict):
            raise ValueError("Invalid military preparation parameters")
        current = world.economy["regions"][p["region"]]["labour_state"]["mobilized"]
        quote = can_fund_war(economic_view(world), o.actor, p["amount"], region=p["region"],
            manpower=max(0, p["manpower"] - current), resources=p["resources"],
            allow_emergency=p["allow_emergency"])
        if not quote["execution_feasibility"]:
            raise ValueError("Military preparation lacks actual manpower, resources or accessible financing")
        def prepare(candidate):
            stocks = candidate.economy["regions"][p["region"]]["stocks"]
            for resource, quantity in p["resources"].items():
                stocks[resource] -= quantity
                inputs = candidate.economy["regions"][p["region"]].get("inputs", {})
                if inputs and resource in ("energy", "raw_materials"):
                    inputs[f"emergency_{resource}_used"] = inputs.get(f"emergency_{resource}_used", 0) + quantity
                    inputs[f"closing_{resource}"] -= quantity
            financing = finance_amount(candidate, o.actor, p["amount"], purpose="war_preparation",
                                       allow_emergency=p["allow_emergency"])
            mobilize(candidate, o.actor, p["region"], p["manpower"])
            return financing
        prepare(economic_candidate(world))
        def operation():
            financing = prepare(world)
            emit("military_preparation_funded", {"country": o.actor, "target": p["target"],
                "manpower": p["manpower"], "financing": financing, "combat_resolved": False})
        return 0, operation
    if o.kind == "diplomatic_engagement":
        if set(p) != {"target"} or p["target"] not in world.countries or p["target"] == o.actor:
            raise ValueError("Diplomatic engagement requires a foreign target")
        def operation():
            key = ":".join(sorted((o.actor, p["target"])))
            relation = world.relations.setdefault(key, {"a": o.actor, "b": p["target"], "status": "neutral"})
            relation["last_engagement"] = {"from": o.actor, "date": world.date}
            emit("diplomatic_offer", {"country": o.actor, "target": p["target"]})
        return 0, operation
    if o.kind == "economic_policy":
        if not world.economy: raise ValueError("Economy is not initialized")
        from .economy import change_policy
        candidate = economic_candidate(world)
        change_policy(candidate, o.actor, p)
        return 0, lambda: change_policy(world, o.actor, p)
    if o.kind == "mobilize":
        from .labour import mobilize
        from .guardrails import require_stabilization
        require_stabilization(world)
        if set(p) != {"region", "amount"}: raise ValueError("Mobilization requires region and total reserve manpower")
        candidate = economic_candidate(world)
        mobilize(candidate, o.actor, p["region"], p["amount"])
        return 0, lambda: mobilize(world, o.actor, p["region"], p["amount"])
    if o.kind == "fiscal_action":
        from .credit import finance_amount
        from .guardrails import require_stabilization
        require_stabilization(world)
        if set(p) != {"amount", "purpose", "allow_emergency", "instruments"} or not isinstance(p["purpose"], str):
            raise ValueError("Fiscal action requires amount, purpose and financing choices")
        candidate = economic_candidate(world)
        if type(p["allow_emergency"]) is not bool: raise ValueError("Emergency financing must be a boolean")
        finance_amount(candidate, o.actor, p["amount"], allow_emergency=p["allow_emergency"], instruments=p["instruments"], purpose=p["purpose"])
        def operation():
            result = finance_amount(world, o.actor, p["amount"], allow_emergency=p["allow_emergency"], instruments=p["instruments"], purpose=p["purpose"])
            emit("fiscal_action_executed", {"country": o.actor, "purpose": p["purpose"], "financing": result})
        return 0, operation
    if o.kind in ("repay_debt", "restructure_debt"):
        from .credit import repay_debt, restructure
        from .guardrails import require_stabilization
        require_stabilization(world)
        candidate = economic_candidate(world)
        if o.kind == "repay_debt":
            if set(p) != {"amount"}: raise ValueError("Repayment requires amount")
            repay_debt(candidate, o.actor, p["amount"])
            return 0, lambda: repay_debt(world, o.actor, p["amount"])
        if p: raise ValueError("Restructuring takes no extra parameters")
        restructure(candidate, o.actor)
        return 0, lambda: restructure(world, o.actor)
    if o.kind == "technology_access":
        from .calibration import SECTORS
        if not world.economy or set(p) != {"region", "sector"} or p["sector"] not in SECTORS or world.regions[p["region"]].owner != o.actor:
            raise ValueError("Technology access requires an owned region/sector")
        if c.treasury < 50: raise ValueError("Insufficient treasury")
        def operation():
            world.economy["regions"][p["region"]]["sectors"][p["sector"]]["technology"].update(access=True, discovery=True)
        return 50, operation
    if o.kind == "plan_project":
        from .projects import plan
        if set(p) != {"project_id", "region", "kind", "target", "amount"}:
            raise ValueError("Project order requires ID, region, kind, target and amount")
        candidate = economic_candidate(world)
        project = plan(candidate, p["project_id"], o.actor, p["region"], p["kind"], p["target"], p["amount"])
        def operation():
            world.projects[p["project_id"]] = copy.deepcopy(project)
            if required_systems and not any(s["name"] == "worldline.projects" for s in required_systems):
                required_systems.insert(0, {"name": "worldline.projects", "version": "1"})
        return 0, operation
    if o.kind == "fund_project":
        from .projects import fund
        if "project_id" not in p or set(p) - {"project_id", "allow_emergency", "instruments"}: raise ValueError("Funding order requires project ID and optional financing choices")
        project = world.projects[p["project_id"]]
        if project["country"] != o.actor or project.get("prepaid"):
            raise ValueError("Funding requires an owned, unreserved project")
        candidate = economic_candidate(world)
        options = {"allow_emergency": p.get("allow_emergency", False), "instruments": p.get("instruments")}
        fund(candidate, candidate.projects[p["project_id"]], **options)
        if world.economy and world.economy.get("stabilization_version") == 1:
            return 0, lambda: fund(world, project, **options)
        return project["cost"], lambda: fund(world, project, prepaid=True)
    if o.kind == "produce":
        if set(p) != {"resource", "quantity"}:
            raise ValueError("produce requires resource and quantity")
        resource, quantity = p["resource"], p["quantity"]
        if not isinstance(resource, str) or type(quantity) is not int or quantity <= 0:
            raise ValueError("Invalid production quantity/resource")
        key = f"{o.actor}:{resource}"
        used = world.production.get(key, 0)
        if used + quantity > c.industries.get(resource, 0):
            raise ValueError("Daily industrial capacity exceeded")
        cost = quantity * 2
        def operation():
            c.resources[resource] = c.resources.get(resource, 0) + quantity
            world.production[key] = used + quantity
    elif o.kind == "research":
        if set(p) != {"technology"}:
            raise ValueError("research requires technology")
        tech = p["technology"]
        if tech not in world.tech_requirements or tech in c.technologies:
            raise ValueError("Unknown or acquired technology")
        if not set(world.tech_requirements[tech]) <= set(c.technologies):
            raise ValueError("Missing technology prerequisites")
        cost = 100
        def operation():
            c.technologies.append(tech)
    elif o.kind == "move_army":
        if set(p) != {"army", "destination"}:
            raise ValueError("move_army requires army and destination")
        army, destination = p["army"], p["destination"]
        a = world.armies[army]
        r = world.regions[destination]
        if a.owner != o.actor or r.owner != o.actor or army in journeys or a.region == destination:
            raise ValueError("Movement requires owned army/territory and an idle army")
        start = world.regions[a.region]
        days = max(1, math.ceil(math.hypot(r.x-start.x, r.y-start.y) / a.speed_km_day))
        cost = math.ceil(a.personnel / 100) * days
        def operation():
            journeys[army] = {"destination": destination, "origin": a.region,
                "departure": world.date, "remaining": days, "duration": days}
    else:
        raise ValueError("Unsupported order kind")
    if c.treasury < cost:
        raise ValueError("Insufficient treasury")
    return cost, operation

