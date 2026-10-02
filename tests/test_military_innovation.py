"""Paid, delayed, locally evidenced innovation, with no historical year locks."""
import copy
import json
import random

import pytest

from worldline import military_innovation_benchmark

from worldline import economy, projects, npc, npc_state, crisis, crisis_state, military_state
from worldline import military_innovation as innovation
from worldline.benchmark import reference_world


def test_five_funded_innovation_benchmarks_reproduce_actual_evidence_and_finite_costs():
    result = military_innovation_benchmark.run_cases()
    assert result == military_innovation_benchmark.run_cases()
    assert len(result["cases"]) == 5
    assert not result["scope"]["monthly_economy_settlement"]
    assert not result["scope"]["full_daily_engine_sqlite"]
    for case in result["cases"].values():
        variants = case["variants"].values() if "variants" in case else [case]
        for variant in variants:
            account = variant["accounting"]
            assert account["remaining_treasury"] == account["initial_treasury"]-account["actual_project_spending"]
            assert 0 < account["remaining_research_raw_materials"] < account["initial_research_raw_materials"]
            assert account["elapsed_days"] >= 210
            assert account["received_observer_memories"]
            assert account["uninformed_foreign_memories"] == []
    good = result["cases"]["measured_trial_memory"]
    bad = result["cases"]["failed_trial_memory_and_next_proposal"]
    assert good["result"]["programme"]["trial_evidence"]["successful"]
    assert good["observer_after"]["score"] > good["observer_before"]["score"]
    assert not bad["result"]["programme"]["trial_evidence"]["successful"]
    assert bad["observer_after"]["score"] < bad["observer_before"]["score"]
    assert bad["next_proposal_state"]["status"] == "stage_completed"
    assert len(good["trials"]) == len(bad["trials"]) == 3
    assert bad["next_proposal_state"]["adoption"] == {}
    assert bad["next_proposal_transition_quote"]["effective_days"] >= 180


def military_world(*, capable=True, day="1590-01-01", army_manpower=100):
    world = reference_world("early_industrial")
    world.date = day
    world.economy["last_settlement_date"] = day
    world.armies["a"].personnel = army_manpower
    economy.enable_stabilization(world)
    config = npc_state.initialize_state()
    npc_state.add_person(config, "officer", "a", "Trial officer")
    npc_state.add_person(config, "distant", "b", "Distant official")
    npc.initialize(world, config)
    crisis.initialize(world, config={"settings": {"auto_enabled": False}}, rng=random.Random(17))
    military_state.initialize(world, random.Random(5))
    if capable:
        world.military_state["actors"]["a"]["technical_capabilities"] = dict.fromkeys(innovation.CAPABILITIES, 1.0)
    return world


def idea(world, id="local", **kwargs):
    return innovation.propose(world, id, "a", "ra", "staff_planning", complexity=0,
        proposer="officer", observers=["officer"], capability_effects={"training": .2,
        "doctrine": {"staff_capability": .2, "subordinate_initiative": .1}}, **kwargs)


def advance_project(world, project, days=None):
    days = sum(s["days"] for s in project["stages"]) if days is None else days
    for _ in range(days):
        previous = world.date
        world.date = military_state.after(world.date, 1)
        projects.tick(world, {"previous_date": previous, "date": world.date, "days": 1}, random.Random(1))


def complete_stage(world, id, stage):
    result = innovation.begin_stage(world, id, stage, cost=1)
    project = world.projects[result["id"]]
    advance_project(world, project)
    assert project["status"] == "completed"
    assert projects.validate(world) == []
    assert innovation.validation_issues(world) == []
    return project


def through(world, id, final):
    for stage in innovation.STAGES[1:innovation.STAGES.index(final)+1]:
        complete_stage(world, id, stage)


def test_proposal_does_not_create_power_equipment_skill_or_reputation():
    world = military_world()
    before = copy.deepcopy(world.economy), copy.deepcopy(world.military_state["formations"]), copy.deepcopy(world.npc_state)
    returned = idea(world)
    assert (world.economy, world.military_state["formations"], world.npc_state) == before
    assert world.projects == {}
    assert returned["adoption"] == {}
    returned["requirements"]["capabilities"]["literacy"] = 99
    assert world.military_state["innovations"]["local"]["requirements"]["capabilities"]["literacy"] == .15


@pytest.mark.parametrize("source", innovation.SOURCES)
def test_innovation_provenance_channels_are_persisted_without_free_capability(source):
    world = military_world()
    before = copy.deepcopy(world.economy), copy.deepcopy(world.military_state["formations"]), copy.deepcopy(world.npc_state)
    proposal = idea(world, source=source)
    assert proposal["source"] == source
    assert world.military_state["outbox"][-1]["source"] == source
    assert (world.economy, world.military_state["formations"], world.npc_state) == before
    assert innovation.validation_issues(world) == []


