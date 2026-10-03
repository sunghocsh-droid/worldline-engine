# Runtime Query Performance v1

기준점은 `0779cebcc4aa8e17f30c4ee0bb3cf5fa134157b9`이다. 게임 규칙, seeded RNG, system 순서, audit의 의미와 save schema를 유지하는 내부 조회 최적화다.

## 조회 경계

- actor/country/entity ID 조회는 기존 dictionary lookup을 유지한다.
- `economy.owned_regions()`는 경제에 등록된 지역만 반환하고 지역 ID 정렬을 유지한다. 두 지역 mapping의 mutation journal을 이용한다.
- NPC 관계 평가는 같은 actor의 평가 구간에서만 delivered observations와 effective memories를 공유한다. 개인별 평가와 중복 ruler 평가, reason trace, 합산 순서를 유지한다.
- `runtime_queries`의 selection/group/latest index는 tracked collection의 private `_active_indexes`에 저장한다. 군사 조회의 기존 API는 `military_active`를 통해 그대로 사용할 수 있다.
- Crisis pending effect/message와 ongoing event를 후보로 조회한 뒤 기존 날짜·scope·정보 수용 판정을 실행한다. historical scope count에는 종료된 사건도 포함한다.
- Engine metadata getter는 필요한 committed field만 JSON encode/decode한다. `true_state`는 여전히 전체 독립 World 사본이다.
- archive history는 LEFT JOIN 한 번, export는 blob와 catalog 조회 두 번을 사용한다. codec·길이·checksum 검사와 행별 독립 decode를 유지한다. 전체 ledger 조회와 semantic audit은 그대로다.

## 캐시 무효화

1. tracked record의 중첩 변경·삽입·삭제·교체는 `changes_since()`에 따라 해당 key를 재검사한다.
2. collection 교체, rollback, bounded change log의 gap은 전체 재구축한다. 로그 한도는 기존 4,096개다.
3. 신규 plain record 및 tracked record 내부의 신규 plain dict/list/tuple은 barrier 전에 다시 검사한다. scalar만 바뀐 tracked record는 일반 incremental 경로를 사용한다.
4. index 구축이 실패하면 token을 버려 다음 조회가 부분 index를 재사용하지 않게 한다.
5. 추적되지 않는 mapping은 fresh scan을 수행한다. 날짜에 따라 달라지는 due/cooldown 판정은 조회 시 계산한다.
6. NPC context는 한 actor의 관계 평가 종료 시 폐기한다. report/memory를 변경할 수 있는 이후 단계로 전달하지 않는다.
7. public Engine 조회는 current-state text, SQLite data_version, total_changes, external_dirty 검사로 기존 committed-read 경계를 유지한다. stale 상태는 기존 전체 DB read/validation 경로를 사용한다.

effect 정렬 key는 최초 구축과 변경된 기록 refresh에서 완료된 기록도 검사한다. 숫자 suffix가 없는 imported ID 등 기존 전체 정렬이 거부하던 기록이 있으면 원래 insertion 순서의 전체 sort 경로로 돌아가 같은 오류를 발생시킨다. 정상적인 warm 조회는 cold 기록을 다시 읽지 않는다. order 함수는 순수한 canonical hashable·comparable key를 반환하는 내부 계약이다.

인덱스는 World dataclass field나 논리 save에 포함되지 않는다. deepcopy·shallow projection·save/load는 기존 public 독립성 규칙을 따른다. 내부 후보 반환은 borrowed record이므로 public API에서 그대로 노출하면 안 된다.

## 재현 명령

정본 저장소에서 PowerShell로 실행한다.

```powershell
.\.venv\Scripts\python.exe -B -m worldline.runtime_query_benchmark --baseline
.\.venv\Scripts\python.exe -B -m worldline.runtime_query_benchmark
$taskQueryComparisonScript = @'
import json
from pathlib import Path
from worldline.runtime_query_benchmark import compare_queries
directory = Path('benchmarks/runtime_query')
before = json.loads((directory / 'query_baseline.json').read_text(encoding='utf-8'))
after = json.loads((directory / 'query_optimized.json').read_text(encoding='utf-8'))
result = compare_queries(before, after)
(directory / 'query_comparison.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
assert result['equal'], result
'@
.\.venv\Scripts\python.exe -B -c $taskQueryComparisonScript
$taskBenchmarkPath = 'work/runtime_query/campaign_runs_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
.\.venv\Scripts\python.exe -B -m worldline.runtime_query_benchmark --campaigns --directory $taskBenchmarkPath
.\.venv\Scripts\python.exe -B -m pytest -q --basetemp work\runtime_query\pytest-full --junitxml benchmarks\runtime_query\pytest_full.xml
```

benchmark는 Git의 정확한 기준점 소스를 `work/runtime_query`에 분리 로딩하며 이전 crisis-v1 fixture를 이번 PRE로 사용하지 않는다. Windows checkout의 CRLF와 Git blob의 LF는 줄바꿈만 정규화하여 검증한다. query는 5회 표본 중앙값이고 SQL tracing·fingerprinting은 timed 구간에서 제외한다. campaign은 세 군사 시나리오의 동일 초기조건·명령·seed·runtime audit을 PRE/POST 각 3회 순차 실행한다. 각 실행에 midpoint save를 포함하고 추가 실행에서 실제 save/load/continue를 검증한다.

측정 파일은 `benchmarks/runtime_query`, scratch와 재생성 가능한 baseline 소스·DB는 `work/runtime_query`에 둔다. 기존 benchmark fixture가 전체 pytest 중 새 자료를 생성할 수 있으므로 이번 검증에서 기존 tracked 증거의 원본을 먼저 보존한다. 기존 테스트는 수정하지 않는다.

장기 군사 회귀는 원래 `run_campaign(..., months=36)`의 seed 1731·verification 설정과 매일 전체 Auditor를 사용한다. 병렬로 준비한 결과도 36개월 실행·최종 export·소스 해시 검증이 모두 끝난 뒤에만 원래 fixture의 complete-result cache로 제공한다. 부분 실행 자료나 다른 소스의 결과를 현재 소스 결과로 바꾸지 않는다. 병렬 장기 실행 시간은 controlled PRE/POST 성능 비교에 포함하지 않는다.

## 확장 시 주의할 비용

group/latest index는 정확한 삭제·교체·rollback 처리를 위해 기록 수에 비례하는 추가 key/descriptor memory를 사용한다. 최초 조회와 load 후 조회는 전체 index를 구축한다. future queue의 활성 후보 scan/sort, public World 사본, 전체 save/load/export와 의미 검사는 여전히 자료 크기의 영향을 받는다.

Diplomacy & Intelligence는 country-pair 관계 목록, actor별 수신 정보와 기억, 사건 scope를 명시적인 조회 경계로 사용해야 한다. 같은 국가 쌍의 모든 관계와 정보의 날짜·동률·원래 순서를 보존한다. actor 관측을 물리적 truth로 대체하는 캐시, 날짜만을 key로 쓰는 금융 quote·route 캐시는 사용하지 않는다. relation/formation/project/receipt index와 큰 세계의 memory·첫 조회 benchmark는 후속 성능 작업이다.
