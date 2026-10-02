"""Causal lifecycle, real consequences, uncertainty and opt-in regression tests."""
import copy
import random
from datetime import timedelta

import pytest

from worldline import crisis, crisis_effects as ce, crisis_state as cs, crisis_response as cr, economy
from worldline.audit import Auditor
from worldline.models import World, canonical_date
from worldline.npc_benchmark import campaign_world


def world(*, auto=False, players=(), seed=31, borders=None):
    w = campaign_world()
    for g in w.npc_state["governments"].values():
        g["autonomous"] = False
    crisis.initialize(w, {"settings": {"auto_enabled": auto}, "player_countries": list(players), "borders": borders or {}}, rng=random.Random(seed))
    return w


def advance(w, days):
    rng = random.Random(3)
    result = []
    for _ in range(days):
        before = w.date
        w.date = (canonical_date(w.date) + timedelta(days=1)).isoformat()
        result.extend(crisis.tick(w, {"previous_date": before, "date": w.date, "days": 1}, rng))
    return result


def test_resolved_child_advances_its_escalated_parents_expired_cooldown_without_erasing_pressure():
    w = world()
    family = "social:ra"
    parent = crisis.create_event(w, "protest", "ra", ["a"], severity=.8, risk_family_key=family)
    child = crisis._continuation(w, parent, "riot")
    assert parent["resolution_state"] == "escalated"
    assert w.crisis_state["cooldowns"][family] == "1897-01-01"
    w.date = "1897-01-10"
    cr.queue_report(w, child["event_id"], "a:ruler", delay=0)
    cr.deliver_due(w)
    assert cr.respond(w, child["event_id"], "a", "negotiate", actor="a:ruler")["status"] == "accepted"
    ce.apply_due(w)
    assert not child["ongoing"] and child["resolution_state"] == "resolved"
    remaining = copy.deepcopy(w.crisis_state["pressures"])
    crisis._review_events(w)
    assert w.crisis_state["cooldowns"][family] == "1897-04-10"
    assert w.crisis_state["pressures"] == remaining
    crisis._review_events(w)
    assert w.crisis_state["cooldowns"][family] == "1897-04-10"


def test_new_resolution_advances_older_family_cooldown_and_cannot_shorten_it():
    w = world()
    family = "social:ra"
    older = crisis.create_event(w, "petition", "ra", ["a"], risk_family_key=family)
    cs.close_event(w, older["event_id"], "resolved")
    crisis._review_events(w)
    assert w.crisis_state["cooldowns"][family] == "1897-04-01"
    w.date = "1897-05-01"
    newer = crisis.create_event(w, "protest", "ra", ["a"], risk_family_key=family)
    cs.close_event(w, newer["event_id"], "resolved")
    remaining = copy.deepcopy(w.crisis_state["pressures"])
    crisis._review_events(w)
    assert w.crisis_state["cooldowns"][family] == "1897-07-30"
    assert w.crisis_state["pressures"] == remaining
    # A later short containment or an escalated parent cannot roll back the
    # already established protection against repeating the same cause.
    other = crisis.create_event(w, "petition", "ra", ["a"], risk_family_key=family)
    crisis._close(w, other, "escalated", cooldown_days=0)
    crisis._review_events(w)
    assert w.crisis_state["cooldowns"][family] == "1897-07-30"


@pytest.mark.parametrize("kind,days", [("epidemic_introduction", 60), ("border_incident", 180)])
def test_helper_specific_cooldown_duration_is_preserved_without_extending_on_review(kind, days):
    w = world()
    family = kind + ":ra"
    e = crisis.create_event(w, kind, "ra", ["a"], risk_family_key=family)
    crisis._close(w, e, "contained", cooldown_days=days)
    expected = (canonical_date(e["end_date"]) + timedelta(days=days)).isoformat()
    assert e["cooldown_until"] == expected
    crisis._review_events(w)
    assert w.crisis_state["cooldowns"][family] == expected
    w.date = "1897-01-10"
    crisis._review_events(w)
    assert e["cooldown_until"] == w.crisis_state["cooldowns"][family] == expected
    assert not cr.validation_issues(w)
    # The optional saved deadline is a dated resolution fact, not a free-form
    # scheduler value that may precede the event's actual ending.
    e["cooldown_until"] = "1896-12-31"
    assert any("resolution cooldown" in issue for issue in cr.validation_issues(w))


