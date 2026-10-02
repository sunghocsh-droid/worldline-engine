# Military v1 이후 실제 플레이 성능 개선 보고

게임 규칙·결과·기록을 보존하면서 transaction과 저장 경로를 개선했습니다. 모든 측정은 완료된 실제 실행의 원시 증거에 연결됩니다. 목표 달성 범위와 후반 월의 남은 지연을 함께 보고합니다.

## 보존과 범위

수정 전 Military v1은 `../checkpoints/military-v1-stable/`의 ZIP(744,919,952 bytes), 320개 파일의 SHA-256 manifest와 frozen source로 보존했습니다. 원격 push는 하지 않았습니다. 기존 1,136개 테스트와 원본 69개 Python 검사 파일은 유지합니다. 최종 생산 코드 43개 파일의 hash는 모든 최종 캠페인과 테스트에 연결됩니다.

Military save schema 6 / World schema 5 / Military state 1 / Economy 3 / NPC 1 / Crisis 1을 유지합니다. save 1~5의 load 의미, player pending decision과 fog-of-war 경계도 유지합니다. 경제·NPC 판단과 reason trace·Crisis·군사 작전·casualty·logistics·command timing·innovation·event ordering·RNG 결과와 호출 의미를 최적화의 조절 변수로 사용하지 않았습니다. 기억·보고·작전 기록을 삭제하거나 요약하지 않았습니다.

## 먼저 측정한 실제 병목

코드 변경 전에 frozen industrial_front(seed 1731)를 cProfile로 실행했습니다. 세부 범주를 나누는 벽시계 profiler는 보존된 동일 before source와 최종 source에서 별도로 실행했습니다. 아래는 마지막 월 progress까지의 **배타적 wall time**입니다. 하위 비용은 부모에서 차감하므로 더할 수 있습니다. Auditor의 inclusive time에는 JSON·validation 등이 포함되므로 별도로 더하지 않습니다. 프로파일러 자체 비용과 호스트 경합이 포함되어 있어 일반 플레이 시간과 구분합니다.

| 범주 | before 초 | 최종 runtime 초 |
| --- | --- | --- |
| World/state deepcopy | 14.2321 | 3.0770 |
| SQLite transaction | 0.3988 | 0.4540 |
| SQLite load | 8.2590 | 0.1948 |
| SQLite write | 0.4447 | 0.8917 |
| logical world reconstruction | 9.9097 | 0.1493 |
| JSON serialization | 28.8627 | 22.6216 |
| validation | 5.0628 | 4.3569 |
| Auditor | 0.0276 | 0.0266 |
| Economy | 0.0172 | 0.1002 |
| NPC | 0.5411 | 1.0112 |
| Crisis | 0.2277 | 0.4340 |
| Military scheduler | 0.0070 | 0.0297 |
| logistics | 0.0278 | 0.1256 |
| movement | 0.0007 | 0.0037 |
| operations/combat | 0.0166 | 0.0503 |
| innovation/politics | 0.0029 | 0.0023 |
| Event Ledger | 0.0127 | 0.0111 |
| historical archive | 2.4815 | 0.3724 |
| component persistence | 0.0000 | 0.0275 |
| projects | 0.0217 | 0.0789 |
| benchmark observations | 0.0243 | 0.0263 |
| unattributed/harness | 1.3777 | 3.3198 |

보조 profiler 값인 Auditor 함수 inclusive 시간 누계는 39.53→13.73초, validation 누계는 30.27→8.98초입니다. JSON·validation·copy 및 동일 범주 내부 중첩도 중복 포함한 누계이며 실제 단일 감사 경계 wall이 아닙니다. 배타적 합에 더하지 않습니다.

전체 계측 구간 71.96→37.37초; 최종 증거 export 포함 74.62→41.67초입니다. 초기 top은 JSON, World/state copy, logical reconstruction, SQLite load였습니다. 군사 계산 자체는 그보다 작은 비용입니다. 최종 공개 조회는 `Engine._stored_payload`의 JSON C clone을 사용하므로 사본 비용 일부가 JSON 범주에 들어갑니다.

최종 profiler의 3개월 orders·명령 21.67초, Engine.advance 13.96초입니다. 81개 독립 비교는 World/RNG/Ledger와 전체 committed engine, archive 원본 bytes까지 일치했습니다.

원시 증거: [벽시계 비교](../measurements/measurement-inventory/stage3-disjoint-wall-comparison.json), [변경 전 cProfile](../measurements/before-profile/top.txt), [최종 3개월 전체 비교](../measurements/measurement-inventory/stage3-profile3-full-equivalence.json).

## 구조 변경과 검증 계층

