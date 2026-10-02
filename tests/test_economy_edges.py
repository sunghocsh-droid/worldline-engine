import copy
import json
import hashlib
import pytest
from worldline import Engine, Order, World, Country, Region
from worldline.audit import Auditor, AuditError
from test_economy import economy, economic_world


@pytest.mark.parametrize("profile", ["preindustrial", "early_industrial", "industrial", "advanced_industrial"])
def test_profiles_provide_ranges_and_stable_finite_state(profile, economic_world, tmp_path):
    e = Engine.create(tmp_path / f"{profile}.db", economic_world)
    e.enable_economy(profile)
    e.advance(365)
    country = e.true_state.economy["countries"]["a"]
    assert country["calibration_ranges"]["productivity"][0] <= country["calibration"]["productivity"] <= country["calibration_ranges"]["productivity"][1]
    assert e.true_state.countries["a"].gdp > 0
    e.close()


def test_zero_capital_and_energy_bottleneck_are_physical(economic_world, tmp_path):
    e = Engine.create(tmp_path / "bottleneck.db", economic_world)
    e.enable_economy(region_overrides={"ra": {"capital_stock": {"machinery": 0, "energy": 0}, "stocks": {"energy": 0}}})
    e.advance(90)
    sectors = e.true_state.economy["regions"]["ra"]["sectors"]
    assert sectors["machinery"]["output"] == 0
    assert sectors["heavy_industry"]["factors"]["input_supply"] == 0
    e.close()


def test_structured_project_orders_deduct_cost_once(economy):
    before = economy.true_state.countries["a"].treasury
    planned = economy.submit(Order("plan", "a", "plan_project", {"project_id": "order-rail", "region": "ra",
        "kind": "infrastructure", "target": "railways", "amount": 1}))
    funded = economy.submit(Order("fund", "a", "fund_project", {"project_id": "order-rail"}))
    assert planned["status"] == funded["status"] == "accepted"
    assert economy.true_state.countries["a"].treasury == before - 100
    assert economy.submit(Order("fund-again", "a", "fund_project", {"project_id": "order-rail"}))["status"] == "rejected"
    assert economy.submit(Order("foreign-fund", "b", "fund_project", {"project_id": "order-rail"}))["status"] == "rejected"


def test_funding_resource_shortage_does_not_charge_money(economy):
    economy.plan_project("huge", "a", "ra", "factory", "machinery", 20000)
    before = economy.true_state
    with pytest.raises(ValueError, match="resource"):
        economy.fund_project("huge")
    assert economy.true_state == before


def test_project_capital_distinguishes_all_investment_sources(economy):
    economy.set_policy("a", {"subsidy": 30, "infrastructure": 20, "foreign_investment_openness": 1})
    economy.advance(365 * 3)
    sectors = economy.true_state.economy["regions"]["ra"]["sectors"]
    assert sum(s["investment"]["government"] for s in sectors.values()) > 0
    assert sum(s["investment"]["private"] for s in sectors.values()) > 0
    assert sum(s["investment"]["foreign"] for s in sectors.values()) > 0
    assert sum(s["depreciation_losses"] for s in sectors.values()) > 0


def test_short_shock_does_not_apply_for_an_entire_month(economy):
    economy.apply_shock("brief", "blockade", "ra", 1, 1)
    economy.advance(31)
    trade_factor = economy.true_state.economy["regions"]["ra"]["shock_factors"]["trade"]
    assert trade_factor == pytest.approx(30 / 31)


def test_war_disrupts_project_immediately_and_resumes_at_end(economy):
    economy.plan_project("rail", "a", "ra", "infrastructure", "railways", 1)
    economy.fund_project("rail")
    economy.apply_shock("war", "war_disruption", "ra", .8, 30)
    assert economy.true_state.projects["rail"]["status"] == "disrupted"
    economy.advance(20)
    assert economy.true_state.projects["rail"]["elapsed_days"] == 0
    economy.advance(15)
    assert economy.true_state.projects["rail"]["elapsed_days"] > 0


