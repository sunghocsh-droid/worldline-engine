# Runtime Query Performance v1 결과

## A. 변경 요약

정본 `C:\Users\c\Documents\Codex\WorldlineEngine\worldline-engine`, 기준점 `0779cebcc4aa8e17f30c4ee0bb3cf5fa134157b9`에서 작업했다. 새 게임 기능을 추가하지 않았다. 내부 후보 조회·actor 구간의 공통 입력·committed metadata 복사·archive SQL을 개선했다. 시스템 일정, 판정, RNG draw, resource 결산, ledger/audit의 의미와 save schema는 유지했다.

## B. 발견한 병목

| 영역 | 기존 구조와 원인 | v1 처리 |
|---|---|---|
| World/entity | ID lookup은 이미 dictionary 기반. country/owner/region 조건 조회는 전체 collection 순회 | 소유 지역에 이중 dependency index |
| Economy | 소유 지역의 반복 정렬·scan. 11개 산업 합산과 노동·신용 등에서 재사용. 노동 origin의 `dict.get` 기본값도 army마다 평가 | owner index와 함수 내 지역 목록 재사용 |
| NPC | relationship target마다 같은 delivered report·memory context 구성, 전체 관측 이벤트 sort/filter | actor 평가 구간 context와 subject 분류 |
| Crisis/effects | 하루 4회 이상의 전체 historical effect 정렬. enqueue마다 idempotency 전체 scan | unapplied selection, idempotency group |
| Crisis/messages | delivered 이력을 매번 읽어 최신 수용 보고를 계산. 같은 날 반복 조회 | queued selection과 accepted latest group |
| Crisis/events | hazard마다 종료된 사건까지 조회. event 생성 시 historical scope count 재계산 | ongoing selection과 전체 historical scope group count |
| Military | active operation/shipment/command index는 이미 존재 | API·의미를 유지하며 공통 index 모듈로 이동 |
| SQLite | history/export가 catalog/digest 결과마다 archive object SQL을 별도 실행 | LEFT JOIN history 1회, export 2회 |
| JSON/public query | metadata 4 getter도 전체 World를 JSON 왕복 변환 | 필요한 committed field만 독립 JSON 복사 |
| Ledger/audit | 전체 ledger 반환과 semantic reverse-reference 검사는 이력 크기에 비례 | 전체 반환과 검사 범위를 유지 |

추가로 확인한 구조는 trade×relation, formation contact×formation, 군사 loss receipt×crisis effect, project 제약의 project×project, region×shock, event×local actor, actor별 credit quote 재계산이다. Diplomacy & Intelligence의 관계·통신 이력이 늘면 이 비용도 커진다. 금융 quote와 route에는 reservation·재고·관계·장비·통제 등의 입력이 있어 날짜만으로 캐시하지 않았다.

## C. 구현한 최적화

`runtime_queries`는 기존 군사 index 구현을 공유하며 selection, group count, first matching record, latest group, stable ordered selection을 제공한다. sorted-key 순서, effect ID의 숫자 순서, imported 동률·중복 group의 원래 insertion 순서를 유지한다. 사건 scope count에는 종료된 사건을 포함해 named RNG key를 바꾸지 않는다.

경제 index는 경제 지역 membership와 World 지역 owner 두 dependency를 추적한다. 누락된 World 지역의 기존 오류, raw mapping scan, 반환 list 독립성을 유지한다. NPC context는 한 actor의 관계 평가 안에서만 공유하며 ruler의 중복 평가도 유지한다. 관측과 기억의 합산 순서를 유지한다.

tracked 중첩 변경·삽입·삭제·교체는 해당 key를 refresh한다. rollback, collection 교체, 4,096개 bounded log의 gap은 재구축한다. 신규 plain record와 tracked record 안의 신규 plain 하위 container는 barrier 전에 재검사한다. 구축 실패 시 token을 버린다. 날짜 의존 판정은 조회 시 실행한다. 추적되지 않는 데이터는 fresh scan을 사용한다. 캐시는 private `_active_indexes`에만 있으며 save에 포함되지 않는다.

