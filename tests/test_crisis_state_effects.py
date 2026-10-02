"""Causal history, saved hazard streams and exactly-once consequence accounting."""
import copy
import json
import random
import pytest

from worldline import economy
from worldline.npc_benchmark import campaign_world
from worldline import crisis_state as cs, crisis_effects as ce


@pytest.fixture
def world():
    w = campaign_world()
    w.crisis_state = cs.initialize_state(root_seed=71)
    cs.configure_region(w, "ra", {"flood": .1}, food_reserve_months=2)
    cs.configure_border(w, "ab", ["a", "b"], ["ra", "rb"], tension=20)
    return w


def event(w, kind="flood", **kwargs):
    return cs.add_event(w, kind, "ra", ["a"], actual_facts={"rainfall": "extreme"}, **kwargs)


def test_crisis_state_is_explicit_json_and_contains_no_historical_calendar(world):
    assert json.loads(json.dumps(world.crisis_state)) == world.crisis_state
    assert cs.validate(world) == []
    assert all(v == 0 for k, v in world.crisis_state["regions"]["ra"]["hazard_rates"].items() if k != "flood")
    assert len(cs.CATEGORIES) == 17


def test_pressure_is_not_an_event_and_does_not_clamp_risk_away(world):
    cs.set_pressure(world, "border_tension", 99, pair=["a", "b"])
    assert not world.crisis_state["events"]
    assert cs.pressure_value(world, "border_tension", pair=["b", "a"]) == 99
    cs.adjust_pressure(world, "border_tension", -7, pair=["b", "a"])
    assert cs.pressure_value(world, "border_tension", pair=["a", "b"]) == 92


@pytest.mark.parametrize("value", [-1, 101, float("nan"), True])
def test_invalid_pressure_values_rejected(world, value):
    with pytest.raises(ValueError):
        cs.set_pressure(world, "civil_unrest", value, region="ra")


def test_causal_chain_has_bidirectional_history(world):
    harvest = event(world, "bad_harvest")
    famine = event(world, "famine", parent_event=harvest["event_id"])
    protest = event(world, "protest", parent_event=famine["event_id"])
    assert harvest["child_events"] == [famine["event_id"]]
    assert protest["parent_event"] == famine["event_id"]
    assert cs.validate(world) == []


def test_causal_cycle_rejected(world):
    a = event(world)
    b = event(world, "food_shortage", parent_event=a["event_id"])
    a["parent_event"] = b["event_id"]
    b["child_events"].append(a["event_id"])
    assert any("cycle" in issue for issue in cs.validate(world))


@pytest.mark.parametrize("bad", [{"region": "missing"}, {"involved_actors": ["ghost"]}, {"severity": 2}])
def test_event_requires_real_location_actor_and_bounded_severity(world, bad):
    args = {"region": "ra", "countries": ["a"], **bad}
    with pytest.raises(ValueError):
        cs.add_event(world, "flood", **args)


def test_end_before_start_is_rejected_and_close_is_idempotent(world):
    e = event(world)
    with pytest.raises(ValueError):
        cs.close_event(world, e["event_id"], end_date="1896-12-31")
    cs.close_event(world, e["event_id"])
    count = len(world.crisis_state["outbox"])
    cs.close_event(world, e["event_id"])
    assert len(world.crisis_state["outbox"]) == count
    world.date = "1897-01-02"
    with pytest.raises(ValueError):
        cs.close_event(world, e["event_id"])


def test_creation_facts_are_immutable_against_previous_truth(world):
    e = event(world)
    previous = copy.deepcopy(world)
    e["actual_facts"]["rainfall"] = "ordinary"
    assert any("rewritten" in issue for issue in cs.validate(world, previous))


def test_active_event_or_receipt_cannot_be_deleted(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "pressure_delta", {"category": "famine", "delta": 10, "region": "ra"})
    ce.apply_due(world)
    previous = copy.deepcopy(world)
    world.crisis_state["events"].clear()
    assert any("removed" in issue for issue in cs.validate(world, previous))