def test_unknown_or_rewritten_innovation_provenance_is_rejected():
    world = military_world()
    with pytest.raises(ValueError, match="provenance"):
        idea(world, source="unexplained_future_bonus")
    assert world.military_state["innovations"] == {}
    idea(world, source="foreign_observation")
    previous = copy.deepcopy(world)
    world.military_state["innovations"]["local"]["source"] = "captured_equipment"
    assert any("changed original provenance" in issue for issue in innovation.validation_issues(world, previous))


@pytest.mark.parametrize("concept", tuple(innovation.EXAMPLES))
def test_five_concepts_have_actual_requirement_and_trial_records(concept):
    world = military_world()
    innovation.propose(world, concept, "a", "ra", concept, complexity=0)
    before = copy.deepcopy(world.military_state["formations"])
    through(world, concept, "trial")
    program = world.military_state["innovations"][concept]
    assert program["trial_evidence"]["date"] == world.date
    assert program["trial_evidence"]["reason_trace"]
    assert len(program["results"]) == 1
    assert len(program["receipts"]) == 3
    assert world.military_state["formations"] == before


def test_armour_concept_without_engine_transmission_fuel_is_not_usable():
    world = military_world(capable=False)
    innovation.propose(world, "landship", "a", "ra", "mobile_armoured_force")
    quote = innovation.assess(world, "landship")
    assert quote["physical_feasibility"]["experimental_work_feasible"]
    assert not quote["physical_feasibility"]["operational_concept_feasible"]
    assert {"engine", "transmission", "fuel", "tracks"} <= set(quote["physical_feasibility"]["technical_gaps"])
    through(world, "landship", "evaluation")
    assert not world.military_state["innovations"]["landship"]["trial_evidence"]["successful"]
    with pytest.raises(ValueError, match="Unproven"):
        innovation.begin_stage(world, "landship", "doctrine_development")


def test_early_capable_state_and_late_incapable_state_follow_capabilities_not_year():
    early = military_world(day="1590-01-01")
    late = military_world(day="1950-01-01", capable=False)
    for world in (early, late):
        innovation.propose(world, "tank", "a", "ra", "mobile_armoured_force")
    assert innovation.assess(early, "tank")["physical_feasibility"]["operational_concept_feasible"]
    assert not innovation.assess(late, "tank")["physical_feasibility"]["operational_concept_feasible"]


def test_access_without_installation_or_domestic_experience_is_not_engineering_capacity():
    world = military_world(capable=False)
    values = innovation.technical_capabilities(world, "a", "ra")
    for sector in world.economy["regions"]["ra"]["sectors"].values():
        sector["technology"].update(access=True, discovery=True, installed=False)
    assert innovation.technical_capabilities(world, "a", "ra") == values


@pytest.mark.parametrize("stage", ("prototype", "training", "diffusion"))
def test_development_cannot_skip_evidence_or_local_training(stage):
    world = military_world()
    idea(world)
    with pytest.raises(ValueError, match="preceding"):
        innovation.begin_stage(world, "local", stage)


def test_common_clock_and_real_budget_resources_are_used():
    world = military_world()
    idea(world)
    cash = world.countries["a"].treasury
    stocks = world.economy["regions"]["ra"]["stocks"]["raw_materials"]
    result = innovation.begin_stage(world, "local", "feasibility_study", cost=10)
    p = world.projects[result["id"]]
    assert p["kind"] == "military" and p["metadata"]["innovation_id"] == "local"
    assert world.countries["a"].treasury == cash - 10
    assert world.economy["countries"]["a"]["finance"]["interim_net_cashflow"] == -10
    assert world.economy["regions"]["ra"]["stocks"]["raw_materials"] == pytest.approx(stocks - .2)
    advance_project(world, p, 29)
    assert not world.military_state["innovations"]["local"]["completed_stages"]
    advance_project(world, p, 1)
    assert world.military_state["innovations"]["local"]["completed_stages"] == ["feasibility_study"]


def test_military_tick_does_not_advance_a_second_hidden_timer():
    world = military_world()
    idea(world)
    result = innovation.begin_stage(world, "local", "feasibility_study", cost=1)
    before = copy.deepcopy(world.military_state["innovations"]), copy.deepcopy(world.projects)
    world.date = military_state.after(world.date, 3650)
    innovation.tick(world, {"days": 3650}, random.Random(1))
    assert (world.military_state["innovations"], world.projects) == before
    assert world.projects[result["id"]]["progress"] == 0


def test_common_labour_shortage_delays_innovation_without_cancelling_idea():
    world = military_world()
    idea(world, requirements={"minimum_labour": 1_000_000})
    result = innovation.begin_stage(world, "local", "feasibility_study", cost=1)
    p = world.projects[result["id"]]
    expected = p["expected_completion_date"]
    advance_project(world, p, 60)
    assert p["elapsed_days"] == 0
    assert p["expected_completion_date"] > expected
    assert p["status"] != "cancelled"