def make(w, kind="flood", **extra):
    return crisis.create_event(w, kind, "ra", ["a"], severity=.8, **extra)


def test_activation_uses_one_saved_rng_draw_and_preserves_previous_rules():
    w = campaign_world()
    before_economy = copy.deepcopy(w.economy)
    rng, expected = random.Random(2), random.Random(2)
    seed = expected.getrandbits(128)
    crisis.initialize(w, rng=rng)
    assert w.crisis_state["root_seed"] == seed and rng.getstate() == expected.getstate()
    assert w.economy == before_economy
    assert w.schema_version == 4
    assert not w.crisis_state["events"]
    Auditor().check(w)


def test_initialization_is_explicit_and_rejects_unsupported_versions():
    w = campaign_world()
    with pytest.raises(ValueError, match="saved game RNG"):
        crisis.initialize(w)
    w.economy["version"] = 2
    with pytest.raises(ValueError, match="Economy v3"):
        crisis.initialize(w, rng=random.Random(1))
    assert w.crisis_state is None


def test_bad_config_does_not_mutate_world_or_advance_rng():
    w = campaign_world()
    before, rng = w.to_dict(), random.Random(1)
    old_rng = rng.getstate()
    with pytest.raises(ValueError, match="Unknown"):
        crisis.initialize(w, {"regions": {"nonexistent": {}}}, rng=rng)
    assert w.to_dict() == before and rng.getstate() == old_rng


def test_player_control_disables_only_the_players_ordinary_autonomous_government():
    w = campaign_world()
    crisis.initialize(w, {"player_countries": ["a"]}, rng=random.Random(1))
    assert not w.npc_state["governments"]["a"]["autonomous"]
    assert w.npc_state["governments"]["b"]["autonomous"]


def test_unobserved_player_crisis_does_not_leak_through_pending_choices():
    w = world(players=("a",))
    e = make(w, "ultimatum", actual_facts={"classified": True})
    assert not w.crisis_state["pending_decisions"]
    advance(w, 1)
    assert not w.crisis_state["pending_decisions"]
    assert not [r for r in e["responses"] if r["country"] == "a"]
    advance(w, 7)
    assert len(w.crisis_state["pending_decisions"]) == 1
    p = next(iter(w.crisis_state["pending_decisions"].values()))
    assert p["created_at"] >= "1897-01-03" and p["status"] == "pending"


def test_occurrence_queues_consequences_and_ledger_outbox_exactly_once():
    w = world()
    e = make(w)
    first = crisis.drain_outbox(w)
    assert len([x for x in first if x["kind"] == "event_started"]) == 1
    assert first[0]["event_id"] == e["event_id"]
    assert not crisis.drain_outbox(w)
    assert e["immediate_consequences"] and e["delayed_consequences"]
    assert not any(r["facts"].get("event_id") == e["event_id"] for r in w.npc_state["records"].values())


def test_same_flood_better_administration_and_infrastructure_reduces_planned_damage():
    poor, strong = world(), world()
    for a in strong.npc_state["persons"].values():
        if a["country"] == "a":
            a["abilities"]["administration"] = 1
    for key in strong.regions["ra"].infrastructure:
        strong.regions["ra"].infrastructure[key] = 200
    a, b = make(poor), make(strong)
    assert b["impact_plan"]["vulnerability_index"] < a["impact_plan"]["vulnerability_index"]
    counts = lambda w, e: sum(x["payload"]["count"] for x in w.crisis_state["effects"].values() if x["event_id"] == e["event_id"] and x["kind"] == "population_loss")
    assert counts(strong, b) <= counts(poor, a)


def test_epidemic_damage_uses_existing_sanitation_and_health_not_future_medicine():
    poor, strong = world(), world()
    strong.regions["ra"].infrastructure["sanitation"] = 200
    strong.economy["regions"]["ra"]["demography"]["health_level"] = .9
    a, b = make(poor, "epidemic"), make(strong, "epidemic")
    assert b["impact_plan"]["epidemic_burden"] < a["impact_plan"]["epidemic_burden"]
    assert not strong.countries["a"].technologies


