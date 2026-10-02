import ast
import copy
import inspect
import random

import pytest

from worldline import npc_decision


def view(role="ruler", **person_fields):
    return {"person": {"id": "official", "country": "fictional", "role": role,
        "abilities": {"judgment": .5, "military": .5}, "traits": {"risk_tolerance": .5},
        "biases": {}, "preferences": {}, "goals": [], "authority": ["execute_policy"],
        "groups": [], **person_fields}, "date": "1897-01-01", "beliefs": {}, "groups": [],
        "memories": [], "relationships": {}, "report_metadata": {"confidence": .8}}


def option(oid, kind="economic_policy", outcomes=None, *, cost=0, risk=0,
           normal=True, emergency=True, physical=True, **fields):
    return {"id": oid, "kind": kind, "country": "fictional", "params": {}, "tags": [],
        "outcomes": outcomes or {}, "financial": {
            "physical_feasibility": {"feasible": physical},
            "normal_financial_feasibility": {"feasible": normal},
            "emergency_financial_feasibility": {"feasible": emergency},
            "perceived_state": {"cost": cost, "risk": risk, "cost_scale": 100}},
        "institutional_feasibility": {"feasible": True}, **fields}


def hold():
    return option("hold", "status_quo")


def decide(actor, options, seed=7):
    return npc_decision.decide(actor, options, random.Random(seed))


def evaluation(result, oid):
    return next(e for e in result["evaluations"] if e["option"] == oid)


def test_finance_and_military_officials_reach_different_policy_conclusions():
    prudence = option("prudence", outcomes={"fiscal": .8, "prosperity": .1})
    military = option("armaments", "military", {"security": .8, "prestige": .4, "fiscal": -.5}, cost=20, risk=.2)
    assert decide(view("finance"), [hold(), prudence, military])["selected"] == "prudence"
    assert decide(view("military"), [hold(), prudence, military])["selected"] == "armaments"


def test_report_correction_changes_war_recommendation_without_access_to_actual_state():
    preparation = option("prepare", "war_preparation", {"security": .1}, cost=10, risk=.05,
        belief_context={"own_strength": 1000, "enemy_strength": 100, "threat": .9})
    peaceful_diplomacy = option("diplomacy", "diplomacy", {"security": .15}, cost=1, risk=.01)
    actor = view("military")
    exaggerated = decide(actor, [hold(), preparation, peaceful_diplomacy])
    assert exaggerated["selected"] == "prepare"
    corrected = copy.deepcopy(preparation)
    corrected["belief_context"]["enemy_strength"] = 5000
    assert decide(actor, [hold(), corrected, peaceful_diplomacy])["selected"] == "diplomacy"
    balance = next(t for t in evaluation(exaggerated, "prepare")["reason_trace"] if t["factor"] == "belief:military_balance")
    assert balance["detail"]["enemy_strength"] == 100


def test_overconfidence_optimism_and_risk_tolerance_can_support_a_dangerous_choice():
    gamble = option("gamble", "war_preparation", {"security": .5, "prestige": .5, "fiscal": -.2},
                    cost=150, risk=.9, normal=False, emergency=True)
    cautious = view("finance", traits={"risk_tolerance": .1}, biases={})
    confident = view("military", traits={"risk_tolerance": .95},
        biases={"overconfidence": .9, "optimism": .9, "nationalism": .9})
    assert decide(cautious, [hold(), gamble])["selected"] == "hold"
    result = decide(confident, [hold(), gamble])
    assert result["selected"] == "gamble"
    assert evaluation(result, "gamble")["eligible"]
    factors = {t["factor"] for t in result["reason_trace"]}
    assert {"bias:overconfidence", "bias:optimism", "bias:nationalism", "perceived_risk"} <= factors


