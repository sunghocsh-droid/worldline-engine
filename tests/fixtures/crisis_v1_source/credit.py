"""Finite public credit and explicit emergency finance, without political vetoes.

Amounts use the economy's existing game account unit. Rates are annual fractions.
Credit assessments are deterministic, read-only information; only execution changes
the world. A bad decision remains selectable when real financing/resources exist.
"""
import copy
import math

VERSION = 1
INSTRUMENTS = (
    "reserve_depletion", "emergency_taxation", "domestic_war_bonds",
    "foreign_borrowing", "forced_loans", "requisition", "delayed_payments",
    "asset_sales", "monetary_financing",
)
DEFAULT_INSTRUMENTS = INSTRUMENTS[:-1]  # monetary financing is an opt-in future hook
DEBT_INSTRUMENTS = {"domestic_war_bonds", "foreign_borrowing", "forced_loans"}


def _positive_amount(value):
    if type(value) is not int or value < 0:
        raise ValueError("Finance amount must be a nonnegative integer")
    return value


def _country(world, country):
    if not world.economy or country not in world.economy["countries"]:
        raise ValueError("Credit requires an initialized country economy")
    return world.countries[country], world.economy["countries"][country]


def _regions(world, country):
    return [r for r in sorted(world.economy["regions"]) if world.regions[r].owner == country]


def _economic_base(world, country):
    c, e = _country(world, country)
    # Before the first GDP settlement use actual equipment's productive capacity,
    # rather than treating a zero initialization/calibration GDP as a penniless state.
    return max(1.0, float(c.gdp or sum(
        s["capital_stock"] for r in _regions(world, country)
        for s in world.economy["regions"][r]["sectors"].values()) * e["calibration"]["productivity"]))


def _at_war(world, country):
    return any(country in (v.get("a"), v.get("b")) and v.get("status") == "war"
               for v in world.relations.values()) or any(
        s["kind"] == "war_disruption" and world.regions[s["region"]].owner == country
        and s["start_date"] <= world.date < s["end_date"]
        for s in world.economy.get("shocks", {}).values())


def _annual_revenue(world, country):
    c, e = _country(world, country)
    f = e.get("finance", {})
    if "total_revenue" in f:
        return max(0.0, f["total_revenue"] * 365 / max(1, f.get("period_days", 365 / 12)))
    return _economic_base(world, country) * (e["policy"]["tax_rate"] + .01)


def _stock_value(world, country):
    from .economy import PRICE
    total = 0.0
    for key in _regions(world, country):
        stocks = world.economy["regions"][key]["stocks"]
        total += stocks.get("energy", 0) * PRICE["energy"]
        total += stocks.get("raw_materials", 0) * PRICE["mining"]
        total += sum(v * PRICE.get(s, 1) for s, v in stocks["goods"].items())
    return total


def _new_state(world, country):
    c, e = _country(world, country)
    foreign = min(c.debt, max(0, int(e.get("finance", {}).get("foreign_debt", 0))))
    rate = e.get("finance", {}).get("interest_rate", e["policy"]["monetary_rate"])
    base = _economic_base(world, country)
    capital = sum(s["capital_stock"] for r in _regions(world, country)
                  for s in world.economy["regions"][r]["sectors"].values())
    # Emergency commitments are finite stocks, not fresh annual gifts. Tax/forced
    # loan limits represent a one-off claim on the existing civilian resource base.
    capacities = {
        "emergency_taxation": int(base * .035),
        "domestic_war_bonds": int(base * .18),
        "foreign_borrowing": int(base * .12 * e["policy"]["foreign_investment_openness"]),
        "forced_loans": int(capital * .04),
        "requisition": int(min(base * .04, _stock_value(world, country))),
        "delayed_payments": int(base * .05),
        "asset_sales": int(capital * .025),
        "monetary_financing": int(base * .02),
    }
    return {
        "version": VERSION, "creditworthiness": 75.0,
        "domestic_debt": c.debt - foreign, "foreign_debt": foreign,
        "domestic_rate": rate, "foreign_rate": rate,
        "arrears": 0, "default_count": 0, "missed_service_days": 0,
        "distress_state": "normal", "restructuring": False,
        "recent_balances": [], "emergency_capacity": capacities,
        "emergency_used": dict.fromkeys(capacities, 0),
        "financing_history": [], "civilian_extraction": 0.0,
        "inflation_risk_stock": 0.0, "social_cost_stock": 0.0,
        "financial_access": {name: True for name in capacities},
        "public_asset_claims": capacities["asset_sales"],
        "hooks": {}, "last_reasons": [], "arrears_paid": 0,
    }


