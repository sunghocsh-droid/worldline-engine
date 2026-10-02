"""Private information, lawful political choice and dated response regressions."""
import copy
import random

import pytest

from worldline import crisis_state as cs, crisis_effects as ce, crisis_response as cr
from worldline import npc, npc_state as ns, npc_decision as nd
from worldline.npc_benchmark import campaign_world


def world(players=()):
    w = campaign_world()
    w.crisis_state = cs.initialize_state({"player_countries": list(players)}, root_seed=731)
    for c in "abcd":
        cs.configure_region(w, "r" + c)
        ns_person = w.npc_state["persons"][c + ":ruler"]
        ns_person["authority"] += ["mobilize", "fiscal_action"]
    npc.sense(w)
    return w


def event(w, kind="flood", severity=.7, countries=("a",), region="ra", **facts):
    return cs.add_event(w, kind, region, countries, severity=severity, actual_facts=facts)


def informed(w, e, actor="a:ruler", **kwargs):
    cr.queue_report(w, e["event_id"], actor, delay=0, **kwargs)
    cr.deliver_due(w)


def test_report_is_frozen_and_unknown_until_received():
    w, e = world(), None
    e = event(w, actual_damage=12)
    message = cr.queue_report(w, e["event_id"], "a:ruler", delay=3)
    e["actual_facts"]["actual_damage"] = 99
    assert not ns.actor_view(w.npc_state, "a:ruler", w.date)["beliefs"].get("event:" + e["event_id"])
    assert not cr.responses_for(w, e["event_id"], "a:ruler")[1]
    assert not cr.deliver_due(w)
    assert not w.npc_state["memories"]["a:ruler"][-1].get("event_id") == e["event_id"]
    w.date = "1897-01-04"
    receipt = cr.deliver_due(w)
    assert len(receipt) == 1
    memory = w.npc_state["memories"]["a:ruler"][-1]
    assert memory["observed_facts"]["actual_damage"] == 12
    assert w.crisis_state["messages"][message["message_id"]]["received_at"] == w.date
    assert not cr.deliver_due(w)


def test_false_information_and_correction_replace_memory_without_truth_broadcast():
    w = world()
    e = event(w, "border_incident", countries=("a", "b"), threat=.1)
    informed(w, e, intentional_distortion={"threat": .95, "severity": .95})
    view, options, _ = cr.responses_for(w, e["event_id"], "a:ruler")
    before = nd.decide(view, options, random.Random(2))
    assert not cr.responses_for(w, e["event_id"], "a:finance")[1]
    informed(w, e, source="corrected field report", confidence=1, reliability=1)
    view, options, _ = cr.responses_for(w, e["event_id"], "a:ruler")
    after = nd.decide(view, options, random.Random(2))
    old = next(v for v in before["evaluations"] if v["option"] == "mobilize")["score"]
    new = next(v for v in after["evaluations"] if v["option"] == "mobilize")["score"]
    assert old > new
    assert len([m for m in w.npc_state["memories"]["a:ruler"] if m["event_id"] == e["event_id"]]) == 1
    assert e["actual_facts"]["threat"] == .1


def test_direct_exposure_and_distant_news_create_different_memories():
    w = world()
    e = event(w)
    informed(w, e, actor="a:ruler", direct=True)
    informed(w, e, actor="a:finance")
    local, distant = (w.npc_state["memories"][a][-1] for a in ("a:ruler", "a:finance"))
    assert local["salience"] > distant["salience"]
    assert local["personal_impact"] < distant["personal_impact"]


def test_natural_occurrence_has_no_automatic_ruler_blame():
    w = world()
    before = ns.evaluate_ruler(w.npc_state, "a:finance", "a:ruler", w.date)
    e = event(w, severity=1)
    informed(w, e, actor="a:finance", direct=True)
    after = ns.evaluate_ruler(w.npc_state, "a:finance", "a:ruler", w.date)
    assert after["respect"] == before["respect"]
    assert after["trust"] == before["trust"]
    assert not [r for r in w.npc_state["records"].values() if r["facts"].get("event_id") == e["event_id"]]


