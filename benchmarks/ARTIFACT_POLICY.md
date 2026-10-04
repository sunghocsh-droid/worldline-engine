# Benchmark 자료 보존·생성 정책

Git은 구현 코드·문서·테스트와 검토 가능한 증거를 보존한다. 실제 게임의 source of truth는 기존 Python + SQLite Engine이며, benchmark JSON·CSV·profile은 특정 소스와 실행 조건의 검증 자료다.

## Git에 남긴 자료

이번 정리 시점에 기존 benchmark 파일 **195개**를 명시적 허용 목록으로 유지했다. 목록의 정본은 루트 `.gitignore`의 `!/benchmarks/...` 경로다. 이 정책 문서는 그 목록에 추가한 설명이며, 이후 파일 개수 자체를 고정 계약으로 사용하지 않는다.

| 자료 | 보존 이유 |
|---|---|
| CSV·요약·비교·실행 metadata·source manifest | 과거 결과와 실행 조건·판정·소스 출처를 검토할 수 있게 보존 |
| pytest text·JUnit XML·invocation 기록, 명시적으로 허용한 profile·작은 압축 결과 | 실제 검증과 성능 측정의 증거 보존 |
| `benchmarks/reference_source/` | 기존 경제 비교의 원본 소스 참조 보존 |
| `benchmarks/calibrated_final.json` | 경제 안정화 회귀 테스트가 직접 읽는 필수 원본 참조 입력. 크기는 **35,253,431바이트**이며 단순 생성 cache로 취급하지 않음 |

`tests/fixtures/crisis_v1_source/`와 그 immutable source manifest도 기존 비교 테스트의 필수 oracle이다. benchmark 원시 생성물을 정리한다는 이유로 이 fixture나 테스트 assertion을 삭제·완화하지 않는다.

## 별도 보존하고 Git에서 제외한 자료

대형 campaign JSON, checkpoint, replay, 전체 system event log 등 **47개 benchmark 경로**는 정리 전 검증한 전체 로컬 백업에 보존한 뒤 Git 이력에서 제외했다. 정확한 경로와 원본→정리 후 commit 대응은 [GIT_HISTORY_REWRITE_MAP.json](../GIT_HISTORY_REWRITE_MAP.json)에 기록한다. 각 다섯 원본 commit에서 제외 경로 이외의 blob ID와 파일 mode는 대응 commit과 같다.

새 실행이 만드는 `work/`, SQLite 파일, 대형 benchmark 원시 결과와 source별 campaign cache는 로컬 생성물이다. `.gitignore`는 benchmark 전체를 제외한 뒤 개별 증거 경로만 허용하므로 새 파일을 자동으로 Git에 추가하지 않는다. 기존 허용 경로의 검증 자료가 실행 중 갱신되면 commit 전에 실제 diff와 출처를 확인한다. 새 증거를 Git에 보존할 때는 필요한 파일만 검토해 허용 목록을 명시적으로 늘린다.

과거 보고서의 원시 결과 링크나 외부 `../checkpoints/`·`../measurements/` 링크는 현재 checkout에 없는 로컬 보존 자료를 가리킬 수 있다. 링크가 없다는 사실과 당시 검증 실패를 동일시하지 않는다. Git에는 본문의 측정·판정과 남긴 증거를 보존하고, 원본 파일은 별도 백업에서 확인한다.

## 테스트 cache와 출처

NPC·Crisis·Military campaign fixture는 현재 `worldline/*.py` 전체의 파일 hash와 seed·실행 기간이 정확히 일치하는 실제 결과만 재사용한다. replay는 checkpoint와 replay 파일 hash도 검증한다. 기준 Git commit selector 같은 검증 도구 코드가 바뀌어도 전체 source fingerprint가 달라지므로 기존 결과를 새 실행으로 취급하지 않는다.

일치하는 cache가 없으면 기존 테스트가 실제 캠페인과 replay를 다시 실행한다. 군사 3년의 세 scenario, NPC 20년, Crisis 30년 검증 때문에 전체 suite는 수 시간이 걸릴 수 있고, 실행 중 생성된 파일은 위의 로컬 생성물 규칙을 따른다. 미완료 campaign directory가 새 실행을 막으면 먼저 그 증거를 보존하고 새 실행 경로를 준비한다.

**기존 cache의 `source_sha256`이나 역사적 commit ID를 새 값으로 바꾸어 재사용하지 않는다.** 과거 결과의 원본 ID·hash는 그대로 남기고, 새 실행에는 정리된 Git 기준점과 실제 소스 hash를 기록한다. 테스트 수를 줄이거나 assertion·고정 결정론 digest를 변경해 정리 결과를 통과시키지 않는다. 최종 통과는 고정한 소스에서 수행한 새 검증 결과로 확인한다.
