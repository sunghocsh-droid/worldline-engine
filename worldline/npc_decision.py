"""Bounded, explainable choices using detached actor perceptions only.

This module has no engine, storage, world-state or external information access.
Its options are perceived alternatives; objective financial sidecars, if accidentally
present, are ignored. Execution and newly delivered information belong to the caller.
"""
import copy
import math

VERSION = 1
DIMENSIONS = ("security", "prosperity", "education", "prestige", "fiscal", "ideology", "group_interest")
ROLE_WEIGHTS = {
    "ruler": (1.2, .9, .3, .7, .8, .8, .3),
    "finance": (.4, .9, .2, .2, 1.7, .3, .5),
    "military": (1.7, .3, .1, 1.0, .4, .7, .6),
    "diplomat": (1.0, .6, .1, .8, .5, .6, .3),
    "education": (.3, .7, 1.7, .3, .7, .7, .4),
    "industry": (.5, 1.6, .6, .4, .7, .5, .5),
    "interior": (.8, .8, .3, .3, .8, .7, .6),
}
ROLE_ALIASES = {
    "finance_minister": "finance", "treasury_minister": "finance", "financial": "finance",
    "military_minister": "military", "war_minister": "military", "commander": "military", "general": "military",
    "army": "military", "education_minister": "education", "industry_minister": "industry",
    "foreign_minister": "diplomat", "foreign": "diplomat", "ambassador": "diplomat",
    "head_of_state": "ruler", "leader": "ruler", "monarch": "ruler", "emperor": "ruler",
    "interior_minister": "interior", "governor": "interior", "local_governor": "interior",
}
MILITARY_KINDS = {"war_preparation", "military", "military_expansion", "mobilization"}
BOLD_TAGS = {"war", "war_preparation", "military_expansion", "debt_funded", "industrialization", "large_project", "expansion"}
BIAS_FIELDS = {"sunk_cost": "sunk_cost_tendency", "groupthink": "group_conformity",
               "historical_analogy": "historical_analogy_bias", "underestimated_enemy": "enemy_underestimation",
               "overestimated_ally": "ally_overestimation"}


def _number(value, default=0.0, low=-1.0, high=1.0):
    if type(value) not in (int, float) or not math.isfinite(value):
        return default
    return max(low, min(high, float(value)))


def _level(mapping, key, default=.5):
    return _number(mapping.get(key), default, 0, 1) if isinstance(mapping, dict) else default


def _person(view):
    person = view.get("person")
    if not isinstance(person, dict) or not isinstance(person.get("id"), str):
        raise ValueError("Decision view needs a detached person with a stable id")
    return person


def _role(person):
    role = str(person.get("role", "ruler")).lower().replace(" ", "_").replace("-", "_")
    return ROLE_ALIASES.get(role, role)


def _weights(person):
    weights = dict(zip(DIMENSIONS, ROLE_WEIGHTS.get(_role(person), (.7, .7, .2, .4, .7, .6, .4))))
    preferences = person.get("preferences", {})
    if isinstance(preferences, dict):
        for key, value in preferences.items():
            # Explicit negative values are meaningful: an actor can resist changes
            # judged beneficial by another actor, without modern values being imposed.
            if type(value) in (int, float) and math.isfinite(value):
                weights[key] = weights.get(key, .5) * 1.5 * _number(value)
    return weights


def _bias(person, name):
    biases = person.get("biases", {})
    strength = _number(biases.get(BIAS_FIELDS.get(name, name), biases.get(name)),
                      0, -1 if name == "optimism" else 0, 1)
    abilities = person.get("abilities", {})
    judgment = _level(abilities, "intelligence", _level(abilities, "judgment"))
    return strength * (1 - .4 * judgment)


def _information_quality(view, topics=()):
    metadata = view.get("report_metadata", {})
    if not isinstance(metadata, dict):
        return .7
    if "confidence" in metadata:
        selected = [metadata]
    else:
        selected = [metadata[t] for t in topics if isinstance(metadata.get(t), dict)]
        if not selected:
            selected = [r for r in metadata.values() if isinstance(r, dict)]
    if not selected:
        return .7
    return sum(_level(r, "confidence", .7) * _level(r, "reliability", 1) /
               (1 + _number(r.get("age_days"), 0, 0, 1e9) / 365)
               for r in selected) / len(selected)