def test_normal_borrowing_limit_is_not_a_risk_or_political_veto():
    action = option("large-project", outcomes={"prosperity": .9}, cost=10, risk=.1,
                    normal=False, emergency=True)
    result = decide(view("industry"), [hold(), action])
    assert result["selected"] == "large-project"
    assert evaluation(result, "large-project")["eligibility_reasons"] == []


@pytest.mark.parametrize("constraint", ["resources", "finance", "authority", "delegation"])
def test_unavailable_resources_finance_and_authority_are_real_constraints(constraint):
    action = option("action", outcomes={"prosperity": 1, "security": 1, "prestige": 1})
    if constraint == "resources": action["financial"]["physical_feasibility"]["feasible"] = False
    elif constraint == "finance":
        action["financial"]["normal_financial_feasibility"]["feasible"] = False
        action["financial"]["emergency_financial_feasibility"]["feasible"] = False
    elif constraint == "authority": action["institutional_feasibility"]["feasible"] = False
    else: action["required_authority"] = ["not_delegated"]
    result = decide(view(), [hold(), action])
    assert result["selected"] == "hold"
    assert not evaluation(result, "action")["eligible"]
    assert evaluation(result, "action")["eligibility_reasons"]


def test_same_seed_view_and_options_reproduce_every_score_and_trace():
    actor = view(biases={"optimism": .7})
    choices = [hold(), option("education", outcomes={"education": .6}, cost=20, risk=.1)]
    assert decide(actor, choices, 19) == decide(actor, choices, 19)


def test_option_order_does_not_change_rng_assignment_or_selection():
    choices = [hold(), option("a", outcomes={"prosperity": .2}), option("z", outcomes={"fiscal": .4})]
    assert decide(view(), choices, 12) == decide(view(), list(reversed(choices)), 12)


def test_detached_input_views_options_and_reports_remain_unchanged():
    actor = view(groups=["g"])
    actor["groups"] = [{"id": "g", "interests": {"prosperity": .8}}]
    choices = [hold(), option("invest", outcomes={"prosperity": .8}, cost=30)]
    before = copy.deepcopy((actor, choices))
    result = decide(actor, choices)
    result["reason_trace"].append({"factor": "outside", "contribution": 999})
    assert (actor, choices) == before


def test_explanation_contributions_exactly_decompose_every_score():
    actor = view("military", biases={"fear": .7, "overconfidence": .6, "groupthink": .4},
                 traits={"risk_tolerance": .8, "ideological_commitment": .7})
    actor["relationships"] = {"advisor": {"respect": .8, "trust": .7}}
    proposal = option("prepare", "war_preparation", {"security": .5, "prestige": .3, "ideology": .2},
        cost=150, risk=.7, belief_context={"own_strength": 100, "enemy_strength": 150, "threat": .8},
        reactions={"advisor": {"expected_support": .6}})
    result = decide(actor, [hold(), proposal])
    for e in result["evaluations"]:
        assert sum(t["contribution"] for t in e["reason_trace"]) == pytest.approx(e["score"], abs=1e-12)
    assert result["reason_trace"] == evaluation(result, result["selected"])["reason_trace"]


def test_raw_true_cost_risk_and_secret_state_never_influence_decision():
    action = option("invest", outcomes={"prosperity": .6}, cost=10, risk=.1)
    original = decide(view(), [hold(), action])
    contaminated = copy.deepcopy(action)
    contaminated["financial"]["true_state"] = {"cost": 10**12, "risk": 1, "secret_enemy_strength": 9999}
    contaminated["financial"]["expected_consequences"] = {"default_risk": 1, "debt_increase": 10**12}
    contaminated["actual_cost"] = 10**12
    contaminated["actual_risk"] = 1
    assert decide(view(), [hold(), contaminated]) == original


def test_pure_module_imports_no_world_engine_sqlite_or_storage():
    tree = ast.parse(inspect.getsource(npc_decision))
    imported = [n.module if isinstance(n, ast.ImportFrom) else a.name
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [None])]
    assert set(imported) <= {"copy", "math"}
    assert not any(name in inspect.getsource(npc_decision) for name in ("sqlite3", "from .engine", "from .models"))


