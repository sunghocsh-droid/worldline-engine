import copy
import json
import pytest
from worldline import Engine, World, Country, Region, Army, Order
from worldline.audit import Auditor, AuditError
from worldline.economy import SECTORS, SHOCKS


@pytest.fixture
def economic_world():
    # Deliberately fictional reference economy; no Korean historical estimates.
    return World("1897-01-01", scenario_id="synthetic_economy",
        countries={"a": Country("Synthetic A", population=10000, treasury=100000),
                   "b": Country("Synthetic B", population=10000, treasury=100000)},
        regions={"ra": Region("A region", "a", 10000), "rb": Region("B region", "b", 10000)},
        armies={"aa": Army("a", "ra", 100), "ab": Army("b", "rb", 100)})


@pytest.fixture
def economy(tmp_path, economic_world):
    e = Engine.create(tmp_path / "economy.sqlite", economic_world, 17)
    e.enable_economy()
    yield e
    e.close()


def test_monthly_economy_gdp_and_population_balance(economy):
    economy.advance(1, "month")
    w = economy.true_state
    report = economy.explain("a")
    assert w.countries["a"].population == w.regions["ra"].population
    assert report["population"]["elapsed_days"] == 31
    assert report["population"]["births"] > 0
    assert w.countries["a"].population > 10000
    assert w.countries["a"].gdp == round(sum(report["sector_value_added"].values()))
    assert sum(report["sector_shares"].values()) == pytest.approx(1)
    assert report["gdp_per_capita"] > 0
    assert set(w.economy["regions"]["ra"]["sectors"]) == set(SECTORS)
    assert Auditor().check(w) == []


def test_school_factory_railroad_have_no_immediate_effect(economy):
    before = economy.true_state
    for pid, kind, target, amount in (("school", "education", "human_capital", 100),
        ("factory", "factory", "machinery", 100), ("rail", "infrastructure", "railways", 1)):
        economy.plan_project(pid, "a", "ra", kind, target, amount)
        economy.fund_project(pid)
    after = economy.true_state
    assert after.economy["regions"]["ra"]["education"] == before.economy["regions"]["ra"]["education"]
    assert after.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"] == before.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"]
    assert after.regions["ra"].infrastructure["railways"] == 0
    assert after.countries["a"].gdp == before.countries["a"].gdp
    economy.advance(360)
    assert economy.true_state.projects["factory"]["status"] == "completed"
    assert economy.true_state.projects["school"]["status"] != "completed"
    economy.advance(35)
    assert economy.true_state.projects["rail"]["status"] == "completed"
    economy.advance(700)
    assert economy.true_state.projects["school"]["status"] == "completed"
    assert economy.true_state.economy["regions"]["ra"]["education"]["literacy"] > .2


def test_information_access_does_not_install_technology(economy):
    result = economy.submit(Order("knowledge", "a", "technology_access", {"region": "ra", "sector": "machinery"}))
    assert result["status"] == "accepted"
    economy.advance(100)
    tech = economy.true_state.economy["regions"]["ra"]["sectors"]["machinery"]["technology"]
    assert tech["access"] and not tech["installed"]
    assert tech["diffusion"] == 0
    economy.plan_project("import", "a", "ra", "technology", "machinery", 1)
    economy.fund_project("import")
    economy.advance(545)
    assert economy.true_state.projects["import"]["status"] == "completed"
    economy.advance(365)
    tech = economy.true_state.economy["regions"]["ra"]["sectors"]["machinery"]["technology"]
    assert 0 < tech["diffusion"] < tech["adoption"] < 1


def test_economy_cannot_be_bypassed_by_legacy_orders(economy):
    assert economy.submit(Order("gdp", "a", "set_gdp", {"value": 999999}))["status"] == "rejected"
    assert economy.submit(Order("p", "a", "produce", {"resource": "grain", "quantity": 1}))["status"] == "rejected"
    assert economy.submit(Order("r", "a", "research", {"technology": "basic"}))["status"] == "rejected"


def test_save_resume_pending_projects_and_builtin_order(economy, tmp_path):
    economy.plan_project("rail", "a", "ra", "infrastructure", "railways", 1)
    economy.fund_project("rail")
    economy.set_policy("a", {"education": 30, "subsidy": 20})
    economy.advance(100)
    before = economy.true_state.projects["rail"]
    path = economy.save(tmp_path / "resume.json")
    resumed = Engine.load(path, tmp_path / "resumed.sqlite")
    assert resumed.true_state.projects["rail"] == before
    assert resumed._system_specs[:2] == [{"name": "worldline.projects", "version": "1"}, {"name": "worldline.economy", "version": "1"}]
    economy.advance(400)
    resumed.advance(400)
    assert economy._payload() == resumed._payload()
    assert economy.ledger() == resumed.ledger()
    resumed.close()


