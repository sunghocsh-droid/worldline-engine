# 경제 안정화 사용 안내

기존 인구·생산·투자·교육·기술 모형에 **유한한 정부 신용과 지역별 노동 배분**을 연결했습니다. GDP는 계속 산업별 부가가치의 합으로 계산합니다. 국가는 위험한 전쟁이나 개발사업을 선택할 수 있지만, 필요한 사람·물자·조달수단과 그 비용을 실제 상태에서 계산합니다.

장기 비교 수치와 기존 차입 개발 경로의 붕괴 원인은 [경제 안정화 보고서](ECONOMIC_STABILIZATION_REPORT.md)를 참고하세요. 원시 결과는 [JSON](benchmarks/stabilization/stabilized.json)과 [CSV](benchmarks/stabilization/stabilized.csv)에 있습니다. 이전 상태로 돌아갈 때는 [economy-v1-calibrated 복원 안내](../checkpoints/economy-v1-calibrated/README.md)를 사용합니다.

## 정부가 돈을 마련하는 방식

정상적인 신규 차입 여력은 생산 규모, 실제 세입, 기존 부채와 이자 부담, 준비자산, 최근 재정수지, 전쟁, 사회 안정성, 과거 채무불이행을 함께 반영합니다. 부채와 위험이 커지면 여력이 줄고 신규 금리가 상승합니다. 기존 차입의 이자와 새로 빌리는 돈의 금리는 구분합니다. 신용 회복에도 수년이 필요합니다.

정상 한도를 넘는 행동에 대해서는 다음 네 가지를 각각 반환합니다.

| 조회 항목 | 뜻 |
| --- | --- |
| `physical_feasibility` | 필요한 실제 사람과 물자가 있는가 |
| `normal_financial_feasibility` | 준비자산과 정상 차입으로 지출을 감당할 수 있는가 |
| `emergency_financial_feasibility` | 명시적으로 선택한 비상수단까지 쓰면 조달할 수 있는가 |
| `expected_consequences` | 늘어나는 부채·이자·미지급금, 민간경제 부담, 신용·사회·인플레이션 위험은 무엇인가 |

높은 위험 자체가 정치적 선택을 취소하지는 않습니다. 조달수단이 남아 있으면 위험한 선택도 실행할 수 있습니다. 실제 사람이 부족하거나 모든 접근 가능한 조달수단이 소진된 경우에는 실행이 제한됩니다. 조회는 상태를 바꾸지 않으며 실제 명령 실행 시 동일한 제약을 다시 계산합니다.

비상수단에는 추가 과세, 국내 전쟁채권, 해외 차입, 준비자산 사용, 강제 대부, 징발, 지급 연기, 자산 매각과 통화조달 연결 지점이 있습니다. 각 수단의 동원 가능액은 유한하고, 이미 사용한 몫은 재사용할 수 없습니다. 준비자산은 일반 현금과 중복 계산하지 않습니다. 징발은 실제 재고를 소모합니다. 강제조달은 민간경제·사회·신용에 부담을 주며, 지급 연기는 미지급금으로 남습니다. 통화조달은 별도로 허용해야 하며 현재는 인플레이션 **위험**만 기록합니다.

`normal`, `stressed`, `severe_stress`, `arrears`, `default`, `restructuring`은 정상·압박·심한 압박·미지급·채무불이행·채무조정 상태를 뜻합니다. 파산 판정은 부채가 임의의 숫자를 넘었다는 이유로 발생하지 않습니다. 실제 이자 지급 실패가 지속되고 미지급 부담이 커졌을 때 채무불이행으로 넘어갑니다. 위험과 신용은 그 전부터 연속적으로 변합니다.

국가가 요청한 예산과 실제 지급한 예산은 별도로 기록합니다. 부족한 예산이 정상적으로 집행된 것으로 처리되지 않습니다. 재정 압박은 신규 투자·해외투자·군사비 지급·사회불안 압력으로 연결할 수 있습니다. 혁명이나 쿠데타는 이번 단계에서 발생시키지 않습니다.

이미 전액 지급한 정부 프로젝트는 이후의 다른 예산 부족 때문에 일괄 중단되지 않습니다. 해당 프로젝트를 미지급 방식으로 조달했다면 **그 프로젝트의 미지급 비율**에 따라 진행이 느려집니다. 노동·물자 부족과 외부 충격도 별도 지연 사유입니다.

## 노동자와 군인의 계산

지역마다 일할 수 있는 인구를 산업별 취업자, 실업자, 이동·훈련 중 인력, 군 복무·군수지원 인력으로 나눕니다. 이들을 합하면 해당 지역 노동력과 일치해야 합니다. 농업과 기계공업에 같은 사람을 두 번 넣을 수 없습니다. 저활용 인력은 취업자의 부분집합입니다.