def _term(trace, factor, contribution, detail):
    if not math.isfinite(contribution):
        raise ValueError("Non-finite decision contribution")
    if abs(contribution) > 1e-12:
        trace.append({"factor": factor, "contribution": float(contribution), "detail": copy.deepcopy(detail)})


def _flag(data, default=False):
    if isinstance(data, dict):
        return data.get("feasible", data.get("possible", default)) is True
    return data is True


def _eligible(view, option):
    person = _person(view)
    kind = option.get("kind", "")
    if kind == "status_quo":
        return True, []
    reasons = []
    if not person.get("active", True):
        reasons.append("inactive_actor")
    institutional = option.get("institutional_feasibility", {})
    if not _flag(institutional, default=True):
        reasons.append("institutional_authority_missing")
    required = option.get("required_authority", [])
    if isinstance(required, str):
        required = [required]
    authority = person.get("authority", [])
    if required and not set(required) <= set(authority):
        reasons.append("required_delegation_missing")
    financial = option.get("financial")
    if financial is not None:
        if not isinstance(financial, dict):
            raise ValueError("Financial option assessment must be a detached mapping")
        if not _flag(financial.get("physical_feasibility", {}), default=True):
            reasons.append("physical_resources_missing")
        if not (_flag(financial.get("normal_financial_feasibility")) or
                _flag(financial.get("emergency_financial_feasibility"))):
            reasons.append("accessible_financing_exhausted")
    return not reasons, reasons


def _perceived(option):
    financial = option.get("financial") or {}
    perceived = financial.get("perceived_state", {})
    if not isinstance(perceived, dict):
        perceived = {}
    cost = _number(perceived.get("cost"), 0.0, 0.0, 1e15)
    risk = _number(perceived.get("risk"), 0.0, 0.0, 1.0)
    # This scale is part of the actor's perceived budget, not a true treasury read.
    scale = _number(perceived.get("cost_scale", perceived.get("affordability_scale")), 100.0, 1.0, 1e15)
    return {"cost": cost, "risk": risk, "cost_scale": scale,
            "basis": perceived.get("basis", "provided_perception"),
            "bias_factors": copy.deepcopy(perceived.get("bias_factors", []))}


def _member_groups(view, person):
    memberships = person.get("groups", person.get("memberships"))
    groups = view.get("groups", [])
    if isinstance(groups, dict):
        groups = [{"id": key, **value} for key, value in groups.items() if isinstance(value, dict)]
    if memberships is None:
        return [g for g in groups if isinstance(g, dict)]
    ids = {m if isinstance(m, str) else m.get("id") for m in memberships if isinstance(m, (str, dict))}
    return [g for g in groups if isinstance(g, dict) and g.get("id") in ids]


def _memory_value(memory):
    interpretation = memory.get("interpretation", {})
    if not isinstance(interpretation, dict):
        interpretation = {}
    value = interpretation.get("success", interpretation.get("valence", memory.get("perceived_result",
            memory.get("emotional_valence", memory.get("valence", 0)))))
    if isinstance(value, str):
        value = {"success": 1, "failure": -1, "mixed": 0}.get(value, 0)
    return _number(value), interpretation


def _evaluation_context(view):
    """Prepare read-only interpretation inputs once per synchronous decision.

    No state or final scores are cached: the next call sees every newly delivered
    report or edited memory. Original memory indices/order and floating-point
    operation order remain intact in each alternative's attention calculation.
    """
    person = _person(view)
    memories = list(view.get("memories", []))
    for group_id, group_memories in sorted(view.get("institutional_memories", {}).items()):
        for memory in group_memories:
            memories.append({**memory, "institutional_group": group_id})
    prepared = []
    for index, memory in enumerate(memories):
        if not isinstance(memory, dict):
            continue
        value, interpretation = _memory_value(memory)
        tags = set(memory.get("tags", [])) | set(interpretation.get("tags", [])) | {memory.get("kind", memory.get("category", ""))}
        confidence = _number(memory.get("confidence"), .5, 0, 1)
        salience = _number(memory.get("effective_salience", memory.get("salience")), .5, 0, 1)
        reliability = _number(memory.get("reliability"), 1, 0, 1)
        strength = _number(memory.get("effective_weight"), confidence * salience * reliability, 0, 1)
        prepared.append((index, memory, value, interpretation, tags, confidence, salience, reliability, strength))
    return {"weights": _weights(person), "groups": _member_groups(view, person), "memories": prepared}


