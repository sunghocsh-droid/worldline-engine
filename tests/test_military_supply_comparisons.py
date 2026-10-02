"""Paired actual supply paths; no full-world or historical calibration claim."""
import copy

import pytest

from test_military_operations import world, naval
from worldline import military, military_state as ms, military_bridge as bridge
from worldline import military_operations as ops, military_logistics as log
from worldline import military_combat as combat


def _day(game, *, cargo=False, upkeep=False):
    previous = game.date
    game.date = ms.after(previous, 1)
    if cargo:
        log.tick(game)
    ops.tick(game, {"previous_date": previous, "date": game.date, "days": 1})
    if upkeep:
        military._upkeep(game)


def test_two_months_actual_rations_change_survival_readiness_and_operation_capacity(world):
    """Same army/seed/order; only finite opening food differs, never refilled."""
    adequate, deprived = copy.deepcopy(world), copy.deepcopy(world)
    for game, food in ((adequate, 20.), (deprived, .01)):
        force = game.military_state["formations"]["a-force"]
        force["supply_state"]["stocks"] = {"food": food, "medical": 2.}
        ops.plan(game, "hold", "a", ["a-force"], "hold", issuer="a")
        ops.send_command(game, "hold", "a", delay=0)
        for _ in range(60):
            _day(game, upkeep=True)
    good, bad = (game.military_state["formations"]["a-force"] for game in (adequate, deprived))
    good_capacity, bad_capacity = (combat.capability(game, "a-force") for game in (adequate, deprived))
    assert good["supply_state"]["last_ratio"] == 1 and bad["supply_state"]["last_ratio"] == 0
    assert good["readiness"] > bad["readiness"]
    assert good["manpower"] > bad["manpower"] > 0
    assert good_capacity["effective_capacity"] > bad_capacity["effective_capacity"]
    assert good_capacity["supply_ratio"] > bad_capacity["supply_ratio"]
    hunger = sum(receipt["result"]["casualties"].get("a-force", {}).get("hunger", 0)
        for receipt in deprived.military_state["loss_receipts"].values())
    assert hunger > 0
    for game, opening in ((adequate, 20.), (deprived, .01)):
        force = game.military_state["formations"]["a-force"]
        consumed = sum(receipt["actual"].get("food", 0) for receipt in
            game.military_state["logistics_receipts"].values()
            if receipt["kind"] == "supply_consumption" and receipt["formation"] == "a-force")
        assert force["supply_state"]["stocks"]["food"] + consumed == pytest.approx(opening)


def test_larger_force_cannot_cross_same_finite_route_as_fast_as_small_force(world):
    small, large = copy.deepcopy(world), copy.deepcopy(world)
    bridge.demobilize(small, "a-force", 900)
    for game in (small, large):
        game.military_state["links"]["a-road"]["throughput"] = 200
        ops.plan(game, "advance", "a", ["a-force"], "move", "ra2", issuer="a")
        ops.send_command(game, "advance", "a", delay=0)
        for _ in range(8):
            _day(game)
    assert small.military_state["operations"]["advance"]["status"] == "completed"
    assert large.military_state["formations"]["a-force"]["location"] == "ra"
    assert large.military_state["operations"]["advance"]["status"] == "in_progress"
    progress = large.military_state["formations"]["a-force"]["journey"]["distance_travelled_km"]
    assert 0 < progress < 50
    small_receipts = [r for day in small.military_state["transport_reservations"].values()
        for link in day.values() for r in link["reservations"].values()]
    large_receipts = [r for day in large.military_state["transport_reservations"].values()
        for link in day.values() for r in link["reservations"].values()]
    assert all(r["ratio"] == 1 for r in small_receipts)
    assert all(0 < r["ratio"] < 1 for r in large_receipts)
    for receipt in small_receipts + large_receipts:
        assert receipt["reserved"] <= receipt["capacity_at_booking"]


def _expedition_start(world):
    """Explicit finite ships/stores; front army first makes a real road march."""
    world.regions["rb"].infrastructure["ports"] = 50
    naval(world, actor="a", formation="transport", ships=5)
    naval(world, actor="b", formation="blockading-fleet", region="rb", ships=80)
    ops.plan(world, "front-deployment", "a", ["a-force"], "move", "ra2", issuer="a")
    ops.send_command(world, "front-deployment", "a", delay=0)
    for _ in range(8):
        _day(world)
    assert world.military_state["formations"]["a-force"]["location"] == "ra2"
    log.add_link(world, "hostile-approach", "rb", "ra2", mode="sea", throughput=20000,
        travel_days=1, distance_km=10)
    log.add_link(world, "sea-supply", "ra", "ra2", actor="a", mode="sea", throughput=8,
        travel_days=1, distance_km=50)
    world.military_state["actors"]["a"]["stocks"]["food"] = 4.  # Finite scenario opening depot stock.
    log.add_depot(world, "home", "a", "ra", stocks={"food": 4.})
    log.add_convoy(world, "cargo", "a", ["transport"], capacity=10)
    return world


def test_actual_naval_blockade_delays_cargo_and_starves_the_land_front(world):
    open_route, blocked = copy.deepcopy(_expedition_start(world)), copy.deepcopy(world)
    for game, kind, target in ((open_route, "hold", None), (blocked, "blockade", "ra2")):
        ops.plan(game, "naval-intent", "b", ["blockading-fleet"], kind, target,
            issuer="b", params={"opponent": "a"})
        ops.send_command(game, "naval-intent", "b", delay=0)
        for _ in range(4):
            _day(game)
        force = game.military_state["formations"]["a-force"]
        force["supply_state"]["stocks"] = {"food": force["manpower"] * log.FOOD_PER_PERSON_DAY,
            "medical": 1.}
        ops.plan(game, "front-hold", "a", ["a-force"], "hold", issuer="a")
        ops.send_command(game, "front-hold", "a", delay=0)
        log.dispatch(game, "real-cargo", "home", "a-force", {"food": 4.},
            destination_kind="formation", mode="sea", convoy="cargo")
    assert ops.blockade_coverage(open_route, "sea-supply", "a")["disruption"] == 0
    assert ops.blockade_coverage(blocked, "sea-supply", "a")["disruption"] > .5
    assert log.edge_capacity(blocked, "sea-supply", "a")["capacity"] < log.edge_capacity(open_route, "sea-supply", "a")["capacity"]
    for _ in range(3):
        for game in (open_route, blocked):
            _day(game, cargo=True, upkeep=True)
    assert open_route.military_state["shipments"]["real-cargo"]["status"] == "delivered"
    assert blocked.military_state["shipments"]["real-cargo"]["status"] == "in_transit"
    good, bad = (game.military_state["formations"]["a-force"] for game in (open_route, blocked))
    assert good["supply_state"]["last_ratio"] == 1 and bad["supply_state"]["last_ratio"] == 0
    assert good["readiness"] > bad["readiness"]
    assert combat.capability(open_route, "a-force")["effective_capacity"] > combat.capability(blocked, "a-force")["effective_capacity"]
    assert blocked.military_state["shipments"]["real-cargo"]["cargo"]["food"] == 4
    assert blocked.military_state["depots"]["home"]["stocks"]["food"] == 0
    assert log.validate(open_route) == log.validate(blocked) == []