def initialize(world):
    """Explicit upgrade preserves all old debt, cash and economy/project records."""
    if not world.economy:
        raise ValueError("Credit requires an economy")
    for country, e in sorted(world.economy["countries"].items()):
        if "credit" not in e:
            e["credit"] = _new_state(world, country)
            metrics = _metrics(world, country, e["credit"])
            e["credit"]["creditworthiness"] = metrics["target_creditworthiness"]
            refresh(world, country)
        elif e["credit"].get("version") != VERSION:
            raise ValueError("Unsupported government credit version")


def _state(world, country):
    _, e = _country(world, country)
    state = e.get("credit")
    if state is None:
        return _new_state(world, country)
    if state.get("version") != VERSION:
        raise ValueError("Unsupported government credit version")
    return state


def _metrics(world, country, state):
    c, e = _country(world, country)
    base = _economic_base(world, country)
    annual_revenue = _annual_revenue(world, country)
    leverage = c.debt / base
    service = state["domestic_debt"] * state["domestic_rate"] + state["foreign_debt"] * state["foreign_rate"]
    service_ratio = service / max(1, annual_revenue)
    recent = state.get("recent_balances", [])
    deficit = max(0, -sum(recent) / max(1, len(recent))) * 12 / max(1, annual_revenue)
    reserve_coverage = c.treasury / max(1, annual_revenue)
    war = _at_war(world, country)
    instability = 1 - c.stability / 100
    unpaid_ratio = state["arrears"] / max(1, annual_revenue)
    historical_default = min(2, state["default_count"])
    burden = (.28 * leverage + .35 * service_ratio + .30 * deficit
              + .65 * unpaid_ratio + .20 * war + .25 * instability
              + .30 * historical_default + .20 * state["social_cost_stock"])
    # Reserves cushion, but cannot make even arbitrarily large debts riskless.
    burden /= 1 + .20 * min(2, reserve_coverage)
    risk = -math.expm1(-max(0.0, burden))
    target = 100 * (1 - risk)
    confidence = min(state["creditworthiness"], target) / 100
    ceiling = (base * .55 + annual_revenue * 3) * confidence ** 2
    if state["distress_state"] == "default":
        ceiling = 0  # failed contractual payments close normal lender access
    capacity = max(0, math.floor(ceiling - c.debt))
    spread = (.015 + .06 * leverage / (1 + leverage)
              + .12 * service_ratio / (1 + service_ratio) + .08 * risk
              + .025 * historical_default + .06 * unpaid_ratio / (1 + unpaid_ratio))
    cost = e["policy"]["monetary_rate"] + spread
    reasons = [
        {"factor": "debt_to_gdp", "value": leverage, "risk_contribution": .28 * leverage},
        {"factor": "debt_service_to_revenue", "value": service_ratio, "risk_contribution": .35 * service_ratio},
        {"factor": "recent_deficit_to_revenue", "value": deficit, "risk_contribution": .30 * deficit},
        {"factor": "reserve_coverage", "value": reserve_coverage, "risk_cushion": .20 * min(2, reserve_coverage)},
        {"factor": "war", "value": war, "risk_contribution": .20 * war},
        {"factor": "instability", "value": instability, "risk_contribution": .25 * instability},
        {"factor": "arrears_to_revenue", "value": unpaid_ratio, "risk_contribution": .65 * unpaid_ratio},
        {"factor": "past_defaults", "value": historical_default, "risk_contribution": .30 * historical_default},
    ]
    return {"creditworthiness": state["creditworthiness"], "target_creditworthiness": target,
            "borrowing_capacity": capacity, "debt_ceiling": ceiling,
            "borrowing_cost": cost, "borrowing_spread": spread,
            "debt_service": service, "debt_service_ratio": service_ratio,
            "annual_revenue": annual_revenue, "debt_to_gdp": leverage,
            "default_risk": risk, "at_war": war, "reasons": reasons}


def _distress_transition(state, metrics):
    # Default reflects repeated failure to honour existing payments, not a debt/GDP
    # threshold. It never cancels a political decision or an already funded project.
    if (state["missed_service_days"] >= 180 and
        state["arrears"] > max(1, metrics["annual_revenue"] * .3) and
        state["distress_state"] not in ("default", "restructuring")):
        state["distress_state"] = "default"
        state["default_count"] += 1
    elif state.get("restructuring"):
        state["distress_state"] = "restructuring"
    elif state["distress_state"] != "default":
        state["distress_state"] = ("arrears" if state["arrears"] else
            "severe_stress" if metrics["default_risk"] > .7 else
            "stressed" if metrics["default_risk"] > .35 else "normal")