@pytest.mark.parametrize("response,sign", [("relief", 1), ("ignore", -1)])
def test_response_execution_is_separate_evidence_delivered_later(response, sign):
    w = world()
    e = event(w)
    informed(w, e)
    before = ns.evaluate_ruler(w.npc_state, "a:finance", "a:ruler", w.date)
    result = cr.respond(w, e["event_id"], "a", response, actor="a:ruler")
    assert result["status"] == "accepted"
    unchanged = ns.evaluate_ruler(w.npc_state, "a:finance", "a:ruler", w.date)
    assert unchanged["respect"] == before["respect"]
    w.date = "1897-02-01"
    cr.deliver_due(w)
    after = ns.evaluate_ruler(w.npc_state, "a:finance", "a:ruler", w.date)
    assert (after["respect"] - before["respect"]) * sign > 0
    assert any(m["category"] == "disaster_response" for m in w.npc_state["memories"]["a:finance"])


def test_paid_relief_and_finance_mirrors_are_real():
    w = world()
    e = event(w)
    informed(w, e)
    cash, debt = w.countries["a"].treasury, w.countries["a"].debt
    result = cr.respond(w, e["event_id"], "a", "relief", actor="a:ruler")
    assert result["status"] == "accepted"
    assert result["response"]["actual_cost"] > 0
    f = w.economy["countries"]["a"]["finance"]
    assert f["reserves"] == w.countries["a"].treasury
    assert f["interim_net_cashflow"] == w.countries["a"].treasury - cash
    assert f["interim_debt_change"] == w.countries["a"].debt - debt
    ce.apply_due(w)
    assert e["response_mitigation"] > 0


def test_authority_cannot_be_inferred_from_an_opinion():
    w = world()
    e = event(w)
    informed(w, e, actor="a:finance")
    _, options, _ = cr.responses_for(w, e["event_id"], "a:finance")
    assert options  # finance minister can offer advice
    before = copy.deepcopy(w.economy)
    result = cr.respond(w, e["event_id"], "a", "relief", actor="a:finance")
    assert result["status"] == "rejected"
    assert w.economy == before


def test_unknown_event_cannot_trigger_executive_action():
    w = world()
    e = event(w)
    assert cr.respond(w, e["event_id"], "a", "relief", actor="a:ruler")["status"] == "rejected"


def test_player_ultimatum_remains_pending_and_timeout_is_explicit_once():
    w = world(players=("a",))
    e = event(w, "ultimatum", countries=("a", "b"))
    informed(w, e)
    pending = cr.schedule_pending(w, e["event_id"], "a", deadline="1897-01-03")
    assert cr.respond(w, e["event_id"], "a", "mediate", actor="a:ruler")["status"] == "rejected"
    cr.process_due(w)
    assert not [r for r in e["responses"] if r["country"] == "a"]
    assert w.crisis_state["pending_decisions"][pending["pending_id"]]["status"] == "pending"
    w.date = "1897-01-03"
    cr.process_due(w)
    record = w.crisis_state["pending_decisions"][pending["pending_id"]]
    assert record["status"] == "expired"
    assert record["resolution"]["response"] == "ignore"
    count = len(e["responses"])
    cr.process_due(w)
    assert len(e["responses"]) == count


def test_player_can_answer_without_engine_selecting_political_option():
    w = world(players=("a",))
    e = event(w, "ultimatum", countries=("a", "b"))
    pending = cr.schedule_pending(w, e["event_id"], "a")
    result = cr.respond(w, e["event_id"], "a", "withdraw", player=True)
    assert result["status"] == "accepted"
    assert w.crisis_state["pending_decisions"][pending["pending_id"]]["status"] == "answered"
    ce.apply_due(w)
    assert not e["ongoing"]


def test_unilateral_mediation_does_not_force_counterpart_to_make_peace():
    w = world()
    e = event(w, "diplomatic_crisis", countries=("a", "b"))
    for actor in ("a:ruler", "b:ruler"):
        informed(w, e, actor=actor)
    assert cr.respond(w, e["event_id"], "a", "mediate", actor="a:ruler")["status"] == "accepted"
    ce.apply_due(w)
    assert e["ongoing"]
    assert cr.respond(w, e["event_id"], "b", "ultimatum", actor="b:ruler")["status"] == "accepted"
    ce.apply_due(w)
    assert e["ongoing"]


def test_mutual_mediation_can_end_a_crisis_with_residual_memories():
    w = world()
    e = event(w, "diplomatic_crisis", countries=("a", "b"))
    for actor in ("a:ruler", "b:ruler"):
        informed(w, e, actor=actor)
    for country in ("a", "b"):
        assert cr.respond(w, e["event_id"], country, "mediate", actor=country + ":ruler")["status"] == "accepted"
    ce.apply_due(w)
    assert not e["ongoing"]
    assert any(m["event_id"] == e["event_id"] for m in w.npc_state["memories"]["a:ruler"])