def test_missing_real_material_does_not_become_available_from_an_idea():
    world = military_world()
    idea(world, requirements={"resources": {"raw_materials": 100_000}})
    before = copy.deepcopy(world.to_dict())
    with pytest.raises(ValueError, match="unavailable"):
        innovation.begin_stage(world, "local", "feasibility_study", cost=1)
    assert world.to_dict() == before


def test_adoption_only_after_paid_training_and_only_in_selected_local_formation():
    world = military_world()
    idea(world)
    original_b = copy.deepcopy(world.military_state["formations"]["b"])
    initial_training = world.military_state["formations"]["a"]["training"]
    labour = copy.deepcopy(world.economy["regions"]["ra"]["labour_state"])
    through(world, "local", "training")
    assert world.military_state["innovations"]["local"]["trial_evidence"]["successful"]
    assert world.military_state["formations"]["a"]["training"] == initial_training
    complete_stage(world, "local", "limited_adoption")
    f = world.military_state["formations"]["a"]
    assert f["adoption"]["local"] == .25
    assert f["training"] == pytest.approx(initial_training + .05)
    assert world.military_state["formations"]["b"] == original_b
    assert world.economy["regions"]["ra"]["labour_state"] == labour
    complete_stage(world, "local", "institutional_adoption")
    assert f["adoption"]["local"] == .65
    complete_stage(world, "local", "diffusion")
    assert f["adoption"]["local"] == 1
    assert f["training"] == pytest.approx(initial_training + .2)
    assert f["manpower"] == 100


def test_stage_effect_is_exactly_once_and_receipt_is_immutable():
    world = military_world()
    idea(world)
    p = complete_stage(world, "local", "feasibility_study")
    before = copy.deepcopy(world.to_dict())
    assert innovation.on_project_completed(world, p) == []
    assert world.to_dict() == before
    old = copy.deepcopy(world)
    world.military_state["innovations"]["local"]["receipts"][p["id"]]["actual_cost"] += 1
    assert any("immutable" in issue for issue in innovation.validation_issues(world, old))


def test_partial_project_cannot_mint_completion_effect():
    world = military_world()
    idea(world)
    p = innovation.begin_stage(world, "local", "feasibility_study", cost=1)
    with pytest.raises(ValueError, match="actual funded"):
        innovation.on_project_completed(world, p)


def test_trial_seed_and_saved_named_stream_are_reproducible():
    left, right = military_world(), military_world()
    idea(left)
    idea(right)
    through(left, "local", "trial")
    through(right, "local", "trial")
    assert left.to_dict() == right.to_dict()
    saved = json.loads(json.dumps(left.to_dict()))
    restored = type(left).from_dict(saved)
    complete_stage(left, "local", "evaluation")
    complete_stage(restored, "local", "evaluation")
    assert json.dumps(left.to_dict(), sort_keys=True) == json.dumps(restored.to_dict(), sort_keys=True)


def test_unrelated_trial_does_not_shift_this_innovation_stream():
    left, right = military_world(), military_world()
    idea(left)
    idea(right)
    idea(right, id="unrelated")
    through(right, "unrelated", "trial")
    through(left, "local", "trial")
    through(right, "local", "trial")
    a, b = (w.military_state["innovations"]["local"]["trial_evidence"] for w in (left, right))
    assert a["draw"] == b["draw"] and a["successful"] == b["successful"]


def test_unreported_trial_does_not_enter_any_npc_memory_or_relationship():
    world = military_world()
    idea(world)
    before = copy.deepcopy(world.npc_state)
    through(world, "local", "trial")
    assert world.npc_state == before
    descriptor = world.military_state["outbox"][-1]
    assert descriptor["observers"] == ["officer"]
    assert "distant" not in descriptor["observers"]


def test_institutional_resistance_depends_on_private_memory_and_interests():
    world = military_world()
    npc_state.add_group(world.npc_state, "traditional", "a", "Old guard", members=["officer"],
                        preferences={"military_innovation": -1}, influence=1)
    p = world.npc_state["persons"]["officer"]
    p["traits"].update(institutional_loyalty=1, ambition=0, prestige_sensitivity=0)
    report = {"novelty": 1, "training_burden": 1, "expected_benefit": .1}
    view = npc_state.actor_view(world.npc_state, "officer", world.date)
    before = copy.deepcopy(world.npc_state)
    response = innovation.institutional_response(view, report)
    assert response["position"] == "oppose"
    assert world.npc_state == before
    assert any(t["factor"] == "institutional_interest:traditional" for t in response["reason_trace"])
    npc_state.record_fact(world.npc_state, "success", world.date, "a", "custom:military_innovation", {"result": 1}, "trial")
    npc_state.deliver_record(world.npc_state, "officer", "success", accuracy=1, confidence=1, reliability=1)
    npc_state.remember(world.npc_state, "officer", "success", interpretation={"innovation_lesson": 1},
                       salience=1, reliability=1)
    learned = innovation.institutional_response(npc_state.actor_view(world.npc_state, "officer", world.date), report)
    assert learned["score"] > response["score"]
    distant = innovation.institutional_response(npc_state.actor_view(world.npc_state, "distant", world.date), report)
    assert not any("memory" in t["factor"] for t in distant["reason_trace"])


