# CURRENT_STATE

## 정본과 안정 기준점

- Repository: `C:\Users\c\Documents\Codex\WorldlineEngine\worldline-engine`
- Stable commit: `c3fa97517721fd6fe71552179c317cc0cfc28f4d` (정리 전 원본 `f492d755ff30abe79943c3f73d134263adf92f8c`)
- Commit: `Performance: Runtime Query Performance v1 stable`
- Stable tag: `runtime-query-performance-v1-stable` (위 commit을 가리키는 annotated tag)
- 안정 기준점 원본 검증: **1,350 passed**. 위 commit/tag는 원본 소스가 같은 이력 대응점이다. 최신 정리 HEAD의 전체 suite는 별도로 재검증했다.
- Stage 1 구현 시작 HEAD: `30438c0d7915ee953509a115b031b3307fe93eaa` — `Design: Freeze Diplomacy & Intelligence v1 scope`.
- Stage 2 구현 시작 HEAD: `585b3cad07a1e7bc8d2117089b6fda4763952da6`.
- Stage 2 완료 commit / Stage 3+4 시작 HEAD: `5a4e8a8220209cf53630cd0a128203c195b4abdf` — `Intelligence: Add D&I v1 delivery slice`. tag/push 없이 의도 파일만 commit했다.
- **Git 저장소가 구현의 source of truth다.** 문서·대화는 범위와 handoff를 설명하며 구현 상태는 정본의 실제 코드와 commit으로 확인한다.

## 저장소 정리와 검증 구분

정리 전 전체 저장소·Git 이력·미커밋 D&I 파일을 별도 로컬 백업으로 복사하고 파일 hash로 검증했다. 이후 generated benchmark 47개 경로를 Git 이력에서 제외했으며, 다섯 원본 commit 각각에서 남긴 모든 경로의 blob ID·파일 mode가 대응 commit과 같음을 확인했다. 원본→현재 commit 대응과 제외 경로는 [GIT_HISTORY_REWRITE_MAP.json](GIT_HISTORY_REWRITE_MAP.json), 생성물·필수 참조 입력 보존 규칙은 [benchmarks/ARTIFACT_POLICY.md](benchmarks/ARTIFACT_POLICY.md)에 있다.

아래 Stage별 passed 수치·소스 hash·`work/` 로그는 **정리 전 검증 기록**이다. Git에서 기준 소스를 export하는 두 selector를 대응 commit으로 갱신했으며, 기존 테스트 assertion과 simulation 로직은 유지했다. 정리 후 고정한 최종 소스에서 전체 suite를 실행해 **1,830 passed**, failure/error/skip 0을 확인했다. 기준 commit selector 인접 테스트도 **24 passed**다. 기존 campaign cache의 source hash를 바꾸어 재사용하지 않고 군사 세 scenario·NPC 20년 replay·Crisis 30년 campaign/replay를 실제 재생성해 정리 전 World·RNG·장부·기록과의 동일성을 확인했다. 새 실행 결과와 실제 소스 hash는 [benchmarks/repository_cleanup/verification.json](benchmarks/repository_cleanup/verification.json), [전체 JUnit XML](benchmarks/repository_cleanup/full.xml)에 있다.

## 개발 gate

완료: MVP → Economy 보정 / Economy v3 안정화 → NPC Decision Engine v1 → Crisis & Event Engine v1 → Performance v1 → Military & Logistics v1 → Military v1 이후 성능 개선 → Runtime Query Performance v1.

근거: `README.md`, `ECONOMIC_CALIBRATION_REPORT.md`, `ECONOMIC_STABILIZATION_REPORT.md`, `NPC_SIMULATION_REPORT.md`, `CRISIS_REQUIREMENTS_CHECK.md`, `PERFORMANCE_STATE_OPTIMIZATION_REPORT.md`, `MILITARY_LOGISTICS_REPORT.md`, `MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md`, `RUNTIME_QUERY_PERFORMANCE_REPORT.md`.

