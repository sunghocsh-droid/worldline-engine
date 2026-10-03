# Diplomacy & Intelligence v1 감사와 최종 범위

범위 확정일은 2026-10-03, 정본 저장소는 `C:\Users\c\Documents\Codex\WorldlineEngine\worldline-engine`이다. 현재 phase는 **scope frozen / implementation not yet started**다. 이 문서는 기존 감사 결과를 출발점으로 최소 구현 계약을 확정한다. 생산 코드·테스트·LLM adapter는 아직 구현하지 않았다.

현재 안정 기준점은 **`f492d755ff30abe79943c3f73d134263adf92f8c`**, commit은 **`Performance: Runtime Query Performance v1 stable`**, tag는 **`runtime-query-performance-v1-stable`**다. HEAD와 annotated tag가 가리키는 commit의 일치를 확인했다. 기존 안정 검증은 **1,350 passed**이며 근거는 `RUNTIME_QUERY_PERFORMANCE_REPORT.md`와 `benchmarks/runtime_query/pytest_full.txt`다. 이번 문서 확정에서 pytest나 benchmark를 다시 실행하지 않았다.

기존 감사가 사용한 Runtime Query Performance v1 소스는 이제 위 안정 commit으로 고정되었다. 당시 코드 분석인 A~C와 Runtime query 분석 H는 유지하며, 당시의 미커밋 작업 트리를 현재 기준점으로 취급하지 않는다. 과거 감사에서 수행한 24 passed와 SHA manifest는 `work/diplomacy_intelligence_audit/`에 있는 과거 감사 증거일 뿐 이번 작업의 실행 결과가 아니다. code ownership 분석을 다시 수행하지 않았으며, actor 계약 확정에 필요한 기존 권한·ID 정의만 좁게 확인했다. 아래의 새 state·API 명칭은 **후속 구현을 위한 확정 계약**이며 현재 제공되는 API라는 뜻은 아니다.

## A. 현재 외교 기능 지도

분류는 사용자가 지정한 다섯 범주를 사용한다. 하나의 기능에 독립된 완성 부분과 미완성 부분이 있으면 행을 나누었다. 파일 경로와 줄 번호는 감사한 현재 작업 트리를 기준으로 한다.

| 기능 | 현재 동작과 소유자 | 분류 | 코드 근거 |
|---|---|---|---|
| 국가 간 관계 저장 | `World.relations`의 각 record가 `a`, `b`와 임의의 관계 필드를 보유한다. 독립 Diplomacy state는 없다 | 존재하지만 Diplomacy & Intelligence에서 확장 필요 | `worldline/models.py:89`, `worldline/audit.py:126` |
| legacy 국가·faction의 NPC 외교 자료 | `Country.npc`/`Faction.npc`의 claims/allies/rivals/red_lines/goals는 저장 가능한 설명 자료다. 이 자료를 조약·동맹 실행 상태로 쓰는 production consumer는 없다 | 존재하지만 Diplomacy & Intelligence에서 확장 필요 | `worldline/models.py:19`, `worldline/models.py:33`, production 필드 사용 검색 |
| 국가·지역·개인 식별 | countries/regions/armies/persons는 ID dictionary다. 국가, NPC 개인, 군사 actor는 서로 다른 식별 영역이다 | 이미 완성되어 재사용 | `worldline/models.py:89`, `worldline/npc_state.py:67`, `worldline/military_state.py:97` |
| 외교 접촉 intent | `diplomatic_engagement(target)`가 `last_engagement`를 기록하고 `diplomatic_offer`를 emit한다. 비용 0이며 상대의 수락·거절은 처리하지 않는다 | 존재하지만 Diplomacy & Intelligence에서 확장 필요 | `worldline/order_execution.py:87` |
| 외교 선택의 판단 | actor가 받은 `diplomacy` report의 target/threat/own_strength/enemy_strength로 선택지를 구성한다. diplomat 역할·능력·개인적 편향과 이유 trace는 기존 NPC가 계산한다 | 이미 완성되어 재사용 | `worldline/npc.py:243`, `worldline/npc.py:295`, `worldline/npc_decision.py:16`, `worldline/npc_decision.py:388` |
| 실행 권한·정부 의사결정 | 국가 소속, active, 권한 scope, ruler/cabinet을 확인한다. 조언·선택과 정부 실행은 분리되어 있다 | 이미 완성되어 재사용 | `worldline/npc.py:340`, `worldline/npc.py:354`, `worldline/npc.py:411` |
| 개인 간 외교 관계 | `npc_state.relationships[observer][target_person]`의 trust/grievance/obligation 등은 관측과 기억에서 계산한다. 국가 pair의 실제 관계와 별개다 | 이미 완성되어 재사용 | `worldline/npc_state.py:21`, `worldline/npc_state.py:432` |
| 국제 위기·최후통첩 | border_incident → diplomatic_crisis → ultimatum → mobilization → armed_clash → state_of_war의 원인·기간·압력·응답·resolution은 Crisis가 보유한다 | 이미 완성되어 재사용 | `worldline/crisis.py:15`, `worldline/crisis.py:230`, `worldline/crisis_response.py:447` |
| 중재·철수·상호 휴전 | `mediate`, `withdraw`, `ultimatum`, `mobilize` 등의 기존 응답이 비용·압력·trust/dispute를 처리한다. state_of_war의 쌍방 진정 응답은 armistice를 만든다 | 이미 완성되어 재사용 | `worldline/crisis_response.py:251`, `worldline/crisis_response.py:510`, `worldline/crisis_effects.py:177` |
| 관계 변경 receipt | Crisis effect가 날짜와 idempotency에 따라 실제 관계를 변경하고 before/after receipt를 남긴다 | 이미 완성되어 재사용 | `worldline/crisis_effects.py:177`, `worldline/crisis_effects.py:apply_due` |
| 교역·해외 금융 효과 | Economy는 모든 matching pair record 중 교역 개방의 최솟값과 war 상태를 사용한다. Credit은 실제 war와 해외 차입 접근성을 사용한다 | 이미 완성되어 재사용 | `worldline/economy.py:506`, `worldline/credit.py:45` |
| 군사 통행권의 물리적 판정 | matching relation의 `military_access is True`면 logistics 접근을 허용한다. 승인·협상·방향·기한의 외교 절차는 없다 | 존재하지만 Diplomacy & Intelligence에서 확장 필요 | `worldline/military_logistics.py:76` |
| 실제 적대 상태 | Military의 `_hostile`은 explicit opponent 또는 실제 matching relation의 `status == war`를 사용한다 | 이미 완성되어 재사용 | `worldline/military_operations.py:748` |
| 전쟁 사건과 군사 theater 연결 | `war_links`는 Crisis armed_clash/state_of_war를 theater에 연결하고 사건의 ongoing 여부로 theater를 닫는다. 국가의 공세 intent를 자동 생성하지 않는다 | 이미 완성되어 재사용 | `worldline/military.py:133` |
| 전투 후 평화 압력 | combat의 `peace_pressure` hook을 bridge가 `diplomatic_hooks`에 축적한다. 이를 협상으로 전환하는 production 소비자는 없다 | 연결만 필요 | `worldline/military_combat.py:183`, `worldline/military_bridge.py:272` |
| 평시 제안·응답·협의 이력 | 접촉 offer 이후의 durable proposal ID, 상대국 delivery, accept/reject, 확인 receipt와 합의 lifecycle은 없다 | 실제로 없음 | `worldline/order_execution.py:87`, 현재 production 모듈·호출 검색 |
| 조약·동맹·보장 모델 | treaty/ally 기억 category는 있으나 실제 조약·동맹·보장 record나 의무 이행 엔진은 없다 | 실제로 없음 | `worldline/npc_state.py:26`, `worldline/npc_state.py:360` |

