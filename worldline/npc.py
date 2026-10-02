"""Trusted NPC adapters: sensing, scheduling, councils and authoritative execution.

Only this adapter touches World. npc_decision receives an actor's detached reports
and perceived costs; actual constraints and consequences live in an execution
sidecar. No network, UI, LLM, or independent random source is used.
"""
import copy
import math
from datetime import timedelta
from .models import Order, canonical_date
from . import guardrails, order_execution, npc_state, npc_decision

VERSION = "1"
RECENT_LIMIT = 128  # Full decisions are emitted to the permanent event ledger.
ACTION_HOOKS = ("policy_proposal", "remonstrance", "lobby", "budget_request", "command_delay",
    "passive_execution", "conceal_information", "resign_threat", "coalition", "local_patrol")


def initialize(world, config=None):
    guardrails.require_stabilization(world)
    if world.npc_state is not None:
        raise ValueError("NPC decision engine is already enabled")
    state = copy.deepcopy(config) if config is not None else npc_state.initialize_state()
    issues = npc_state.validate(state, world.countries, world.regions, world.date)
    if issues:
        raise ValueError("; ".join(issues))
    world.npc_state, world.schema_version = state, 3
    state.setdefault("adapter", {"last_sensing_date": None, "last_settlement": None,
                                 "record_sequence": 1, "pending": {}})
    for government in state["governments"].values():
        if government.get("next_due") is None:
            government["next_due"] = world.date
    return state


def _state(world):
    if world.npc_state is None:
        raise ValueError("Enable NPC decision engine first")
    return world.npc_state


def _number(value, default=0):
    try:
        return float(value) if type(value) in (int, float) and math.isfinite(value) else default
    except OverflowError:
        return default


def _clip(value, low=0, high=1):
    return min(high, max(low, value))


def _economic_report(world, person):
    """Delegated domestic reporting only. Never disclose foreign hidden state."""
    country = person["country"]
    c, e = world.countries[country], world.economy["countries"][country]
    f, cr = e["finance"], e["credit"]
    regions = [r for r in sorted(world.regions) if world.regions[r].owner == country]
    revenue = f.get("total_revenue", max(1, c.gdp * e["policy"]["tax_rate"] / 12))
    # An initial economy has equipment but has not yet published measured GDP.
    if not c.gdp and "total_revenue" not in f:
        equipment = sum(s["capital_stock"] for r in regions for s in world.economy["regions"][r]["sectors"].values())
        revenue = equipment * e["calibration"]["productivity"] * e["policy"]["tax_rate"] / 12
    handling = person.get("abilities", {}).get("intelligence", .5)
    finance_skill = person.get("abilities", {}).get("finance", .5)
    precision = .55 + .40 * (handling + finance_skill) / 2
    def reported(x):
        # Resolution models imperfect administrative accounts without free noise.
        unit = max(1, abs(x) * (1 - precision) * .12)
        return round(x / unit) * unit if x else 0
    service = f.get("debt_service_requested", cr.get("debt_service", 0) / 12)
    return {"country": country, "gdp": reported(c.gdp), "revenue": reported(revenue),
        "reserves": reported(c.treasury), "debt": reported(c.debt), "debt_service": reported(service),
        "fiscal_risk": cr.get("default_risk", 0), "credit_state": cr["distress_state"],
        "borrowing_cost": cr.get("borrowing_cost", .04),
        "civilian_labour": sum(world.economy["regions"][r]["labour"].get("employed", 0) for r in regions),
        "skilled_labour": sum(world.economy["regions"][r]["labour"].get("skilled", 0) for r in regions),
        "mobilized": sum(world.economy["regions"][r]["labour_state"]["mobilized"] for r in regions),
        "bottlenecks": copy.deepcopy(e.get("report", {}).get("bottlenecks", [])),
        "growth": e.get("real_growth_estimate", 0), "policy": copy.deepcopy(e["policy"]),
        "regions": regions, "population": reported(c.population),
        "infrastructure": {r: copy.deepcopy(world.regions[r].infrastructure) for r in regions}}