effect의 정렬 key는 완료된 기록도 최초·변경 시 검증한다. 숫자 suffix가 없는 imported ID는 Auditor가 허용해도 기존 전체 정렬에서 오류를 내므로, invalid key가 있으면 원래 전체 sort로 돌아가 동일 오류와 Engine rollback을 보존한다. 정상 warm 조회는 cold 기록을 재검사하지 않는다. 한 번의 refresh 중에만 collection 참조를 보유하고 즉시 제거한다.

Engine은 기존 SQLite 변경 감지와 transaction 중 committed view를 유지한다. stale/dirty 상태는 기존 전체 DB read와 physical validation 경로를 사용한다. archive query는 codec·raw length·SHA-256 검증과 행별 독립 JSON decode를 유지한다. LEFT JOIN으로 누락된 blob도 오류로 검출한다. history의 raw-byte memo는 한 호출 안에서만 존재한다.

## D. benchmark 전/후

Python 3.14.8, 같은 Windows host의 sequential 실행이다. query는 실제 생성한 유효한 상태에서 warm-up 후 5회 중앙값, SQL tracing과 fingerprint 계산은 timed 구간 밖이다. 아래 값은 호출 단위이며 결과·workload 크기가 모두 일치한다.

| query workload | PRE | POST | 속도비 / SQL |
|---|---:|---:|---|
| archive history, 500 objects | 5.541 ms | 5.001 ms | 1.11×, SQL 501→1 |
| archive export, 500 objects | 7.004 ms | 5.622 ms | 1.25×, SQL 502→2 |
| metadata 4 getter, canon 20,000 values | 36.893 ms | 1.115 ms | 33.09× |
| 소유 지역, 2,000 regions·20 countries 전체 조회 | 5.553 ms | 0.228 ms | 24.32× |
| 완료 effect 1,000건·due 조회 | 0.39672 ms | 0.002535 ms | 156.50× |
| 전달 완료 message 1,000건·due 조회 | 1.214865 ms | 0.003175 ms | 382.63× |

metadata group의 JSON encode bytes는 1,009,052→7,325다. 안전성 검사용 SQL 횟수는 유지했다. Crisis 값은 활성 후보가 없는 retained history 조회 비용이며 전체 tick 속도비가 아니다. 최초 index 구축·load·활성 future queue 비용은 별도로 남는다.

기존 실제 SQLite 군사 benchmark를 사용해 동일 seed 941·2개월·runtime audit·명령·midpoint save를 PRE/POST 각 3회 실행했다. 추가 resume은 반복 중앙값에서 제외했다.

| 시나리오 | PRE 중앙값 | POST 중앙값 | 속도비 |
|---|---:|---:|---:|
| preindustrial_border | 9.838 s | 9.968 s | 0.987× |
| industrial_front | 11.774 s | 11.784 s | 0.999× |
| expeditionary_sea | 11.276 s | 11.086 s | 1.017× |

작은 4개국·2개월 campaign의 전체 시간은 약 1.3% 느림~1.7% 빠름으로 차이가 작고 실행 변동의 영향을 받는다. 이번 측정으로 전체 simulation의 큰 속도 향상을 주장하지 않는다. archive export도 decode·decompression 비용이 남는다.

원시 자료: [query PRE](benchmarks/runtime_query/query_baseline.json), [query POST](benchmarks/runtime_query/query_optimized.json), [query 비교](benchmarks/runtime_query/query_comparison.json), [실제 campaign 비교](benchmarks/runtime_query/campaign_comparison.json), [구현 전 baseline](benchmarks/runtime_query/before/), [기준점 manifest](benchmarks/runtime_query/baseline_manifest.json).

## E. determinism 검증