관계 dictionary의 key는 통일된 pair ID가 아니다. 기존 접촉은 `a:b`를 만들지만 Crisis는 기존 matching record의 정렬상 첫 key를 고르거나 `a-b`를 만든다. 현재 Auditor는 endpoint 존재를 검사하며 pair 유일성이나 모든 관계 필드의 domain schema를 강제하지 않는다. 동일 pair에 여러 record가 있는 기존 유효 상태를 정규화·병합하거나 first record 하나로 바꾸면 기존 교역·위기·군사 결과가 바뀔 수 있다.

## B. 현재 정보/첩보 기능 지도

| 기능 | 현재 동작과 소유자 | 분류 | 코드 근거 |
|---|---|---|---|
| player observed-state | Engine의 별도 `observed[player][key]`에 Model `Report(as_of, source, confidence, data)`를 기록한다. World truth와 별도이며 NPC reports와 자동 연결되지 않는다 | 이미 완성되어 재사용 | `worldline/models.py:73`, `worldline/engine.py:792` |
| NPC 보고 저장·belief view | `receive_report`는 actor별 topic을 갱신한다. `actor_view`는 해당 actor의 날짜상 이용 가능한 보고만 노출하며 objective records와 audit accuracy를 제외한다 | 이미 완성되어 재사용 | `worldline/npc_state.py:165`, `worldline/npc_state.py:516`, `worldline/npc_state.py:546` |
| objective 사실과 개인 기억 | immutable `records`와 actor가 받은 `observed_facts`, interpretation, confidence, salience, received_date가 분리되어 있다. 미전달 사건은 remember할 수 없다 | 이미 완성되어 재사용 | `worldline/npc_state.py:184`, `worldline/npc_state.py:228` |
| 기관 기억과 명시적 공유 | `remember_group`은 실제 group member informant의 알려진 경험을 기록한다. 개인들의 모든 보고를 자동 공유하지 않는다 | 이미 완성되어 재사용 | `worldline/npc_state.py:293`, `worldline/npc_state.py:316` |
| 국내 경제 관측 | `npc.sense`는 자기 국가의 행정 보고를 구성한다. skill에 따른 결정론적 해상도 조절이며 외국의 숨겨진 truth를 읽는 일반 센서가 아니다 | 이미 완성되어 재사용 | `worldline/npc.py:53`, `worldline/npc.py:85` |
| 외국 정보 주입 | `Engine.npc_report`와 `npc_outcome`은 trusted scenario/event hook이다. benchmark의 외국 report 주입은 국가 정보 수집 엔진의 구현이 아니다 | 연결만 필요 | `worldline/engine.py:435`, `worldline/npc.py:119`, `worldline/npc_benchmark.py:111` |
| Crisis 지연·왜곡·정정 | actor-specific frozen message, audience, as_of, sent/delivery/received 날짜와 신뢰도·왜곡이 있다. 오래된 늦은 보고는 최신 belief/memory를 덮지 않는다 | 이미 완성되어 재사용 | `worldline/crisis_response.py:86`, `worldline/crisis_response.py:154`, `worldline/crisis_response.py:162` |
| Crisis 관측 경계 | 자동 foreign crisis 보고는 공개할 필드를 걸러내고 이전 actor intelligence를 사용한다. 적국의 실제 국가 동원을 공개하지 않는다 | 이미 완성되어 재사용 | `worldline/crisis.py:182` |
| Military 보고와 command delay | `military_state.commands`에 command와 report가 함께 저장된다. operation과 recipient에 묶인 queue이며 수신 뒤에만 NPC belief/memory가 바뀐다 | 이미 완성되어 재사용 | `worldline/military_operations.py:324`, `worldline/military_operations.py:375`, `worldline/military_operations.py:495` |
| 현지 군사 판단 | 전달받은 보고와 명시적 local delegation을 통해 commander가 reconsider/limited_local_action을 선택한다. 국정·전쟁 선포를 대신하지 않는다 | 이미 완성되어 재사용 | `worldline/military_operations.py:688`, `worldline/crisis_response.py:596` |
| 정보 은폐·명령 지연 intent hook | NPC ACTION_HOOKS의 conceal_information/command_delay 등은 local_action의 accepted request다. `hook_only`이며 정보망·메시지 interception을 실제 실행하지 않는다 | 존재하지만 Diplomacy & Intelligence에서 확장 필요 | `worldline/npc.py:15`, `worldline/npc.py:354` |
| 전술 intelligence | combat capability에 intelligence quality 입력이 있고 결과에 contact·enemy range·confidence가 있다. 국가 첩보망의 수집·정확도 판정 모델은 아니다 | 존재하지만 Diplomacy & Intelligence에서 확장 필요 | `worldline/military_combat.py:33`, `worldline/military_combat.py:110`, `worldline/military_combat.py:180` |
| 군사 보고의 정치적 활용 | 군사 증원 결정은 자기 actor의 matching delivered report snapshot을 저장하며 hidden enemy truth로 NPC score/RNG를 바꾸지 않는다 | 이미 완성되어 재사용 | `worldline/military_politics.py:188`, `worldline/military_politics.py:299` |
| 군사 결과의 사건·정보 연결 | `military.publish`가 실제 결과를 Crisis 사건·effect·recipient별 dispatch로 연결한다 | 이미 완성되어 재사용 | `worldline/military.py:19` |
| 국가 정보의 공통 provenance·배포 | Crisis event/operation에 속하지 않는 국가 정보에 대해 수집 snapshot, 대상별 배포 receipt, 전달 권한과 recipient-scoped history를 함께 관리하는 domain은 없다 | 실제로 없음 | 기존 World/state bucket·API 검색 |
| 독립 정찰·첩보 활동 | patrol 종류는 있으나 KINDS에 별도 reconnaissance 임무가 없고 spy/network/agent 모집·유지·탐지 모델도 없다 | 실제로 없음 | `worldline/military_operations.py:17`, 현재 production 정의 검색 |