- 전체 World deepcopy로 rollback 대상을 만들던 경로를 record/container mutation journal과 일별·outer frame으로 바꿨습니다. 변경된 객체의 이전 값, RNG·seed·system metadata를 보존하며 실패하면 모두 복원합니다. 독립 공개 사본과 제한된 경제 후보 copy는 유지합니다.
- 임시 shallow copy에는 journal metadata를 복사하지 않습니다. 기존 shallow copy의 nested reference 공유는 유지하되 임시 객체가 live graph의 가짜 parent로 등록되지 않도록 했습니다.
- 매 command의 전체 logical World 복원·전체 current payload 쓰기 대신 dirty component row와 작은 revision manifest를 씁니다. SQLite source of truth, 다른 writer/같은 connection의 변경 감지, 불변 archive body·영구 catalog·현재 head·Ledger의 atomic commit을 유지합니다.
- Runtime은 기존 NPC/Crisis bulk JSON 검사에서 journal이 증명한 변경 없는 subtree의 유효성을 재사용합니다. 신규·교체·변경 subtree와 list는 검증합니다. 매월 첫날과 verification 매일에는 이 기존 JSON 검사도 전체 수행합니다. Explicit save/load/checkpoint는 full snapshot과 Auditor를 검사합니다. Verification의 매일 전체 World를 직렬화해 저장하는 경로는 만들지 않습니다.
- 자원 중복, 불가능한 음수, 참조, casualty, receipt, 기억·보고, pending decision과 정보 경계의 기존 의미 검사는 runtime에서도 매 tick 전체 범위를 유지합니다. 역참조 누락 가능성이 있는 sparse semantic audit은 채택하지 않았습니다.
- active operation/미완료 cargo(이동·중단·대상/저장공간 대기 포함)/pending command/최신 command·convoy 참조를 index로 찾습니다. 완료 기록은 저장하고 계산 후보만 분리합니다. 부대의 날짜별 maintenance/supply/readiness와 원래 attrition_due 주간 일정, manpower=0 예외, 평시 분기, 순서·RNG는 그대로입니다. 모든 route/formation 계산을 무조건 생략하는 fast path는 만들지 않았습니다.

자세한 현재 구조는 [MILITARY_PERFORMANCE_CURRENT_STATE.md](MILITARY_PERFORMANCE_CURRENT_STATE.md), 모드 선택은 [MILITARY_PERFORMANCE_GUIDE.md](MILITARY_PERFORMANCE_GUIDE.md)에 있습니다.

## 3 / 12 / 36개월 before / after

4국가·20 NPC의 동일 가상 시나리오, seed 1731, 동일 initial state와 원래 campaign controller/명령을 사용했습니다. 3·12는 동일 36개월 실행의 해당 경계까지 누계입니다. Simulation은 월별 orders+advance의 실제 wall 합이며 save/load·측정 callback·최종 증거 export는 제외합니다. 아래는 before를 보존한 실제 Military v1 실행과 비교합니다. before의 생산 코드 40개는 frozen source와 정확히 일치합니다.

최종 4개 캠페인과 기존 장기 테스트를 6 logical CPU/16GB Windows 호스트에서 병행했습니다. 실제 경합과 변화하는 부하가 시간에 포함됩니다. before의 과거 실행 부하와 같다고 주장하거나 CPU 시간으로 보정하지 않습니다. 과거 36개월을 새로 완주한 실행으로 가장하지 않습니다. 새 before 12개월 실행과 source·save 일치 증거도 별도로 보존했습니다.

| scenario | 개월 | before 총 초 | runtime 총 초 | before 초/월 | runtime 초/월 | before 최악 월 | runtime 최악 월 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 산업전 | 3 | 67.42 | 21.57 | 22.47 | 7.19 | 38.77 | 10.98 |
| 산업전 | 12 | 1,053.44 | 297.53 | 87.79 | 24.79 | 156.02 | 45.12 |
| 산업전 | 36 | 8,489.06 | 2,338.89 | 235.81 | 64.97 | 482.47 | 127.69 |
| 전근대 | 3 | 66.37 | 20.89 | 22.12 | 6.96 | 41.60 | 11.74 |
| 전근대 | 12 | 1,355.21 | 408.17 | 112.93 | 34.01 | 213.98 | 75.18 |
| 전근대 | 36 | 10,791.48 | 2,874.51 | 299.76 | 79.85 | 570.29 | 141.63 |
| 해상원정 | 3 | 73.74 | 20.65 | 24.58 | 6.88 | 42.15 | 11.17 |
| 해상원정 | 12 | 1,307.22 | 357.49 | 108.93 | 29.79 | 209.75 | 74.13 |
| 해상원정 | 36 | 10,569.87 | 2,715.82 | 293.61 | 75.44 | 538.16 | 129.43 |