def test_same_calibre_is_not_automatically_interchangeable_high_precision_parts():
    world = military_world()
    innovation.register_standard(world, "rough", "personal_weapons", ammunition_family="common", parts_family="hand_fitted",
                                 precision=.15, interchangeability=.1)
    innovation.register_standard(world, "precision", "artillery", ammunition_family="common", parts_family="machined",
                                 precision=.9, interchangeability=.9)
    f = world.military_state["formations"]["a"]
    f["equipment_state"] = {"personal_weapons": {"quantity": 50, "standard_id": "rough"},
                             "artillery": {"quantity": 2, "standard_id": "precision"}}
    quote = innovation.equipment_compatibility(world, "a")
    assert quote["ammunition_compatible"]
    assert quote["manufacturing_precision"] == .15
    assert quote["parts_interchangeability"] == .1
    assert quote["maintenance_load"] > 1


def test_multiple_families_add_logistics_and_training_load_without_creating_supply():
    world = military_world()
    for id, kind in (("rifle", "personal_weapons"), ("cannon", "artillery")):
        innovation.register_standard(world, id, kind, ammunition_family=id, parts_family=id, precision=.8, interchangeability=.8)
    f = world.military_state["formations"]["a"]
    f["equipment_state"] = {"personal_weapons": {"quantity": 100, "standard_id": "rifle"},
                             "artillery": {"quantity": 2, "standard_id": "cannon"}}
    before = copy.deepcopy(world.to_dict())
    quote = innovation.equipment_compatibility(world, "a")
    assert not quote["ammunition_compatible"]
    assert quote["ammunition_family_equipment"] == {"rifle": 100, "cannon": 2}
    assert quote["per_standard_quantities"] == {"rifle": 100, "cannon": 2}
    assert quote["training_load"] > 1 and quote["maintenance_load"] > 1
    assert world.to_dict() == before


def test_training_conditions_do_not_treat_remote_or_marching_workers_as_local():
    world = military_world()
    idea(world)
    through(world, "local", "doctrine_development")
    result = innovation.begin_stage(world, "local", "training", cost=1)
    f = world.military_state["formations"]["a"]
    f["journey"] = {"path": ["ra", "rb"]}
    assert not innovation.project_conditions(world, world.projects[result["id"]])["ready"]
    advance_project(world, world.projects[result["id"]], 10)
    assert world.projects[result["id"]]["elapsed_days"] == 0
    f["journey"] = None
    f["location"] = "rb"
    assert not innovation.project_conditions(world, world.projects[result["id"]])["ready"]


def test_real_return_arrival_retains_journey_evidence_and_allows_local_training_adoption(monkeypatch):
    from worldline import military_operations as operations, military_logistics as logistics
    from worldline.models import Region
    world = military_world()
    # Ordinary move completion releases its journey; longer operations may keep
    # it. Capture the actual pre-release arrival evidence, without changing any
    # movement, distance, completion, resource or time calculation.
    arrivals = {}
    release = operations._release
    def capture_arrival(candidate, operation):
        arrivals[operation["id"]] = copy.deepcopy(candidate.military_state["formations"]["a"]["journey"])
        return release(candidate, operation)
    monkeypatch.setattr(operations, "_release", capture_arrival)
    idea(world)
    through(world, "local", "doctrine_development")
    project_id = innovation.begin_stage(world, "local", "training", cost=1)["id"]
    world.regions["ra2"] = Region("Second local region", "a", x=20, infrastructure={"roads": 50})
    world.regions["ra"].infrastructure["roads"] = 50
    logistics.add_link(world, "local-road", "ra", "ra2", actor="a", mode="road",
        throughput=20000, travel_days=3, distance_km=20)
    for id, target in (("outward", "ra2"), ("return", "ra")):
        operations.plan(world, id, "a", ["a"], "move", target=target, issuer="a")
        operations.send_command(world, id, "a", delay=0)
        saw_moving = False
        for _ in range(40):
            previous = world.date
            world.date = military_state.after(world.date, 1)
            operations.tick(world, {"previous_date": previous, "date": world.date, "days": 1})
            f = world.military_state["formations"]["a"]
            if not logistics.is_stationary(world, f):
                saw_moving = True
                assert not innovation.project_conditions(world, world.projects[project_id])["ready"]
            if world.military_state["operations"][id]["status"] == "completed":
                break
        assert saw_moving
        assert world.military_state["operations"][id]["status"] == "completed"
    f = world.military_state["formations"]["a"]
    f["journey"] = arrivals["return"]
    assert f["journey"]["arrival"] == world.date and f["journey"]["leg_index"] == len(f["journey"]["legs"])
    assert f["location"] == "ra" and logistics.is_stationary(world, f)
    assert innovation.project_conditions(world, world.projects[project_id])["ready"]
    advance_project(world, world.projects[project_id])
    complete_stage(world, "local", "limited_adoption")
    assert f["adoption"]["local"] == .25
    assert f["journey"]["arrival"] is not None


