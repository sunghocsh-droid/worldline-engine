import copy
import json
import math
from pathlib import Path
import pytest
from worldline import Engine
from worldline import economy, projects
from worldline.audit import Auditor, AuditError
from worldline.benchmark import (Replay, reference_world, run_case, policy_for, export,
                                verify_explanation, PROFILES, BOTTLENECKS)


def test_research_replay_matches_sqlite_world_and_rng(tmp_path):
    world = reference_world("early_industrial")
    replay = Replay(world)
    engine = Engine.create(tmp_path / "parity.sqlite", world, replay.seed)
    for month in range(12):
        # First common settlement, then identical decisions and shock hooks.
        if month:
            changes = policy_for(replay.world, "balanced", month)
            economy.change_policy(replay.world, "a", changes)
            engine.set_policy("a", changes)
        if month == 6:
            economy.add_shock(replay.world, "war", "war_disruption", "ra", .8, 90)
            engine.apply_shock("war", "war_disruption", "ra", .8, 90)
        replay.month()
        engine.advance(1, "month")
        assert engine.true_state.to_dict() == replay.world.to_dict()
        assert engine.rng.getstate() == replay.rng.getstate()
    engine.close()


@pytest.mark.parametrize("profile", PROFILES)
def test_thirty_year_baseline_stability(profile):
    result = run_case({"profile": profile, "months": 360})
    initial, final = result["records"][0], result["records"][-1]
    assert .8 < final["gdp"] / initial["gdp"] < 3
    assert .7 < final["gdp_per_capita"] / initial["gdp_per_capita"] < 2
    assert .8 < final["population"] / initial["population"] < 2
    assert final["debt"] == 0


def test_same_seed_policy_benchmark_is_identical(tmp_path):
    spec = {"profile": "industrial", "path": "balanced", "months": 24, "checkpoints": [12, 24], "seed": 17}
    left, right = run_case(spec), run_case(spec)
    assert left == right
    export([left, right], tmp_path, "seed-check")
    raw = json.loads((tmp_path / "seed-check.json").read_text(encoding="utf-8"))
    assert raw["seed"] == 17 and raw["seeds"] == [17]


def test_thirty_year_research_checkpoint_resume(tmp_path):
    left = Replay(reference_world("preindustrial"))
    right = Replay(reference_world("preindustrial"))
    for month in range(360):
        if month:
            changes = policy_for(left.world, "balanced", month)
            economy.change_policy(left.world, "a", changes)
            economy.change_policy(right.world, "a", changes)
        left.month()
        right.month()
        if month == 179:
            right.checkpoint(tmp_path / "research.json")
            right = Replay.restore(tmp_path / "research.json")
    assert left.world.to_dict() == right.world.to_dict()
    assert left.rng.getstate() == right.rng.getstate()


def test_ten_year_sqlite_save_resume_world_rng_and_ledger(tmp_path):
    world = reference_world("early_industrial")
    engine = Engine.create(tmp_path / "original.sqlite", world, 91)
    engine.set_policy("a", {"education": 10, "infrastructure": 10, "subsidy": 10})
    engine.advance(60, "month")
    resumed = Engine.load(engine.save(tmp_path / "five_years.json"), tmp_path / "resumed.sqlite")
    engine.advance(60, "month")
    resumed.advance(60, "month")
    assert engine._payload() == resumed._payload()
    assert engine.ledger() == resumed.ledger()
    engine.close()
    resumed.close()


def test_replacement_is_income_funded_and_waits_for_construction():
    replay = Replay(reference_world("early_industrial"))
    initial = sum(s["capital_stock"] for s in replay.world.economy["regions"]["ra"]["sectors"].values())
    replay.month()
    e = replay.world.economy["countries"]["a"]
    f = e["finance"]
    assert 0 < f["replacement_reserve"] <= f["replacement_need"]
    assert f["replacement_reserve"] + f["net_private_saving"] <= f["private_disposable_income"]
    assert sum(e["replacement_savings"].values()) == pytest.approx(f["replacement_reserve"])
    capital = sum(s["capital_stock"] for s in replay.world.economy["regions"]["ra"]["sectors"].values())
    assert capital < initial  # Saving is not immediate capital creation.
    for _ in range(11): replay.month()
    ps = [p for p in replay.world.projects.values() if p["source"] == "private"]
    assert ps and all(p["status"] != "completed" for p in ps)