산업전의 두 검증 모드를 같은 조건으로 별도로 실제 실행했습니다. 전근대·해상원정의 최종 36개월 full-audit 성능 실행은 추가하지 않았으며 두 scenario의 runtime 완주와 기존 frozen full-audit 결과를 비교했습니다. 신규 단기 모드 동일성 검사도 세 scenario를 포함합니다.

| 모드 | 개월 | simulation 총 초 | 초/월 | 최악 월 초 | 경계 wall 초¹ |
| --- | --- | --- | --- | --- | --- |
| 산업전 runtime | 3 | 21.57 | 7.19 | 10.98 | 33.92 |
| 산업전 runtime | 12 | 297.53 | 24.79 | 45.12 | 358.45 |
| 산업전 runtime | 36 | 2,338.89 | 64.97 | 127.69 | 2,570.61 |
| 산업전 verification | 3 | 28.01 | 9.34 | 15.40 | 41.67 |
| 산업전 verification | 12 | 368.84 | 30.74 | 56.59 | 431.59 |
| 산업전 verification | 36 | 2,698.82 | 74.97 | 136.56 | 2,920.87 |

¹ 경계 wall은 setup·관측·그 경계까지의 save/load/equality를 포함하며 최종 result export 전 시간입니다. Simulation과 다른 범위입니다.

| 완료 실행 | simulation 총 초 | 전체 wall 초 | save/load/equality IO 합 초 | 최종 증거 처리 초 |
| --- | --- | --- | --- | --- |
| 산업전 runtime | 2,338.89 | 2,604.19 | 201.95 | 24.85 |
| 산업전 verification | 2,698.82 | 2,949.77 | 191.42 | 21.50 |
| 전근대 runtime | 2,874.51 | 3,102.91 | 172.60 | 17.44 |
| 해상원정 runtime | 2,715.82 | 2,948.09 | 175.89 | 18.34 |

원본 36개월 전체 wall은 산업전 8,600.09초, 전근대 10,889.86초, 해상원정 10,670.58초입니다. 원본의 별도 observer·midpoint checkpoint 비용과 최종 harness의 3/12/36 IO 비용은 서로 다르므로 순수 simulation 비교를 우선합니다.

## 메모리 · SQLite · save / load

RSS는 Windows WorkingSet이며 경계 RSS는 그 경계의 save 직전입니다. Peak는 앞선 경계의 IO까지 포함한 process PeakWorkingSet입니다. 과거 캠페인 RSS가 기록되지 않은 값은 추정하지 않습니다. 아래 MiB는 bytes/2²⁰입니다.

| 완료 실행 | 개월 | 경계 RSS MiB | 경계 전 peak MiB | IO 후 peak MiB | DB MiB | save 초 | JSON load 초 | save MiB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 산업전 runtime | 3 | 143.0 | 163.5 | 420.9 | 9.9 | 3.86 | 5.33 | 33.7 |
| 산업전 runtime | 12 | 272.6 | 420.9 | 1,300.1 | 37.2 | 16.18 | 21.64 | 119.0 |
| 산업전 runtime | 36 | 678.1 | 1,300.1 | 4,976.9 | 148.6 | 59.03 | 66.33 | 484.3 |
| 산업전 verification | 3 | 143.1 | 164.4 | 425.3 | 9.9 | 4.38 | 5.74 | 33.7 |
| 산업전 verification | 12 | 271.8 | 425.3 | 1,304.8 | 37.2 | 17.20 | 20.31 | 119.0 |
| 산업전 verification | 36 | 679.4 | 1,304.8 | 4,945.6 | 148.6 | 56.99 | 60.00 | 484.3 |
| 전근대 runtime | 3 | 131.0 | 152.3 | 398.8 | 9.2 | 3.61 | 5.17 | 30.8 |
| 전근대 runtime | 12 | 292.5 | 398.8 | 1,255.1 | 36.5 | 19.40 | 21.25 | 112.8 |
| 전근대 runtime | 36 | 703.7 | 1,255.1 | 4,389.5 | 134.0 | 47.89 | 49.68 | 422.0 |
| 해상원정 runtime | 3 | 127.8 | 144.5 | 376.8 | 8.7 | 3.25 | 5.03 | 28.5 |
| 해상원정 runtime | 12 | 285.8 | 376.8 | 1,187.1 | 34.3 | 16.04 | 21.75 | 105.1 |
| 해상원정 runtime | 36 | 723.2 | 1,187.1 | 4,325.9 | 131.7 | 48.01 | 54.15 | 413.0 |

