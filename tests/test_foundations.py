import copy
import json
from pathlib import Path
import pytest
from worldline import Engine, World
from worldline.audit import Auditor, AuditError
from worldline.persistence import SAVE_SCHEMA_VERSION


def account(before, after, **changes):
    return {"period_start": "1897-01-01", "period_end": "1897-02-01", "elapsed_days": 31,
            "before": before, "after": after, "births": 30, "deaths": 20, "migration": 0,
            "war_losses": 0, "epidemic_losses": 0, "disaster_losses": 0,
            "sources": ["monthly settlement"], **changes}


def test_monthly_population_receipt_is_not_one_day_growth(world):
    old = copy.deepcopy(world)
    old.date = "1897-01-31"
    world.date = "1897-02-01"
    world.countries["a"].population += 10
    world.population_accounts["a"] = account(10000, 10010)
    assert Auditor().check(world, old) == []


@pytest.mark.parametrize("corrupt", ["births", "elapsed_days", "after", "sources"])
def test_population_receipt_requires_matching_flows(world, corrupt):
    old = copy.deepcopy(world)
    world.date = "1897-02-01"
    world.countries["a"].population += 10
    receipt = account(10000, 10010)
    receipt[corrupt] = [] if corrupt == "sources" else 999999
    world.population_accounts["a"] = receipt
    with pytest.raises(AuditError): Auditor().check(world, old)


def test_explicit_war_and_epidemic_losses_allow_explained_decline(world):
    old = copy.deepcopy(world)
    world.date = "1897-02-01"
    world.countries["a"].population = 8500
    for r in world.regions.values(): r.population = 4000
    world.population_accounts["a"] = account(10000, 8500, births=20, deaths=20,
        migration=200, war_losses=1000, epidemic_losses=500, disaster_losses=200)
    Auditor().check(world, old)


def test_v1_save_migrates_without_inventing_economy(tmp_path):
    path = Path(__file__).parents[1] / "examples/initial.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["payload"]["schema_version"] == 1
    e = Engine.load(path, tmp_path / "legacy.db")
    assert e.true_state.schema_version == SAVE_SCHEMA_VERSION == 2
    assert e.true_state.economy is None
    assert e.true_state.projects == {}
    assert e.true_state.population_accounts == {}
    e.advance()
    new = e.save(tmp_path / "new.json")
    assert json.loads(new.read_text(encoding="utf-8"))["payload"]["schema_version"] == 2
    e.close()


def test_future_world_version_is_explicit_error():
    with pytest.raises(ValueError, match="schema"):
        World.from_dict({"date": "1897-01-01", "schema_version": 999})


def test_common_project_pipeline_lag_and_resume(engine, tmp_path):
    p = engine.plan_project("rail", "a", "r1", "infrastructure", "railways", 1)
    assert p["status"] == "planned"
    assert engine.true_state.regions["r1"].infrastructure.get("railways", 0) == 0
    engine.fund_project("rail")
    engine.advance(100)
    before = engine.true_state.projects["rail"]
    resumed = Engine.load(engine.save(tmp_path / "project.json"), tmp_path / "project.db")
    assert resumed.true_state.projects["rail"] == before
    engine.advance(295)
    resumed.advance(295)
    assert engine._payload() == resumed._payload()
    assert engine.true_state.projects["rail"]["status"] == "completed"
    assert engine.true_state.regions["r1"].infrastructure["railways"] == 1
    resumed.close()


def test_project_disruption_postpones_completion_and_cancellation(engine):
    engine.plan_project("p", "a", "r1", "infrastructure", "roads", 1)
    engine.fund_project("p")
    deadline = engine.true_state.projects["p"]["expected_completion_date"]
    engine.control_project("p", "disrupt")
    engine.advance(30)
    assert engine.true_state.projects["p"]["progress"] == 0
    assert engine.true_state.projects["p"]["expected_completion_date"] > deadline
    engine.control_project("p", "resume")
    engine.advance(10)
    assert engine.true_state.projects["p"]["progress"] > 0
    engine.control_project("p", "cancel")
    engine.advance(400)
    assert engine.true_state.projects["p"]["status"] == "cancelled"
    assert engine.true_state.regions["r1"].infrastructure.get("roads", 0) == 0


def test_project_without_funding_does_not_advance(engine):
    engine.plan_project("p", "a", "r1", "infrastructure", "roads", 1)
    engine.advance(400)
    assert engine.true_state.projects["p"]["progress"] == 0
