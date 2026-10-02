"""Political replacement choices use the existing NPC solver and real saved state."""
import copy
import json
import random
import sqlite3
from pathlib import Path

import pytest

from worldline import Engine, economy, npc, npc_state, crisis, military_state, military_bridge, credit
from worldline import military_politics as politics
from worldline import state_storage
from worldline.benchmark import reference_world
from worldline.models import World


@pytest.fixture
def world():
    w = reference_world("early_industrial")
    economy.enable_stabilization(w)
    s = npc_state.initialize_state()
    npc_state.add_person(s, "ruler", "a", "Ruler", role="ruler", authority=["mobilize"])
    npc_state.add_person(s, "general", "a", "General", role="military", authority=["mobilize"],
        preferences={"security": 1., "prestige": 1., "fiscal": 0., "prosperity": 0.},
        traits={"risk_tolerance": 1., "ambition": 1., "prestige_sensitivity": 1.})
    npc_state.add_person(s, "finance", "a", "Finance", role="finance", authority=["mobilize"],
        preferences={"fiscal": 1., "security": 0., "prestige": 0., "prosperity": 1.},
        traits={"risk_tolerance": 0., "pragmatism": 1.})
    npc_state.add_person(s, "local", "a", "Local commander", role="commander", authority=["local_action"])
    npc_state.add_person(s, "foreign", "b", "Foreign ruler", role="ruler", authority=["mobilize"])
    npc_state.configure_government(s, "a", "ruler", ["general", "finance"], autonomous=False)
    npc_state.configure_government(s, "b", "foreign", [], autonomous=False)
    npc_state.add_group(s, "army", "a", "Army institution", kind="military_branch", members=["general"],
        influence=.8, interests={"security": 1., "fiscal": -1.}, rivals=["treasury"])
    npc_state.add_group(s, "treasury", "a", "Treasury", members=["finance"],
        influence=.8, interests={"fiscal": 1.}, rivals=["army"])
    npc.initialize(w, s)
    crisis.initialize(w, {"settings": {"auto_enabled": False}}, rng=random.Random(82))
    military_state.initialize(w, random.Random(83))
    return w


OUTCOMES = {
    "replenish": {"security": 1., "fiscal": -1., "prestige": 1., "prosperity": -.8},
    "defer": {"security": -1., "fiscal": 1., "prestige": -.7, "prosperity": .5},
    "reduce_commitment": {"security": -.8, "fiscal": .6, "prestige": -.9, "prosperity": .3},
}


def report(w, actor="general", manpower=20, *, as_of=None, outcomes=None, perceived=None, topic="replacement:a"):
    data = {"replacement_request": {"recipient": "a", "manpower": manpower},
            "outcomes": copy.deepcopy(outcomes or OUTCOMES),
            "perceived": {"cost": manpower, "risk": .5, "cost_scale": 1000., **(perceived or {})}}
    npc_state.receive_report(w.npc_state, actor, topic, data, as_of or w.date, "received quartermaster forecast", reliability=.8)
    npc_state.receive_report(w.npc_state, actor, "economy", {"revenue": 100., "fiscal_risk": .5, "reserves": 100.},
                             as_of or w.date, "received finance report")
    return data


def training(w, key, receipt, manpower=20, **kwargs):
    military_bridge.train_replacements(w, key, "a", manpower,
        decision_actor=receipt["body"]["decision_actor"], approval_receipt=receipt["body"]["id"], **kwargs)
    return politics.consume_approval(w, receipt["body"]["id"], "a", manpower,
        replacement_id=key, decision_actor=receipt["body"]["decision_actor"],
        allow_emergency=kwargs.get("allow_emergency", False), duration_days=kwargs.get("duration_days", 120),
        instruments=kwargs.get("instruments"))


def test_import_uses_project_under_test_source():
    assert Path(politics.__file__).resolve() == Path(__file__).resolve().parents[1] / "worldline" / "military_politics.py"


def test_old_constant_willingness_is_ignored_without_mutation(world):
    world.military_state["politics"]["a"]["will_to_continue"] = .5
    before = world.to_dict()
    assert world.military_state["politics"]["a"]["will_to_continue"] == .5
    value = politics.willingness(world, "a")
    assert value["status"] == "not_assessed"
    assert value["institutional_choices"] == []
    assert world.to_dict() == before


