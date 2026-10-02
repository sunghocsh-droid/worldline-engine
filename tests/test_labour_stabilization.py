"""Conservation, physical limits and elapsed-time labour regressions."""
import copy
import json
import math
from datetime import timedelta

import pytest

from worldline import labour
from worldline.benchmark import reference_world
from worldline.models import World, canonical_date


def stabilized(profile="early_industrial"):
    world = reference_world(profile)
    world.economy["stabilization_version"] = 1
    labour.initialize(world)
    return world


def period(world, days=30):
    world.date = (canonical_date(world.date) + timedelta(days=days)).isoformat()
    labour.allocate(world, "a", days)
    return world.economy["regions"]["ra"]["labour_state"]


def conservation(world):
    for region, r in world.economy["regions"].items():
        state = r["labour_state"]
        jobs = math.fsum(state["jobs"].values())
        moving = math.fsum(p["workers"] for p in state["pipeline"])
        military = state["standing_army"] + state["mobilized"] + state["military_support"]
        assert jobs + moving + military + state["unemployed"] == pytest.approx(state["labour_force"])
        assert r["labour"]["employed"] == pytest.approx(jobs)
        for sector in labour.SECTORS:
            assert 0 <= state["professional_jobs"][sector] <= state["skilled_jobs"][sector] <= state["jobs"][sector]
    assert labour.validate(world) == []


@pytest.mark.parametrize("profile", ("preindustrial", "early_industrial", "industrial"))
def test_initialized_workers_are_exclusive_and_skills_are_subsets(profile):
    world = stabilized(profile)
    conservation(world)
    totals = world.economy["countries"]["a"]["labour"]
    assert totals["employed"] + totals["military"] == pytest.approx(totals["labour_force"])
    assert totals["professionals"] <= totals["skilled"] <= totals["employed"]


def test_activation_is_idempotent_and_preserves_existing_cohorts():
    world = stabilized()
    period(world)
    before = copy.deepcopy(world.economy)
    labour.initialize(world)
    assert world.economy == before


def test_legacy_economy_remains_unchanged_without_explicit_activation():
    world = reference_world("early_industrial")
    before = copy.deepcopy(world.to_dict())
    assert labour.allocate(world, "a", 31) is None
    assert labour.validate(world) == []
    assert world.to_dict() == before
    with pytest.raises(ValueError, match="stabilization_version"):
        labour.initialize(world)


def test_new_industry_does_not_instantly_receive_agricultural_workers():
    world = stabilized("preindustrial")
    r = world.economy["regions"]["ra"]
    before = copy.deepcopy(r["labour_state"])
    r["sectors"]["machinery"]["capital_stock"] *= 100
    period(world, 31)
    state = r["labour_state"]
    assert state["jobs"]["machinery"] == before["jobs"]["machinery"]
    assert state["skilled_jobs"]["machinery"] - before["skilled_jobs"]["machinery"] < before["labour_force"] * .005
    transfers = [p for p in state["pipeline"] if p["target"] == "machinery"]
    assert transfers and all(p["remaining_days"] >= 270 for p in transfers)
    assert sum(p["workers"] for p in transfers) < before["labour_force"] * .02
    conservation(world)


def test_decade_of_industrial_demand_changes_actual_job_structure():
    world = stabilized("industrial")
    r = world.economy["regions"]["ra"]
    before_agriculture = r["labour_state"]["jobs"]["agriculture"]
    before_manufacturing = r["labour"]["manufacturing"]
    for sector in ("heavy_industry", "machinery", "chemicals", "services"):
        r["sectors"][sector]["capital_stock"] *= 8
    for _ in range(120):
        period(world)
        conservation(world)
    assert r["labour_state"]["jobs"]["agriculture"] < before_agriculture * .75
    assert r["labour"]["manufacturing"] > before_manufacturing * 1.5


def test_skilled_and_professional_training_cannot_materialize_in_one_month():
    world = stabilized("preindustrial")
    r = world.economy["regions"]["ra"]
    for key in r["education"]:
        r["education"][key] = .9
    r["skilled_share"], r["professional_share"] = .75, .3
    old_skilled = r["labour"]["skilled"]
    old_professional = r["labour"]["professionals"]
    period(world, 31)
    force = r["labour_state"]["labour_force"]
    assert r["labour"]["skilled"] - old_skilled <= force * (.005 + .03 * .9 + .005 * .9) * 31 / 365
    assert r["labour"]["professionals"] - old_professional <= force * (.001 + .008 * .9) * 31 / 365
    assert r["labour"]["skilled"] < force * .10
    conservation(world)