@pytest.mark.parametrize("bad", (float("nan"), float("inf"), -1, 2, True))
def test_invalid_capability_inputs_are_rejected(bad):
    world = military_world()
    with pytest.raises(ValueError):
        idea(world, requirements={"capabilities": {"engine": bad}})


def test_nonstate_cannot_bypass_finite_resources_with_national_project_credit():
    world = military_world()
    military_state.add_actor(world, "guild", kind="private", home_region="ra", treasury=10)
    innovation.propose(world, "guild-idea", "guild", "ra", "staff_planning")
    assert not innovation.assess(world, "guild-idea", cost=11)["execution_feasibility"]
    with pytest.raises(ValueError, match="Non-state"):
        innovation.begin_stage(world, "guild-idea", "feasibility_study", cost=1)


def test_nonstate_funded_resources_do_not_claim_unimplemented_project_execution():
    world = military_world()
    military_state.add_actor(world, "guild", kind="private", home_region="ra", treasury=100)
    world.military_state["actors"]["guild"]["stocks"]["raw_materials"] = 10
    innovation.propose(world, "guild-idea", "guild", "ra", "staff_planning")
    before = copy.deepcopy(world.to_dict())
    quote = innovation.assess(world, "guild-idea", cost=1)
    assert quote["physical_feasibility"]["experimental_work_feasible"]
    assert quote["normal_financial_feasibility"]["feasible"]
    assert quote["emergency_financial_feasibility"]["feasible"]
    assert not quote["execution_feasibility"]
    assert quote["execution_limitations"] == ["unavailable_funding_adapter"]
    assert any(reason["factor"] == "unavailable_funding_adapter" for reason in quote["reason_trace"])
    with pytest.raises(ValueError, match="funding adapter"):
        innovation.begin_stage(world, "guild-idea", "feasibility_study", cost=1)
    assert world.to_dict() == before


def test_stage_default_materials_are_in_quote_before_any_project_is_created():
    world = military_world()
    idea(world)
    world.economy["regions"]["ra"]["stocks"]["raw_materials"] = 0
    before = copy.deepcopy(world.to_dict())
    quote = innovation.assess(world, "local", cost=10)
    assert not quote["physical_feasibility"]["feasible"]
    assert quote["physical_feasibility"]["missing_resources"]["raw_materials"]["required"] == .2
    with pytest.raises(ValueError, match="unavailable"):
        innovation.begin_stage(world, "local", "feasibility_study", cost=10)
    assert world.to_dict() == before


def test_unknown_even_zero_resource_is_not_a_free_inventory_namespace():
    world = military_world()
    with pytest.raises(ValueError, match="real scalar"):
        idea(world, requirements={"resources": {"invented_material": 0}})


def test_true_and_perceived_costs_are_separate_detached_values():
    world = military_world()
    idea(world)
    perceived = {"cost": 1, "risk": 0, "basis": "optimistic_report"}
    before = copy.deepcopy(world.to_dict())
    quote = innovation.assess(world, "local", cost=10, perceived=perceived)
    assert quote["true_state"]["cost"] == 10
    assert quote["perceived_state"]["cost"] == 1
    quote["perceived_state"]["cost"] = 99
    quote["physical_feasibility"]["available_capabilities"]["engine"] = 99
    assert perceived["cost"] == 1
    assert world.to_dict() == before


def test_institution_cannot_review_unreported_idea_and_opposition_does_not_cancel_it():
    world = military_world()
    idea(world)
    with pytest.raises(ValueError, match="not received"):
        innovation.review_institution(world, "local", "officer")
    npc_state.record_fact(world.npc_state, "idea-report", world.date, "a", "custom:military_innovation",
                         {"innovation_id": "local", "novelty": 1, "training_burden": 1, "expected_benefit": 0}, "proposer")
    npc_state.deliver_record(world.npc_state, "officer", "idea-report", accuracy=1, confidence=1, reliability=1)
    opinion = innovation.review_institution(world, "local", "officer")
    assert opinion["position"] == "oppose"
    assert world.military_state["innovations"]["local"]["status"] == "proposed"
    assert innovation.validation_issues(world) == []
    complete_stage(world, "local", "feasibility_study")


