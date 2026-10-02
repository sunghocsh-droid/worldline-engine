"""Five focused, actually funded innovation experiments using production APIs.

This is a project-window experiment, not an accelerated full-world campaign.
It deliberately omits economic settlement, military upkeep, autonomous councils
and SQLite writes. It cannot establish long-war sustainability or performance.
"""
import argparse
import copy
import hashlib
import json
import random
import time
from pathlib import Path

from . import economy, projects, npc, npc_state, crisis, crisis_response, military, military_state
from .models import canonical_date
from . import military_innovation as innovation
from .benchmark import reference_world

SEED = 941


def research_world(profile="early_industrial", seed=SEED, *, training=.65,
                   organization=.55, mission_culture=.1, industrial_capability=None):
    """Explicit fictional starting endowment; nothing is replenished mid-run."""
    w = reference_world(profile)
    w.date = "1700-01-01"
    w.economy["last_settlement_date"] = w.date
    w.countries["a"].treasury = 15_000
    w.economy["regions"]["ra"]["stocks"]["raw_materials"] = 2_000.
    if industrial_capability is not None:
        for sector in w.economy["regions"]["ra"]["sectors"].values():
            sector["technology"].update(installed=True, access=True,
                adoption=industrial_capability, domestic_capability=industrial_capability)
    economy.enable_stabilization(w)
    config = npc_state.initialize_state()
    npc_state.add_person(config, "officer", "a", "Synthetic trial officer", region="ra")
    npc_state.add_person(config, "old_guard", "a", "Synthetic institutional observer", region="ra",
        traits={"institutional_loyalty": .8, "patience": .4})
    npc_state.add_person(config, "distant", "b", "Uninformed foreign official", region="rb")
    npc_state.configure_government(config, "a", "officer", ["old_guard"], autonomous=False)
    npc_state.add_group(config, "command-institution", "a", "Synthetic command institution", kind="military_branch",
        members=["old_guard"], influence=.8, interests={"military_innovation": 2*mission_culture-1})
    npc.initialize(w, config)
    crisis.initialize(w, config={"settings": {"auto_enabled": False}}, rng=random.Random(seed))
    military_state.initialize(w, random.Random(seed))
    f = w.military_state["formations"]["a"]
    f["training"] = training
    f["doctrine"].update(staff_capability=organization, subordinate_initiative=mission_culture,
        mission_oriented_command=mission_culture, planning_flexibility=mission_culture,
        punishment_risk_culture=1-mission_culture)
    return w


def _opinion(w):
    return innovation.institutional_response(npc_state.actor_view(w.npc_state, "old_guard", w.date),
        {"novelty": .6, "training_burden": .5, "expected_benefit": .6})


def _funded_stage(w, programme_id, stage):
    project = innovation.begin_stage(w, programme_id, stage)
    project = w.projects[project["id"]]
    # The existing common daily project clock owns all delay and resource checks.
    rng = random.Random(SEED)
    for _ in range(sum(s["days"] for s in project["stages"])):
        previous = w.date
        w.date = military_state.after(w.date, 1)
        projects.tick(w, {"previous_date": previous, "date": w.date, "days": 1}, rng)
    if project["status"] != "completed":
        raise RuntimeError("Focused innovation project did not actually complete: " + project["id"])
    military.publish(w)  # Actual measured trial -> queued report -> actual received memory.
    issues = projects.validate(w) + innovation.validation_issues(w)
    if issues:
        raise ValueError("; ".join(issues))
    return project