def test_negotiation_and_repression_have_different_actual_effect_paths():
    w, v = world(), world()
    a, b = event(w, "protest"), event(v, "protest")
    informed(w, a)
    informed(v, b)
    assert cr.respond(w, a["event_id"], "a", "negotiate", actor="a:ruler")["status"] == "accepted"
    assert cr.respond(v, b["event_id"], "a", "limited_repression", actor="a:ruler")["status"] == "accepted"
    ce.apply_due(w)
    ce.apply_due(v)
    assert not a["ongoing"] and b["ongoing"]
    assert v.crisis_state["triggers"][-1]["payload"]["response"] == "limited_repression"


def test_local_patrol_requires_owned_region_force_and_delegation():
    w = world()
    ns.add_person(w.npc_state, "local", "a", "Local commander", role="commander", region="ra",
        authority=["local_action"], preferences={"security": 1, "prestige": 1}, traits={"risk_tolerance": 1, "prestige_sensitivity": 1})
    e = event(w, "border_incident", countries=("a", "b"), threat=.05, central_intent="avoid_conflict")
    informed(w, e, actor="local", intentional_distortion={"threat": .95})
    military_before = copy.deepcopy(w.economy["regions"]["ra"]["labour_state"])
    result = cr._local(w, e, "local")
    assert result["kind"] == "local_action"
    assert result["execution"]["hook_only"] and not result["national_mobilization"]
    assert w.economy["regions"]["ra"]["labour_state"] == military_before
    ce.apply_due(w)
    trigger = w.crisis_state["triggers"][-1]["payload"]
    assert trigger["central_order"] == "avoid_conflict" and trigger["misinformation"]
    assert cr._local(w, e, "local") is None
    w.npc_state["persons"]["local"]["authority"] = []
    f = event(w, "border_incident", countries=("a", "b"), threat=1)
    informed(w, f, actor="local")
    assert cr._local(w, f, "local") is None
    assert cr.respond(w, f["event_id"], "a", "mobilize", actor="local")["status"] == "rejected"


def test_mobilization_uses_existing_labour_pipeline_and_paid_finance():
    w = world()
    e = event(w, "ultimatum", countries=("a", "b"))
    informed(w, e)
    before = w.economy["regions"]["ra"]["labour"]["employed"]
    result = cr.respond(w, e["event_id"], "a", "mobilize", actor="a:ruler")
    assert result["status"] == "accepted"
    assert w.economy["regions"]["ra"]["labour_state"]["mobilized"] > 0
    assert w.economy["regions"]["ra"]["labour"]["employed"] < before
    assert result["response"]["actual_cost"] > 0


def test_perceived_cost_does_not_change_when_hidden_truth_changes():
    w = world()
    e = event(w)
    informed(w, e)
    _, before, _ = cr.responses_for(w, e["event_id"], "a:ruler")
    e["severity"] = .99
    w.countries["a"].gdp = 100000
    _, after, _ = cr.responses_for(w, e["event_id"], "a:ruler")
    assert [o["financial"]["perceived_state"] for o in before] == [o["financial"]["perceived_state"] for o in after]


@pytest.mark.parametrize("fault", ["delay", "recipient", "receipt", "deadline"])
def test_report_and_pending_validator_rejects_corrupt_data(fault):
    w = world(players=("a",))
    e = event(w, "ultimatum", countries=("a", "b"))
    message = cr.queue_report(w, e["event_id"], "a:ruler", delay=2)
    pending = cr.schedule_pending(w, e["event_id"], "a")
    assert not cr.validation_issues(w)
    if fault == "delay":
        w.crisis_state["messages"][message["message_id"]]["communication_delay"] = 100
    elif fault == "recipient":
        w.crisis_state["messages"][message["message_id"]]["actor"] = "unknown"
    elif fault == "receipt":
        w.crisis_state["messages"][message["message_id"]].update(status="delivered", received_at="1896-01-01")
    else:
        w.crisis_state["pending_decisions"][pending["pending_id"]]["deadline"] = "1896-01-01"
    assert cr.validation_issues(w)


