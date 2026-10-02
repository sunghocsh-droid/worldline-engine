"""Risk originates in modeled state, not an event quota or historical year."""
import copy
import json
from datetime import timedelta
import pytest

from worldline import crisis_state as cs, crisis_risk as risk
from worldline.models import canonical_date
from worldline.npc_benchmark import campaign_world


@pytest.fixture
def world():
    w = campaign_world()
    w.crisis_state = cs.initialize_state(root_seed=71)
    for region in w.regions:
        cs.configure_region(w, region)
    cs.configure_border(w, "ab", ["a", "b"], ["ra", "rb"], tension=5)
    return w


def create(w, kind, **kwargs):
    return cs.add_event(w, kind, **kwargs)


def weekly(w, weeks=1):
    events = []
    for _ in range(weeks):
        w.date = (canonical_date(w.date) + timedelta(days=7)).isoformat()
        risk.update_pressures(w)
        events.extend(risk.detect(w, create))
    return events


def vulnerable_food(w, security=0):
    w.economy["settlements"] = 1
    w.economy["regions"]["ra"]["demography"]["food_security"] = security
    return cs.add_event(w, "bad_harvest", "ra", ["a"], actual_facts={"crop_failure": True})


def test_continuous_hazard_probability_scales_with_elapsed_time():
    p_week = risk.probability(.4, 7)
    assert 0 < p_week < risk.probability(.4, 30) < risk.probability(.4, 365) < 1
    assert (1 - p_week) ** (365 / 7) == pytest.approx(1 - risk.probability(.4, 365))
    assert risk.probability(0, 36500) == 0


@pytest.mark.parametrize("rate,days", [(-1, 7), (float("nan"), 7), (1, -7)])
def test_invalid_hazard_parameters_rejected(rate, days):
    with pytest.raises(ValueError):
        risk.probability(rate, days)


def test_initial_zero_food_inventory_is_not_famine(world):
    world.crisis_state["borders"] = {}
    for _ in range(100):
        weekly(world)
    assert not any(e["event_type"] in {"famine", "food_shortage"} for e in world.crisis_state["events"].values())
    assert risk.food_state(world, "ra")["food_gap"] == 0


def test_all_seventeen_pressure_categories_have_slots(world):
    traces = risk.update_pressures(world)
    assert {p["category"] for p in world.crisis_state["pressures"].values()} == set(cs.CATEGORIES)
    assert all(0 <= t["pressure"] <= 100 and isinstance(t["causes"], list) for t in traces)


def test_actual_food_reserves_prevent_famine_without_changing_crop_event(world):
    harvest = vulnerable_food(world, .2)
    world.economy["regions"]["ra"]["stocks"]["goods"]["agriculture"] = 1000
    events = weekly(world, 120)
    assert not any(e["event_type"] in {"food_shortage", "famine"} for e in events)
    assert harvest["actual_facts"]["crop_failure"]
    assert risk.food_state(world, "ra")["actual_reserve_months"] > 1


def test_same_crop_failure_in_vulnerable_country_can_form_causal_famine(world):
    harvest = vulnerable_food(world, 0)
    events = weekly(world, 400)
    famine = next(e for e in events if e["event_type"] == "famine")
    parent = world.crisis_state["events"][famine["parent_event"]]
    assert parent["event_id"] == harvest["event_id"] or parent["parent_event"] == harvest["event_id"]
    assert famine["causes"][0]["actual_reserve_months"] == 0


def test_food_reserve_label_cannot_rescue_a_country_without_real_food(world):
    vulnerable_food(world, .1)
    world.crisis_state["regions"]["ra"]["food_reserve_months"] = 100
    assert risk.food_state(world, "ra")["food_gap"] == pytest.approx(.9)
    risk.update_pressures(world)
    assert cs.pressure_value(world, "famine", country="a", region="ra") == pytest.approx(90)


def test_food_imports_are_not_double_counted_with_inventory(world):
    vulnerable_food(world, .2)
    world.economy["trades"]["food"] = {"sector": "agriculture", "importer": "a", "exporter": "b", "imported": 100}
    food = risk.food_state(world, "ra")
    assert food["actual_food_imports"] == 100 and food["food_gap"] == pytest.approx(.8)
    world.economy["regions"]["ra"]["stocks"]["goods"]["agriculture"] = 100
    assert risk.food_state(world, "ra")["food_gap"] == 0