- 현재 gate: **Diplomacy & Intelligence v1**
- 현재 phase: **Stage 3+4 — Contact Round Trip + NPC Decision Adapter 구현·저장소 정리 후 최종 재검증 완료**
- 확정 계약: `DIPLOMACY_INTELLIGENCE_AUDIT_DESIGN.md`, `FIRST_PLAY_SLICE.md`
- Stage 1: full-stack의 명시적 opt-in World 6/save 7, domain state, system suffix, transaction/Ledger/archive 기반.
- Stage 1 검증: 신규·인접 **315 passed**, 신규 **107개** 포함 전체 **1,457 passed**. 기존 테스트 수정·skip 없이 통과했다. 결과는 `work/diplomacy_stage1/targeted-final.txt`와 `work/diplomacy_stage1/full-resumed.txt`에 있다.
- Stage 2: trusted observation ingestion → frozen snapshot → 명시적 person/player dispatch → simulation-day delay → delivery/application receipt → 기존 NPC reports/memory 또는 player observed → recipient-scoped committed query → save/load/rollback을 구현했다. stale/correction·같은 ID의 intent 충돌·exactly-once perception을 검증하며 ingestion/delivery/query는 RNG를 사용하지 않는다. Stage 2 범위에서 Crisis/Military 자동 재발행과 contact lifecycle·NPC 외교 협상은 제외했다.
- Stage 2 targeted 검증: 최종 신규 **136개**를 포함한 **667 passed**, 앞선 신규·인접 실행 **417 passed** 및 **343 passed**. 결과는 `work/diplomacy_stage2/boundary-targeted.txt`, `targeted-01.txt`, `targeted-final.txt`에 있다. 기존 테스트를 수정·완화·skip하지 않았다.
- Stage 2 최종 전체 pytest: 큰 JSON 정수의 기존 NPC 신호 해석 overflow 보정까지 포함한 최종 소스에서 **1,593 passed** (3:06:39), failure/error/skip 0. 최종 closeout에서 소스 변경 없이 전체 suite를 한 번 실행했다. 결과는 `work/diplomacy_stage2/closeout-full.txt`와 `closeout-full.xml`에 있다. 앞선 전체 검증은 보정 이전 **1,589 passed**였으며, 보정 이후 신규 4개 사례를 포함한 인접 **667개**도 통과했다.
- Stage 3+4: `propose_contact` → Diplomacy 소유 frozen proposal/dispatch → 실제 recipient person 수신 → 기존 deterministic NPC scorer·executive/cabinet 규칙 → 현재 권한을 재확인하는 `respond_contact` → explicit reply route의 지연 수신 → `diplomacy_view`를 연결했다. 기존 NPC reports/memory와 player observed를 재사용하고 수신 전 결과를 숨긴다. contact acceptance는 접촉의 durable 결과만 기록하며 기존 관계·전쟁·경제 효과를 실행하지 않는다. 새 RNG, 병렬 belief/memory/Ledger, World/save schema 변경은 없다.
- Stage 3+4 최종 targeted: 신규 **237개**를 포함한 **912 passed** (4:33), failure/error/skip 0. 저장 네 단계·rollback·재해시 변조 거부·수신 격리·기존 Stage 2 bytes/동작과 인접 경로를 검증했다. named `FIRST_PLAY_SLICE` Engine-only acceptance는 proposal/response 각각의 in-transit save/load와 실제 NPC 판단·player 수신까지 통과했다. 결과는 `work/diplomacy_stage34/final-targeted.txt`와 `final-targeted.xml`에 있다.
- Stage 3+4 최종 전체 pytest: targeted 완료 후 동결한 최종 소스에서 전체 suite를 한 번 실행해 **1,830 passed** (3:06:34), failure/error/skip 0. 실행 전후 production·신규 테스트 hash가 같고 HEAD는 Stage 2 commit 그대로다. 결과는 `work/diplomacy_stage34/final-full.txt`와 `final-full.xml`에 있다. pytest 생성물은 보존 후 작업 diff에서 정리했고 `git diff --check`를 통과했다. 이는 정리 전 미커밋 상태의 기록이다. 정리 후 재검증한 Stage 3+4 변경은 저장소 정리 결과와 함께 commit한다.

## 제품 방향과 다음 순서

Worldline이 실제 계산을 담당하고 LLM이 주 플레이 인터페이스가 되는 **대체역사 국가운영 게임**이다. Engine이 권한·세계상태·시간·실제 결과를 결정하고, LLM은 허용된 관측을 읽고 구조화된 요청과 자연어 표현을 연결한다.

다음 순서: **D&I 최소 구현 → LLM thin slice → 첫 실제 플레이 → play-driven fixes → 장기 캠페인 준비**.

저장소 정리 후 최종 전체 검증을 완료했다. diff·증거·의도 파일을 확인해 D&I 변경과 정리 결과를 함께 commit하며, GitHub push 직전에 최종 상태를 보고한다. LLM adapter는 후속 단계이고 첫 캠페인 시대와 LLM vendor·transport는 정하지 않았다. 기존 20년/30년 regression campaign은 테스트가 정확히 일치하는 소스 기록을 찾지 못하면 재생성하며, 새 D&I 기능의 장기 캠페인 요구사항을 추가한 것은 아니다.