def test_group_membership_creates_distinct_interests_without_one_country_personality():
    groups = [{"id": "landowners", "interests": {"prosperity": -1}},
              {"id": "manufacturers", "interests": {"prosperity": 1}}]
    reform = option("reform", outcomes={"prosperity": .8, "fiscal": -.1}, cost=20)
    actor_a, actor_b = view(groups=["landowners"]), view(groups=["manufacturers"])
    actor_a["groups"] = actor_b["groups"] = groups
    a, b = decide(actor_a, [hold(), reform]), decide(actor_b, [hold(), reform])
    assert evaluation(b, "reform")["score"] > evaluation(a, "reform")["score"]
    assert any(t["factor"] == "group:landowners:prosperity" for t in evaluation(a, "reform")["reason_trace"])
    assert not any("manufacturers" in t["factor"] for t in evaluation(a, "reform")["reason_trace"])


def test_explicit_signed_preferences_can_reject_industrialization():
    actor = view("industry", preferences={"prosperity": -1})
    industrialize = option("industrialize", "industrialization", {"prosperity": .8})
    assert decide(actor, [hold(), industrialize])["selected"] == "hold"


def test_memory_interpretation_not_hidden_outcome_affects_choice():
    actor = view("military", biases={"historical_analogy": .9, "previous_success": .9})
    actor["memories"] = [{"id": "old-war", "kind": "war_preparation", "confidence": 1, "salience": 1,
        "interpretation": {"success": .9, "cause": "leadership courage"}, "actual_outcome": "catastrophe"}]
    action = option("prepare", "war_preparation", {"security": .15}, cost=10, risk=.3)
    successful_narrative = decide(actor, [hold(), action])
    corrected = copy.deepcopy(actor)
    corrected["memories"][0]["actual_outcome"] = "success"
    assert decide(corrected, [hold(), action]) == successful_narrative
    corrected["memories"][0]["interpretation"]["success"] = -.9
    failed_narrative = decide(corrected, [hold(), action])
    assert evaluation(successful_narrative, "prepare")["score"] > evaluation(failed_narrative, "prepare")["score"]
    assert any(t["factor"] == "bias:historical_analogy_bias" for t in evaluation(successful_narrative, "prepare")["reason_trace"])


def test_sunk_cost_is_named_and_can_increase_willingness_to_continue():
    actor = view(biases={"sunk_cost": .9})
    action = option("continue", "industrialization", {"prosperity": .1}, cost=40, risk=.3)
    actor["memories"] = [{"id": "investment", "kind": "industrialization", "confidence": .9, "salience": 1,
        "interpretation": {"success": -.4, "committed_resources": 400, "continuation": True}}]
    result = decide(actor, [hold(), action])
    contribution = next(t for t in evaluation(result, "continue")["reason_trace"] if t["factor"] == "bias:sunk_cost_tendency")
    assert contribution["contribution"] > 0


def test_loyalty_affection_and_respect_do_not_require_approval_of_every_policy():
    actor = view("finance", traits={"ideological_commitment": .9, "ambition": .3})
    actor["relationships"]["ruler"] = {"loyalty": .95, "respect": .95, "affection": .95,
        "trust": .95, "perceived_legitimacy": .95, "obligation": .95}
    offensive = option("offensive", "war_preparation", {"fiscal": -.8, "ideology": -.8}, cost=100, risk=.8)
    response = npc_decision.reaction(actor, offensive)
    kinds = {a["kind"] for a in response["actions"]}
    assert "dissent" in kinds and "reconsider" in kinds
    assert "resign_threat" in kinds
    assert "obedient_execution" not in kinds