def refresh(world, country, days=0):
    """Credit falls over months and recovers over years; no random default trigger."""
    c, e = _country(world, country)
    if "credit" not in e:
        raise ValueError("Credit has not been initialized")
    state = e["credit"]
    metrics = _metrics(world, country, state)
    if days:
        if type(days) is not int or days < 0:
            raise ValueError("Credit elapsed days must be nonnegative")
        target = metrics["target_creditworthiness"]
        tau = 180 if target < state["creditworthiness"] else 1460
        state["creditworthiness"] += (target - state["creditworthiness"]) * -math.expm1(-days / tau)
        state["social_cost_stock"] *= math.exp(-days / 730)
        state["inflation_risk_stock"] *= math.exp(-days / 365)
    _distress_transition(state, metrics)
    metrics = _metrics(world, country, state)
    state.update({k: v for k, v in metrics.items() if k != "reasons"})
    state["last_reasons"] = metrics["reasons"]
    state["hooks"] = {
        "investment_multiplier": 1 / (1 + .5 * metrics["default_risk"] + state["civilian_extraction"]),
        "government_project_speed": state.get("government_payment_ratio", 1.0),
        "civilian_multiplier": 1 / (1 + state["civilian_extraction"]),
        "foreign_investment_multiplier": (1 - .7 * metrics["default_risk"]) * (.25 if state["distress_state"] == "default" else 1),
        "military_payment_ratio": state.get("military_payment_ratio", 1.0),
        "social_pressure": min(100.0, state["social_cost_stock"] * 50 + state["arrears"] / max(1, metrics["annual_revenue"]) * 20),
    }
    key = f"{country}:revolution"
    old_pressure = world.pressures.get(key, {"country": country, "risk": "revolution", "value": 0, "causes": []})
    previous_component = state.get("last_social_pressure_component", 0.0)
    component = state["hooks"]["social_pressure"]
    if component or previous_component:
        causes = [v for v in old_pressure.get("causes", []) if not v.startswith("fiscal:")]
        if component:
            causes += ["fiscal: unpaid government obligations or emergency civilian extraction"]
        world.pressures[key] = {"country": country, "risk": "revolution",
            "value": min(100.0, max(0.0, old_pressure["value"] - previous_component) + component), "causes": causes}
        state["last_social_pressure_component"] = component
    e["finance"].update(government_debt=c.debt, foreign_debt=state["foreign_debt"],
        domestic_debt=state["domestic_debt"], reserves=c.treasury,
        creditworthiness=state["creditworthiness"], borrowing_capacity=metrics["borrowing_capacity"],
        borrowing_cost=metrics["borrowing_cost"], borrowing_spread=metrics["borrowing_spread"],
        debt_service_ratio=metrics["debt_service_ratio"], arrears=state["arrears"],
        distress_state=state["distress_state"])
    return copy.deepcopy(state)


def _emergency_available(world, country, state, metrics):
    _, e = _country(world, country)
    pools = {k: max(0, v - state["emergency_used"].get(k, 0))
             for k, v in state["emergency_capacity"].items()}
    for name in pools:
        if not state["financial_access"].get(name, False):
            pools[name] = 0
    # Foreign money requires actual foreign access, and repeated default closes it.
    if e["policy"]["foreign_investment_openness"] == 0 or state["distress_state"] == "default":
        pools["foreign_borrowing"] = 0
    pools["requisition"] = min(pools["requisition"], math.floor(_stock_value(world, country)))
    current_capital = math.fsum(s["capital_stock"] for r in _regions(world, country)
                               for s in world.economy["regions"][r]["sectors"].values())
    pools["asset_sales"] = min(pools["asset_sales"], max(0,
        math.floor(current_capital * .025) - state["emergency_used"]["asset_sales"]))
    # Assets cannot be sold a second time after their saleable allocation is gone.
    return pools


