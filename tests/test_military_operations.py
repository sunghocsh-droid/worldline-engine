"""Real strategic routes, bounded authority and received-information decisions."""
import copy
import random
from datetime import timedelta

import pytest

from worldline import military_state as ms, military_operations as ops, military_logistics as logistics
from worldline import military_bridge as bridge, npc_state
from worldline.crisis_benchmark import CrisisReplay
from worldline.models import Region, World, canonical_date


@pytest.fixture
def world():
    world = CrisisReplay().world
    ms.initialize(world, random.Random(42), {"import_armies": False})
    world.regions["ra2"] = Region("Second A region", "a", x=100, infrastructure={"roads": 50, "ports": 50})
    world.regions["ra"].infrastructure.update(roads=50, ports=50)
    scenario_force(world, "a-force", "a", 1000, "ra", training=.6, readiness=1.,
        equipment_state={"personal_weapons": {"quantity": 1000, "condition": 1, "standard": "local"}})
    logistics.add_link(world, "a-road", "ra", "ra2", actor="a", mode="road",
        throughput=20000, travel_days=5, distance_km=100)
    return world


def advance(world, days=1):
    for _ in range(days):
        previous = world.date
        world.date = (canonical_date(previous)+timedelta(days=1)).isoformat()
        ops.tick(world, {"date": world.date, "previous_date": previous, "days": 1})


def planned(world, kind="move", operation_id="march", **kwargs):
    return ops.plan(world, operation_id, "a", ["a-force"], kind,
        target="ra2" if kind in ops.MOVEMENT else None, issuer="a", **kwargs)


def naval(world, actor="a", formation="fleet", region="ra", ships=10, crew=100):
    return scenario_force(world, formation, actor, crew, region, service_type="navy", readiness=1.,
        mobility={"mode": "sea", "speed_km_day": 50, "motorized": False},
        equipment_state={"ships": {"quantity": ships, "condition": 1., "standard": "scenario", "troop_capacity": 200}},
        supply_state={"stocks": {"food": 1000, "ammunition": 1000}, "last_ratio": 1., "consumption": {}, "requirements": {}})


def scenario_force(world, key, actor, manpower, region, **initial_state):
    # Scenario initial stocks/equipment are explicit fixtures, never recruitment
    # production. Personnel are still charged to the actual labour/actor pool.
    raised = bridge.recruit(world, key, actor, manpower, region)
    world.military_state["formations"][key] = ms.formation(key, actor, manpower, region,
        origin_region=region, labour_backing=raised["labour_backing"], **initial_state)
    return copy.deepcopy(world.military_state["formations"][key])


def field_camp(world):
    """An own force actually marches into a foreign field camp, without annexing it."""
    world.regions["rb"].infrastructure.update(roads=50, ports=50)
    logistics.add_link(world, "front-road", "ra", "rb", mode="road", throughput=20000,
        travel_days=5, distance_km=100)
    ops.plan(world, "camp-march", "a", ["a-force"], "raid", "rb", issuer="a")
    ops.send_command(world, "camp-march", "a", delay=0)
    advance(world, 20)
    camp = world.military_state["formations"]["a-force"]
    assert camp["location"] == "rb" and camp["manpower"] > 0
    assert ops.explain(world, "camp-march")["status"] == "completed"
    assert not logistics._access(world, "rb", "a")
    scenario_force(world, "incoming", "a", 100, "ra", training=.6, readiness=1.,
        equipment_state={"personal_weapons": {"quantity": 100, "condition": 1}},
        supply_state={"stocks": {"food": 100}, "last_ratio": 1., "consumption": {}, "requirements": {}})
    return camp


def test_planning_and_unsent_orders_do_not_move_or_complete(world):
    plan = planned(world)
    original_location = world.military_state["formations"]["a-force"]["location"]
    advance(world, 10)
    assert ops.explain(world, "march")["status"] == "planned"
    assert world.military_state["formations"]["a-force"]["location"] == original_location
    assert plan["progress"] == 0
    assert world.military_state["loss_receipts"] == {}


def test_real_march_has_delay_distance_shared_capacity_and_receipts(world):
    planned(world)
    ops.send_command(world, "march", "a", delay=3)
    advance(world, 2)
    assert ops.explain(world, "march")["execution_started_at"] is None
    assert world.military_state["formations"]["a-force"]["journey"] is None
    advance(world)
    started = world.date
    assert ops.explain(world, "march")["execution_started_at"] == started
    advance(world)
    assert world.military_state["formations"]["a-force"]["location"] == "ra"
    assert 0 < world.military_state["formations"]["a-force"]["journey"]["distance_travelled_km"] < 100
    advance(world, 10)
    assert ops.explain(world, "march")["status"] == "completed"
    assert world.military_state["formations"]["a-force"]["location"] == "ra2"
    receipt = next(iter(world.military_state["movement_receipts"].values()))
    assert receipt["elapsed_days"] >= 5 and receipt["distance_km"] == 100
    assert receipt["departure"] == started
    assert world.military_state["formations"]["a-force"]["current_order"] is None


def test_new_dispatch_keeps_received_order_until_receipt_and_hold_really_stops_march(world):
    planned(world)
    ops.send_command(world, "march", "a", delay=0)
    advance(world, 2)
    travelled = world.military_state["formations"]["a-force"]["journey"]["distance_travelled_km"]
    ops.send_command(world, "march", "a", command={"intent": "hold"}, delay=2)
    assert ops.explain(world, "march")["status"] == "in_progress"
    advance(world)
    assert world.military_state["formations"]["a-force"]["journey"]["distance_travelled_km"] > travelled
    advance(world)
    assert ops.explain(world, "march")["status"] == "paused"
    held = copy.deepcopy(world.military_state["formations"]["a-force"]["journey"])
    advance(world, 3)
    assert world.military_state["formations"]["a-force"]["journey"] == held


def test_obsolete_late_command_cannot_override_new_received_command(world):
    planned(world, "hold")
    stale = ops.send_command(world, "march", "a", command={"intent": "execute"}, delay=10)
    advance(world)
    current = ops.send_command(world, "march", "a", command={"intent": "hold_fire"}, delay=1)
    advance(world, 10)
    assert ops.explain(world, "march")["standing_intent"] == "hold_fire"
    assert world.military_state["commands"][stale["id"]]["belief_accepted"] is False
    assert world.military_state["commands"][current["id"]]["belief_accepted"] is True


