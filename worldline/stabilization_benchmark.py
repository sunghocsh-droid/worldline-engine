"""Credit/labour audit replay and Korean report; no target GDP or AI decisions.

The calibrated checkpoint's raw measurements are the comparison reference. New
cases call the same daily project/monthly economy functions as the game engine.
This research checkpoint intentionally omits gameplay SQLite/ledger IO.
"""
import argparse
import copy
import csv
import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import economy, credit, labour, projects
from .benchmark import (Replay, PROFILES, SEED, EXTRA_PATH, reference_world,
                        policy_for, technology_budget, measure, verify_explanation)

PATHS = ("baseline", "balanced", "military_heavy", "debt_boom", EXTRA_PATH)
NAMES = {"baseline": "기준 유지", "balanced": "균형 개발", "military_heavy": "군비 과다",
         "debt_boom": "차입 개발", EXTRA_PATH: "균형 개발+기술 도입"}
FACTORS = ("skilled_labour", "technology", "infrastructure", "market_access",
           "demand", "disruption", "harvest", "resource_endowment", "input_supply", "unit_multiplier")
FACTORS_KO = {"labour_capacity": "민간 노동능력", "capital_capacity": "설비능력",
    "skilled_labour": "숙련노동", "technology": "기술 확산", "infrastructure": "인프라",
    "market_access": "시장 접근·세금·부채 부담", "demand": "군수·건설 수요",
    "disruption": "생산 중단", "harvest": "작황", "resource_endowment": "자원",
    "input_supply": "전력·원료 공급", "unit_multiplier": "산업 단위환산"}
CORE_FILES = ("economy.py", "credit.py", "labour.py", "projects.py", "calibration.py",
              "benchmark.py", "stabilization_benchmark.py")


def core_source_hashes():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in CORE_FILES}


# Worker processes record the source they imported, then reject changing source.
# A final export must not relabel earlier workers' output with newer logic's hash.
IMPORTED_CORE_SHA256 = core_source_hashes()


def case_specs(months=360, seed=SEED):
    if type(months) is not int or months < 1 or type(seed) is not int:
        raise ValueError("Benchmark requires positive whole months and integer seed")
    return [{"profile": p, "path": path, "months": months, "seed": seed}
            for p in PROFILES for path in PATHS]


def calibrated_case(raw, profile, path):
    """Reject duplicate/confounded reference cases instead of matching first."""
    candidates = [c for c in raw["cases"] if c["spec"].get("profile") == profile
        and c["spec"].get("path", "baseline") == path and c["spec"].get("months") == 360
        and not set(c["spec"]) - {"profile", "path", "months", "seed"}]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one calibrated reference case: {profile}/{path}")
    return candidates[0]


def at(case, month):
    return next(record for record in case["records"] if record["month"] == month)


def factor_state(factors):
    state = {key: factors[key] for key in FACTORS if key != "demand"}
    market = factors["market_access"]
    state["demand"] = factors["capacity_utilization"] / market if market else 0
    state.update(labour_capacity=factors["labour_capacity"], capital_capacity=factors["capital_capacity"])
    return state


def annual_value(state, coefficient):
    return coefficient * min(state["labour_capacity"], state["capital_capacity"]) * math.prod(state[k] for k in FACTORS)