def test_undelivered_truth_cannot_be_smuggled_into_npc_memory():
    w = world()
    e = event(w)
    ns.receive_report(w.npc_state, "a:ruler", "fake-delivery", {"event_id": e["event_id"], "day": w.date,
        "subject": "crisis:" + e["event_id"], "category": "custom:crisis_event", "facts": {"correct_secret_truth": 10}}, w.date, "unrecorded delivery")
    ns.remember(w.npc_state, "a:ruler", e["event_id"])
    assert any("undelivered" in value for value in cr.validation_issues(w))


def test_report_and_response_are_reproducible_with_saved_named_streams():
    w = world()
    e = event(w, "protest")
    for actor in ("a:ruler", "a:finance", "a:military", "a:education"):
        cr.queue_report(w, e["event_id"], actor, delay=0)
    v = copy.deepcopy(w)
    first, second = cr.process_due(w), cr.process_due(v)
    assert first == second and w.to_dict() == v.to_dict()
    assert w.crisis_state == v.crisis_state


def distressed(w):
    c, e = w.countries["a"], w.economy["countries"]["a"]
    c.treasury, c.debt = 0, 10000
    e["credit"].update(domestic_debt=10000, foreign_debt=0, distress_state="default", default_count=1)
    e["finance"]["reserves"] = 0
    from worldline import credit
    credit.refresh(w, "a")


def test_default_does_not_politically_veto_accessible_emergency_relief():
    w = world()
    distressed(w)
    e = event(w)
    informed(w, e)
    view, options, actual = cr.responses_for(w, e["event_id"], "a:ruler")
    quote = actual["relief"]
    assert not quote["normal_financial_feasibility"]["feasible"]
    assert quote["emergency_financial_feasibility"]["feasible"]
    selected = nd.decide(view, options, random.Random(2))
    assert next(v for v in selected["evaluations"] if v["option"] == "relief")["eligible"]
    result = cr.respond(w, e["event_id"], "a", "relief", actor="a:ruler")
    assert result["status"] == "accepted"
    assert result["response"]["actual_risk"] > .5
    assert any(w.economy["countries"]["a"]["credit"]["emergency_used"].values())


def test_exhausted_finance_prevents_creating_free_relief_resources():
    w = world()
    distressed(w)
    s = w.economy["countries"]["a"]["credit"]
    s["financial_access"] = dict.fromkeys(s["financial_access"], False)
    e = event(w)
    informed(w, e)
    before = copy.deepcopy(w.economy)
    result = cr.respond(w, e["event_id"], "a", "relief", actor="a:ruler")
    assert result["status"] == "rejected"
    assert w.economy == before
    assert not e["responses"]


def test_armed_deployment_without_any_personnel_is_physically_infeasible():
    w = world()
    w.armies = {a: v for a, v in w.armies.items() if v.owner != "a"}
    e = event(w, "riot")
    informed(w, e)
    before = copy.deepcopy(w.economy)
    result = cr.respond(w, e["event_id"], "a", "military_deployment", actor="a:ruler")
    assert result["status"] == "rejected"
    assert w.economy == before


def test_local_command_trace_does_not_invent_central_avoid_order():
    w = world()
    ns.add_person(w.npc_state, "local", "a", "Local commander", role="commander", region="ra",
        authority=["local_action"], preferences={"security": 1, "prestige": 1})
    e = event(w, "border_incident", countries=("a", "b"), threat=1)
    informed(w, e, actor="local")
    cr._local(w, e, "local")
    ce.apply_due(w)
    trigger = w.crisis_state["triggers"][-1]["payload"]
    assert trigger["central_order"] == "await_authorization"


def test_unrelated_event_does_not_change_same_scope_npc_response_noise():
    a, b = world(), world()
    event(b, "fire", countries=("d",), region="rd")
    left, right = event(a, "protest"), event(b, "protest")
    assert left["event_id"] != right["event_id"]
    for w, e in ((a, left), (b, right)):
        for actor in ("a:ruler", "a:finance"):
            cr.queue_report(w, e["event_id"], actor, delay=0)
    first, second = cr.process_due(a), cr.process_due(b)
    choices = lambda logs: [(v["actor"], v["decision"]["selected"],
        [q["score"] for q in v["decision"]["evaluations"]]) for v in logs if v["kind"] == "crisis_npc_decision"]
    assert choices(first) == choices(second)