def _plan(world, country, amount, allow_emergency, instruments, use_reserves=True):
    c, _ = _country(world, country)
    state = _state(world, country)
    metrics = _metrics(world, country, state)
    if instruments is not None and (not isinstance(instruments, (list, tuple)) or
                                   not all(isinstance(v, str) for v in instruments)):
        raise ValueError("Emergency instruments must be an ordered list")
    names = list(DEFAULT_INSTRUMENTS if instruments is None else instruments)
    if len(set(names)) != len(names) or set(names) - set(INSTRUMENTS):
        raise ValueError("Unknown or duplicate emergency financing instrument")
    remaining = amount
    sources = {"reserves": min(c.treasury, remaining) if use_reserves else 0}
    remaining -= sources["reserves"]
    sources["normal_borrowing"] = min(metrics["borrowing_capacity"], remaining)
    remaining -= sources["normal_borrowing"]
    pools = _emergency_available(world, country, state, metrics)
    if allow_emergency:
        for name in names:
            if name == "reserve_depletion":
                continue  # same treasury already consumed; never counted twice
            take = min(pools[name], remaining)
            if take:
                sources[name] = take
                remaining -= take
    return sources, remaining, pools, metrics


def assess(world, country, amount, purpose="general", allow_emergency=True,
           instruments=None, perceived=None, physical_constraints=None):
    """Quote real constraints alongside separately supplied actor perceptions.

    physical_constraints is an optional list of already computed resource shortages;
    neither cost/risk perceptions nor political motivations alter the real quote.
    """
    amount = _positive_amount(amount)
    c, e = _country(world, country)
    sources, shortfall, pools, metrics = _plan(world, country, amount, allow_emergency, instruments,
                                            use_reserves=purpose != "borrowing")
    state = _state(world, country)
    normal_available = (c.treasury if purpose != "borrowing" else 0) + metrics["borrowing_capacity"]
    debt_increase = sources.get("normal_borrowing", 0) + sum(sources.get(k, 0) for k in DEBT_INSTRUMENTS)
    emergency = sum(v for k, v in sources.items() if k not in ("reserves", "normal_borrowing"))
    extraction = sum(sources.get(k, 0) for k in ("emergency_taxation", "forced_loans", "requisition", "asset_sales"))
    base = _economic_base(world, country)
    projected = copy.copy(world)
    projected.countries = dict(world.countries)
    projected.countries[country] = copy.copy(c)
    projected.countries[country].debt += debt_increase
    projected.countries[country].treasury -= sources["reserves"]
    projected.economy = dict(world.economy)
    projected.economy["countries"] = dict(world.economy["countries"])
    projected.economy["countries"][country] = dict(e)
    projected_credit = copy.deepcopy(state)
    projected.economy["countries"][country]["credit"] = projected_credit
    normal = sources.get("normal_borrowing", 0)
    foreign = (round(normal * .25 * e["policy"]["foreign_investment_openness"])
               if state["financial_access"].get("foreign_borrowing", False) else 0)
    _record_loan(projected_credit, normal - foreign, metrics["borrowing_cost"])
    _record_loan(projected_credit, foreign, metrics["borrowing_cost"] + .01, True)
    for name in sources:  # same stable order as the execution's weighted coupon updates
        if name not in DEBT_INSTRUMENTS:
            continue
        if sources.get(name, 0):
            _record_loan(projected_credit, sources[name], metrics["borrowing_cost"] +
                (.04 if name == "forced_loans" else .025), name == "foreign_borrowing")
    projected_credit["arrears"] += sources.get("delayed_payments", 0)
    pressure_increase = min(100.0, (extraction + sources.get("delayed_payments", 0)) / base * 100)
    projected_credit["social_cost_stock"] += pressure_increase / 50
    projected_metrics = _metrics(projected, country, projected_credit)
    _distress_transition(projected_credit, projected_metrics)
    projected_metrics = _metrics(projected, country, projected_credit)
    consequences = {
        "debt_increase": debt_increase,
        "domestic_debt_increase": debt_increase - foreign - sources.get("foreign_borrowing", 0),
        "foreign_debt_increase": foreign + sources.get("foreign_borrowing", 0),
        "closing_reserves": c.treasury - sources["reserves"],
        "annual_debt_service_increase": projected_metrics["debt_service"] - metrics["debt_service"],
        "new_borrowing_cost": projected_metrics["borrowing_cost"],
        "civilian_income_cost": extraction,
        "creditworthiness_change_estimate": projected_metrics["target_creditworthiness"] - state["creditworthiness"],
        "creditworthiness_target_after_financing": projected_metrics["target_creditworthiness"],
        "default_risk": projected_metrics["default_risk"],
        "inflation_risk": min(1.0, state["inflation_risk_stock"] + sources.get("monetary_financing", 0) / base * 10),
        "arrears_increase": sources.get("delayed_payments", 0),
        "social_pressure_increase": pressure_increase,
        "emergency_finance": emergency, "financing_sources": copy.deepcopy(sources),
        "funding_shortfall": shortfall,
        "projected_risk_reasons": copy.deepcopy(projected_metrics["reasons"]),
    }
    constraints = copy.deepcopy(physical_constraints or [])
    possible = not constraints
    result = {
        "country": country, "purpose": purpose, "amount": amount,
        "physical_feasibility": {"possible": possible, "feasible": possible, "constraints": constraints,
            "financing_access_exhausted": shortfall > 0 and normal_available + sum(pools.values()) < amount},
        "normal_financial_feasibility": {"possible": amount <= normal_available, "feasible": amount <= normal_available,
            "available": normal_available, "shortfall": max(0, amount - normal_available)},
        "emergency_financial_feasibility": {"possible": not shortfall and possible, "feasible": not shortfall and possible,
            "available": amount - shortfall, "shortfall": shortfall,
            "instruments_available": pools, "selected_sources": sources},
        "expected_consequences": consequences, "reasons": copy.deepcopy(metrics["reasons"]),
    }
    result["true_state"] = {"cost": amount, "risk": consequences["default_risk"],
        "consequences": copy.deepcopy(consequences)}
    result["perceived_state"] = (copy.deepcopy(perceived) if perceived is not None else
        {"cost": amount, "risk": consequences["default_risk"], "basis": "objective_quote", "bias_factors": []})
    return result