def _evaluate(view, option, noise=0.0, prepared=None):
    person = _person(view)
    if not isinstance(option, dict) or not isinstance(option.get("id"), str):
        raise ValueError("Decision options need unique string ids")
    eligible, eligibility_reasons = _eligible(view, option)
    prepared = _evaluation_context(view) if prepared is None else prepared
    weights, trace = prepared["weights"], []
    outcomes = option.get("outcomes", {})
    if not isinstance(outcomes, dict):
        raise ValueError("Perceived outcomes must be a mapping")
    for dimension, value in sorted(outcomes.items()):
        _term(trace, f"outcome:{dimension}", _number(value) * weights.get(dimension, .5),
              {"perceived_outcome": _number(value), "actor_weight": weights.get(dimension, .5), "role": person.get("role")})
    perceived = _perceived(option)
    fiscal_weight = abs(weights.get("fiscal", .7))
    finance_ability = _level(person.get("abilities", {}), "finance", .5)
    normalized_cost = perceived["cost"] / (perceived["cost_scale"] + perceived["cost"])
    _term(trace, "perceived_cost", -normalized_cost * (.3 + .6 * fiscal_weight) * (.8 + .4 * finance_ability),
          {"cost": perceived["cost"], "perceived_budget_scale": perceived["cost_scale"],
           "normalized_cost": normalized_cost, "finance_ability": finance_ability})
    tolerance = _level(person.get("traits", {}), "risk_tolerance", .5)
    if "risk_tolerance" in person:
        raw = person["risk_tolerance"]
        tolerance = _number(raw / 100 if type(raw) in (int, float) and raw > 1 else raw, .5, 0, 1)
    risk_penalty = perceived["risk"] * (.6 + .6 * fiscal_weight) * (1.25 - .8 * tolerance) * (.8 + .4 * finance_ability)
    _term(trace, "perceived_risk", -risk_penalty,
          {"risk": perceived["risk"], "risk_tolerance": tolerance, "fiscal_weight": fiscal_weight})
    financial = option.get("financial")
    if isinstance(financial, dict):
        normal = _flag(financial.get("normal_financial_feasibility"))
        emergency = _flag(financial.get("emergency_financial_feasibility"))
        trace.append({"factor": "constraint:financing_access", "contribution": 0.0,
            "detail": {"normal_financial_feasibility": normal, "emergency_financial_feasibility": emergency,
                       "meaning": "Emergency finance permits a dangerous choice; no accessible finance is an execution constraint."}})
    tags = set(option.get("tags", [])) | {option.get("kind", "")}
    bold = bool(tags & BOLD_TAGS)
    if bold:
        _term(trace, "bias:overconfidence", risk_penalty * .45 * _bias(person, "overconfidence"),
              {"discounts": "perceived downside", "strength": _bias(person, "overconfidence")})
        _term(trace, "bias:optimism", risk_penalty * .35 * _bias(person, "optimism"),
              {"discounts": "perceived downside", "strength": _bias(person, "optimism")})
    prestige = _number(outcomes.get("prestige"))
    ideology = _number(outcomes.get("ideology"))
    commitment = _level(person.get("traits", {}), "ideological_commitment", 0)
    _term(trace, "value:ideological_commitment", ideology * .8 * commitment,
          {"alignment": ideology, "commitment": commitment})
    for bias, value, coefficient in (("nationalism", prestige + ideology, .35),
                                     ("religious_conviction", ideology, .55),
                                     ("prestige", prestige, .45), ("revenge", ideology + prestige, .3)):
        _term(trace, f"bias:{bias}", _bias(person, bias) * value * coefficient,
              {"perceived_alignment": value, "strength": _bias(person, bias)})
    traits = person.get("traits", {})
    _term(trace, "trait:prestige_sensitivity", prestige * (_level(traits, "prestige_sensitivity") - .5) * .7,
          {"perceived_prestige": prestige, "sensitivity": _level(traits, "prestige_sensitivity")})
    self_gain = _number(outcomes.get("personal_interest", outcomes.get("group_interest")))
    _term(trace, "trait:self_interest", self_gain * (_level(traits, "self_interest") - .5) * .7,
          {"perceived_personal_interest": self_gain, "strength": _level(traits, "self_interest")})
    _term(trace, "trait:ambition", prestige * (_level(traits, "ambition") - .5) * .25,
          {"perceived_prestige": prestige, "ambition": _level(traits, "ambition")})
    delayed = bool(tags & {"development", "industrialization", "education", "infrastructure", "technology"})
    delay = _number(option.get("delay_months"), 30 if delayed else 0, 0, 1200)
    long_gain = (_number(outcomes.get("prosperity")) + _number(outcomes.get("education"))) / 2
    delay_weight = delay / (delay + 12) if delay else 0
    _term(trace, "trait:patience", long_gain * delay_weight * (_level(traits, "patience") - .5) * .6,
          {"perceived_delay_months": delay, "delayed_gain": long_gain, "patience": _level(traits, "patience")})
    impulsiveness = _level(traits, "impulsiveness")
    _term(trace, "trait:impulsiveness", (impulsiveness - .5) *
          ((risk_penalty * .15 if bold else 0) - long_gain * delay_weight * .35),
          {"strength": impulsiveness, "perceived_delay_months": delay, "bold_action": bold})
    info_quality = _information_quality(view, ("economy",))
    _term(trace, "information:staleness_uncertainty", -(1 - info_quality) *
          (perceived["risk"] * .2 + normalized_cost * .1),
          {"report_quality": info_quality, "basis": "confidence, reliability and report age only"})
    context = option.get("belief_context", {})
    if not isinstance(context, dict):
        context = {}
    known_military = view.get("beliefs", {}).get("military", {})
    if not isinstance(known_military, dict):
        known_military = {}
    threat = _number(context.get("threat", known_military.get("threat")), 0, 0, 1)
    if option.get("kind") in MILITARY_KINDS or "war_preparation" in tags:
        own = _number(context.get("own_strength", known_military.get("own_strength")), 0, 0, 1e15)
        enemy = _number(context.get("enemy_strength", known_military.get("enemy_strength")), 0, 0, 1e15)
        ally = _number(context.get("ally_strength", known_military.get("ally_strength")), 0, 0, 1e15)
        balance = (own + ally - enemy) / max(1, own + ally + enemy)
        confidence = _level(person.get("abilities", {}), "military", .5)
        military_quality = _information_quality(view, ("military", "diplomacy"))
        coefficient = weights.get("security", .7) * threat * (.4 + .4 * confidence) * military_quality
        _term(trace, "belief:military_balance", balance * coefficient,
              {"own_strength": own, "enemy_strength": enemy, "ally_strength": ally,
               "threat": threat, "perceived_balance": balance, "report_quality": military_quality})
        underestimated = enemy * (1 - .6 * _bias(person, "underestimated_enemy"))
        overestimated_ally = ally * (1 + .6 * _bias(person, "overestimated_ally"))
        biased_balance = (own + overestimated_ally - underestimated) / max(1, own + overestimated_ally + underestimated)
        enemy_only_balance = (own + ally - underestimated) / max(1, own + ally + underestimated)
        _term(trace, "bias:enemy_underestimation", (enemy_only_balance - balance) * coefficient,
              {"biased_enemy_strength": underestimated, "strength": _bias(person, "underestimated_enemy")})
        _term(trace, "bias:ally_overestimation", (biased_balance - enemy_only_balance) * coefficient,
              {"biased_ally_strength": overestimated_ally, "strength": _bias(person, "overestimated_ally")})
        _term(trace, "bias:fear", _bias(person, "fear") * threat * .35,
              {"threat": threat, "strength": _bias(person, "fear")})
        _term(trace, "trait:fear_sensitivity", (_level(traits, "fear_sensitivity") - .5) * threat * .45,
              {"threat": threat, "sensitivity": _level(traits, "fear_sensitivity")})
    groups = prepared["groups"]
    for group in sorted(groups, key=lambda g: str(g.get("id", ""))):
        interests = group.get("interests", group.get("preferences", {}))
        if not isinstance(interests, dict):
            continue
        group_outcomes = option.get("group_outcomes", {}).get(group.get("id"), outcomes)
        if not isinstance(group_outcomes, dict):
            group_outcomes = outcomes
        for dimension, weight in sorted(interests.items()):
            value = _number(group_outcomes.get(dimension))
            contribution = value * _number(weight) * .5 / max(1, len(groups))
            _term(trace, f"group:{group.get('id', 'unknown')}:{dimension}", contribution,
                  {"group": group.get("id"), "interest": dimension, "alignment": value, "weight": _number(weight)})
    for index, goal in enumerate(person.get("goals", [])):
        if isinstance(goal, str):
            if goal in tags:
                _term(trace, f"goal:{goal}", .3, {"matched_tag": goal})
        elif isinstance(goal, dict):
            dimension = goal.get("dimension", goal.get("outcome"))
            value = _number(outcomes.get(dimension)) if dimension else float(bool(tags & set(goal.get("tags", []))))
            weight = _number(goal.get("priority", goal.get("weight", .5)), .5, 0, 1)
            direction = _number(goal.get("direction", 1), 1)
            _term(trace, f"goal:{goal.get('id', index)}", value * weight * direction * .6,
                  {"dimension": dimension, "priority": weight, "direction": direction, "alignment": value})
    memories = [entry for entry in prepared["memories"] if entry[4] & tags]
    relevant_weight = 0.0
    for _, memory, _, _, _, _, _, _, strength in memories:
        relevant_weight += strength * (
            .5 if memory.get("institutional_group") else 1)
    attention_budget = 2 + 2 * _level(person.get("abilities", {}), "intelligence", .5)
    attention_fraction = min(1.0, attention_budget / relevant_weight) if relevant_weight else 1.0
    for index, memory, remembered_value, interpretation, _, confidence, salience, reliability, strength in memories:
        memory_id = memory.get("id", memory.get("event_id", index))
        if memory.get("institutional_group"):
            memory_id = f"institution:{memory['institutional_group']}:{memory_id}"
            strength *= .5  # collective narrative is a separate, bounded influence
        strength *= attention_fraction
        _term(trace, f"memory:{memory_id}", remembered_value * strength * .2,
          {"interpreted_valence": remembered_value, "confidence": confidence, "salience": salience,
               "interpretation": interpretation, "attention_fraction": attention_fraction,
               "attention_budget": attention_budget})
        dimension_lesson = 0.0
        for dimension in DIMENSIONS:
            if dimension in interpretation:
                interpreted = _number(interpretation[dimension])
                # A remembered fiscal loss remains a warning about a similar
                # alternative. Multiplying two negative consequences would invert
                # that warning into a spurious bonus.
                contribution = interpreted * abs(_number(outcomes.get(dimension))) * weights.get(dimension, .5) * strength * .4
                dimension_lesson += contribution
                _term(trace, f"memory:{memory_id}:{dimension}", contribution,
                      {"interpretation": interpreted, "perceived_alternative": _number(outcomes.get(dimension)),
                       "effective_memory_weight": strength, "lesson": interpretation.get("lesson", "")})
        _term(trace, "bias:historical_analogy_bias", (remembered_value * strength * .35 + dimension_lesson * .5) * _bias(person, "historical_analogy"),
              {"memory": memory_id, "interpreted_valence": remembered_value, "dimension_lesson": dimension_lesson})
        if remembered_value > 0:
            _term(trace, "bias:previous_success", remembered_value * strength * _bias(person, "previous_success") * .35,
                  {"memory": memory_id, "remembered_success": remembered_value})
        committed = _number(interpretation.get("committed_resources"), 0, 0, 1e15)
        if interpretation.get("continuation") and committed:
            _term(trace, "bias:sunk_cost_tendency", committed / (100 + committed) * _bias(person, "sunk_cost") * .4 * strength,
                  {"memory": memory_id, "perceived_committed_resources": committed})
    reactions = option.get("reactions", {})
    if isinstance(reactions, dict) and reactions:
        known_support = []
        for other, value in sorted(reactions.items()):
            if isinstance(value, dict):
                value = value.get("expected_support", value.get("support", 0))
            relation = view.get("relationships", {}).get(other, {})
            respect = _level(relation, "respect", .5)
            trust = _level(relation, "trust", .5)
            known_support.append(_number(value) * (respect + trust) / 2)
        support = sum(known_support) / len(known_support)
        _term(trace, "social:expected_support", support * .15,
              {"mean_expected_support": support, "known_actors": sorted(reactions)})
        _term(trace, "bias:group_conformity", support * _bias(person, "groupthink") * .35,
              {"mean_expected_support": support, "strength": _bias(person, "groupthink")})
    _term(trace, "bounded_deliberation_variation", noise,
          {"range": [-.02, .02], "purpose": "small tie/persuasion variation, never crisis creation"})
    return {"option": option["id"], "kind": option.get("kind"), "eligible": eligible,
            "execution_eligible": eligible, "eligibility_reasons": eligibility_reasons,
            "score": math.fsum(t["contribution"] for t in trace), "reason_trace": trace,
            "perceived": perceived}