def test_report_is_unknown_before_receipt_and_stays_old_news_after_delivery(world):
    planned(world, "hold")
    actor = "a:military"
    message = ops.queue_report(world, "march", actor, {"threat": .9, "enemy_strength": 10}, delay=14,
        interpretation={"lesson": "observed border risk", "security": -.2})
    before = copy.deepcopy(world.npc_state["memories"][actor])
    advance(world, 13)
    assert world.npc_state["memories"][actor] == before
    assert ops._known(npc_state.actor_view(world.npc_state, actor, world.date), "march") is None
    advance(world)
    report = world.npc_state["reports"][actor]["military-operation:march"]
    memory = world.npc_state["memories"][actor][-1]
    assert report["as_of"] == message["as_of"] and report["received_at"] == world.date
    assert memory["received_date"] == world.date and memory["day"] == message["as_of"]
    assert npc_state.actor_view(world.npc_state, actor, world.date)["report_metadata"]["military-operation:march"]["age_days"] == 14


def test_new_report_correction_preserves_belief_when_old_report_arrives_later(world):
    planned(world, "hold")
    stale = ops.queue_report(world, "march", "a:military", {"threat": .95}, delay=14)
    advance(world)
    fresh = ops.queue_report(world, "march", "a:military", {"threat": .1}, delay=0)
    ops.deliver_due(world)
    advance(world, 14)
    assert ops._known(npc_state.actor_view(world.npc_state, "a:military", world.date), "march") == {"threat": .1}
    assert world.npc_state["memories"]["a:military"][-1]["observed_facts"] == {"threat": .1}
    assert world.military_state["commands"][stale["id"]]["belief_accepted"] is False
    assert world.military_state["commands"][fresh["id"]]["belief_accepted"] is True


def test_same_arrival_message_nine_ten_keep_latest_numeric_update(world):
    planned(world, "hold")
    for _ in range(8):
        ops.queue_report(world, "march", "a:military", {"threat": .4}, delay=0)
    old = ops.queue_report(world, "march", "a:military", {"threat": .9}, delay=0)
    new = ops.queue_report(world, "march", "a:military", {"threat": .1}, delay=0)
    assert old["id"].endswith("-9") and new["id"].endswith("-10")
    ops.deliver_due(world)
    assert ops._known(npc_state.actor_view(world.npc_state, "a:military", world.date), "march") == {"threat": .1}


def test_formation_cannot_be_double_assigned_or_parent_counted_with_child(world):
    planned(world)
    with pytest.raises(ValueError, match="already committed"):
        planned(world, "hold", operation_id="second")
    ops.send_command(world, "march", "a", command={"intent": "cancel"}, delay=0)
    ops.deliver_due(world)
    bridge.recruit(world, "child", "a", 100, "ra", parent="a-force")
    with pytest.raises(ValueError, match="Parent and subordinate"):
        ops.plan(world, "duplicate", "a", ["a-force", "child"], "hold", issuer="a")


def test_delegated_local_commander_cannot_issue_offensive_or_out_of_theatre_orders(world):
    world.military_state["delegations"]["a:local"] = {"actor": "a", "formations": ["a-force"],
        "regions": ["ra"], "scopes": ["defensive", "local"], "expires": None}
    assert ops.authority(world, "a", ["a-force"], "defend", "a:local", "ra")["feasible"]
    assert not ops.authority(world, "a", ["a-force"], "attack", "a:local", "ra2")["feasible"]
    with pytest.raises(ValueError, match="scope|theatre"):
        ops.plan(world, "illegal", "a", ["a-force"], "attack", "ra2", commander="a:local", issuer="a:local")


def test_actual_disconnected_route_and_missing_ships_are_physical_limits(world):
    world.military_state["links"].clear()
    with pytest.raises(ValueError, match="physically usable"):
        planned(world)
    with pytest.raises(ValueError, match="ships"):
        ops.plan(world, "paper-navy", "a", ["a-force"], "blockade", "ra", issuer="a")


def test_partial_transport_cannot_teleport_amphibious_army(world):
    naval(world, ships=1)
    world.regions["rb"].infrastructure["ports"] = 50
    logistics.add_link(world, "sea", "ra", "rb", actor=None, mode="sea", throughput=20000,
        travel_days=4, distance_km=200)
    with pytest.raises(ValueError, match="transport capacity"):
        ops.plan(world, "landing", "a", ["a-force", "fleet"], "amphibious", "rb", issuer="a")


def test_only_present_supplied_ships_affect_blockaded_link_and_their_opponent(world):
    naval(world)
    world.regions["rb"].infrastructure["ports"] = 50
    logistics.add_link(world, "sea", "ra", "rb", actor=None, mode="sea", throughput=20000,
        travel_days=4, distance_km=200)
    ops.plan(world, "block", "a", ["fleet"], "blockade", "rb", issuer="a", params={"opponent": "b"})
    assert ops.blockade_coverage(world, "sea", "b")["disruption"] == 0
    ops.send_command(world, "block", "a", delay=0)
    advance(world, 10)
    coverage = ops.blockade_coverage(world, "sea", "b")
    assert 0 < coverage["disruption"] < 1 and coverage["formations"] == ["fleet"]
    assert ops.blockade_coverage(world, "sea", "a")["disruption"] == 0
    assert ops.blockade_coverage(world, "sea", "c")["disruption"] == 0
    assert ops.blockade_coverage(world, "a-road", "b")["disruption"] == 0
    world.military_state["formations"]["fleet"]["supply_state"]["last_ratio"] = 0
    assert ops.blockade_coverage(world, "sea", "b")["disruption"] == 0


def test_save_restore_continues_same_journey_reports_commands_and_rng(world):
    planned(world, commander="a:local")
    world.military_state["delegations"]["a:local"] = {"actor": "a", "formations": ["a-force"],
        "regions": ["ra", "ra2"], "scopes": ["movement", "local"], "expires": None}
    ops.send_command(world, "march", "a", delay=2)
    ops.queue_report(world, "march", "a:local", {"threat": .8, "enemy_strength": 50, "own_strength": 150}, delay=2)
    advance(world, 4)
    restored = World.from_dict(copy.deepcopy(world.to_dict()))
    assert restored.military_state == world.military_state
    for _ in range(15):
        advance(world)
        advance(restored)
    assert restored.to_dict() == world.to_dict()


def test_unreceived_hidden_enemy_information_cannot_trigger_commander_decision(world):
    planned(world, "hold", commander="a:local")
    world.military_state["delegations"]["a:local"] = {"actor": "a", "formations": ["a-force"],
        "regions": ["ra"], "scopes": ["defensive", "local"], "expires": None}
    original_streams = copy.deepcopy(world.military_state["rng_streams"])
    ops.queue_report(world, "march", "a:local", {"threat": .99, "enemy_strength": 1}, delay=20)
    advance(world, 10)
    assert world.military_state["rng_streams"] == original_streams
    assert not any(e["kind"] == "military_commander_decision" for e in world.military_state["outbox"])