def test_mobilization_removes_exactly_the_same_number_of_civilians():
    world = stabilized()
    r = world.economy["regions"]["ra"]
    before = r["labour"]["employed"]
    receipts = labour.mobilize(world, "a", "ra", 1500)
    assert r["labour"]["employed"] == pytest.approx(before - 1500)
    assert sum(item["workers"] for item in receipts) == pytest.approx(1500)
    assert r["labour"]["military_service"] == 1600
    assert r["labour_state"]["jobs"]["agriculture"] < before
    conservation(world)


def test_mobilization_rejects_missing_local_age_sex_cohort():
    world = stabilized()
    before = copy.deepcopy(world.to_dict())
    with pytest.raises(ValueError, match="eligible local"):
        labour.mobilize(world, "a", "ra", 5000)
    assert world.to_dict() == before


def test_demobilized_workers_wait_before_returning_and_seeking_jobs():
    world = stabilized()
    r = world.economy["regions"]["ra"]
    labour.mobilize(world, "a", "ra", 1000)
    employed = r["labour"]["employed"]
    labour.mobilize(world, "a", "ra", 0)
    assert r["labour"]["employed"] == employed
    assert r["labour"]["transit_training"] == 1000
    period(world, 30)
    assert not r["labour_state"]["last_report"]["returns"]
    for _ in range(3):
        period(world, 30)
    returned = r["labour_state"]["last_report"]["returns"]
    assert any(p["reason"] == "demobilization" and p["workers"] == 1000 for p in returned)
    assert r["labour"]["unemployed"] > 800
    for _ in range(24):
        period(world, 30)
    assert r["labour"]["employed"] > employed
    conservation(world)


def test_military_supply_jobs_cost_civilian_labor_even_without_recruiting():
    world = stabilized()
    r = world.economy["regions"]["ra"]
    before = r["labour"]["employed"]
    world.economy["countries"]["a"]["policy"]["military"] = 2000
    period(world)
    assert r["labour"]["military_support"] == 1100
    assert r["labour"]["employed"] <= before - 1100
    assert r["labour_state"]["last_report"]["military_burden"]
    conservation(world)


def test_army_deployed_abroad_keeps_home_manpower_cost():
    world = stabilized()
    before = copy.deepcopy(world.economy["regions"]["ra"]["labour_state"])
    world.armies["a"].region = "rb"
    period(world)
    assert world.economy["labour_army_origins"]["a"] == "ra"
    assert world.economy["regions"]["ra"]["labour_state"]["standing_army"] == before["standing_army"]
    assert world.economy["countries"]["a"]["labour"]["military_service"] == 100
    conservation(world)


def test_army_initially_abroad_is_not_free():
    world = reference_world("early_industrial")
    world.armies["a"].region = "rb"
    world.economy["stabilization_version"] = 1
    labour.initialize(world)
    assert world.economy["regions"]["ra"]["labour_state"]["standing_army"] == 100
    conservation(world)


@pytest.mark.parametrize("deployed_abroad", (False, True))
def test_war_quote_recruitment_limit_matches_executable_local_mobilization(deployed_abroad):
    from worldline import guardrails
    world = stabilized()
    if deployed_abroad:
        world.armies["a"].region = "rb"
    labour.mobilize(world, "a", "ra", 400)
    labour.mobilize(world, "a", "ra", 100)  # 300 people remain in the return pipeline.
    state = world.economy["regions"]["ra"]["labour_state"]
    eligible = math.floor(state["working_age_population"] * state["eligible_military_share"] -
                          state["standing_army"] - state["mobilized"])
    before = copy.deepcopy(world.to_dict())
    quote = guardrails.can_fund_war(world, "a", 0, region="ra", manpower=eligible)
    assert quote["physical_feasibility"]["feasible"]
    assert quote["physical_feasibility"]["eligible_additional_recruits"] == eligible
    excessive = guardrails.can_fund_war(world, "a", 0, region="ra", manpower=eligible + 1)
    assert not excessive["physical_feasibility"]["feasible"]
    assert world.to_dict() == before
    allowed = copy.deepcopy(world)
    labour.mobilize(allowed, "a", "ra", 100 + eligible)
    conservation(allowed)
    with pytest.raises(ValueError, match="eligible local"):
        labour.mobilize(world, "a", "ra", 101 + eligible)
    assert world.to_dict() == before


def test_national_available_labor_query_preserves_regions_and_is_read_only():
    world = stabilized()
    before = copy.deepcopy(world.to_dict())
    query = labour.available(world, "a")
    assert query["cross_region_instant_transfer"] is False
    assert set(query["regions"]) == {"ra"}
    assert "rb" not in query["regions"]
    query["regions"]["ra"]["employed_by_sector"]["agriculture"] = 999999
    assert world.to_dict() == before
    with pytest.raises(ValueError, match="owned region"):
        labour.available(world, "a", "rb")


