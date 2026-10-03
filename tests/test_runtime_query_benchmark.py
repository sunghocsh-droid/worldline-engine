"""Runtime query measurements preserve results and identify the exact PRE source."""
import pytest
from pathlib import Path

from worldline.runtime_query_benchmark import (
    BASELINE_COMMIT, REPOSITORY, _inside_repository, archive_queries, compare_queries,
    current_modules, exact_reference, measure_call,
    campaign_fingerprints, run_campaign_comparison,
)


@pytest.mark.parametrize("repetitions,batch", [(0, 1), (True, 1), (1, 0), (1, 1.5)])
def test_query_measurement_rejects_invalid_sample_counts(repetitions, batch):
    with pytest.raises(ValueError, match="whole"):
        measure_call(lambda: {}, repetitions=repetitions, batch=batch)


def test_query_measurement_rejects_result_drift_including_json_types():
    values = iter([{"value": 1}, {"value": True}])
    with pytest.raises(ValueError, match="logical result"):
        measure_call(lambda: next(values), repetitions=1)


def test_runtime_query_reference_is_requested_git_commit_and_not_crisis_fixture():
    reference = exact_reference()
    assert reference.commit == BASELINE_COMMIT
    assert reference.engine.__name__.startswith("_worldline_performance_baseline_")
    assert "baseline_source" in Path(reference.engine.__file__).parts
    assert "military.py" in reference.source_sha256


def test_archive_measurement_fingerprints_match_exact_reference():
    before = archive_queries(exact_reference(), blobs=8, repetitions=1)
    after = archive_queries(current_modules(), blobs=8, repetitions=1)
    assert before["history"]["sql_statements"]["select"] == 9
    assert before["export"]["sql_statements"]["select"] == 10
    for query in ("history", "export"):
        assert before[query]["result_sha256"] == after[query]["result_sha256"]


def test_benchmark_evidence_cannot_write_outside_canonical_repository():
    with pytest.raises(ValueError, match="canonical repository"):
        _inside_repository(REPOSITORY.parent / "outside-runtime-query-evidence.json")


def test_query_comparison_detects_changed_fingerprint():
    item = {"result_sha256": "before", "per_call_seconds": 2, "sql_statements": {"total": 3}}
    def evidence(value):
        return {"workloads": {"archive": {"history": value, "export": item}, "metadata": item,
                              "owned_regions": item, "cold_crisis": {"effects": item, "messages": item}}}
    assert compare_queries(evidence(item), evidence(item))["equal"]
    after = {**item, "result_sha256": "changed", "per_call_seconds": 1}
    result = compare_queries(evidence(item), evidence(after))
    assert not result["equal"]
    assert result["queries"]["archive.history"]["speedup"] == 2


def test_empty_query_results_do_not_hide_changed_workload_size():
    item = {"result_sha256": "same", "per_call_seconds": 1, "sql_statements": []}
    before = {"workloads": {"archive": {"history": item, "export": item, "blobs": 500}, "metadata": item,
                            "owned_regions": item, "cold_crisis": {"effects": item, "messages": item, "retained": 1000}}}
    import copy
    after = copy.deepcopy(before)
    after["workloads"]["cold_crisis"]["retained"] = 1
    result = compare_queries(before, after)
    assert not result["equal"]
    assert not result["workload_dimensions_equal"]["cold_crisis"]


@pytest.mark.parametrize("months,repetitions,seed", [(1, 3, 941), (True, 3, 941), (2, 0, 941), (2, 3, True)])
def test_campaign_comparison_rejects_invalid_sample_parameters(months, repetitions, seed):
    with pytest.raises(ValueError):
        run_campaign_comparison(REPOSITORY / "work/runtime_query/invalid", months=months,
                                repetitions=repetitions, seed=seed)


def test_campaign_fingerprint_compares_json_types_in_world_rng_ledger_rows_and_actions():
    import copy
    original = {"final_logical": {"world": {"value": 1}, "rng": [1], "ledger": [{"value": 1}]},
                "rows": [{"value": 1}], "actions": [{"value": 1}]}
    before = campaign_fingerprints(original)
    for key in ("world", "rng", "ledger", "rows", "actions"):
        after = copy.deepcopy(original)
        if key in ("world", "rng", "ledger"):
            after["final_logical"][key] = [True]
        else:
            after[key] = [True]
        result = campaign_fingerprints(after)
        assert before[key] != result[key]
        assert all(before[other] == result[other] for other in before if other != key)
