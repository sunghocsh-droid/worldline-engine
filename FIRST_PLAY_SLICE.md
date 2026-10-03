# 첫 LLM 대화형 플레이: 최소 acceptance contract

상태: **scope frozen / implementation not yet started**. 2026-10-03에 확정한 Diplomacy & Intelligence v1의 첫 플레이 계약이다. 정본 기준은 `f492d755ff30abe79943c3f73d134263adf92f8c`, tag `runtime-query-performance-v1-stable`, 기존 검증 기록은 **1,350 passed**다. 이 문서는 실행 결과가 아니며 LLM adapter·생산 코드·테스트 구현을 포함하지 않는다. 상세 소유권·저장 계약은 `DIPLOMACY_INTELLIGENCE_AUDIT_DESIGN.md`를 따른다.

Worldline이 실제 계산을 담당하고 LLM이 주 플레이 인터페이스가 되는 대체역사 국가운영 게임의 첫 목표는 **평시 외국 정부와의 접촉 요청 한 건을 실제 지연·판단·응답·기억·저장을 거쳐 완주하는 것**이다. LLM vendor, API, MCP, Apps SDK, UI, 요금제와 첫 캠페인의 시대는 선택하지 않는다.

## 1. 플레이어의 최소 상호작용

플레이어는 자신의 허용된 관측과 접촉 이력을 질문하고, 외국 국가의 정부·권한 있는 대표에게 접촉을 지시하고, simulation 시간을 진행시켜 전달을 기다리고, 실제 받은 응답을 다시 질문할 수 있어야 한다. 미수신 상태에는 기다림을 설명하고 수락·거절을 추측해 확정하지 않는다. 임의의 조약 조건이나 새 행동은 접촉 요청으로 조용히 치환하지 않고 지원 범위 밖임을 알려 준다.

최소 왕복은 다음 순서를 모두 거친다.

1. 인증된 player가 허용된 actor/player 관측을 조회하고 자연어로 평시 접촉을 지시한다.
2. adapter가 국가 party, 발신 대표자, 수신 대표자, bounded 접촉 내용과 request/proposal ID를 가진 구조화된 요청을 만든다. 질문은 허용된 view 조회로 처리한다.
3. Engine이 player의 제어 범위, 국가·개인의 존재와 소속, active 상태, 정부 실행 권한과 외교 scope, 현재 proposal 상태를 검증한다.
4. Engine이 durable contact proposal과 발신 receipt를 기록하고 명시적 정수 simulation-day delay의 dispatch를 만든다. 첫 acceptance에서는 왕복의 각 방향 지연을 0보다 크게 설정한다. 상대의 inbox·belief·memory에는 아직 반영하지 않는다.
5. 예정일 이후 상대 person이 proposal을 실제 수신한다. 그때 recipient inbox와 기존 NPC belief/memory가 갱신된다.
6. 기존 NPC 판단 구조가 수신한 정보만으로 accept/reject를 선택한다. 정부 판단과 권한 있는 대표자의 실행을 구분하고 Engine이 응답 시점의 권한·수신·상태를 다시 검증한다.
7. response receipt와 회신 dispatch를 기록한다. 접촉 수락은 접촉 성립이며 국가 관계·전쟁·통행권·교역 조건을 자동 변경하지 않는다.
8. 회신이 실제 수신된 뒤에만 player가 수락·거절 결과를 읽고 LLM이 자연어로 표현한다. 상대가 응답한 시점과 player가 알게 된 시점은 다를 수 있다.
9. 이후 대화와 save/load 후에도 같은 proposal·receipt·수신 이력·NPC 기억으로 이어진다.

## 2. actor identity와 권한 연결

국가·집단·기관도 제품상 행위자가 될 수 있으나 첫 slice는 현재 식별 영역을 그대로 사용한다. 국가 party와 개인 대표자를 같은 ID로 취급하거나 범용 Actor hierarchy를 만들지 않는다.