def test_invalid_policy_and_shock_roll_back(economy):
    before = copy.deepcopy(economy._payload())
    with pytest.raises(ValueError): economy.apply_shock("invalid", "epidemic", "ra", 2)
    assert economy._payload() == before
    result = economy.set_policy("a", {"tax_rate": .95})
    assert result["status"] == "rejected"
    assert economy.true_state.economy["countries"]["a"]["policy"]["tax_rate"] == .12


def test_education_does_not_immediately_convert_workforce(economy):
    economy.advance(31)
    before = economy.true_state.economy["regions"]["ra"]["skilled_share"]
    economy.set_policy("a", {"education": 100})
    assert economy.true_state.economy["regions"]["ra"]["skilled_share"] == before
    economy.advance(365)
    assert economy.true_state.economy["regions"]["ra"]["education"]["literacy"] == .2


def test_foreign_restriction_reduces_foreign_investment(economy):
    economy.set_policy("a", {"foreign_investment_openness": 0})
    economy.set_policy("b", {"foreign_investment_openness": 1})
    economy.advance(365 * 2)
    projects = economy.true_state.projects.values()
    assert not any(p["country"] == "a" and p["source"] == "foreign" for p in projects)
    assert any(p["country"] == "b" and p["source"] == "foreign" for p in projects)
    finance = economy.true_state.economy["countries"]
    assert finance["a"]["finance"]["foreign_profit_remittances"] == 0
    assert finance["b"]["finance"]["foreign_profit_remittances"] > 0


def test_migration_health_sanitation_and_food_affect_demography(economic_world, tmp_path):
    e = Engine.create(tmp_path / "demography.db", economic_world)
    e.enable_economy(region_overrides={"ra": {"demography": {"migration": 10, "health_level": .8},
        "infrastructure": {"sanitation": 100}}})
    e.advance(31)
    a, b = e.true_state.economy["regions"]["ra"]["demography"], e.true_state.economy["regions"]["rb"]["demography"]
    assert a["last_account"]["migration"] > 0
    assert a["last_account"]["deaths"] < b["last_account"]["deaths"]
    assert a["total_population"] > b["total_population"]
    e.close()


def test_tiny_multiregion_budget_is_not_overspent(tmp_path):
    w = World("1897-01-01", countries={"a": Country("A", population=9000, treasury=1000)},
        regions={f"r{i}": Region(str(i), "a", 3000) for i in range(3)})
    e = Engine.create(tmp_path / "split.db", w)
    e.enable_economy()
    e.set_policy("a", {"education": 1, "infrastructure": 1, "subsidy": 1})
    e.advance(31)
    assert sum(p["cost"] for p in e.true_state.projects.values() if p["source"] == "government") == 3
    e.close()


def test_refresh_attaches_new_builtin_configuration(economic_world, tmp_path):
    e = Engine.create(tmp_path / "refresh.db", economic_world)
    stale = Engine(e.path)
    e.enable_economy()
    stale.refresh()
    assert stale._system_specs == e._system_specs
    stale.advance(31)
    assert stale.true_state.countries["a"].gdp > 0
    stale.close()
    e.close()