Engine의 `true_state`, `ledger`, `historical_state`, trusted inject/send hook은 privileged runtime API다. `npc_view(actor)`와 `observed_state(player)`의 반환값은 격리되지만 Engine 객체 자체가 caller 인증·권한 계층은 아니다. 이 객체나 DB를 NPC에 주면 정보 경계가 무너진다. 새 view도 trusted caller가 올바른 actor를 선택한 후 detached한 관측만 전달해야 한다.

## C. 기존 시스템과 중복되는 부분

| 새로 만들 경우의 중복 | 분류 | 유지할 기존 소유자·연결 규칙 |
|---|---|---|
| diplomat 성격·utility·정부 투표·충성도 재구현 | 중복 구현 위험 있음 | `npc_decision`의 scorer와 `npc`의 정부·권한 구조를 사용한다 |
| report를 받은 뒤의 믿음·기억·정정 store | 중복 구현 위험 있음 | `npc_state.reports/memories/institutional_memories`가 유일한 NPC perception store다 |
| border incident·ultimatum·mobilization·휴전 effect | 중복 구현 위험 있음 | 기존 사건은 Crisis에 명령·reference로 연결하며 새 Diplomacy가 같은 effect를 다시 적용하지 않는다 |
| 작전 보고·command delay·현지 자율판단 | 중복 구현 위험 있음 | operation-linked 정보와 명령은 Military queue를 사용한다 |
| casualty·occupation·blockade·전술 intelligence adjudication | 중복 구현 위험 있음 | Military가 실제 결과를 만들고 기존 bridge/publish가 회계·Crisis에 전달한다 |
| trade access·foreign debt·금융 비용 계산 | 중복 구현 위험 있음 | Economy/Credit의 물리적 결산·접근 판정을 사용한다 |
| parallel 국가 관계 truth·전역 평판 score | 중복 구현 위험 있음 | 국가 실제 관계는 `World.relations`, 개인 판단은 NPC 개인별 관계에 남긴다 |
| 두 번째 Event Ledger·global archive | 중복 구현 위험 있음 | 기존 Engine transaction/ledger와 content-addressed archive를 확장한다 |

기존 Crisis/Military message를 새 국가 intelligence message로 무조건 재발행하면 동일 사건이 두 번 수신·기억되거나 순서가 달라질 수 있다. 기존 recipient에게 이미 전달한 메시지는 원래 domain·message ID·recipient를 연결 receipt로 참조한다. 추가 actor에게 명시적으로 전달할 때만 새 delivery를 만들고 원래 observation identity를 보존한다. 기관 공유도 기존의 명시적 informant 공유 규칙을 따른다.

## D. D&I v1 최종 승인 범위

필수 범위는 다음 네 가지로 고정한다.

1. 평시 국가 간 `kind=contact` proposal의 durable 제출 → 상대 수신 → accept/reject → 발신자 응답 수신 lifecycle.
2. actor별 proposal/response inbox와 관측 상태. Engine의 실제 terminal 상태와 수신자가 아는 상태를 분리한다.
3. Crisis/Military 외부의 일반 intelligence observation snapshot과 명시적 recipient dispatch. trusted ingestion과 결정론적 지연 전달을 제공한다.
4. 위 기능의 opt-in save / transaction / 단일 Ledger / audit / recipient-scoped query 계약.

contact acceptance는 접촉을 받아들였다는 결과다. 조약 의무·교역 개방·통행권·전쟁 종료·trust/dispute 변경을 자동 발생시키지 않는다. 새 메시지가 NPC belief/memory에 남는 것은 기존 perception 처리이며 국가 관계 truth를 다시 계산하는 효과가 아니다.

일반 treaty DSL, 동맹 자동 이행·연쇄 참전, guarantee, 군사통행권 협상, peace settlement, 첩보망, spy/agent, 공작·방첩·기만, 국제 통신망, 전역 intelligence score는 보류한다. 다자 협상·counteroffer·취소/withdrawal·deadline/expiry·자동 peace-pressure 협상·전 국가 쌍 자동 접촉도 첫 왕복에 필요하지 않으므로 v1 필수에서 제외한다. 취소·만료는 기존 감사의 후보 설계였으며 이번 freeze에서는 채택하지 않는다. 미응답 제안은 pending으로 저장·조회·재개할 수 있다.

## E. 책임과 actor identity 계약

국가의 실제 관계 truth는 기존 **`World.relations`**가 소유한다. 관계 key·중복 pair record와 consumer별 min/max/any/first 의미를 유지한다. Diplomacy는 contact lifecycle·응답·전달·실행 receipt, Intelligence는 관측 provenance·명시적 배포를 소유한다. NPC는 받은 정보의 belief/memory·선택 이유·정부 판단, Crisis는 위기·최후통첩·휴전, Military는 작전·군사 보고·지연 명령, Economy/Credit은 실제 자원·교역·신용을 계속 소유한다. 기존 기능을 복제하지 않는다.

### E1. 기존 ID를 사용하는 최소 reference