def test_quotes_are_read_only_detached_and_consume_no_rng(world):
    report(world)
    before = world.to_dict()
    q = politics.replacement_quote(world, "a", 20, observer="general")
    assert q["political_willingness"]["status"] == "awaiting_decision"
    assert q["actual"]["training_cost"] == 20
    assert not q["actual"]["quote_is_guaranteed_future_delivery"]
    assert q["actual"]["transport"]["path"] == ["ra"]
    q["perceived"]["source"]["data"]["replacement_request"]["manpower"] = 9000
    assert world.to_dict() == before


@pytest.mark.parametrize("actor", ["local", "foreign"])
def test_authority_is_institutional_and_recipient_owned(world, actor):
    report(world, actor=actor)
    before = world.to_dict()
    with pytest.raises(ValueError, match="authority|scope"):
        politics.decide_replacement(world, "unauthorized", "a", 20, decision_actor=actor)
    assert world.to_dict() == before
    assert not politics.authority(world, "a", "b", "ruler")["feasible"]


def test_authority_does_not_come_from_high_military_skill_or_local_delegation(world):
    world.npc_state["persons"]["local"]["abilities"]["military"] = 1
    military_bridge.delegate(world, "local", "a", formations=["a"], scopes=["local", "defensive"], regions=["ra"])
    assert not politics.authority(world, "a", "a", "local")["feasible"]


def test_no_received_request_yields_unassessed_not_fictional_half_support(world):
    before_rng = copy.deepcopy(world.military_state["rng_streams"])
    result = politics.decide_replacement(world, "uninformed", "a", 20, decision_actor="general")
    assert result["body"]["mode"] == "not_assessed"
    assert result["body"]["selected"] is None and not result["body"]["approved"]
    assert world.military_state["rng_streams"] == before_rng
    assert politics.willingness(world, "a")["status"] == "not_assessed"


def test_valid_prose_dispatch_does_not_block_existing_replacement_forecast(world):
    report(world)
    before = politics.replacement_quote(world, "a", 20, observer="general")
    npc_state.receive_report(world.npc_state, "general", "a-unrelated",
        {"facts": "ordinary dispatch prose"}, world.date, "personal letter")
    assert npc_state.validate(world.npc_state, world.countries, world.regions, world.date) == []
    stored = copy.deepcopy(world.npc_state)
    rng = copy.deepcopy(world.military_state["rng_streams"])
    assert politics.replacement_quote(world, "a", 20, observer="general") == before
    assert world.npc_state == stored
    assert world.military_state["rng_streams"] == rng
    decision = politics.decide_replacement(world, "mixed-dispatches", "a", 20, decision_actor="general")
    assert decision["body"]["selected"] is not None
    assert decision["body"]["source_snapshot"]["topic"] == "replacement:a"
    assert politics.validation_issues(world) == []
    assert world.npc_state["reports"]["general"]["a-unrelated"]["data"] == {"facts": "ordinary dispatch prose"}


def test_valid_prose_only_is_not_a_forecast_or_a_random_political_choice(world):
    npc_state.receive_report(world.npc_state, "general", "a-unrelated",
        {"facts": "ordinary dispatch prose"}, world.date, "personal letter")
    assert npc_state.validate(world.npc_state, world.countries, world.regions, world.date) == []
    before = copy.deepcopy(world.npc_state)
    rng = copy.deepcopy(world.military_state["rng_streams"])
    quote = politics.replacement_quote(world, "a", 20, observer="general")
    assert quote["perceived"] is None
    assert quote["political_willingness"]["status"] == "not_assessed"
    decision = politics.decide_replacement(world, "prose-only", "a", 20, decision_actor="general")
    assert decision["body"]["selected"] is None
    assert decision["body"]["mode"] == "not_assessed"
    assert decision["body"]["source_snapshot"] is None
    assert world.military_state["rng_streams"] == rng
    assert world.npc_state == before
    assert politics.validation_issues(world) == []


def test_objective_records_and_foreign_report_do_not_inform_own_decider(world):
    npc_state.record_fact(world.npc_state, "hidden-request", world.date, "ruler", "custom:military_request",
                          {"replacement_request": {"recipient": "a", "manpower": 20}, "outcomes": OUTCOMES}, "true confidential plan")
    report(world, "finance")
    result = politics.decide_replacement(world, "private", "a", 20, decision_actor="general")
    assert result["body"]["mode"] == "not_assessed"


