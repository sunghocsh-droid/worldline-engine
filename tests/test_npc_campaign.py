import copy
import gzip
import hashlib
import json
import random
from pathlib import Path

import pytest

from worldline import Engine, credit, npc, npc_decision, npc_state
from worldline.audit import Auditor
from worldline.npc_benchmark import (COUNTRIES, SEED, CampaignReplay, campaign_world,
    encode, export, generate_report, run_campaign, source_hashes, summarize, verify_decision)


@pytest.fixture(scope="module")
def short_campaign():
    return run_campaign(months=2)


def test_fictional_campaign_uses_four_governments_and_private_reports():
    world = campaign_world()
    assert len(world.npc_state["persons"]) == 16
    assert len(world.npc_state["groups"]) == 8
    assert set(world.npc_state["governments"]) == set(COUNTRIES)
    assert len({g["regime"] for g in world.npc_state["governments"].values()}) == 4
    report = world.npc_state["reports"]["b:ruler"]["diplomacy"]
    assert report["data"]["target"] == "c"
    assert report["data"]["enemy_strength"] != world.armies["army-c"].personnel
    assert report["accuracy"] < report["confidence"]
    view = npc_state.actor_view(world.npc_state, "b:ruler", world.date)
    assert "accuracy" not in view["report_metadata"]["diplomacy"]
    assert "records" not in view


def test_campaign_decisions_are_diverse_and_authorized(short_campaign):
    summary = short_campaign["summary"]
    assert {"development", "fiscal_consolidation", "industrialization", "war_preparation"} <= set(summary["choices"])
    assert len(summary["actor_decisions"]) == 16
    assert summary["combat_resolutions"] == 0
    for event in short_campaign["events"]:
        if event["kind"] == "npc_action":
            action = event["action"]
            assert action["executor"] == action["country"] + ":ruler"
            assert "perceived" in action and "actual" in action
            assert action["execution"]["status"] in ("accepted", "rejected")


def test_government_actions_have_unambiguous_unique_ids(short_campaign):
    actions = [e["action"] for e in short_campaign["events"] if e["kind"] == "npc_action"]
    assert len({a["id"] for a in actions}) == len(actions)


def test_government_councils_keep_operating_across_short_and_long_months():
    data = run_campaign(months=3)
    for country in COUNTRIES:
        actions = [e["action"] for e in data["events"] if e["kind"] == "npc_action" and e["action"]["country"] == country]
        # Closing-date April 1 is a valid additional council after three months.
        assert {"1897-01", "1897-02", "1897-03"} <= {a["date"][:7] for a in actions}
        assert len({a["date"][:7] for a in actions}) == len(actions)


def test_ruler_in_cabinet_does_not_receive_a_second_vote():
    plain = campaign_world()
    listed = copy.deepcopy(plain)
    listed.npc_state["governments"]["a"]["cabinet"].append("a:ruler")
    Auditor().check(listed)
    results = []
    for world in (plain, listed):
        replay = CampaignReplay(world)
        replay.month()
        action = next(e["action"] for e in replay.events if e["kind"] == "npc_action" and e["action"]["country"] == "a")
        results.append(action["option"])
    assert results[0] == results[1]


def test_accepted_ruler_vacancy_configuration_does_not_crash():
    world = campaign_world()
    world.npc_state["governments"]["a"]["ruler"] = None
    issues = npc_state.validate(world.npc_state, world.countries, world.regions, world.date)
    if issues:
        assert any("ruler" in issue or "government" in issue for issue in issues)
    else:
        CampaignReplay(world).month()


def test_accepted_empty_evaluation_date_does_not_crash():
    world = campaign_world()
    world.npc_state["schedules"]["a:ruler"] = {"next_due": None, "last_evaluated": None}
    issues = npc_state.validate(world.npc_state, world.countries, world.regions, world.date)
    if issues:
        assert any("schedule" in issue for issue in issues)
    else:
        CampaignReplay(world).month()