def test_relief_pressure_reduction_survives_next_update_and_risk_rebuilds(world):
    vulnerable_food(world, .1)
    risk.update_pressures(world)
    cs.adjust_pressure(world, "famine", -50, country="a", region="ra")
    reduced = cs.pressure_value(world, "famine", country="a", region="ra")
    risk.update_pressures(world)
    next_value = cs.pressure_value(world, "famine", country="a", region="ra")
    assert reduced < next_value < 90
    world.economy["regions"]["ra"]["stocks"]["goods"]["agriculture"] = 500
    risk.update_pressures(world)
    assert cs.pressure_value(world, "famine", country="a", region="ra") < next_value


def test_no_source_means_no_crisis_despite_centuries_of_clock(world):
    world.crisis_state["borders"] = {}
    world.relations = {}
    for c in world.countries.values():
        c.stability = 100
    assert weekly(world, 520) == []
    assert not world.crisis_state["events"]


def test_configured_disaster_can_occur_and_active_event_prevents_spam(world):
    world.crisis_state["regions"]["ra"]["hazard_rates"]["flood"] = 20
    events = weekly(world, 40)
    floods = [e for e in events if e["event_type"] == "flood"]
    assert len(floods) == 1
    assert floods[0]["causes"][0]["annual_rate"] == 20
    assert floods[0]["triggering_conditions"]["draw"] < floods[0]["triggering_conditions"]["occurrence_probability"]


def test_cooldown_blocks_duplicate_event_but_not_its_pressure(world):
    vulnerable_food(world)
    risk.update_pressures(world)
    world.crisis_state["cooldowns"]["food_shortage:ra"] = "1900-01-01"
    world.crisis_state["cooldowns"]["famine:ra"] = "1900-01-01"
    events = weekly(world, 52)
    assert not any(e["event_type"] in {"food_shortage", "famine"} for e in events)
    assert cs.pressure_value(world, "famine", country="a", region="ra") == 100


def test_low_border_pressure_never_directly_creates_war(world):
    world.relations = {}
    events = weekly(world, 520)
    assert not any(e["event_type"] in {"state_of_war", "armed_clash", "ultimatum", "full_mobilization"} for e in events)
    assert sum(e["event_type"] == "border_incident" for e in events) == 0


def test_high_tension_and_mobilization_make_an_incident_possible_not_automatic_war(world):
    world.crisis_state["borders"]["ab"]["tension"] = 95
    world.economy["regions"]["ra"]["labour_state"]["mobilized"] = 1500
    events = weekly(world, 200)
    incidents = [e for e in events if e["event_type"] == "border_incident"]
    assert len(incidents) == 1
    assert incidents[0]["actual_facts"]["military_outcomes_unresolved"] is True
    assert not any(e["event_type"] == "state_of_war" for e in events)


def test_authoritative_credit_default_and_arrears_create_financial_causes(world):
    credit = world.economy["countries"]["a"]["credit"]
    credit.update(distress_state="default", arrears=10000, creditworthiness=10, debt_service_ratio=1)
    events = weekly(world, 200)
    payment = next(e for e in events if e["event_type"] == "payment_failure")
    assert payment["actual_facts"]["arrears"] == 10000 and payment["actual_facts"]["default"]
    assert cs.pressure_value(world, "fiscal_crisis", country="a") > 70
    assert not any(e["event_type"] == "bank_run" for e in events)


def test_mobilization_and_underemployment_raise_labour_pressure(world):
    risk.update_pressures(world)
    before = cs.pressure_value(world, "labour_unrest", country="a", region="ra")
    r = world.economy["regions"]["ra"]
    r["labour_state"]["mobilized"] = 1000
    r["labour"].update(unemployed=1000, underemployed=500)
    risk.update_pressures(world)
    assert cs.pressure_value(world, "labour_unrest", country="a", region="ra") > before
    causes = world.crisis_state["pressures"][cs.pressure_key("labour_unrest", "a", "ra")]["causes"]
    assert {cause["factor"] for cause in causes} >= {"mobilization", "unemployment_underutilization"}


def test_group_grievances_have_separate_region_pressure(world):
    world.crisis_state["regions"]["ra"]["groups"] = {"language-minority": {"grievance": .85}, "local-elite": {"grievance": .2}}
    risk.update_pressures(world)
    assert world.crisis_state["pressures"]["ethnic_or_religious_tension:region:ra:group:language-minority"]["value"] == 85
    assert world.crisis_state["pressures"]["ethnic_or_religious_tension:region:ra:group:local-elite"]["value"] == 20


def test_legacy_pressure_is_retained_as_an_explainable_risk_source(world):
    world.pressures["a:revolution"] = {"country": "a", "risk": "revolution", "value": 85, "causes": ["persistent suppression"]}
    risk.update_pressures(world)
    assert cs.pressure_value(world, "revolution", country="a", region="ra") == 85