def _programme(w, id, concept, *, requirements=None, complexity=.3, parent=None,
               final="evaluation", effects=None, institutional_review=False):
    innovation.propose(w, id, "a", "ra", concept, requirements=requirements,
        complexity=complexity, proposer="officer", observers=["officer", "old_guard"],
        parent_innovation=parent, capability_effects=effects)
    quote = innovation.assess(w, id)
    before = copy.deepcopy(w.military_state["formations"]["a"])
    for stage in innovation.STAGES[1:innovation.STAGES.index(final)+1]:
        p = w.military_state["innovations"][id]
        if stage == "doctrine_development" and not p["trial_evidence"]["successful"]:
            break  # A failed experiment is not silently promoted to usable capability.
        if stage == "training" and institutional_review:
            npc_state.evaluate_ruler(w.npc_state, "old_guard", "officer", w.date)
            innovation.review_institution(w, id, "old_guard")
        _funded_stage(w, id, stage)
    p = w.military_state["innovations"][id]
    return {"programme": copy.deepcopy(p), "initial_quote": quote,
        "formation_before": before, "formation_after": copy.deepcopy(w.military_state["formations"]["a"]),
        "stage_projects": {s: copy.deepcopy(w.projects[key]) for s, key in p["stage_projects"].items()}}


def _account(w, starting_date="1700-01-01"):
    records = copy.deepcopy(w.npc_state["records"])
    return {"starting_date": starting_date, "end_date": w.date,
        "elapsed_days": (canonical_date(w.date)-canonical_date(starting_date)).days,
        "initial_treasury": 15_000, "remaining_treasury": w.countries["a"].treasury,
        "initial_research_raw_materials": 2_000.,
        "remaining_research_raw_materials": w.economy["regions"]["ra"]["stocks"]["raw_materials"],
        "actual_project_spending": sum(p["spent"] for p in w.projects.values()),
        "objective_trial_records": records,
        "received_observer_memories": copy.deepcopy(w.npc_state["memories"]["old_guard"]),
        "uninformed_foreign_memories": copy.deepcopy(w.npc_state["memories"]["distant"]),
        "unreceived_reports": sum(m["status"] == "queued" for m in w.crisis_state["messages"].values())}


