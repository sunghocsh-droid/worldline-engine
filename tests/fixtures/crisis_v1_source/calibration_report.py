"""Render Korean audit tables directly from benchmark data, without fitting GDP."""
import argparse
import json
import math
from pathlib import Path
from .benchmark import PROFILES, PATHS, EXTRA_PATH, SHOCK_CASES

NAMES = {"baseline": "A 유지", "infrastructure": "B 인프라", "education_industry": "C 교육·산업",
    "balanced": "D 균형 개발", "military_heavy": "E 군비 과다", "debt_boom": "F 차입 개발",
    EXTRA_PATH: "G 균형+실제 기술 도입"}
SHOCK_NAMES = {"war": "전쟁", "capital": "자본 파괴", "harvest": "흉작", "epidemic": "전염병",
    "blockade": "봉쇄", "foreign": "해외 투자", "resource": "자원 발견"}


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] +
                     ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def num(x): return f"{x:,.0f}"
def pct(x): return f"{x * 100:.1f}%"
def record(case, month): return next(r for r in case["records"] if r["month"] == month)


def main_case(cases, profile, path):
    return next(c for c in cases if c["spec"]["profile"] == profile and c["spec"].get("path", "baseline") == path
                and c["spec"]["months"] == 360 and not any(k in c["spec"] for k in ("shock", "sensitivity", "bottleneck", "control")))


