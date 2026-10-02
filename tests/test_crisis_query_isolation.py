"""Read-only resource reservation retains the pre-optimization financial quotes."""
import copy
import json
from pathlib import Path

import pytest

from worldline import credit, guardrails, labour, projects
from worldline.models import Region, World
from worldline.npc_benchmark import campaign_world, encode


def query_world(condition):
    world = campaign_world()
    world.regions["ra2"] = Region("Second local labour and resource pool", "a", 10000, 40, 0,
                                  dict(world.regions["ra"].infrastructure))
    world.economy["regions"]["ra2"] = copy.deepcopy(world.economy["regions"]["ra"])
    world.countries["a"].population += 10000
    labour.aggregate(world, "a")
    if condition not in ("healthy", "resource_shortage"):
        world.countries["a"].treasury = 0
        world.countries["a"].debt = 12000
        state = world.economy["countries"]["a"]["credit"]
        state.update(domestic_debt=12000, foreign_debt=0, recent_balances=[-150] * 6)
        if condition in ("default", "exhausted"):
            state.update(distress_state="default", default_count=1, arrears=900, missed_service_days=365)
        if condition == "exhausted":
            state["financial_access"] = dict.fromkeys(state["financial_access"], False)
        credit.refresh(world, "a")
    if condition == "resource_shortage":
        world.economy["regions"]["ra"]["stocks"]["raw_materials"] = 0
    projects.plan(world, "query-project", "a", "ra", "factory", "machinery", 100)
    return world


def query_quotes(world, perceived=None):
    perceived = perceived if perceived is not None else {"cost": 80, "risk": .2,
        "bias_factors": [{"factor": "optimism", "value": .2}]}
    return {
        "project": guardrails.can_afford_project(world, "query-project", perceived=perceived),
        "war_regional": guardrails.can_fund_war(world, "a", 400, region="ra", manpower=200,
            resources={"energy": 8, "raw_materials": 5}, perceived=perceived),
        "war_all_regions": guardrails.can_fund_war(world, "a", 400, manpower=200,
            resources={"energy": 150, "raw_materials": 220}, perceived=perceived),
    }


@pytest.mark.parametrize("condition", ("healthy", "debt", "default", "exhausted", "resource_shortage"))
def test_reservation_queries_equal_quotes_recorded_before_copy_optimization(condition):
    expected = json.loads((Path(__file__).with_name("fixtures") / "crisis_query_quotes.json").read_text(encoding="utf-8"))
    world = query_world(condition)
    before = encode(world.to_dict())
    assert query_quotes(world) == expected["cases"][condition]
    assert encode(world.to_dict()) == before


@pytest.mark.parametrize("condition", ("healthy", "default", "exhausted", "resource_shortage"))
def test_reservation_and_returned_quotes_do_not_mutate_truth_or_caller_perceptions(condition):
    world = query_world(condition)
    perception = {"cost": 80, "risk": .2, "bias_factors": [{"factor": "optimism", "value": .2}]}
    before, before_perception = encode(world.to_dict()), copy.deepcopy(perception)
    quotes = query_quotes(world, perception)
    for quote in quotes.values():
        quote["perceived_state"]["bias_factors"][0]["value"] = 99
        quote["expected_consequences"]["financing_sources"]["reserves"] = 10 ** 9
        quote["physical_feasibility"]["missing_resources"].clear()
        quote["true_state"]["consequences"]["projected_risk_reasons"].clear()
    assert encode(world.to_dict()) == before
    assert perception == before_perception


def test_financing_query_no_longer_clones_the_entire_political_world(monkeypatch):
    world = query_world("healthy")
    original = copy.deepcopy
    def check(value, memo=None):
        assert not isinstance(value, World), "financial query must not clone the complete world"
        return original(value, memo)
    monkeypatch.setattr(guardrails.copy, "deepcopy", check)
    assert query_quotes(world)["war_all_regions"]["physical_feasibility"]["feasible"]
