# Worldline Engine 저장소 정리 감사 결과

## 보존과 원인

미커밋 D&I Stage 3+4 코드·신규 테스트를 포함한 원본 저장소 전체를 별도 복사했다. **13,431개 파일, 16,571,899,045바이트**의 SHA256이 모두 일치하고 백업 Git 무결성 검사도 통과했다. 원본 Git 디렉터리는 이름만 바꾸어 별도 보존해 당시 속성과 이력도 남겼다.

- 전체 백업: `C:\Users\c\Documents\Codex\WorldlineEngine-safe-backups\worldline-engine-before-cleanup-2026-10-04`
- 원본 Git: `C:\Users\c\Documents\Codex\WorldlineEngine-safe-backups\retired-original-git-2026-10-04`
- 기존 생성물: `C:\Users\c\Documents\Codex\WorldlineEngine-local-artifacts\cleanup-2026-10-04`
- 새 검증 생성물: `C:\Users\c\Documents\Codex\WorldlineEngine-local-artifacts\cleanup-2026-10-04\post-validation`

정리 전 loose object는 **582.06 MiB**, pack은 **492.95 MiB**였다. loose 476개가 이미 pack에 포함되어 중복 공간을 사용했다. 실제 `.git` 파일 합계는 **1,127,316,152바이트**였다. Git object의 압축 전 크기 약 99.67%, pack 저장 크기 약 99.78%는 `benchmarks/`에 해당했다. 현재 tracked benchmark 242개는 약 **1,995.51 MiB**, 로컬 `work/`는 **11,637,119,868바이트**였다. 문서 `CRISIS_DESIGN.md`는 원본 그대로 7,196바이트이며 Git 비대화 원인이 아니었다.

큰 blob의 대표 원인은 gzip 전체 시스템 사건 로그(95,730,824바이트), 최적화 resume 사건 로그(55,976,950바이트), NPC campaign JSON(301,735,214바이트), NPC checkpoint JSON(244,727,106바이트), 군사 midpoint JSON(196,093,702바이트)이었다. JSON의 로컬/Git byte 차이 일부는 CRLF/LF 변환과 delta 압축에서 생겼으며 이것만으로 작업 손상을 판단하지 않았다.

## 실행한 정리

실제 GitHub의 head/tag가 비어 있음을 조회하고, 정리 전 전체 백업에서 복제한 별도 저장소에서 이력을 정리했다. 대형 원시 benchmark **47개 경로**를 제외하고 기존 소스·설계문서·테스트·필수 참조 입력과 검증 증거를 보존했다. **다섯 원본 commit 각각에서 모든 보존 경로의 blob ID와 mode가 대응 commit과 같음**을 검증한 뒤 정본에 적용했다. 원본/새 commit 대응은 [GIT_HISTORY_REWRITE_MAP.json](GIT_HISTORY_REWRITE_MAP.json)에 있다. 정리 직후 Git은 loose 0개, pack 434개, **8.13 MiB**였다.

원본 소스를 그대로 보존한 과거 commit의 코드·보고서 안에는 당시의 옛 SHA도 그대로 남아 있다. 현재 정리 HEAD에서는 두 실행 selector를 대응 SHA로 갱신했다. **과거 tag를 단독 checkout해 당시의 Git 참조 도구·전체 테스트까지 재현하려면 원본 백업을 별도 복제해 사용한다.** 보존 중인 안전 백업 자체는 수정하지 않는다. 현재 Git의 과거 대응 commit은 동일한 기준 소스를 export하는 비교 oracle로 사용할 수 있으며, 최신 정리 HEAD는 실제 전체 suite로 다시 검증했다. 과거 fixture의 줄바꿈도 당시 원문을 유지하고, 최신 HEAD에 clone byte 보존 설정을 추가했다.

기존 benchmark **195개**와 새 정책·검증 증거를 보존했다. 현재 staged benchmark 파일은 **200개**다. `.gitignore`는 `work/`, cache, SQLite, 대형 benchmark 원시 결과를 제외하고 benchmark 증거를 개별 경로로 허용한다. 보존 기준은 [benchmarks/ARTIFACT_POLICY.md](benchmarks/ARTIFACT_POLICY.md)에 있다. 생성물은 삭제 대신 검증된 별도 보관 경로로 옮겼다. 새 검증에서 생성한 **1,775개 파일, 1,430,781,509바이트**도 이동 후 SHA256을 다시 확인했다. 전체 백업과 별도 생성물을 남겼으므로 이 작업은 전체 디스크 공간을 모두 회수하는 작업이 아니다.

새 `work/`는 같은 볼륨에서 폴더 전체를 이동해 pytest 임시 alias 링크 **384개**도 원래 target 문자열 그대로 보존했다. 링크를 따라가거나 외부 target을 수정·삭제하지 않았다. 이동 후 원래 절대 경로를 가리키는 `current` alias가 끊길 수 있으므로 실제 데이터는 보존된 숫자 suffix 폴더의 원본 파일을 사용한다.

## D&I와 필수 fixture 보존

원본 D&I 파일은 기존 최종 Stage 3+4 source manifest와 모두 일치했다. 소스와 테스트 Python 141개를 다시 비교했으며, 실제 소스 byte 변경은 Git에서 동일한 기준 소스를 export하기 위한 commit selector 두 곳의 literal 교체뿐이다. 미커밋 D&I 로직·신규 테스트·기존 assertion을 보존했고 테스트 삭제·완화·skip은 없다. 신규 237개 D&I 사례는 전체 suite에 포함한다. 권한·수신 격리·idempotency·save/load·rollback·변조 거부·determinism과 NPC contact 연결은 기존 테스트로 재검증했다.

