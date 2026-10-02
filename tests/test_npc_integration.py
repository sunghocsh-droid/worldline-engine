"""Authoritative-engine boundaries, persistence and private NPC information."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from worldline import Engine, World, Country, Region, Army
from worldline import npc, npc_decision, npc_state
from worldline.audit import Auditor, AuditError
from worldline.engine import encode


def initial_world(enemy_personnel=100):
    return World("1897-01-01", scenario_id="fictional_npc_boundary",
        countries={"a": Country("A", population=10000, gdp=4000, treasury=500),
                   "b": Country("B", population=10000, gdp=4000, treasury=500)},
        regions={"ra": Region("A region", "a", 10000), "rb": Region("B region", "b", 10000)},
        armies={"army-a": Army("a", "ra", 100), "army-b": Army("b", "rb", enemy_personnel)})


def configuration(autonomous=True):
    state = npc_state.initialize_state()
    npc_state.add_person(state, "leader", "a", "Fictional ruler", role="ruler", importance=.9,
        authority=["economic_policy", "war_preparation", "fiscal_action", "mobilize", "diplomatic_engagement"],
        traits={"risk_tolerance": .8, "prestige_sensitivity": .7}, biases={"optimism": .4})
    npc_state.add_person(state, "minister", "a", "Fictional minister", role="finance", importance=.5,
        authority=[], preferences={"fiscal": .9}, traits={"institutional_loyalty": .8})
    npc_state.add_group(state, "council", "a", "Council", kind="government",
        members=["leader", "minister"], leadership=["leader"], preferences={"fiscal": .5})
    npc_state.configure_government(state, "a", "leader", ["minister"], autonomous=autonomous)
    npc_state.record_fact(state, "past-account", "1896-01-01", "leader", "economic_performance",
        {"performance": -.2}, "fictional public account")
    npc_state.deliver_record(state, "minister", "past-account", as_of="1896-01-01")
    npc_state.remember(state, "minister", "past-account", interpretation={"fiscal": -.2,
        "tags": ["industrialization"], "lesson": "A past investment exceeded its budget"}, salience=.6)
    npc_state.remember_group(state, "council", "past-account", "minister",
        interpretation={"fiscal": -.1, "tags": ["industrialization"]}, salience=.4)
    npc_state.evaluate_ruler(state, "minister", "leader", "1897-01-01")
    return state


def make_engine(path, *, npc_enabled=True, autonomous=True, enemy_personnel=100, seed=51):
    engine = Engine.create(path, initial_world(enemy_personnel), seed)
    engine.enable_economy()
    engine.stabilize_economy()
    if npc_enabled:
        engine.enable_npc(configuration(autonomous))
        engine.npc_report("leader", "diplomacy", {"target": "b", "own_strength": 300,
            "enemy_strength": 100, "threat": .5}, source="uncertain foreign intelligence",
            accuracy=.2, confidence=.9, reliability=.8)
    return engine


@pytest.fixture
def government(tmp_path):
    engine = make_engine(tmp_path / "npc.sqlite")
    yield engine
    engine.close()


def write_changed_snapshot(source, target, mutation):
    envelope = json.loads(source.read_text(encoding="utf-8"))
    mutation(envelope["payload"])
    envelope["sha256"] = hashlib.sha256(encode(envelope["payload"]).encode()).hexdigest()
    target.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


def war_option(amount, resources=None, manpower=0):
    return {"id": "deliberate-war-preparation", "kind": "war_preparation", "country": "a",
        "params": {"target": "b", "region": "ra", "amount": amount, "manpower": manpower,
                   "resources": resources or {}, "allow_emergency": True}}


def test_npc_activation_requires_explicit_economy_stabilization(tmp_path):
    engine = Engine.create(tmp_path / "plain.sqlite", initial_world(), 51)
    try:
        with pytest.raises(ValueError): engine.enable_npc(configuration())
        engine.enable_economy()
        with pytest.raises(ValueError): engine.enable_npc(configuration())
        assert engine.true_state.npc_state is None
        assert engine.true_state.economy["model_revision"] == 2
        engine.stabilize_economy()
        before, rng = engine.true_state, engine.rng.getstate()
        result = engine.enable_npc(configuration())
        assert result == {"version": 1, "save_schema_version": 4}
        after = engine.true_state
        assert after.countries == before.countries and after.projects == before.projects
        assert after.economy == before.economy
        assert engine.rng.getstate() == rng
        assert after.schema_version == 3 and after.npc_state["version"] == 1
    finally:
        engine.close()


@pytest.mark.parametrize("example", ["initial.json", "economy_v1_month_one.json", "stabilization_month_one.json"])
def test_old_mvp_and_economy_saves_do_not_silently_enable_npc(tmp_path, example):
    source = Path(__file__).resolve().parents[1] / "examples" / example
    engine = Engine.load(source, tmp_path / "old.sqlite")
    try:
        assert engine.true_state.npc_state is None
        assert engine.true_state.schema_version == 2
        assert not any(s["name"] == "worldline.npc" for s in engine._system_specs)
    finally:
        engine.close()


def test_economy_only_save_remains_schema3_and_npc_save_is_schema4(tmp_path):
    engine = make_engine(tmp_path / "economy.sqlite", npc_enabled=False)
    try:
        economy_save = tmp_path / "economy.json"
        engine.save(economy_save)
        assert json.loads(economy_save.read_text(encoding="utf-8"))["payload"]["schema_version"] == 3
        engine.enable_npc(configuration())
        new_save = tmp_path / "npc.json"
        engine.save(new_save)
        payload = json.loads(new_save.read_text(encoding="utf-8"))["payload"]
        assert payload["schema_version"] == 4
        assert payload["engine"]["world"]["schema_version"] == 3
        assert payload["engine"]["world"]["npc_state"]["version"] == 1
        assert payload["engine"]["systems"] == [
            {"name": "worldline.projects", "version": "1"},
            {"name": "worldline.economy", "version": "1"},
            {"name": "worldline.npc", "version": "1"}]
        loaded = Engine.load(new_save, tmp_path / "loaded.sqlite")
        try: assert loaded._system_specs == payload["engine"]["systems"]
        finally: loaded.close()
    finally:
        engine.close()


@pytest.mark.parametrize("change", ["save_version", "world_version", "npc_version", "economy_version",
                                      "npc_manifest_version", "manifest_order", "missing_npc_manifest"])
def test_npc_save_versions_and_builtin_manifest_are_strict(government, tmp_path, change):
    source, target = tmp_path / "valid.json", tmp_path / "invalid.json"
    government.save(source)

    def mutation(payload):
        kernel = payload["engine"]
        if change == "save_version": payload["schema_version"] = 99
        elif change == "world_version": kernel["world"]["schema_version"] = 99
        elif change == "npc_version": kernel["world"]["npc_state"]["version"] = 99
        elif change == "economy_version": kernel["world"]["economy"]["version"] = 99
        elif change == "npc_manifest_version": kernel["systems"][-1]["version"] = "99"
        elif change == "manifest_order": kernel["systems"].reverse()
        else: kernel["systems"] = kernel["systems"][:-1]

    write_changed_snapshot(source, target, mutation)
    with pytest.raises(ValueError): Engine.load(target, tmp_path / "invalid.sqlite")
    assert not (tmp_path / "invalid.sqlite").exists()


def test_same_seed_reports_and_initial_world_reproduce_real_actions_rng_and_ledger(tmp_path):
    first, second = make_engine(tmp_path / "first.sqlite"), make_engine(tmp_path / "second.sqlite")
    try:
        for engine in (first, second): engine.advance(8)
        assert first.true_state.npc_state["actions"]
        assert first.true_state.to_dict() == second.true_state.to_dict()
        assert first.rng.getstate() == second.rng.getstate()
        assert first.orders == second.orders and first.ledger() == second.ledger()
    finally:
        first.close(); second.close()


def test_save_load_continuation_preserves_full_private_state_and_future_results(government, tmp_path):
    government.advance(1, "month")
    snapshot = tmp_path / "midgame.json"
    government.save(snapshot)
    loaded = Engine.load(snapshot, tmp_path / "resume.sqlite")
    try:
        before = government.true_state.npc_state
        assert loaded.true_state.npc_state == before
        for field in ("reports", "memories", "institutional_memories", "groups", "relationships", "schedules"):
            assert before[field]
        assert loaded.rng.getstate() == government.rng.getstate()
        assert loaded.orders == government.orders
        for engine in (government, loaded): engine.advance(1, "month")
        assert loaded.true_state.to_dict() == government.true_state.to_dict()
        assert loaded.rng.getstate() == government.rng.getstate()
        assert loaded.orders == government.orders
        assert loaded.ledger() == government.ledger()
    finally:
        loaded.close()


def test_hidden_foreign_truth_does_not_change_same_private_view_or_decision(tmp_path):
    first = make_engine(tmp_path / "weak.sqlite", enemy_personnel=100)
    second = make_engine(tmp_path / "strong.sqlite", enemy_personnel=2000)
    try:
        assert first.true_state.armies["army-b"].personnel != second.true_state.armies["army-b"].personnel
        for engine in (first, second):
            with engine._transaction(): npc.sense(engine._world)
        assert first.npc_view("leader") == second.npc_view("leader")
        first_view, first_options, _ = npc.options_for(first.true_state, "leader")
        second_view, second_options, _ = npc.options_for(second.true_state, "leader")
        assert first_options == second_options
        assert npc_decision.decide(first_view, first_options, first.rng) == npc_decision.decide(second_view, second_options, second.rng)
    finally:
        first.close(); second.close()


def test_private_view_is_deeply_detached_and_omits_unseen_facts(government):
    government.npc_outcome("leader", "custom:secret", {"secret": 987654}, observers=[])
    original = government.true_state.npc_state
    view = government.npc_view("leader")
    assert "records" not in view and "accuracy" not in view["report_metadata"]["diplomacy"]
    assert "987654" not in json.dumps(view)
    assert "past-account" not in view["beliefs"]  # private minister observation is not the ruler's report
    view["person"]["name"] = "Changed outside"
    view["beliefs"]["diplomacy"]["enemy_strength"] = 999999
    view["groups"]["council"]["interests"]["fiscal"] = -1
    view["institutional_memories"]["council"][0]["interpretation"]["fiscal"] = 99
    assert government.true_state.npc_state == original


def test_actual_and_perceived_financial_costs_are_separate_in_action_history(government):
    with government._transaction():
        government._world.npc_state["governments"]["a"]["decision_rule"] = "executive"
    government.advance(1)
    actions = government.true_state.npc_state["actions"]
    action = next(a for a in actions if a.get("actual") and a.get("perceived"))
    actual, perceived = action["actual"]["true_state"], action["perceived"]
    assert actual["cost"] != perceived["cost"]
    assert actual["consequences"]["financing_sources"]
    assert "risk" in perceived


def test_non_delegated_actor_cannot_execute_even_if_the_policy_is_popular(government):
    before = government.true_state.to_dict()
    result = government.npc_execute("minister", {"id": "unapproved", "kind": "economic_policy",
        "country": "a", "params": {"tax_rate": .5}})
    assert result["status"] == "rejected" and "authority" in result["reason"]
    assert government.true_state.to_dict() == before


def test_impossible_physical_action_is_rejected_without_financial_or_labour_changes(government):
    before = government.true_state.to_dict()
    result = government.npc_execute("leader", war_option(10, {"raw_materials": 10**9}, manpower=100))
    assert result["status"] == "rejected"
    after = government.true_state.to_dict()
    for field in ("countries", "economy", "armies", "projects", "population_accounts"):
        assert after[field] == before[field]
    assert "resources" in result["reason"] or "financing" in result["reason"]


def test_emergency_funded_risky_choice_executes_the_centralized_economic_rules(government):
    quote = government.policy_constraints("a", 10**8, allow_emergency=False)
    amount = int(quote["normal_financial_feasibility"]["available"]) + 50
    actual = government.policy_constraints("a", amount, allow_emergency=True)
    assert not actual["normal_financial_feasibility"]["feasible"]
    assert actual["emergency_financial_feasibility"]["feasible"]
    before = government.true_state.countries["a"]
    result = government.npc_execute("leader", war_option(amount, manpower=10))
    assert result["status"] == "accepted"
    after = government.true_state
    assert after.countries["a"].debt > before.debt
    assert after.economy["regions"]["ra"]["labour_state"]["mobilized"] == 10
    assert after.economy["countries"]["a"]["credit"]["civilian_extraction"] > 0
    assert any(e["kind"] == "military_preparation_funded" for e in result["execution_events"])
    Auditor().check(after)


def test_player_command_creates_records_memories_and_reactions_without_cancelling_it(government):
    before_records = len(government.true_state.npc_state["records"])
    result = government.set_policy("a", {"military": 120, "education": 0}, order_id="player-decision")
    assert result["status"] == "accepted"
    world = government.true_state
    assert world.economy["countries"]["a"]["policy"]["military"] == 120
    assert len(world.npc_state["records"]) > before_records
    assert any(m["received_date"] == world.date for m in world.npc_state["memories"]["minister"])
    assert any(row["kind"] == "system_event" and row["payload"].get("kind") == "npc_reaction" for row in government.ledger())
    assert government.orders["player-decision"]["status"] == "accepted"


def test_external_system_failure_rolls_back_npc_records_actions_rng_and_sql(tmp_path):
    def failing(world, tick, rng):
        rng.random()
        raise RuntimeError("later system failed")

    engine = Engine.create(tmp_path / "failure.sqlite", initial_world(), 51)
    try:
        # Declare the external system before the built-in manifest is committed;
        # adding undeclared code after a saved manifest would correctly be refused.
        engine.register_system(failing, name="tests.failure", version="1")
        engine.enable_economy()
        engine.stabilize_economy()
        engine.enable_npc(configuration())
        before, events = copy.deepcopy(engine._payload()), engine.ledger()
        with pytest.raises(RuntimeError, match="later system"): engine.advance(1)
        assert engine._payload() == before
        assert engine.ledger() == events
        assert engine._stored_payload()["world"] == before["world"]
    finally:
        engine.close()


@pytest.mark.parametrize("change", ["edit", "remove", "rehash"])
def test_existing_objective_record_cannot_be_changed_even_in_valid_transaction(government, change):
    before = government._stored_payload()
    with pytest.raises(AuditError):
        with government._transaction():
            records = government._world.npc_state["records"]
            if change == "remove": del records["past-account"]
            else:
                records["past-account"]["facts"]["performance"] = .9
                if change == "rehash": records["past-account"]["sha256"] = npc_state._digest(records["past-account"])
    assert government._stored_payload() == before


def test_npc_inspection_reads_sql_truth_and_external_updates_make_writer_stale(government):
    stored = government._stored_payload()
    stored["world"]["npc_state"]["persons"]["leader"]["name"] = "Committed SQL name"
    government.db.execute("UPDATE current_state SET payload=? WHERE id=1", (encode(stored),))
    government.db.commit()
    assert government.npc_view("leader")["person"]["name"] == "Committed SQL name"
    before = government._stored_payload()
    with pytest.raises(ValueError, match="another writer"):
        government.npc_report("leader", "uncommitted", {"value": 1})
    assert government._stored_payload() == before


def test_future_private_report_is_rejected_and_rolled_back(government):
    before, events = government._stored_payload(), government.ledger()
    with pytest.raises(ValueError):
        government.npc_report("leader", "future", {"secret": 1}, as_of="1897-02-01")
    assert government._stored_payload() == before
    assert government.ledger() == events
