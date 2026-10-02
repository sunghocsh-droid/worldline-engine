import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from worldline import Engine
from worldline.crisis_benchmark import (COUNTRIES, SEED, CrisisReplay, TraceAccumulator,
    campaign_config, campaign_world, deterministic_payload, encode, export,
    generate_report, run_campaign, run_stress_cases, source_hashes)
from worldline.crisis_benchmark import summarize


@pytest.fixture(scope="module")
def short_campaign():
    return run_campaign(months=2)


def test_crisis_fictional_world_has_varied_regimes_local_authorities_and_hazards():
    world = campaign_world()
    state = world.npc_state
    assert len(state["persons"]) == 20
    assert len({g["regime"] for g in state["governments"].values()}) == 4
    assert len(world.economy["trades"]) == 4
    assert world.relations["d-a"]["status"] == "allied"
    assert world.relations["b-c"]["status"] == "rival"
    for c in COUNTRIES:
        actor = state["persons"][c + ":local"]
        assert actor["region"] == "r" + c
        assert actor["authority"] == ["local_action"]
    profiles = campaign_config()["regions"]
    assert len({encode(p["hazard_rates"]) for p in profiles.values()}) == 4


def test_benchmark_rolling_trace_is_sensitive_to_order_and_facts():
    first, second, changed = TraceAccumulator(), TraceAccumulator(), TraceAccumulator()
    events = [{"kind": "event_started", "value": 1}, {"kind": "event_resolved", "value": 2}]
    for event in events:
        first.observe(event)
    for event in reversed(events):
        second.observe(event)
    for event in [{"kind": "event_started", "value": 3}, events[1]]:
        changed.observe(event)
    assert len({first.digest, second.digest, changed.digest}) == 3
    assert first.count == second.count == changed.count == 2
    restored = TraceAccumulator.restore(first.to_dict())
    assert restored.to_dict() == first.to_dict()


def test_causal_summary_counts_social_escalation_and_unique_mediated_events():
    from worldline import crisis_state
    replay = CrisisReplay(config={"settings": {"auto_enabled": False}})
    parent = crisis_state.add_event(replay.world, "protest", "rb", ["b"])
    child = crisis_state.add_event(replay.world, "riot", "rb", ["b"], parent_event=parent["event_id"])
    crisis_state.close_event(replay.world, parent["event_id"], "escalated")
    border = crisis_state.add_event(replay.world, "diplomatic_crisis", "rb", ["b", "c"],
        actual_facts={"local_unilateral": True, "misinformation": True},
        causes=[{"trigger_kind": "local_unilateral_action"}])
    border["responses"] = [{"country": c, "response": "mediate", "actual_cost": 0} for c in ("b", "c")]
    crisis_state.close_event(replay.world, border["event_id"], "contained")
    summary = summarize(replay.world, replay.trace)
    assert child["escalation_level"] == parent["escalation_level"]
    assert summary["escalations"] == 1
    assert summary["mediated_resolutions"] == 1
    assert summary["local_triggered_events"] == 1
    assert summary["misinformation_triggered_events"] == 1


def test_crisis_benchmark_same_seed_reproduces_short_campaign(short_campaign):
    repeated = run_campaign(months=2)
    assert encode(deterministic_payload(short_campaign)) == encode(deterministic_payload(repeated))


@pytest.mark.parametrize("kwargs", [{"checkpoint_month": 1}, {"checkpoint_path": "unused"},
    {"checkpoint_path": "unused", "checkpoint_month": 3},
    {"checkpoint_path": "unused", "checkpoint_month": True}])
def test_benchmark_does_not_claim_a_checkpoint_it_did_not_restore(kwargs):
    with pytest.raises(ValueError, match="Checkpoint"):
        run_campaign(months=2, **kwargs)


def test_crisis_benchmark_checkpoint_preserves_world_streams_and_event_digest(tmp_path):
    left = CrisisReplay(seed=29)
    left.month()
    path = tmp_path / "checkpoint.json.gz"
    left.checkpoint(path)
    right = CrisisReplay.restore(path)
    assert left.world.to_dict() == right.world.to_dict()
    assert left.rng.getstate() == right.rng.getstate()
    assert left.trace.to_dict() == right.trace.to_dict()
    left.month()
    right.month()
    assert left.world.to_dict() == right.world.to_dict()
    assert left.rng.getstate() == right.rng.getstate()
    assert left.trace.to_dict() == right.trace.to_dict()