def test_local_misinterpretation_can_produce_limited_trigger_but_not_national_war(world):
    planned(world, "hold", commander="a:local")
    world.military_state["delegations"]["a:local"] = {"actor": "a", "formations": ["a-force"],
        "regions": ["ra"], "scopes": ["defensive", "local"], "expires": None}
    person = world.npc_state["persons"]["a:local"]
    person["traits"].update(risk_tolerance=.95, prestige_sensitivity=.95)
    person["biases"].update(overconfidence=.9, enemy_underestimation=.9)
    relations = copy.deepcopy(world.relations)
    ops.send_command(world, "march", "a", command={"intent": "hold_fire"}, delay=0)
    ops.queue_report(world, "march", "a:local", {"threat": .95, "enemy_strength": 1, "own_strength": 1000}, delay=0)
    advance(world)
    decisions = [e for e in world.military_state["outbox"] if e["kind"] == "military_commander_decision"]
    assert decisions and decisions[-1]["received_intent"] == "hold_fire"
    limited = [e for e in world.military_state["outbox"] if e["kind"] == "military_local_action"]
    assert limited and limited[-1]["actual_national_policy_changed"] is False
    assert world.relations == relations
    assert ops.explain(world, "march")["status"] == "paused"


def test_public_plan_report_and_explanation_are_detached(world):
    result = planned(world, "hold")
    report = ops.queue_report(world, "march", "a:military", {"details": {"threat": .4}}, delay=0)
    before = copy.deepcopy(world.to_dict())
    result["formations"].clear()
    report["data"]["facts"]["details"]["threat"] = 99
    explanation = ops.explain(world, "march")
    explanation["reason_trace"].clear()
    assert world.to_dict() == before


def test_cancelling_partial_march_requires_actual_return_distance(world):
    planned(world)
    ops.send_command(world, "march", "a", delay=0)
    advance(world, 3)
    before = world.military_state["formations"]["a-force"]["journey"]["leg_progress_km"]
    assert before > 0
    ops.send_command(world, "march", "a", command={"intent": "cancel"}, delay=0)
    advance(world)
    operation = ops.explain(world, "march")
    assert operation["status"] == "in_progress" and operation["cancel_return"]
    assert world.military_state["formations"]["a-force"]["journey"]["legs"][0]["distance_km"] == before
    advance(world)
    assert ops.explain(world, "march")["status"] == "in_progress"
    advance(world, 10)
    assert ops.explain(world, "march")["status"] == "cancelled"
    assert world.military_state["formations"]["a-force"]["location"] == "ra"
    receipt = next(iter(world.military_state["movement_receipts"].values()))
    assert receipt["phase"].startswith("cancel-return") and receipt["distance_km"] == before


def test_received_withdrawal_replaces_attack_after_physical_turnaround(world):
    planned(world, "attack")
    ops.send_command(world, "march", "a", delay=0)
    advance(world, 3)
    ops.send_command(world, "march", "a", command={"intent": "withdraw", "target": "ra"}, delay=0)
    advance(world)
    assert ops.explain(world, "march")["cancel_return"]
    advance(world, 12)
    result = ops.explain(world, "march")
    assert result["kind"] == "withdraw" and result["target"] == "ra" and result["status"] == "completed"
    assert world.military_state["formations"]["a-force"]["location"] == "ra"
    assert not result["results"] and not world.military_state["loss_receipts"]


def test_actual_amphibious_units_share_ship_pace_and_transport_reservation(world):
    naval(world)
    world.regions["rb"].infrastructure["ports"] = 50
    logistics.add_link(world, "sea", "ra", "rb", mode="sea", throughput=20000, travel_days=4, distance_km=200)
    ops.plan(world, "landing", "a", ["a-force", "fleet"], "amphibious", "rb", issuer="a")
    ops.send_command(world, "landing", "a", delay=0)
    advance(world, 2)
    ground = world.military_state["formations"]["a-force"]
    fleet = world.military_state["formations"]["fleet"]
    assert ground["journey"]["leg_progress_km"] == fleet["journey"]["leg_progress_km"] > 0
    assert ground["location"] == fleet["location"] == "ra"
    reservation = world.military_state["transport_reservations"][world.date]["sea"]
    assert reservation["used"] == 1100 and len(reservation["reservations"]) == 1
    fleet["equipment_state"]["ships"]["quantity"] = 0
    before = ground["journey"]["leg_progress_km"]
    advance(world)
    assert ground["journey"]["leg_progress_km"] == fleet["journey"]["leg_progress_km"] == before
    assert not world.military_state["loss_receipts"]


def test_naval_withdrawal_uses_real_sea_route(world):
    naval(world)
    logistics.add_link(world, "a-sea", "ra", "ra2", actor="a", mode="sea", throughput=20000, travel_days=3, distance_km=120)
    operation = ops.plan(world, "fleet-withdraw", "a", ["fleet"], "withdraw", "ra2", issuer="a")
    assert operation["route_quote"]["fleet"]["legs"][0]["mode"] == "sea"
    ops.send_command(world, "fleet-withdraw", "a", delay=0)
    advance(world, 8)
    assert world.military_state["formations"]["fleet"]["location"] == "ra2"


def test_marines_use_actual_ships_when_embarked_and_land_routes_after_landing(world):
    world.military_state["formations"]["a-force"]["service_type"] = "marine"
    land = planned(world)
    assert land["route_quote"]["a-force"]["legs"][0]["mode"] == "road"
    ops.send_command(world, "march", "a", command={"intent": "cancel"}, delay=0)
    ops.deliver_due(world)
    naval(world)
    logistics.add_link(world, "a-sea", "ra", "ra2", actor="a", mode="sea", throughput=20000, travel_days=3, distance_km=120)
    ops.plan(world, "marine-landing", "a", ["a-force", "fleet"], "amphibious", "ra2", issuer="a")
    ops.send_command(world, "marine-landing", "a", delay=0)
    advance(world, 2)
    marine, ship = (world.military_state["formations"][key] for key in ("a-force", "fleet"))
    assert marine["journey"]["leg_progress_km"] == ship["journey"]["leg_progress_km"] > 0
    advance(world, 5)
    assert marine["location"] == ship["location"] == "ra2"