Worldline에서는 개인뿐 아니라 국가·집단·기관도 제품상의 행위자가 될 수 있다. v1을 위해 범용 Actor hierarchy나 전체 ID 체계를 만들지 않는다. 역할별 reference를 분리하며 같은 문자열이 다른 namespace에 있어도 자동 동일시하지 않는다.

| 역할 | v1 reference와 실제 소유자 | 계약 |
|---|---|---|
| 외교의 실제 국가 party | `proposer_country_id`, `recipient_country_id`: `World.countries` key / `Country.id` | 서로 다른 기존 국가 두 곳. person/group/military ID가 국가 party를 대체하지 않음 |
| 의사결정 주체 | `government_country_id`: `npc_state.governments[country_id]`; 판단 참여자는 `decision_person_ids` | government는 country-keyed record다. ruler/cabinet의 person ID와 기존 decision_rule을 사용 |
| 실행 권한을 가진 대표자 | `executing_person_id`: `npc_state.persons` key | receipt에 실제 집행자를 기록. 선택을 조언한 사람과 집행자는 다를 수 있음 |
| proposal sender / recipient | `sender_person_id`, `recipient_person_id`: `npc_state.persons` key | 각각 proposer/recipient 국가 소속의 명시적 대표 개인. opaque `proposer_actor` / `recipient_actor` 필드는 채택하지 않음 |
| 정보·inbox recipient | `recipient_ref={kind:"person", id:person_id}` 또는 `{kind:"player", id:player_id}` | person은 NPC namespace, player는 기존 `Engine.observed`의 player key namespace. kind 생략·국가 ID 암묵 변환 금지 |
| 집단·기관 | 기존 `npc_state.groups[group_id]`, membership 및 institutional_memories | 기존 명시적 informant 공유만 재사용. 직접 contact party·새 기관 inbox는 후속 범위 |
| 군사 actor | 기존 `military_state.actors[military_actor_id]`와 country adapter | military_actor_id와 NPC commander person ID를 구분. 새 일반 recipient/party로의 확대는 후속 범위 |

legacy `World.factions`와 `Country.npc` 설명 자료를 새 party/권한 store로 사용하지 않는다. 정부를 의미하는 group도 country-keyed government record와 자동 동일시하지 않는다. 비국가 party·기관 독립 외교·군사 actor 직접 정보 수신은 v1에 넣지 않는다. 기존 Crisis/Military의 지원 recipient는 계속 원래 domain에서 처리한다.

player의 인증 주체와 국가 대표 개인은 별개다. trusted host가 player/session의 제어권을 확인하고, D&I의 저장 가능한 명시적 `player_bindings[player_id]={country_id, representative_person_id}` 및 reply-recipient 설정과 결합한다. 이 설정은 새 범용 인증/Actor 시스템이 아니라 v1 제어·수신 route다. 기존 `crisis_state.player_countries`와 정부 autonomous 정책을 존중한다. player ID가 country ID와 같아도 binding 없이 권한을 얻지 않는다. session 인증은 transport의 책임이고 세계 내 국가·대표·수신 권한은 Engine이 다시 검증한다.

### E2. 판단·실행·수신의 분리

기존 `npc._authority`의 외교 의미를 유지한다: 현재 person 존재·해당 국가 소속·active, `diplomatic_engagement` scope, 현재 정부 ruler/cabinet의 실행 자격을 모두 검사한다. 높은 diplomacy skill·trust, diplomat role, 기관 membership만으로 실행 권한을 부여하지 않는다. 일반 비정부 diplomat의 새 위임 모델은 후속 범위다. 제출과 응답 각각에서 권한을 재검사한다.

NPC의 조언은 실제 수신한 proposal snapshot과 actor-local belief/memory만 사용한다. 정부는 기존 executive/cabinet 의사결정 규칙으로 선택하고 권한 있는 person이 집행한다. 대표 한 명이 받았다는 이유로 내각 전원이 제안을 알게 되지 않는다. 여러 판단자에게 전달할 경우 각각 명시적인 recipient receipt가 필요하고 미수신 판단자는 해당 제안 평가에 참여시키지 않는다. 실제 받은 근거로만 판단하는 얇은 adapter를 추가하며 legacy monthly council·Crisis/Military 판단을 다시 실행하지 않는다.

첫 acceptance fixture는 외국 정부의 기존 `decision_rule=executive`, autonomous 정부와 ruler를 recipient/executor로 지정할 수 있다. 이 지원 profile은 첫 왕복을 성립시키며 새 정치/내각 엔진을 요구하지 않는다. player-controlled 국가의 응답을 NPC가 대신 결정하지 않는다. 대표가 비활성화되거나 권한을 잃어도 메시지를 다른 개인에게 자동 재주소하지 않고, 실제 수신 사실과 현재 집행 권한을 구분해 보존한다.

## F. 최소 state / mutation / query 계약

### F1. authoritative state와 lifecycle

다음은 미래 구현의 논리 계약이다. 현재 production에 존재하는 타입이라고 주장하지 않는다.

| 저장 위치 | 최소 내용 | 소유권·불변 조건 |
|---|---|---|
| `World.diplomacy_state` version 1 | proposals, dispatches, receipts, outbox, next_ids, 명시적 delay/player binding 설정; 필요 시에만 Diplomacy RNG state | contact lifecycle과 배포 증거. 국가 relation을 mirror하지 않음 |
| proposals[id] | immutable 생성 body: 양 국가 ID, sender/recipient person ID, 명시적 reply recipient refs, `kind=contact`, bounded 내용, created_at, source_ref; actual status와 response receipt ref | actual status는 `pending/accepted/rejected`. 수신 여부는 status에 섞지 않음 |
| contact dispatch / receipt | proposal ID, message kind `proposal/response`, typed recipient, frozen payload, sequence, sent_at, delivery_date, `status=queued/delivered`, received_at; 집행·전달 receipt | proposal actual status와 전달 status는 독립. proposal/response 배포는 Diplomacy 소유. 상대 actual status는 전달 완료 전 view에 없음 |
| `World.intelligence_state` version 1 | observations, dispatches, receipts, outbox, next_ids, 명시적 source/audience 설정 | 비-Crisis/비-operation 관측 배포. RNG·spy·network state 없음 |
| observations[id] | source_ref(domain/id), stable event/subject/topic identity, collector reference, as_of, collected_at, frozen observed_data; audit-only provenance | 관측은 오래되거나 틀릴 수 있음. source truth와 분리 |
| intelligence dispatch | observation ID, typed recipient, frozen allowed payload, sequence, sent_at/delivery_date, status, received_at, belief_accepted | 수신 전 NPC belief나 player observation에 넣지 않음 |
| 기존 perception store | NPC reports/memories/institutional_memories; player `Engine.observed` | person은 기존 receive_report/remember, player는 기존 record_observation 경로로 연결. 두 번째 belief store를 만들지 않음 |