def test_no_authority_means_lobby_or_reconsider_not_secret_execution():
    actor = view("education", authority=[])
    school = option("school", outcomes={"education": .8}, required_authority=["education_policy"])
    response = npc_decision.reaction(actor, school)
    assert response["primary_action"] == "lobby"
    assert not response["execution_authorized"]
    assert not any(a["can_execute"] for a in response["actions"])


def test_high_ambition_low_ruler_respect_seeks_power_without_creating_coup():
    actor = view("military", authority=[], traits={"ambition": .95})
    actor["relationships"]["ruler"] = {"respect": .1, "trust": .3, "perceived_legitimacy": .3, "obligation": .5}
    response = npc_decision.reaction(actor, option("arms", "military", {"security": .5}))
    kinds = {a["kind"] for a in response["actions"]}
    assert {"request_delegated_authority", "coalition"} <= kinds
    assert not any("coup" in k for k in kinds)
    assert not any(a["can_execute"] for a in response["actions"])


def test_local_action_requires_express_scope_and_relevant_local_alternative():
    actor = view("local_governor", authority=["local_action"])
    local = option("local-relief", "local_action", outcomes={"prosperity": .5}, tags=["local"])
    response = npc_decision.reaction(actor, local)
    assert any(a["kind"] == "local_action" and a["can_execute"] for a in response["actions"])
    national = option("national-relief", outcomes={"prosperity": .5})
    assert not npc_decision.reaction(actor, national)["execution_authorized"]


def test_small_rng_variation_cannot_overturn_a_large_substantive_difference():
    policy = option("policy", outcomes={"prosperity": .8}, cost=1)
    for seed in range(100):
        result = decide(view(), [hold(), policy], seed)
        assert result["selected"] == "policy"
        for e in result["evaluations"]:
            for t in e["reason_trace"]:
                if t["factor"] == "bounded_deliberation_variation": assert abs(t["contribution"]) <= .02


def test_neutral_status_quo_available_and_no_options_has_no_secret_policy():
    assert decide(view(authority=[]), [hold()])["selected"] == "hold"
    assert decide(view(), [])["selected"] is None


def test_no_fixed_war_or_peace_bonus_without_perceived_benefit_cost_or_context():
    # Id-ordered RNG draws are identical because only the kind labels differ.
    preparation = option("same-id", "war_preparation")
    diplomacy = option("same-id", "diplomacy")
    assert evaluation(decide(view(), [preparation]), "same-id")["score"] == evaluation(decide(view(), [diplomacy]), "same-id")["score"]


def test_negative_optimism_represents_pessimism_and_raises_perceived_downside_weight():
    action = option("build", "industrialization", {"prosperity": .4}, cost=20, risk=.6)
    optimistic = decide(view(biases={"optimism": .8}), [hold(), action])
    pessimistic = decide(view(biases={"optimism": -.8}), [hold(), action])
    assert evaluation(optimistic, "build")["score"] > evaluation(pessimistic, "build")["score"]


def test_illegal_duplicate_option_ids_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        decide(view(), [hold(), hold()])


def test_another_actor_decision_cannot_replace_a_ministers_own_assessment():
    ruler, minister = view("military", id="ruler"), view("finance", id="minister")
    action = option("war", "war_preparation", {"security": .8, "fiscal": -.8}, cost=100, risk=.7)
    ruling = decide(ruler, [hold(), action])
    assert npc_decision.reaction(minister, action, ruling) == npc_decision.reaction(minister, action)


def test_local_delegation_cannot_bypass_an_explicit_national_scope_requirement():
    actor = view("local_governor", authority=["local_action"])
    action = option("foreign-treaty", "diplomacy", {"prestige": .8}, tags=["local"], required_authority=["foreign_policy"])
    assert not npc_decision.reaction(actor, action)["execution_authorized"]


def test_authority_does_not_create_nonexistent_resources_or_financing():
    actor = view(authority=["execute_policy"])
    impossible = option("impossible", outcomes={"prosperity": 1}, physical=False)
    response = npc_decision.reaction(actor, impossible)
    assert not response["execution_authorized"]
    assert not any(a["can_execute"] for a in response["actions"])