| 역할 | 첫 slice의 식별·의미 |
|---|---|
| 외교의 실제 국가 party | `proposer_country_id` / `recipient_country_id`: 발신국·수신국 각각 기존 `Country.id`. 접촉의 국가 쌍이며 개인 관계 ID가 아님 |
| 의사결정 주체 | 해당 국가의 기존 government/ruler/cabinet 규칙과 NPC person 판단. 새 독립 government actor ID를 만들지 않음 |
| 실행 대표자 | `executing_person_id`: 기존 `npc_state.persons`의 person ID. 국가 소속·active·정부 역할·`diplomatic_engagement` scope를 Engine이 검증 |
| proposal sender/recipient | `sender_person_id` / `recipient_person_id`: 위 person ID를 명시적으로 지정. 모호한 `proposer_actor`/`recipient_actor` 필드는 채택하지 않으며 국가 party는 별도 필드 |
| 정보·회신 recipient | v1에서는 명시적 person reference 또는 player reference만 허용. person ID와 player ID namespace를 구분 |
| player의 제어 | trusted session binding이 player ID, 제어 국가·허용 person, 허용 action을 지정. player ID를 Country.id/person ID로 추정하지 않음 |

정부 상대를 자연어로 지정하면 trusted adapter가 현재의 권한 있는 수신 person으로 해석하고 Engine이 확인한다. 정부·그룹의 모든 구성원에게 자동 공유하지 않는다. player가 접촉 결과를 받을 대상이라면 player recipient dispatch를 명시한다. 같은 국가 소속 또는 대표자의 수신만으로 player view가 갱신되지 않는다. 일반 기관·그룹·군사 actor를 새 contact 당사자나 일반 정보 recipient로 확대하는 작업은 후속 범위다. 기존 group memory와 Military actor 의미는 그대로 유지한다.

첫 counterpart fixture는 기존 autonomous executive 정부의 ruler를 명시적 proposal recipient와 실행 대표자로 지정할 수 있다. 여러 cabinet member가 판단하는 경우에는 기존 정부 절차를 재사용하되 각 member가 실제 받은 허용 evidence만 평가한다. 대표자 한 명의 수신을 모든 cabinet member의 수신으로 간주하지 않는다.

## 3. transport-neutral LLM boundary

흐름은 **사용자 자연어 → 허용된 관측 조회 → 구조화된 질문/접촉/제안 → Engine 검증 → NPC/정부 판단 → 실제 결과·전달 지연 → 허용된 결과 조회 → LLM 자연어 표현**이다. adapter는 authenticated player/actor binding과 allowlist를 적용한다. LLM이 요청한 ID만 믿어 다른 actor의 view를 열거나 대신 실행하지 않는다.

| 경계 | 허용 범위 |
|---|---|
| 기존 read | `observed_state(player)`의 player 관측. `npc_view(person)`는 session 정책이 명시적으로 허용한 person의 관측·기억만 사용하며 외국 NPC view를 player에게 임의 제공하지 않음 |
| 새 read | recipient-scoped diplomacy/intelligence view의 직접 제출한 요청, 실제 수신한 proposal/response, 수신 report·허용 provenance. committed detached projection만 반환 |
| write 요청 | 검증된 contact 제출, 자기 제어·권한 범위에서 실제 수신한 contact의 accept/reject 응답, simulation 시간 진행 요청만 allowlist에 등록. 외국 NPC의 응답은 Engine/NPC 내부 판단 경로 |
| 응답 표현 | Engine의 receipt·recipient view에 있는 결과와 simulation 날짜를 표현. 수신하지 않은 결과나 NPC의 비공개 이유를 생성해 실제 결과로 제시하지 않음 |

`propose_contact`, `respond_contact`, `diplomacy_view`, `intelligence_view` 같은 새 이름은 **후속 구현의 계약 명칭**이며 현재 동작하는 API라는 뜻이 아니다. 질문은 truth를 변경하지 않는다. 자연어·LLM 출력은 검증할 요청이며 실행 권한이나 사실 증명이 아니다.