원본의 추가 12개월 반복 계측에서 3/12개월 메모리·DB·IO도 실제로 측정했습니다. 이는 위 before 36개월 과거 캡처와 별도 실행이며 새 전체 36개월 실행이 아닙니다. 3/12 World·RNG·Ledger와 save bytes는 최종 실행과 같습니다.

| frozen 추가 반복 | simulation 총 초 | 경계 RSS MiB | IO 후 peak MiB | DB MiB | save 초 | JSON load 초 |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | 66.85 | 151.4 | 407.4 | 10.4 | 3.96 | 4.86 |
| 12 | 1,013.91 | 309.7 | 1,288.3 | 38.6 | 12.27 | 13.68 |

원본 산업전 36개월 DB는 151.965MiB입니다. 원본의 최종 DB를 읽기 전용 backup으로 다시 측정한 JSON save/load는 52.81/56.28초, SQLite open/resume은 16.33초, IO process peak는 4.61GiB였습니다. 이는 역사 캠페인의 RSS peak가 아닙니다. 측정 전후 원본 DB hash는 동일합니다. Full JSON export는 전체 기록을 포함하므로 크기와 메모리 비용이 남습니다.

3·12·36개월 산업전 save는 before/runtime/verification 파일 bytes와 SHA-256까지 동일합니다(35,298,965 / 124,748,239 / 507,796,369 bytes). 세 scenario의 모든 save/load도 World·RNG·Ledger exact equality를 통과했습니다. 실제 mode별 IO 및 DB size는 같은 결과여도 호스트 부하·물리 row 배치 때문에 달라질 수 있습니다.

최종 산업전 DB의 save/load도 캠페인 동시 export와 구분하여 순차 재측정했습니다. 아래는 IO 전용 process의 peak이며 gameplay peak가 아닙니다. before 재측정의 당시 부하 조건은 해당 metadata에 별도로 보존했습니다.

| 36개월 DB의 IO 재측정 | SQLite resume 초 | save 초 | JSON load 초 | IO peak MiB | save MiB |
| --- | --- | --- | --- | --- | --- |
| frozen before | 16.33 | 52.81 | 56.28 | 4,722.4 | 484.3 |
| 최종 runtime 순차 IO | 15.29 | 45.29 | 48.98 | 4,811.7 | 484.3 |

## 결과 동일성과 테스트

| 비교 | 동일 | 독립 비교 수 | 차이 |
| --- | --- | --- | --- |
| industrial-runtime-frozen | True | 81 | 0 |
| industrial-verification-frozen | True | 81 | 0 |
| preindustrial-runtime-frozen | True | 81 | 0 |
| sea-runtime-frozen | True | 81 | 0 |
| industrial-runtime-verification | True | 81 | 0 |

실제 SQLite 행도 별도 streaming 검사로 확인했습니다. 세 scenario의 Ledger 원문·논리 hash, archive 압축/원문 bytes, catalog/head 행수가 원본과 같으며 기록을 삭제해 얻은 속도가 아닙니다.

| scenario | Ledger 행 | Archive 행 | Catalog 행 | Head 행 | 압축 archive bytes | 원문 archive bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 산업전 | 15342 | 15153 | 18724 | 15066 | 18,348,239 | 117,912,321 |
| 전근대 | 17316 | 17285 | 20248 | 16198 | 16,934,360 | 101,313,987 |
| 해상원정 | 19261 | 18368 | 21863 | 18641 | 16,238,414 | 99,605,174 |

비교는 World 각 component, Economy, NPC record/판단/reason trace/기억/보고, Crisis, Military 각 bucket(formation/supply/cargo/operation/casualty/control/innovation/politics), RNG, 순서를 포함한 Ledger, 전체 committed engine의 seed/system/observed/order/journey/pending 및 archive/catalog/head의 원본 bytes를 포함합니다. Hash만 비교한 것이 아니라 구조·행 자체의 equality도 검사했습니다. 모든 campaign의 월별 결과 행과 원래 명령 결과도 동일합니다.

**기존 1,136 + 신규 117 = 1,253 passed**, 실패·오류·건너뜀 0입니다. Core 1,197, 추가 SQL read 2, Crisis/NPC 35, Military campaign 19의 서로 겹치지 않는 실제 실행 합집합입니다. JUnit 시간 합 4,610.80초는 단일 실행의 wall time이 아닙니다.

| 신규 검사 파일 | 통과 |
| --- | --- |
| test_incremental_json | 15 |
| test_incremental_persistence | 11 |
| test_military_active_runtime | 10 |
| test_runtime_invariants | 76 |
| test_runtime_shallow_copy | 5 |