def _reviewed_training_world(*, ruler_signal=0, faction_interest=0, deliver=True):
    world = military_world()
    npc_state.add_person(world.npc_state, "ruler", "a", "Synthetic ruler", role="ruler")
    npc_state.configure_government(world.npc_state, "a", "ruler", ["officer"], autonomous=False)
    npc_state.add_group(world.npc_state, "institution", "a", "Officer institution", kind="military_branch",
        members=["officer"], influence=1, interests={"military_innovation": faction_interest})
    idea(world)
    through(world, "local", "doctrine_development")
    npc_state.record_fact(world.npc_state, "proposal-report", world.date, "ruler", "custom:military_innovation",
        {"innovation_id": "local", "novelty": .5, "training_burden": .5, "expected_benefit": .5}, "trial board")
    npc_state.deliver_record(world.npc_state, "officer", "proposal-report", accuracy=1, confidence=1, reliability=1)
    if ruler_signal:
        for index in range(3):
            event = "performance:" + str(index)
            npc_state.record_fact(world.npc_state, event, world.date, "ruler", "administrative_performance",
                {"result": ruler_signal}, "synthetic realized administration")
            if deliver:
                npc_state.deliver_record(world.npc_state, "officer", event, accuracy=1, confidence=1, reliability=1)
    npc_state.evaluate_ruler(world.npc_state, "officer", "ruler", world.date)
    innovation.review_institution(world, "local", "officer")
    return world


@pytest.mark.parametrize("axis", ("ruler", "faction"))
def test_observed_ruler_and_faction_support_affect_real_paid_stage_duration(axis):
    worlds = [_reviewed_training_world(ruler_signal=sign if axis == "ruler" else 0,
        faction_interest=sign if axis == "faction" else 0) for sign in (1, -1)]
    projects_created = [innovation.begin_stage(world, "local", "training", cost=1) for world in worlds]
    durations = [sum(s["days"] for s in p["stages"]) for p in projects_created]
    assert innovation.MINIMUM_DAYS["training"] <= durations[0] < durations[1] <= 360
    for world, p, duration in zip(worlds, projects_created, durations):
        assert p["metadata"]["stage_duration"]["effective_days"] == duration
        assert p["metadata"]["stage_duration"]["institutional_reviews"]
        assert p["metadata"]["training_cost"]["duration_days"] == duration
        advance_project(world, world.projects[p["id"]], 180)
        assert world.projects[p["id"]]["status"] != "completed"
        assert innovation.validation_issues(world) == []
        assert world.military_state["innovations"]["local"]["status"] == "in_progress"


def test_unobserved_ruler_achievements_cannot_secretly_accelerate_institutional_adoption():
    neutral = _reviewed_training_world()
    secret = _reviewed_training_world(ruler_signal=1, deliver=False)
    assert neutral.military_state["innovations"]["local"]["institutions"] == secret.military_state["innovations"]["local"]["institutions"]
    for candidate in (neutral, secret):
        p = innovation.begin_stage(candidate, "local", "training", cost=1)
        assert p["metadata"]["stage_duration"] == innovation.stage_duration(candidate, "local", "training", 180)
    assert neutral.projects == secret.projects


def test_mission_command_actual_transition_culture_changes_training_speed_without_instant_effect():
    from worldline.military_innovation_benchmark import research_world
    results = []
    for culture in (.05, .7):
        world = research_world("industrial", mission_culture=culture, industrial_capability=.8)
        innovation.propose(world, "mission-command", "a", "ra", "mission_oriented_command", complexity=0,
            requirements={"capabilities": {"training": .4, "organization": .4}},
            capability_effects={"training": .2, "doctrine": {"mission_oriented_command": .2}})
        through(world, "mission-command", "doctrine_development")
        original = copy.deepcopy(world.military_state["formations"]["a"])
        p = innovation.begin_stage(world, "mission-command", "training", cost=1)
        assert world.military_state["formations"]["a"] == original
        reason = p["metadata"]["stage_duration"]["reason_trace"][0]
        assert reason["factor"] == "mission_command_transition"
        assert {"training", "staff_capability", "literacy", "centralized_control", "punishment_risk_culture"} <= set(reason["actual_conditions"])
        results.append(sum(s["days"] for s in p["stages"]))
        advance_project(world, world.projects[p["id"]], 180)
        assert world.projects[p["id"]]["status"] != "completed"
    assert 180 < results[1] < results[0] <= 360