def test_institutions_can_disagree_on_same_replenishment(world):
    for actor in ("general", "finance"):
        report(world, actor)
    army = politics.decide_replacement(world, "army-choice", "a", 20, decision_actor="general")
    finance = politics.decide_replacement(world, "treasury-choice", "a", 20, decision_actor="finance")
    assert army["body"]["selected"] == "replenish"
    assert finance["body"]["selected"] != "replenish"
    assert {v["decision_actor"] for v in politics.willingness(world, "a")["institutional_choices"]} == {"general", "finance"}
    assert politics.validation_issues(world) == []


def test_unsupported_all_authority_is_rejected_without_extending_legacy_npc_scopes(world):
    world.npc_state["persons"]["general"]["authority"] = ["all"]
    report(world)
    before = world.to_dict()
    with pytest.raises(ValueError, match="scope"):
        politics.decide_replacement(world, "all-scope", "a", 20, decision_actor="general")
    assert world.to_dict() == before
    assert "npc: invalid person authority" in npc_state.validate(world.npc_state, world.countries, world.regions, world.date)


def test_high_material_capacity_does_not_force_political_approval(world):
    world.countries["a"].treasury = 100_000
    report(world, "finance")
    q = politics.replacement_quote(world, "a", 20, observer="finance")
    assert q["actual"]["finance"]["normal_financial_feasibility"]["feasible"]
    decision = politics.decide_replacement(world, "rich-refusal", "a", 20, decision_actor="finance")
    assert not decision["body"]["approved"]


def test_emergency_overcommitment_can_still_be_chosen(world):
    world.countries["a"].treasury = 0
    world.countries["a"].debt = 10000
    c = world.economy["countries"]["a"]["credit"]
    c["domestic_debt"], c["foreign_debt"] = 10000, 0
    credit.refresh(world, "a")
    report(world, manpower=200, perceived={"cost": 2., "risk": .01})
    result = politics.decide_replacement(world, "gamble", "a", 200, decision_actor="general")
    finance = result["body"]["actual_quote"]["finance"]
    assert not finance["normal_financial_feasibility"]["feasible"]
    assert finance["emergency_financial_feasibility"]["feasible"]
    assert result["body"]["approved"]
    assert result["body"]["perceived"]["financial"]["perceived_state"]["risk"] != finance["expected_consequences"]["default_risk"]


def test_financing_exhausted_prevents_npc_actual_replenishment_option(world):
    world.countries["a"].treasury = 0
    world.countries["a"].debt = 100000
    c = world.economy["countries"]["a"]["credit"]
    c["domestic_debt"], c["foreign_debt"] = 100000, 0
    c["financial_access"] = dict.fromkeys(c["financial_access"], False)
    credit.refresh(world, "a")
    report(world, perceived={"risk": 0.})
    result = politics.decide_replacement(world, "no-access", "a", 20, decision_actor="general")
    assert not result["body"]["actual_quote"]["finance"]["emergency_financial_feasibility"]["feasible"]
    assert not result["body"]["approved"]


def test_explicit_player_choice_is_not_cancelled_by_risk_but_creates_no_people(world):
    before = copy.deepcopy(world.military_state["formations"]), copy.deepcopy(world.economy)
    decision = politics.decide_replacement(world, "player", "a", 20, player_approval=True, allow_emergency=False)
    assert decision["body"]["approved"] and decision["body"]["mode"] == "explicit_player_approval"
    assert (world.military_state["formations"], world.economy) == before
    assert not world.military_state["replacements"]
    assert not world.military_state["rng_streams"]


def test_stale_reports_remain_stale_in_actual_reason_trace(world):
    report(world, as_of="1896-01-01")
    decision = politics.decide_replacement(world, "stale", "a", 20, decision_actor="general")
    basis = decision["body"]["decision"]["basis"]["report_metadata"]
    assert basis["replacement:a"]["age_days"] == 366
    assert decision["body"]["source_snapshot"]["metadata"]["as_of"] == "1896-01-01"
    assert any(t["factor"] == "information:staleness_uncertainty" for t in decision["body"]["decision"]["reason_trace"])


def test_latest_received_correction_changes_forecast_without_reading_truth(world):
    report(world)
    first = politics.decide_replacement(world, "before-correction", "a", 20, decision_actor="general")
    report(world, outcomes={"replenish": {"security": -1., "prestige": -1.},
                           "defer": {"security": 1., "prestige": 1.}, "reduce_commitment": {"security": .4}})
    second = politics.decide_replacement(world, "after-correction", "a", 20, decision_actor="general")
    assert first["body"]["selected"] == "replenish"
    assert second["body"]["selected"] == "defer"
    assert first["body"]["source_snapshot"]["data"] != second["body"]["source_snapshot"]["data"]
    assert politics.validation_issues(world) == []