def test_named_random_stream_does_not_shift_due_to_unrelated_risks(world):
    second = copy.deepcopy(world)
    with cs.stream(world, "border:ab") as rng:
        a = rng.random()
    with cs.stream(second, "unrelated:flood:rd") as rng:
        for _ in range(100):
            rng.random()
    with cs.stream(second, "border:ab") as rng:
        b = rng.random()
    assert a == b
    with cs.stream(world, "border:ab") as rng:
        c = rng.random()
    with cs.stream(second, "border:ab") as rng:
        d = rng.random()
    assert c == d


def test_json_saved_rng_next_draw_and_active_effect_are_identical(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 4}, "1897-01-07")
    with cs.stream(world, "region:ra") as rng:
        rng.random()
    clone = copy.deepcopy(world)
    clone.crisis_state = json.loads(json.dumps(world.crisis_state))
    with cs.stream(world, "region:ra") as rng:
        expected = rng.random()
    with cs.stream(clone, "region:ra") as rng:
        assert rng.random() == expected
    assert clone.crisis_state == world.crisis_state


def test_random_failure_does_not_advance_persisted_stream(world):
    with pytest.raises(RuntimeError):
        with cs.stream(world, "failed") as rng:
            rng.random()
            raise RuntimeError("transaction failed")
    assert "failed" not in world.crisis_state["streams"]


def test_invalid_saved_rng_is_explicit_error(world):
    world.crisis_state["streams"]["invalid"] = [3, [1], None]
    assert any("random stream" in issue for issue in cs.validate(world))


def test_population_effect_is_pending_then_accounted_monthly_once(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 100})
    receipt = ce.apply_due(world)[0]["receipt"]
    assert world.regions["ra"].population == 10000
    assert receipt["status"] == "delivered_to_monthly_demography"
    assert world.economy["regions"]["ra"]["pending_losses"]["disaster_losses"] == 100
    assert ce.apply_due(world) == []
    world.date = "1897-02-01"
    economy.tick(world, {}, random.Random(0))
    account = world.economy["regions"]["ra"]["demography"]["last_account"]
    assert account["disaster_losses"] == 100
    assert world.regions["ra"].population == account["before"] + account["births"] - account["deaths"] - 100
    assert cs.validate(world) == []


def test_duplicate_submission_does_not_destroy_capital_twice(world):
    e = event(world)
    p = {"kind": "capital_destruction", "region": "ra", "severity": .5, "duration_days": 30}
    first = ce.enqueue(world, e["event_id"], "economic_shock", p)
    second = ce.enqueue(world, e["event_id"], "economic_shock", copy.deepcopy(p))
    assert first is second and len(world.crisis_state["effects"]) == 1
    before = world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"]
    ce.apply_due(world)
    assert first["receipt"]["status"] == "delivered_to_monthly_economy"
    assert world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"] == before
    world.date = "1897-02-01"
    economy.shocks_for_period(world, "1897-01-01", world.date)
    after = world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"]
    assert after == pytest.approx(before * .65)
    assert ce.apply_due(world) == []
    economy.shocks_for_period(world, "1897-01-01", world.date)
    assert world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"] == after


def test_applied_receipt_cannot_be_reset_to_damage_again(world):
    e = event(world)
    effect = ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 3})
    ce.apply_due(world)
    previous = copy.deepcopy(world)
    effect.update(applied=False, receipt=None, applied_at=None)
    assert any("receipt rewritten" in issue for issue in cs.validate(world, previous))


def test_resolved_event_cannot_add_new_damage_but_prior_delay_survives(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 3}, "1897-01-07")
    cs.close_event(world, e["event_id"])
    with pytest.raises(ValueError):
        ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 5})
    world.date = "1897-01-07"
    assert ce.apply_due(world)[0]["receipt"]["count"] == 3


def test_timely_mitigation_reduces_future_damage(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "mitigation", {"event_id": e["event_id"], "strength": .6, "response": "relief"})
    ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 100}, "1897-01-05")
    ce.apply_due(world)
    world.date = "1897-01-05"
    assert ce.apply_due(world)[0]["receipt"]["count"] == 40


def test_population_loss_cannot_exceed_remaining_people(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 10001})
    with pytest.raises(ValueError, match="remaining population"):
        ce.apply_due(world)
    assert world.economy["regions"]["ra"]["pending_losses"]["disaster_losses"] == 0