def test_unknown_save_version_is_explicit_and_leaves_no_destination(economy, tmp_path):
    path = economy.save(tmp_path / "future.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["schema_version"] = 999
    body = json.dumps(raw["payload"], ensure_ascii=False, sort_keys=True, allow_nan=False)
    raw["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="schema version"):
        Engine.load(path, tmp_path / "future.db")
    assert not (tmp_path / "future.db").exists()


def test_fast_education_pipeline_is_not_accepted(economy):
    economy.plan_project("school", "a", "ra", "education", "human_capital", 100)
    w = economy.true_state
    w.projects["school"]["stages"] = [{"status": "training", "days": 1}]
    with pytest.raises(AuditError): Auditor().check(w)


def test_economy_failure_restores_entire_month(economic_world, tmp_path):
    e = Engine.create(tmp_path / "rollback.db", economic_world)
    def broken(w, tick, rng):
        if w.date == "1897-02-02": raise RuntimeError("after settlement")
    e.register_system(broken, name="broken")
    e.enable_economy()
    before, history = copy.deepcopy(e._payload()), e.ledger()
    with pytest.raises(RuntimeError): e.advance(40)
    assert e._payload() == before
    assert e.ledger() == history
    e.close()


def test_military_stimulus_increases_some_industry_but_reduces_services(economy):
    economy.set_policy("a", {"military": 5000})
    economy.advance(90)
    r = economy.true_state.economy["regions"]
    assert r["ra"]["sectors"]["heavy_industry"]["output"] > r["rb"]["sectors"]["heavy_industry"]["output"]
    assert r["ra"]["sectors"]["services"]["output"] < r["rb"]["sectors"]["services"]["output"]
    assert r["ra"]["sectors"]["heavy_industry"]["factors"]["capacity_utilization"] <= 1


def test_gdp_initial_calibration_is_an_anchor_not_a_runtime_override(economic_world, tmp_path):
    economic_world.countries["a"].gdp = 50000
    e = Engine.create(tmp_path / "calibration.db", economic_world)
    e.enable_economy()
    e.advance(31)
    assert e.true_state.countries["a"].gdp == pytest.approx(50000, rel=.03)
    e.apply_shock("war", "war_disruption", "ra", 1, 90)
    e.advance(31)
    assert e.true_state.countries["a"].gdp < 50000
    e.close()


def test_school_and_factory_progress_and_deadline_restore_exactly(economy, tmp_path):
    for key, kind, target in (("school", "education", "human_capital"), ("factory", "factory", "machinery")):
        economy.plan_project(key, "a", "ra", kind, target, 100)
        economy.fund_project(key)
    economy.advance(60)
    path = economy.save(tmp_path / "school-factory.json")
    restored = Engine.load(path, tmp_path / "school-factory.db")
    for key in ("school", "factory"):
        assert restored.true_state.projects[key] == economy.true_state.projects[key]
    restored.close()


@pytest.mark.parametrize("sector", ["energy", "mining"])
def test_traded_intermediate_inputs_remain_conserved(economy, sector):
    economy.add_trade_route("input", "a", "b", sector, 10)
    economy.advance(90)
    assert economy.true_state.economy["trades"]["input"]["exported"] > 0
    Auditor().check(economy.true_state)


def test_sea_trade_requires_ports_and_diplomatic_opening(economic_world, tmp_path):
    e = Engine.create(tmp_path / "sea.db", economic_world)
    e.enable_economy()
    e.add_trade_route("sea", "a", "b", "light_industry", 5, "sea")
    e.advance(31)
    assert e.true_state.economy["trades"]["sea"]["exported"] == 0
    e.close()


def test_even_small_unexplained_population_changes_are_rejected(economy):
    economy.advance(31)
    before = economy.true_state
    after = copy.deepcopy(before)
    after.countries["a"].population += 1
    after.regions["ra"].population += 1
    after.economy["regions"]["ra"]["demography"]["total_population"] += 1
    with pytest.raises(AuditError, match="population"):
        Auditor().check(after, before)


def test_construction_demand_precedes_infrastructure_completion(economy):
    economy.set_policy("a", {"infrastructure": 100, "subsidy": 100})
    economy.advance(31)
    w = economy.true_state
    assert w.regions["ra"].infrastructure["railways"] == 0
    assert w.economy["regions"]["ra"]["sectors"]["construction"]["output"] > w.economy["regions"]["rb"]["sectors"]["construction"]["output"]


def test_project_cannot_create_unfunded_extra_effects(economy):
    economy.plan_project("plant", "a", "ra", "factory", "machinery", 100)
    world = economy.true_state
    world.projects["plant"]["effects"][0]["amount"] = 100000
    with pytest.raises(AuditError): Auditor().check(world)
