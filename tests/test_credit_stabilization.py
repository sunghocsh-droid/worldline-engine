"""Credit constraints distinguish dangerous choices from impossible ones."""
import copy

import pytest

from worldline import World, Country, Region
from worldline.economy import initialize as initialize_economy
from worldline import credit


def make_world(debt=0, treasury=0, stability=70):
    world = World("1897-01-01", countries={"a": Country("Fictional", 10000, 4000, treasury, debt,
        stability=stability)}, regions={"r": Region("Region", "a", 10000)})
    initialize_economy(world)
    credit.initialize(world)
    return world


def state(world):
    return world.economy["countries"]["a"]["credit"]


def test_quote_predicts_default_transition_from_new_delayed_payments():
    w = make_world(debt=10000)
    s = state(w)
    s['arrears'] = 150
    s['missed_service_days'] = 180
    credit.refresh(w, 'a')
    assert s['distress_state'] != 'default'
    quote = credit.assess(w, 'a', 50, instruments=['delayed_payments'])
    assert quote['emergency_financial_feasibility']['feasible']
    credit.finance_amount(w, 'a', 50, allow_emergency=True, instruments=['delayed_payments'])
    assert s['distress_state'] == 'default'
    assert quote['expected_consequences']['default_risk'] == pytest.approx(s['default_risk'])
    assert quote['expected_consequences']['new_borrowing_cost'] == pytest.approx(s['borrowing_cost'])


def test_upgrade_preserves_legacy_assets_debt_and_project_records():
    w = make_world(500, 120)
    e = w.economy["countries"]["a"]
    del e["credit"]
    e["finance"]["foreign_debt"] = 170
    before = copy.deepcopy(w.countries["a"])
    sectors = copy.deepcopy(w.economy["regions"]["r"]["sectors"])
    credit.initialize(w)
    assert w.countries["a"] == before
    assert w.economy["regions"]["r"]["sectors"] == sectors
    assert state(w)["foreign_debt"] == 170
    assert state(w)["domestic_debt"] == 330
    assert credit.validation_issues(w) == []


def test_normal_borrowing_is_finite_and_does_not_regenerate_per_request():
    w = make_world()
    borrowed = 0
    for _ in range(30):
        available = credit.assess(w, "a", 0)["normal_financial_feasibility"]["available"]
        if not available:
            break
        amount = min(100, available)
        credit.finance_amount(w, "a", amount)
        borrowed += amount
    assert 0 < borrowed < 4000
    assert credit.assess(w, "a", 1, allow_emergency=False)["normal_financial_feasibility"]["shortfall"] == 1
    before = w.to_dict()
    with pytest.raises(ValueError, match="Insufficient"):
        credit.finance_amount(w, "a", 1)
    assert w.to_dict() == before


def test_indebted_country_has_less_credit_and_higher_new_loan_rate():
    healthy, indebted = make_world(), make_world(3500)
    h = credit.estimated_fiscal_risk(healthy, "a")
    d = credit.estimated_fiscal_risk(indebted, "a")
    assert h["borrowing_capacity"] > d["borrowing_capacity"]
    assert state(indebted)["borrowing_cost"] > state(healthy)["borrowing_cost"]
    assert d["true_state"]["default_risk"] > h["true_state"]["default_risk"]


@pytest.mark.parametrize("condition", ["low_revenue", "war", "instability", "default_history", "deficit"])
def test_credit_records_each_real_risk_driver(condition):
    w = make_world(500)
    before = credit.estimated_fiscal_risk(w, "a")["true_state"]["default_risk"]
    if condition == "low_revenue":
        w.economy["countries"]["a"]["finance"].update(total_revenue=1, period_days=30)
    elif condition == "war":
        w.relations["war"] = {"a": "a", "b": "outsider", "status": "war"}
    elif condition == "instability":
        w.countries["a"].stability = 10
    elif condition == "default_history":
        state(w)["default_count"] = 1
    else:
        state(w)["recent_balances"] = [-100] * 12
    risk = credit.estimated_fiscal_risk(w, "a")
    assert risk["true_state"]["default_risk"] > before
    factors = {r["factor"] for r in risk["true_state"]["reasons"]}
    assert {"debt_to_gdp", "debt_service_to_revenue", "recent_deficit_to_revenue", "war", "instability", "past_defaults"} <= factors


def test_reserves_cushion_but_cannot_remove_debt_risk():
    low, high = make_world(500), make_world(500, 10000)
    assert state(high)["default_risk"] < state(low)["default_risk"]
    assert state(high)["default_risk"] > 0