@pytest.mark.parametrize("bias,factor", [
    ("enemy_underestimation", "bias:enemy_underestimation"),
    ("ally_overestimation", "bias:ally_overestimation"),
    ("historical_analogy_bias", "bias:historical_analogy_bias"),
    ("sunk_cost_tendency", "bias:sunk_cost_tendency"),
    ("group_conformity", "bias:group_conformity"),
])
def test_persisted_canonical_bias_names_actually_affect_named_decision_factors(bias, factor):
    from worldline import npc_state
    state = npc_state.initialize_state()
    npc_state.add_person(state, "official", "fictional", "Official", role="military",
                         biases={bias: .9}, authority=["war_preparation"])
    actor = npc_state.actor_view(state, "official", "1897-01-01")
    actor["memories"] = [{"event_id": "war-memory", "kind": "war_result", "tags": ["war"],
        "effective_weight": .8, "emotional_valence": .7,
        "interpretation": {"success": .7, "committed_resources": 400, "continuation": True}}]
    actor["relationships"]["colleague"] = {"trust": .8, "respect": .8}
    action = option("prepare", "war_preparation", {"security": .5, "prestige": .4}, risk=.6,
        tags=["war"], belief_context={"own_strength": 100, "enemy_strength": 130, "ally_strength": 30, "threat": .8},
        reactions={"colleague": {"expected_support": .8}})
    result = decide(actor, [hold(), action])
    contribution = next(t for t in evaluation(result, "prepare")["reason_trace"] if t["factor"] == factor)
    assert contribution["contribution"] > 0
    neutral = copy.deepcopy(actor)
    neutral["person"]["biases"][bias] = 0
    assert evaluation(result, "prepare")["score"] > evaluation(decide(neutral, [hold(), action]), "prepare")["score"]


@pytest.mark.parametrize("trait,factor", [
    ("prestige_sensitivity", "trait:prestige_sensitivity"),
    ("fear_sensitivity", "trait:fear_sensitivity"),
    ("self_interest", "trait:self_interest"),
    ("patience", "trait:patience"),
    ("impulsiveness", "trait:impulsiveness"),
])
def test_requested_personality_traits_are_effective_with_real_state_schema(trait, factor):
    from worldline import npc_state
    state = npc_state.initialize_state()
    npc_state.add_person(state, "official", "fictional", "Official", traits={trait: .9})
    actor = npc_state.actor_view(state, "official", "1897-01-01")
    action = option("prepare", "war_preparation", {"security": .4, "prosperity": .5, "education": .5,
        "prestige": .5, "group_interest": .5}, risk=.6, tags=["development"],
        belief_context={"own_strength": 100, "enemy_strength": 100, "threat": .8})
    result = decide(actor, [hold(), action])
    assert any(t["factor"] == factor and t["contribution"] != 0 for t in evaluation(result, "prepare")["reason_trace"])


def test_stale_low_confidence_reports_reduce_military_certainty_and_show_reason():
    actor = view("military")
    actor["report_metadata"] = {"diplomacy": {"confidence": .95, "reliability": .9, "age_days": 0},
                                "economy": {"confidence": .9, "reliability": .9, "age_days": 0}}
    action = option("prepare", "war_preparation", {"security": .1}, cost=30, risk=.4,
                    belief_context={"own_strength": 500, "enemy_strength": 100, "threat": .8})
    fresh = decide(actor, [hold(), action])
    actor["report_metadata"]["diplomacy"].update(confidence=.3, age_days=1500)
    actor["report_metadata"]["economy"].update(confidence=.3, age_days=1500)
    stale = decide(actor, [hold(), action])
    assert evaluation(fresh, "prepare")["score"] > evaluation(stale, "prepare")["score"]
    assert any(t["factor"] == "information:staleness_uncertainty" for t in evaluation(stale, "prepare")["reason_trace"])


