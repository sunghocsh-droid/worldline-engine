# Worldline Engine — Economy v3 · NPC v1 · Crisis v1 · Military & Logistics v1

SQLite에 저장한 실제 세계를 기준으로 국가를 운영하는 로컬 싱글플레이 엔진입니다. 기존 MVP, **Economy v3**, **NPC Decision Engine v1**, **Crisis & Event Engine v1**, **Performance v1**을 유지하며 **Military & Logistics v1**을 추가했습니다. 전선·작전·공성·함대 수준으로 전쟁과 보급을 처리하고, 실제 동원·생산·훈련·수송·손실이 경제와 사건·NPC에 돌아옵니다. 개별 병사의 전술전투·MCP·그래픽 UI는 없습니다.

경제 제약은 정치적 선택을 대신하지 않습니다. 정상적인 한도를 넘는 선택도 실제 비상조달 수단이 남아 있으면 실행할 수 있고, 그 비용은 부채·미지급금·민간경제·신용·사회불안에 남습니다. 사람과 자원은 중복 사용하지 않습니다. GDP를 목표값에 맞춰 지정하지 않습니다.

- [군사·병참·혁신 구조와 사용 안내](MILITARY_LOGISTICS_GUIDE.md)
- [군사 통합 실험과 실제 테스트](MILITARY_LOGISTICS_REPORT.md)
- [군사 이후 성능 개선과 실제 측정](MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md)
- [현재 계산·검증·저장 구조](MILITARY_PERFORMANCE_CURRENT_STATE.md) / [runtime·verification 사용 안내](MILITARY_PERFORMANCE_GUIDE.md)
- [사건 엔진의 설계와 보존 규칙](CRISIS_DESIGN.md)
- [사건·보고·대응·플레이어 결정 사용 안내](CRISIS_EVENT_GUIDE.md)
- [30년 자율세계 검증과 한계](CRISIS_EVENT_REPORT.md)
- [NPC의 판단·기억·관계·저장과 사용 안내](NPC_DECISION_GUIDE.md)
- [20년 가상 NPC 실험과 한계](NPC_SIMULATION_REPORT.md)
- [경제 안정화 결과와 10·20·30년 비교](ECONOMIC_STABILIZATION_REPORT.md)
- [사용 방법과 설계 설명](ECONOMIC_STABILIZATION_GUIDE.md)
- [실제 테스트 결과](TEST_RESULTS.md)
- [새 비교 원시 JSON](benchmarks/stabilization/stabilized.json) / [CSV](benchmarks/stabilization/stabilized.csv)
- [기존 경제 보정 보고서](ECONOMIC_CALIBRATION_REPORT.md)
- [기존 구조 감사](ARCHITECTURE_AUDIT.md) / [경제 v1 설계](ECONOMY_DESIGN.md) / [초기 설계](DESIGN.md)

기존 `economy-v1-calibrated` 복원 지점은 프로젝트 바깥 `../checkpoints/economy-v1-calibrated/`에 그대로 남아 있습니다. 이번 변경으로 그 백업을 수정하지 않았습니다.

## 실행