def test_hidden_enemy_truth_cannot_change_npc_scores_or_rng(world):
    report(world)
    clone = World.from_dict(world.to_dict())
    clone.military_state["formations"]["b"]["manpower"] *= 100
    clone.military_state["formations"]["b"]["equipment_state"] = {"artillery": {"quantity": 99999}}
    a = politics.decide_replacement(world, "same", "a", 20, decision_actor="general")
    b = politics.decide_replacement(clone, "same", "a", 20, decision_actor="general")
    assert a["body"]["decision"] == b["body"]["decision"]
    assert json.dumps(world.military_state["rng_streams"], sort_keys=True) == json.dumps(clone.military_state["rng_streams"], sort_keys=True)


def test_same_id_is_idempotent_and_different_policy_cannot_reuse_it(world):
    report(world)
    first = politics.decide_replacement(world, "once", "a", 20, decision_actor="general")
    before = world.to_dict()
    assert politics.decide_replacement(world, "once", "a", 20, decision_actor="general") == first
    assert world.to_dict() == before
    with pytest.raises(ValueError, match="different request"):
        politics.decide_replacement(world, "once", "a", 21, decision_actor="general")


def test_approval_is_one_use_matching_actual_cohort_and_financing_policy(world):
    approval = politics.decide_replacement(world, "once", "a", 20, player_approval=True, allow_emergency=False)
    consumed = training(world, "cohort", approval)
    assert consumed["consumed_by"] == "cohort"
    assert politics.validation_issues(world) == []
    assert politics.consume_approval(world, "once", "a", 20, replacement_id="cohort", allow_emergency=False) == consumed
    with pytest.raises(ValueError, match="already consumed"):
        politics.consume_approval(world, "once", "a", 20, replacement_id="another", allow_emergency=False)
    with pytest.raises(ValueError, match="match"):
        politics.consume_approval(world, "once", "a", 20, replacement_id="cohort", allow_emergency=True)


def test_preflight_is_readonly_and_cannot_switch_duration_instruments_or_recipient(world):
    approval = politics.decide_replacement(world, "preflight", "a", 20, player_approval=True, allow_emergency=False)
    before = world.to_dict()
    assert politics.verify_approval(world, "preflight", "a", 20, allow_emergency=False) == approval
    assert world.to_dict() == before
    for kwargs in ({"duration_days": 180}, {"instruments": ["forced_loans"]}, {"transport_mode": "sea"}):
        with pytest.raises(ValueError, match="match"):
            politics.verify_approval(world, "preflight", "a", 20, allow_emergency=False, **kwargs)
    with pytest.raises(ValueError, match="match"):
        politics.verify_approval(world, "preflight", "b", 20, allow_emergency=False)
    assert world.to_dict() == before


def test_another_approval_cannot_claim_the_already_bound_cohort(world):
    first = politics.decide_replacement(world, "original", "a", 20, player_approval=True, allow_emergency=False)
    training(world, "cohort", first)
    politics.decide_replacement(world, "second", "a", 20, player_approval=True, allow_emergency=False)
    with pytest.raises(ValueError, match="matching cohort"):
        politics.consume_approval(world, "second", "a", 20, replacement_id="cohort", allow_emergency=False)


def test_unapproved_or_stale_or_expired_authority_cannot_fund_replacement(world):
    report(world)
    approval = politics.decide_replacement(world, "now", "a", 20, decision_actor="general", allow_emergency=False)
    world.npc_state["persons"]["general"]["authority"] = []
    with pytest.raises(ValueError, match="authority"):
        politics.consume_approval(world, "now", "a", 20, replacement_id="missing", decision_actor="general", allow_emergency=False)
    world.npc_state["persons"]["general"]["authority"] = ["mobilize"]
    world.date = "1897-01-02"
    with pytest.raises(ValueError, match="stale"):
        politics.consume_approval(world, "now", "a", 20, replacement_id="missing", decision_actor="general", allow_emergency=False)
    assert approval["body"]["approved"]