기준 소스가 같은 대응 commit을 사용하도록 `worldline/runtime_query_benchmark.py`와 `tests/test_diplomacy_intelligence_legacy_equivalence.py`의 Git SHA selector만 갱신했다. 이로 인해 전체 source fingerprint가 바뀌므로 이전 cache의 source hash를 바꾸어 재사용하지 않고 실제 장기 실행을 새로 수행했다.

`tests/fixtures/crisis_v1_source/`의 27개 Python 파일은 원본 manifest가 검증하는 정확한 byte를 유지한다. 원본 혼합 줄바꿈이 새 clone에서 자동 변환되지 않도록 해당 immutable fixture에만 `.gitattributes`의 `-text`를 지정했다. 네 파일의 index 줄바꿈 저장 방식만 달라졌으며 실제 fixture 파일·Python 의미·manifest·assertion은 바꾸지 않았다. index와 별도 checkout에서도 27개 hash가 일치했다. 필수 `benchmarks/calibrated_final.json`은 **35,253,431바이트**, SHA256 `a0ce517588176a9c71016777fa0470f0e13890a69f2c2016f55e80ae59ae997c`로 보존했다.

README의 최신 D&I opt-in World 6/save 7과 FIRST_PLAY_SLICE의 구현 상태를 실제 코드에 맞췄다. 과거 gate의 테스트 기록은 과거 증거로 남기고 현재 재검증 결과와 구분했다. 현재 checkout에 없는 과거 외부 checkpoints/measurements 링크는 과거 측정 실패로 단정하지 않았다.

## Git 정리 삭제 실패

원본 `.git/objects/00`은 이미 비어 있었으며 `ReadOnly|Directory`(속성 17)였다. 현재 사용자 FullControl, Deny 없음, reparse 없음, 검사 당시 Git/gc 프로세스와 lock 없음을 확인했다. 별도 빈 디렉터리 실험에서 일반 속성 16은 삭제 성공, 읽기 전용 17은 Windows 오류 5로 실패, 읽기 전용 bit만 제거하면 성공했다. 정상 권한에서도 이 속성만으로 실패가 발생함을 실증했다. 정리 후 새 `.git` 디렉터리는 정상 속성 16으로 만들었다.

과거 순간의 파일 핸들을 재구성할 수는 없다. Defender는 켜져 있었으나 관련 차단 이벤트 증거는 없었으므로 AV/프로세스를 원인으로 단정하지 않았다. 원본 Git과 백업의 속성은 변경하지 않았다. 최종 commit 뒤 새 Git의 일반 gc와 무결성 검사를 실행하고 push 직전 결과를 별도로 보고한다.

## 최종 검증

- Python 3.12.14 / pytest 9.1.1.
- 최종 전체 suite: **1,830 passed**, failure/error/skip 0, 3722.179초. 실행 중/전후 production source hash 일치.
- Git 기준점 selector 인접 검증: **24 passed**, failure/error/skip 0.
- 실제 재실행: 군사 36개월 세 scenario, NPC 240개월 replay, Crisis 360개월 campaign/replay. **여섯 결과의 World·RNG·장부·기록·summary·trace가 정리 전 결과와 정확히 같다.** 소스 hash와 실행 시간 metadata만 새 실행 정보로 구분했다.
- 원본 history 대응 다섯 commit의 보존 path/mode/blob 일치, 필수 fixture SHA256 일치, staged diff whitespace 검사 통과.

전체 [JUnit XML](benchmarks/repository_cleanup/full.xml), [출력](benchmarks/repository_cleanup/full.txt), [실제 소스 hash](benchmarks/repository_cleanup/source-manifest.json), [검증·결정론 비교](benchmarks/repository_cleanup/verification.json)를 함께 보존한다. 전체 실행은 fixture 생성을 포함해 수 시간이 걸릴 수 있다. 원시 cache가 없는 새 clone도 기존 테스트가 실제 campaign/replay를 재생성한다.

## 현재 큰 tracked 파일

| 경로 | 로컬 파일 바이트 |
|---|---:|
| `benchmarks/calibrated_final.json` | 35,253,431 |
| `examples/npc_month_one.json` | 4,674,169 |
| `examples/crisis_pending.json` | 2,873,336 |
| `benchmarks/military/performance/enabled/result.json.gz` | 920,264 |
| `benchmarks/runtime_query/before/industrial_front/result.json.gz` | 762,712 |
| `benchmarks/runtime_query/before/expeditionary_sea/result.json.gz` | 663,453 |
| `benchmarks/runtime_query/before/preindustrial_border/result.json.gz` | 658,390 |
| `benchmarks/military/performance/disabled/result.json.gz` | 656,349 |
| `benchmarks/military/volley_training_followup.json` | 560,170 |
| `examples/stabilization_month_one.json` | 388,478 |
| `benchmarks/crisis/stress_cases.json` | 330,657 |
| `benchmarks/runtime_query/long/crisis/stress_cases.json` | 329,980 |

정리 결과와 보존한 D&I 작업을 함께 commit하며, **GitHub push는 실행하지 않는다.** 최신 commit·최종 clean status·gc/무결성 검사·실제 크기는 commit 후 push 직전 보고에서 확인한다. 별도 백업은 원본 이력과 미커밋 작업을 포함하므로 유지한다.