def test_builtin_version_mismatch_is_refused(economy, tmp_path):
    import hashlib
    path = economy.save(tmp_path / "versions.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["engine"]["systems"][1]["version"] = "999"
    body = json.dumps(raw["payload"], ensure_ascii=False, sort_keys=True, allow_nan=False)
    raw["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="system"):
        Engine.load(path, tmp_path / "bad.sqlite")


def test_external_system_still_requires_matching_registration(economy, tmp_path):
    # Economy enable freezes its manifest; external systems must be configured before enabling.
    world = economy.true_state
    world.economy = None
    world.projects.clear()
    e = Engine.create(tmp_path / "external.sqlite", world, 3)
    def diplomacy(w, tick, rng): return [{"module": "diplomacy", "draw": rng.random()}]
    e.register_system(diplomacy, name="diplomacy", version="7")
    e.enable_economy()
    e.advance(60)
    restored = Engine.load(e.save(tmp_path / "external.json"), tmp_path / "external-resume.sqlite")
    with pytest.raises(ValueError, match="system"): restored.advance()
    restored.register_system(diplomacy, name="diplomacy", version="7")
    e.advance(30)
    restored.advance(30)
    assert e._payload() == restored._payload()
    assert e.ledger() == restored.ledger()
    e.close()
    restored.close()


def test_military_cost_and_debt_warnings(economy):
    economy.set_policy("a", {"military": 5000})
    economy.advance(365)
    w = economy.true_state
    assert w.economy["countries"]["a"]["finance"]["budget_balance"] < 0
    assert w.economy["countries"]["a"]["labour"]["military"] > w.economy["countries"]["b"]["labour"]["military"]
    assert w.economy["regions"]["ra"]["sectors"]["heavy_industry"]["factors"]["military_demand"] > 1
    assert w.economy["regions"]["ra"]["sectors"]["services"]["output"] < w.economy["regions"]["rb"]["sectors"]["services"]["output"]
    economy.advance(365 * 2)
    w = economy.true_state
    assert w.countries["a"].debt > 0
    audit = Auditor()
    audit.check(w)
    assert any("deficit" in item["message"] for item in audit.warnings)
    assert w.pressures["a:financial_crisis"]["value"] > w.pressures["b:financial_crisis"]["value"]


def test_war_shock_hurts_population_capital_gdp_and_finance(economy):
    economy.advance(90)
    economy.apply_shock("war", "war_disruption", "ra", 1, 365)
    economy.advance(90)
    w = economy.true_state
    assert w.countries["a"].population < w.countries["b"].population
    assert w.countries["a"].gdp < w.countries["b"].gdp
    assert w.economy["regions"]["ra"]["sectors"]["heavy_industry"]["capital_stock"] < w.economy["regions"]["rb"]["sectors"]["heavy_industry"]["capital_stock"]
    assert w.economy["countries"]["a"]["finance"]["budget_balance"] < w.economy["countries"]["b"]["finance"]["budget_balance"]
    assert economy.explain("a")["population"]["war_losses"] == 0  # applied once, never every month
    assert w.economy["regions"]["ra"]["demography"]["war_losses"] > 0


@pytest.mark.parametrize("kind", SHOCKS)
def test_all_shock_interfaces_have_finite_results(economy, kind):
    economy.apply_shock(kind, kind, "ra", .5, 60)
    economy.advance(90)
    Auditor().check(economy.true_state)
    assert economy.true_state.economy["shocks"][kind]["applied"]


def test_trade_is_bilateral_and_blockade_limits_it(economy):
    economy.add_trade_route("route", "a", "b", "light_industry", 5)
    economy.advance(60)
    w = economy.true_state
    t = w.economy["trades"]["route"]
    assert t["exported"] == t["imported"] > 0
    assert w.economy["countries"]["a"]["exports"] == w.economy["countries"]["b"]["imports"]
    economy.apply_shock("blockade", "blockade", "ra", 1, 120)
    economy.advance(60)
    assert economy.true_state.economy["trades"]["route"]["exported"] == 0


@pytest.mark.parametrize("corruption", ["gdp", "labour", "capital", "energy", "railway", "tech"])
def test_economic_validator_rejects_impossible_state(economy, corruption):
    economy.advance(31)
    old = economy.true_state
    w = copy.deepcopy(old)
    s = w.economy["regions"]["ra"]["sectors"]["machinery"]
    if corruption == "gdp": w.countries["a"].gdp *= 2
    if corruption == "labour": s["labour"] = 10000000
    if corruption == "capital": s["capital_stock"] = 0
    if corruption == "energy": w.economy["regions"]["ra"]["stocks"]["energy"] += 1000
    if corruption == "railway": s["factors"]["railway_access"] = 1
    if corruption == "tech": s["technology"]["diffusion"] = 1
    with pytest.raises(AuditError): Auditor().check(w, old)


def test_same_seed_same_initial_state_and_commands(economic_world, tmp_path):
    engines = [Engine.create(tmp_path / f"same-{i}.sqlite", economic_world, 99) for i in range(2)]
    for e in engines:
        e.enable_economy()
        e.set_policy("a", {"education": 20, "infrastructure": 20, "subsidy": 20})
        e.apply_shock("harvest", "good_harvest", "ra", .3)
        e.advance(500)
    assert engines[0]._payload() == engines[1]._payload()
    assert engines[0].ledger() == engines[1].ledger()
    for e in engines: e.close()


def test_ten_year_baseline_does_not_explode_or_collapse(economy):
    economy.advance(31)
    baseline = economy.true_state.countries["a"].gdp
    economy.advance(3652 - 31)
    w = economy.true_state
    assert .4 < w.countries["a"].gdp / baseline < 3
    assert 8000 < w.countries["a"].population < 16000
    assert w.countries["a"].debt == 0


def test_ten_year_investment_beats_identical_no_investment_country(economy):
    # Both regions start identically; only A has the sustained investment policy.
    for month in range(120):
        economy.set_policy("a", {"education": 30, "infrastructure": 30, "subsidy": 30,
            "infrastructure_target": "railways" if month % 2 == 0 else "electricity",
            "subsidy_sector": "machinery"})
        economy.advance(1, "month")
    w = economy.true_state
    assert w.countries["a"].gdp > w.countries["b"].gdp
    assert w.economy["countries"]["a"]["gdp_per_capita"] > w.economy["countries"]["b"]["gdp_per_capita"]
    assert w.economy["regions"]["ra"]["education"]["literacy"] > w.economy["regions"]["rb"]["education"]["literacy"]
    assert w.regions["ra"].infrastructure["railways"] > 0
    assert w.regions["ra"].infrastructure["electricity"] > 0