def test_infrastructure_damage_changes_existing_stock_and_no_gdp_setter(world):
    e = event(world)
    before = world.regions["ra"].infrastructure["roads"]
    gdp = world.countries["a"].gdp
    ce.enqueue(world, e["event_id"], "infrastructure_damage", {"region": "ra", "fraction": .2, "target": "roads"})
    ce.apply_due(world)
    assert world.regions["ra"].infrastructure["roads"] == pytest.approx(before * .8)
    assert world.countries["a"].gdp == gdp


def test_unfunded_payment_creates_no_money_and_emits_failure_trigger(world):
    e = event(world)
    before_cash, before_debt = world.countries["a"].treasury, world.countries["a"].debt
    ce.enqueue(world, e["event_id"], "funded_payment", {"country": "a", "amount": 10**12})
    result = ce.apply_due(world)[0]["receipt"]
    assert result["status"] == "unfunded"
    assert world.countries["a"].treasury == before_cash and world.countries["a"].debt == before_debt
    assert world.crisis_state["triggers"][0]["kind"] == "payment_failure"


def test_funded_payment_books_cash_reconciliation(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "funded_payment", {"country": "a", "amount": 100})
    ce.apply_due(world)
    assert world.countries["a"].treasury == 900
    assert world.economy["countries"]["a"]["finance"]["interim_net_cashflow"] == -100


def test_diplomacy_effect_cannot_fabricate_treasury(world):
    e = event(world)
    with pytest.raises(ValueError, match="cannot create resources"):
        ce.enqueue(world, e["event_id"], "diplomacy", {"a": "a", "b": "b", "treasury": 9999})


def test_diplomacy_contact_and_pressure_relief_do_not_erase_memories(world):
    e = event(world, "diplomatic_crisis")
    memory = copy.deepcopy(world.npc_state["memories"])
    ce.enqueue(world, e["event_id"], "diplomacy", {"a": "a", "b": "b", "trust_delta": .1, "contact": "mediation"})
    ce.enqueue(world, e["event_id"], "pressure_delta", {"category": "border_tension", "delta": -5, "pair": ["a", "b"]})
    ce.apply_due(world)
    assert cs.pressure_value(world, "border_tension", pair=["a", "b"]) == 15
    assert world.npc_state["memories"] == memory


def test_delayed_trigger_preserves_causal_parent(world):
    e = event(world, "food_shortage")
    ce.enqueue(world, e["event_id"], "trigger", {"event_type": "protest", "parent_event": e["event_id"], "region": "ra"}, "1897-01-03")
    assert ce.apply_due(world) == []
    world.date = "1897-01-03"
    ce.apply_due(world)
    assert world.crisis_state["triggers"][0]["payload"]["parent_event"] == e["event_id"]


def test_food_reserve_metadata_does_not_create_actual_food(world):
    stocks = world.economy["regions"]["ra"]["stocks"]["goods"]
    assert world.crisis_state["regions"]["ra"]["food_reserve_months"] == 2
    assert stocks["agriculture"] == 0
    e = event(world)
    ce.enqueue(world, e["event_id"], "food_stock_loss", {"region": "ra", "fraction": .3})
    receipt = ce.apply_due(world)[0]["receipt"]
    assert receipt["opening"] == receipt["destroyed"] == receipt["closing"] == 0


def test_food_inventory_damage_is_real_conserved_and_applied_once(world):
    stocks = world.economy["regions"]["ra"]["stocks"]["goods"]
    stocks["agriculture"] = 100
    e = event(world)
    ce.enqueue(world, e["event_id"], "food_stock_loss", {"region": "ra", "fraction": .3})
    receipt = ce.apply_due(world)[0]["receipt"]
    assert receipt["opening"] == receipt["destroyed"] + receipt["closing"]
    assert stocks["agriculture"] == 70
    assert ce.apply_due(world) == []
    assert stocks["agriculture"] == 70


def test_local_event_rng_key_not_shifted_by_unrelated_event(world):
    clone = copy.deepcopy(world)
    a = event(world)
    cs.add_event(clone, "protest", "rb", ["b"])
    b = event(clone)
    assert a["event_id"] != b["event_id"]
    assert a["rng_key"] == b["rng_key"]