@pytest.mark.parametrize("kind", ["flood", "epidemic"])
def test_one_occurrence_has_one_demographic_loss_owner(kind):
    w = world()
    e = make(w, kind)
    planned = next(x["payload"]["count"] for x in w.crisis_state["effects"].values() if x["event_id"] == e["event_id"] and x["kind"] == "population_loss")
    advance(w, 30)
    w.date = "1897-02-01"
    economy.tick(w, {"previous_date": "1897-01-31", "date": w.date, "days": 1}, random.Random(1))
    account = w.population_accounts["a"]
    assert account["epidemic_losses" if kind == "epidemic" else "disaster_losses"] == planned
    ce.apply_due(w)
    assert not any(w.economy["regions"]["ra"]["pending_losses"].values())


def test_flood_has_one_monthly_capital_and_infrastructure_damage_owner():
    w, baseline = world(), world()
    e = make(w)
    cap = {k: s["capital_stock"] for k, s in w.economy["regions"]["ra"]["sectors"].items()}
    old_roads = w.regions["ra"].infrastructure["roads"]
    ce.apply_due(w)
    shock = next(s for s in w.economy["shocks"].values() if s["kind"] == "capital_destruction")
    assert w.regions["ra"].infrastructure["roads"] == old_roads  # no stale production capacity
    w.date = "1897-02-01"
    economy.tick(w, {"previous_date": "1897-01-31", "date": w.date, "days": 1}, random.Random(1))
    baseline.date = w.date
    economy.tick(baseline, {"previous_date": "1897-01-31", "date": baseline.date, "days": 1}, random.Random(1))
    for sector, old_cap in cap.items():
        assert w.economy["regions"]["ra"]["sectors"][sector]["destruction_losses"] == pytest.approx(old_cap * .7 * shock["severity"])
    assert w.regions["ra"].infrastructure["roads"] == pytest.approx(baseline.regions["ra"].infrastructure["roads"] * (1 - .7 * shock["severity"]))


def test_funded_relief_reduces_delayed_mortality_without_undoing_past_damage():
    normal, relieved = world(players=("a",)), world(players=("a",))
    a, b = make(normal), make(relieved)
    ce.apply_due(normal)
    ce.apply_due(relieved)
    assert cr.respond(relieved, b["event_id"], "a", "relief", player=True)["status"] == "accepted"
    ce.apply_due(relieved)
    advance(normal, 14)
    advance(relieved, 14)
    assert relieved.economy["regions"]["ra"]["pending_losses"]["disaster_losses"] < normal.economy["regions"]["ra"]["pending_losses"]["disaster_losses"]
    assert any(s["kind"] == "capital_destruction" for s in relieved.economy["shocks"].values())


def test_bad_harvest_is_not_direct_famine_or_immediate_mass_death():
    w = world()
    e = make(w, "bad_harvest")
    ce.apply_due(w)
    assert not e["child_events"]
    assert not [x for x in w.crisis_state["effects"].values() if x["kind"] == "population_loss"]
    assert all(v == 0 for v in w.economy["regions"]["ra"]["pending_losses"].values())


def test_resolved_crisis_does_not_repeat_damage_or_delete_memory():
    w = world()
    e = make(w, "earthquake")
    advance(w, 100)
    assert not e["ongoing"]
    count = len(w.crisis_state["effects"])
    pending = copy.deepcopy(w.economy["regions"]["ra"]["pending_losses"])
    advance(w, 30)
    assert len(w.crisis_state["effects"]) == count
    assert w.economy["regions"]["ra"]["pending_losses"] == pending
    assert any(m["event_id"] == e["event_id"] for m in w.npc_state["memories"]["a:ruler"])


def test_no_event_grants_no_peace_bonus_or_free_resources():
    w = world()
    before = copy.deepcopy(w.economy)
    c = copy.deepcopy(w.countries)
    advance(w, 20)
    assert not w.crisis_state["events"]
    assert w.economy == before and w.countries == c