def test_measured_aftermath_can_correct_initial_response_capacity_evidence():
    w = world()
    e = event(w)
    informed(w, e)
    assert cr.respond(w, e["event_id"], "a", "relief", actor="a:ruler")["status"] == "accepted"
    outcome = cr.record_response_outcome(w, e["event_id"], "a", -.8, facts={"warning_ignored": True, "aid_arrived_after_damage": True})
    assert outcome["facts"]["measured_aftermath"]
    assert not any(m["event_id"] == outcome["record_id"] for m in w.npc_state["memories"]["a:finance"])
    w.date = "1897-02-01"
    cr.deliver_due(w)
    assert any(m["event_id"] == outcome["record_id"] and m["observed_facts"]["performance"] == -.8 for m in w.npc_state["memories"]["a:finance"])


def test_failed_execution_retries_on_schedule_instead_of_daily_full_council():
    w = world()
    for actor in ("a:ruler", "a:finance", "a:military", "a:education"):
        w.npc_state["persons"][actor]["authority"] = []
    e = event(w, "protest")
    informed(w, e)
    assert any(v["kind"] == "crisis_npc_decision" for v in cr.process_due(w))
    w.date = "1897-01-02"
    assert not any(v["kind"] == "crisis_npc_decision" for v in cr.process_due(w))


def test_repression_does_not_assign_modern_moral_failure_before_outcome():
    w = world()
    e = event(w, "protest")
    informed(w, e)
    result = cr.respond(w, e["event_id"], "a", "limited_repression", actor="a:ruler")
    evidence = next(v for v in result["events"] if v["kind"] == "ruler_record")
    assert evidence["facts"]["performance"] == 0


def test_commands_are_delayed_separate_from_event_reports_and_create_no_force():
    w = world()
    ns.add_person(w.npc_state, "local", "a", "Local commander", role="commander", region="ra", authority=["local_action"])
    e = event(w, "border_incident", countries=("a", "b"), threat=.9)
    informed(w, e, actor="local")
    before = copy.deepcopy(w.economy)
    message = cr.queue_command(w, e["event_id"], "local", {"intent": "avoid_conflict", "withdraw_if_attacked": True}, delay=4, sender="a:ruler")
    view = ns.actor_view(w.npc_state, "local", w.date)
    assert not cr._known_command(view, e["event_id"])
    assert cr._known(view, e["event_id"])["threat"] == .9
    w.date = "1897-01-05"
    cr.deliver_due(w)
    view = ns.actor_view(w.npc_state, "local", w.date)
    assert cr._known_command(view, e["event_id"])["central_intent"] == "avoid_conflict"
    assert cr._known(view, e["event_id"])["threat"] == .9
    assert w.crisis_state["messages"][message["message_id"]]["received_at"] == w.date
    assert w.economy == before
    assert not cr.validation_issues(w)


def test_negotiating_default_enters_existing_workout_without_erasing_debt_or_crisis():
    w = world()
    distressed(w)
    s = w.economy["countries"]["a"]["credit"]
    s["arrears"] = 200
    e = event(w, "payment_failure")
    informed(w, e)
    opening = w.countries["a"].debt
    result = cr.respond(w, e["event_id"], "a", "negotiate", actor="a:ruler")
    assert result["status"] == "accepted"
    assert s["restructuring"] and s["distress_state"] == "restructuring"
    assert w.countries["a"].debt >= opening
    ce.apply_due(w)
    assert e["ongoing"]


def test_concession_to_unpaid_contracts_pays_only_actual_funded_amount():
    w = world()
    s = w.economy["countries"]["a"]["credit"]
    s["arrears"] = 200
    s["distress_state"] = "arrears"
    e = event(w, "payment_failure")
    informed(w, e)
    result = cr.respond(w, e["event_id"], "a", "concession", actor="a:ruler")
    assert result["status"] == "accepted"
    paid = result["response"]["fiscal_aftermath"]["existing_arrears_paid"]
    assert paid <= result["response"]["actual_cost"]
    assert s["arrears"] == 200 - paid
    ce.apply_due(w)
    assert e["ongoing"]