def test_unfinished_siege_investment_continues_with_unique_actual_results(world):
    scenario_force(world, "b-garrison", "b", 1100, "rb", training=.6, readiness=1.,
        equipment_state={"personal_weapons": {"quantity": 1100, "condition": 1., "standard": "scenario"}})
    world.regions["rb"].infrastructure["roads"] = 50
    world.regions["rb"].infrastructure["fortifications"] = 5
    world.economy["regions"]["rb"]["stocks"]["goods"]["agriculture"] = 50  # Finite scenario production stock.
    logistics.add_link(world, "border-road", "ra", "rb", mode="road", throughput=20000, distance_km=1)
    bridge.register_fortress(world, "rb", "b", strength=5, food=50, garrison=["b-garrison"])
    ops.plan(world, "siege", "a", ["a-force"], "siege", "rb", issuer="a",
        params={"opponent": "b", "objective_threshold": .99})
    ops.send_command(world, "siege", "a", delay=0)
    advance(world, 7)
    assert not ops.explain(world, "siege")["results"]
    advance(world, 10)
    operation = ops.explain(world, "siege")
    assert operation["status"] == "in_progress"
    assert len(operation["results"]) >= 2 and len(set(operation["results"])) == len(operation["results"])
    assert len(operation["actual_results"]) == len(operation["results"])
    assert all(result["reason_trace"]["siege_days"] >= 7 for result in operation["actual_results"])


def test_defensive_delay_can_meet_aim_despite_tactical_setback(world):
    scenario_force(world, "b-invader", "b", 1100, "rb", training=.9, readiness=1.,
        equipment_state={"personal_weapons": {"quantity": 1100, "condition": 1., "standard": "scenario"}})
    world.military_state["formations"]["b-invader"]["location"] = "ra"
    world.military_state["formations"]["a-force"].update(training=.1, morale=.2, readiness=.2)
    operation = ops.plan(world, "delay", "a", ["a-force"], "defend", "ra", issuer="a",
        params={"opponent": "b", "war_aim": "delay", "required_delay_days": 7})
    ops.send_command(world, "delay", "a", delay=0)
    advance(world, 7)
    assert not ops.explain(world, "delay")["results"]
    advance(world)
    result = ops.explain(world, "delay")["actual_results"][0]
    assert result["tactical_result"] == "setback" and result["strategic_objective_achieved"]
    assert result["objective_completion"] == 1 and result["casualties"]["a-force"]["battle_casualties"] > 0
    before = copy.deepcopy(world.military_state["loss_receipts"])
    advance(world, 10)
    assert world.military_state["loss_receipts"] == before


def test_peacetime_defensive_posture_does_not_attack_foreign_co_location(world):
    scenario_force(world, "b-visitor", "b", 100, "rb")
    world.military_state["formations"]["b-visitor"]["location"] = "ra"
    ops.plan(world, "defense", "a", ["a-force"], "defend", "ra", issuer="a")
    ops.send_command(world, "defense", "a", delay=0)
    advance(world, 20)
    assert not ops.explain(world, "defense")["results"] and not world.military_state["loss_receipts"]


def test_nonstate_actor_uses_finite_manpower_and_actual_operational_assets(world):
    ms.add_actor(world, "company", kind="mercenary", home_region="ra", manpower_pool=50, trained_reserve=20, treasury=100)
    bridge.recruit(world, "company-force", "company", 50, "ra")
    assert world.military_state["actors"]["company"]["manpower_pool"] == 0
    with pytest.raises(ValueError, match="exhausted"):
        bridge.recruit(world, "extra", "company", 1, "ra")
    ops.plan(world, "company-hold", "company", ["company-force"], "hold", issuer="company")
    ops.send_command(world, "company-hold", "company", delay=2)
    advance(world, 3)
    assert ops.explain(world, "company-hold")["status"] == "in_progress"


def test_training_cohort_cannot_be_allocated_to_an_operation_at_the_same_time(world):
    cohort = bridge.train_replacements(world, "trained", "a-force", 10, duration_days=120)
    before = copy.deepcopy(world.military_state["formations"][cohort["cohort"]])
    with pytest.raises(ValueError, match="training"):
        ops.plan(world, "training-shortcut", "a", [cohort["cohort"]], "move", "ra2", issuer="a")
    assert world.military_state["formations"][cohort["cohort"]] == before
    assert "training-shortcut" not in world.military_state["operations"]


def test_country_linked_company_uses_real_government_ruler_authority(world):
    ms.add_actor(world, "state-company", kind="company", country="a", home_region="ra")
    bridge.recruit(world, "company-force", "state-company", 50, "ra")
    ruler = world.npc_state["governments"]["a"]["ruler"]
    assert ops.authority(world, "state-company", ["company-force"], "hold", ruler)["feasible"]
    assert not ops.authority(world, "state-company", ["company-force"], "hold", "b:military")["feasible"]


def test_convoy_escort_shares_cargo_journey_and_cannot_accept_independent_mission(world):
    naval(world, formation="transport")
    naval(world, formation="escort")
    logistics.add_link(world, "a-sea", "ra", "ra2", actor="a", mode="sea", throughput=20000, travel_days=3, distance_km=120)
    logistics.add_convoy(world, "convoy", "a", ["transport"], escorts=["escort"])
    world.military_state["actors"]["a"]["stocks"]["food"] = 200  # Explicit scenario opening inventory.
    logistics.add_depot(world, "source", "a", "ra", stocks={"food": 200})
    logistics.add_depot(world, "target", "a", "ra2")
    ops.plan(world, "escort-order", "a", ["escort"], "escort", "ra2", issuer="a", params={"convoy": "convoy"})
    ops.send_command(world, "escort-order", "a", delay=0)
    advance(world)
    logistics.dispatch(world, "cargo", "source", "target", {"food": 100}, mode="sea", convoy="convoy")
    with pytest.raises(ValueError, match="committed"):
        ops.plan(world, "double-transport", "a", ["transport"], "blockade", "ra2", issuer="a")
    for _ in range(8):
        advance(world)
        logistics.tick(world)
        assert world.military_state["formations"]["escort"]["location"] == world.military_state["formations"]["transport"]["location"]
        assert world.military_state["formations"]["escort"]["journey"] is None
    assert world.military_state["shipments"]["cargo"]["status"] == "delivered"
    assert ops.explain(world, "escort-order")["status"] == "completed"
    assert world.military_state["formations"]["transport"]["location"] == "ra2"


@pytest.mark.parametrize("change", ["expired", "wrong_actor", "inactive"])
def test_invalid_local_delegation_cannot_make_autonomous_physical_trigger(world, change):
    planned(world, "hold", commander="a:local")
    delegation = {"actor": "a", "formations": ["a-force"], "regions": ["ra"], "scopes": ["local"], "expires": None}
    world.military_state["delegations"]["a:local"] = delegation
    if change == "expired":
        delegation["expires"] = "1896-12-31"
    elif change == "wrong_actor":
        delegation["actor"] = "b"
    else:
        world.npc_state["persons"]["a:local"]["active"] = False
    ops.queue_report(world, "march", "a:local", {"threat": .99}, delay=0)
    advance(world, 10)
    assert not any(e["kind"] == "military_commander_decision" for e in world.military_state["outbox"])