def test_event_id_counter_cannot_overwrite_or_roll_back_history(world):
    event(world)
    previous = copy.deepcopy(world)
    world.crisis_state["next_ids"]["event"] = 1
    with pytest.raises(ValueError, match="overwrite history"):
        event(world)
    errors = cs.validate(world, previous)
    assert any("identifier sequence" in issue for issue in errors)


def test_conflicting_effect_idempotency_key_is_rejected(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 1}, idempotency_key="damage")
    with pytest.raises(ValueError, match="conflicts"):
        ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 2}, idempotency_key="damage")


@pytest.mark.parametrize("mutation", [
    lambda s: s["events"].update({"broken": None}),
    lambda s: s["events"]["crisis-event-1"].update(countries=None),
    lambda s: s["events"]["crisis-event-1"].update(child_events=[{}]),
    lambda s: s["events"]["crisis-event-1"].update(parent_event=[]),
    lambda s: s["pressures"].update({"bad": {"category": "famine", "value": float("nan")}}),
    lambda s: s["next_ids"].update(event=None),
])
def test_malformed_saves_are_audit_errors_instead_of_runtime_crashes(world, mutation):
    event(world)
    mutation(world.crisis_state)
    assert cs.validate(world)


def test_unknown_event_type_rejected_with_explicit_extension_namespace(world):
    with pytest.raises(ValueError):
        event(world, "historical_world_war_1914")
    assert event(world, "custom:future_military_supply")['event_type'] == "custom:future_military_supply"


def test_crisis_owns_one_off_damage_without_duplicate_economy_mortality(world):
    e = event(world)
    ce.enqueue(world, e["event_id"], "economic_shock", {"kind": "flood", "region": "ra", "severity": .5, "one_off_effects": False})
    ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 30})
    ce.enqueue(world, e["event_id"], "economic_shock", {"kind": "capital_destruction", "region": "ra", "severity": 1 / 7})
    before = world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"]
    results = ce.apply_due(world)
    after = world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"]
    assert after == before
    assert results[0]["receipt"]["one_off_owner"] == "crisis_pipeline"
    world.date = "1897-02-01"
    economy.shocks_for_period(world, "1897-01-01", world.date)
    assert world.economy["regions"]["ra"]["sectors"]["machinery"]["capital_stock"] == pytest.approx(before * .9)
    assert world.economy["regions"]["ra"]["pending_losses"]["disaster_losses"] == 30
    assert world.economy["regions"]["ra"]["shock_factors"]["disruption"] < 1


def test_one_off_effect_owner_flag_requires_boolean(world):
    e = event(world)
    with pytest.raises(ValueError, match="boolean"):
        ce.enqueue(world, e["event_id"], "economic_shock", {"kind": "flood", "region": "ra", "severity": .5, "one_off_effects": "false"})


def test_delivered_shock_marker_cannot_be_reset_to_duplicate_crisis_deaths(world):
    e = event(world)
    effect = ce.enqueue(world, e["event_id"], "economic_shock", {"kind": "epidemic", "region": "ra", "severity": .5, "one_off_effects": False})
    ce.apply_due(world)
    previous = copy.deepcopy(world)
    world.economy["shocks"][effect["receipt"]["shock_id"]]["applied"] = False
    issues = cs.validate(world, previous)
    assert any("duplicate" in issue for issue in issues)


def test_delivered_shock_severity_cannot_exceed_its_saved_receipt(world):
    e = event(world)
    effect = ce.enqueue(world, e["event_id"], "economic_shock", {"kind": "flood", "region": "ra", "severity": .3})
    ce.apply_due(world)
    world.economy["shocks"][effect["receipt"]["shock_id"]]["severity"] = .9
    assert any("conflict with receipt" in issue for issue in cs.validate(world))


@pytest.mark.parametrize("field,value", [
    ("schedules", {"risk_next_due": "zzz"}),
    ("schedules", {"risk_last_review": "1900-01-01"}),
    ("cooldowns", {"famine:ra": "not-a-date"}),
    ("risk_components", {"missing-pressure": {"target": 5, "date": "1897-01-01", "causes": []}}),
    ("settings", {"auto_enabled": "yes"}),
])
def test_malformed_scheduler_metadata_is_rejected_before_next_tick(world, field, value):
    world.crisis_state[field] = value
    assert cs.validate(world)