def sense(world):
    state = _state(world)
    for actor, person in sorted(state["persons"].items()):
        if not person.get("active", True):
            continue
        skill = person.get("abilities", {}).get("intelligence", .5)
        npc_state.receive_report(state, actor, "economy", _economic_report(world, person), world.date,
            "domestic administrative accounts", accuracy=.55 + .4 * skill,
            confidence=.55 + .4 * skill, reliability=.8)
    state["adapter"]["last_sensing_date"] = world.date


def _new_record(world, subject, category, facts, source, *, observers=None, interpretation=None,
                salience=.35, personal_impact=0, group_impact=0, emotional_valence=0):
    state = _state(world)
    adapter = state["adapter"]
    event_id = f"record-{adapter['record_sequence']}"
    while event_id in state["records"]:
        adapter["record_sequence"] += 1
        event_id = f"record-{adapter['record_sequence']}"
    adapter["record_sequence"] += 1
    npc_state.record_fact(state, event_id, world.date, subject, category, facts, source)
    targets = sorted(observers if observers is not None else [a for a, p in state["persons"].items()
        if p["country"] == next((c for c, g in state["governments"].items() if g.get("ruler") == subject), None)])
    for actor in targets:
        npc_state.deliver_record(state, actor, event_id, as_of=world.date, accuracy=1, confidence=.9, reliability=.9)
        npc_state.remember(state, actor, event_id, interpretation=interpretation or {}, salience=salience,
            reliability=.9, personal_impact=personal_impact, group_impact=group_impact,
            emotional_valence=emotional_valence)
        state["adapter"]["pending"].setdefault(actor, []).append(category)
    return {"system": "npc", "kind": "ruler_record", "record_id": event_id,
            "subject": subject, "category": category, "facts": copy.deepcopy(facts), "source": source}


def record_outcome(world, subject, category, facts, source="external event engine", **kwargs):
    """Trusted fact delivery hook for domains not simulated yet; never sets reputation.

    Engine records this call in its immutable ledger. Observers must be explicitly
    listed for foreign/private events; an NPC cannot invent outcomes by calling it.
    """
    return _new_record(world, subject, category, facts, source, **kwargs)


def ingest_player_order(world, result):
    state = _state(world)
    if result.get("status") != "accepted":
        return []
    order = result["order"]
    country = order["actor"]
    government = state["governments"].get(country)
    if not government:
        return []
    subject, kind, params = government["ruler"] or f"government:{country}", order["kind"], order["params"]
    facts = {"order_id": order["id"], "policy": copy.deepcopy(params), "implemented": True}
    category = "class_policy"
    if kind == "economic_policy":
        if "tax_rate" in params:
            category = "tax_burden"
            facts["burden"] = params["tax_rate"] / .6
        elif params.get("welfare", 0):
            category = "disaster_response"
            facts["kindness"] = .2
    elif kind == "mobilize":
        category = "military_treatment"
    elif kind == "war_preparation":
        category = "military_treatment"
    return [_new_record(world, subject, category, facts, f"order ledger:{order['id']}",
                       observers=[a for a, p in state["persons"].items() if p["country"] == country])]