LLM에 `Engine.true_state`, Engine 객체, 전체 Ledger, DB path/connection, `historical_state`, hidden archive, 미수신 dispatch, 전체 observation table, audit-only accuracy, 상대국의 hidden truth를 전달하지 않는다. `npc_report`, `npc_outcome`, observation ingestion, 직접 DB 주입은 trusted scenario/domain hook이며 LLM write 경계에 넣지 않는다. actor-facing 오류도 상대의 미관측 실제 상태를 드러내지 않는다.

## 4. 필요한 D&I 기능과 기존 기능의 재사용

첫 플레이에 필요한 D&I는 durable 평시 contact lifecycle의 제출 → 제안 수신 → accept/reject → 회신 수신, person/player별 proposal·response inbox와 관측 상태, Crisis/Military 외부의 일반 observation snapshot·명시적 recipient dispatch, 그리고 이를 저장·transaction·Ledger·audit·query에 연결하는 opt-in 계약이다. proposal ID, receipt ID, 관측 identity, message ID를 구분하고 같은 요청의 재전송으로 실행·수신·기억이 중복되지 않아야 한다.

| 기존 domain | 재사용할 책임 |
|---|---|
| NPC | person·소속·권한, diplomat 판단과 이유 trace, 기존 정부 판단, reports/beliefs/memories와 명시적 기관 기억 공유. 별도 외교 성격·기억·투표 엔진을 만들지 않음 |
| Economy/Credit | 경제·교역·재정과 접근성의 실제 계산 및 기존 행정 관측. 접촉 수락으로 새 비용·금융 효과를 만들지 않음 |
| Crisis | 기존 위기·최후통첩·중재·동원·휴전과 effect/receipt. crisis-linked 행동은 원래 경로를 사용하고 평시 contact로 중복 실행하지 않음 |
| Military | 기존 command delay·operation 보고·전투 결과·publish와 군사 actor. 기존 report를 연결할 경우 원래 observation/delivery identity와 수신 사실을 보존 |
| Engine/Runtime | 기존 simulation clock, transaction/Journal, 단일 Ledger, archive, committed public view와 필요한 recipient query |

최초 ingestion/delivery와 지연은 deterministic하고 난수를 사용하지 않는다. 국제 지연은 trusted 설정의 명시적 정수 simulation days로 정하고 기존 국내 leg가 있는 경우에만 기존 helper를 재사용한다. NPC의 새 외교 선택에 실제 noise가 필요한 경우에만 독립 named Diplomacy stream을 사용한다. 기존 global/NPC RNG 순서를 바꾸거나 legacy 정기 council을 같은 tick에 다시 실행하지 않는다.

첫 플레이 전에 일반 treaty DSL, 다자 협상, 동맹 자동 이행, guarantee, 군사통행권 협상, peace settlement, 첩보망, spy/agent, 공작, 방첩, 기만, 국제 통신망, 전역 intelligence score, 자동 국가별 전수 수집, 새 사회·이념·금융 엔진은 필요하지 않다. counteroffer, deadline, withdraw/expiry, 통신 실패·재시도 시뮬레이션, 기존 Crisis/Military queue의 이전·자동 relay/재발행도 v1 필수에서 보류한다. 완성된 MCP/API/UI, 특정 provider의 대화 session, 20년/30년 캠페인과 첫 시대 결정도 이 contract의 전제조건이 아니다.

## 5. save/load와 대화 지속

D&I는 명시적 activation으로 **World schema 6 / save schema 7**을 사용한다. 최초 지원 profile은 기존 Economy v3/NPC v1/Crisis v1/Military v1이 활성화된 full-stack World 5다. legacy save 1~6을 D&I schema로 자동 변환하거나 D&I를 자동 활성화하지 않는다. feature off의 기존 save bytes·World·RNG·Ledger 의미를 보존하고 새 authoritative state를 기존 임의 dict에 숨기지 않는다. 기존 system prefix `projects → economy → npc → crisis → military` 순서를 유지한다.