def test_persistent_deficits_raise_risk_without_infinite_new_debt():
    w = make_world()
    initial = state(w)["default_risk"]
    for _ in range(36):
        credit.settle(w, "a", {"taxes": 15}, {"military": 200, "education": 200}, 30)
    f = w.economy["countries"]["a"]["finance"]
    assert state(w)["default_risk"] > initial
    assert f["unpaid_spending"]["education"] > 0
    assert w.countries["a"].debt < 4000
    assert state(w)["arrears"] < 36 * 400  # intentions are not infinitely funded credit


def test_missed_contract_payments_default_and_close_normal_lender_access():
    w = make_world(10000)
    for _ in range(12):
        credit.settle(w, "a", {"taxes": 0}, {"military": 100}, 30)
    assert state(w)["distress_state"] == "default"
    assert state(w)["default_count"] == 1
    assert state(w)["borrowing_capacity"] == 0
    assert state(w)["arrears"] > 0
    assert state(w)["hooks"]["foreign_investment_multiplier"] < .25
    assert state(w)["hooks"]["military_payment_ratio"] == 0


def test_fiscal_recovery_and_principal_repayment_rebuild_confidence_slowly():
    w = make_world(10000)
    for _ in range(12):
        credit.settle(w, "a", {"taxes": 0}, {}, 30)
    credit.restructure(w, "a")
    before = state(w)["creditworthiness"]
    credit.settle(w, "a", {"taxes": 11000}, {}, 30)
    credit.repay_debt(w, "a", 10000)
    one_month = state(w)["creditworthiness"]
    assert one_month < 80
    for _ in range(120):
        credit.settle(w, "a", {"taxes": 50}, {}, 30)
    assert state(w)["creditworthiness"] > one_month
    assert state(w)["creditworthiness"] > before
    assert state(w)["default_count"] == 1
    assert w.countries["a"].debt == 0
    assert state(w)["distress_state"] != "default"


def test_unaffordable_policy_is_reported_and_only_paid_spending_is_spendable():
    w = make_world(10000)
    f = credit.settle(w, "a", {"taxes": 20}, {"education": 500, "infrastructure": 500}, 30)
    assert f["paid_spending"]["education"] == 0
    assert f["unpaid_spending"]["education"] == 500
    assert f["total_requested_expenditure"] > f["total_expenditure"]
    assert state(w)["hooks"]["government_project_speed"] == 0
    assert w.projects == {}


def test_emergency_war_can_exceed_normal_capacity_without_physical_rejection():
    w = make_world()
    normal = state(w)["borrowing_capacity"]
    quote = credit.assess(w, "a", normal + 300, purpose="war")
    assert not quote["normal_financial_feasibility"]["feasible"]
    assert quote["physical_feasibility"]["feasible"]
    assert quote["emergency_financial_feasibility"]["feasible"]
    assert quote["expected_consequences"]["emergency_finance"] > 0
    executed = credit.finance_amount(w, "a", normal + 300, allow_emergency=True, purpose="war")
    assert executed["paid"] == normal + 300


def test_emergency_choice_has_real_larger_credit_and_civilian_cost():
    w = make_world()
    small = credit.assess(w, "a", 100)
    large = credit.assess(w, "a", state(w)["borrowing_capacity"] + 300, purpose="war")
    assert large["expected_consequences"]["default_risk"] > small["expected_consequences"]["default_risk"]
    assert large["expected_consequences"]["civilian_income_cost"] > small["expected_consequences"]["civilian_income_cost"]
    credit.finance_amount(w, "a", large["amount"], allow_emergency=True)
    assert state(w)["civilian_extraction"] > 0
    assert state(w)["hooks"]["civilian_multiplier"] < 1


def test_all_emergency_means_can_be_exhausted_without_inventing_new_money():
    w = make_world()
    quote = credit.assess(w, "a", 1000000, instruments=list(credit.INSTRUMENTS))
    amount = quote["emergency_financial_feasibility"]["available"]
    credit.finance_amount(w, "a", amount, True, list(credit.INSTRUMENTS))
    again = credit.assess(w, "a", 1000000, instruments=list(credit.INSTRUMENTS))
    assert not again["emergency_financial_feasibility"]["feasible"]
    assert again["physical_feasibility"]["feasible"]  # an empty purse is not missing people/materials
    assert again["physical_feasibility"]["financing_access_exhausted"]
    assert again["emergency_financial_feasibility"]["available"] < 100
    before = w.to_dict()
    with pytest.raises(ValueError, match="Insufficient"):
        credit.finance_amount(w, "a", 1000000, True, list(credit.INSTRUMENTS))
    assert w.to_dict() == before
    assert credit.validation_issues(w) == []