def test_chain_mediation_can_close_without_zeroing_underlying_border_risk():
    w = world()
    e = crisis.create_event(w, "border_incident", "ra", ["a", "b"], severity=.8)
    cs.set_pressure(w, "border_tension", 90, pair=["a", "b"])
    for actor in ("a:ruler", "b:ruler"):
        cr.queue_report(w, e["event_id"], actor, delay=0)
    cr.deliver_due(w)
    for c in ("a", "b"):
        assert cr.respond(w, e["event_id"], c, "mediate", actor=c + ":ruler")["status"] == "accepted"
    crisis.tick(w, {"days": 1}, random.Random(1))
    assert not e["ongoing"]
    assert 0 < cs.pressure_value(w, "border_tension", pair=["a", "b"]) < 90
    assert not any(v["event_type"] == "state_of_war" for v in w.crisis_state["events"].values())


def test_authorized_ultimatum_creates_causal_child_not_unconnected_lottery():
    w = world()
    e = crisis.create_event(w, "border_incident", "ra", ["a", "b"], severity=.8)
    cr.queue_report(w, e["event_id"], "a:ruler", delay=0)
    cr.deliver_due(w)
    assert cr.respond(w, e["event_id"], "a", "ultimatum", actor="a:ruler")["status"] == "accepted"
    crisis.tick(w, {"days": 1}, random.Random(1))
    child = w.crisis_state["events"][e["child_events"][0]]
    assert child["event_type"] == "diplomatic_crisis" and child["parent_event"] == e["event_id"]
    assert child["causes"][0]["response"] == "ultimatum"
    assert not e["ongoing"]


def test_pressure_alone_cannot_jump_to_war_or_invent_military_mobilization():
    w = world()
    e = crisis.create_event(w, "ultimatum", "ra", ["a", "b"], severity=.95)
    cs.set_pressure(w, "border_tension", 100, pair=["a", "b"])
    e["escalation_drivers"] = [{"choices": ["ultimatum"], "local_unilateral": False}]
    advance(w, 365)
    assert e["ongoing"]
    assert not e["child_events"]
    assert not any(r["labour_state"]["mobilized"] for r in w.economy["regions"].values())


def test_war_state_is_not_a_combat_casualty_generator():
    w = world()
    armies = copy.deepcopy(w.armies)
    crisis.create_event(w, "state_of_war", "ra", ["a", "b"], severity=.9)
    ce.apply_due(w)
    assert any(r["status"] == "war" and {r["a"], r["b"]} == {"a", "b"} for r in w.relations.values())
    assert w.armies == armies
    assert not any(s["kind"] == "war_disruption" for s in w.economy["shocks"].values())
    assert not any(r["pending_losses"]["war_losses"] for r in w.economy["regions"].values())


def test_funded_bilateral_mobilization_can_causally_escalate_an_armed_clash_to_war_reproducibly():
    w = world(seed=31, borders={"ab": {"countries": ["a", "b"], "regions": ["ra", "rb"], "tension": 97}})
    e = crisis.create_event(w, "armed_clash", "ra", ["a", "b"], severity=.95,
        causes=[{"mechanism": "unresolved border claims and an ongoing hostile encounter"}],
        actual_facts={"combat_result_pending": True}, risk_family_key="international:ab")
    initial_armies = copy.deepcopy(w.armies)
    initial_population = {c: w.countries[c].population for c in ("a", "b")}
    for actor in ("a:ruler", "b:ruler"):
        cr.queue_report(w, e["event_id"], actor, delay=0)
    cr.deliver_due(w)
    for country in ("a", "b"):
        before = w.economy["regions"]["r" + country]["labour_state"]["mobilized"]
        result = cr.respond(w, e["event_id"], country, "mobilize", actor=country + ":ruler")
        assert result["status"] == "accepted" and result["response"]["actual_cost"] > 0
        assert w.economy["regions"]["r" + country]["labour_state"]["mobilized"] > before
    restored = World.from_dict(w.to_dict())
    first, second = advance(w, 365), advance(restored, 365)
    assert first == second and w.to_dict() == restored.to_dict()
    wars = [event for event in w.crisis_state["events"].values() if event["event_type"] == "state_of_war"]
    assert len(wars) == 1
    war = wars[0]
    assert war["parent_event"] == e["event_id"] and war["event_id"] in e["child_events"]
    assert e["resolution_state"] == "escalated" and war["ongoing"]
    cause = war["causes"][0]
    assert cause["mechanism"] == "unresolved pressure with actor actions"
    assert 0 < cause["probability"] < 1 and cause["draw"] < cause["probability"]
    assert any(key.startswith("escalation:") for key in w.crisis_state["streams"])
    assert any(r["status"] == "war" and {r["a"], r["b"]} == {"a", "b"} for r in w.relations.values())
    assert w.armies == initial_armies
    assert {c: w.countries[c].population for c in ("a", "b")} == initial_population
    assert not any(s["kind"] == "war_disruption" for s in w.economy["shocks"].values())
    assert not any(r["pending_losses"]["war_losses"] for r in w.economy["regions"].values())