def test_authorized_npc_approval_reaches_real_paid_training_with_actual_labour_cost(world):
    report(world)
    approval = politics.decide_replacement(world, "npc-approval", "a", 20,
                                         decision_actor="general", allow_emergency=False)
    before_treasury = world.countries["a"].treasury
    before_civilian = sum(world.economy["regions"]["ra"]["labour_state"]["jobs"].values())
    result = training(world, "npc-cohort", approval)
    assert result["consumed_by"] == "npc-cohort"
    assert world.countries["a"].treasury == before_treasury - 20
    assert sum(world.economy["regions"]["ra"]["labour_state"]["jobs"].values()) < before_civilian
    cohort = world.military_state["replacements"]["npc-cohort"]
    assert cohort["status"] == "training"
    assert cohort["political_decision_id"] == "npc-approval"
    assert world.projects[cohort["project"]]["elapsed_days"] == 0
    assert politics.validation_issues(world) == []


@pytest.mark.parametrize("field,value", [("version", 999), ("date", "1999-01-01"), ("recipient", "b"), ("source", "changed")])
def test_corrupt_receipt_is_detected(world, field, value):
    report(world)
    politics.decide_replacement(world, "proof", "a", 20, decision_actor="general")
    receipt = world.military_state["political_decisions"]["proof"]
    if field == "recipient": receipt["body"]["request"][field] = value
    elif field == "source": receipt["body"]["source_snapshot"]["data"]["perceived"]["risk"] = .99
    else: receipt["body"][field] = value
    assert politics.validation_issues(world)


def test_invalid_future_source_and_changed_approved_history_rejected_even_with_new_digest(world):
    report(world)
    politics.decide_replacement(world, "proof", "a", 20, decision_actor="general")
    previous = World.from_dict(world.to_dict())
    receipt = world.military_state["political_decisions"]["proof"]
    receipt["body"]["source_snapshot"]["metadata"]["as_of"] = "1899-01-01"
    receipt["sha256"] = politics._digest(receipt["body"])
    issues = politics.validation_issues(world, previous)
    assert any("future" in issue for issue in issues)
    assert any("immutable" in issue for issue in issues)


def test_past_political_decision_survives_actor_exile_but_execution_authority_does_not(world):
    report(world)
    decision = politics.decide_replacement(world, "before-exile", "a", 20, decision_actor="general", allow_emergency=False)
    before = World.from_dict(world.to_dict())
    world.npc_state["persons"]["general"]["country"] = "b"
    assert decision["body"]["permission"]["country"] == "a"
    assert politics.validation_issues(world, before) == []
    assert politics.willingness(world, "a")["institutional_choices"][0]["receipt_id"] == "before-exile"
    with pytest.raises(ValueError, match="authority"):
        politics.verify_approval(world, "before-exile", "a", 20, decision_actor="general", allow_emergency=False)
    with pytest.raises(ValueError, match="scope"):
        politics.decide_replacement(world, "after-exile", "a", 20, decision_actor="general")


@pytest.mark.parametrize("corruption", ["unknown_choice", "decision_date", "duplicate_evaluation", "score", "eligibility", "selection"])
def test_political_trace_sanity_rejects_forgery_even_with_recalculated_receipt_digest(world, corruption):
    report(world)
    politics.decide_replacement(world, "proof", "a", 20, decision_actor="general")
    r = world.military_state["political_decisions"]["proof"]
    b, d = r["body"], r["body"]["decision"]
    if corruption == "unknown_choice": b["selected"] = d["selected"] = "invented"
    elif corruption == "decision_date": d["basis"]["date"] = "1896-01-01"
    elif corruption == "duplicate_evaluation": d["evaluations"][1] = copy.deepcopy(d["evaluations"][0])
    elif corruption == "score": d["evaluations"][0]["score"] += 10
    elif corruption == "eligibility": d["evaluations"][0]["eligible"] = "true"
    elif corruption == "selection": b["selected"] = d["selected"] = "defer"
    b["approved"] = b["selected"] == "replenish"
    r["sha256"] = politics._digest(b)
    assert politics.validation_issues(world)


def resign_source(receipt):
    """Forge a consistent body checksum so semantic checks, not SHA alone, matter."""
    body, source = receipt["body"], receipt["body"]["source_snapshot"]
    body["decision"]["basis"]["report_metadata"][source["topic"]] = copy.deepcopy(source["metadata"])
    source["data_sha256"] = politics._digest(source["data"])
    source["content_reference"] = politics._source_digest(source)
    body["perceived"]["source_reference"] = source["content_reference"]
    receipt["sha256"] = politics._digest(body)