숙련 노동자와 기술자·전문가는 추가 인구가 아닙니다. 전문가는 숙련 노동자의 부분집합이고, 숙련 노동자는 해당 직업 인원의 부분집합입니다. 신규 노동시장 진입자에게 숙련 자격을 자동 부여하지 않습니다.

공장과 설비가 늘면 산업별 노동 수요가 바뀝니다. 기존 직장을 떠난 사람은 이동·훈련 과정에 들어가며, 그동안 이전 산업과 새 산업 어느 쪽에서도 생산 노동자로 계산되지 않습니다. 이동에는 대략 **90~545일 범위**의 시간이 필요하고 고숙련 산업의 적응·훈련은 더 오래 걸립니다. 이동량은 도시화, 교육, 기술훈련, 교통망, 사회 안정성, 전쟁·혼란의 영향을 받습니다. 교육으로 숙련도가 오르는 속도 역시 별도로 제한합니다.

지역별 풀은 서로 분리됩니다. 전국에 기술자가 있다고 해서 먼 지역 공장에 즉시 배치하지 않습니다. 본격적인 지역 간 이주와 이동망 모형은 후속 단계의 과제입니다.

동원 명령의 수치는 **해당 지역의 추가 예비병 총원**입니다. 이미 존재하는 상비군은 별도로 차감합니다. 동원하면 같은 수의 민간 인력이 즉시 줄어 농업과 산업에 부담이 생깁니다. 군수지원 노동도 민간 노동과 중복 계산하지 않습니다. 해외 배치군은 출신 지역의 인구를 계속 사용합니다.

동원 총원을 줄이면 전역 인력은 **120일 복귀 과정**을 거쳐 지역 실업 풀로 돌아옵니다. 이후에는 일반적인 구직·산업 이동 과정을 따릅니다. 월말에 전역한 사람이 다음 결산에서 한 달 치 복귀 시간을 인정받는 오류를 막기 위해 실제 시작 날짜를 검사합니다.

## 저장본과 호환성

저장본을 열었다는 이유만으로 과거 세계의 경제 규칙을 바꾸지 않습니다. 안정화 전환은 명시적인 명령이며, 전환 시 부채·준비자산·인구·설비·프로젝트·난수 상태를 보존합니다.

| 구분 | 저장되는 버전 |
| --- | --- |
| 기존 MVP·경제 v1/v2 저장본 | save schema 1은 지원되는 기본값으로 복원, 이후 schema 2 사용 |
| 안정화 적용 저장본 | save schema **3** |
| 공통 세계 구조 | world schema **2** |
| 안정화 경제 데이터·계산 규칙 | economy version **3**, model revision **3** |
| 정부 신용·지역 노동 배분 | credit version **1**, labour version **1** |

버전 번호는 게임의 이름과 다릅니다. 이번 작업은 Economy & Demography Engine v1의 안정화 확장입니다. 알려지지 않은 버전이나 저장 형식과 계산 규칙이 맞지 않는 파일은 명확한 오류로 거절합니다. 새 저장본에는 신용 상태, 국내·해외 부채, 미지급금, 지역별 노동 배분, 진행 중인 이동·훈련 과정과 완료 예정일이 함께 들어갑니다.

## 로컬 실행 예시

아래 명령은 프로젝트 폴더에서 PowerShell로 실행합니다. `synthetic_economy`는 가상 국가 예제입니다. `korea_1897`은 아직 빈 골격이므로 경제 실험에는 가상 예제를 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m worldline new saves\stabilized.sqlite --scenario scenarios\synthetic_economy.json --stabilized --seed 1897
.\.venv\Scripts\python.exe -m worldline constraints saves\stabilized.sqlite a 500
.\.venv\Scripts\python.exe -m worldline advance saves\stabilized.sqlite 1 month
.\.venv\Scripts\python.exe -m worldline explain saves\stabilized.sqlite a
.\.venv\Scripts\python.exe -m worldline audit saves\stabilized.sqlite
.\.venv\Scripts\python.exe -m worldline save saves\stabilized.sqlite stabilization-demo
```

`constraints`는 비용과 위험의 견적이며 실행 명령이 아닙니다. `explain`에는 실제 생산 요인, 재정 원인, 노동 이동과 병목이 포함됩니다. `audit`는 모순을 검사하고 오류와 주의사항을 구분합니다.

기존 저장본은 새 SQLite 파일로 복원한 뒤 전환합니다. 처음 만든 economy version 1 저장본에는 다음 순서를 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m worldline load saves\old.json saves\migrated.sqlite
.\.venv\Scripts\python.exe -m worldline economy-upgrade saves\migrated.sqlite
.\.venv\Scripts\python.exe -m worldline economy-stabilize saves\migrated.sqlite
```