def test_distressed_government_can_authorize_a_lower_future_budget():
    world = campaign_world()
    country, economic = world.countries["c"], world.economy["countries"]["c"]
    country.treasury, country.debt = 0, 100
    cr = economic["credit"]
    cr.update(domestic_debt=100, foreign_debt=0, distress_state="default", arrears=1000,
              default_count=1, missed_service_days=365)
    cr["emergency_used"] = dict(cr["emergency_capacity"])
    cr["public_asset_claims"] = 0
    cr["financial_access"] = {k: False for k in cr["financial_access"]}
    credit.refresh(world, "c")
    Auditor().check(world)
    npc.sense(world)
    view, options, actual_quotes = npc.options_for(world, "c:finance")
    decision = npc_decision.decide(view, options, random.Random(SEED))
    evaluated = next(e for e in decision["evaluations"] if e["option"] == "fiscal_consolidation")
    assert evaluated["eligible"] is True
    # A political budget authorization does not manufacture immediate liquidity.
    assert actual_quotes["fiscal_consolidation"]["normal_financial_feasibility"]["feasible"] is False
    assert actual_quotes["fiscal_consolidation"]["emergency_financial_feasibility"]["feasible"] is False


def test_false_information_and_memory_have_calculated_reasons(short_campaign):
    factors = short_campaign["summary"]["scoring_factors"]
    assert any(k.startswith("memory:") for k in factors)
    assert "belief:military_balance" in factors
    assert "bias:enemy_underestimation" in factors
    assert any(k.startswith("group:") for k in factors)


def test_campaign_explanation_scores_reconstruct_actual_decisions(short_campaign):
    for event in short_campaign["events"]:
        if event["kind"] == "npc_decision":
            verify_decision(event["decision"])
    corrupt = copy.deepcopy(next(e["decision"] for e in short_campaign["events"] if e["kind"] == "npc_decision"))
    corrupt["evaluations"][0]["score"] += 1
    with pytest.raises(ValueError, match="reconstruct"):
        verify_decision(corrupt)


def test_short_campaign_same_seed_reproduces_all_events_and_rng(short_campaign):
    assert run_campaign(months=2) == short_campaign


def test_campaign_checkpoint_preserves_pending_memories_actions_and_rng(tmp_path):
    left = CampaignReplay(campaign_world(), seed=20)
    left.month()
    path = tmp_path / "checkpoint.json"
    left.checkpoint(path)
    right = CampaignReplay.restore(path)
    assert left.world.to_dict() == right.world.to_dict()
    assert left.rng.getstate() == right.rng.getstate()
    left.month()
    right.month()
    assert left.world.to_dict() == right.world.to_dict()
    assert left.events == right.events
    assert left.rng.getstate() == right.rng.getstate()


def test_campaign_research_ticks_match_actual_sqlite_engine(tmp_path):
    initial = campaign_world()
    research = CampaignReplay(initial, seed=31)
    engine = Engine.create(tmp_path / "game.sqlite", initial, seed=31)
    try:
        research.month()
        engine.advance(1, "month")
        assert engine.true_state.to_dict() == research.world.to_dict()
        assert engine.rng.getstate() == research.rng.getstate()
        npc_events = [e["payload"] for e in engine.ledger()
            if e["kind"] == "system_event" and e["payload"].get("system") == "npc"]
        assert npc_events == research.events
    finally:
        engine.close()