def test_malformed_structural_pressure_target_cannot_crash_the_next_update(world):
    from worldline import crisis_risk
    crisis_risk.update_pressures(world)
    key = next(iter(world.crisis_state["risk_components"]))
    world.crisis_state["risk_components"][key]["target"] = "bad"
    assert any("structural risk component" in issue for issue in cs.validate(world))


def measured(world, settle=True):
    from worldline import npc_state
    e = event(world)
    e["responses"].append({"country": "a", "response": "ignore", "date": world.date})
    loss = ce.enqueue(world, e["event_id"], "population_loss", {"region": "ra", "count": 100})
    ce.apply_due(world)
    if settle:
        world.date = "1897-02-01"
        economy.tick(world, {}, random.Random(0))
    account = copy.deepcopy(world.population_accounts.get("a", {}))
    facts = {"event_id": e["event_id"], "measured_aftermath": True, "performance": -.1,
        "realized_population_loss": 100, "prevented_population_loss": 0, "unmitigated_loss_plan": 100,
        "loss_effects": [loss["effect_id"]], "population_account": account}
    npc_state.record_fact(world.npc_state, "measured-test", world.date, "a:ruler", "disaster_response", facts, "monthly observed aftermath")
    e["measured_response_outcomes"] = {"a": {"record_id": "measured-test", "date": world.date, "facts": facts}}
    return e


def test_measured_population_damage_requires_actual_monthly_accounting(world):
    measured(world)
    assert cs.validate(world) == []
    unsettled = campaign_world()
    unsettled.crisis_state = cs.initialize_state(root_seed=71)
    measured(unsettled, settle=False)
    assert any("realized monthly population account" in issue for issue in cs.validate(unsettled))


def test_measured_outcome_marker_is_immutable_and_cannot_be_removed_to_repeat_evaluation(world):
    e = measured(world)
    previous = copy.deepcopy(world)
    e["measured_response_outcomes"].clear()
    assert any("measured response outcome removed/rewritten" in issue for issue in cs.validate(world, previous))


def test_measured_outcome_must_match_permanent_npc_evidence(world):
    e = measured(world)
    e["measured_response_outcomes"]["a"]["facts"]["realized_population_loss"] = 300
    assert any("permanent NPC evidence" in issue for issue in cs.validate(world))


def test_queued_loss_receipt_cannot_be_presented_as_realized_monthly_deaths(world):
    e = measured(world)
    effect_id = e["measured_response_outcomes"]["a"]["facts"]["loss_effects"][0]
    world.crisis_state["effects"][effect_id].update(applied=False, applied_at=None, receipt=None)
    assert any("pending/nonlocal losses as realized" in issue for issue in cs.validate(world))


@pytest.mark.parametrize("field,value", [("next_review", "zzz"), ("expected_end", "bad"), ("last_review", "1900-01-01"), ("deadline", "1896-12-01")])
def test_lifecycle_dates_cannot_freeze_active_crisis_silently(world, field, value):
    e = event(world)
    e[field] = value
    assert any("lifecycle" in issue for issue in cs.validate(world))


@pytest.mark.parametrize("field,value", [("bad_harvest_rate", -1), ("area_km2", "unknown"), ("groups", {"minority": {"grievance": 300}})])
def test_region_risk_metadata_has_explicit_numeric_units(world, field, value):
    world.crisis_state["regions"]["ra"][field] = value
    assert cs.validate(world)


def test_corrupted_border_tension_cannot_silently_remove_the_risk(world):
    world.crisis_state["borders"]["ab"]["tension"] = "unknown"
    assert any("border tension" in issue for issue in cs.validate(world))


def test_language_restriction_event_has_a_real_identity_pressure_hook(world):
    from worldline.crisis import create_event
    event = create_event(world, "language_restriction", "ra", ["a"], severity=.8,
        actual_facts={"group": "local-language-community", "restriction": "school language"})
    ce.apply_due(world)
    assert event["event_type"] == "language_restriction"
    assert cs.pressure_value(world, "ethnic_or_religious_tension", region="ra") == 12
    assert cs.pressure_value(world, "separatism", region="ra") > 0