def test_actual_memory_dimensions_and_effective_weight_preserve_fiscal_warning_sign():
    actor = view("finance")
    actor["memories"] = [{"event_id": "mobilization", "category": "military_treatment", "tags": ["military_treatment"],
        "effective_weight": .8, "emotional_valence": -.2, "reliability": .9,
        "interpretation": {"lesson": "Civilian labour and funding cost resources", "fiscal": -.2,
                           "prosperity": -.1, "tags": ["war"]}}]
    action = option("prepare", "war_preparation", {"fiscal": -.5, "prosperity": -.3}, tags=["war"])
    result = decide(actor, [hold(), action])
    term = next(t for t in evaluation(result, "prepare")["reason_trace"] if t["factor"] == "memory:mobilization:fiscal")
    assert term["contribution"] < 0
    faded = copy.deepcopy(actor)
    faded["memories"][0]["effective_weight"] = .01
    assert evaluation(decide(faded, [hold(), action]), "prepare")["score"] > evaluation(result, "prepare")["score"]


def test_own_institutional_memories_are_separate_bounded_trace_factors():
    actor = view("military", groups=["army"])
    actor["institutional_memories"] = {"army": [{"event_id": "victory", "tags": ["war"],
        "effective_weight": .8, "perceived_result": .8, "interpretation": {"security": .4, "tags": ["war"]}}]}
    action = option("prepare", "war_preparation", {"security": .5}, tags=["war"])
    result = decide(actor, [hold(), action])
    assert any(t["factor"].startswith("memory:institution:army:victory") for t in evaluation(result, "prepare")["reason_trace"])


def test_real_government_officeholder_and_scope_drive_reaction():
    from worldline import npc_state
    state = npc_state.initialize_state()
    npc_state.add_person(state, "actual-ruler", "fictional", "Ruler", authority=["economic_policy"])
    npc_state.add_person(state, "minister", "fictional", "Minister", role="finance",
        authority=["economic_policy"], traits={"personal_loyalty": .9, "institutional_loyalty": .9})
    npc_state.configure_government(state, "fictional", "actual-ruler", ["minister"])
    actor = npc_state.actor_view(state, "minister", "1897-01-01")
    actor["relationships"]["actual-ruler"] = {"trust": .9, "respect": .8, "perceived_legitimacy": .9, "obligation": .9}
    action = option("balanced", outcomes={"fiscal": .5})
    response = npc_decision.reaction(actor, action)
    assert response["relationships_used"]["ruler"] == "actual-ruler"
    assert response["execution_authorized"]
    assert response["primary_action"] == "obedient_execution"


def test_emergency_feasibility_has_named_zero_weight_constraint_trace():
    action = option("gamble", outcomes={"security": .6}, normal=False, emergency=True, risk=.7)
    result = decide(view("military"), [hold(), action])
    term = next(t for t in evaluation(result, "gamble")["reason_trace"] if t["factor"] == "constraint:financing_access")
    assert term["contribution"] == 0
    assert term["detail"]["normal_financial_feasibility"] is False
    assert term["detail"]["emergency_financial_feasibility"] is True


def test_hundreds_of_enduring_memories_do_not_create_unbounded_score_multipliers():
    actor = view(abilities={"intelligence": .5})
    action = option("invest", "industrialization", {"prosperity": .2})
    neutral_score = evaluation(decide(actor, [hold(), action]), "invest")["score"]
    actor["memories"] = [{"event_id": f"past-{index}", "tags": ["industrialization"],
        "effective_weight": 1, "perceived_result": 1, "interpretation": {}}
        for index in range(1000)]
    result = decide(actor, [hold(), action])
    assert evaluation(result, "invest")["score"] - neutral_score <= .60000001
    assert any(t["detail"].get("attention_fraction", 1) < 1 for t in evaluation(result, "invest")["reason_trace"])