def test_crisis_research_ticks_match_actual_sqlite_engine(tmp_path):
    research = CrisisReplay(seed=39)
    engine = Engine.create(tmp_path / "game.sqlite", campaign_world(), seed=39)
    try:
        engine.enable_crisis(campaign_config())
        research.month()
        engine.advance(1, "month")
        assert engine.true_state.to_dict() == research.world.to_dict()
        assert engine.rng.getstate() == research.rng.getstate()
        actual = TraceAccumulator()
        for event in engine.ledger():
            if event["kind"] == "system_event":
                actual.observe(event["payload"])
        assert actual.to_dict() == research.trace.to_dict()
    finally:
        engine.close()


def test_crisis_benchmark_corrupt_checkpoint_is_rejected(tmp_path):
    path = tmp_path / "checkpoint.json.gz"
    CrisisReplay().checkpoint(path)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        raw = json.load(stream)
    raw["seed"] += 1
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(raw, stream)
    with pytest.raises(ValueError, match="checkpoint"):
        CrisisReplay.restore(path)


def test_crisis_benchmark_incompatible_source_checkpoint_is_rejected(tmp_path):
    path = tmp_path / "checkpoint.json.gz"
    CrisisReplay().checkpoint(path)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        raw = json.load(stream)
    raw.pop("sha256")
    raw["source_sha256"]["crisis.py"] = "0" * 64
    raw["sha256"] = hashlib.sha256(encode(raw).encode("utf-8")).hexdigest()
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(raw, stream)
    with pytest.raises(ValueError, match="source changed"):
        CrisisReplay.restore(path)


