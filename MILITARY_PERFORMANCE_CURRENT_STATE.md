# Military v1 성능 개선 후 현재 구조

이 문서는 Military & Logistics v1의 계산·저장 구조를 설명합니다. 게임 기능과 규칙을 추가하지 않습니다. 이전 완료 상태는 프로젝트 바깥 `../checkpoints/military-v1-stable/`의 ZIP·파일별 SHA-256 manifest·독립 실행 가능한 frozen source에 보존했습니다. 원격 push는 하지 않았습니다.

## 한 번의 명령 또는 날짜 진행

1. `BEGIN IMMEDIATE`와 mutation journal의 outer frame을 엽니다.
2. 잠금 안에서 SQLite committed revision과 다른 writer의 변경 여부를 확인합니다. SQLite가 source of truth입니다. 날짜별 frame은 현재 객체를 공유하고, 최초로 바뀐 record/container의 이전 값만 보존합니다. RNG·seed·system metadata도 보존합니다.
3. 기존 프로젝트 → Economy → NPC → Crisis → Military 순서, 날짜와 명령 지연, RNG 호출, 자원·피해·보고·기억 생성 규칙으로 계산합니다.
4. 일별 및 transaction 검사를 수행합니다. 기존 의미 검사는 전체 범위를 유지합니다. Runtime은 기존 NPC/Crisis bulk JSON 검사에서 변경 없는 subtree의 유효성만 재사용합니다. 매월 첫날과 verification 모드에서는 이 JSON 검사도 전체 수행합니다.
5. dirty component를 직렬화하고 변경된 `state_components` 행만 갱신합니다. 불변 body와 영구 기록은 기존 archive/catalog에 보존하고 현재 참조만 갱신합니다. Ledger까지 같은 transaction으로 commit합니다.
6. 실패하면 outer frame 안의 모든 일별 frame, 객체 필드, collection, RNG·seed·system metadata 및 SQLite 변경을 rollback합니다. active index와 persistence cache도 이전 상태의 결과를 잘못 재사용하지 않도록 무효화합니다.

독립 객체의 검사는 기존 full audit입니다. 변경된 JSON의 신규 key·scalar·subtree와 list는 검사하며, NaN·무한대·불가능한 JSON type·cycle은 거부합니다. receipt·자원 보존·참조·피해·명령·pending decision·보고·기억의 역참조 검사는 runtime에서도 생략하지 않습니다.

## 현재 상태와 과거 기록

`state_components`는 현재 World의 top-level component와 NPC/Crisis/Military bucket을 저장합니다. `current_state`는 경로·revision manifest이며, 이전 archive marker version과 새로운 component envelope를 구분합니다. `state_archive`, `archive_catalog`, `state_heads`의 논리적 역할과 불변 자료는 유지합니다. 이미 보관한 body는 mutation version과 내용 일치 검사를 통해 재사용합니다.

활성 operation, 미완료 cargo(이동·중단·대상/저장공간 대기 포함), 대기 command, 최신 command/convoy 참조는 derived index로 계산합니다. 완료 operation·이동·command·보고·receipt·trial을 삭제하거나 요약하지 않습니다. 부대별 날짜 maintenance/supply/readiness/attrition은 모두 기존과 같이 수행합니다. 원래의 manpower=0 예외, 평시 분기와 attrition_due의 주간 일정도 유지합니다. 원래 순서의 동률 처리와 plain World의 fallback도 유지합니다. index는 World·save·RNG에 포함되지 않습니다.

## 공개 조회와 저장 호환

`true_state` 등 공개 조회는 caller가 수정해도 Engine에 영향을 주지 않는 독립 사본입니다. 변경이 없는 committed graph에서 dataclass skeleton projection을 만들고 JSON의 C 구현으로 사본을 만듭니다. private 변경 또는 DB 변경을 발견하면 DB에서 다시 복원합니다. Engine transaction 중 공개 조회는 원래 committed view를 반환합니다. 다른 connection과 같은 connection의 직접 SQLite 수정도 검사합니다. stale Engine은 `refresh()` 또는 재open으로 DB에 재동기화할 수 있습니다.

명시적 save/checkpoint/export는 full logical snapshot을 생성하고 full audit을 수행합니다. 기존 checksum·load·pending decision·fog-of-war 의미는 유지합니다. 논리 schema는 Military save 6 / World 5 / Military state 1 / Economy 3 / NPC 1 / Crisis 1이며 save 1~5의 기존 호환 규칙을 유지합니다. 내부 component 저장 형식은 게임 save schema를 바꾸지 않습니다.

## 비용이 남는 경로

공개 World 사본, 전체 의미 검사, 현재 archive 참조 조립, 경제의 제한된 후보 사본, full JSON save/load는 기록 크기의 영향을 받습니다. 좁은 transaction은 모든 copy 비용을 제거하지 않습니다. 큰 export의 메모리는 실제 플레이의 steady-state RSS와 따로 보고합니다.

모드 사용법은 [MILITARY_PERFORMANCE_GUIDE.md](MILITARY_PERFORMANCE_GUIDE.md), 최종 계측과 검증은 [MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md](MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md)를 참조합니다.
