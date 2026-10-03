# CURRENT_STATE

## 정본과 안정 기준점

- Repository: `C:\Users\c\Documents\Codex\WorldlineEngine\worldline-engine`
- Stable commit: `f492d755ff30abe79943c3f73d134263adf92f8c`
- Commit: `Performance: Runtime Query Performance v1 stable`
- Stable tag: `runtime-query-performance-v1-stable` (위 commit을 가리키는 annotated tag)
- 안정 기준점 검증: **1,350 passed**.
- 구현 시작 HEAD: `c670c644fb9dcb8c8d9207c7d6ee86ecac892f88` — `Design: Freeze Diplomacy & Intelligence v1 scope`.
- **Git 저장소가 구현의 source of truth다.** 문서·대화는 범위와 handoff를 설명하며 구현 상태는 정본의 실제 코드와 commit으로 확인한다.

## 개발 gate

완료: MVP → Economy 보정 / Economy v3 안정화 → NPC Decision Engine v1 → Crisis & Event Engine v1 → Performance v1 → Military & Logistics v1 → Military v1 이후 성능 개선 → Runtime Query Performance v1.

근거: `README.md`, `ECONOMIC_CALIBRATION_REPORT.md`, `ECONOMIC_STABILIZATION_REPORT.md`, `NPC_SIMULATION_REPORT.md`, `CRISIS_REQUIREMENTS_CHECK.md`, `PERFORMANCE_STATE_OPTIMIZATION_REPORT.md`, `MILITARY_LOGISTICS_REPORT.md`, `MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md`, `RUNTIME_QUERY_PERFORMANCE_REPORT.md`.

- 현재 gate: **Diplomacy & Intelligence v1**
- 현재 phase: **Stage 1 — Persistence & Activation Foundation 구현·검증 완료 / 미커밋·검토 대기**
- 확정 계약: `DIPLOMACY_INTELLIGENCE_AUDIT_DESIGN.md`, `FIRST_PLAY_SLICE.md`
- Stage 1: full-stack의 명시적 opt-in World 6/save 7, 빈 domain state, system suffix, transaction/Ledger/archive 기반. contact lifecycle·NPC 외교 판단·관측 ingestion/delivery는 아직 구현하지 않았다.
- Stage 1 검증: 신규·인접 **315 passed**, 신규 **107개** 포함 전체 **1,457 passed**. 기존 테스트 수정·skip 없이 통과했다. 결과는 `work/diplomacy_stage1/targeted-final.txt`와 `work/diplomacy_stage1/full-resumed.txt`에 있다.

## 제품 방향과 다음 순서

Worldline이 실제 계산을 담당하고 LLM이 주 플레이 인터페이스가 되는 **대체역사 국가운영 게임**이다. Engine이 권한·세계상태·시간·실제 결과를 결정하고, LLM은 허용된 관측을 읽고 구조화된 요청과 자연어 표현을 연결한다.

다음 순서: **D&I 최소 구현 → LLM thin slice → 첫 실제 플레이 → play-driven fixes → 장기 캠페인 준비**.

이번 diff 검토 후 commit 여부를 결정하며, 다음 구현은 Stage 2의 contact/정보 최소 동작이다. LLM adapter는 후속 단계이며, 첫 캠페인 시대와 LLM vendor·transport는 정하지 않았다. 사용자 요청으로 첫 pytest 시도를 중단했고, 재개한 전체 실행이 완료됐다. pytest에 포함된 기존 장기 회귀를 유지했으며 별도 benchmark/replay는 실행하지 않았다.