def test_high_risk_does_not_cancel_political_decision_or_project():
    w = make_world(4000)
    w.projects["deliberate-risk"] = {"status": "funded", "country": "a"}
    result = credit.finance_amount(w, "a", 200, allow_emergency=True, purpose="political_gamble")
    assert result["paid"] == 200
    assert w.projects["deliberate-risk"]["status"] == "funded"
    assert state(w)["financing_history"][-1]["purpose"] == "political_gamble"


def test_objective_cost_risk_and_actor_perceptions_never_share_mutable_data():
    w = make_world(3000)
    perceived = {"cost": 100, "risk": .01, "bias_factors": ["overconfidence", "expected_indemnity"]}
    before = w.to_dict()
    q = credit.assess(w, "a", 500, perceived=perceived)
    assert w.to_dict() == before
    assert q["true_state"]["cost"] == 500
    assert q["perceived_state"]["cost"] == 100
    q["perceived_state"]["risk"] = 0
    assert perceived["risk"] == .01
    assert q["true_state"]["risk"] > 0


def test_missing_physical_resources_remain_impossible_even_with_reserves():
    w = make_world(treasury=100000)
    before = w.to_dict()
    with pytest.raises(ValueError, match="physical"):
        credit.finance_amount(w, "a", 1, True, physical_constraints=["no steel"])
    assert w.to_dict() == before


def test_reserves_are_not_double_counted_as_an_emergency_instrument():
    w = make_world(debt=10000, treasury=10)
    q = credit.assess(w, "a", 11, instruments=["reserve_depletion"])
    assert q["emergency_financial_feasibility"]["available"] == 10
    assert q["emergency_financial_feasibility"]["shortfall"] == 1


@pytest.mark.parametrize("instruments", [[], ["emergency_taxation"], ["delayed_payments"], ["forced_loans"], ["monetary_financing"]])
def test_cash_and_debt_books_reconcile_for_each_financing_mode(instruments):
    w = make_world(debt=3500, treasury=12)
    f = credit.settle(w, "a", {"taxes": 20}, {"military": 70, "education": 40}, 30,
                      allow_emergency=True, instruments=instruments)
    assert w.countries["a"].treasury == (f["opening_treasury"] + f["budget_balance"] +
        f["borrowed"] + f["emergency_non_debt_financing"] + f["arrears_financing"])
    assert w.countries["a"].debt == f["opening_debt"] + f["borrowed"]
    assert f["total_expenditure"] <= f["total_requested_expenditure"]
    assert credit.validation_issues(w) == []


def test_no_automatic_monetary_financing_when_emergency_options_not_selected():
    w = make_world(10000)
    q = credit.assess(w, "a", 100000)
    assert "monetary_financing" not in q["expected_consequences"]["financing_sources"]
    explicit = credit.assess(w, "a", 100, instruments=["monetary_financing"])
    assert explicit["expected_consequences"]["inflation_risk"] > 0


def test_financial_state_roundtrip_and_same_policy_are_identical():
    w = make_world()
    for _ in range(12):
        credit.settle(w, "a", {"taxes": 25}, {"military": 50}, 30)
    loaded = World.from_dict(w.to_dict())
    assert state(loaded) == state(w)
    for candidate in (w, loaded):
        for _ in range(24):
            credit.settle(candidate, "a", {"taxes": 30}, {"military": 50}, 30)
    assert loaded.to_dict() == w.to_dict()


def test_unknown_credit_version_is_explicitly_rejected():
    w = make_world()
    state(w)["version"] = 999
    with pytest.raises(ValueError, match="Unsupported"):
        credit.assess(w, "a", 1)


def test_social_pressure_component_preserves_existing_other_causes():
    w = make_world(4000)
    w.pressures["a:revolution"] = {"country": "a", "risk": "revolution", "value": 20,
        "causes": ["regional dispute"]}
    credit.finance_amount(w, "a", 100, True, ["emergency_taxation"])
    assert w.pressures["a:revolution"]["value"] > 20
    assert "regional dispute" in w.pressures["a:revolution"]["causes"]
    for _ in range(5):
        credit.refresh(w, "a")
    assert w.pressures["a:revolution"]["value"] < 30  # a quote does not compound unrest