아래 명령은 이 프로젝트 폴더에서 PowerShell로 실행합니다. 이 성능 작업 복사본에는 재생성 가능한 `.venv`를 포함하지 않았습니다. 이번 검증은 기존 `C:\Users\c\Documents\Codex\2026-09-30\worldline-engine-chatgpt-llm-gdp-python\outputs\worldline-engine\.venv\Scripts\python.exe`(Python 3.12.14, pytest 9.1.1)로 실행했습니다. 이 복사본에 `.venv`를 준비하지 않았다면 아래 예시의 실행 파일을 그 기존 경로로 바꿔 실행하십시오.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m worldline new demo.sqlite --scenario scenarios/synthetic_economy.json --seed 1897 --stabilized
.\.venv\Scripts\python.exe -m worldline advance demo.sqlite 12 month --autosave saves
.\.venv\Scripts\python.exe -m worldline explain demo.sqlite a
.\.venv\Scripts\python.exe -m worldline constraints demo.sqlite a 1000
.\.venv\Scripts\python.exe -m worldline save demo.sqlite first-year --directory saves
```

`synthetic_economy`는 가상 국가 예제입니다. `korea_1897`은 역사 자료를 아직 채우지 않은 골격이므로 경제 초기화에 사용할 수 없습니다. 이미 존재하는 DB 이름 대신 새 이름을 사용하세요.

## 기존 게임 호환

기존 저장본은 불러올 때 기존 규칙을 유지합니다. 보정 전 경제 version 1은 먼저 `economy-upgrade`, 보정된 경제 version 2는 바로 `economy-stabilize`로 전환합니다. 업그레이드는 기존 자산·인구·부채·프로젝트·RNG를 보존하고 사건 기록에 남습니다.

```powershell
.\.venv\Scripts\python.exe -m worldline load old-save.json restored.sqlite
# 경제 version 1인 경우에만 먼저 실행
.\.venv\Scripts\python.exe -m worldline economy-upgrade restored.sqlite
.\.venv\Scripts\python.exe -m worldline economy-stabilize restored.sqlite
```

경제 안정화만 활성화한 게임은 **save schema 3 / World schema 2 / economy version 3 / model revision 3 / stabilization version 1**을 사용합니다. NPC를 명시적으로 활성화한 게임은 **save schema 4 / World schema 3 / NPC state 1**을 사용합니다. 사건까지 활성화하면 **save schema 5 / World schema 4 / crisis state 1**을 사용하며 경제·NPC 버전은 유지합니다. 군사까지 명시적으로 활성화하면 **save schema 6 / World schema 5 / Military state 1**입니다. 시스템 호출 버전은 모두 1이고 프로젝트 → 경제 → NPC → 사건 → 군사 순서를 저장·검증합니다. 이전 save schema 1~5는 기존 규칙을 유지하며 NPC·사건·군사 기능이 자동 생성되지 않습니다. 활성화 방법은 각 사용 안내에 있습니다.

군사 보충의 정치적 승인은 선택적 `politics_version=1` 기록입니다. 이 기록이 없는 초기 군사 save 6도 복원하며, 과거의 임의 찬성 수치를 새 승인으로 해석하지 않습니다. 실제 지속능력, 기관이 받은 정보, 보충·유보 판단과 일회용 집행 승인을 구분합니다.

## 파일 구조

```text
worldline/
  engine.py, models.py, persistence.py, audit.py   # 기존 세계·SQLite·기록·저장·검사
  economy.py, demography.py, calibration.py        # 기존 경제·인구·보정
  projects.py                                    # 공통 사업 진행
  credit.py                                      # 신용·차입·비상조달·채무불이행
  labour.py                                      # 지역 노동배분·이동·훈련·동원
  guardrails.py                                  # 비용·자원·위험의 읽기 전용 조회
  order_execution.py                             # 플레이어와 NPC의 공통 명령 검증·실행
  npc_state.py                                   # 개인·집단·정보·기억·관계·선대 유산
  npc_runtime.py                                 # 날짜·기억 감소의 제한된 순수 계산 캐시
  npc_decision.py                                # 관측 상태만 사용하는 판단·반응·설명
  npc.py                                         # 정보 전달·일정·내각·권한과 실제 실행
  npc_benchmark.py                               # 가상 NPC 장기 실험과 원시 기록
  crisis_state.py, crisis_risk.py                 # 사건 상태·압력·지역 위험과 발생 검사
  crisis.py, crisis_effects.py                    # 인과 경로·진행·중복 없는 예약 결과
  crisis_response.py                             # 보고·명령 지연·NPC 대응·플레이어 결정
  crisis_benchmark.py                            # 30년 가상 자율세계와 극단조건 검증
  state_storage.py                               # SQLite 불변 보관소·현재 참조·과거 버전 보존
  state_runtime.py, incremental_audit.py          # 변경 journal·rollback·기존 JSON 검사의 증분 유효성
  military_active.py                             # 미완료 작전·cargo·명령의 derived index
  performance_benchmark.py                        # 원본 비교·장기 계측·규모별 성능 실험
  military_state.py, military_bridge.py            # 범용 행위자·부대·동원·조달·피해·보충
  military_logistics.py                            # 육상·해상 노선·항만·공유 처리량·유한 재고
  military_operations.py, military_combat.py       # 지연 명령·이동·전선·공성·함대 판정
  military_innovation.py, military_politics.py      # 유료 혁신·교리·기관 판단·보충 승인
  military.py                                     # 일정·기존 사건·보고·기억 연결
  military_benchmark.py                            # 실제 3년 전쟁·저장 복원·성능 실험
  military_innovation_benchmark.py                 # 가상 기반의 5종 유료 혁신 시험
  benchmark.py, calibration_report.py             # 기존 장기 검증
  stabilization_benchmark.py                     # 이번 장기 비교·원인 분해
  __main__.py                                    # CLI