def test_actual_arrears_expand_fiscal_stress_and_recovery_can_end_it():
    w = world()
    e = make(w, "fiscal_stress")
    s = w.economy["countries"]["a"]["credit"]
    s["arrears"] = 100
    s["distress_state"] = "arrears"
    advance(w, 7)
    child = w.crisis_state["events"][e["child_events"][0]]
    assert child["event_type"] == "payment_failure"
    s["arrears"] = 0
    s["distress_state"] = "normal"
    advance(w, 7)
    assert not child["ongoing"]


def test_causal_history_and_named_rng_survive_serialized_world_restore():
    w = world(players=("a",))
    make(w)
    advance(w, 8)
    restored = World.from_dict(w.to_dict())
    first, second = advance(w, 22), advance(restored, 22)
    assert first == second and w.to_dict() == restored.to_dict()
    assert w.crisis_state["pending_decisions"]


def test_measured_response_evidence_waits_for_real_population_account_and_delivery():
    w = world(players=("a",))
    e = make(w, "earthquake")
    ce.apply_due(w)
    assert cr.respond(w, e["event_id"], "a", "relief", player=True)["status"] == "accepted"
    ce.apply_due(w)
    advance(w, 14)
    assert not e.get("measured_response_outcomes")  # loss is delivered, not yet accounted
    w.date = "1897-02-01"
    economy.tick(w, {"previous_date": "1897-01-31", "date": w.date, "days": 1}, random.Random(2))
    logs = crisis.tick(w, {"days": 1}, random.Random(2))
    measured = next(v for v in logs if v["kind"] == "response_outcome_measured")
    assert measured["facts"]["realized_population_loss"] == w.population_accounts["a"]["disaster_losses"]
    assert measured["facts"]["prevented_population_loss"] > 0
    record_id = measured["record_id"]
    assert not any(m["event_id"] == record_id for m in w.npc_state["memories"]["a:finance"])
    again = crisis.tick(w, {"days": 1}, random.Random(2))
    assert not any(v["kind"] == "response_outcome_measured" for v in again)
    advance(w, 8)
    assert any(m["event_id"] == record_id for m in w.npc_state["memories"]["a:finance"])


def test_natural_damage_without_any_response_never_gets_measured_ruler_blame():
    w = world()
    e = make(w, "earthquake")
    advance(w, 30)
    w.date = "1897-02-01"
    economy.tick(w, {"previous_date": "1897-01-31", "date": w.date, "days": 1}, random.Random(2))
    logs = crisis.tick(w, {"days": 1}, random.Random(2))
    assert not any(v["kind"] == "response_outcome_measured" for v in logs)
    assert not [r for r in w.npc_state["records"].values() if r["facts"].get("event_id") == e["event_id"]]


def test_automatic_foreign_crisis_reports_never_disclose_enemy_true_national_mobilization():
    w = world()
    e = crisis.create_event(w, "full_mobilization", "ra", ["a", "b"], severity=.7,
        actual_facts={"actual_mobilization": {"a": {"standing": 100, "mobilized": 200}, "b": {"standing": 999999, "mobilized": 54321}},
                      "secret_plan": "classified invasion route"})
    reports = [m for m in w.crisis_state["messages"].values() if m["event_id"] == e["event_id"] and m["actor"] == "a:ruler"]
    assert reports
    facts = reports[0]["data"]["facts"]
    assert "actual_mobilization" not in facts and "secret_plan" not in facts
    assert facts["enemy_strength"] == w.npc_state["reports"]["a:ruler"]["diplomacy"]["data"]["enemy_strength"]
    assert facts["enemy_strength"] != 999999