def test_real_land_marching_accumulates_distance_based_fatigue(world):
    planned(world)
    ops.send_command(world, "march", "a", delay=0)
    advance(world, 2)
    force = world.military_state["formations"]["a-force"]
    distance = force["journey"]["distance_travelled_km"]
    assert distance > 0 and force["fatigue"] == pytest.approx(distance/1000)


def test_forced_march_is_faster_but_costs_finite_food_fatigue_and_cohesion(world):
    normal = copy.deepcopy(world)
    for game, forced in ((normal, False), (world, True)):
        game.military_state["formations"]["a-force"]["supply_state"]["stocks"]["food"] = 5
        planned(game, params={"forced_march": forced})
        ops.send_command(game, "march", "a", delay=0)
        advance(game, 3)
    a, b = (game.military_state["formations"]["a-force"] for game in (normal, world))
    assert b["journey"]["distance_travelled_km"] > a["journey"]["distance_travelled_km"]
    assert b["fatigue"] > a["fatigue"] and b["cohesion"] < a["cohesion"]
    assert 0 < b["supply_state"]["stocks"]["food"] < a["supply_state"]["stocks"]["food"]
    receipts = [r for r in world.military_state["logistics_receipts"].values() if r.get("id", "").startswith("forced-march:")]
    assert len(receipts) == 2
    assert 5-b["supply_state"]["stocks"]["food"] == pytest.approx(sum(r["actual"]["food"] for r in receipts))
    before = copy.deepcopy(world.to_dict())
    ops.tick(world, {"days": 1, "date": world.date})
    assert world.to_dict() == before


def test_forced_rations_do_not_erase_existing_ordinary_supply_shortage(world):
    force = world.military_state["formations"]["a-force"]
    force["supply_state"].update(last_ratio=.4, component_ratios={"food": .4, "fuel": .3, "ammunition": .2})
    force["supply_state"]["stocks"]["food"] = 5
    planned(world, params={"forced_march": True})
    ops.send_command(world, "march", "a", delay=0)
    advance(world, 2)
    assert force["supply_state"]["last_ratio"] == .4
    assert force["supply_state"]["component_ratios"] == {"food": .4, "fuel": .3, "ammunition": .2}


def test_blocked_march_has_no_phantom_fatigue_or_extra_food(world):
    planned(world, params={"forced_march": True})
    ops.send_command(world, "march", "a", delay=0)
    advance(world)
    world.military_state["links"]["a-road"]["disruption"] = 1.
    advance(world, 3)
    force = world.military_state["formations"]["a-force"]
    assert force["fatigue"] == 0 and force["journey"]["distance_travelled_km"] == 0
    assert not any(r.get("id", "").startswith("forced-march:") for r in world.military_state["logistics_receipts"].values())


def test_forced_foot_order_does_not_accelerate_ships_or_embarked_troops(world):
    naval(world)
    logistics.add_link(world, "a-sea", "ra", "ra2", actor="a", mode="sea", throughput=20000, travel_days=3, distance_km=120)
    normal = copy.deepcopy(world)
    for game, forced in ((normal, False), (world, True)):
        ops.plan(game, "landing", "a", ["a-force", "fleet"], "amphibious", "ra2", issuer="a", params={"forced_march": forced})
        ops.send_command(game, "landing", "a", delay=0)
        advance(game, 2)
    for key in ("a-force", "fleet"):
        force = world.military_state["formations"][key]
        assert force["journey"] == normal.military_state["formations"][key]["journey"]
        assert force["fatigue"] == 0
    assert not any(r.get("id", "").startswith("forced-march:") for r in world.military_state["logistics_receipts"].values())


def test_audited_save_preserves_real_in_progress_turnaround(world):
    planned(world)
    ops.send_command(world, "march", "a", delay=0)
    advance(world, 3)
    ops.send_command(world, "march", "a", command={"intent": "withdraw", "target": "ra"}, delay=0)
    advance(world)
    assert not ops.validation_issues(world)
    restored = World.from_dict(copy.deepcopy(world.to_dict()))
    assert not ops.validation_issues(restored)
    for _ in range(12):
        advance(world)
        advance(restored)
    assert restored.to_dict() == world.to_dict()


@pytest.mark.parametrize("case", ["unknown_memory_kwarg", "bad_memory_impact", "unrelated_observation", "bad_quality", "unknown_commander", "nonexistent_result", "invalid_journey_link"])
def test_audit_rejects_undeliverable_report_and_impossible_physical_history(world, case):
    planned(world)
    message = ops.queue_report(world, "march", "a:military", {"threat": .5}, delay=14)
    stored = world.military_state["commands"][message["id"]]
    if case == "unknown_memory_kwarg":
        stored["memory"]["bogus"] = 1
    elif case == "bad_memory_impact":
        stored["memory"]["salience"] = "very"
    elif case == "unrelated_observation":
        stored["data"]["event_id"] = "unrelated"
    elif case == "bad_quality":
        stored["confidence"] = 4
    elif case == "unknown_commander":
        world.military_state["operations"]["march"]["commander"] = "missing"
    elif case == "nonexistent_result":
        world.military_state["operations"]["march"]["results"] = ["fabricated-result"]
    else:
        ops.send_command(world, "march", "a", delay=0)
        advance(world, 2)
        world.military_state["formations"]["a-force"]["journey"]["legs"][0]["link"] = "nonexistent-road"
    assert ops.validation_issues(world)


@pytest.mark.parametrize("case", ["backward_receipt", "unknown_region", "repeated_result", "rewritten_message"])
def test_audit_rejects_corrupt_operation_or_communication_save(world, case):
    planned(world, "hold")
    message = ops.send_command(world, "march", "a", delay=0)
    previous = copy.deepcopy(world)
    if case == "backward_receipt":
        world.military_state["commands"][message["id"]].update(status="delivered", received_at="1896-12-31", belief_accepted=True)
    elif case == "unknown_region":
        world.military_state["operations"]["march"]["target"] = "missing-region"
    elif case == "repeated_result":
        world.military_state["operations"]["march"]["results"] = ["same", "same"]
    else:
        world.military_state["commands"][message["id"]]["data"] = {"intent": "cancel"}
    assert ops.validation_issues(world, previous)