제출 transaction은 proposal·발신 receipt·queued dispatch/outbox를 함께 기록한다. 상대방이 proposal을 실제 수신한 뒤, pending 상태에서 현재 권한 있는 대표자의 accept 또는 reject 한 번만 terminal 전이를 만든다. 응답 receipt와 frozen response dispatch/outbox는 그 전이와 같은 transaction에 기록한다. 발신자는 응답을 실제 수신할 때만 accepted/rejected를 관측한다. player에게도 명시적인 reply dispatch와 수신 receipt가 있어야 결과가 알려진다. 같은 국가라는 이유로 person에게 온 응답을 player에게 즉시 복사하지 않는다.

같은 request/receipt ID와 같은 intent의 재시도는 기존 결과를 반환하고 재전달·재기억·재실행하지 않는다. 다른 intent의 ID 재사용은 충돌이며, terminal 상태의 다른 응답은 거부한다. 동시 명령은 기존 Engine 직렬 transaction 순서로 처리하며 첫 terminal 전이가 유지된다. 권한·수신·상태 검증 오류는 미수신 상대 상태를 자세히 공개하지 않고 actor에게 허용된 행동 불가 결과만 반환한다.

contact의 기존 `diplomatic_engagement` 즉시 `last_engagement`/offer 경로를 몰래 대체하지 않는다. 별도 opt-in contact API와 receipt를 사용하며 legacy intent 연결은 후속 명시적 작업이다. 새 contact는 관계 status/trust/dispute/trade_openness/military_access를 바꾸지 않는다.

### F2. 미래 API 경계

| 계약 API | caller와 검증 |
|---|---|
| `Engine.enable_diplomacy_intelligence(config)` | trusted host의 명시적 opt-in transaction. G의 지원 profile·binding·지연 설정 검사 |
| `propose_contact(request_id, proposer_country_id, recipient_country_id, sender_person_id, recipient_person_id, content)` | bound player 또는 권한 있는 NPC 대표의 구조화된 접촉. 국가·대표·제어권·scope·kind·내용 한도·ID 검증 |
| `respond_contact(request_id, proposal_id, executing_person_id, accept_or_reject)` | 실제 수신 근거·상대 국가·현재 권한·pending 재검사. NPC 자동 판단도 같은 mutation 사용 |
| `diplomacy_view(subject_ref)` | typed person/player의 직접 제출한 요청과 실제 받은 proposal/response만 detached committed projection으로 반환 |
| `ingest_observation(source_ref, observed_data, audience, ...)` | trusted source hook. 명시적인 allowed snapshot을 제공하며 LLM/NPC가 objective fact를 만드는 API 아님 |
| `queue_intelligence_report(observation_id, recipient_ref, ...)` / internal `deliver_due` | trusted source/audience 검증 후 결정론적 frozen 배포·수신·receipt |
| `intelligence_view(subject_ref, subject_or_topic=None)` | 해당 recipient가 실제 받은 관측과 허용 provenance만 반환. 전체 observation·미수신 dispatch·audit accuracy 제외 |

reply recipient는 trusted 저장 설정과 요청 principal에서 Engine이 결정하며 LLM이 arbitrary audience를 추가하지 않는다. query는 actor가 알고 있는 상태를 반환하므로 sender의 조회 시 상대 수락 여부를 actual table에서 읽어오지 않는다. 반환값을 수정해도 Engine state가 바뀌지 않아야 한다. transaction 중 public view는 기존 committed isolation을 유지한다.

### F3. 시간·관측·기억

모든 날짜는 simulation clock을 쓴다. 최초 전달 delay는 trusted 설정의 명시적 유한 비음수 정수 day 값이다. 첫 acceptance의 양방향 delay는 양수로 고정한다. 관측 시간 `as_of` ≤ 수집 `collected_at` ≤ 발신 `sent_at` ≤ 예정 수신 `delivery_date`, 실제 수신 `received_at` ≥ 예정 수신을 검증한다. 미래 관측은 거부하며 query나 wall clock으로 수신을 앞당기지 않는다. 기존 국내 leg가 있을 때만 기존 communication_delay helper를 그대로 사용한다. 국제 route/통신망을 새로 계산하지 않는다.

due 순서는 새 domain 안에서 `(delivery_date, domain-local sequence, message_id)`로 고정한다. 수신 시 recipient·subject/topic별 최신성은 `(as_of, sent_at, domain-local sequence, message_id)` 순서로 판단한다. 늦은 오래된 메시지는 수신 이력에는 남지만 최신 belief/memory를 덮지 않는다. correction은 새 snapshot이며 stable event identity로 중복 기억을 막고 원본 frozen body를 수정하지 않는다. 기존 Crisis/Military의 순서·동률 규칙은 바꾸지 않는다.

기존 `npc.options_for`가 읽는 단일 `diplomacy` topic에 새 proposal과 여러 국가 report를 계속 덮어쓰지 않는다. proposal-ID inbox와 subject/topic query에서 실제 수신 근거만 좁은 contact 평가 adapter에 넘긴다. 같은 나라/기관의 모든 보고를 union하지 않는다. trusted Crisis queue의 omitted observed_facts→actual_facts 기본값을 일반 intelligence 정책으로 사용하지 않는다.

기존 Crisis/Military 보고는 기존 queue/publish에서 처리한다. v1이 이 메시지를 자동 재발행하거나 queue를 공통 통신 엔진으로 이전하지 않는다. 후속 연결 시 원래 domain/message/recipient의 delivery reference를 보존하고 동일 receipt에 두 번째 receive_report/remember를 호출하지 않는 계약을 적용한다. 해당 연결 자체는 첫 왕복의 선행 조건이 아니다.