def can_borrow(world, country, amount, **kwargs):
    """Structured assessment, deliberately not an action-veto boolean."""
    quote = assess(world, country, _positive_amount(amount), purpose="borrowing", **kwargs)
    quote["requested_new_borrowing"] = amount
    return quote


def estimated_fiscal_risk(world, country):
    state = _state(world, country)
    metrics = _metrics(world, country, state)
    return {"true_state": {"default_risk": metrics["default_risk"],
        "distress_state": state["distress_state"], "reasons": metrics["reasons"]},
        "perceived_state": None, "borrowing_capacity": metrics["borrowing_capacity"]}


def _consume_stocks(world, country, amount):
    from .economy import PRICE
    remaining = float(amount)
    for key in _regions(world, country):
        stocks = world.economy["regions"][key]["stocks"]
        inventories = [(stocks, "energy", PRICE["energy"]),
                       (stocks, "raw_materials", PRICE["mining"])]
        inventories += [(stocks["goods"], s, PRICE.get(s, 1)) for s in sorted(stocks["goods"])]
        for inventory, name, price in inventories:
            take = min(inventory[name], remaining / price)
            inventory[name] -= take
            remaining -= take * price
            if inventory is stocks and name in ("energy", "raw_materials"):
                inputs = world.economy["regions"][key].get("inputs", {})
                label = "energy" if name == "energy" else "raw_materials"
                if inputs:
                    inputs[f"emergency_{label}_used"] = inputs.get(f"emergency_{label}_used", 0.0) + take
                    inputs[f"closing_{label}"] -= take
    if remaining > 1e-6:
        raise ValueError("Requisition exceeds available real inventories")


def _record_loan(state, amount, rate, foreign=False):
    bucket, rate_key = ("foreign_debt", "foreign_rate") if foreign else ("domestic_debt", "domestic_rate")
    old = state[bucket]
    state[rate_key] = (old * state[rate_key] + amount * rate) / max(1, old + amount)
    state[bucket] += amount


