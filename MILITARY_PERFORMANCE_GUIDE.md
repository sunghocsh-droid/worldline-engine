# Military v1 이후 실제 플레이 성능 구조

군사·경제·NPC·Crisis 규칙과 논리 저장 schema는 그대로입니다. Military save 6 / World 5 / Military state 1, Economy 3 / NPC 1 / Crisis 1을 유지하며 save 1~5도 기존 규칙으로 불러옵니다.

## 검증 모드

`Engine.create`, `Engine.load`, `Engine`의 기본 `validation_mode`는 `runtime`입니다.

```python
game = Engine('game.sqlite', validation_mode='runtime')
verified = Engine('verification.sqlite', validation_mode='verification')
```

두 모드는 같은 시스템·일정·명령·RNG를 사용합니다. 검증 모드는 저장 파일의 게임 규칙이 아니며, 언제든 `game.validation_mode`로 변경할 수 있습니다.

- 매일/명령 transaction: 기존 의미 검사 전체를 유지합니다. 자원·인구·피해·참조·receipt·명령·보고·기억·정보 경계 검사는 변경 없는 행의 역참조까지 검사합니다. sparse semantic 검사로 바꾸지 않았습니다.
- runtime: 기존 NPC/Crisis bulk JSON 검사에서 journal이 증명하는 동일 identity의 변경 없는 subtree는 이미 통과한 유효성 검사를 재사용합니다. 변경된 scalar/키, 신규·교체된 subtree는 검사하며, 변경된 list는 전체 JSON 검사를 유지합니다.
- 매월 첫날과 verification의 매일: 기존 NPC/Crisis bulk JSON 검사를 전체 수행하고 기존 Auditor를 유지합니다. 매일 full-world serialized snapshot을 만들지는 않습니다. save/load는 full snapshot 검사를 수행합니다. 알 수 없는/독립된 JSON root도 전체 검사합니다.
- 개발용 `military_benchmark`는 기본 `verification`입니다. `--validation-mode runtime`으로 실제 플레이 경로를 측정할 수 있습니다.

## Transaction과 저장

Engine 소유 객체에만 mutation journal을 붙입니다. 날짜 진행과 명령은 변경된 container/record의 이전 값, RNG·seed·system metadata를 보존합니다. 실패하면 모든 일별 frame을 포함한 outer transaction을 되돌리고 SQLite 변경도 rollback합니다. callback의 외부 파일·네트워크·별도 내부 카운터는 원래부터 Engine rollback 범위가 아닙니다.

SQLite는 계속 source of truth입니다. 새 내부 `state_components` 테이블에는 현재 component를 저장하고, 변경된 component만 씁니다. `current_state`는 component 경로와 revision manifest를 저장합니다. 기존 `state_archive`, `archive_catalog`, `state_heads`는 불변 body·영구 기록·현재 참조 역할을 유지합니다. archive/catalog/Ledger는 같은 SQLite transaction으로 commit합니다. 다른 writer나 같은 connection의 직접 수정은 캐시를 무효화하며, stale Engine은 `refresh()` 또는 재open으로 DB에 재동기화합니다.

`true_state` 등 공개 조회는 독립된 사본을 반환합니다. private `_world` 변경을 공개 committed 상태로 노출하지 않습니다. 과거 기록과 현재 상태를 저장에서 삭제하거나 요약하지 않습니다. full JSON snapshot은 명시적 save/checkpoint/export 시 만들어 이전의 논리 내용·checksum·schema를 유지합니다. `autosave_dir`를 요청하면 이 전체 export 비용도 발생합니다.

## 군사 active/cold 구분

활성 작전·미완료 cargo(이동·중단·대상/저장공간 대기 포함)·대기 명령·최신 명령/항해 참조는 derived index를 사용합니다. 이 index는 save/World/RNG에 포함되지 않으며 rollback·새 객체·change-log overflow 시 재구성합니다. 동률은 원래 dictionary 입력 순서를 유지합니다.

완료 작전·명령·이동·보고·receipt·혁신 시험은 그대로 보관합니다. 모든 부대의 날짜별 maintenance/supply/readiness/attrition은 유지합니다. 원래의 manpower=0 예외, 평시 분기와 attrition_due의 주간 일정도 유지합니다. resource consumption을 생략하는 평시 fast path는 만들지 않았습니다. 의미 검사와 persistence의 참조 목록에는 여전히 역사 크기에 따른 비용이 남습니다.

실제 측정·결과 동일성·잔여 병목은 [MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md](MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md)에 기록합니다. 기존 Military v1 완료 자료는 로컬 체크포인트에 보존되어 있습니다.