정확한 Git 기준점 소스를 별도 package로 로딩했다. 세 군사 시나리오×두 seed(941, 1731)의 신규 회귀 검사에서 8일간 전체 committed Engine, RNG, ledger를 일별 비교하고 마지막 archive도 비교했다. 각 시나리오의 PRE 3회·POST 3회와 추가 실제 resume 1회의 benchmark에서도 전체 World/RNG/ledger, 월별 rows, 명령 actions가 일치했다. NPC tick의 전체 이벤트·World·RNG와 노동 결산도 기준점과 별도 비교했다. 캐시는 RNG state, system 순서, 계산값에 들어가지 않는다.

최종 소스에서 seed 941의 기존 Crisis benchmark harness의 360개월 primary와 변경하지 않은 기존 pytest fixture의 180개월 중간 저장·복원을 포함한 360개월 replay를 실제 실행했다. 기존 genuine 30년 결과의 43개 소스 manifest가 기준점과 정확히 같음을 검증한 뒤 새 결과와 비교했다. 최종 World/RNG, 모든 records, summary, trace, 초기 checksum과 seed가 모두 같았다. 실행 시간·소스 해시는 출처 metadata로 구분했다. [30년 전후 비교](benchmarks/runtime_query/crisis_30_year_comparison.json), [변경하지 않은 소스·주요 판정 함수](benchmarks/runtime_query/rule_preservation.json)에 증거를 기록했다.

NPC도 최종 소스에서 240개월 primary와 120개월 중간 저장·복원을 포함한 240개월 replay를 실제 실행했다. seed 731의 기존 20년 replay 소스 manifest가 `0779ceb`와 같음을 검증했고, 모든 events/records, 최종 World/RNG, summary와 초기 checksum이 새 replay와 정확히 일치했다. [20년 전후 비교](benchmarks/runtime_query/npc_20_year_comparison.json)에 기록했다.

세 군사 시나리오는 seed 1731·verification mode의 실제 SQLite Engine으로 각각 36개월을 실행했다. 모든 일별 full Auditor와 월별 소스 동결 검사를 수행했다. 완료된 결과만 동일 소스 pytest cache 경로에 옮겼고 기존 fixture의 검사는 유지했다. 군사 36개월의 기존 보관 결과는 소스가 기준점과 다르므로 `0779ceb` 전후 동등성 근거로 사용하지 않았다. 이전 최적화 trial과 최종 소스의 correction regression 증거는 구분해서 보존한다. 이 병렬 실행 시간은 성능 전후 수치에 포함하지 않는다.

## F. save compatibility 검증

세 시나리오·두 seed에서 PRE/POST save 파일 bytes가 같았다. 기준점이 만든 save를 새 코드로 load한 뒤 이전 코드·새 코드·resumed 코드를 계속 진행해 일별 상태·RNG·ledger·archive를 비교했다. 반복 campaign의 모든 midpoint save bytes가 같았고 추가 세 실행은 실제 `Engine.save → Engine.load → continue`를 수행했다. 기존 논리 schema와 저장 version 상수는 변경하지 않았다. legacy save 검사는 기존 전체 pytest에 포함되어 있다.

imported 적용 완료 effect ID에 대한 이전 코드의 오류도 호환 동작으로 보존한다. 실제 event/outbox를 포함한 기준점 save를 새 코드로 load해 advance했을 때 같은 ValueError와 World/실제 RNG/ledger/SQL/journal rollback을 확인했다. 실패 후 save bytes도 기준점 파일과 같다.

기존 장기 checkpoint 검사는 Crisis 180개월, NPC 120개월의 실제 저장·복원 뒤 남은 기간을 계속 실행했고 uninterrupted 실행과 전체 결과가 같았다. 장기 연구 harness checkpoint와 실제 SQLite `Engine.save/load` 검증을 구분했다.

## G. 테스트 결과

최종 전체 pytest는 **1,350 passed in 620.15s**다. 기존 1,253개와 신규 97개를 모두 실행했고 failure/error/skip은 0개다. 수집 nodeid와 실제 JUnit testcase가 정확히 일치한다. 기존 검사 Python 파일 74개의 SHA-256은 모두 기준점과 같고 최종 생산 소스 45개와 신규 테스트 8개도 동결한 SHA-256 그대로다. 기존 테스트 삭제·수정·약화는 없다.