save는 proposal lifecycle, frozen payload, outbox, 예정일·실제 수신일, receipt와 ID sequence, NPC belief/memory, player 관측, player bindings와 명시적 reply recipient refs, 필요한 named stream state를 일관되게 보존해야 한다. 진행 중인 왕복 어느 단계에서 저장해도 load 후 중복 발신·수신·응답 없이 계속된다. 실패 rollback은 state/outbox/receipt/NPC belief/memory/Ledger/archive 및 global/domain RNG를 함께 복원한다. 상세 activation·manifest·audit 계약은 감사 설계 문서에 고정한다.

자연어 transcript는 adapter의 대화 context로 별도 보관할 수 있다. 게임 truth와 지속의 기준은 Engine의 player/actor binding, proposal/receipt/message ID와 실제 수신 이력이다. 같은 player로 복귀하면 허용된 view를 다시 조회해 대화를 재구성할 수 있어야 한다. 외부 LLM session이나 요약문만 있어야 접촉 상태가 유지되는 저장 방식은 허용하지 않는다. transcript가 Engine 상태와 다르면 Engine 관측을 사용한다.

## 6. 성공·실패 acceptance criteria

아래는 후속 구현·첫 플레이의 검증 기준이며 이번 문서 작업에서 실행하지 않는다.

| 기준 | 성공 | 실패 |
|---|---|---|
| 최소 왕복 | 자연어 접촉 지시가 검증된 proposal로 생성되고 지연 수신, NPC accept/reject, 회신, player 실제 수신까지 이어짐 | offer만 emit되거나 즉시 상대 결과를 공개함; 자연어 서술만 있고 receipt가 없음 |
| 관측 격리 | 상대 inbox는 수신 후 열리고 발신 player의 결과는 회신 수신 후 바뀜. 미수신 actor/player의 view는 그대로 | actual accepted/rejected 상태·hidden truth·archive·전체 Ledger가 LLM에 노출됨 |
| 권한 | 잘못된 국가·person, inactive·무권한 대표자, 수신 전 응답, 다른 player 제어 요청을 Engine이 거부 | 높은 skill/trust, 같은 국가 소속 또는 LLM 문구로 실행 권한이 생김 |
| 판단·기억 | 기존 NPC가 받은 proposal로 판단하고 기존 belief/memory에 동일 identity로 기억. 다음 대화에서 수신 결과를 재조회 | 새 병렬 판단·기억 store, 미전달 기억, 중복 기억, transcript만의 기억 |
| 전달·determinism | 고정 설정·명령 순서에서 같은 날짜·payload·receipt·판단 결과. query/ingestion/delivery는 draw 없음 | wall clock·미저장 RNG·무관한 global/NPC draw로 결과가 달라짐 |
| 재요청·rollback | 같은 ID/intent는 기존 receipt를 반환하고 충돌은 거부. 실패 후 전체 관련 상태가 원상 복원 | 중복 효과·회신·Ledger 또는 남은 outbox/archive/NPC belief 발생 |
| save/load 지속 | in-transit proposal과 response를 각각 저장·재개해 uninterrupted 진행과 같은 상태에 도달; 받은 결과·기억이 이후 대화에 남음 | load가 전달을 생략·반복하거나 외부 LLM session 없이는 세계상태를 복구하지 못함 |
| legacy 보존·소유권 | feature off 의미를 보존하고 contact 수락이 기존 Economy/Crisis/Military 효과를 추가 실행하지 않음 | legacy 자동 활성화·D&I schema 변환, 기존 prefix 변경, 관계·전쟁·통행권의 암묵적 변경 |

이 왕복과 저장 지속이 성공하면 **첫 실제 플레이 slice를 통과**한 것이다. 이후 순서는 play-driven fixes를 거쳐 장기 캠페인 준비로 진행하며, 이 slice에 필요하지 않은 기능을 D&I v1 필수 범위로 확대하지 않는다.
