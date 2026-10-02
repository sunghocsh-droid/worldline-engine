import copy
import json
import math
from pathlib import Path

import pytest

from worldline import Engine, economy
from worldline.benchmark import Replay, reference_world, policy_for
from worldline.stabilization_benchmark import (PATHS, at, calibrated_case, case_specs,
    decompose_records, legacy_debt_diagnosis, run_case, extended_measure, verify_record)


@pytest.fixture(scope="module")
def calibrated():
    return json.loads((Path(__file__).parents[1] / "benchmarks/calibrated_final.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("profile", ("preindustrial", "early_industrial", "industrial"))
def test_original_debt_factor_decomposition_exactly_explains_gdp(profile, calibrated):
    case = calibrated_case(calibrated, profile, "debt_boom")
    for month in (120, 240, 360):
        decomposition = decompose_records(at(case, 0), at(case, month))
        explained = math.fsum(decomposition["contributions"].values()) + decomposition["rounding_residual"]
        assert explained == pytest.approx(decomposition["gdp_change"], abs=1e-8)
        assert math.fsum(s["change"] for s in decomposition["sectors"].values()) + decomposition["rounding_residual"] == pytest.approx(decomposition["gdp_change"])
        assert decomposition["contributions"]["market_access"] < 0
        assert decomposition["contributions"]["skilled_labour"] > 0


def test_requested_old_collapse_numbers_identify_actual_profile(calibrated):
    diagnosis = legacy_debt_diagnosis(calibrated)
    assert diagnosis["profile"] == "early_industrial"
    assert diagnosis["starting_gdp"] == 4014
    # These are provenance checks of existing immutable results, not new GDP targets.
    assert [r["gdp"] for r in diagnosis["records"]] == [3066, 1428, 1142]
    assert all(r["debt_service_monthly"] > r["revenue_monthly"] for r in diagnosis["records"])
    assert diagnosis["records"][-1]["machinery_input_supply"] == 1
    assert diagnosis["records"][-1]["machinery_capital_capacity"] > diagnosis["records"][-1]["machinery_labour_capacity"] * 5


def test_reference_case_matching_rejects_duplicate_or_confounded_cases(calibrated):
    case = calibrated_case(calibrated, "industrial", "balanced")
    with pytest.raises(ValueError, match="exactly one"):
        calibrated_case({"cases": [case, copy.deepcopy(case)]}, "industrial", "balanced")
    changed = copy.deepcopy(case)
    changed["spec"]["sensitivity"] = "saving"
    with pytest.raises(ValueError, match="exactly one"):
        calibrated_case({"cases": [changed]}, "industrial", "balanced")


def test_changed_explain_output_is_detected(calibrated):
    case = calibrated_case(calibrated, "early_industrial", "debt_boom")
    final = copy.deepcopy(at(case, 360))
    final["explain"]["production_factors"]["ra"]["machinery"]["input_supply"] *= .5
    with pytest.raises(ValueError, match="reconstruct"):
        decompose_records(at(case, 0), final)


def test_stabilization_case_specs_keep_profiles_seed_and_policies():
    specs = case_specs(seed=21)
    assert len(specs) == 15
    assert {s["path"] for s in specs} == set(PATHS)
    assert {s["seed"] for s in specs} == {21}
    assert {s["months"] for s in specs} == {360}
    with pytest.raises(ValueError): case_specs(months=0)


def test_stabilization_benchmark_same_seed_policies_and_records():
    spec = {"profile": "early_industrial", "path": "debt_boom", "months": 12, "checkpoints": [1, 12], "seed": 23}
    left, right = run_case(spec), run_case(spec)
    assert left == right
    assert left["records"][-1]["credit"]
    assert left["records"][-1]["regional_labour"]["ra"]["allocation"]
    for record in left["records"]: verify_record(record)
    assert len(left["timeline"]) == 12
    assert left["records"][-1]["cumulative"]["revenue"] == sum(r["revenue"] for r in left["timeline"])


def test_stabilized_research_checkpoint_preserves_credit_jobs_and_rng(tmp_path):
    world = reference_world("industrial")
    economy.enable_stabilization(world)
    left, right = Replay(world, 25), Replay(world, 25)
    for month in range(12):
        if month:
            changes = policy_for(left.world, "balanced", month)
            economy.change_policy(left.world, "a", changes)
            economy.change_policy(right.world, "a", changes)
        left.month()
        right.month()
        if month == 5:
            right.checkpoint(tmp_path / "six_months.json")
            right = Replay.restore(tmp_path / "six_months.json")
    assert left.world.to_dict() == right.world.to_dict()
    assert left.rng.getstate() == right.rng.getstate()


def test_stabilized_replay_matches_actual_sqlite_execution(tmp_path):
    world = reference_world("early_industrial")
    economy.enable_stabilization(world)
    replay = Replay(world, 29)
    engine = Engine.create(tmp_path / "parity.sqlite", world, 29)
    try:
        for month in range(3):
            if month:
                changes = policy_for(replay.world, "military_heavy", month)
                changes["emergency_finance"] = True
                economy.change_policy(replay.world, "a", changes)
                engine.set_policy("a", changes)
            replay.month()
            engine.advance(1, "month")
            assert replay.world.to_dict() == engine.true_state.to_dict()
            assert replay.rng.getstate() == engine.rng.getstate()
        record = extended_measure(replay.world, 3, {})
        record["finance"]["total_revenue"] += 1
        with pytest.raises(ValueError, match="finance"): verify_record(record)
    finally:
        engine.close()