이미 보정된 economy version 2라면 `economy-upgrade`를 생략하고 `economy-stabilize`만 실행합니다. 이미 안정화한 세계에 다시 전환 명령을 실행하면 오류를 반환합니다. 필요한 경우 전환 전에 별도 이름으로 저장하세요.

예비병 1,000명 동원과 전체 전역은 다음과 같습니다.

```powershell
.\.venv\Scripts\python.exe -m worldline mobilize saves\stabilized.sqlite a ra 1000
.\.venv\Scripts\python.exe -m worldline mobilize saves\stabilized.sqlite a ra 0
```

## 후속 NPC 계층의 연결 예시

다음은 개발자가 연결할 때 사용하는 예시입니다. 사용자가 직접 구현 방법을 결정할 필요는 없습니다. 모든 실행은 같은 엔진 명령·검사·기록을 거칩니다.

```python
from worldline import Engine, Order

engine = Engine("saves/stabilized.sqlite")

# 실제 비용과 지도부의 인식을 각각 반환한다. 인식이 실행 비용을 바꾸지는 않는다.
quote = engine.policy_constraints(
    "a", 500,
    perceived={"cost": 250, "risk": 0.1, "bias_factors": ["optimism_bias"]},
)

# 철도 계획과 비용 조회. 자금·물자·노동 부족은 서로 다른 원인으로 표시된다.
engine.plan_project("rail_demo", "a", "ra", "infrastructure", "railways", 1)
project_quote = engine.policy_constraints("a", project_id="rail_demo")

# 위험을 감수하는 선택은 명시적으로 비상조달을 허용한다.
result = engine.submit(Order(
    "fund-rail-demo", "a", "fund_project",
    {"project_id": "rail_demo", "allow_emergency": True,
     "instruments": ["domestic_war_bonds", "forced_loans"]},
))

# 같은 원칙으로 지출, 동원, 원금 상환과 채무조정 명령을 제공한다.
spending = engine.fiscal_action(
    "a", 500, purpose="war", allow_emergency=True,
    instruments=["domestic_war_bonds", "foreign_borrowing"],
)
mobilization = engine.mobilize("a", "ra", 1000)
repayment = engine.repay_debt("a", 10)
workout = engine.restructure_debt("a")
engine.close()
```

각 결과의 `accepted`/`rejected` 상태와 이유를 확인합니다. 원금 상환에는 실제 준비자산과 미상환 원금이 필요합니다. 채무조정은 미지급 또는 채무불이행이 있는 국가에서만 시작할 수 있으며, 부채를 자동으로 탕감하거나 새로운 돈을 만들어주지 않습니다.

별도의 조회 함수로 `can_borrow`, `can_fund_war`, `can_afford_project`, `estimated_fiscal_risk`, `available_labour`, `available_skilled_labour`, `project_resource_constraints`를 제공합니다. 전쟁 조회에서도 군인으로 동원할 수 있는 지역별 인원과 물자를 함께 확인합니다. 정부가 원하면 객관적으로 위험한 결정을 선택할 수 있고, 엔진은 실제 조달과 결과 비용을 계산합니다.

`true_state`에는 객관적인 비용과 위험을, `perceived_state`에는 후속 NPC가 제공한 예상 비용·위험·편향 정보를 담습니다. 낙관, 명예, 공포, 이념, 오정보 등의 동기를 기록할 수 있지만 이번 단계에서는 NPC 의사결정을 구현하지 않았습니다. 인식 정보는 실제 노동자나 돈을 늘리지 않습니다.

## 현재 범위

신용과 비상재정은 장기 게임을 위한 설명 가능한 근사입니다. 비상조달 한도는 초기 경제 기반에서 계산한 유한한 동원 가능 몫이며, 세밀한 조세행정·채권시장·은행·가격·환율을 표현하지 않습니다. 노동은 연속적인 인원 규모로 계산하고 동원 명령은 정수 인원을 받습니다. 연령·성별 동원 가능 비율은 단순화되어 있으며 개인별 직업과 임금, 구체적인 이주 노선은 아직 계산하지 않습니다.

자동 위기 발생, NPC AI, 전투, 실제 1897년 역사 자료, MCP와 UI는 후속 계층의 범위입니다. 현재는 로컬 싱글플레이 엔진의 계산·저장·검증과 연결 인터페이스를 제공합니다.
