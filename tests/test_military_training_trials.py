"""Supplemental actual training experiment; no production or seed changes.

Run this file directly to write the follow-up raw results. Its predeclared
three-course curriculum is finite: failed courses stay failed, without rerolls.
"""
import argparse
import copy
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from worldline import military_innovation as innovation, military_logistics as logistics
from worldline.military_innovation_benchmark import research_world, _programme, _account

SEED = 941
CURRICULUM = ("staff_basics", "unit_coordination", "fire_control_drills")


def _starting_world(*, strong=False, seed=SEED):
    w = research_world(seed=seed, training=.75 if strong else .35, organization=.65 if strong else .15)
    f = w.military_state["formations"]["a"]
    # Explicit fictional initial equipment/supplies, never replenished later.
    f["equipment_state"]["personal_weapons"] = {"quantity": 100, "condition": 1., "uses_ammunition": True}
    f["supply_state"]["stocks"]["ammunition"] = 100.
    return w


def _fire_trial(w, id):
    receipt = logistics.consume_supply(w, "a", {"ammunition": 10.}, receipt_id="trial-ammunition:"+id)
    if receipt["actual"]["ammunition"] != 10.:
        raise RuntimeError("Actual finite ammunition is insufficient for the planned firing trial")
    result = _programme(w, id, "alternate_volley_fire", requirements={"capabilities": {
        "training": .5, "organization": .5}}, complexity=.3)
    result["actual_firing_supply_receipt"] = receipt
    return result


def run_followup(seed=SEED):
    w = _starting_world(seed=seed)
    opening = copy.deepcopy(w.military_state["formations"]["a"])
    initial = _fire_trial(w, "volley-before-curriculum")
    courses = []
    # These three different modules are planned in advance, rather than repeated
    # until a particular result occurs. All successful modules complete the
    # actual common training/adoption/diffusion stages before their effect.
    for name in CURRICULUM:
        ammunition = logistics.consume_supply(w, "a", {"ammunition": 10.},
            receipt_id="course-ammunition:"+name)
        course = _programme(w, "curriculum:"+name, name, requirements={"capabilities": {
            "literacy": .15, "training": .25}}, complexity=.2, final="diffusion",
            effects={"training": .1, "doctrine": {"staff_capability": .2}})
        course["actual_training_supply_receipt"] = ammunition
        courses.append(course)
    final = _fire_trial(w, "volley-after-curriculum")
    closing = copy.deepcopy(w.military_state["formations"]["a"])
    strong = _starting_world(strong=True, seed=seed)
    reference = _fire_trial(strong, "volley-after-curriculum")
    return {"benchmark_schema": 1, "seed": seed, "mode": "supplemental_funded_project_windows",
        "production_source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/"worldline").glob("*.py"))},
        "experiment_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": {"original_failed_volley_benchmark_preserved": True,
            "production_coefficients_and_sources_unchanged": True, "finite_predeclared_curriculum": list(CURRICULUM),
            "actual_common_training_projects": True, "actual_ammunition_consumption": True,
            "full_economy_settlement_or_military_upkeep_or_sqlite_campaign": False,
            "note": "교육 과정별 실제 시험·완공 기록을 사용하는 집중 실험입니다. 장기 경제·군 유지비를 함께 진행한 캠페인은 아닙니다."},
        "initial_weak_formation": opening, "initial_failed_trial": initial,
        "paid_training_courses": courses, "final_weak_origin_formation": closing,
        "one_retrial_after_fixed_curriculum": final, "weak_origin_accounting": _account(w),
        "strong_initial_reference": {"note": "처음부터 숙련·조직이 갖춰진 별도 초기조건이며 약한 부대를 무료 변환한 결과가 아닙니다.",
            "result": reference, "accounting": _account(strong)},
        "limitations": ["교육 효과는 0~1의 집계된 숙련·참모 능력이며 개별 교관·교과목·훈련장 세부 모형은 없습니다.",
            "세 과정의 시험 난수는 각각 한 번만 사용합니다. 실패는 실제 자료에 남으며 성과를 위해 다시 뽑지 않습니다.",
            "집중 프로젝트 시계 밖의 전체 경제 결산·일별 군 유지비·SQLite 저장 성능은 검증하지 않습니다."]}


def test_actual_paid_training_can_change_readiness_and_retrial_without_grant_or_reroll():
    result = run_followup()
    initial = result["initial_failed_trial"]["programme"]["trial_evidence"]
    final = result["one_retrial_after_fixed_curriculum"]["programme"]["trial_evidence"]
    assert not initial["successful"]
    assert {"training", "organization"} <= set(initial["technical_gaps"])
    assert len(result["paid_training_courses"]) == len(CURRICULUM)
    successful = [course for course in result["paid_training_courses"] if course["programme"]["trial_evidence"]["successful"]]
    assert successful
    opening, closing = result["initial_weak_formation"], result["final_weak_origin_formation"]
    assert closing["training"] > opening["training"]
    assert closing["doctrine"]["staff_capability"] > opening["doctrine"]["staff_capability"]
    assert final["probability"] > initial["probability"]
    assert not final["technical_gaps"]
    for course in successful:
        assert course["programme"]["completed_stages"] == list(innovation.STAGES[1:])
        assert course["stage_projects"]["training"]["elapsed_days"] >= 180
        assert course["stage_projects"]["diffusion"]["elapsed_days"] >= 730
    assert closing["manpower"] == opening["manpower"]
    assert closing["equipment_state"] == opening["equipment_state"]
    assert opening["supply_state"]["stocks"]["ammunition"]-closing["supply_state"]["stocks"]["ammunition"] == 50.
    account = result["weak_origin_accounting"]
    assert account["remaining_treasury"] == account["initial_treasury"]-account["actual_project_spending"]
    assert account["remaining_research_raw_materials"] < account["initial_research_raw_materials"]


def test_supplemental_fixed_curriculum_and_one_retrial_are_seed_reproducible():
    assert run_followup() == run_followup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="benchmarks/military/volley_training_followup.json")
    args = parser.parse_args()
    start = time.perf_counter()
    result = run_followup()
    result["wall_seconds"] = time.perf_counter()-start
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(path), "wall_seconds": result["wall_seconds"]}))


if __name__ == "__main__":
    main()