def finance_amount(world, country, amount, allow_emergency=False, instruments=None,
                   purpose="general", perceived=None, physical_constraints=None):
    """Execute a fully funded plan. Impossible quotes leave the world unchanged.

    This pays the action cost once. The caller may subsequently fund a resource-
    checked project as prepaid within its ordinary transaction. There is no privileged
    NPC bypass and no automatic cancellation solely because a quote is dangerous.
    """
    amount = _positive_amount(amount)
    _, e = _country(world, country)
    if "credit" not in e:
        raise ValueError("Initialize government credit before financing")
    quote = assess(world, country, amount, purpose, allow_emergency, instruments, perceived, physical_constraints)
    if not quote["physical_feasibility"]["possible"]:
        raise ValueError("Action lacks required physical resources")
    if not quote["emergency_financial_feasibility"]["possible"]:
        raise ValueError("Insufficient accessible financing; emergency means exhausted or not selected")
    c, e = _country(world, country)
    state = e["credit"]
    sources = quote["emergency_financial_feasibility"]["selected_sources"]
    rate = _metrics(world, country, state)["borrowing_cost"]
    loans, nondebt, arrears = 0, 0, 0
    normal = sources["normal_borrowing"]
    # Ordinary foreign share follows access, but no foreign access means domestic
    # borrowing only; emergency foreign borrowing has a separate finite commitment.
    foreign = (round(normal * .25 * e["policy"]["foreign_investment_openness"])
        if state["financial_access"].get("foreign_borrowing", False) else 0)
    _record_loan(state, normal - foreign, rate)
    _record_loan(state, foreign, rate + .01, True)
    loans += normal
    for name, value in sources.items():
        if name in ("reserves", "normal_borrowing"):
            continue
        state["emergency_used"][name] += value
        if name in DEBT_INSTRUMENTS:
            _record_loan(state, value, rate + (.04 if name == "forced_loans" else .025), name == "foreign_borrowing")
            loans += value
        elif name == "delayed_payments":
            state["arrears"] += value
            arrears += value
        else:
            nondebt += value
        if name == "requisition":
            _consume_stocks(world, country, value)
        elif name == "asset_sales":
            # Ownership of existing assets changes; no machinery vanishes and no
            # extra capital is created just because its ownership was transferred.
            state["public_asset_claims"] -= value
    c.debt += loans
    c.treasury += loans + nondebt + arrears - amount
    base = _economic_base(world, country)
    consequences = quote["expected_consequences"]
    state["civilian_extraction"] += consequences["civilian_income_cost"] / base
    state["social_cost_stock"] += consequences["social_pressure_increase"] / 50
    state["inflation_risk_stock"] += sources.get("monetary_financing", 0) / base * 10
    state["financing_history"].append({"date": world.date, "amount": amount, "purpose": purpose,
        "sources": copy.deepcopy(sources), "borrowing_cost": rate,
        "expected_consequences": copy.deepcopy(consequences)})
    state["financing_history"] = state["financing_history"][-36:]
    refresh(world, country)
    return {**quote, "borrowed": loans, "emergency_non_debt_financing": nondebt,
            "arrears_financing": arrears, "paid": amount, "sources": copy.deepcopy(sources)}