## G. save / version / RNG / transaction 최종 계약

### G1. World schema 6 / save schema 7 채택

**opt-in World schema 6 / save schema 7을 최종 채택한다.** 현재 loader의 World 1~5/save 1~6 허용·feature presence 검사 및 NPC adapter exact-key 검사 때문에 새 authoritative state를 기존 임의 dict에 숨기는 무승격 대안은 더 안전하지 않다. 기존 감사 근거는 `worldline/models.py:121`, `worldline/persistence.py:8`, `worldline/npc_state.py:571`이며, 불필요한 schema 재설계를 하지 않는다.

최초 activation 지원은 Economy v3/NPC v1/Crisis v1/Military v1이 이미 켜진 **World 5 / save 6의 기존 다섯 builtin system profile**이다. Military 없이 D&I를 쓰는 조합과 custom system activation은 후속 검증 범위로 남긴다. 지원하지 않는 profile의 activation은 전체 transaction을 atomic하게 거부한다.

acceptance contract는 다음과 같다.

- 기존 save **1~6을 load했다고 World 6/save 7로 자동 변환하거나 D&I를 자동 활성화하지 않는다.** 기존 normalize·기능 presence·rule version 규칙은 유지한다. 기존 World 1→2 normalization을 제거하거나 입력 파일의 raw bytes 그대로 반환한다고 주장하지 않는다.
- feature off에서는 안정 baseline의 동일 load/save/continue가 만들던 **기존 save bytes, World, RNG, Ledger 의미를 정확히 보존**한다. 새 state key/null·system entry·named stream을 직렬화하거나 생성하지 않는다.
- 새 authoritative state는 명시적 `World.diplomacy_state` / `World.intelligence_state`에 저장한다. relations/canon/NPC adapter/기존 임의 dict에 숨기지 않는다.
- activation은 trusted host의 **명시적 command**다. NPC/LLM 요청·조회·load가 activation을 유발하지 않는다. activation receipt, version, 설정과 지원 조건을 저장·audit한다.
- 기존 `projects → economy → npc → crisis → military` system prefix와 기존 rule/system version은 유지한다. enabled profile만 그 뒤에 **intelligence delivery → diplomacy processing**을 append한다. 같은 tick Diplomacy 단계는 contact delivery 후 기존 pure evaluator를 호출할 수 있으나 legacy council을 재실행하지 않는다.
- World/save/feature version 불일치·presence 불일치·unknown system을 거부한다. 기존 enable API가 World 6을 3/4/5로 낮추지 않도록 guard한다. 새 reader의 기존 save 호환은 보장하지만 이전 binary의 save 7 forward compatibility는 약속하지 않는다.
- save/load는 proposal·outbox·dispatch·actual/observed lifecycle·receipt·player bindings·NPC belief/memory·player observed state·Ledger·archive·RNG를 보존한다. 대화 이후 재조회와 다음 날짜 진행이 uninterrupted 실행과 같아야 한다.

### G2. RNG

최초 일반 intelligence ingestion/delivery·contact delivery·query는 **deterministic이며 RNG를 사용하지 않는다.** activation·빈 tick·단순 조회도 기존 global/NPC RNG를 소비하거나 새 stream을 만들지 않는다.

새 NPC contact 선택에 실제 noise가 필요한 경우에만 **독립 named Diplomacy stream**을 lazy 생성한다. 기존 NPC 정기 option 목록·평가 일정·global/NPC RNG draw 순서를 바꾸지 않고 Crisis/Military stream을 빌리지 않는다. deterministic evaluator로 충분하면 RNG state 자체가 필요하지 않다.

필요한 stream의 root는 저장된 Engine seed, 고정 `worldline-diplomacy-v1` namespace/version, scenario identity로 SHA-256 파생한다. stream key는 명시적 country pair와 country-pair-local decision sequence로 구분하고 root/state/sequence를 opt-in diplomacy_state에 저장한다. 전역 proposal/message ID sequence를 stream key로 사용하지 않는다. Python hash·wall clock·미저장 random instance를 사용하지 않는다. 무관한 pair의 요청·조회가 다른 pair의 draw를 이동시키지 않아야 한다. 실패 시 draw와 생성된 stream까지 rollback한다. spy/intelligence uncertainty RNG는 v1 구현 범위에서 제외한다.

### G3. transaction / Ledger / archive / audit

기존 `Engine._transaction`과 mutation Journal 하나를 사용한다. 실패 시 **state/outbox/dispatch/receipt/NPC belief·memory/player observation/Ledger/archive**와 next_ids, global/domain RNG, component/head metadata가 함께 복원되어야 한다. response 상태만 되돌리고 통지·기억을 남기는 partial rollback은 금지한다. publish/SQL/archive 실패도 같은 범위에 포함하며 관련 derived index·cache는 무효화한다.

Event Ledger는 기존 append-only SQL Ledger 하나다. domain·kind·proposal/observation/message/receipt ID와 pending outbox를 연결해 exactly-once와 state↔ledger 일치를 좁게 검사한다. accepted/rejected actual과 actor 관측은 별개이며 상세 truth 원인은 trusted Ledger/audit에만 남긴다.

archive는 기존 content-addressed codec·SHA/length·catalog 불변성·detached decode를 사용한다. 감사에서 확인한 `state_storage.compact`의 명시 bucket 및 `write_components` 분기에 두 새 state를 제한적으로 등록한다. 생성 snapshot/dispatch/receipt를 namespaced kind로 보존하고 mutable lifecycle의 이전 object를 덮어쓰지 않는다. hidden archive는 actor knowledge source가 아니다. 새 query는 recipient별 실제 전달 evidence에서만 결과를 구성한다.

## H. Runtime Query Performance 영향

기존 감사 분석을 유지한다. 아래 최적화 후보 전체를 v1 필수 구현으로 승격하지 않으며 F/G의 query 정확성과 기존 Runtime 의미 보존을 우선한다.

현재 ID lookup은 이미 dictionary이므로 country/region/actor용 범용 lookup cache를 새로 만들 이유가 없다. 국가별 사람·지역 목록의 실제 반복 요구가 생기면 기존 owner query와 domain dictionary를 사용하고 필요를 측정한 뒤 membership group을 추가한다. 모든 국가 쌍이나 모든 actor의 intelligence 목록을 미리 생성하지 않는다.