def react_to_order(world, result):
    """Lawful reaction hooks cannot silently undo an accepted player command."""
    if result.get("status") != "accepted":
        return []
    state = _state(world)
    order = result["order"]
    country = order["actor"]
    government = state["governments"].get(country)
    if not government:
        return []
    params, kind = order["params"], order["kind"]
    perceived_outcomes = {}
    tags = [kind]
    if kind == "economic_policy":
        scale = max(1, sum(v for v in params.values() if type(v) is int))
        perceived_outcomes = {"security": params.get("military", 0) / scale,
            "education": params.get("education", 0) / scale,
            "prosperity": (params.get("infrastructure", 0) + params.get("subsidy", 0)) / scale,
            "fiscal": -sum(v for v in params.values() if type(v) is int) / max(1, world.countries[country].gdp)}
    elif kind in ("war_preparation", "mobilize"):
        tags.append("war")
        perceived_outcomes = {"security": .5, "prestige": .3, "fiscal": -.5, "prosperity": -.3}
    option = {"id": order["id"], "kind": kind, "country": country, "params": copy.deepcopy(params),
        "tags": tags, "outcomes": perceived_outcomes, "institutional_feasibility": {"feasible": True}}
    events = []
    for actor in sorted(set(a for a in [government["ruler"], *government.get("cabinet", [])] if a)):
        view = npc_state._actor_read_view(state, actor, world.date)
        response = npc_decision.reaction(view, option)
        action = {"id": f"reaction-{order['id']}-{actor}", "date": world.date,
            "country": country, "actor": actor, "kind": "player_order_reaction",
            "order_id": order["id"], "response": response,
            "implemented_command_preserved": True, "execution": {"status": "hook_only"}}
        state["actions"] = (state["actions"] + [action])[-RECENT_LIMIT:]
        events.append({"system": "npc", "kind": "npc_reaction", "action": action})
    return events


def _settlement_records(world):
    state = _state(world)
    if state["adapter"]["last_settlement"] == world.economy["last_settlement_date"]:
        return []
    state["adapter"]["last_settlement"] = world.economy["last_settlement_date"]
    events = []
    for country, government in sorted(state["governments"].items()):
        e = world.economy["countries"][country]
        report = e.get("report", {})
        if not report:
            continue
        facts = {"performance": _clip(e.get("real_growth_estimate", 0) * 8, -1, 1),
            "gdp": world.countries[country].gdp, "growth": e.get("real_growth_estimate", 0),
            "requested_budget_balance": e["finance"].get("requested_budget_balance", 0),
            "fiscal_state": e["credit"]["distress_state"],
            "period_start": report["period_start"], "period_end": world.date,
            "civilian_labour": e["labour"].get("employed", 0)}
        # Same fact can be given a different interpretation by each recipient.
        event = _new_record(world, government["ruler"] or f"government:{country}", "economic_performance", facts,
                            "measured economic settlement", salience=.12,
                            observers=[a for a, p in state["persons"].items() if p["country"] == country])
        events.append(event)
    return events


def _perceived_finance(view, quote, amount):
    economy = view.get("beliefs", {}).get("economy", {})
    person = view["person"]
    bias = person.get("biases", {})
    traits = person.get("traits", {})
    observed_revenue = max(0, _number(economy.get("revenue")))
    cost_scale = observed_revenue * 12
    if not math.isfinite(cost_scale):
        cost_scale = observed_revenue
    risk = _clip(_number(economy.get("fiscal_risk"), .15))
    perceived_risk = _clip(risk * (1 - .35 * bias.get("overconfidence", 0)) - .1 * bias.get("optimism", 0))
    # Finance officials' stronger information handling makes this discount smaller.
    skill = person.get("abilities", {}).get("finance", .5)
    perceived_risk += skill * .15 * risk
    return {"physical_feasibility": {"feasible": quote["physical_feasibility"].get("feasible", quote["physical_feasibility"].get("possible", False))},
        "normal_financial_feasibility": {"feasible": quote["normal_financial_feasibility"]["feasible"]},
        "emergency_financial_feasibility": {"feasible": quote["emergency_financial_feasibility"]["feasible"]},
        "perceived_state": {"cost": amount * (1 - .25 * bias.get("optimism", 0)),
            "risk": _clip(perceived_risk), "revenue": observed_revenue,
            "cost_scale": max(1, cost_scale),
            "reserves": max(0, _number(economy.get("reserves"))), "opportunity_cost": amount / max(1, cost_scale)},
        "credit_state": economy.get("credit_state", "unknown"),
        "labour_constraints": copy.deepcopy(economy.get("bottlenecks", [])),
        "basis": "actor's dated economic report, not objective execution consequences"}