def test_deleted_trial_evidence_or_forged_institution_is_detected():
    world = military_world()
    idea(world)
    through(world, "local", "trial")
    world.military_state["innovations"]["local"]["trial_evidence"] = None
    issues = innovation.validation_issues(world)
    assert any("missing trial evidence" in issue for issue in issues)
    assert any("trial differs" in issue for issue in issues)


def test_explicitly_delivered_failed_trial_can_have_different_institutional_lessons():
    world = military_world(capable=False)
    innovation.propose(world, "failed", "a", "ra", "mobile_armoured_force", observers=["officer"])
    through(world, "failed", "trial")
    failed = world.military_state["innovations"]["failed"]["trial_evidence"]
    assert not failed["successful"]
    event = "trial-failed"
    npc_state.record_fact(world.npc_state, event, world.date, "a", "custom:military_innovation",
                         {"innovation_id": "failed", "successful": False, "technical_gaps": failed["technical_gaps"]}, "trial-board")
    with pytest.raises(ValueError, match="never delivered"):
        npc_state.remember(world.npc_state, "distant", event)
    npc_state.deliver_record(world.npc_state, "officer", event, accuracy=1, confidence=1, reliability=1)
    npc_state.remember(world.npc_state, "officer", event, interpretation={"innovation_lesson": -.8}, salience=.9)
    quote = {"novelty": .5, "training_burden": .5, "expected_benefit": .5}
    judgement = innovation.institutional_response(npc_state.actor_view(world.npc_state, "officer", world.date), quote)
    assert any(t["factor"] == "observed_innovation_memory:" + event and t["contribution"] < 0 for t in judgement["reason_trace"])
    assert world.npc_state["memories"]["distant"] == []


@pytest.mark.parametrize("capable,concept", ((True, "staff_planning"), (False, "mobile_armoured_force")))
def test_coordinator_publishes_actual_success_or_failure_only_to_delivered_observers(capable, concept):
    from worldline import military
    world = military_world(capable=capable)
    world.npc_state["persons"]["officer"]["region"] = "ra"
    crisis_state.configure_region(world, "ra")
    innovation.propose(world, "measured", "a", "ra", concept, complexity=0,
                       proposer="officer", observers=["officer"])
    through(world, "measured", "trial")
    assert world.npc_state["memories"]["officer"] == []
    actual = copy.deepcopy(world.military_state["innovations"]["measured"]["trial_evidence"])
    military.publish(world)
    records = [r for r in world.npc_state["records"].values() if r["facts"].get("innovation_id") == "measured"]
    assert len(records) == 1
    assert records[0]["facts"]["trial_evidence"] == actual
    assert records[0]["facts"]["trial_success"] == capable
    assert "competence" not in records[0]["facts"]
    memories = [m for m in world.npc_state["memories"]["officer"] if m["observed_facts"].get("innovation_id") == "measured"]
    assert len(memories) == 1
    assert memories[0]["observed_facts"]["trial_success"] == capable
    assert world.npc_state["memories"]["distant"] == []
    opinion = innovation.review_institution(world, "measured", "officer")
    trace = [t for t in opinion["reason_trace"] if t["factor"].startswith("observed_innovation_memory")]
    assert trace and (trace[0]["contribution"] > 0) == capable
    assert innovation.validation_issues(world) == []


def test_an_npc_can_propose_without_having_right_to_spend_the_state_budget():
    world = military_world()
    idea(world)
    before = copy.deepcopy(world.to_dict())
    with pytest.raises(ValueError, match="no delegated"):
        innovation.begin_stage(world, "local", "feasibility_study", cost=1, decision_actor="officer")
    assert world.to_dict() == before


def test_ruler_and_formally_delegated_npc_can_authorize_paid_innovation():
    ruler = military_world()
    idea(ruler)
    npc_state.configure_government(ruler.npc_state, "a", "officer", autonomous=False)
    p = innovation.begin_stage(ruler, "local", "feasibility_study", cost=1, decision_actor="officer")
    assert p["metadata"]["permission"]["scope"] == "government_ruler"
    delegate = military_world()
    idea(delegate)
    delegate.military_state["delegations"]["officer"] = {"actor": "a", "formations": ["a"],
        "regions": ["ra"], "scopes": ["innovation"], "expires": None}
    p = innovation.begin_stage(delegate, "local", "feasibility_study", cost=1, decision_actor="officer")
    assert p["metadata"]["permission"]["scope"] == "delegated_innovation"


@pytest.mark.parametrize("change", ({"scopes": ["offensive"]}, {"regions": ["rb"]},
                                  {"formations": ["b"]}, {"expires": "1589-12-31"}, {"actor": "b"}))