def test_secret_event_does_not_automatically_inform_every_cabinet_member():
    w = world(players=("a",))
    e = make(w, "border_incident", visibility="secret", involved_actors=["a:military"])
    actors = {m["actor"] for m in w.crisis_state["messages"].values() if m["event_id"] == e["event_id"]}
    assert actors == {"a:military"}
    advance(w, 1)
    assert not w.crisis_state["pending_decisions"]


def test_mutual_ceasefire_ends_actual_war_status_without_territory_or_battle_result():
    w = world()
    e = crisis.create_event(w, "state_of_war", "ra", ["a", "b"], severity=.9)
    ce.apply_due(w)
    before = copy.deepcopy(w.armies)
    for actor in ("a:ruler", "b:ruler"):
        cr.queue_report(w, e["event_id"], actor, delay=0)
    cr.deliver_due(w)
    assert cr.respond(w, e["event_id"], "a", "withdraw", actor="a:ruler")["status"] == "accepted"
    ce.apply_due(w)
    assert e["ongoing"] and any(r["status"] == "war" for r in w.relations.values())
    assert cr.respond(w, e["event_id"], "b", "mediate", actor="b:ruler")["status"] == "accepted"
    ce.apply_due(w)
    assert not e["ongoing"]
    pair = next(r for r in w.relations.values() if {r["a"], r["b"]} == {"a", "b"})
    assert pair["status"] == "armistice"
    assert w.armies == before and w.regions["ra"].owner == "a" and w.regions["rb"].owner == "b"


def coercive_world(seed, severity, pressure):
    w = world(seed=seed)
    e = crisis.create_event(w, "protest", "ra", ["a"], severity=severity)
    cs.set_pressure(w, "civil_unrest", pressure, country="a", region="ra")
    cr.queue_report(w, e["event_id"], "a:ruler", delay=0)
    cr.deliver_due(w)
    assert cr.respond(w, e["event_id"], "a", "limited_repression", actor="a:ruler")["status"] == "accepted"
    crisis.tick(w, {"days": 1}, random.Random(1))
    assert not e["child_events"]
    advance(w, 70)
    return bool(e["child_events"])


def test_coercion_is_a_risk_driver_not_a_guaranteed_riot():
    low = [coercive_world(seed, .1, 3) for seed in range(12)]
    high = [coercive_world(seed, .95, 95) for seed in range(12)]
    assert not any(low)
    assert any(high) and not all(high)


@pytest.mark.parametrize("kind", ["cultural_restriction", "language_restriction"])
def test_cultural_and_language_restrictions_feed_identity_pressure_without_full_identity_ai(kind):
    w = world()
    e = make(w, kind)
    ce.apply_due(w)
    assert cs.pressure_value(w, "ethnic_or_religious_tension", region="ra") > 0
    assert cs.pressure_value(w, "separatism", region="ra") > 0
    assert not e["child_events"]


@pytest.mark.parametrize("kind", ["full_mobilization", "armed_clash", "state_of_war"])
def test_additional_funded_mobilization_cannot_reset_or_reverse_later_crisis_stages(kind):
    w = world()
    e = crisis.create_event(w, kind, "ra", ["a", "b"], severity=.9)
    initial_level, review_date = e["chain_level"], e["next_review"]
    before = w.economy["regions"]["ra"]["labour_state"]["mobilized"]
    cr.queue_report(w, e["event_id"], "a:ruler", delay=0)
    cr.deliver_due(w)
    result = cr.respond(w, e["event_id"], "a", "mobilize", actor="a:ruler")
    assert result["status"] == "accepted" and result["response"]["actual_cost"] > 0
    assert w.economy["regions"]["ra"]["labour_state"]["mobilized"] > before
    crisis.tick(w, {"days": 1}, random.Random(1))
    assert e["ongoing"] and not e["child_events"]
    assert e["chain_level"] == initial_level and e["event_type"] == kind
    assert e["next_review"] == review_date
    assert e["escalation_drivers"][-1]["choices"] == ["mobilize"]
    assert len(w.crisis_state["events"]) == 1