현재 relation record 수를 R이라 하면 교역 T개가 pair마다 scan하는 경로는 O(T×R), configured border B개의 pressure review는 O(B×R), K번의 접근·적대 검사도 O(K×R)까지 늘 수 있다. sparse한 기존 관계와 실제 query 수가 근거이며 국가 수만으로 O(N²) 빈 relation을 생성할 이유는 없다. 새 pair group의 cold 구축은 O(R), warm 읽기는 matching record 수와 변경 key 수의 영향을 받는다. 기존 consumer를 연결할 때는 집계·실패·순서의 ref-scan equality를 확인한 경로만 좁게 바꾼다. 새 subject/report는 actor별 H의 영향을 받으므로 전역 H scan보다 기존 actor dictionary와 recipient group을 우선한다.

| 필요한 조회 | 최소 제안 | 범위·무효화 |
|---|---|---|
| 실제 국가 pair의 모든 relation record | 기존 `runtime_queries.grouped_items`로 정렬 tuple(a,b) group | endpoint·삽입·삭제·교체를 추적. 모든 matching record와 기존 key 순서 보존. 기존 소비자의 min/max/any/first 의미를 통일하지 않음 |
| 새 proposal의 ID lookup | proposals dictionary 직접 lookup | cache 불필요 |
| actor가 받은 pending contact | dispatches의 recipient group + delivered 후보/현재 상태 filter | actor가 알 수 있는 상태 기준. global actual proposals를 filter 없이 노출하지 않음 |
| 새 due dispatch | status=`queued` selection 후 현재 날짜 검사 | date를 index predicate의 captured 상수로 넣지 않음. 최초에는 active 후보만 scan하며 heap은 측정 뒤 검토 |
| actor·subject/topic의 최신 수신 정보 | delivered/accepted의 `latest_by_group` | group은 recipient·stable subject/topic, order는 정의된 관측/발신 순서. 날짜·동률·correction 처리 보존 |
| actor별 과거 보고 | 새 dispatch에 필요한 recipient group; NPC의 현재 report는 기존 actor dictionary | 과거 모든 버전은 domain dispatch/archive, belief는 최신 topic. 전역 history scan·actor별 중복 전역 memo를 만들지 않음 |
| actor 의사결정의 공통 입력 | 기존 actor-local context와 delivered view 재사용 | 한 동기 평가 구간에서만 유지. report 수신·기관 공유·기억 수정 이후로 넘기지 않음 |

현재 cache name은 한 collection의 고정 group/predicate/order 계약을 식별한다. 새로운 query는 domain/version별 이름을 사용한다. `grouped_items`와 후보 record는 borrowed reference이므로 public API에서 detached 복사를 해야 한다. 원본 body만 의존하는 pure selector를 사용하고 government authority, membership, 날짜처럼 다른 collection에 의존하는 조건은 조회 시 확인하거나 명시적 multi-dependency token을 둔다. 한 날짜만으로 belief/권한/route/금융 값을 memoize하지 않는다.

tracked nested edit·insert/delete/replacement는 해당 key를 refresh한다. rollback, collection 교체, bounded log 4,096개 gap은 rebuild하며 신규 plain descendant는 barrier 전에 재검사한다. 실패 token은 폐기하고 raw mapping은 fresh scan을 유지한다. cold load와 history index의 O(history) 메모리를 숨기지 않는다 (`worldline/runtime_queries.py:28`, `worldline/state_runtime.py:353`).

공개 조회 경계도 별도로 측정해야 한다. 현재 `Engine.npc_view`는 `true_state` 사본을 만든 뒤 actor_view를 구성한다. detached World에는 tracked index가 없으므로 거기에 index를 붙여도 hot internal query와 같은 효과를 얻지 못한다. `observed_state(player)`도 현재 전체 `_stored_payload()` 경로를 쓴다 (`worldline/engine.py:453`, `worldline/engine.py:801`). 최초 구현은 기존 committed isolation을 유지하고 public 복사 비용을 함께 측정한다. 실제로 지배적이면 기존 DB text/data_version/total_changes/external_dirty gate와 transaction committed frame을 그대로 쓰는 작은 domain read helper만 추가한다. 후보 `_world`를 읽어 미커밋 정보를 내보내는 우회는 금지한다. 내부 index benchmark와 Engine endpoint benchmark를 구분한다.

## I. 후속 구현 acceptance와 검증 범위

이 절은 미래 구현의 검증 계약이다. 이번에는 테스트를 추가하거나 실행하지 않았다.

| 축 | 구현 후 acceptance |
|---|---|
| feature off / legacy | 안정 commit과 동일 조건에서 World/RNG/Ledger/archive/기존 save bytes exact equality; save 1~6의 기존 load/save/continue와 자동 활성화 없음 |
| contact 왕복 | 제출·지연·상대 실제 수신·NPC 정부 accept/reject·응답 지연·player 실제 수신·후속 기억·save/load/continue |
| identity / authority | country/person/player namespace 구분; 현재 active·소속·scope·정부 자격·binding 검증; 미수신·비권한 응답 거부 |
| information isolation | recipient 밖의 inbox·actual response·source truth·accuracy·전체 Ledger/archive 비노출; query 반환값 수정과 transaction 중 committed isolation |
| idempotency / determinism | 같은 intent의 재시도는 동일 receipt·효과 1회; conflicting ID·두 번째 응답 거부; 같은 command/date/order 결과 일치 |
| 관측·기억 | as_of와 received_at 분리; stale report가 최신 belief를 덮지 않음; correction·사건 identity에 따른 중복 기억 방지 |
| save / rollback | in-transit·수신 후 미응답·응답 후 발신자 미수신·완료 상태를 저장/재개; state/outbox/receipt/belief/Ledger/archive 및 RNG failure rollback |
| 기존 domain 보존 | Crisis/Military/Economy/NPC 효과·queue·prefix·기존 RNG 순서 보존; contact 수락으로 조약·전쟁·교역·돈 효과 없음 |
| query / storage | 기존 Runtime invalidation·plain mapping fallback·recipient filtering·detached 반환·archive identity와 version/presence 검사 |