신규 검사에는 structural scan/SQL counts, nested/raw 변경, rollback, 로그 초과, 실패한 index retry, imported 순서·실패 호환성, private cache의 저장 비노출, committed isolation, 외부 SQLite writer, 실제 기준점 결과와 save bytes 비교가 포함된다. 전체 실행 전 최종 소스의 genuine 장기 캠페인을 준비했고, 기존 Crisis 원래 장기 pytest는 1 passed/1,221.85초, NPC 원래 장기 pytest는 1 passed/359.66초였다. 전체 suite는 이 실제 결과의 source-matching cache를 원래 fixture 규칙에 따라 사용하면서 기존 비교·복원·invariant 검사를 모두 수행했다.

새 장기 증거를 별도 보존하고 기존 tracked benchmark 177개를 최초 바이트로 복원해 SHA-256을 검증했다. [전체 pytest 로그](benchmarks/runtime_query/pytest_full.txt), [JUnit 결과](benchmarks/runtime_query/pytest_full.xml), [파일·검사 보존 검증](benchmarks/runtime_query/final_verification.json)에 기록했다. 최종 package digest는 `b40b2034173299934a0683f52cab624180af2409adb0d9b3dc58d7cf9b0eace9`다.

## H. 변경 파일

- 생산 코드: `worldline/runtime_queries.py`, `worldline/military_active.py`, `worldline/economy.py`, `worldline/npc.py`, `worldline/npc_state.py`, `worldline/crisis_effects.py`, `worldline/crisis_response.py`, `worldline/crisis_state.py`, `worldline/crisis_risk.py`, `worldline/engine.py`, `worldline/state_storage.py`.
- benchmark: `worldline/runtime_query_benchmark.py`, `benchmarks/runtime_query/`, 실제 36개월 실행으로 만든 `benchmarks/military/*-pytest-b40b20341732/`.
- 신규 테스트: `tests/test_runtime_query_benchmark.py`, `tests/test_runtime_query_storage.py`, `tests/test_runtime_query_engine.py`, `tests/test_runtime_query_equivalence.py`, `tests/test_runtime_query_economy_npc.py`, `tests/test_runtime_query_crisis.py`, `tests/test_runtime_query_order_validation.py`, `tests/test_runtime_query_import_compatibility.py`.
- 문서·scratch 관리: 이 보고서, `RUNTIME_QUERY_PERFORMANCE_GUIDE.md`, `.gitignore`의 `work/`.

## I. 남은 성능 위험

group/latest index는 exact deletion·교체·rollback을 위해 O(history) key/descriptor 메모리를 사용한다. load 뒤 최초 구축도 O(history)다. 변경된 hot record에 새 plain container가 있으면 barrier 전 hot body 재검사 비용이 있다. 활성 future effect/message 후보는 여전히 scan/sort한다.

closed-event cooldown/aftermath, 군사 semantic receipt 역참조, relation pair/contact/region actor/project 제약, 금융 projection, full World copy/save/load/ledger/export는 추가 병목이다. `state_storage.read`의 compact logical restore는 digest별 lookup을 유지한다. 큰 history 조회는 전체 detached 반환 객체와 호출 내 raw bytes를 함께 보유할 수 있다. 이 v1의 작은 campaign 수치는 대규모 세계나 수십 년 군사 캠페인의 throughput 보장이 아니다.

## J. Diplomacy & Intelligence 연결 상태

**기존 규모에서 Diplomacy & Intelligence 연결 작업을 시작할 수 있는 v1 기반은 준비됐다.** 명시적인 tracked index와 actor 구간 memoization, committed query 경계, 기준점 비교 benchmark를 확보했고 전체 회귀도 통과했다. 대규모 세계의 처리량 보장은 아직 하지 않는다. 큰 세계를 위한 후속 우선순위는 country-pair 전체 관계 index, actor별 delivered intelligence/history 조회, formation 위치·receipt 역참조 index와 국가·actor·history 규모별 cold/warm memory benchmark다. 새 대형 기능 개발은 이번 작업에서 시작하지 않았다.