def settle(world, country, revenues, expenditures, days, allow_emergency=False, instruments=None):
    """Settle income, existing interest and affordable policy payments.

    Requested policy spending can exceed means. Unfinanced intentions do not build
    factories, become an infinite stream of unpaid supplier credit, or disappear from
    the explanation. Only missed existing debt interest and explicitly selected
    delayed payments add arrears. Cash and debt reconciliation are exact integers.
    """
    if type(days) is not int or days <= 0:
        raise ValueError("Settlement requires positive elapsed days")
    for mapping in (revenues, expenditures):
        for value in mapping.values():
            _positive_amount(value)
    c, e = _country(world, country)
    state = e.get("credit")
    if state is None:
        raise ValueError("Initialize government credit before settlement")
    revenue = dict(revenues)
    requested = dict(expenditures)
    opening_treasury, opening_debt = c.treasury, c.debt
    period_service = round(_metrics(world, country, state)["debt_service"] * days / 365)
    requested["debt_service"] = period_service
    total_revenue = sum(revenue.values())
    # Publish current earned income before quoting borrowing capacity.
    old_finance = e["finance"]
    e["finance"] = {**old_finance, "total_revenue": total_revenue, "period_days": days}
    c.treasury += total_revenue
    quote = assess(world, country, sum(requested.values()), "government_budget", allow_emergency, instruments)
    accessible = sum(requested.values()) - quote["expected_consequences"]["funding_shortfall"]
    paid = dict.fromkeys(requested, 0)
    # Existing contracts take payment priority; the government then distributes its
    # actual remaining policy budget proportionally instead of privileging one sector.
    paid["debt_service"] = min(period_service, accessible)
    remaining = max(0, accessible - paid["debt_service"])
    nonservice = sum(v for k, v in requested.items() if k != "debt_service")
    for key in sorted(requested):
        if key != "debt_service":
            paid[key] = min(requested[key], math.floor(remaining * requested[key] / max(1, nonservice)))
    rounding_left = remaining - sum(v for k, v in paid.items() if k != "debt_service")
    for key in sorted(requested):
        if key != "debt_service" and rounding_left and paid[key] < requested[key]:
            paid[key] += 1
            rounding_left -= 1
    result = finance_amount(world, country, sum(paid.values()), allow_emergency, instruments,
                            purpose="government_budget")
    missed = period_service - paid["debt_service"]
    if missed:
        state["arrears"] += missed
        state["missed_service_days"] += days
    elif not state["arrears"]:
        state["missed_service_days"] = 0
    requested_balance = total_revenue - sum(requested.values())
    state["recent_balances"] = (state["recent_balances"] + [requested_balance * (365 / 12) / days])[-12:]
    # Actual surplus repays unpaid contracts before becoming free reserves again.
    arrears_paid = min(c.treasury, state["arrears"])
    c.treasury -= arrears_paid
    state["arrears"] -= arrears_paid
    state["arrears_paid"] += arrears_paid
    if not state["arrears"] and state["restructuring"]:
        state["restructuring"] = False
        state["distress_state"] = "stressed"
        state["missed_service_days"] = 0
    project_requested = sum(requested.get(k, 0) for k in ("education", "infrastructure", "subsidy"))
    state["government_payment_ratio"] = (sum(paid.get(k, 0) for k in ("education", "infrastructure", "subsidy")) /
        project_requested if project_requested else 1.0)
    state["military_payment_ratio"] = paid.get("military", 0) / requested["military"] if requested.get("military") else 1.0
    # Civilian emergency extraction fades as the economy earns new income. It is
    # deliberately not an irreversible global production penalty.
    state["civilian_extraction"] *= math.exp(-days / 365)
    rate = ((state["domestic_debt"] * state["domestic_rate"] + state["foreign_debt"] * state["foreign_rate"]) /
            max(1, c.debt)) if c.debt else e["policy"]["monetary_rate"]
    e["finance"] = {**revenue, **paid, "other_expenditure": paid.get("other", 0),
        "budget_balance": total_revenue - sum(paid.values()) - arrears_paid,
        "requested_budget_balance": requested_balance, "government_debt": c.debt,
        "foreign_debt": state["foreign_debt"], "domestic_debt": state["domestic_debt"],
        "reserves": c.treasury, "interest_rate": rate,
        "deficit_months": old_finance.get("deficit_months", 0) + 1 if requested_balance < 0 else 0,
        "borrowed": result["borrowed"], "opening_treasury": opening_treasury,
        "opening_debt": opening_debt, "total_revenue": total_revenue,
        "total_expenditure": sum(paid.values()) + arrears_paid,
        "total_requested_expenditure": sum(requested.values()), "period_days": days,
        "paid_spending": paid, "requested_spending": requested,
        "unpaid_spending": {k: requested[k] - paid[k] for k in requested},
        "debt_service_requested": period_service, "debt_service_paid": paid["debt_service"],
        "arrears_paid_this_period": arrears_paid,
        "emergency_non_debt_financing": result["emergency_non_debt_financing"],
        "arrears_financing": result["arrears_financing"],
        "financing_sources": result["sources"], "funding_quote": quote,
    }
    refresh(world, country, days)
    credit = e["credit"]
    world.pressures[f"{country}:financial_crisis"] = {"country": country, "risk": "financial_crisis",
        "value": credit["default_risk"] * 100,
        "causes": [f"{r['factor']}={r['value']}" for r in credit["last_reasons"]]}
    return e["finance"]


def restructure(world, country):
    """Enter an explicit workout; no invented debt forgiveness or fresh funding."""
    _, e = _country(world, country)
    state = e["credit"]
    if state["distress_state"] != "default" and not state["arrears"]:
        raise ValueError("No distressed obligations to restructure")
    state["restructuring"] = True
    state["distress_state"] = "restructuring"
    return refresh(world, country)


def repay_debt(world, country, amount):
    """Pay down actual principal from real cash; confidence still recovers slowly."""
    amount = _positive_amount(amount)
    c, e = _country(world, country)
    s = e["credit"]
    if amount > c.debt or amount > c.treasury:
        raise ValueError("Repayment exceeds outstanding principal or available cash")
    foreign = round(amount * s["foreign_debt"] / max(1, c.debt))
    domestic = amount - foreign
    s["foreign_debt"] -= foreign
    s["domestic_debt"] -= domestic
    c.debt -= amount
    c.treasury -= amount
    return refresh(world, country)


