# CURRENT_STATE

## 정본과 안정 기준점

- Repository: `C:\Users\c\Documents\Codex\WorldlineEngine\worldline-engine`
- Stable commit: `f492d755ff30abe79943c3f73d134263adf92f8c`
- Commit: `Performance: Runtime Query Performance v1 stable`
- Stable tag: `runtime-query-performance-v1-stable` (위 commit을 가리키는 annotated tag)
- 안정 기준점 검증: **1,350 passed**.
- Stage 1 구현 시작 HEAD: `c670c644fb9dcb8c8d9207c7d6ee86ecac892f88` — `Design: Freeze Diplomacy & Intelligence v1 scope`.
- Stage 2 구현 시작 HEAD: `e00ecb22b30f7e9d8c84c8ac63f1d076b2ea6542`.
- **Git 저장소가 구현의 source of truth다.** 문서·대화는 범위와 handoff를 설명하며 구현 상태는 정본의 실제 코드와 commit으로 확인한다.

## 개발 gate

완료: MVP → Economy 보정 / Economy v3 안정화 → NPC Decision Engine v1 → Crisis & Event Engine v1 → Performance v1 → Military & Logistics v1 → Military v1 이후 성능 개선 → Runtime Query Performance v1.

근거: `README.md`, `ECONOMIC_CALIBRATION_REPORT.md`, `ECONOMIC_STABILIZATION_REPORT.md`, `NPC_SIMULATION_REPORT.md`, `CRISIS_REQUIREMENTS_CHECK.md`, `PERFORMANCE_STATE_OPTIMIZATION_REPORT.md`, `MILITARY_LOGISTICS_REPORT.md`, `MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md`, `RUNTIME_QUERY_PERFORMANCE_REPORT.md`.

- 현재 gate: **Diplomacy & Intelligence v1**
- 현재 phase: **Stage 2 — Intelligence Delivery Slice 최종 검증 완료**
- 확정 계약: `DIPLOMACY_INTELLIGENCE_AUDIT_DESIGN.md`, `FIRST_PLAY_SLICE.md`
- Stage 1: full-stack의 명시적 opt-in World 6/save 7, domain state, system suffix, transaction/Ledger/archive 기반.
- Stage 1 검증: 신규·인접 **315 passed**, 신규 **107개** 포함 전체 **1,457 passed**. 기존 테스트 수정·skip 없이 통과했다. 결과는 `work/diplomacy_stage1/targeted-final.txt`와 `work/diplomacy_stage1/full-resumed.txt`에 있다.
- Stage 2: trusted observation ingestion → frozen snapshot → 명시적 person/player dispatch → simulation-day delay → delivery/application receipt → 기존 NPC reports/memory 또는 player observed → recipient-scoped committed query → save/load/rollback을 구현했다. stale/correction·같은 ID의 intent 충돌·exactly-once perception을 검증하며 ingestion/delivery/query는 RNG를 사용하지 않는다. Crisis/Military 자동 재발행, contact lifecycle, NPC 외교 협상은 구현하지 않았다.
- Stage 2 targeted 검증: 최종 신규 **136개**를 포함한 **667 passed**, 앞선 신규·인접 실행 **417 passed** 및 **343 passed**. 결과는 `work/diplomacy_stage2/boundary-targeted.txt`, `targeted-01.txt`, `targeted-final.txt`에 있다. 기존 테스트를 수정·완화·skip하지 않았다.
- Stage 2 최종 전체 pytest: 큰 JSON 정수의 기존 NPC 신호 해석 overflow 보정까지 포함한 최종 소스에서 **1,593 passed** (3:06:39), failure/error/skip 0. 최종 closeout에서 소스 변경 없이 전체 suite를 한 번 실행했다. 결과는 `work/diplomacy_stage2/closeout-full.txt`와 `closeout-full.xml`에 있다. 앞선 전체 검증은 보정 이전 **1,589 passed**였으며, 보정 이후 신규 4개 사례를 포함한 인접 **667개**도 통과했다.

## 제품 방향과 다음 순서

Worldline이 실제 계산을 담당하고 LLM이 주 플레이 인터페이스가 되는 **대체역사 국가운영 게임**이다. Engine이 권한·세계상태·시간·실제 결과를 결정하고, LLM은 허용된 관측을 읽고 구조화된 요청과 자연어 표현을 연결한다.

다음 순서: **D&I 최소 구현 → LLM thin slice → 첫 실제 플레이 → play-driven fixes → 장기 캠페인 준비**.

Stage 2 최종 전체 검증 후 의도 파일만 `Intelligence: Add D&I v1 delivery slice`로 commit한다. 다음 구현은 이 commit 위에서 확정 범위의 Stage 3+4 contact 왕복과 NPC 판단 adapter를 연결한다. LLM adapter는 후속 단계이고 첫 캠페인 시대와 LLM vendor·transport는 정하지 않았다. 별도 20년/30년 benchmark·장기 replay와 새 최종 D&I report는 만들지 않았다.