def test_innovation_authority_cannot_escape_scope_region_formations_or_expiry(change):
    world = military_world()
    idea(world)
    delegation = {"actor": "a", "formations": ["a"], "regions": ["ra"], "scopes": ["innovation"], "expires": None}
    delegation.update(change)
    world.military_state["delegations"]["officer"] = delegation
    before = copy.deepcopy(world.to_dict())
    with pytest.raises(ValueError):
        innovation.begin_stage(world, "local", "feasibility_study", cost=1, decision_actor="officer")
    assert world.to_dict() == before


def test_inactive_npc_does_not_keep_executive_authority():
    world = military_world()
    idea(world)
    npc_state.configure_government(world.npc_state, "a", "officer", autonomous=False)
    world.npc_state["persons"]["officer"]["active"] = False
    assert not innovation.stage_authority(world, "local", "officer")["feasible"]


def test_large_training_cohort_cannot_be_taught_for_arbitrary_one_unit_budget():
    world = military_world(army_manpower=2500)
    idea(world)
    through(world, "local", "doctrine_development")
    cash = world.countries["a"].treasury
    result = innovation.begin_stage(world, "local", "training", cost=1)
    info = result["metadata"]["training_cost"]
    assert info["manpower"] == 2500
    assert info["minimum_cost"] == 25
    assert result["cost"] == 25
    assert world.countries["a"].treasury == cash - 25
    assert result["metadata"]["proposed_cost"] == 1
    assert world.military_state["innovations"]["local"]["training_cohort"]["formations"] == {"a": 2500}


def test_advanced_training_has_real_additional_material_and_instructor_cost():
    world = military_world(army_manpower=2500)
    innovation.propose(world, "advanced", "a", "ra", "standardized_artillery", complexity=0)
    through(world, "advanced", "doctrine_development")
    result = innovation.begin_stage(world, "advanced", "training", cost=1)
    assert result["metadata"]["training_cost"]["annual_cost_per_trainee"] == .04
    assert result["cost"] == 50


def _reinforce_actual_standing_cohort(world, count):
    from worldline import labour
    f = world.military_state["formations"]["a"]
    pool = world.economy["regions"]["ra"]["labour_state"]
    labour._military_change(world, "ra", "standing_army", pool["standing_army"] + count, "actual standing reinforcement")
    f["manpower"] += count
    f["combat_manpower"] += count
    world.armies["a"].personnel += count
    labour._write_region(world, "ra")
    labour.aggregate(world, "a")
    assert labour.validate(world) == []


def test_people_joining_mid_training_do_not_acquire_the_paid_cohorts_innovation():
    world = military_world()
    idea(world)
    through(world, "local", "doctrine_development")
    result = innovation.begin_stage(world, "local", "training", cost=1)
    project = world.projects[result["id"]]
    advance_project(world, project, 90)
    _reinforce_actual_standing_cohort(world, 100)
    advance_project(world, project, 90)
    assert world.military_state["innovations"]["local"]["training_cohort"]["formations"] == {"a": 100}
    complete_stage(world, "local", "limited_adoption")
    f = world.military_state["formations"]["a"]
    assert f["adoption"]["local"] == .125  # .25 adoption of the original half.
    assert f["training"] == pytest.approx(.3 + .2 * .125)


def test_paid_cohort_and_stage_window_survive_save_load_exactly():
    world = military_world(army_manpower=2500)
    idea(world)
    through(world, "local", "doctrine_development")
    result = innovation.begin_stage(world, "local", "training", cost=1)
    advance_project(world, world.projects[result["id"]], 90)
    restored = type(world).from_dict(json.loads(json.dumps(world.to_dict())))
    assert restored.military_state["innovations"]["local"]["training_cohort"] == world.military_state["innovations"]["local"]["training_cohort"]
    for candidate in (world, restored):
        advance_project(candidate, candidate.projects[result["id"]], 90)
        complete_stage(candidate, "local", "limited_adoption")
    assert json.dumps(world.to_dict(), sort_keys=True) == json.dumps(restored.to_dict(), sort_keys=True)


def test_training_snapshot_cannot_be_expanded_for_existing_unit_at_same_cost():
    world = military_world()
    idea(world)
    through(world, "local", "doctrine_development")
    innovation.begin_stage(world, "local", "training", cost=1)
    previous = copy.deepcopy(world)
    world.military_state["innovations"]["local"]["training_cohort"]["formations"]["a"] = 10_000
    issues = innovation.validation_issues(world, previous)
    assert any("changed paid training cohort" in issue for issue in issues)
    assert any("invalid paid training cohort" in issue for issue in issues)


def test_forged_underfunded_training_is_detected_before_effect_completion():
    world = military_world(army_manpower=2500)
    idea(world)
    through(world, "local", "doctrine_development")
    result = innovation.begin_stage(world, "local", "training", cost=1)
    p = world.projects[result["id"]]
    p["cost"] = p["spent"] = p["amount"] = p["effects"][0]["amount"] = 1
    assert any("underfunded training cohort" in issue for issue in innovation.validation_issues(world))