def validation_issues(world):
    """Stable invariant checks for the existing validator integration."""
    issues = []
    if not world.economy:
        return issues
    def numeric(value, low=0, high=None):
        return (type(value) in (int, float) and math.isfinite(value) and
                value >= low and (high is None or value <= high))

    def integer(value):
        return type(value) is int and value >= 0

    capacities = set(INSTRUMENTS) - {"reserve_depletion"}
    hooks = {"investment_multiplier", "government_project_speed", "civilian_multiplier",
             "foreign_investment_multiplier", "military_payment_ratio", "social_pressure"}
    states = {"normal", "stressed", "severe_stress", "arrears", "default", "restructuring"}
    for country, e in sorted(world.economy["countries"].items()):
        if "credit" not in e:
            continue
        s, c = e["credit"], world.countries[country]
        if not isinstance(s, dict):
            issues.append(f"credit: invalid country state {country}")
            continue
        if type(s.get("version")) is not int or s.get("version") != VERSION:
            issues.append(f"credit: unsupported version {country}")
        for name in ("domestic_debt", "foreign_debt", "arrears", "default_count", "missed_service_days",
                     "arrears_paid", "public_asset_claims", "borrowing_capacity"):
            if not integer(s.get(name)):
                issues.append(f"credit: invalid {country}:{name}")
        if integer(s.get("domestic_debt")) and integer(s.get("foreign_debt")) and s["domestic_debt"] + s["foreign_debt"] != c.debt:
            issues.append(f"credit: domestic and foreign debt mismatch {country}")
        for name in ("domestic_rate", "foreign_rate", "borrowing_cost", "borrowing_spread", "default_risk"):
            if not numeric(s.get(name), 0, 1):
                issues.append(f"credit: invalid {country}:{name}")
        for name in ("creditworthiness", "target_creditworthiness"):
            if not numeric(s.get(name), 0, 100):
                issues.append(f"credit: invalid {country}:{name}")
        for name in ("debt_ceiling", "debt_service", "debt_service_ratio", "annual_revenue", "debt_to_gdp",
                     "civilian_extraction", "inflation_risk_stock", "social_cost_stock"):
            if not numeric(s.get(name)):
                issues.append(f"credit: invalid {country}:{name}")
        if (not isinstance(s.get("distress_state"), str) or s["distress_state"] not in states or
            type(s.get("restructuring")) is not bool):
            issues.append(f"credit: invalid distress state {country}")
        for field in ("emergency_capacity", "emergency_used", "financial_access"):
            values = s.get(field)
            if not isinstance(values, dict) or set(values) != capacities:
                issues.append(f"credit: missing or unknown {country}:{field}")
                continue
            for name, value in values.items():
                good = type(value) is bool if field == "financial_access" else integer(value)
                if not good:
                    issues.append(f"credit: invalid {country}:{field}:{name}")
        caps, used = s.get("emergency_capacity", {}), s.get("emergency_used", {})
        if isinstance(caps, dict) and isinstance(used, dict):
            for name in capacities:
                if integer(caps.get(name)) and integer(used.get(name)) and used[name] > caps[name]:
                    issues.append(f"credit: emergency financing overdrawn {country}:{name}")
            if (integer(s.get("public_asset_claims")) and integer(caps.get("asset_sales")) and
                integer(used.get("asset_sales")) and s["public_asset_claims"] != caps["asset_sales"] - used["asset_sales"]):
                issues.append(f"credit: public assets sold twice {country}")
        values = s.get("hooks")
        if not isinstance(values, dict) or set(values) != hooks:
            issues.append(f"credit: missing hooks {country}")
        else:
            for name, value in values.items():
                if not numeric(value, 0, 100 if name == "social_pressure" else 1):
                    issues.append(f"credit: invalid hook {country}:{name}")
        balances = s.get("recent_balances")
        if not isinstance(balances, list) or len(balances) > 12 or not all(numeric(v, -float("inf")) for v in balances):
            issues.append(f"credit: invalid balance history {country}")
        history = s.get("financing_history")
        if not isinstance(history, list) or len(history) > 36:
            issues.append(f"credit: invalid financing history {country}")
        else:
            for item in history:
                if (not isinstance(item, dict) or not integer(item.get("amount")) or
                    not numeric(item.get("borrowing_cost"), 0, 1)):
                    issues.append(f"credit: invalid financing entry {country}")
                    continue
                sources = item.get("sources")
                if (not isinstance(sources, dict) or set(sources) - (set(INSTRUMENTS) | {"reserves", "normal_borrowing"}) or
                    not all(integer(v) for v in sources.values()) or sum(sources.values()) != item["amount"]):
                    issues.append(f"credit: invalid financing source ledger {country}")
    return issues


def validate(world, old=None):
    """Adapter used by the existing economy validation framework."""
    return validation_issues(world)