def test_legacy_save_keeps_original_economic_revision(tmp_path):
    path = Path(__file__).parents[1] / "examples/economy_v1_month_one.json"
    engine = Engine.load(path, tmp_path / "legacy.sqlite")
    assert "model_revision" not in engine.true_state.economy
    engine.advance(12, "month")
    assert "replacement_savings" not in engine.true_state.economy["countries"]["a"]
    assert engine.true_state.economy.get("model_revision", 1) == 1
    engine.close()


def test_legacy_model_upgrade_is_explicit_and_preserves_assets(tmp_path):
    path = Path(__file__).parents[1] / "examples/economy_v1_month_one.json"
    engine = Engine.load(path, tmp_path / "legacy-upgrade.sqlite")
    before, rng = engine.true_state, engine.rng.getstate()
    engine.upgrade_economy()
    after = engine.true_state
    assert after.economy["version"] == 2
    assert before.countries == after.countries
    assert before.projects == after.projects
    assert before.economy["regions"] == after.economy["regions"]
    assert engine.rng.getstate() == rng
    assert engine.ledger()[-1]["kind"] == "economic_model_upgraded"
    resumed = Engine.load(engine.save(tmp_path / "upgraded.json"), tmp_path / "upgraded.sqlite")
    engine.advance(12, "month")
    resumed.advance(12, "month")
    assert engine._payload() == resumed._payload()
    assert engine.ledger() == resumed.ledger()
    with pytest.raises(ValueError, match="legacy"): engine.upgrade_economy()
    engine.close()
    resumed.close()


def test_revision_and_replacement_accounting_are_validated():
    w = reference_world("preindustrial")
    w.economy["model_revision"] = 999
    with pytest.raises(AuditError, match="revision"): Auditor().check(w)
    w.economy["model_revision"] = 2
    w.economy["countries"]["a"]["replacement_savings"] = {"machinery": -1}
    with pytest.raises(AuditError, match="replacement"): Auditor().check(w)


def test_economic_model_version_distinguishes_old_formula_readers():
    w = reference_world("preindustrial")
    assert w.economy["version"] == w.economy["model_revision"] == 2
    w.economy["version"] = 1
    with pytest.raises(AuditError, match="version"): Auditor().check(w)


@pytest.mark.parametrize("parameter", economy.MODEL_PARAMETERS)
def test_model_parameter_sensitivity_is_persisted_and_validated(parameter, tmp_path):
    value = economy.MODEL_PARAMETERS[parameter] * 1.1
    replay = Replay(reference_world("industrial", model_parameters={parameter: value}))
    replay.month()
    replay.checkpoint(tmp_path / "parameters.json")
    resumed = Replay.restore(tmp_path / "parameters.json")
    assert resumed.world.economy["model_parameters"] == {parameter: value}
    replay.month()
    resumed.month()
    assert replay.world.to_dict() == resumed.world.to_dict()
    resumed.world.economy["model_parameters"][parameter] = 999
    with pytest.raises(AuditError, match="parameter"): Auditor().check(resumed.world)


def test_replacement_project_ids_do_not_depend_on_json_key_order():
    left = Replay(reference_world("industrial"))
    left.month()
    # SQLite snapshots sort mapping keys; equivalent data must execute identically.
    right = Replay(type(left.world).from_dict(json.loads(json.dumps(left.world.to_dict(), sort_keys=True))))
    right.rng.setstate(left.rng.getstate())
    for _ in range(11):
        left.month()
        right.month()
    assert left.world.projects == right.world.projects
    assert left.world.to_dict() == right.world.to_dict()


def test_horizon_prefix_is_identical_to_independent_short_run():
    short = run_case({"profile": "preindustrial", "path": "balanced", "months": 12})
    long = run_case({"profile": "preindustrial", "path": "balanced", "months": 24})
    assert short["initial_sha256"] == long["initial_sha256"]
    assert short["records"] == long["records"]  # Default records only include month 12 in both.


def test_foreign_equity_shock_is_once_and_builds_with_lag():
    replay = Replay(reference_world("industrial"))
    w = replay.world
    capital = sum(s["capital_stock"] for s in w.economy["regions"]["ra"]["sectors"].values())
    economy.add_shock(w, "inflow", "foreign_investment_boom", "ra", 1, 90)
    replay.month()
    commitment = w.economy["shocks"]["inflow"]["foreign_capital_committed"]
    assert commitment == pytest.approx(capital * .2 * .5)
    assert w.economy["countries"]["a"]["foreign_savings"] >= commitment
    replay.month()
    assert w.economy["shocks"]["inflow"]["foreign_capital_committed"] == commitment
    assert w.economy["countries"]["a"]["foreign_savings"] < commitment * 1.1
    assert not any(p["source"] == "foreign" and p["status"] == "completed" for p in w.projects.values())