def options_for(world, actor, *, _view=None):
    """Generate proposals from reports, attach real feasibility without hidden costs.

    Return public options + private execution quotes. The quotes never enter decide.
    """
    state = _state(world)
    # The public boundary remains detached. Trusted synchronous callers may
    # provide a read projection prepared at this exact state point; it is never
    # stored or carried across an authoritative operation.
    view = npc_state.actor_view(state, actor, world.date) if _view is None else _view
    person, beliefs = view["person"], view.get("beliefs", {})
    country = person["country"]
    observed = beliefs.get("economy", {})
    revenue = max(0, _number(observed.get("revenue")))
    service = max(0, _number(observed.get("debt_service")))
    budget = max(0, revenue - service)
    options = [{"id": "status_quo", "kind": "status_quo", "country": country, "params": {},
        "tags": ["continuity"], "outcomes": {"fiscal": .05},
        "institutional_feasibility": {"feasible": True}}]
    actual = {}
    # Spending scales to information available now, never to a future GDP target.
    plans = {
        "development": {"administration": .15, "military": .12, "education": .25, "infrastructure": .30, "subsidy": .12, "welfare": .03},
        "industrialization": {"administration": .15, "military": .12, "education": .22, "infrastructure": .18, "subsidy": .28, "welfare": .02},
        "military_expansion": {"administration": .15, "military": .70, "education": .06, "infrastructure": .05, "subsidy": .13, "welfare": .01},
        "fiscal_consolidation": {"administration": .12, "military": .10, "education": .07, "infrastructure": .03, "subsidy": .02, "welfare": .04},
    }
    outcomes = {
        "development": {"prosperity": .55, "education": .75, "fiscal": -.12, "security": -.10, "prestige": .12},
        "industrialization": {"prosperity": .75, "education": .35, "fiscal": -.20, "security": .15, "prestige": .25},
        "military_expansion": {"prosperity": -.35, "education": -.20, "fiscal": -.55, "security": .60, "prestige": .50},
        "fiscal_consolidation": {"prosperity": -.12, "education": -.10, "fiscal": .85, "security": -.18, "prestige": -.10},
    }
    month = canonical_date(world.date).year * 12 + canonical_date(world.date).month
    for name, shares in plans.items():
        params = {key: int(budget * share) for key, share in shares.items()}
        params.update(infrastructure_target=("railways", "electricity", "ports", "sanitation")[month % 4],
                      subsidy_sector=("machinery", "heavy_industry")[month % 2])
        amount = sum(params[k] for k in shares)
        quote = guardrails.estimated_fiscal_risk(order_execution.economic_view(world), country, amount, allow_emergency=True)
        financial = _perceived_finance(view, quote, amount)
        # Authorizing a future budget costs no cash today. Actual monthly payments
        # remain subject to the unchanged Economy v3 financing settlement.
        financial["budget_payment_forecast"] = {
            "normal_feasible": financial["normal_financial_feasibility"]["feasible"],
            "emergency_feasible": financial["emergency_financial_feasibility"]["feasible"]}
        financial["authorization_only"] = True
        financial["physical_feasibility"] = {"feasible": True}
        financial["normal_financial_feasibility"] = {"feasible": True, "immediate_cost": 0}
        options.append({"id": name, "kind": "economic_policy", "country": country, "params": params,
            "tags": [name], "outcomes": copy.deepcopy(outcomes[name]),
            "financial": financial,
            "institutional_feasibility": {"feasible": True}})
        actual[name] = quote
    diplomacy = beliefs.get("diplomacy", {})
    target = diplomacy.get("target")
    if target in world.countries and target != country:
        threat = _clip(_number(diplomacy.get("threat", 0)))
        own = max(1, _number(diplomacy.get("own_strength", 1)))
        enemy = max(1, _number(diplomacy.get("enemy_strength", own)))
        regions = observed.get("regions", [])
        regions = [r for r in regions if isinstance(r, str)] if isinstance(regions, list) else []
        if regions:
            region = regions[0]
            current = _number(observed.get("mobilized", 0))
            # Preparing an existing force repeatedly is not free; recurring finance
            # and continued civilian labour costs remain visible.
            manpower = int(min(max(10, _number(observed.get("population", 0)) * .015),
                               max(10, _number(observed.get("civilian_labour", 0)) * .08)))
            amount = max(1, int(revenue * (1.5 + 2 * threat)))
            resources = {"raw_materials": manpower * .01}
            if region in world.regions and world.regions[region].owner == country:
                quote = guardrails.can_fund_war(order_execution.economic_view(world), country, amount, region=region,
                    manpower=max(0, manpower - current), resources=resources, allow_emergency=True)
            else:
                quote = guardrails.estimated_fiscal_risk(order_execution.economic_view(world), country, amount, allow_emergency=True)
                quote["physical_feasibility"] = {"feasible": False,
                    "reason": "reported recruitment region is unknown or outside jurisdiction", "region": region}
                quote["execution_feasibility"] = False
            options.append({"id": "war_preparation", "kind": "war_preparation", "country": country,
                "params": {"target": target, "region": region, "amount": amount, "manpower": manpower,
                           "resources": resources, "allow_emergency": True},
                "tags": ["war", "security", "prestige"],
                "belief_context": {"own_strength": own, "enemy_strength": enemy, "threat": threat},
                "outcomes": {"security": _clip(threat * own / enemy, -1, 1),
                    "prestige": .7, "fiscal": -.8, "prosperity": -.55, "education": -.25},
                "financial": _perceived_finance(view, quote, amount),
                "institutional_feasibility": {"feasible": True}})
            actual["war_preparation"] = quote
        options.append({"id": "diplomatic_engagement", "kind": "diplomatic_engagement", "country": country,
            "params": {"target": target}, "tags": ["diplomacy"],
            "outcomes": {"security": .25 * (1 - threat), "prosperity": .2, "fiscal": .1, "prestige": .05},
            "belief_context": {"threat": threat, "enemy_strength": enemy, "own_strength": own},
            "institutional_feasibility": {"feasible": True}})
    return view, options, actual