@pytest.mark.parametrize("fault", ["memory", "event_id", "memory_range", "choices", "resolution"])
def test_strict_save_boundary_rejects_later_delivery_crashes(fault):
    w = world(players=("a",))
    e = event(w, "ultimatum", countries=("a", "b"))
    message = cr.queue_report(w, e["event_id"], "a:ruler")
    pending = cr.schedule_pending(w, e["event_id"], "a")
    if fault == "memory":
        w.crisis_state["messages"][message["message_id"]]["memory"] = {"bogus": 1}
    elif fault == "event_id":
        w.crisis_state["messages"][message["message_id"]]["data"]["event_id"] = "some-other-event"
    elif fault == "memory_range":
        w.crisis_state["messages"][message["message_id"]]["memory"]["personal_impact"] = -100
    elif fault == "choices":
        w.crisis_state["pending_decisions"][pending["pending_id"]]["available_responses"] = ["invent_money"]
    else:
        w.crisis_state["pending_decisions"][pending["pending_id"]].update(status="answered", resolution={})
    assert cr.validation_issues(w)


def test_delivered_report_and_pending_history_cannot_be_rewritten():
    w = world(players=("a",))
    e = event(w, "ultimatum", countries=("a", "b"))
    informed(w, e)
    pending = cr.schedule_pending(w, e["event_id"], "a")
    previous = copy.deepcopy(w)
    message = next(iter(w.crisis_state["messages"].values()))
    message["data"]["facts"]["severity"] = .01
    w.crisis_state["pending_decisions"][pending["pending_id"]]["deadline"] = "1898-01-01"
    issues = cr.validation_issues(w, previous)
    assert any("snapshot" in value for value in issues)
    assert any("decision history" in value for value in issues)


def test_both_border_officers_can_act_only_on_their_explicit_border_regions():
    w = world()
    cs.configure_border(w, "ab", ["a", "b"], ["ra", "rb"], tension=90)
    ns.add_person(w.npc_state, "opposite", "b", "Opposite commander", role="commander", region="rb",
        authority=["local_action"], preferences={"security": 1, "prestige": 1})
    # B is industrial, so its good communications normally limit local autonomy;
    # this example is a communication-poor isolated border district.
    w.economy["countries"]["b"]["profile"] = "early_industrial"
    e = event(w, "border_incident", countries=("a", "b"), threat=1)
    informed(w, e, actor="opposite")
    result = cr._local(w, e, "opposite")
    assert result["kind"] == "local_action"
    assert result["execution"]["request"]["region"] == "rb"
    w.npc_state["persons"]["opposite"]["region"] = "rd"
    assert cr._local(w, e, "opposite") is None


def test_corrected_enemy_report_changes_perceived_military_balance():
    w = world()
    e = event(w, "border_incident", countries=("a", "b"), threat=.9)
    informed(w, e, intentional_distortion={"own_strength": 100, "enemy_strength": 20})
    view, options, _ = cr.responses_for(w, e["event_id"], "a:ruler")
    initial = nd.decide(view, options, random.Random(3))
    informed(w, e, observed_facts={"threat": .9, "own_strength": 100, "enemy_strength": 300}, source="confirmed reconnaissance")
    view, options, _ = cr.responses_for(w, e["event_id"], "a:ruler")
    corrected = nd.decide(view, options, random.Random(3))
    score = lambda decision: next(v["score"] for v in decision["evaluations"] if v["option"] == "mobilize")
    assert score(initial) > score(corrected)


def test_delayed_observation_stays_stale_while_memory_begins_on_receipt():
    w = world()
    e = event(w, "border_incident", countries=("a", "b"), threat=.4)
    w.date = "1897-01-06"
    message = cr.queue_report(w, e["event_id"], "a:ruler", delay=14)
    assert message["as_of"] == "1897-01-06"
    assert message["data"]["day"] == "1897-01-01"
    w.date = "1897-01-20"
    cr.deliver_due(w)
    view = ns.actor_view(w.npc_state, "a:ruler", w.date)
    metadata = view["report_metadata"]["event:" + e["event_id"]]
    assert metadata["as_of"] == "1897-01-06" and metadata["age_days"] == 14
    report = w.npc_state["reports"]["a:ruler"]["event:" + e["event_id"]]
    memory = next(m for m in view["memories"] if m["event_id"] == e["event_id"])
    assert report["received_at"] == memory["received_date"] == "1897-01-20"
    assert memory["day"] == "1897-01-01" and memory["age_days"] == 0
    assert not cr.validation_issues(w)