def test_reinforcements_reach_actual_own_foreign_camp_only_after_received_march(world):
    camp = field_camp(world)
    before = world.military_state["formations"]["incoming"]["manpower"]
    plan = ops.plan(world, "front-relief", "a", ["incoming"], "relief", "rb", issuer="a",
        params={"recipient_formation": camp["id"]})
    assert plan["kind"] == "reinforce" and plan["route_quote"]["incoming"]["path"] == ["ra", "rb"]
    ops.send_command(world, "front-relief", "a", delay=3)
    advance(world, 2)
    assert world.military_state["formations"]["incoming"]["journey"] is None
    advance(world, 2)
    incoming = world.military_state["formations"]["incoming"]
    assert incoming["location"] == "ra" and 0 < incoming["journey"]["distance_travelled_km"] < 100
    assert world.military_state["transport_reservations"]
    advance(world, 10)
    assert incoming["location"] == "rb" and incoming["manpower"] == before
    assert ops.explain(world, "front-relief")["status"] == "completed"
    receipt = next(r for r in world.military_state["movement_receipts"].values() if r["formation"] == "incoming")
    assert receipt["distance_km"] == 100 and receipt["elapsed_days"] >= 5
    assert not logistics._access(world, "rb", "a")  # Supplying a camp grants no national transit rights.
    assert not ops.validation_issues(world)


@pytest.mark.parametrize("recipient", [None, "missing", "incoming", "b-camp"])
def test_foreign_region_has_no_reinforcement_access_without_actual_own_camp(world, recipient):
    field_camp(world)
    scenario_force(world, "b-camp", "b", 100, "rb")
    params = {} if recipient is None else {"recipient_formation": recipient}
    before = copy.deepcopy(world.to_dict())
    with pytest.raises(ValueError, match="route|recipient"):
        ops.plan(world, "unauthorized-entry", "a", ["incoming"], "reinforce", "rb", issuer="a", params=params)
    assert world.to_dict() == before


def test_camp_exception_never_grants_hostile_intermediate_transit(world):
    camp = field_camp(world)
    world.military_state["links"].pop("front-road")
    world.regions["hostile-midpoint"] = Region("Enemy transit region", "b", x=50, infrastructure={"roads": 50})
    logistics.add_link(world, "first-enemy-leg", "ra", "hostile-midpoint", mode="road", throughput=20000, distance_km=50)
    logistics.add_link(world, "last-camp-leg", "hostile-midpoint", "rb", mode="road", throughput=20000, distance_km=50)
    with pytest.raises(ValueError, match="route"):
        ops.plan(world, "not-a-transit-license", "a", ["incoming"], "reinforce", "rb", issuer="a",
            params={"recipient_formation": camp["id"]})


@pytest.mark.parametrize("change", ["dead", "withdrawal", "moved"])
def test_recipient_disappearance_blocks_new_and_in_transit_reinforcements(world, change):
    camp = field_camp(world)
    ops.plan(world, "front-relief", "a", ["incoming"], "reinforce", "rb", issuer="a",
        params={"recipient_formation": camp["id"]})
    ops.send_command(world, "front-relief", "a", delay=0)
    advance(world, 2)
    incoming = world.military_state["formations"]["incoming"]
    travelled, fatigue = incoming["journey"]["distance_travelled_km"], incoming["fatigue"]
    if change == "dead":
        bridge.demobilize(world, camp["id"], camp["manpower"])
    else:
        ops.plan(world, "camp-withdrawal", "a", [camp["id"]], "withdraw", "ra", issuer="a")
        ops.send_command(world, "camp-withdrawal", "a", delay=0)
        ops.deliver_due(world)
        ops._begin(world, world.military_state["operations"]["camp-withdrawal"])
        if change == "moved":
            advance(world, 10)
            assert camp["location"] == "ra"
    advance(world, 2)
    assert incoming["location"] == "ra"
    assert incoming["journey"]["distance_travelled_km"] == travelled and incoming["fatigue"] == fatigue
    assert not any(key.startswith("movement:front-relief") for r in
        world.military_state["transport_reservations"].get(world.date, {}).values() for key in r["reservations"])
    assert any(t["factor"] == "movement_constraint" for t in ops.explain(world, "front-relief")["reason_trace"])
    scenario_force(world, "second-incoming", "a", 10, "ra")
    with pytest.raises(ValueError, match="live stationary camp"):
        ops.plan(world, "late-relief", "a", ["second-incoming"], "reinforce", "rb", issuer="a",
            params={"recipient_formation": camp["id"]})


def test_received_reinforcement_rechecks_real_delegation_before_progress(world):
    camp = field_camp(world)
    world.military_state["delegations"]["a:local"] = {"actor": "a", "formations": ["incoming"],
        "regions": ["ra", "rb"], "scopes": ["movement"], "expires": None}
    ops.plan(world, "delegated-relief", "a", ["incoming"], "reinforce", "rb", issuer="a:local",
        params={"recipient_formation": camp["id"]})
    ops.send_command(world, "delegated-relief", "a:local", delay=0)
    advance(world)
    world.military_state["delegations"]["a:local"]["scopes"] = []
    advance(world, 3)
    f = world.military_state["formations"]["incoming"]
    assert f["location"] == "ra" and f["journey"]["distance_travelled_km"] == 0
    assert any(t["detail"].get("required_scope") == "movement" for t in ops.explain(world, "delegated-relief")["reason_trace"] if t["factor"] == "movement_constraint")


def test_preplanned_ordinary_movement_rechecks_lost_intermediate_access(world):
    world.regions["rb"].infrastructure["roads"] = 50
    relation = next(r for r in world.relations.values() if {r["a"], r["b"]} == {"a", "b"})
    relation["military_access"] = True
    world.military_state["links"].clear()
    logistics.add_link(world, "intermediate", "ra", "rb", mode="road", throughput=20000, distance_km=50)
    logistics.add_link(world, "beyond", "rb", "ra2", mode="road", throughput=20000, distance_km=50)
    planned(world)
    ops.send_command(world, "march", "a", delay=0)
    advance(world)
    relation["military_access"] = False
    advance(world, 3)
    f = world.military_state["formations"]["a-force"]
    assert f["location"] == "ra" and f["journey"]["distance_travelled_km"] == 0
    assert not world.military_state["transport_reservations"]


def sea_reinforcement(world):
    camp = field_camp(world)
    world.military_state["links"].pop("front-road")
    naval(world, formation="relief-fleet", ships=2)
    logistics.add_link(world, "relief-sea", "ra", "rb", mode="sea", throughput=20000, travel_days=4, distance_km=200)
    return ops.plan(world, "sea-relief", "a", ["incoming", "relief-fleet"], "reinforce", "rb", issuer="a",
        params={"recipient_formation": camp["id"], "transport_mode": "sea"})