신규 검사는 dirty persistence·다른 writer/같은 connection 변경·rollback·다일 진행 실패·archive write 이후 실패·dirty isolation·active set/동률/overflow·runtime/full audit·save/resume·history 보존·중복 casualty/resource consumption·RNG·pending/fog 경계를 포함합니다. 기존 30년 Crisis와 20년 NPC 및 checkpoint replay 조건도 변경하지 않았습니다.

증거: [실제 테스트 합집합 설명](../measurements/completed-test-summary.json), [JUnit 합집합](../measurements/pytest-completed-union.xml), [최종 source와 원본 테스트 hash](../measurements/final-source-manifest.json), [동일성 결과](../measurements/equivalence/).

## 남은 병목과 실제 플레이 판단

명령 controller는 한 달에 다수의 독립 공개 World 조회를 수행합니다. 성숙한 13개월 profiler에서는 54회 true_state 조회가 큰 비용이었습니다. 최종 JSON clone으로 Python 재귀 복원 비용은 줄었지만 전체 사본의 크기는 줄이지 않았습니다. 매 tick의 전체 의미 검사, 누적 receipt·report·memory의 참조 검사, archive 현재 참조 조립과 제한된 경제 후보 deepcopy도 남습니다. 성숙한 캠페인의 시간은 기록 증가에 따라 커집니다.

다른 장기 실행이 종료된 뒤 같은 36개월 DB를 복사하여 37개월 한 turn을 두 모드로 따로 측정했습니다. 프로파일링 비용은 제외하고 SQLite resume과 명령/advance를 분리했습니다.

| 모드 | SQLite resume 초 | orders·명령 초 | 1개월 advance 초 | turn 합 초 |
| --- | --- | --- | --- | --- |
| runtime | 14.59 | 81.24 | 17.56 | 98.80 |
| verification | 15.02 | 88.90 | 26.22 | 115.12 |

두 turn의 전체 World/RNG/Ledger hash도 동일합니다. 단독 runtime의 순수 월 진행 17.56초는 수십 초 범위에 들어왔습니다. 그러나 명령·반복 공개 조회를 포함한 전체 turn은 98.80초이고 장기 실행의 최악 월도 127.69초이므로 **대표 캠페인의 전체 월 turn을 수초~수십초 이하로 낮추는 목표는 미달**입니다. 큰 full JSON checkpoint/export를 매 turn 요청하면 별도 IO 시간이 추가됩니다.

**Diplomacy & Intelligence**: 저장·rollback·동일성 기반은 준비되어 설계 작업은 가능합니다. 실제 플레이용 대규모 모델 추가는 현재 보류를 권합니다. 먼저 공개 조회의 사용량과 전체 의미 검사 비용을 줄이고 실제 1592 규모를 검증해야 합니다. 다음 성능 작업은 기존 공개 조회 경로와 안전한 incremental 의존성 설계·검증입니다. 이번 작업에서는 이를 위해 규칙을 단순화하거나 검사를 끄지 않았습니다.

**실제 1592 예상 지연**: 저장소에는 완성된 1592 국가·인물·지도·사건 데이터가 없습니다. 현재 값은 4국가·20 NPC 가상 군사 캠페인 규모와 같은 부하에 대한 관측 범위입니다. 명령량·활성 전쟁·history 크기가 비슷한 경우에만 orders/advance 시간을 참고할 수 있습니다. 실제 1592의 월 turn latency, 더 큰 병력·지도·NPC 규모, LLM·UI·네트워크 지연으로 외삽할 근거는 아직 없습니다. 별도 실제 scenario를 동일 seed·명령으로 계측해야 합니다.

## 재현 자료

프로젝트 바깥 `run_latency_campaign.py`가 최종 source hash를 시작·매월·종료 시 확인하고 3/12/36 save/load와 RSS를 기록합니다. `profile_wall_categories.py`는 서로 중복되지 않는 wall 범주를 측정합니다. `compare_performance_evidence.py`는 논리 결과와 SQLite archive 원본을 비교합니다. `measure_campaign_turn.py`는 보존 DB의 복사본에서 한 turn을 측정합니다. 중단하거나 교체한 Stage1/Stage2 및 15개월 before 실행은 원시 자료를 보존했으며 최종 완주 수치에 섞지 않았습니다.

최종 완료 증거 디렉터리: `../measurements/completed-industrial-runtime/`, `completed-industrial-verification36/`, `completed-preindustrial-runtime/`, `completed-sea-runtime/`. 이전 캡처의 provenance: `../measurements/measurement-inventory/archived-before-summary.json`.