def test_message_nine_precedes_ten_so_same_arrival_correction_is_latest():
    w = world()
    e = event(w, "border_incident", countries=("a", "b"), threat=.1)
    for _ in range(8):
        cr.queue_report(w, e["event_id"], "a:finance", delay=0)
    old = cr.queue_report(w, e["event_id"], "a:ruler", delay=0, intentional_distortion={"threat": .95})
    correction = cr.queue_report(w, e["event_id"], "a:ruler", delay=0, source="verified correction")
    assert old["message_id"].endswith("-9") and correction["message_id"].endswith("-10")
    receipts = [v for v in cr.deliver_due(w) if v["actor"] == "a:ruler"]
    assert [r["message_id"] for r in receipts] == [old["message_id"], correction["message_id"]]
    view = ns.actor_view(w.npc_state, "a:ruler", w.date)
    assert cr._known(view, e["event_id"])["threat"] == .1
    memory = next(m for m in view["memories"] if m["event_id"] == e["event_id"])
    assert memory["observed_facts"]["threat"] == .1 and memory["source"] == "verified correction"
    assert not cr.validation_issues(w)


def test_late_obsolete_report_preserves_newer_belief_and_memory_after_load():
    w = world()
    e = event(w, "border_incident", countries=("a", "b"), threat=.1)
    old = cr.queue_report(w, e["event_id"], "a:ruler", delay=14, intentional_distortion={"threat": .95})
    w.date = "1897-01-03"
    correction = cr.queue_report(w, e["event_id"], "a:ruler", delay=1, source="confirmed reconnaissance")
    w.date = "1897-01-04"
    cr.deliver_due(w)
    report = copy.deepcopy(w.npc_state["reports"]["a:ruler"]["event:" + e["event_id"]])
    memory = copy.deepcopy(next(m for m in w.npc_state["memories"]["a:ruler"] if m["event_id"] == e["event_id"]))
    w.schema_version = 4  # the minimal fixture installs crisis state directly
    v = type(w).from_dict(w.to_dict())
    w.date = v.date = "1897-01-15"
    receipts = cr.deliver_due(w)
    assert receipts == cr.deliver_due(v) and w.to_dict() == v.to_dict()
    assert len(receipts) == 1 and receipts[0]["belief_accepted"] is False
    assert w.crisis_state["messages"][old["message_id"]]["received_at"] == w.date
    assert w.crisis_state["messages"][correction["message_id"]]["belief_accepted"] is True
    assert w.npc_state["reports"]["a:ruler"]["event:" + e["event_id"]] == report
    assert next(m for m in w.npc_state["memories"]["a:ruler"] if m["event_id"] == e["event_id"]) == memory
    assert not cr.validation_issues(w)


def test_late_obsolete_command_does_not_reverse_current_standing_order():
    w = world()
    e = event(w, "border_incident", countries=("a", "b"))
    old = cr.queue_command(w, e["event_id"], "a:military", {"intent": "reinforce_patrol"}, delay=14, sender="a:ruler")
    w.date = "1897-01-03"
    cr.queue_command(w, e["event_id"], "a:military", {"intent": "avoid_conflict"}, delay=1, sender="a:ruler")
    w.date = "1897-01-04"
    cr.deliver_due(w)
    memory = copy.deepcopy(next(m for m in w.npc_state["memories"]["a:military"] if m["event_id"] == e["event_id"] + ":command"))
    w.date = "1897-01-15"
    cr.deliver_due(w)
    assert cr._known_command(ns.actor_view(w.npc_state, "a:military", w.date), e["event_id"])["central_intent"] == "avoid_conflict"
    assert next(m for m in w.npc_state["memories"]["a:military"] if m["event_id"] == e["event_id"] + ":command") == memory
    assert w.crisis_state["messages"][old["message_id"]]["belief_accepted"] is False
    assert not cr.validation_issues(w)


def test_measured_evidence_keeps_measurement_date_separate_from_delivery():
    w = world()
    e = event(w)
    ns.record_fact(w.npc_state, "measured", "1897-01-02", "a:ruler", "disaster_response",
        {"event_id": e["event_id"], "performance": .5, "measured_aftermath": True}, "settlement measurement")
    w.date = "1897-01-04"
    message = cr.queue_report(w, e["event_id"], "a:finance", record_id="measured", delay=2)
    assert message["as_of"] == "1897-01-02" and message["created_at"] == "1897-01-04"
    w.date = "1897-01-06"
    cr.deliver_due(w)
    report = w.npc_state["reports"]["a:finance"]["event:measured"]
    memory = next(m for m in w.npc_state["memories"]["a:finance"] if m["event_id"] == "measured")
    assert report["as_of"] == "1897-01-02" and report["received_at"] == memory["received_date"] == "1897-01-06"
    assert not cr.validation_issues(w)