def test_population_entry_does_not_invent_skilled_workers():
    world = stabilized()
    r = world.economy["regions"]["ra"]
    before = r["labour"]["skilled"]
    r["demography"]["productive_population"] += 200
    period(world)
    assert r["labour"]["unemployed"] > 150
    assert r["labour_state"]["unemployed_skilled"] == 0
    education = r["education"]
    cap = 5600 * (.005 + .03 * education["technical"] + .005 * education["literacy"]) * 30 / 365
    assert r["labour"]["skilled"] - before <= cap + 1e-9
    conservation(world)


def test_population_loss_conserves_surviving_workforce_and_skill_subsets():
    world = stabilized()
    labour.mobilize(world, "a", "ra", 1000)
    r = world.economy["regions"]["ra"]
    r["demography"]["productive_population"] -= 1000
    period(world)
    assert r["labour"]["labour_force"] == 4500
    conservation(world)


def test_state_and_transitions_are_identical_after_json_world_restore():
    left = stabilized()
    left.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"] *= 20
    period(left)
    labour.mobilize(left, "a", "ra", 800)
    labour.mobilize(left, "a", "ra", 0)
    right = World.from_dict(json.loads(json.dumps(left.to_dict())))
    assert right.to_dict() == left.to_dict()
    for _ in range(24):
        period(left)
        period(right)
        assert left.to_dict() == right.to_dict()
    conservation(left)


def test_demobilization_late_in_month_receives_only_actual_elapsed_day():
    world = stabilized()
    period(world, 31)
    world.date = "1897-02-28"
    labour.mobilize(world, "a", "ra", 1000)
    labour.mobilize(world, "a", "ra", 0)
    world.date = "1897-03-01"
    labour.allocate(world, "a", 28)
    state = world.economy["regions"]["ra"]["labour_state"]
    cohort = next(p for p in state["pipeline"] if p["reason"] == "demobilization")
    assert cohort["elapsed_days"] == 1
    assert cohort["remaining_days"] == 119
    assert cohort["expected_completion_date"] == "1897-06-28"
    conservation(world)


def test_identical_initial_state_policy_and_elapsed_days_are_reproducible():
    left, right = stabilized(), stabilized()
    for world in (left, right):
        world.economy["countries"]["a"]["policy"]["military"] = 100
        world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"] *= 4
    for _ in range(120):
        period(left)
        period(right)
    assert left.to_dict() == right.to_dict()


def test_explanation_contains_actual_source_target_and_shortage_counts():
    world = stabilized()
    r = world.economy["regions"]["ra"]
    r["sectors"]["machinery"]["capital_stock"] *= 30
    period(world)
    explanation = labour.explain(world, "a", "ra")["regions"]["ra"]
    assert any(row["source"] == "agriculture" and row["target"] == "machinery" for row in explanation["moves"])
    assert any(row["kind"] == "labour_shortage" and row["sector"] == "machinery" for row in explanation["bottlenecks"])
    row = next(row for row in explanation["bottlenecks"] if row["kind"] == "labour_shortage" and row["sector"] == "machinery")
    assert row["available"] == r["labour_state"]["jobs"]["machinery"]
    assert row["desired"] > row["available"]


def test_validator_detects_duplicates_and_skill_subset_corruption():
    world = stabilized()
    state = world.economy["regions"]["ra"]["labour_state"]
    state["jobs"]["machinery"] += 100
    issues = labour.validate(world)
    assert any("duplicated or lost" in issue for issue in issues)
    assert any("mirror mismatch" in issue for issue in issues)
    state["professional_jobs"]["agriculture"] = state["jobs"]["agriculture"] + 1
    assert any("skill subsets" in issue for issue in labour.validate(world))


def test_validator_detects_instant_transition_and_wrong_army_accounting():
    world = stabilized()
    period(world)
    state = world.economy["regions"]["ra"]["labour_state"]
    assert state["pipeline"]
    state["pipeline"][0]["total_days"] = 1
    state["standing_army"] = 0
    assert any("instant or malformed" in issue for issue in labour.validate(world))
    assert any("standing army is free" in issue for issue in labour.validate(world))


@pytest.mark.parametrize("value", (-1, 1.5, True))
def test_invalid_mobilization_is_rejected_without_mutation(value):
    world = stabilized()
    before = world.to_dict()
    with pytest.raises(ValueError):
        labour.mobilize(world, "a", "ra", value)
    assert world.to_dict() == before