def decide(view, options, rng):
    """Rank perceived alternatives with bounded supplied-RNG deliberation.

    Missing authority or real resources prevents execution, while dangerous but
    accessible emergency financing remains eligible. There is no war/peace bonus.
    """
    person = _person(view)
    if not isinstance(options, (list, tuple)) or not hasattr(rng, "random"):
        raise ValueError("Decision requires ordered detached options and a supplied RNG")
    ids = [o.get("id") for o in options if isinstance(o, dict)]
    if len(ids) != len(options) or len(set(ids)) != len(ids) or not all(isinstance(i, str) for i in ids):
        raise ValueError("Options must have unique string ids")
    evaluations = []
    context = _evaluation_context(view) if options else None
    uncertainty = 1 - _information_quality(view)
    abilities = person.get("abilities", {})
    judgment = _level(abilities, "intelligence", _level(abilities, "judgment", .5))
    for option in sorted(options, key=lambda o: o["id"]):
        eligible, _ = _eligible(view, option)
        noise = (float(rng.random()) * 2 - 1) * .02 * (1 - .5 * judgment) * (.5 + .5 * uncertainty) if eligible else 0
        evaluations.append(_evaluate(view, option, noise, context))
    eligible = [e for e in evaluations if e["eligible"]]
    selected = max(eligible, key=lambda e: (e["score"], e["option"])) if eligible else None
    return {"actor": person["id"], "selected": selected["option"] if selected else None,
            "evaluations": evaluations,
            "reason_trace": copy.deepcopy(selected["reason_trace"]) if selected else [],
            "perceived": copy.deepcopy(selected["perceived"]) if selected else None,
            "basis": {"date": view.get("date"), "information": "detached delivered reports and actor interpretations",
                      "decision_version": VERSION, "report_metadata": copy.deepcopy(view.get("report_metadata", {}))}}