def _authority(state, actor, country, kind):
    person = state["persons"].get(actor)
    if not person or person["country"] != country or not person.get("active", True):
        return False
    scopes = person.get("authority", [])
    if kind not in scopes and "all" not in scopes:
        return False
    government = state["governments"].get(country, {})
    if actor == government.get("ruler") or actor in government.get("cabinet", []):
        return True
    # Local authority must be explicitly delegated, never inferred from opinion.
    return kind == "local_action" and "local_action" in scopes


def execute_intent(world, actor, option, *, action_id=None):
    state = _state(world)
    country, kind = option["country"], option["kind"]
    if actor not in state["persons"] or state["persons"][actor]["country"] != country:
        return {"status": "rejected", "reason": "unknown actor or country jurisdiction", "actor": actor}
    if kind != "status_quo" and not _authority(state, actor, country, kind):
        return {"status": "rejected", "reason": "actor lacks delegated institutional authority",
                "actor": actor, "country": country, "kind": kind}
    if kind == "status_quo":
        return {"status": "accepted", "actor": actor, "country": country, "kind": kind}
    if kind == "local_action":
        person, params = state["persons"][actor], option.get("params", {})
        region = params.get("region")
        if (set(params) != {"region", "action", "details"} or person.get("region") != region
            or region not in world.regions or world.regions[region].owner != country
            or params["action"] not in ACTION_HOOKS or not isinstance(params["details"], dict)):
            return {"status": "rejected", "actor": actor,
                    "reason": "local action exceeds delegated region or supported action hook"}
        # A hook is an attempted institutional action, not free national spending,
        # a covert combat resolver, or an implemented coup/revolution.
        return {"status": "accepted", "actor": actor, "country": country, "kind": kind,
                "hook_only": True, "request": copy.deepcopy(params)}
    events = []
    old_cash, old_debt = world.countries[country].treasury, world.countries[country].debt
    if action_id is None:
        action_id = f"npc-{country}-{state['next_decision_id']}"
        state["next_decision_id"] += 1
    result = order_execution.execute(world, Order(action_id,
        country, kind, copy.deepcopy(option["params"])), emit=lambda k, p: events.append({"kind": k, "payload": p}))
    f = world.economy["countries"][country]["finance"]
    f["interim_net_cashflow"] = f.get("interim_net_cashflow", 0) + world.countries[country].treasury - old_cash
    f["interim_debt_change"] = f.get("interim_debt_change", 0) + world.countries[country].debt - old_debt
    f["reserves"] = world.countries[country].treasury
    return {**result, "actor": actor, "country": country, "kind": kind, "execution_events": events}