def test_zero_raw_input_requirement_is_not_rationed_by_raw_shortage():
    w = reference_world("early_industrial")
    r = w.economy["regions"]["ra"]
    for name in ("mining", "energy"):
        r["sectors"][name]["capital_stock"] = r["sectors"][name]["initial_capital_stock"] = 0
    r["stocks"].update(energy=1000, raw_materials=0)
    w.economy["trades"] = {}
    legacy = copy.deepcopy(w)
    legacy.economy["version"] = 1
    legacy.economy.pop("model_revision")
    current, old = Replay(w), Replay(legacy)
    current.month()
    old.month()
    sectors = current.world.economy["regions"]["ra"]["sectors"]
    assert sectors["agriculture"]["output"] > 0
    assert sectors["heavy_industry"]["output"] == 0
    assert sectors["agriculture"]["raw_materials_used"] == 0
    assert old.world.economy["regions"]["ra"]["sectors"]["agriculture"]["output"] == 0
    assert "raw_materials" not in current.world.economy["countries"]["a"]["report"]["bottlenecks"]["ra"]["agriculture"]
    verify_explanation(current.world)


def test_closed_foreign_policy_rejects_equity_commitment():
    replay = Replay(reference_world("industrial"))
    economy.change_policy(replay.world, "a", {"foreign_investment_openness": 0})
    economy.add_shock(replay.world, "inflow", "foreign_investment_boom", "ra", 1)
    replay.month()
    assert replay.world.economy["shocks"]["inflow"]["foreign_capital_committed"] == 0


def test_repeated_technician_import_cannot_exceed_one():
    w = reference_world("industrial")
    effect = {"region": "ra", "effects": [{"type": "technology", "target": "machinery", "amount": 20}]}
    for _ in range(10): projects.apply_effects(w, effect)
    assert w.economy["regions"]["ra"]["sectors"]["machinery"]["technology"]["foreign_experts"] == 1
    Auditor().check(w)


@pytest.mark.parametrize("bottleneck", BOTTLENECKS)
def test_bottleneck_explanation_reconstructs_production(bottleneck):
    replay = Replay(reference_world("industrial", bottleneck=bottleneck))
    if bottleneck == "skilled_no_capital":
        r = replay.world.economy["regions"]["ra"]
        assert r["skilled_share"] > .5
        assert sum(s["capital_stock"] for s in r["sectors"].values()) < 20
    replay.month()
    verify_explanation(replay.world)
    report = replay.world.economy["countries"]["a"]["report"]
    labels = report["bottlenecks"]["ra"]["machinery"]
    expected = {"unskilled_capital": "skills", "skilled_no_capital": "capital", "no_energy": "energy",
        "no_transport": "transport", "access_no_capability": "domestic_capability", "large_uneducated": "skills"}
    assert expected[bottleneck] in labels
    if bottleneck == "no_transport":
        assert report["trade_routes"]["a-export"]["port_access"] == 0
    if bottleneck == "skilled_no_capital": assert "skills" not in labels


@pytest.mark.parametrize("path", ["baseline", "balanced", "military_heavy", "debt_boom", "technology_development"])
def test_policy_explanations_use_actual_internal_results(path):
    replay = Replay(reference_world("industrial"))
    replay.month()
    for month in range(24):
        economy.change_policy(replay.world, "a", policy_for(replay.world, path, month))
        replay.month()
        verify_explanation(replay.world)


def test_war_explanation_and_recovery_follow_expiry():
    replay = Replay(reference_world("early_industrial"))
    replay.month()
    economy.add_shock(replay.world, "war", "war_disruption", "ra", .9, 90)
    replay.month()
    impact = replay.world.countries["a"].gdp
    for _ in range(12): replay.month()
    verify_explanation(replay.world)
    assert replay.world.economy["regions"]["ra"]["shock_factors"]["disruption"] == 1
    assert replay.world.countries["a"].gdp > impact


def test_checkpoint_corruption_is_not_silently_read(tmp_path):
    replay = Replay(reference_world("industrial"))
    path = tmp_path / "checkpoint.json"
    replay.checkpoint(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["world"]["countries"]["a"]["gdp"] = 123
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint"): Replay.restore(path)