후속 코드 변경 시 필요한 D&I 동작 검사와 기존 전체 회귀 **1,350개 유지**를 확인한다. 이번 문서 변경에서는 전체 regression을 재실행할 이유가 없다. 20년/30년 benchmark 재실행이나 새 대형 benchmark 설계는 이번 작업에 포함하지 않는다. 작은 2국 왕복 fixture와 실제 query 비용 확인으로 시작하고 성능 최적화는 측정된 필요에 한정한다. H의 pair-index/consumer 최적화 후보는 감사 참고이며 v1의 독립 필수 gate가 아니다.

## J. 후속 순서

1. **완료: 안정 기준점과 범위 고정.** 지정 commit/tag와 기존 1,350 passed를 기준으로 이 문서의 최소 계약을 freeze했다.
2. **D&I 최소 구현.** 명시적 activation·state/save·transaction/Ledger/archive·recipient query, deterministic 일반 관측 전달, contact 왕복과 기존 NPC 판단 연결을 구현한다.
3. **LLM thin slice.** 아래 M의 transport-neutral read/write 계약을 특정 transport에 연결하는 최소 adapter를 후속 작업에서 만든다.
4. **첫 실제 플레이 → play-driven fixes → 장기 캠페인 준비.** 첫 왕복 이후 실제 대화에서 생긴 결함을 수정하고 장기 운영으로 확대한다.

큰 리팩터링·새 조약/첩보망/사회/이념/금융 엔진·MCP/API 구현·첫 캠페인 시대 결정은 이번 문서 작업에서 수행하지 않는다.

## K. 보류와 남는 구현 위험

v1 보류 목록은 D에 고정했다. actor 종류 확대·일반 기관 inbox·Military actor 직접 recipient·새 대표 위임 모델·lightweight feature 조합·custom system activation·일반 report forwarding도 후속 범위다. 특정 LLM vendor/요금제/OpenAI API/ChatGPT MCP/Apps SDK/UI를 선택하지 않는다.

남는 구현 위험은 actual과 observed lifecycle 혼동, ID namespace 혼동, 같은 정보의 이중 수신·기억, query에 hidden truth/accuracy 노출, 새 NPC 평가의 기존 RNG 이동, 저장 분기 누락과 partial rollback이다. 이 위험은 I와 FIRST_PLAY_SLICE.md의 acceptance로 처리하며 새 대형 시스템을 추가하는 근거로 쓰지 않는다.

## L. 구현 시작 가능 여부

**설계 게이트 GO: 범위는 확정되었으며 후속 D&I 최소 구현을 시작할 수 있다.** 이전 감사의 안정 checkpoint와 범위 채택 조건은 지정 commit/tag 및 이번 문서 계약으로 충족되었다. 설계 통과가 기능 구현·실행 검증 완료를 뜻하지 않는다. 현재 phase는 **scope frozen / implementation not yet started**이며 이번 요청에 따라 구현은 시작하지 않았다.

이번 변경은 이 감사 문서와 새 `CURRENT_STATE.md`, `FIRST_PLAY_SLICE.md`뿐이다. 기존 감사의 A~C/H 코드 분석과 과거 증거는 유지했다. 생산 코드·테스트 수정, pytest·장기 benchmark 실행, commit은 수행하지 않았다. Git 저장소가 구현의 source of truth이며 짧은 handoff와 첫 플레이 acceptance는 두 새 문서를 따른다.

## M. 첫 LLM 플레이의 transport-neutral boundary

사용자 자연어 → bound actor/player에게 허용된 관측 조회 → 구조화된 질문/접촉/제안 → Engine 권한·상태 검증 → 기존 NPC/정부 판단 → 실제 결과·전달 지연 → actor/player의 허용 결과 재조회 → LLM 자연어 표현 순서를 지원한다. 질문은 read이며 새 세계 fact를 생성하지 않는다. 제안의 v1 kind는 contact로 한정한다.

| 경계 | 허용 계약 |
|---|---|
| host/session binding | trusted host가 인증된 player와 제어 country/representative person, 허용 subject refs를 bind. LLM이 입력 ID로 인증·제어권을 바꾸지 못함 |
| LLM read | 기존 `observed_state(player_id)`, 허용 person의 `npc_view(person_id)`, 새 `diplomacy_view(subject_ref)` / `intelligence_view(subject_ref)`의 detached committed projection. 여러 view의 임의 전역 union 없음 |
| LLM write | bounded structured `propose_contact`; 실제 수신한 contact에 대한 권한 있는 `respond_contact`; 명시적인 simulation time advance 요청을 host allowlist로 라우팅. player-controlled 결정을 NPC가 대신 집행하지 않음 |
| Engine 최종 권한 | ID·party·binding·대표 authority·수신 evidence·lifecycle·중복·simulation time·NPC/정부 판단·실제 mutation·delay·save/rollback 검증. 자연어가 성공/수신/합의를 선언해도 상태를 변경하지 않음 |
| Engine→LLM 결과 | 제출 receipt·actor가 아는 pending 상태·실제 수신한 response/report·허용 오류만 반환. 엔진 내부 결정 이유는 해당 actor에게 실제 전달된 부분만 표현 |
| LLM 표현·기억 | receipt/proposal/event ID 및 allowed view에 근거해 자연어를 표현. 후속 대화와 load 후 재조회로 같은 상태를 회복하며 LLM transcript를 세계 truth로 저장/복원하지 않음 |

**`Engine.true_state`, 전체 Ledger, DB path, hidden archive, 미수신 actual response, source의 비공개 사실, audit accuracy는 LLM에 전달하지 않는다.** Engine 객체나 SQLite handle도 adapter payload로 주지 않는다. `npc_report`, `npc_outcome`, observation ingestion/queue, feature enable, 직접 World/RNG/Ledger 쓰기는 trusted runtime hook이며 LLM write allowlist가 아니다.

첫 플레이는 `FIRST_PLAY_SLICE.md`의 contact 왕복으로 판정한다. 대화 transcript 보관은 transport/adapter의 선택이며 필수 세계 상태는 Engine save에 있다. 새 LLM session도 허용된 view와 수신 receipt를 재조회해 계속할 수 있어야 한다. 이번 단계에서는 LLM adapter 코드를 구현하지 않는다.