def _vote_weight(state, actor):
    person = state["persons"][actor]
    weight = .5 + person.get("abilities", {}).get("persuasion", .5)
    for group_id in person.get("groups", []):
        group = state["groups"].get(group_id, {})
        weight += group.get("influence", .5) * group.get("internal_cohesion", group.get("cohesion", .5))
    return weight


def _remember_interpretation(state, actor, event_id, action):
    role = state["persons"][actor].get("role", "")
    if "military" in role or "army" in role or "navy" in role:
        interpretation = {"lesson": "mobilization can secure the state", "security": .25,
                          "prestige": .15, "tags": ["war"]}
    else:
        interpretation = {"lesson": "funding and civilian labour have an opportunity cost",
                          "fiscal": -.2, "prosperity": -.1, "tags": ["war"]}
    npc_state.deliver_record(state, actor, event_id, as_of=action["date"])
    npc_state.remember(state, actor, event_id, interpretation=interpretation, salience=.45, group_impact=.2)


def _run_council(world, country, recommendations, options_by_actor, actual_by_actor):
    state = _state(world)
    gov = state["governments"][country]
    members = [a for a in dict.fromkeys([gov["ruler"], *gov.get("cabinet", [])]) if a in recommendations]
    if not members:
        return []
    rule = gov.get("decision_rule", "cabinet")
    ruler = gov["ruler"]
    if rule == "executive" and ruler in recommendations:
        chosen = recommendations[ruler]["selected"]
        proposer = ruler
    else:
        votes = {}
        for actor in members:
            selection = recommendations[actor]["selected"]
            if selection is not None:
                votes[selection] = votes.get(selection, 0) + _vote_weight(state, actor)
        if not votes:
            return []
        chosen = max(sorted(votes), key=lambda key: votes[key])
        proposer = next(a for a in members if recommendations[a]["selected"] == chosen)
    option = next((o for o in options_by_actor[proposer] if o["id"] == chosen), None)
    if option is None:
        return []
    # Advice is not authority. The configured decision maker authorizes execution.
    executor = (ruler or proposer) if option["kind"] == "status_quo" or _authority(state, ruler, country, option["kind"]) else next(
        (a for a in members if _authority(state, a, country, option["kind"])), None)
    action = {"id": f"action-{country}-{state['next_decision_id']}", "date": world.date, "country": country,
        "proposer": proposer, "executor": executor, "option": chosen, "kind": option["kind"],
        "coalition": {a: recommendations[a]["selected"] for a in members},
        "perceived": copy.deepcopy(option.get("financial", {}).get("perceived_state", {})),
        "actual": copy.deepcopy(actual_by_actor[proposer].get(chosen, {})),
        "reactions": {a: npc_decision.reaction(npc_state._actor_read_view(state, a, world.date), option,
                            recommendations[a]) for a in members}}
    result = execute_intent(world, executor, option, action_id=action["id"]) if executor else {
        "status": "rejected", "reason": "no authorized decision maker"}
    action["execution"] = result
    state["actions"] = (state["actions"] + [action])[-RECENT_LIMIT:]
    events = [{"system": "npc", "kind": "npc_action", "action": copy.deepcopy(action)}]
    if result["status"] == "accepted" and option["kind"] == "war_preparation":
        key = f"{country}:war"
        pressure = world.pressures.setdefault(key, {"country": country, "risk": "war", "value": 0, "causes": []})
        pressure["value"] = min(100, pressure["value"] + 2)
        pressure["causes"] = [f"funded military preparation targeting {option['params']['target']}"]
        event = _new_record(world, ruler or f"government:{country}", "military_treatment", {"prepared": True,
            "amount": option["params"]["amount"], "target": option["params"]["target"]},
            f"NPC action ledger:{action['id']}", observers=[], salience=.4)
        for a in members:
            _remember_interpretation(state, a, event["record_id"], action)
            for group in state["persons"][a].get("groups", []):
                memory = next(m for m in state["memories"][a] if m["event_id"] == event["record_id"])
                npc_state.remember_group(state, group, event["record_id"], a,
                    interpretation=memory["interpretation"], salience=.45, group_impact=.2)
        events.append(event)
    return events