def test_campaign_checkpoint_tampering_is_rejected(tmp_path):
    path = tmp_path / "checkpoint.json"
    CampaignReplay(campaign_world()).checkpoint(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["seed"] += 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint"):
        CampaignReplay.restore(path)


def test_campaign_checkpoint_requires_matching_source(tmp_path):
    path = tmp_path / "checkpoint.json"
    CampaignReplay(campaign_world()).checkpoint(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("sha256")
    data["source_sha256"]["npc_decision.py"] = "0" * 64
    data["sha256"] = hashlib.sha256(encode(data).encode()).hexdigest()
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        CampaignReplay.restore(path)


def test_campaign_export_and_report_match_actual_actions(tmp_path, short_campaign):
    export(short_campaign, tmp_path)
    assert json.loads((tmp_path / "campaign.json").read_text(encoding="utf-8"))["summary"] == short_campaign["summary"]
    assert len((tmp_path / "campaign.csv").read_text(encoding="utf-8-sig").splitlines()) == 13
    report = tmp_path / "report.md"
    generate_report(short_campaign, report, "연구 fixture 검증")
    text = report.read_text(encoding="utf-8")
    assert "전투 해결 횟수는 0" in text
    assert "fiscal_consolidation" in text
    assert str(short_campaign["summary"]["decision_count"]) in text


@pytest.fixture(scope="module")
def long_campaigns(tmp_path_factory):
    """Reuse only a matching production benchmark, then replay all twenty years."""
    path = Path(__file__).parents[1] / "benchmarks/npc/campaign.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if data is None or data.get("months") != 240 or data.get("seed") != SEED or data.get("source_sha256") != source_hashes():
        data = run_campaign(months=240)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    replay_file = directory / "replayed.json.gz"
    verified_file = directory / "replay_verification.json"
    checkpoint = directory / "replay_checkpoint.json"
    verified = json.loads(verified_file.read_text(encoding="utf-8")) if verified_file.exists() else {}
    matching = (replay_file.exists() and checkpoint.exists() and
        verified.get("source_sha256") == source_hashes() and verified.get("seed") == SEED and
        verified.get("months") == 240 and verified.get("checkpoint_month") == 120 and
        verified.get("replayed_file_sha256") == hashlib.sha256(replay_file.read_bytes()).hexdigest())
    if matching:
        with gzip.open(replay_file, "rt", encoding="utf-8") as stream:
            repeated = json.load(stream)
        restored = CampaignReplay.restore(checkpoint)
        assert restored.world.date == "1907-01-01"
        assert repeated["events"][:len(restored.events)] == restored.events
    else:
        repeated = run_campaign(months=240, checkpoint_path=checkpoint, checkpoint_month=120,
            progress=lambda month: print(f"NPC replay {month}/240 months", flush=True))
        with gzip.open(replay_file, "wt", encoding="utf-8") as stream:
            json.dump(repeated, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        verified = {"source_sha256": source_hashes(), "seed": SEED, "months": 240,
            "checkpoint_month": 120, "replayed_file_sha256": hashlib.sha256(replay_file.read_bytes()).hexdigest(),
            "initial_sha256": repeated["initial_sha256"]}
        verified_file.write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
    # Canonical JSON normalizes serialized tuple/list RNG representations.
    return data, repeated


def test_long_campaign_same_seed_and_midpoint_save_reproduce_twenty_years(long_campaigns):
    uninterrupted, resumed = long_campaigns
    assert encode(uninterrupted) == encode(resumed)


def test_long_campaign_diversity_resources_and_fiscal_invariants(long_campaigns):
    data, _ = long_campaigns
    summary = data["summary"]
    assert summary["decision_count"] > 2000
    assert {"war_preparation", "economic_policy"} <= set(summary["action_types"])
    assert len(summary["choices"]) >= 4
    assert summary["combat_resolutions"] == 0
    assert set(summary["country_decisions"]) == set(COUNTRIES)
    assert len(data["records"]) == 241
    # Not every government is made to collapse, and risk is not forbidden.
    end = data["records"][-1]["countries"]
    assert sum(r["distress_state"] == "normal" for r in end.values()) >= 2
    for record in data["records"]:
        for row in record["countries"].values():
            assert 0 <= row["creditworthiness"] <= 100
            assert row["debt"] >= 0 and row["arrears"] >= 0 and row["reserves"] >= 0
            assert row["labour"]["military"] + row["labour"]["employed"] + row["labour"]["unemployed"] <= row["labour"]["labour_force"] + 1e-6
    # Actual execution decisions are checked separately from political choices.
    assert any(e["action"]["kind"] == "war_preparation" and e["action"]["execution"]["status"] == "accepted"
               for e in data["events"] if e["kind"] == "npc_action")