def test_borrowing_quote_does_not_spend_or_relabel_existing_reserves():
    w = make_world(treasury=100000)
    before = w.to_dict()
    quote = credit.can_borrow(w, "a", 100)
    assert quote["amount"] == 100
    assert quote["normal_financial_feasibility"]["available"] == state(w)["borrowing_capacity"]
    assert quote["expected_consequences"]["financing_sources"]["reserves"] == 0
    assert quote["expected_consequences"]["closing_reserves"] == 100000
    uncovered = credit.can_borrow(w, "a", state(w)["borrowing_capacity"] + 1, allow_emergency=False)
    assert not uncovered["normal_financial_feasibility"]["feasible"]
    assert not uncovered["emergency_financial_feasibility"]["feasible"]
    assert uncovered["expected_consequences"]["closing_reserves"] == 100000
    assert w.to_dict() == before


def test_quoted_risk_debt_service_and_spread_match_executed_internal_calculation():
    w = make_world()
    before_service = state(w)["debt_service"]
    amount = state(w)["borrowing_capacity"] + 300
    quote = credit.assess(w, "a", amount, purpose="war")
    expected = quote["expected_consequences"]
    credit.finance_amount(w, "a", amount, True, purpose="war")
    assert state(w)["default_risk"] == pytest.approx(expected["default_risk"])
    assert state(w)["borrowing_cost"] == pytest.approx(expected["new_borrowing_cost"])
    assert state(w)["debt_service"] - before_service == pytest.approx(expected["annual_debt_service_increase"])
    assert state(w)["domestic_debt"] == expected["domestic_debt_increase"]
    assert state(w)["foreign_debt"] == expected["foreign_debt_increase"]


def test_requisition_updates_real_inventory_and_conservation_account():
    w = make_world(10000)
    r = w.economy["regions"]["r"]
    r["inputs"] = {"closing_energy": r["stocks"]["energy"],
                   "closing_raw_materials": r["stocks"]["raw_materials"]}
    before = r["stocks"]["energy"]
    credit.finance_amount(w, "a", 20, True, ["requisition"])
    assert r["stocks"]["energy"] == pytest.approx(before - 80)
    assert r["inputs"]["emergency_energy_used"] == pytest.approx(80)
    assert r["inputs"]["closing_energy"] == pytest.approx(before - 80)
    assert credit.validation_issues(w) == []


def test_selling_assets_transfers_existing_claims_without_creating_or_destroying_capital():
    w = make_world(10000)
    r = w.economy["regions"]["r"]
    before = sum(v["capital_stock"] for v in r["sectors"].values())
    claims = state(w)["public_asset_claims"]
    credit.finance_amount(w, "a", 20, True, ["asset_sales"])
    assert state(w)["public_asset_claims"] == claims - 20
    assert sum(v["capital_stock"] for v in r["sectors"].values()) == before
    assert credit.validation_issues(w) == []


@pytest.mark.parametrize("key,value", [
    ("creditworthiness", float("inf")), ("creditworthiness", float("nan")),
    ("borrowing_cost", -1), ("debt_service_ratio", float("inf")),
    ("domestic_debt", "bad"), ("arrears", -1), ("default_count", True),
    ("emergency_capacity", {}), ("emergency_used", {"wrong": 1}),
    ("financial_access", []), ("recent_balances", [float("nan")]),
    ("hooks", {}), ("financing_history", [{}]), ("public_asset_claims", -1),
])
def test_malformed_credit_save_data_is_reported_without_crashing(key, value):
    w = make_world()
    state(w)[key] = value
    assert credit.validation_issues(w)


def test_missing_required_credit_field_and_exhaustion_survive_save_roundtrip():
    w = make_world(10000)
    capacity = state(w)["emergency_capacity"]["emergency_taxation"]
    credit.finance_amount(w, "a", capacity, True, ["emergency_taxation"])
    loaded = World.from_dict(w.to_dict())
    assert credit.assess(loaded, "a", 1, instruments=["emergency_taxation"])["emergency_financial_feasibility"]["shortfall"] == 1
    del state(loaded)["domestic_rate"]
    assert any("domestic_rate" in v for v in credit.validation_issues(loaded))


def test_unordered_emergency_instruments_are_rejected_to_preserve_replay_order():
    w = make_world()
    with pytest.raises(ValueError, match="ordered list"):
        credit.assess(w, "a", 100, instruments={"forced_loans", "domestic_war_bonds"})
