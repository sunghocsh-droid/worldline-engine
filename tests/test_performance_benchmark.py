"""Measurements use real production paths and never relabel historical results."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import pstats
import random

import pytest

from worldline.crisis_benchmark import CrisisReplay, campaign_config, campaign_world, encode, source_hashes
from worldline.performance_benchmark import (PROFILE_BOUNDARIES, advance_days, canonical_sha256,
    compare_campaigns, compare_fingerprints, history_counts, load_baseline_modules, logical_fingerprints,
    process_memory, profile_categories, restore_original_checkpoint, run_measured_campaign,
    resume_measured_campaign, run_original_scaling, run_scaling, scaling_scenario)


def test_streaming_digest_matches_exact_historical_encoder_and_distinguishes_json_types():
    for data in ({"한국": [1, 2.0, True, None, "é"]}, ["sequence", {"a": 7}]):
        assert canonical_sha256(data) == hashlib.sha256(encode(data).encode("utf-8")).hexdigest()
    assert len({canonical_sha256({"x": value}) for value in (1, 1.0, True)}) == 3


def test_isolated_comparator_loads_verified_portable_original_without_replacing_production():
    import worldline.models
    baseline = load_baseline_modules()
    assert baseline.directory.name == "crisis_v1_source"
    assert len(baseline.source_sha256) == 27
    assert "performance_benchmark.py" not in baseline.source_sha256
    assert baseline.models.World is not worldline.models.World
    assert baseline.models.World.__module__.startswith(baseline.package_name)
    assert load_baseline_modules().models is baseline.models


def test_isolated_comparator_rejects_wrong_manifest():
    baseline = load_baseline_modules()
    wrong = copy.deepcopy(baseline.source_sha256)
    wrong["models.py"] = "0" * 64
    with pytest.raises(ValueError, match="manifest"):
        load_baseline_modules(expected_sources=wrong)


def test_isolated_comparator_rejects_missing_sources(tmp_path):
    with pytest.raises(ValueError, match="unavailable"):
        load_baseline_modules(tmp_path)


def test_npc_campaign_freshness_tracks_extracted_runtime_and_storage_modules():
    from worldline import npc_benchmark, npc_runtime, state_storage
    hashes = npc_benchmark.source_hashes()
    for module in (npc_runtime, state_storage):
        path = Path(module.__file__)
        assert hashes[path.name] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert hashes == source_hashes()


def test_fingerprints_detect_private_information_rng_and_pending_changes():
    replay = CrisisReplay()
    before = logical_fingerprints(replay.world, replay.rng, replay.trace)
    after = dict(before)
    for key in ("rng", "npc_reports", "npc_memories", "pending_decisions", "event_sequence"):
        after[key] = "changed"
    result = compare_fingerprints(before, after)
    assert not result["equal"]
    assert set(result["differences"]) == {"rng", "npc_reports", "npc_memories", "pending_decisions", "event_sequence"}
    assert compare_fingerprints(before, dict(before))["equal"]


def test_campaign_comparison_excludes_only_actual_metadata_and_catches_small_real_changes():
    before = {"source_sha256": {"old": "hash"}, "runtime_seconds": 10,
        "final_world": {"reports": [1]}, "final_rng": [2], "trace": {"sha256": "old"}}
    after = copy.deepcopy(before)
    after.update(source_sha256={"new": "hash"}, runtime_seconds=2)
    result = compare_campaigns(before, after)
    assert result["equal"] and result["before_sources"] != result["after_sources"]
    after["final_world"]["reports"][0] = True
    assert compare_campaigns(before, after)["differences"] == ["final_world"]


@pytest.mark.parametrize("days", [-1, True, 1.5])
def test_golden_tick_helper_rejects_invalid_day_counts(days):
    with pytest.raises(ValueError, match="days"):
        advance_days(CrisisReplay(), days)


def test_daily_golden_helper_is_exact_same_production_order_rng_and_trace_as_month():
    daily, monthly = CrisisReplay(), CrisisReplay()
    advance_days(daily, 31)
    monthly.month()
    assert daily.world.date == "1897-02-01"
    assert logical_fingerprints(daily.world, daily.rng, daily.trace) == logical_fingerprints(monthly.world, monthly.rng, monthly.trace)


def test_explicit_historical_checkpoint_import_preserves_origin_world_rng_trace_and_file(tmp_path):
    baseline = load_baseline_modules()
    old = baseline.crisis_benchmark.CrisisReplay()
    old.month()
    path = tmp_path / "original.json.gz"
    old.checkpoint(path)
    raw_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    restored = restore_original_checkpoint(path, baseline.source_sha256)
    assert restored.source_sha256 == source_hashes()
    assert restored.origin_source_sha256 == baseline.source_sha256
    assert restored.source_sha256 != restored.origin_source_sha256
    assert restored.world.to_dict() == old.world.to_dict()
    assert restored.rng.getstate() == old.rng.getstate()
    assert restored.trace.to_dict() == old.trace.to_dict()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == raw_digest
    advance_days(restored, 14)
    advance_days(old, 14)
    assert logical_fingerprints(restored.world, restored.rng, restored.trace) == logical_fingerprints(old.world, old.rng, old.trace)


def test_historical_checkpoint_import_rejects_original_source_mismatch_and_corruption(tmp_path):
    baseline = load_baseline_modules()
    path = tmp_path / "original.json.gz"
    baseline.crisis_benchmark.CrisisReplay().checkpoint(path)
    wrong = copy.deepcopy(baseline.source_sha256)
    wrong["crisis.py"] = "0" * 64
    with pytest.raises(ValueError, match="manifest"):
        restore_original_checkpoint(path, wrong)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    payload["seed"] += 1
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    with pytest.raises(ValueError, match="checksum"):
        restore_original_checkpoint(path, baseline.source_sha256)


def test_all_thirteen_categories_choose_actual_internal_boundaries_without_nested_sum():
    stats = pstats.Stats()
    stats.stats = {
        ("npc_state.py", 1, "actor_view"): (1, 1, .1, 9),
        ("npc_state.py", 2, "_actor_read_view"): (1, 1, .2, 3),
        ("npc_state.py", 3, "effective_memory"): (1, 1, .3, 6),
        ("npc_state.py", 4, "_effective_entries"): (1, 1, .4, 2)}
    categories = profile_categories(stats)
    assert len(categories) == len(PROFILE_BOUNDARIES) == 13
    assert categories["npc_view_construction"]["cumulative_seconds"] == 3
    assert categories["memory_lookup"]["cumulative_seconds"] == 2
    assert len(categories["npc_view_construction"]["function_rows"]) == 2
    assert not categories["economy"]["called"]
    del stats.stats[("npc_state.py", 2, "_actor_read_view")]
    assert profile_categories(stats)["npc_view_construction"]["cumulative_seconds"] == 9


def test_resident_memory_measurement_is_actual_or_explicitly_unavailable():
    memory = process_memory()
    assert memory["method"]
    assert memory["rss_bytes"] is None or memory["rss_bytes"] > 0
    assert memory["peak_rss_bytes"] is None or memory["peak_rss_bytes"] >= memory["rss_bytes"]


def test_four_country_scaling_initial_conditions_are_exact_original_scenario():
    world, config = scaling_scenario(4)
    assert world.to_dict() == campaign_world().to_dict()
    assert config == campaign_config()


@pytest.mark.parametrize("countries", [10, 20])
def test_larger_performance_worlds_have_five_actual_actors_each_and_valid_region_trade_border_links(countries):
    world, config = scaling_scenario(countries)
    counts = history_counts(world)
    assert counts["countries"] == counts["regions"] == countries
    assert counts["persons"] == countries * 5
    assert len(world.economy["trades"]) == len(config["borders"]) == countries
    for border in config["borders"].values():
        assert all(c in world.countries for c in border["countries"])
        assert all(r in world.regions for r in border["regions"])
    assert all(p["country"] in world.countries for p in world.npc_state["persons"].values())


@pytest.mark.parametrize("countries", [3, 5, True])
def test_scaling_harness_does_not_silently_change_requested_world_size(countries):
    with pytest.raises(ValueError, match="countries"):
        scaling_scenario(countries)


def test_scaling_old_and_new_ticks_produce_identical_results_without_timing_in_state():
    before = run_original_scaling(4, 1)
    after = run_scaling(4, 1)
    assert before["fingerprints"] == after["fingerprints"]
    assert before["closing"] == after["closing"]
    assert before["source_sha256"] != after["source_sha256"]
    assert after["total_seconds"] > 0 and len(after["month_seconds"]) == 1


def test_measured_reference_and_genuine_resume_keep_actual_checkpoint_provenance(tmp_path):
    reference, timing = run_measured_campaign(tmp_path, months=2, checkpoint_month=1)
    resumed, repeated = resume_measured_campaign(tmp_path, reference)
    assert compare_campaigns(reference, resumed)["equal"]
    assert repeated["actually_replayed_months"] == 1
    assert repeated["comparison"]["equal"]
    assert len(timing["month_seconds"]) == 2
    assert timing["simulation_seconds"] == sum(timing["month_seconds"])
    assert timing["milestones"][0]["month"] == 2
    checkpoint = CrisisReplay.restore(tmp_path / "optimized_checkpoint.json.gz")
    assert checkpoint.world.date == "1897-02-01"
    assert checkpoint.source_sha256 == source_hashes()
    assert (tmp_path / "campaign_performance.csv").read_text(encoding="utf-8-sig").startswith("month,years,date")