tests/                                           # Military v1의 1,136개 유지 + 성능·저장 회귀 117개
scenarios/                                       # 가상 예제와 빈 korea_1897
examples/                                        # 기존 저장 예제
benchmarks/                                      # 이전·이후 원시 결과
```

## 장기 검증 재실행

```powershell
.\.venv\Scripts\python.exe -m worldline.stabilization_benchmark --workers 3
.\.venv\Scripts\python.exe -m worldline.npc_benchmark --months 240
.\.venv\Scripts\python.exe -m worldline.crisis_benchmark --months 360
```

3개 가상 profile × 5개 정책 경로를 30년 실행하고 10·20·30년 결과를 저장합니다. 실제 게임과 같은 경제·프로젝트 함수를 사용하며, 매월 수치 보존과 설명 결과를 검사합니다. 같은 seed·초기조건·명령의 재현성 및 SQLite 엔진과의 일치도 pytest로 확인합니다.

NPC 실험은 가상 국가 4개와 개인 16명을 20년 진행합니다. 국내 경제 보고·받은 외교 정보·개인 및 기관의 기억·집단 이해관계를 사용합니다. 전쟁 준비에는 실제 지출과 동원 비용이 들지만 전투 승패를 생성하지 않습니다. 전체 실험 기록은 `benchmarks/npc/`에, 해설은 [NPC_SIMULATION_REPORT.md](NPC_SIMULATION_REPORT.md)에 저장합니다. 최종 테스트 개수와 실행 결과는 [TEST_RESULTS.md](TEST_RESULTS.md)를 참고하세요.

사건 실험은 가상 국가 4개와 개인 20명을 30년 진행합니다. 지역 위험·실제 재정·식량·고용·외교 상태가 사건의 근거가 됩니다. 압축된 원시 결과와 전체 시스템 사건 로그는 `benchmarks/crisis/`에 보관합니다. 실제 전투 결과를 생성하지 않습니다.

현재 NPC v1과 사건 v1은 제한된 정책·대응 목록을 사용합니다. 현지 독단과 사회적 불안은 위기 경로로 연결하지만 자동 쿠데타·혁명·반란 전투를 완성하지 않습니다. 군사 v1은 작전·공성·함대와 유한 병참을 계산합니다. 세밀한 물가·환율·은행·채권시장, 개별 전술전투·점령사회·전국 전쟁전략 AI, MCP, 실제 역사 자료 대량 입력은 후속 단계입니다. 비상조달·노동 이동·NPC 판단·재난 피해·군사 판정 계수는 설명 가능한 게임용 근사이며 실제 시나리오 자료와 후속 검증으로 보정할 대상입니다.

성능 최적화는 경제계수·판단식·사건확률·평가 일정·RNG를 유지합니다. 내부 조회와 기억 계산의 반복을 줄이고 SQLite에 불변 과거 자료를 분리해 보관합니다. 기존 JSON/SQLite 저장은 계속 복원하며, 새 JSON 백업에는 과거 버전을 보존하는 선택적인 자료가 추가될 수 있습니다. 사용법과 저장 구조는 [PERFORMANCE_STATE_GUIDE.md](PERFORMANCE_STATE_GUIDE.md), 실제 측정과 제한은 [PERFORMANCE_STATE_OPTIMIZATION_REPORT.md](PERFORMANCE_STATE_OPTIMIZATION_REPORT.md), 최종 테스트 결과는 [TEST_RESULTS.md](TEST_RESULTS.md)를 참고하세요.