@pytest.mark.parametrize("field,value", [
    ("confidence", -.01), ("confidence", 1.01), ("confidence", True),
    ("reliability", -.01), ("reliability", 5.), ("reliability", False),
    ("age_days", False), ("age_days", 0.), ("age_days", -1),
    ("source", ""), ("as_of", "18970101"), ("as_of", "1899-01-01"),
])
def test_source_metadata_is_validated_even_when_all_references_are_resigned(world, field, value):
    report(world)
    politics.decide_replacement(world, "proof", "a", 20, decision_actor="general")
    receipt = world.military_state["political_decisions"]["proof"]
    receipt["body"]["source_snapshot"]["metadata"][field] = value
    resign_source(receipt)
    assert receipt["sha256"] == politics._digest(receipt["body"])
    assert politics.validation_issues(world)


@pytest.mark.parametrize("corruption", [
    "received_before_observation", "received_after_decision", "noncanonical_received",
    "metadata_shape", "data_shape", "facts_shape", "source_id", "snapshot_shape",
])
def test_source_snapshot_shape_and_reception_dates_cannot_be_forged(world, corruption):
    report(world)
    politics.decide_replacement(world, "proof", "a", 20, decision_actor="general")
    receipt = world.military_state["political_decisions"]["proof"]
    source = receipt["body"]["source_snapshot"]
    if corruption == "received_before_observation": source["received_at"] = "1896-12-31"
    elif corruption == "received_after_decision": source["received_at"] = "1897-01-02"
    elif corruption == "noncanonical_received": source["received_at"] = "18970101"
    elif corruption == "metadata_shape": source["metadata"]["accuracy"] = .8
    elif corruption == "data_shape": source["data"] = []
    elif corruption == "facts_shape": source["facts"] = []
    elif corruption == "source_id":
        source["source_id"] = "unrelated-report"
        receipt["body"]["source_report_ids"] = [source["source_id"]]
    elif corruption == "snapshot_shape": source["unexpected"] = True
    resign_source(receipt)
    assert politics.validation_issues(world)


def political_archive(world):
    with sqlite3.connect(":memory:") as db:
        state_storage.ensure_schema(db)
        physical = state_storage.compact(db, {"world": world.to_dict()})
        assert state_storage.read(db, json.dumps(physical)) == json.loads(json.dumps({"world": world.to_dict()}))
        return state_storage.export_archive(db)


@pytest.mark.parametrize("field,value", [
    ("owner", "b"), ("item", "other-decision"), ("country", "b"),
    ("region", "rb"), ("day", "1897-01-02"),
])
def test_political_archive_catalog_matches_immutable_receipt_identity(world, field, value):
    report(world)
    politics.decide_replacement(world, "proof", "a", 20, decision_actor="general")
    archive = political_archive(world)
    assert state_storage.validate_archive(archive) is archive
    row = next(v for v in archive["catalog"] if v["kind"] == "military_political_decisions")
    source = copy.deepcopy(archive["objects"][row["digest"]])
    row[field] = value
    # The archived object remains genuine; the contradictory query labels fail.
    assert archive["objects"][row["digest"]] == source
    with pytest.raises(ValueError, match="catalog conflicts"):
        state_storage.validate_archive(archive)


def test_actual_save_rejects_wrong_political_archive_country_with_recalculated_outer_checksum(tmp_path):
    from test_crisis_integration import make
    from worldline.engine import encode
    import hashlib
    e = make(tmp_path / "political-archive.sqlite")
    try:
        e.enable_military()
        e.decide_military_replacements("archive-proof", "army-a", 10, player_approval=True)
        checkpoint = e.save(tmp_path / "political-archive.json")
        raw = json.loads(checkpoint.read_text(encoding="utf-8"))
        row = next(v for v in raw["payload"]["state_archive"]["catalog"]
                   if v["kind"] == "military_political_decisions")
        assert row["country"] == "a"
        row["country"] = "b"
        raw["sha256"] = hashlib.sha256(encode(raw["payload"]).encode()).hexdigest()
        checkpoint.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="catalog conflicts"):
            Engine.load(checkpoint, tmp_path / "rejected.sqlite")
    finally:
        e.close()