def decide_deterministic(view, options):
    """Rank delivered perceptions with the existing evaluator and no RNG draws."""
    person = _person(view)
    if not isinstance(options, (list, tuple)):
        raise ValueError("Decision requires ordered detached options")
    ids = [o.get("id") for o in options if isinstance(o, dict)]
    if len(ids) != len(options) or len(set(ids)) != len(ids) or not all(isinstance(i, str) for i in ids):
        raise ValueError("Options must have unique string ids")
    context = _evaluation_context(view) if options else None
    evaluations = [_evaluate(view, option, 0.0, context)
                   for option in sorted(options, key=lambda o: o["id"])]
    eligible = [evaluation for evaluation in evaluations if evaluation["eligible"]]
    selected = max(eligible, key=lambda e: (e["score"], e["option"])) if eligible else None
    return {"actor": person["id"], "selected": selected["option"] if selected else None,
            "evaluations": evaluations,
            "reason_trace": copy.deepcopy(selected["reason_trace"]) if selected else [],
            "perceived": copy.deepcopy(selected["perceived"]) if selected else None,
            "basis": {"date": view.get("date"), "information": "detached delivered reports and actor interpretations",
                      "decision_version": VERSION, "report_metadata": copy.deepcopy(view.get("report_metadata", {}))}}