def test_actual_seaborne_reinforcement_shares_real_voyage_and_one_reservation(world):
    plan = sea_reinforcement(world)
    assert plan["transport_mode"] == "sea" and plan["embarkation"]["ra"]["troops"] == ["incoming"]
    ops.send_command(world, "sea-relief", "a", delay=2)
    advance(world)
    assert world.military_state["formations"]["incoming"]["journey"] is None
    advance(world, 2)
    troops, fleet = (world.military_state["formations"][key] for key in ("incoming", "relief-fleet"))
    assert troops["journey"]["distance_travelled_km"] == fleet["journey"]["distance_travelled_km"] > 0
    today_reservations = world.military_state["transport_reservations"][world.date]["relief-sea"]
    assert len(today_reservations["reservations"]) == 1 and today_reservations["used"] == 200
    advance(world, 12)
    assert troops["location"] == fleet["location"] == "rb"
    assert not ops.explain(world, "sea-relief")["results"]  # Relief is no free amphibious assault.
    assert ops.explain(world, "sea-relief")["status"] == "completed"
    assert not ops.validation_issues(world)


@pytest.mark.parametrize("failure", ["no_fleet", "no_seats", "different_port", "no_crew"])
def test_seaborne_reinforcement_requires_actual_co_located_crew_and_seats(world, failure):
    camp = field_camp(world)
    world.military_state["links"].pop("front-road")
    logistics.add_link(world, "relief-sea", "ra", "rb", mode="sea", throughput=20000, distance_km=200)
    formation_ids = ["incoming"]
    if failure != "no_fleet":
        naval(world, formation="relief-fleet", ships=1)
        formation_ids.append("relief-fleet")
        if failure == "different_port":
            logistics.add_link(world, "fleet-staging", "ra", "ra2", actor="a", mode="sea", throughput=20000, distance_km=100)
            ops.plan(world, "fleet-relocation", "a", ["relief-fleet"], "move", "ra2", issuer="a")
            ops.send_command(world, "fleet-relocation", "a", delay=0)
            advance(world, 8)
            assert world.military_state["formations"]["relief-fleet"]["location"] == "ra2"
        elif failure == "no_seats":
            world.military_state["formations"]["relief-fleet"]["equipment_state"]["ships"]["troop_capacity"] = 1
        elif failure == "no_crew":
            bridge.demobilize(world, "relief-fleet", 100)
    with pytest.raises(ValueError, match="ships|capacity|empty formation"):
        ops.plan(world, "impossible-sea-relief", "a", formation_ids, "reinforce", "rb", issuer="a",
            params={"recipient_formation": camp["id"], "transport_mode": "sea"})


def test_lost_transport_ships_halt_embarked_reinforcement_without_teleport_or_capacity_use(world):
    sea_reinforcement(world)
    ops.send_command(world, "sea-relief", "a", delay=0)
    advance(world, 2)
    troops = world.military_state["formations"]["incoming"]
    travelled = troops["journey"]["distance_travelled_km"]
    from worldline.military_combat import resolve
    # Loss is externally adjudicated, using the bridge's once-only finite account.
    result = resolve(world, {"id": "external-fleet-loss", "actor": "a", "opponent": "b", "kind": "naval_attack",
        "formations": ["relief-fleet"], "target": "ra", "params": {"defenders": []}})
    result["equipment_losses"] = {"relief-fleet": {"ships": 2}}
    bridge.apply_result(world, result)
    advance(world, 3)
    assert troops["location"] == "ra" and troops["journey"]["distance_travelled_km"] == travelled
    assert not world.military_state["transport_reservations"].get(world.date, {})


def test_sea_relief_saved_mid_voyage_preserves_bound_camp_ship_pace_and_next_state(world):
    sea_reinforcement(world)
    ops.send_command(world, "sea-relief", "a", delay=0)
    advance(world, 2)
    restored = World.from_dict(world.to_dict())
    for _ in range(12):
        advance(world)
        advance(restored)
    assert world.to_dict() == restored.to_dict()


@pytest.mark.parametrize("kind", ["reinforce", "amphibious"])
def test_real_ship_crew_shortage_limits_embarked_troop_capacity(world, kind):
    camp = field_camp(world)
    naval(world, formation="undercrewed-fleet", ships=10, crew=1)
    fleet = world.military_state["formations"]["undercrewed-fleet"]
    fleet["equipment_state"]["ships"]["crew_per_ship"] = 10
    assert ops._transport_capacity(fleet) == pytest.approx(20.)  # Only 1/100 of 2,000 seats is crewed.
    logistics.add_link(world, "undercrewed-sea", "ra", "rb", mode="sea", throughput=20000, distance_km=200)
    with pytest.raises(ValueError, match="transport capacity"):
        ops.plan(world, "not-a-paper-fleet", "a", ["incoming", "undercrewed-fleet"], kind, "rb", issuer="a",
            params={"recipient_formation": camp["id"], "transport_mode": "sea"} if kind == "reinforce" else {})


def test_sea_reinforcements_cannot_reuse_fleet_already_carrying_real_cargo(world):
    camp = field_camp(world)
    naval(world, formation="reserved-fleet")
    logistics.add_link(world, "cargo-sea", "ra", "ra2", actor="a", mode="sea", throughput=20000, distance_km=200)
    world.military_state["actors"]["a"]["stocks"]["food"] = 100  # Finite scenario inventory.
    logistics.add_depot(world, "source", "a", "ra", stocks={"food": 100})
    logistics.add_depot(world, "target", "a", "ra2")
    logistics.add_convoy(world, "active-convoy", "a", ["reserved-fleet"])
    logistics.dispatch(world, "real-cargo", "source", "target", {"food": 50}, mode="sea", convoy="active-convoy")
    before = world.to_dict()
    with pytest.raises(ValueError, match="active convoy"):
        ops.plan(world, "double-booked-relief", "a", ["incoming", "reserved-fleet"], "reinforce", "rb", issuer="a",
            params={"recipient_formation": camp["id"], "transport_mode": "sea"})
    assert world.to_dict() == before