def decompose_records(initial, final):
    """Exact ordered production-factor substitution, explicitly path dependent.

    This is an accounting explanation of realized value added, not an experiment
    claiming uniquely identifiable causal contributions. Labour is changed first,
    then capital; min(labour, capital) prevents attributing idle capital as output.
    Reference countries use the same fixed price scale, so a sector's unit value
    added is constant across the comparison.
    """
    before_report, after_report = initial["explain"], final["explain"]
    regions = sorted(before_report["production_factors"])
    if regions != sorted(after_report["production_factors"]):
        raise ValueError("Factor decomposition requires the same modeled regions")
    if len(regions) != 1:
        raise ValueError("Benchmark decomposition requires one reference region")
    region = regions[0]
    contributions = dict.fromkeys(("labour_capacity", "capital_capacity", *FACTORS), 0.0)
    sectors = {}
    for sector in economy.SECTORS:
        initial_f = before_report["production_factors"][region][sector]
        final_f = after_report["production_factors"][region][sector]
        start, finish = factor_state(initial_f), factor_state(final_f)
        # GDP reference worlds have price_scale=1. Infer the actual coefficient
        # when production is nonzero; preserve zero-output cases explicitly.
        raw_coefficient = economy.PRICE[sector] - economy.ENERGY[sector] * economy.PRICE["energy"] - economy.RAW[sector] * economy.PRICE["mining"]
        base_product = annual_value(start, 1)
        coefficient = (before_report["sector_value_added"][sector] / base_product
                       if base_product else raw_coefficient)
        current = dict(start)
        value = annual_value(current, coefficient)
        changes = {}
        for key in contributions:
            current[key] = finish[key]
            updated = annual_value(current, coefficient)
            changes[key] = updated - value
            contributions[key] += changes[key]
            value = updated
        actual = after_report["sector_value_added"][sector]
        if not math.isclose(value, actual, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(f"Factor explanation does not reconstruct {sector} output")
        sectors[sector] = {"before": before_report["sector_value_added"][sector], "after": actual,
                           "change": actual - before_report["sector_value_added"][sector], "factors": changes}
    unrounded_change = math.fsum(s["change"] for s in sectors.values())
    rounding = final["gdp"] - initial["gdp"] - unrounded_change
    return {"method": "ordered factor substitution; labour, capital, skill, technology, infrastructure, market, demand, disruption, harvest, resources, inputs, units",
        "interpretation": "exact accounting decomposition; order-dependent, not an additive causal identification",
        "gdp_before": initial["gdp"], "gdp_after": final["gdp"], "gdp_change": final["gdp"] - initial["gdp"],
        "contributions": contributions, "rounding_residual": rounding, "sectors": sectors}


def legacy_debt_diagnosis(raw):
    case = calibrated_case(raw, "early_industrial", "debt_boom")
    initial = at(case, 0)
    findings = []
    for month in (120, 240, 360):
        record = at(case, month)
        finance = record["explain"]["finance"]
        factors = record["explain"]["production_factors"]["ra"]
        machinery = factors["machinery"]
        findings.append({"years": month // 12, "gdp": record["gdp"], "revenue_monthly": finance["total_revenue"],
            "debt_service_monthly": finance["debt_service"], "interest_to_revenue": finance["debt_service"] / max(1, finance["total_revenue"]),
            "government_debt": record["debt"], "new_borrowing_monthly": finance["borrowed"],
            "project_spending_monthly": finance["education"] + finance["infrastructure"] + finance["subsidy"],
            "replacement_need": finance["replacement_need"], "replacement_funded": finance["replacement_reserve"],
            "private_saving": finance["net_private_saving"], "capital_stock": record["capital_stock"],
            "skilled_labour": record["skilled_labour"], "military_manpower": record["labour"]["military"],
            "machinery_labour_capacity": machinery["labour_capacity"], "machinery_capital_capacity": machinery["capital_capacity"],
            "market_utilization": machinery["market_access"], "machinery_skill_multiplier": machinery["skilled_labour"],
            "machinery_input_supply": machinery["input_supply"], "decomposition": decompose_records(initial, record)})
    return {"profile": "early_industrial", "starting_gdp": initial["gdp"], "records": findings,
        "formulas": {"automatic_borrowing": "max(0, -(treasury + revenue - expenditure)); no lender limit",
            "interest": "opening debt * min(.3, monetary_rate + min(.2, debt/GDP*.02)) * days/365",
            "production_market": "max(.25, (.6+.4*logistics)*(1-tax*.3)/(1+debt_ratio*.08))",
            "replacement_crowding": "max(.2,1-military/monthly_value_added*.05)/(1+debt_ratio*.2+monetary_rate*2)"},
        "diagnosis": ["Unpaid interest was automatically funded by unlimited new debt, producing compounding principal.",
            "Debt directly lowered capacity utilization across all sectors and private replacement investment.",
            "Industrial capital grew far above machinery/heavy-industry labour capacity, so more factories could not remove the workforce bottleneck.",
            "Skills improved and physical inputs were not binding at the reported horizons; they do not explain the late GDP collapse.",
            "Shrinking revenue and near-zero equipment replacement worsened the spiral; agriculture lost capital and food security."]}


def extended_measure(world, month, cumulative):
    record = measure(world, month)
    country = world.economy["countries"]["a"]
    record.update(credit=copy.deepcopy(country.get("credit", {})), finance=copy.deepcopy(country["finance"]),
                  cumulative=copy.deepcopy(cumulative), regional_labour={})
    for region in economy.owned_regions(world, "a"):
        state = world.economy["regions"][region]
        record["regional_labour"][region] = {"labour": copy.deepcopy(state["labour"]),
            "allocation": copy.deepcopy(state.get("labour_state", {})),
            "sector_labour": {s: {"workers": v["labour"], "skilled": v["skilled_labour"],
                                   "professionals": v.get("professional_labour", 0)} for s, v in state["sectors"].items()}}
    return record


def verify_record(record):
    """Validate recorded accounting/explain fields, with no expected GDP target."""
    if record["explain"]["gdp"] != record["gdp"] or round(math.fsum(record["explain"]["sector_value_added"].values())) != record["gdp"]:
        raise ValueError("Recorded sector value added does not equal GDP")
    if record["finance"] != record["explain"]["finance"]:
        raise ValueError("Recorded finance explanation differs from state")
    credit = record["credit"]
    if credit and credit["domestic_debt"] + credit["foreign_debt"] != record["debt"]:
        raise ValueError("Debt components do not conserve government debt")
    for region, regional in record["regional_labour"].items():
        labour = regional["labour"]
        if "labour_force" not in labour:
            continue
        employed = math.fsum(labour[s] for s in economy.SECTORS)
        counted = employed + labour["military"] + labour.get("unemployed", 0) + labour.get("transit_training", 0)
        if not math.isclose(counted, labour["labour_force"], rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(f"Regional labour is duplicated/missing: {region}")


def run_case(spec):
    if core_source_hashes() != IMPORTED_CORE_SHA256:
        raise ValueError("Economic source changed after benchmark worker import; restart")
    if spec.get("profile") not in PROFILES or spec.get("path", "baseline") not in PATHS:
        raise ValueError("Unknown stabilization reference/policy")
    horizon = spec.get("months", 360)
    if type(horizon) is not int or horizon < 1 or type(spec.get("seed", SEED)) is not int:
        raise ValueError("Invalid stabilization horizon or seed")
    world = reference_world(spec["profile"])
    legacy_initial_hash = hashlib.sha256(json.dumps(world.to_dict(), sort_keys=True).encode()).hexdigest()
    economy.enable_stabilization(world)
    initial_hash = hashlib.sha256(json.dumps(world.to_dict(), sort_keys=True).encode()).hexdigest()
    replay = Replay(world, spec.get("seed", SEED))
    replay.month()
    cumulative = dict.fromkeys(("revenue", "requested_spending", "paid_spending", "new_borrowing", "interest_requested", "interest_paid", "emergency_non_debt_financing", "government_project_cost"), 0)
    records = [extended_measure(replay.world, 0, cumulative)]
    verify_record(records[0])
    timeline = []
    checkpoints = set(spec.get("checkpoints", (12, 120, 240, 360)))
    checkpoints.add(horizon)
    for month in range(horizon):
        changes = policy_for(replay.world, spec.get("path", "baseline"), month)
        # Risky governments explicitly permit emergency instruments. The engine
        # never silently changes the political policy or creates unlimited funds.
        if spec.get("path") in ("debt_boom", "military_heavy"):
            changes["emergency_finance"] = True
        economy.change_policy(replay.world, "a", changes)
        if spec.get("path") == EXTRA_PATH:
            technology_budget(replay.world, month)
        replay.month()
        verify_explanation(replay.world)
        e = replay.world.economy["countries"]["a"]
        finance = e["finance"]
        for target, key in (("revenue", "total_revenue"), ("requested_spending", "total_requested_expenditure"),
            ("paid_spending", "total_expenditure"), ("new_borrowing", "borrowed"),
            ("interest_requested", "debt_service_requested"), ("interest_paid", "debt_service_paid"),
            ("emergency_non_debt_financing", "emergency_non_debt_financing")):
            cumulative[target] += finance.get(key, 0)
        cumulative["government_project_cost"] += sum(p["cost"] for p in replay.world.projects.values()
            if p["country"] == "a" and p["source"] == "government" and p["start_date"] == replay.world.date)
        credit = e.get("credit", {})
        region_state = replay.world.economy["regions"]["ra"]
        regional = region_state["labour"]
        movement_report = region_state.get("labour_state", {}).get("last_report", {})
        timeline.append({"month": month + 1, "date": replay.world.date, "gdp": replay.world.countries["a"].gdp,
            "debt": replay.world.countries["a"].debt, "arrears": credit.get("arrears", 0),
            "creditworthiness": credit.get("creditworthiness", 0), "distress_state": credit.get("distress_state", ""),
            "revenue": finance["total_revenue"], "requested_spending": finance.get("total_requested_expenditure", finance["total_expenditure"]),
            "paid_spending": finance["total_expenditure"], "borrowed": finance["borrowed"],
            "interest_requested": finance.get("debt_service_requested", finance["debt_service"]),
            "interest_paid": finance.get("debt_service_paid", finance["debt_service"]),
            "employed": regional.get("employed", 0), "agriculture": regional["agriculture"],
            "manufacturing": regional["manufacturing"], "military": regional["military"],
            "transit_training": regional.get("transit_training", 0),
            "finance_reasons": copy.deepcopy(credit.get("last_reasons", [])),
            "labour_movements": copy.deepcopy(movement_report),
            "emergency_used": copy.deepcopy(credit.get("emergency_used", {}))})
        if month + 1 in checkpoints:
            record = extended_measure(replay.world, month + 1, cumulative)
            verify_record(record)
            records.append(record)
    if core_source_hashes() != IMPORTED_CORE_SHA256:
        raise ValueError("Economic source changed during benchmark; restart")
    return {"spec": copy.deepcopy(spec), "seed": replay.seed, "legacy_initial_sha256": legacy_initial_hash,
        "core_source_sha256": copy.deepcopy(IMPORTED_CORE_SHA256),
        "initial_sha256": initial_hash, "economy_version": replay.world.economy["version"],
        "model_revision": replay.world.economy.get("model_revision", 1),
        "stabilization_version": replay.world.economy.get("stabilization_version"), "records": records, "timeline": timeline}


def export(cases, directory, reference):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    reference = Path(reference)
    raw = json.loads(reference.read_text(encoding="utf-8"))
    current_sources = core_source_hashes()
    if any(case["core_source_sha256"] != current_sources for case in cases):
        raise ValueError("Cannot export mixed/stale economic source benchmark cases")
    data = {"benchmark_schema": 1, "cases": sorted(cases, key=lambda c: json.dumps(c["spec"], sort_keys=True)),
        "completed_cases": len(cases), "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))},
        "method": "production Replay; explicit finite emergency finance for military/debt paths; no target GDP; monthly audit",
        "legacy_debt_diagnosis": legacy_debt_diagnosis(raw)}
    (directory / "stabilized.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ("profile", "path", "month", "gdp", "legacy_gdp", "population", "debt", "reserves", "creditworthiness", "borrowing_capacity", "borrowing_cost", "arrears", "distress_state", "government_revenue", "government_expenditure", "requested_spending", "literacy", "skilled_labour", "capital_stock", "agriculture", "manufacturing", "military", "transit_training")
    with (directory / "stabilized.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in data["cases"]:
            old = calibrated_case(raw, case["spec"]["profile"], case["spec"].get("path", "baseline"))
            for record in case["records"]:
                try: legacy_gdp = at(old, record["month"])["gdp"]
                except StopIteration: legacy_gdp = ""
                labour = record["labour"]
                values = {**case["spec"], **record, **record["credit"], **labour, "legacy_gdp": legacy_gdp,
                    "requested_spending": record["finance"].get("total_requested_expenditure", record["government_expenditure"])}
                writer.writerow({key: values.get(key, "") for key in fields})
    return data


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] +
                     ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def num(value):
    return f"{value:,.0f}"


def generate_report(data, reference, destination, tests="아직 최종 검사 전"):
    raw = json.loads(Path(reference).read_text(encoding="utf-8"))
    cases = data["cases"]
    rows, finance_rows, labour_rows, initial_rows = [], [], [], []
    for profile in PROFILES:
        group = [c for c in cases if c["spec"]["profile"] == profile]
        if group:
            if len({c["initial_sha256"] for c in group}) != 1 or len({c["seed"] for c in group}) != 1:
                raise ValueError("Policy comparison cases have different starting conditions or seed")
            original = calibrated_case(raw, profile, "baseline")
            initial_rows.append([profile, num(at(original, 0)["gdp"]), num(at(group[0], 0)["gdp"]), group[0]["seed"]])
    for case in cases:
        profile, path = case["spec"]["profile"], case["spec"].get("path", "baseline")
        old = calibrated_case(raw, profile, path)
        if old["initial_sha256"] != case["legacy_initial_sha256"] or old["seed"] != case["seed"]:
            raise ValueError("New and calibrated reference experiments differ before enabling stabilization")
        for month in (120, 240, 360):
            try: r, before = at(case, month), at(old, month)
            except StopIteration: continue
            credit, f, labour = r["credit"], r["finance"], r["labour"]
            rows.append([profile, NAMES[path], month // 12, num(before["gdp"]), num(r["gdp"]),
                         f'{(r["gdp"] / max(1,before["gdp"]) - 1) * 100:+.1f}%', num(before["debt"]), num(r["debt"])])
            finance_rows.append([profile, NAMES[path], month // 12, num(r["government_revenue"]),
                num(f.get("total_requested_expenditure", 0)), num(r["government_expenditure"]),
                f'{credit.get("creditworthiness",0):.1f}', f'{credit.get("borrowing_cost",0)*100:.1f}%',
                num(credit.get("borrowing_capacity",0)), num(credit.get("arrears",0)), credit.get("distress_state", "")])
            labour_rows.append([profile, NAMES[path], month // 12, num(r["population"]), num(labour.get("labour_force",0)),
                num(labour["agriculture"]), num(labour["manufacturing"]), num(labour["services"]),
                num(labour["skilled"]), num(labour["military"]), num(labour.get("unemployed",0)), num(labour.get("transit_training",0))])
    text = ["# ECONOMIC_STABILIZATION_REPORT\n", "정부 신용·재정위험 및 지역별 노동배분 안정화 검증\n",
        f"완료한 장기 경로: **{len(cases)}개**. 최종 pytest 결과: **{tests}**.\n",
        "## 검증 방법\n",
        "기존 economy-v1-calibrated의 calibrated_final.json 원시 결과와 비교했다. 표의 v1은 보정 완료 체크포인트(저장 경제 model revision 2)이며, 최초의 보정 전 경제판과 구분한다. 3개 가상 profile마다 동일한 초기 인구·설비·교육·인프라·교역·seed 1897을 사용하고, 안정화 계층만 명시적으로 활성화했다. GDP 목표치를 입력하거나 국가별 보너스를 만들지 않았다. 30년 경로의 120·240·360개월 값을 10·20·30년 결과로 사용한다. 공통 첫 결산 후 진행하므로 시작 GDP는 profile별 기존 결과와 비교 가능한 기준값이다.\n",
        "기준·균형·균형+기술 경로는 기존 세입에 기초한 예산 규칙을 유지한다. 군비·차입 개발 경로는 같은 과도한 지출을 요구하고 비상조달을 명시적으로 허용한다. 엔진은 위험하다는 이유로 정책을 취소하지 않으며, 실제 지급·차입·미지급액은 유한한 조달수단과 물리적 제약에 따라 결정된다. 예상 지출과 실제 지급을 함께 기록했다. 기술 도입도 공통 프로젝트를 사용해 비용·훈련기간을 거친다.\n",
        table(["profile", "v1 첫 결산 GDP", "안정화 첫 결산 GDP", "seed"], initial_rows), "\n",
        "첫 결산의 차이 역시 새 노동 이동·훈련 및 신용 계산의 결과다. 첫 달 숫자를 억지로 같게 맞추지 않았다. 정책별 실험은 같은 profile 안에서 정확히 같은 상태로 시작한다.\n",
        "Replay는 게임과 동일한 일별 프로젝트·월별 경제 함수를 호출하고 매월 audit를 실행한다. 연구 도구에서만 SQLite 기록 IO를 생략한다. 게임 저장본과 연구 체크포인트는 별도로 검증한다.\n",
        "## 경제 v1 대비 GDP·부채 10/20/30년\n",
        table(["profile", "정책", "년", "v1 GDP", "안정화 GDP", "차이", "v1 부채", "안정화 부채"], rows), "\n",
        "## 재정 조달과 위험\n", table(["profile", "정책", "년", "월세입", "요구 지출", "실제 지급", "신용/100", "신규 연이율", "정상차입 여력", "미지급", "재정상태"], finance_rows), "\n",
        "GDP가 높다는 이유만으로 정책이 안전한 것은 아니다. 총부채뿐 아니라 미지급채무·요구 지출·실제 지급·비상수단 소진을 함께 읽어야 한다. 준비자산은 소진할 수 있지만 무한히 생성하지 않는다. 미지급 이자를 정상 대출처럼 자동 재차입하지 않는다. 파산 위험은 신용·금리·가용조달에 연속적으로 반영되고, 지급 실패가 지속될 때 상태가 악화된다. 정치적 선택과 경제적 결과를 구분한다.\n",
        "## 지역 노동배분\n", table(["profile", "정책", "년", "인구", "노동력", "농업", "제조업", "서비스", "숙련(부분집합)", "군사", "미취업", "이동·훈련중"], labour_rows), "\n",
        "숙련·전문가는 별도 인구를 더한 것이 아니라 같은 노동자의 능력 부분집합이다. 민간 산업별 배분+군사+미취업+이동·훈련중이 지역 노동력을 초과할 수 없다. 지역 밖 기술자를 즉시 빌려 쓰지 않는다. 매월 이동의 출발 산업·도착 산업·인원·훈련 및 완료 예정일을 저장한다. 상세 월별 기록과 프로젝트·노동 파이프라인은 JSON에 있다.\n",
        "## 기존 차입 개발의 붕괴 원인\n"]
    diagnosis = data["legacy_debt_diagnosis"]
    text += ["사용자가 제시한 **4,014→3,066→1,428→1,142**는 early_industrial의 차입 개발 결과와 정확히 일치한다. 이 숫자를 목표로 새 모형을 조정하지 않았다.\n"]
    drows = []
    for d in diagnosis["records"]:
        drows.append([d["years"], num(d["gdp"]), num(d["revenue_monthly"]), num(d["debt_service_monthly"]),
            num(d["project_spending_monthly"]), num(d["capital_stock"]), num(d["skilled_labour"]),
            f'{d["market_utilization"]:.3f}', f'{d["replacement_funded"]:.2f}/{d["replacement_need"]:.2f}'])
    text += [table(["년", "GDP", "월세입", "월이자", "개발 프로젝트 지출", "총설비", "숙련노동", "시장 가동계수", "교체적립/마모"], drows), "\n",
        "기존 모형은 부족한 현금 전부를 신규 차입으로 자동 충당했다. 이자를 갚기 위해 다시 빌린 원금에 이자가 붙어 30년 부채가 2,915,415까지 커졌다. 같은 부채비율이 생산 가동률과 민간 설비교체 적립을 동시에 낮췄다. 총자본과 숙련노동은 증가했지만, 기계업 설비능력은 노동능력보다 훨씬 커 유휴 설비가 생겼다. 후기에는 세입 감소와 농업 설비 마모가 추가 하락을 만들었다. 원료·전력 및 숙련 부족은 보고 시점의 붕괴 주원인이 아니었다.\n",
        "기존 식: 시장 접근은 `max(.25, (.6+.4*logistics)*(1-tax*.3)/(1+debt_ratio*.08))`, 설비교체 여력은 `max(.2,1-military/monthly_value_added*.05)/(1+debt_ratio*.2+monetary_rate*2)`였다. 따라서 부채 자체가 실제 대출 접근 여부와 별개로 모든 생산을 억제했다. 이번 단계에서는 이 생산·저축 마찰 계수나 GDP 공식은 바꾸지 않고, 무한 재차입을 유한 조달·미지급으로 분리하고 실제 신용·지급위험 hook을 연결했다. 새로운 금융제약의 결과를 이전 GDP 숫자에 맞추지 않았다.\n",
        "다음 분해는 실제 부문별 부가가치 식에서 요인을 순서대로 바꿔 계산했다. 모든 기여와 반올림 잔차의 합은 GDP 변화와 일치한다. 요인 변경 순서에 의존하는 회계 분해이며, 유일한 인과적 기여율이라고 주장하지 않는다.\n"]
    erows = []
    for d in diagnosis["records"]:
        dc = d["decomposition"]
        erows.append([d["years"], *[f'{dc["contributions"][k]:+.1f}' for k in ("labour_capacity", "capital_capacity", "skilled_labour", "infrastructure", "market_access", "demand", "input_supply")], num(dc["gdp_change"])])
    text += [table(["년", "노동", "설비", "숙련", "인프라", "시장·부채", "수요", "전력·원료", "총 GDP 변화"], erows), "\n",
        "## 새 신용·노동 규칙과 수정 이유\n",
        "정상 차입여력은 `(GDP×0.55 + 연간 세입×3)×신뢰도² − 기존 부채`의 양수 부분이다. 신뢰도는 저장 신용과 객관적 위험으로 계산한 목표 신용 중 낮은 값이다. 아직 첫 GDP 결산 전이라 GDP가 0인 초기화에서는 실제 설비 총량×profile 생산성으로 경제 규모를 추정한다. 준비자산은 위험을 완충하지만 무한 부채를 정당화하지 않는다. 부채·이자/세입·적자·미지급·전쟁·불안·과거 불이행의 가중 합을 `1−exp(−부담)`으로 바꿔 위험을 연속적으로 계산한다. 신규 금리에는 그 부담에 따른 spread가 붙고, 기존 대출의 가중 계약금리는 별도로 보존한다. 이는 실제 역사 통계에 맞춘 추정치가 아니라 게임 계정 단위의 초기 신용 규칙이며 후속 시나리오 자료로 검증해야 한다.\n",
        "정부 프로젝트는 실제로 조달된 예산에 대해서만 생성된다. 현금으로 이미 전액 지급한 프로젝트는 다른 예산 항목이 미지급이라는 이유로 자동 중단하지 않는다. 해당 프로젝트 자체에 지급유예로 조달한 부분이 있으면 그 부분이 정산될 때까지 진척이 느려진다. 수동 프로젝트의 자재를 비상징발 재원과 동시에 사용하는 것도 막았다. 정책의 요구 지출·확보한 재원·실제 건설 가능한 작업을 구분한 수정이다.\n",
        "신용 하락은 약 180일, 회복은 약 1,460일의 적응시간을 사용한다. 180일 이상 실제 채무 지급이 실패하고 미지급이 세입에 비해 커질 때 정상대출 접근이 닫힌다. 단순히 부채가 특정 숫자를 넘었다는 이유로 파산시키지 않는다. 비상세금·전쟁채권·외국대출·강제대출·징발·지급유예·자산매각은 기존 자원·자산에 대한 유한한 청구권이며 재정·민간경제·사회·신용 비용을 남긴다.\n",
        "노동 이동 한도는 도시화·문해율·기술교육·도로/철도/항만/통신·생산중단·안정성을 반영한 연간 이동률×지역 노동력×경과기간이다. 일반 업종 이동은 90~365일, 중공업·기계·화학·전력은 추가 180일의 적응·안전훈련을 요구한다. 이동했다고 숙련 자격이 자동 생성되지 않으며, 별도 교육·현장훈련 축적이 필요하다. 전역자는 120일 복귀 과정을 거쳐 지역 노동시장에 들어온다. 시간은 목표 GDP를 맞추기 위한 계수가 아니라 이동과 훈련을 순식간에 끝내지 않도록 한 설명 가능한 초기 규칙이다.\n",
        "## 수정 후 explain의 근거\n",
        "신용 결과에는 부채·상환부담·세입·준비자산·전쟁·안정성·이전 불이행의 원인과 실제 조달 내역이 남는다. 노동 결과에는 지역별 이동·복귀·훈련·군사 부담이 남는다. 설명은 같은 내부 상태를 복사하며 GDP 합산·부채 구성·노동 보존을 자동 검사한다. 상세 숫자는 stabilized.json의 credit, finance, regional_labour, explain, timeline에 보존한다.\n",
        "## 저장본 호환과 설계 범위\n",
        "기존 v1 저장본은 기존 계산으로 계속 읽으며 안정화 전환은 명시적으로 수행한다. 기존 저장본은 save schema 1/2 및 economy version/revision 1/2 호환을 유지한다. 안정화 저장본은 save schema 3, economy version/revision 3, stabilization version 1로 표시해 과거 엔진이 새 신용·노동 데이터를 조용히 무시하며 계산하지 못하게 한다. SQLite의 현재 상태에도 economy version 3을 저장하므로 과거 코드로 직접 열어도 버전 검사를 받는다. 새 신용상태·조달수단 소진·지역 노동배분·이동과 전역 파이프라인은 World에 저장되어 저장/복원 후 동일하게 이어진다. economy-v1-calibrated 복원 체크포인트는 이번 수정의 영향을 받지 않는다.\n",
        "정상 재정능력, 비상 조달능력, 물리적 실행 가능성, 예상 결과를 분리했다. 실제 비용·위험과 향후 지도부의 예상 비용·위험은 다른 값으로 제공할 수 있다. 이 단계는 NPC의 편향이나 정치적 결정을 구현하지 않는다.\n",
        "## 한계와 다음 단계\n",
        "신용·비상조달 한도는 역사 국가의 복잡한 채권시장을 단순화한 설명 가능한 게임 모델이다. 가격·환율·은행·인플레이션은 계산하지 않으며 monetary financing은 연결용 위험 hook이다. 채무 재조정은 상태와 비용을 기록하는 범위다. 지역 이동은 지역별 파이프라인을 제공하지만 실제 전국 이주망은 아직 없다. 연령·성별 노동력은 단순 가용집단이며 전투 피해·NPC·자동 위기 사건은 구현하지 않았다. 이번 15개 경로는 주요 회귀검증이며 모든 가능한 세율·정치행동 조합의 안전성을 증명하지 않는다.\n",
        "NPC Engine v1은 읽기 평가→명시적 정책/조달 선택→동일 엔진 실행 경로로 연결할 수 있다. 위험 선택은 허용하되 존재하지 않는 인력이나 소진한 금융수단을 생성하지 않는 원칙을 유지해야 한다. 장기 대규모 NPC 캠페인 전에는 여러 행위자·전쟁비용·오정보를 포함한 추가 스트레스 실험이 필요하다.\n"]
    Path(destination).write_text("\n".join(text), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="benchmarks/stabilization")
    parser.add_argument("--reference", default="benchmarks/calibrated_final.json")
    parser.add_argument("--report", default="ECONOMIC_STABILIZATION_REPORT.md")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--months", type=int, default=360)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--tests", default="아직 최종 검사 전")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    if args.report_only:
        data = json.loads((Path(args.output) / "stabilized.json").read_text(encoding="utf-8"))
    else:
        specs, cases = case_specs(args.months, args.seed), []
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_case, spec): spec for spec in specs}
            for future in as_completed(futures):
                case = future.result()
                cases.append(case)
                print(f"{len(cases)}/{len(specs)} {case['spec']}", flush=True)
                data = export(cases, args.output, args.reference)
    generate_report(data, args.reference, args.report, args.tests)
    print(f"Wrote {len(data['cases'])} cases and {args.report}", flush=True)


if __name__ == "__main__":
    main()