def run_cases(seed=SEED):
    """Return actual results, including failures; do not select RNG for success."""
    cases = {}
    w = research_world(seed=seed, training=.35, organization=.15)
    trials = []
    for index in range(3):
        id = "alternate-volley:" + str(index+1)
        trials.append(_programme(w, id, "alternate_volley_fire",
            requirements={"capabilities": {"training": .5, "organization": .5}},
            parent=None if index == 0 else "alternate-volley:" + str(index)))
    cases["alternate_volley_weak_organization_repeated_trials"] = {
        "explanation": "돈과 반복 시험만으로 미훈련·미조직 부대를 자동으로 유능하게 만들지 않습니다.",
        "trials": trials, "accounting": _account(w)}

    paired = {}
    for label, culture in (("low_initiative", .05), ("high_initiative", .7)):
        w = research_world("industrial", seed, mission_culture=culture, industrial_capability=.8)
        before = _opinion(w)
        result = _programme(w, "mission-command", "mission_oriented_command",
            requirements={"capabilities": {"training": .4, "organization": .4, "communications": .25}},
            final="diffusion", institutional_review=True, effects={"training": .15, "doctrine": {
                "subordinate_initiative": .2, "mission_oriented_command": .2,
                "planning_flexibility": .15, "punishment_risk_culture": -.15}})
        paired[label] = {"result": result, "observer_before": before,
            "observer_after": _opinion(w), "accounting": _account(w)}
    cases["mission_command_low_and_high_culture"] = {
        "explanation": "같은 기술·예산·종자에서도 기존 조직문화는 보존되며, 성공한 시험 뒤에도 실제 교육·도입 기간을 거칩니다.",
        "variants": paired}

    armored = {}
    for label, profile, capability in (("weak", "preindustrial", .05),
            ("intermediate", "early_industrial", .5), ("strong", "industrial", .95)):
        w = research_world(profile, seed, industrial_capability=capability)
        armored[label] = {"profile": profile, "initial_installed_adoption_and_domestic_capability": capability,
            "result": _programme(w, "armoured-concept", "mobile_armoured_force"), "accounting": _account(w)}
    cases["armoured_concept_weak_intermediate_strong_foundations"] = {
        "explanation": "연도가 아니라 엔진·변속기·궤도·연료·정밀가공 등의 실효 능력으로 시험 가능성과 성공을 구분합니다.",
        "variants": armored}

    w = research_world("industrial", seed, training=1., organization=1., industrial_capability=.95)
    before = _opinion(w)
    ruler_before = npc_state.evaluate_ruler(w.npc_state, "old_guard", "officer", w.date)
    trials = [_programme(w, "successful-trial-memory" + ("" if index == 0 else ":"+str(index+1)),
        "staff_planning", complexity=0.) for index in range(3)]
    ruler_after = npc_state.evaluate_ruler(w.npc_state, "old_guard", "officer", w.date)
    cases["measured_trial_memory"] = {"explanation": "제안 자체가 아니라 실제 측정 결과를 전달받은 관찰자의 기억만 의견에 반영됩니다. 난수 결과를 성공으로 덮어쓰지 않습니다.",
        "result": trials[0], "trials": trials, "observer_before": before, "observer_after": _opinion(w),
        "ruler_relationship_before": ruler_before, "ruler_relationship_after": ruler_after, "accounting": _account(w)}

    w = research_world("preindustrial", seed, industrial_capability=.05)
    before = _opinion(w)
    ruler_before = npc_state.evaluate_ruler(w.npc_state, "old_guard", "officer", w.date)
    trials = [_programme(w, "failed-trial-memory" + ("" if index == 0 else ":"+str(index+1)),
        "mobile_armoured_force") for index in range(3)]
    ruler_after = npc_state.evaluate_ruler(w.npc_state, "old_guard", "officer", w.date)
    innovation.propose(w, "next-armoured-proposal", "a", "ra", "mobile_armoured_force",
        proposer="officer", parent_innovation="failed-trial-memory")
    _funded_stage(w, "next-armoured-proposal", "feasibility_study")
    # The actual completed study exists in the causal Event pipeline. Deliver
    # its real report explicitly; no synthetic ruler achievement is invented.
    study_event = next(event for event in reversed(list(w.crisis_state["events"].values()))
        if event["actual_facts"].get("innovation_id") == "next-armoured-proposal")
    crisis_response.queue_report(w, study_event["event_id"], "old_guard", direct=True,
        source="actual completed feasibility study", emotional_valence=0, personal_impact=0)
    crisis_response.deliver_due(w)
    next_review = innovation.review_institution(w, "next-armoured-proposal", "old_guard")
    cases["failed_trial_memory_and_next_proposal"] = {
        "explanation": "실제로 보고받은 실패가 다음 제안에 대한 판단을 바꿀 수 있지만 새 아이디어를 정치적으로 자동 취소하지는 않습니다.",
        "result": trials[0], "trials": trials, "observer_before": before, "observer_after": _opinion(w),
        "ruler_relationship_before": ruler_before, "ruler_relationship_after": ruler_after,
        "next_proposal_review": next_review,
        "next_proposal_transition_quote": innovation.stage_duration(w, "next-armoured-proposal", "training", 180),
        "next_proposal_state": copy.deepcopy(w.military_state["innovations"]["next-armoured-proposal"]),
        "accounting": _account(w)}
    return {"benchmark_schema": 1, "seed": seed, "mode": "focused_funded_project_windows",
        "scope": {"production_common_daily_project_clock": True, "finite_actual_financing_and_raw_materials": True,
            "actual_trial_reports_and_received_memories": True, "full_daily_engine_sqlite": False,
            "monthly_economy_settlement": False, "military_upkeep_and_combat": False,
            "autonomous_npc_scheduling": False, "warning": "장기 전쟁의 지속가능성이나 전체 엔진 속도를 입증하는 benchmark가 아닙니다."},
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob("*.py"))}, "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="benchmarks/military/innovation_results.json")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    start = time.perf_counter()
    result = run_cases(args.seed)
    result["wall_seconds"] = time.perf_counter()-start
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(path), "cases": len(result["cases"]),
        "wall_seconds": result["wall_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