def test_crisis_benchmark_exported_metrics_and_report_are_actual(short_campaign, tmp_path):
    export(short_campaign, tmp_path)
    with gzip.open(tmp_path / "campaign.json.gz", "rt", encoding="utf-8") as stream:
        assert json.load(stream)["summary"] == short_campaign["summary"]
    rows = (tmp_path / "campaign.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 1 + (short_campaign["months"] + 1) * 4
    report = tmp_path / "report.md"
    generate_report(short_campaign, report, "fixture 검증")
    text = report.read_text(encoding="utf-8")
    assert "전투 해결 횟수는 0" in text
    assert str(short_campaign["summary"]["event_count"]) in text


@pytest.fixture(scope="module")
def stress_cases():
    path = Path(__file__).parents[1] / "benchmarks/crisis/stress_cases.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if data.get("source_sha256") != source_hashes() or data.get("seed") != SEED or data.get("months_after_setup") != 3:
        cases = run_stress_cases(3)
        data = {"source_sha256": source_hashes(), "seed": SEED, "months_after_setup": 3, "cases": cases}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return data["cases"]


def test_all_eight_synthetic_extremes_have_real_conditions_and_audited_paths(stress_cases):
    assert set(stress_cases) == {"default_and_war", "harvest_blockade_low_reserves", "mobilization_and_labour",
        "identity_grievance", "hardline_border_commanders", "moderate_governments_local_incident",
        "growth_and_flood", "recession_and_small_petition"}
    assert stress_cases["default_and_war"]["opening"]["countries"]["b"]["distress_state"] == "default"
    assert stress_cases["harvest_blockade_low_reserves"]["opening"]["countries"]["c"]["reserves"] == 0
    assert stress_cases["mobilization_and_labour"]["opening"]["countries"]["b"]["military"] >= 2000
    assert stress_cases["growth_and_flood"]["opening"]["countries"]["a"]["gdp"] > 0
    assert stress_cases["recession_and_small_petition"]["opening"]["countries"]["c"]["gdp"] > 0
    growth = stress_cases["growth_and_flood"]["economic_context"]
    recession = stress_cases["recession_and_small_petition"]["economic_context"]
    assert growth["external_equity_committed"] > 0
    assert growth["measured_growth"] > 0
    assert growth["pre_flood_gdp"] > growth["baseline_gdp"]
    assert recession["measured_growth"] < 0
    assert recession["pre_petition_capital"] < recession["pre_destruction_capital"]
    for case in stress_cases.values():
        assert case["months_after_setup"] == 3
        assert case["summary"]["combat_resolutions"] == 0
        paths = {e["event_id"]: e for e in case["event_paths"]}
        for event in paths.values():
            if event["parent_event"]:
                assert event["parent_event"] in paths
                assert event["event_id"] in paths[event["parent_event"]]["child_events"]


def test_extreme_conditions_keep_finite_resources_and_do_not_invent_battle_deaths(stress_cases):
    for case in stress_cases.values():
        for row in case["closing"]["countries"].values():
            assert row["population"] >= 0 and row["debt"] >= 0 and row["reserves"] >= 0
            assert 0 <= row["creditworthiness"] <= 100
            assert row["war_losses"] == 0
        for loss in case["summary"]["realized_losses"].values():
            assert loss["capital_destruction"] >= 0 and loss["paid_response_cost"] >= 0


@pytest.fixture(scope="module")
def long_campaigns():
    """Reuse only real matching production runs; otherwise do all thirty years."""
    directory = Path(__file__).parents[1] / "benchmarks/crisis"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "campaign.json.gz"
    data = None
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
    if data is None or data.get("months") != 360 or data.get("seed") != SEED or data.get("source_sha256") != source_hashes():
        data = run_campaign(months=360)
        export(data, directory)
    replay_path, verified_path = directory / "replayed.json.gz", directory / "replay_verification.json"
    checkpoint = directory / "replay_checkpoint.json.gz"
    verified = json.loads(verified_path.read_text(encoding="utf-8")) if verified_path.exists() else {}
    matching = (replay_path.exists() and checkpoint.exists() and
        verified.get("source_sha256") == source_hashes() and verified.get("seed") == SEED and
        verified.get("months") == 360 and verified.get("checkpoint_month") == 180 and
        verified.get("replayed_file_sha256") == hashlib.sha256(replay_path.read_bytes()).hexdigest())
    if matching:
        with gzip.open(replay_path, "rt", encoding="utf-8") as stream:
            repeated = json.load(stream)
        restored = CrisisReplay.restore(checkpoint)
        assert restored.world.date == "1912-01-01"
        assert restored.trace.count <= repeated["trace"]["count"]
    else:
        repeated = run_campaign(months=360, checkpoint_path=checkpoint, checkpoint_month=180,
            progress=lambda month: print(f"Crisis replay {month}/360 months", flush=True))
        with gzip.open(replay_path, "wt", encoding="utf-8") as stream:
            json.dump(repeated, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        verified = {"source_sha256": source_hashes(), "seed": SEED, "months": 360, "checkpoint_month": 180,
            "replayed_file_sha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
            "initial_sha256": repeated["initial_sha256"], "trace_sha256": repeated["trace"]["sha256"]}
        verified_path.write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
    return data, repeated


def test_crisis_thirty_year_same_seed_and_midpoint_save_reproduce_all_state(long_campaigns):
    uninterrupted, resumed = long_campaigns
    assert encode(deterministic_payload(uninterrupted)) == encode(deterministic_payload(resumed))


def test_crisis_thirty_year_campaign_has_causal_activity_and_conserves_finance_labour(long_campaigns):
    data, _ = long_campaigns
    assert data["months"] == 360
    assert data["summary"]["event_count"] > 0
    assert data["summary"]["combat_resolutions"] == 0
    assert len(data["records"]) == 361
    final = data["records"][-1]["countries"]
    assert len(final) == 4
    assert any(row["capital_destruction"] > 0 for row in final.values())
    for record in data["records"]:
        for row in record["countries"].values():
            assert row["population"] >= 0
            assert row["debt"] >= 0 and row["reserves"] >= 0 and row["arrears"] >= 0
            assert 0 <= row["creditworthiness"] <= 100
            assert 0 <= row["food_security"] <= 1
            assert row["military"] <= row["population"]
            assert row["war_losses"] == 0