def generate(directory, destination, tests):
    directory = Path(directory)
    raw = json.loads((directory / "calibrated_final.json").read_text(encoding="utf-8"))
    before = json.loads((directory / "before_full.json").read_text(encoding="utf-8"))
    cases, old = raw["cases"], before["cases"]
    assert len(cases) == 231
    for p in PROFILES:
        clones = [main_case(cases, p, path) for path in (*PATHS, EXTRA_PATH)]
        assert len({c["initial_sha256"] for c in clones}) == 1
        assert {c["seed"] for c in clones} == {1897}
    rows = []
    for p in PROFILES:
        for path in (*PATHS, EXTRA_PATH):
            c = main_case(cases, p, path)
            r = record(c, 360)
            rows.append([p, NAMES[path], num(record(c, 0)["gdp"]), *[num(record(c, m)["gdp"]) for m in (120, 240, 360)],
                         pct(r["gdp"] / record(c, 0)["gdp"] - 1), num(r["debt"])])
    text = ["# ECONOMIC_CALIBRATION_REPORT\n", "2026-09-30 — 장기 경제 캘리브레이션·민감도 감사\n",
        f"최종 보정 모델 **231개 실험 경로**를 실제 실행했다. 보정 전 102개 실험도 보존했다. 최종 전체 테스트: **{tests}**.\n",
        "## 판단\n",
        "수입 범위의 개발과 무정책 경로는 30년 동안 제한된 속도로 성장했다. 생산능력·숙련·투입·시간 지연의 방향성은 유지된다. 설비 교체가 없어서 평시에도 경제가 지속 축소되던 결함을 고쳤다. 과도한 차입은 지금도 이자 누적으로 지속 불가능해진다. 이는 정상적인 위험 방향이지만 대출 중단·파산이 없는 재정 모델의 한계다. 따라서 NPC의 제한된 정책 실험은 가능하나, 무제한 차입 권한을 준 장기 자동 운영을 바로 허용하기에는 이르다.\n",
        "## 실험 방법과 조건\n",
        "실제 국가 통계나 목표 GDP를 입력하지 않았다. 각 profile은 인구 10,000, 국고 1,000, 군인 100과 동일한 교역 상대국으로 시작한다. 낮음/중간/높음의 문해율은 12%/30%/65%, 기술교육은 1%/6%/20%, 기본 인프라는 5/20/50이다. 초기 자본은 기존 profile 초기화 규칙이다. profile 안의 A~G는 초기 상태 해시와 seed 1897이 정확히 같다. profile 사이 차이는 의도한 초기 조건이다.\n",
        "일반 기준 국가의 시작 숙련 비율은 기존 초기화 값 6%다. 따라서 높은 교육 수준 국가의 초기 노동시장 적응도 baseline 성장에 포함된다. 별도의 '숙련은 많고 자본은 부족' 시험은 실제 숙련 비율을 교육 수준과 일치하는 55%로 초기화했다.\n",
        "공통 첫 한 달 결산을 기준점으로 삼고, 그 다음 120/240/360개월을 진행한다. 30년 경로의 10/20년 값을 사용한다. 독립적으로 짧게 실행한 결과와 긴 경로의 같은 시점이 일치하는 검사도 있다. GDP는 해당 월의 부가가치를 연율 환산한 고정 가격 게임 수치이며 달러나 12개월 합계 통계가 아니다.\n",
        "연구용 Replay는 실제 경제·프로젝트 함수를 매일 같은 순서로 호출하고 월마다 validator를 실행한다. 게임 DB를 수정하지 않는다. 매일 SQLite 원장 쓰기와 복사를 생략하므로 대량 실험이 가능하다. SQLite 엔진과 1년간 월별 전체 World/RNG 일치를 확인했으며, 실제 SQLite 게임의 5년 저장→10년 복원 진행과 끊김 없는 진행의 상태·원장·RNG 일치도 검사했다. 연구용 체크포인트는 게임 저장본과 구분하며 30년 중간 복원도 검사한다.\n",
        "| 경로 | 매월 실제 세입에 따른 정책 |\n|---|---|\n"
        "| A | 행정 15%, 군사 15%; 대규모 신규 개발 없음 |\n"
        "| B | 기본 지출 + 인프라 60%; 철도/항만/도로/전력 순환 |\n"
        "| C | 기본 지출 + 교육 28%, 산업 32%; 기계/중공업 순환 |\n"
        "| D | 기본 지출 + 교육 20%, 인프라 25%, 산업 20%; 위생 포함 |\n"
        "| E | 군사 150%, 군수 관련 산업 50%, 행정 15% |\n"
        "| F | 인프라 150%, 교육 75%, 산업 150%, 기본 지출 |\n"
        "| G 추가 진단 | D + 매년 세입의 최대 10%를 실제 기술 도입에 배정; 보유 국고 한도 |\n",
        "A~D의 예산 기준은 직전 세입에서 이자 부담을 뺀 금액이다. E/F는 의도적으로 세입을 넘겨 차입한다. G의 기술 구입은 실제 국고와 자원을 차감하며 공통 설치/훈련 프로젝트를 거친다. 어떤 경로도 GDP 성장률을 지정하지 않는다.\n",
        "## GDP 10/20/30년 정책 비교\n", table(["profile", "경로", "시작 GDP", "10년", "20년", "30년", "30년 총변화", "30년 부채"], rows), "\n",
        "G는 교육·시설 투자와 실제 기술 도입을 구분하기 위한 추가 실험이다. C의 투자가 B나 D보다 반드시 높은 GDP를 보장하지 않는다. 숙련 요구를 이미 충족하거나 설비보다 노동/수송이 제한인 경우 자본 추가의 효과가 작다. 이 결과를 목표치에 맞추어 바꾸지 않았다.\n",
        "## 인구·1인당 GDP의 장기 경로\n"]
    rows = []
    for p in PROFILES:
        for path in ("baseline", "balanced", EXTRA_PATH):
            c = main_case(cases, p, path)
            for m in (120, 240, 360):
                r = record(c, m)
                rows.append([p, NAMES[path], m // 12, num(r["population"]), f'{r["gdp_per_capita"]:.3f}', pct(r["literacy"]), num(r["skilled_labour"]), pct(r["urbanization"])])
    text += [table(["profile", "경로", "년", "인구", "1인당 GDP", "문해율", "숙련 노동", "도시화"], rows), "\n", "## 30년 재정·설비·교역\n"]
    rows = []
    for p in PROFILES:
        for path in PATHS:
            r = record(main_case(cases, p, path), 360)
            rows.append([p, NAMES[path], num(r["government_revenue"]), num(r["government_expenditure"]), num(r["reserves"]), num(r["capital_stock"]), f'{r["debt"] / max(1,r["gdp"]):.1f}', f'{r["trade"]["exports"]:.1f}', f'{r["trade"]["imports"]:.1f}'])
    text += [table(["profile", "경로", "월 수입", "월 지출", "국고", "설비", "부채/GDP", "월 수출", "월 수입품"], rows), "\n",
        "산업별 생산량, 모든 인프라 수준, 부문별 채택/확산/국내 역량, 노동 배분과 주요 병목은 각 시점의 원시 JSON에 들어 있다. GDP에 불리한 군비/부채 경로도 삭제하지 않았다.\n",
        "## 충격과 회복\n",
        "기준 시점 2년 뒤 충격을 전달했다. 전쟁 강도 .9(365일), 자본 파괴 .8(90일), 흉작 .9(365일), 전염병 .9(180일), 봉쇄 1(365일), 해외 투자 1(1095일), 자원 발견 1을 사용했다. 각 profile에서 A/D 후속 정책을 비교했다. 표는 **같은 시점의 무충격 대조 GDP를 100으로 둔 값**이다. 직후는 첫 월 결산이고 1년은 지속 충격의 마지막 월이 포함될 수 있다. 손실과 긴급비용은 첫 결산에 반영된다.\n"]
    rows = []
    shock_cases = [c for c in cases if "shock" in c["spec"]]
    for c in shock_cases:
        s = c["spec"]
        control = next(d for d in cases if d["spec"].get("control") and d["spec"]["profile"] == s["profile"] and d["spec"]["path"] == s["path"])
        indices = [record(c, m)["gdp"] / max(1, record(control, m)["gdp"]) * 100 for m in (25, 36, 84, 144)]
        rows.append([s["profile"], NAMES[s["path"]], SHOCK_NAMES[s["shock"]], *[f"{v:.1f}" for v in indices]])
    text += [table(["profile", "후속 정책", "충격", "첫 결산", "1년", "5년", "10년"], rows), "\n",
        "전쟁/재난의 생산 중단 계수는 기한 후 해제된다. 파괴된 설비와 인구는 자동 복구하지 않으며 투자·출생으로 새로 축적한다. 이전 GDP를 넘어섰는지와 무충격 국가를 따라잡았는지는 서로 다르다. 봉쇄는 교역을 차단하지만 자급 및 재고가 충분한 국가의 GDP 피해는 작을 수 있다. 해외 유입도 노동/숙련 한도에 막히면 즉시 GDP가 늘지 않는다.\n",
        "## 병목 시험\n"]
    rows = []
    for c in cases:
        if "bottleneck" not in c["spec"]: continue
        r = record(c, 1)
        f = r["explain"]["production_factors"]["ra"]["machinery"]
        rows.append([c["spec"]["bottleneck"], num(r["gdp"]), ", ".join(r["bottlenecks"]["machinery"]), f'{f["skilled_labour"]:.3f}', f'{f["energy_supply"]:.3f}', f'{f["raw_material_supply"]:.3f}', f'{f["technology"]:.3f}'])
    text += [table(["의도적 조건", "GDP", "기계부문 병목", "숙련 계수", "에너지 공급", "원료 공급", "기술 계수"], rows), "\n",
        "자본 부족과 노동 부족은 min(노동능력, 설비능력)의 어느 쪽이 작았는지로 구분한다. 숙련/전력/원료/국내 역량은 실제 계수에서 판정한다. 수송·항만은 교역 경로의 transport_capacity/port_access도 제시한다. 라벨의 .95 등 경계는 설명용이며 GDP 계산에 별도의 판정 보너스를 넣지 않는다.\n",
        "## 민감도\n",
        "한 번에 한 계수만 ±10% 바꾸었다. 생산성·자본 효율·출생률·사망률·감가상각·저축은 A/D/F에서, 숙련 형성 속도·기술 확산 기본 속도·인프라 체감 척도·기술 생산성 계수는 D/G에서 검사했다. 표는 동일 profile/정책의 원래 계수 실험 대비 30년 GDP 비율이다.\n"]
    rows = []
    ratios = []
    coefficients = sorted({c["spec"]["sensitivity"] for c in cases if "sensitivity" in c["spec"]})
    for p in PROFILES:
        for key in coefficients:
            group = [c for c in cases if c["spec"].get("sensitivity") == key and c["spec"]["profile"] == p]
            values = []
            debt_values = []
            for c in group:
                base = record(main_case(cases, p, c["spec"]["path"]), 360)
                r = record(c, 360)
                value = r["gdp"] / max(1, base["gdp"])
                values.append(value)
                if base["debt"]: debt_values.append(r["debt"] / base["debt"])
            ratios.extend(values)
            rows.append([p, key, f"{min(values):.3f}~{max(values):.3f}", f"{min(debt_values):.3f}~{max(debt_values):.3f}" if debt_values else "—"])
    text += [table(["profile", "係数", "30년 GDP 비율 범위", "부채 비율 범위(F)"], rows), "\n",
        f"전체 표의 GDP 비율 범위는 **{min(ratios):.3f}~{max(ratios):.3f}**다. ±10% 변경으로 결과가 수십 배 벌어지는 현상은 이 실험에서 없었다. 노동 한도에 도달한 산업은 생산성에 직접 민감하고 자본 효율/저축에는 덜 민감하다. 이는 계수의 폭발적 증폭과는 다르지만, 현재 고정 산업별 노동 배분이 자본 투자의 효과를 제한하는 모델 한계다. 계수 간 상호작용·더 넓은 범위·다지역·세금/이자 자체의 추가 민감도는 아직 검증하지 않았다.\n",
        "## 발견한 결함과 수정 전후\n",
        "1. **설비 교체 부재:** 감가상각을 차감하면서 net 저축으로만 새 설비를 짓고 있었다. 가처분 생산 소득에서 순저축을 뺀 범위 안에서 마모량에 맞춘 교체 적립을 추가했다. 부채/군비/금리 부담은 교체도 억제한다. 실제 자본은 기존 공사 지연 후 늘어난다. 감가상각률과 profile의 저축률은 바꾸지 않았다.\n",
        "2. **사용하지 않는 원료의 부족으로 생산 차단:** 공통 원료 부족 비율을 원료를 쓰지 않는 농업에도 적용했다. 보정 모델에서는 해당 산업의 실제 투입 요구가 0이면 그 자원으로 생산을 제한하지 않는다. 총 소비량은 여전히 공급 한도 안이다.\n",
        "3. **해외 투자 충격 규모 불명확:** 작은 평시 유입을 몇 배 하는 방식만 있어 '대규모 유입'의 자본 규모가 모호했다. 강도 1의 추가 제안 규모를 해당 지역 기존 설비의 20% × 투자 개방도로 정의했다. 한 번만 투자 적립금에 넣으며 기존 프로젝트/원료/노동/시간 지연을 거친다. 이는 실제 역사 추정치가 아닌 명시적인 shock 강도 규약이다. 기본 평시 투자 계수는 유지했다.\n",
        "4. **기술자 누적 상한:** 반복 도입으로 foreign_experts가 1을 넘으면 validator가 게임 진행을 중단했다. 비율을 1에서 포화시켰다.\n",
        "5. **저장 후 프로젝트 순서:** 새 교체 적립의 사전 순서가 JSON 정렬 뒤 달라져 프로젝트 ID/원장이 달라졌다. 고정 SECTORS 순서로 생성하도록 고쳤고 10년 실제 SQLite 복원 검사를 통과시켰다.\n",
        "6. **설명의 구분 부족:** 에너지·원료 공급, 수송·항만, 부채, 국내 기술 역량과 교체 예산을 실제 계산에서 조회하도록 보강했다. 설명 라벨이 계산 결과를 별도로 바꾸지 않는다.\n",
        "실험 도구 자체도 검증했다. 숙련 풍부 시험이 실제 높은 숙련 인구를 갖는지 확인하고, 사용자 지정 seed가 결과 파일에 정확히 표시되도록 했다. 이 수정은 국가 보너스나 GDP 조정이 아닌 실험 입력/기록의 정확성 개선이다.\n"]
    rows = []
    for p in PROFILES:
        for path in ("baseline", "balanced", "education_industry"):
            a, b = main_case(old, p, path), main_case(cases, p, path)
            rows.append([p, NAMES[path], num(record(a, 360)["gdp"]), num(record(b, 360)["gdp"]), num(record(a, 360)["capital_stock"]), num(record(b, 360)["capital_stock"])])
    text += [table(["profile", "경로", "보정 전 GDP", "보정 후 GDP", "전 설비", "후 설비"], rows), "\n",
        "수정 전 실험의 초기 인구·교육·인프라·profile·seed·정책 예산 규칙은 같다. 보정 전 source와 JSON을 남겼다. 전후 비교는 GDP를 맞춘 결과가 아니라 교체 공사와 실제 투입 조건의 변화 결과다.\n",
        "## 수정한 공식·계수와 버전\n",
        "교체 적립 = min(기간 감가상각액 × 기존 군비/부채/금리 부담 계수, 가처분 소득 − 순저축). 산업별 실제 마모 비중으로 적립하고 연간 공사 자금을 배정한다. 순저축과 교체 적립의 합이 가처분 소득을 넘으면 validator 오류다. 정부 예산과 중복 청구하지 않는다.\n",
        "부문 생산의 원료/에너지 공급 계수 = min(실제로 필요한 자원의 공급 비율). 자본·노동 능력과 생산함수의 기존 승수는 유지했다. 외국 기술자 비율 = min(1, 기존 비율 + 실제 완료 효과). 해외 추가 투자 제안 = 기존 지역 설비 × .20 × shock 강도 × 개방도.\n",
        "민감도 도구가 사용할 기존 상수를 저장 가능한 설정으로 노출했다. 기본값은 숙련 형성 .3/년, 기술 확산 기본 속도 .03/년, 도로/전력 생산 효과의 체감 척도 50, 기술 생산성 증가 계수 2로 **변경하지 않았다**.\n",
        "게임 save/World schema는 2를 유지한다. 새 경제 데이터는 version 2 / model_revision 2다. 기존 경제 version 1 저장본은 원래 규칙으로 이어가며 자동 보정하지 않는다. 알 수 없거나 모순된 경제 버전은 거절한다. 시스템 등록 인터페이스 버전 1과 순서 검사도 유지한다. 옛 바이너리는 경제 version 2를 거절하므로 새 규칙을 조용히 옛 공식으로 읽지 않는다. 새 파라미터와 교체 적립은 저장 상태에 포함된다.\n",
        "기존 게임에 새 규칙을 적용하려면 먼저 이름을 붙인 저장본으로 백업하고 `python -m worldline economy-upgrade DB경로`를 실행한다. 업그레이드는 원장에 기록되고 기존 인구·설비·부채·프로젝트·RNG를 유지한다. 이후 결산부터 새 규칙이 적용된다. 기존 예제의 복원→명시적 업그레이드→한 달 진행→audit도 실제 실행하여 오류/경고 0개를 확인했다.\n",
        "## explain 사례와 자동 대조\n"]
    examples = {
        "빠른 성장": record(main_case(cases, "industrial", EXTRA_PATH), 360),
        "투자 대비 정체": record(main_case(cases, "industrial", "education_industry"), 360),
        "군비 과다": record(main_case(cases, "industrial", "military_heavy"), 360),
        "부채 과다": record(main_case(cases, "industrial", "debt_boom"), 360),
        "전쟁 피해": record(next(c for c in shock_cases if c["spec"]["profile"] == "industrial" and c["spec"]["path"] == "balanced" and c["spec"]["shock"] == "war"), 25),
        "전력 병목": record(next(c for c in cases if c["spec"].get("bottleneck") == "no_energy"), 1)}
    rows = []
    for label, r in examples.items():
        f = r["explain"]["production_factors"]["ra"]["machinery"]
        rows.append([label, num(r["gdp"]), num(r["debt"]), ", ".join(r["bottlenecks"]["machinery"]), f'{f["technology"]:.3f}', f'{f["input_supply"]:.3f}', f'{f["market_access"]:.3f}'])
    text += [table(["사례", "GDP", "부채", "기계부문 이유", "기술 계수", "투입 계수", "시장 계수"], rows), "\n",
        "각 결산에서 부문별 기록된 능력×기간×숙련×기술×인프라×이용률×중단×수확×자원×단위×투입을 다시 곱해 실제 output과 대조했다. 부가가치 합계와 GDP, 인구 장부, 재정 설명도 실제 상태와 대조했다. 완전한 인과 기여율 분해를 주장하지 않는다. 여섯 사례의 전체 설명은 `benchmarks/explain_examples.json`에 저장했다.\n",
        "## 남은 불확실성과 NPC 연결 판단\n",
        "- 3개 가상 profile/1지역 국가와 고정 상대국을 대상으로 한 방향성 검증이다. 역사 통계 정확성·수백 년·다지역·영토 변경을 보증하지 않는다.\n"
        "- 산업별 노동 배분은 도시화/교육 비중으로 간소화되어 산업간 수익률에 따른 세밀한 이동을 하지 않는다. 충분한 기술자·설비가 있는 산업국에서 교육/자본만 추가한 효과는 작다.\n"
        "- 저축은 교체와 순투자로 구분했지만 가계 소비/가격/기업별 이윤과 소유권별 교체 책임은 상세 모델이 아니다. 게임 화폐와 설비 비용의 역사적 보정이 필요하다.\n"
        "- 해외 자금의 기본 배치는 기계 부문에 집중한다. 한계가 막힌 산업에서는 추가 자본의 GDP 효과가 작다. 향후 투자 판단에서 기대 수익·병목·외국인의 목적을 고려할 필요가 있다.\n"
        "- 에너지는 전력·연료를 합친 재고다. 전력망 자체를 별도로 모델링하지 않고, 교역의 수요/가격 영향도 간소화되어 봉쇄 효과가 자급 정도에 따라 작을 수 있다.\n"
        "- 과다 차입의 이자와 민간 부담은 있지만 신용 한도·채무불이행·대출자의 판단은 없다. 지속 차입 국가의 부채가 비현실적으로 오래 증가할 수 있다. GDP 성장 폭발과는 다른 재정 모형의 한계다.\n"
        "- 출생/사망률의 시대 전환, 연령 코호트, 기술별 비용 차이, 학교 수료율, 교육 수준별 직종은 후속 보정 대상이다.\n"
        "NPC 연결은 **예산·부채 위험 한도와 관측 정보 경계를 적용한 제한된 실험부터 가능**하다. 자율 장기 캠페인에 쓰기 전 재정 지속 가능성/신용 규칙과 산업 노동 이동을 우선 검토해야 한다. 이번 단계에서는 NPC AI·전투·Crisis·MCP·실제 역사 자료를 추가하지 않았다.\n",
        "## 재실행과 원시 결과\n",
        "```powershell\n.\\.venv\\Scripts\\python.exe -m worldline.benchmark --suite full --label calibrated_final --output benchmarks --workers 4\n"
        ".\\.venv\\Scripts\\python.exe -m worldline.calibration_report --tests \"실제 pytest 결과 문자열\"\n"
        ".\\.venv\\Scripts\\python.exe -m pytest -q\n```\n",
        "`--config`에 JSON 실험 목록을 전달하면 profile, path, months, checkpoints, calibration, model_parameters, shock, bottleneck, seed를 지정할 수 있다. `--seed`는 기본 seed를 바꾼다. 연구 체크포인트를 게임 load 명령에 전달하지 않는다.\n",
        "- `benchmarks/before_full.json/.csv`: 수정 전 102경로\n"
        "- `benchmarks/calibrated_final.json/.csv`: 최종 231경로와 모든 요청 지표\n"
        "- `benchmarks/explain_examples.json`: 여섯 사례의 내부 계산 설명\n"
        "- `benchmarks/reference_source/`: 수정 전 핵심 코드 비교 자료\n"
        "최종 JSON에는 계산 소스 해시·초기 상태 해시·seed가 있다. CSV는 주요 수치 표이며 산업·인프라·기술·병목의 상세 자료는 JSON을 참조한다.\n"]
    (directory / "explain_examples.json").write_text(json.dumps(examples, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(destination).write_text("\n".join(text).replace("係数", "계수"), encoding="utf-8")
    print(f"Wrote {destination}; {len(cases)} final cases, {len(old)} original cases")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="benchmarks")
    parser.add_argument("--output", default="ECONOMIC_CALIBRATION_REPORT.md")
    parser.add_argument("--tests", required=True)
    args = parser.parse_args()
    generate(args.directory, args.output, args.tests)


if __name__ == "__main__": main()