def test_unknown_extension_version_fails_clearly_and_old_queries_do_not_initialize(world):
    world.military_state.pop("politics_version", None)
    world.military_state.pop("political_decisions", None)
    before = world.to_dict()
    assert politics.willingness(world, "a")["status"] == "not_assessed"
    assert world.to_dict() == before
    world.military_state["politics_version"] = 99
    with pytest.raises(ValueError, match="version"):
        politics.replacement_quote(world, "a", 20)


def test_burdens_use_real_references_without_inventing_public_support(world):
    military_bridge.apply_result(world, {"id": "actual-loss", "date": world.date, "actor": "a", "region": "ra", "operation_id": None,
        "casualties": {"a": {"battle_casualties": 5}}, "equipment_losses": {}, "supply_consumption": {}, "unit_changes": {}})
    before = world.to_dict()
    p = politics.burden_projection(world, "a")
    assert p["recorded_burdens"]["casualty_burden"] == 5
    assert p["casualty_and_failure_evidence"][0]["receipt_id"] == "actual-loss"
    assert p["branch_rivalry"][0]["rivals"]
    assert p["civilian_hardship"][0]["region"] == "ra"
    assert p["fiscal_strain"]["credit_state"] == world.economy["countries"]["a"]["credit"]["distress_state"]
    assert p["assessment_status"] == "not_assessed"
    assert world.to_dict() == before


def test_next_choice_after_serialization_preserves_receipts_reports_and_saved_rng(world):
    report(world)
    first = politics.decide_replacement(world, "first", "a", 20, decision_actor="general")
    clone = World.from_dict(json.loads(json.dumps(world.to_dict())))
    assert clone.military_state["political_decisions"]["first"] == first
    left = politics.decide_replacement(world, "next", "a", 20, decision_actor="general")
    right = politics.decide_replacement(clone, "next", "a", 20, decision_actor="general")
    assert left == right
    assert json.dumps(world.military_state["rng_streams"], sort_keys=True) == json.dumps(clone.military_state["rng_streams"], sort_keys=True)
    assert politics.validation_issues(clone) == []


def test_actual_sqlite_save_load_keeps_political_decision_and_next_choice(tmp_path):
    from test_crisis_integration import make
    e = make(tmp_path / "politics.sqlite")
    try:
        e.enable_military()
        w = e.true_state
        ruler = w.npc_state["governments"]["a"]["ruler"]
        observation_date = w.date
        e.plan_operation("replacement-report", "a", ["army-a"], "defensive", "ra", issuer="a")
        message = e.send_military_report("replacement-report", ruler, {
            "replacement_request": {"recipient": "army-a", "manpower": 10}, "outcomes": OUTCOMES,
            "perceived": {"cost": 10, "risk": .2, "cost_scale": 1000}}, delay=1,
            source="own recruiting dispatch")
        assert e.military_replacement_quote("army-a", 10, observer=ruler)["perceived"] is None
        e.advance(1)
        w = e.true_state
        received_date = w.date
        assert w.military_state["commands"][message["id"]]["received_at"] == received_date
        assert w.military_state["commands"][message["id"]]["belief_accepted"]
        quote = e.military_replacement_quote("army-a", 10, observer=ruler)
        assert quote["perceived"] is not None
        assert quote["perceived"]["source"]["metadata"]["age_days"] == 1
        first = e.decide_military_replacements("saved", "army-a", 10, decision_actor=ruler, allow_emergency=False)
        assert first["body"]["selected"] is not None
        assert first["body"]["source_snapshot"]["metadata"]["as_of"] == observation_date
        assert first["body"]["source_snapshot"]["received_at"] == received_date
        assert first["body"]["source_report_ids"] == ["military-observation:replacement-report"]
        assert "political-replacement:saved:" + ruler in e.true_state.military_state["rng_streams"]
        checkpoint = e.save(tmp_path / "checkpoint.json")
        loaded = Engine.load(checkpoint, tmp_path / "loaded.sqlite")
        try:
            assert loaded.true_state.military_state["political_decisions"] == e.true_state.military_state["political_decisions"]
            a = e.decide_military_replacements("next", "army-a", 10, decision_actor=ruler, allow_emergency=False)
            b = loaded.decide_military_replacements("next", "army-a", 10, decision_actor=ruler, allow_emergency=False)
            assert a["body"]["selected"] is not None
            assert a == b
            assert loaded.true_state.to_dict() == e.true_state.to_dict()
            assert loaded.rng.getstate() == e.rng.getstate()
            assert loaded.ledger() == e.ledger()
        finally:
            loaded.close()
    finally:
        e.close()