def tick(world, tick_event, rng):
    state = _state(world)
    adapter = state["adapter"]
    today = canonical_date(world.date)
    events = _settlement_records(world)
    if today.day == 1 or adapter["last_sensing_date"] is None:
        sense(world)
    recommendations, options_by_actor, actual_by_actor = {}, {}, {}
    council_due = set()
    for gov in state["governments"].values():
        if gov.get("autonomous", False) and (gov.get("next_due") or world.date) <= world.date:
            council_due.update(a for a in [gov["ruler"], *gov.get("cabinet", [])] if a)
    for actor, person in sorted(state["persons"].items()):
        schedule = state["schedules"].setdefault(actor, {"next_due": world.date, "last_evaluated": None})
        due = (schedule.get("next_due") or world.date) <= world.date or actor in council_due
        triggered = bool(adapter["pending"].get(actor))
        if not due and not triggered:
            continue
        if not person.get("active", True):
            schedule["next_due"] = (today + timedelta(days=365)).isoformat()
            continue
        for target in list(state["relationships"].get(actor, {})):
            npc_state.evaluate_ruler(state, actor, target, world.date)
        ruler = state["governments"].get(person["country"], {}).get("ruler")
        if ruler:
            npc_state.evaluate_ruler(state, actor, ruler, world.date)
        view = npc_state._actor_read_view(state, actor, world.date)
        view, options, actual = options_for(world, actor, _view=view)
        decision = npc_decision.decide(view, options, rng)
        decision.update(id=f"decision-{state['next_decision_id']}", date=world.date,
                        country=person["country"], actor=actor)
        state["next_decision_id"] += 1
        state["decisions"] = (state["decisions"] + [decision])[-RECENT_LIMIT:]
        recommendations[actor], options_by_actor[actor], actual_by_actor[actor] = decision, options, actual
        importance = person.get("importance", .5)
        interval = 30 if importance >= .8 else 90 if importance >= .4 else 365
        schedule.update(last_evaluated=world.date, next_due=(today + timedelta(days=interval)).isoformat())
        adapter["pending"].pop(actor, None)
        events.append({"system": "npc", "kind": "npc_decision", "decision": copy.deepcopy(decision)})
    for country, gov in sorted(state["governments"].items()):
        if not gov.get("autonomous", False):
            continue
        if (gov.get("next_due") or world.date) > world.date:
            continue
        events.extend(_run_council(world, country, recommendations, options_by_actor, actual_by_actor))
        month = today.year * 12 + today.month
        gov["next_due"] = f"{month // 12:04d}-{month % 12 + 1:02d}-01"
    return events