def test_real_trained_replacement_registry_binds_front_and_incorporates_only_after_arrival(world):
    from worldline import projects
    camp = field_camp(world)
    before_camp = camp["manpower"]
    replacement = bridge.train_replacements(world, "actual-cohort", camp["id"], 10, duration_days=90)
    cohort = replacement["cohort"]
    world.military_state["actors"]["a"]["stocks"]["food"] = 10  # Finite scenario opening stores.
    world.military_state["actors"]["a"]["equipment_reserve"]["personal_weapons"] = 10
    logistics.add_depot(world, "training-source", "a", "ra", stocks={"food": 10})
    logistics.transfer_to_formation(world, "training-source", cohort, {"food": 5}, receipt_id="actual-training-food")
    with pytest.raises(ValueError, match="already committed"):
        ops.plan(world, "untrained-relief", "a", [cohort], "reinforce", "rb", issuer="a")
    for _ in range(90):
        previous = world.date
        advance(world)
        projects.tick(world, {"date": world.date, "previous_date": previous, "days": 1}, random.Random(0))
    assert world.military_state["replacements"]["actual-cohort"]["status"] == "ready"
    assert world.projects[replacement["project"]]["spent"] == 10
    bridge.issue_equipment(world, "a", cohort, "personal_weapons", 10)
    labour = world.economy["regions"]["ra"]["labour_state"]
    charged = labour["standing_army"] + labour["mobilized"]
    plan = ops.plan(world, "actual-replacement-march", "a", [cohort], "reinforce", "rb", issuer="a")
    assert plan["params"]["recipient_formation"] == camp["id"]
    with pytest.raises(ValueError, match="actually arrive"):
        bridge.incorporate_replacements(world, "actual-cohort")
    ops.send_command(world, "actual-replacement-march", "a", delay=3)
    advance(world, 14)
    assert world.military_state["formations"][cohort]["location"] == "rb"
    assert camp["manpower"] == before_camp  # Arrival and incorporation are separate finite steps.
    bridge.incorporate_replacements(world, "actual-cohort")
    assert camp["manpower"] == before_camp + 10 and world.military_state["formations"][cohort]["manpower"] == 0
    assert labour["standing_army"] + labour["mobilized"] == pytest.approx(charged)


def test_sea_reinforcement_halts_shared_voyage_when_own_recipient_withdraws(world):
    sea_reinforcement(world)
    ops.send_command(world, "sea-relief", "a", delay=0)
    advance(world, 2)
    before = {f: world.military_state["formations"][f]["journey"]["distance_travelled_km"]
        for f in ("incoming", "relief-fleet")}
    # Actual host departure uses its real road and received withdrawal order.
    logistics.add_link(world, "host-withdraw-road", "rb", "ra", mode="road", throughput=20000, distance_km=100)
    ops.plan(world, "host-withdrawal", "a", ["a-force"], "withdraw", "ra", issuer="a")
    ops.send_command(world, "host-withdrawal", "a", delay=0)
    advance(world, 2)
    for formation_id, distance in before.items():
        f = world.military_state["formations"][formation_id]
        assert f["location"] == "ra" and f["journey"]["distance_travelled_km"] == distance
    assert "relief-sea" not in world.military_state["transport_reservations"].get(world.date, {})


@pytest.mark.parametrize("corruption", ["ground_only", "missing_fleet", "unrelated_fleet", "duplicate", "wrong_origin", "wrong_mode"])
def test_audit_rejects_forged_saved_embarkation_identities(world, corruption):
    sea_reinforcement(world)
    operation = world.military_state["operations"]["sea-relief"]
    group = operation["embarkation"]["ra"]
    if corruption == "ground_only":
        group["fleets"] = []
    elif corruption == "missing_fleet":
        group["fleets"] = ["deleted-fleet"]
    elif corruption == "unrelated_fleet":
        naval(world, formation="other-fleet")
        group["fleets"] = ["other-fleet"]
    elif corruption == "duplicate":
        group["troops"].append("incoming")
    elif corruption == "wrong_origin":
        operation["embarkation"] = {"ra2": group}
    else:
        operation["transport_mode"] = None
    assert any("embark" in issue or "voyage" in issue for issue in ops.validation_issues(world))


def test_battle_reduced_ship_quantity_is_a_runtime_constraint_not_corrupt_embarkation(world):
    sea_reinforcement(world)
    ops.send_command(world, "sea-relief", "a", delay=0)
    advance(world, 2)
    world.military_state["formations"]["relief-fleet"]["equipment_state"]["ships"]["quantity"] = 0
    restored = World.from_dict(world.to_dict())
    assert not ops.validation_issues(restored)
    before = restored.military_state["formations"]["incoming"]["journey"]["distance_travelled_km"]
    advance(restored, 2)
    assert restored.military_state["formations"]["incoming"]["journey"]["distance_travelled_km"] == before


def test_received_sea_withdrawal_preserves_legal_shared_embarkation_history(world):
    sea_reinforcement(world)
    ops.send_command(world, "sea-relief", "a", delay=0)
    advance(world, 2)
    ops.send_command(world, "sea-relief", "a", command={"intent": "withdraw", "target": "ra"}, delay=0)
    advance(world, 10)
    assert ops.explain(world, "sea-relief")["kind"] == "withdraw"
    assert not ops.validation_issues(world)
    assert world.military_state["formations"]["incoming"]["location"] == "ra"


@pytest.mark.parametrize("corruption", ["ground_only", "missing_fleet", "unrelated_fleet"])
def test_actual_sqlite_snapshot_load_rejects_forged_embarkation(tmp_path, corruption):
    from test_crisis_integration import initial, government, rewrite
    from worldline import Engine
    from worldline.models import Army
    opening = initial()
    opening.armies.update({"replacement-army": Army("a", "ra", 10), "real-fleet": Army("a", "ra", 20),
        "other-fleet": Army("a", "ra", 20)})
    game = Engine.create(tmp_path / "original.sqlite", opening, seed=71)
    try:
        game.enable_economy()
        game.stabilize_economy()
        game.enable_npc(government())
        game.enable_crisis({"settings": {"auto_enabled": False}, "player_countries": ["a"]})
        game.enable_military({"initial_equipment": {key: {"ships": {"quantity": 1, "condition": 1., "troop_capacity": 100}}
            for key in ("real-fleet", "other-fleet")}})
        with game._transaction():
            for key in ("real-fleet", "other-fleet"):
                formation = game._world.military_state["formations"][key]
                formation.update(service_type="navy", mobility={"mode": "sea", "speed_km_day": 50, "motorized": False})
        game.plan_operation("real-sea-relief", "a", ["replacement-army", "real-fleet"], "reinforce", "ra", issuer="a",
            params={"recipient_formation": "army-a", "transport_mode": "sea"})
        snapshot = game.save(tmp_path / "valid.json")
        valid = Engine.load(snapshot, tmp_path / "valid.sqlite")
        valid.close()
        def corrupt(payload):
            operation = payload["engine"]["world"]["military_state"]["operations"]["real-sea-relief"]
            operation["embarkation"]["ra"]["fleets"] = [] if corruption == "ground_only" else ["deleted-fleet" if corruption == "missing_fleet" else "other-fleet"]
        broken = tmp_path / "invalid.json"
        rewrite(snapshot, broken, corrupt)  # Correct checksum cannot make nonexistent assets physically valid.
        with pytest.raises(ValueError, match="embark|voyage|military"):
            Engine.load(broken, tmp_path / "rejected.sqlite")
    finally:
        game.close()