def test_same_seed_and_json_restored_state_produce_identical_next_events(world):
    world.crisis_state["regions"]["ra"]["hazard_rates"]["flood"] = .4
    world.crisis_state["regions"]["rb"]["hazard_rates"]["fire"] = .6
    weekly(world, 20)
    clone = copy.deepcopy(world)
    clone.crisis_state = json.loads(json.dumps(world.crisis_state))
    assert weekly(world, 100) == weekly(clone, 100)
    assert world.crisis_state == clone.crisis_state


def test_unrelated_region_hazard_does_not_change_other_regions_stream(world):
    world.crisis_state["borders"] = {}
    world.crisis_state["regions"]["ra"]["hazard_rates"]["flood"] = .4
    clone = copy.deepcopy(world)
    clone.crisis_state["regions"]["rd"]["hazard_rates"]["fire"] = 20
    a = [e for e in weekly(world, 100) if e.get("region") == "ra"]
    b = [e for e in weekly(clone, 100) if e.get("region") == "ra"]
    assert [{k: v for k, v in e.items() if k not in ("event_id",)} for e in a] == [{k: v for k, v in e.items() if k not in ("event_id",)} for e in b]


def test_local_existing_epidemic_creates_spread_pressure_and_sanitation_reduces_it(world):
    cs.add_event(world, "epidemic_introduction", "ra", ["a"], severity=.9)
    risk.update_pressures(world)
    before = cs.pressure_value(world, "epidemic", country="a", region="ra")
    assert before > 0
    world.regions["ra"].infrastructure["sanitation"] = 200
    world.economy["regions"]["ra"]["demography"]["health_level"] = .9
    risk.update_pressures(world)
    assert cs.pressure_value(world, "epidemic", country="a", region="ra") < before


def test_paid_relief_is_a_real_signal_but_an_unfunded_policy_is_not(world):
    vulnerable_food(world, .1)
    risk.update_pressures(world)
    before = cs.pressure_value(world, "civil_unrest", country="a", region="ra")
    world.economy["countries"]["a"]["policy"]["welfare"] = 100000
    risk.update_pressures(world)
    assert cs.pressure_value(world, "civil_unrest", country="a", region="ra") == before
    world.economy["countries"]["a"]["finance"]["paid_spending"] = {"welfare": 100}
    risk.update_pressures(world)
    assert cs.pressure_value(world, "civil_unrest", country="a", region="ra") < before
    assert world.economy["regions"]["ra"]["stocks"]["goods"]["agriculture"] == 0


def test_bad_local_intelligence_is_an_explicit_incident_risk_input(world):
    from worldline import npc_state
    actor = "a:military"
    world.npc_state["persons"][actor]["region"] = "ra"
    world.npc_state["persons"][actor]["authority"].append("local_action")
    npc_state.receive_report(world.npc_state, actor, "diplomacy", {"threat": .9, "enemy_strength": 25},
        world.date, "incorrect patrol intelligence", accuracy=.1, confidence=.9, reliability=.7)
    world.crisis_state["borders"]["ab"]["tension"] = 95
    events = weekly(world, 300)
    incident = next(e for e in events if e["event_type"] == "border_incident")
    effective = incident["actual_facts"]["local_threat_reports"]
    assert 0 < effective < .9
    assert any(c["factor"] == "local_observed_threat" and c["value"] == effective for c in incident["causes"])


def test_expired_harvest_is_not_parent_of_an_unrelated_later_shortage(world):
    old = cs.add_event(world, "bad_harvest", "ra", ["a"])
    cs.close_event(world, old["event_id"])
    world.date = "1907-01-01"
    world.economy["settlements"] = 120
    world.economy["regions"]["ra"]["demography"]["food_security"] = 0
    events = weekly(world, 200)
    shortage = next(e for e in events if e["event_type"] == "food_shortage")
    assert shortage["parent_event"] is None
    famine = next(e for e in events if e["event_type"] == "famine")
    assert famine["parent_event"] == shortage["event_id"]


def test_recent_ended_harvest_can_have_residual_causal_effects(world):
    old = cs.add_event(world, "bad_harvest", "ra", ["a"])
    cs.close_event(world, old["event_id"])
    world.date = "1897-02-01"
    assert risk._parent(world, {"bad_harvest"}, region="ra") == old["event_id"]
    world.date = "1897-05-01"
    assert risk._parent(world, {"bad_harvest"}, region="ra") is None


def test_country_already_at_war_does_not_spawn_new_peacetime_border_incidents(world):
    world.crisis_state["borders"]["ab"]["tension"] = 100
    world.relations["a-b"]["status"] = "war"
    events = weekly(world, 300)
    assert not any(e["event_type"] == "border_incident" for e in events)