def reaction(view, option, decision=None):
    """Return proposals/reactions within authority; execute no policy or coup."""
    person = _person(view)
    own_decision = decision if isinstance(decision, dict) and decision.get("actor") == person["id"] else {}
    evaluation = next((e for e in own_decision.get("evaluations", []) if e.get("option") == option.get("id")), None)
    if evaluation is None:
        evaluation = _evaluate(view, option)
    score = evaluation["score"]
    authority = list(person.get("authority", []))
    required = option.get("required_authority", [])
    if isinstance(required, str):
        required = [required]
    if not required and option.get("kind") in (
            "economic_policy", "mobilize", "fiscal_action", "war_preparation", "local_action", "diplomatic_engagement"):
        required = [option["kind"]]
    can_execute = (bool(required) and set(required) <= set(authority) or
                   "execute_policy" in authority or not required and "local_action" in authority and "local" in option.get("tags", []))
    can_execute = bool(can_execute and person.get("active", True) and _flag(option.get("institutional_feasibility", {}), True))
    can_execute = can_execute and not any(r in evaluation["eligibility_reasons"] for r in (
        "physical_resources_missing", "accessible_financing_exhausted"))
    ruler = person.get("ruler_id", view.get("ruler_id", "ruler"))
    relationship = view.get("relationships", {}).get(ruler, {})
    loyalty = _level(relationship, "loyalty", (
        _level(relationship, "trust") + _level(relationship, "perceived_legitimacy") +
        _level(relationship, "obligation")) / 3)
    traits = person.get("traits", {})
    personal_loyalty = _level(traits, "personal_loyalty")
    institutional_loyalty = _level(traits, "institutional_loyalty")
    loyalty *= .5 + .5 * personal_loyalty
    duty = loyalty * .6 + institutional_loyalty * .4
    respect = _level(relationship, "respect")
    commitment = _level(person.get("traits", {}), "ideological_commitment", 0)
    ideological_opposition = _number(option.get("outcomes", {}).get("ideology")) < -.4 and commitment >= .6
    ambition = _level(person.get("traits", {}), "ambition", .5)
    actions = []

    def add(kind, detail, executing=False):
        actions.append({"kind": kind, "can_execute": bool(executing and can_execute),
                        "requires_authority": copy.deepcopy(required) if executing else [], "detail": detail})

    if ideological_opposition or score < -.15:
        add("dissent", "Substantive opposition survives affection, respect and loyalty.")
        add("reconsider", "Request another deliberation or a different policy.")
        if ideological_opposition and commitment > .75:
            add("resign_threat", "Strong ideological commitment can make continued office conditional.")
        elif can_execute and duty > .65:
            add("passive_execution", "Institutional duty can produce limited compliance despite disagreement.", True)
    elif can_execute:
        add("obedient_execution" if duty >= .5 else "willing_execution",
            "Delegated authority and the actor's own assessment permit implementation.", True)
    else:
        add("lobby", "Advocate a preferred policy without assuming execution authority.")
    if ambition > .65 and respect < .4:
        add("request_delegated_authority", "Ambition and low respect encourage a request for greater institutional power.")
        add("coalition", "Seek support inside existing institutions; this does not create a coup.")
    elif not can_execute and score < -.15 and ambition > .5:
        add("coalition", "Organize lawful institutional support for reconsideration.")
    if can_execute and "local_action" in authority and "local" in option.get("tags", []):
        add("local_action", "Act only inside the expressly delegated local scope.", True)
    trace = copy.deepcopy(evaluation["reason_trace"])
    return {"actor": person["id"], "option": option.get("id"), "policy_score": score,
            "approval": math.tanh(score), "primary_action": actions[0]["kind"],
            "action": actions[0]["kind"], "actions": actions, "execution_authorized": can_execute,
            "authority_scope": authority, "reason_trace": trace,
            "relationships_used": {"ruler": ruler, "loyalty": loyalty, "respect": respect,
                                   "ideological_opposition": ideological_opposition,
                                   "personal_loyalty": personal_loyalty,
                                   "institutional_loyalty": institutional_loyalty, "institutional_duty": duty}}
