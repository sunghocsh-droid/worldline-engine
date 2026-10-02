# Military & Logistics Engine v1 — 요구사항과 검증 근거

이 문서는 요청한 군사 기능이 어느 계산 경로와 자동 검사에 연결되어 있는지 대조한다. **구현이 있다는 것, 개별 기능을 검사했다는 것, 실제 세계 전체를 수년간 실행했다는 것은 서로 다르다.** 아래 표는 이 차이를 구분한다. 최종 40개 소스를 고정한 실제 검사에서 기존 751개, 새 기능 366개, 실제 캠페인 19개를 합한 **1,136개가 모두 통과했고 실패·오류·건너뛰기는 0개**였다. 세 pytest 실행의 XML과 시작·종료 소스 및 테스트 파일 해시를 확인했다. 실험 수치와 상세 한계는 `MILITARY_LOGISTICS_REPORT.md`에 기록한다.

## 검사 범위 읽는 법

- **SQLite 통합 검사**: 실제 Engine의 저장·회계·원래 시스템 순서와 전체 Auditor를 사용한다. 변경을 임의로 허용하는 모의 저장소가 아니다.
- **기능 검사**: 실제 군사 모듈의 함수와 유한한 가상 자산을 사용한다. 특정 기능을 분리하여 비교하므로 전체 월별 경제·정치 실행을 모두 포함하지 않을 수 있다.
- **구성 경로 검사**: 요구사항을 이루는 소비·손실·이동·정보 경로를 검사한다. 해당 문장의 모든 조건을 결합한 별도의 장기 비교 실험을 의미하지 않는다.
- **유료 사업 실험**: 실제 Project Pipeline을 날짜별로 진행하고 돈·원료·시험·보고·기억을 검사한다. 전체 SQLite 세계의 군사 유지비·경제 결산은 제외한다.

검사 이름은 개발자가 근거를 다시 찾을 수 있도록 남겼다. 파일 약칭은 다음과 같다.

| 약칭 | 실제 검사 파일 |
|---|---|
| 통합 | `tests/test_military_integration.py` |
| 병참 | `tests/test_military_logistics.py` |
| 작전 | `tests/test_military_operations.py` |
| 교리 | `tests/test_military_command_doctrine.py` |
| 혁신 | `tests/test_military_innovation.py` |
| 보존 | `tests/test_military_physical_receipts.py` |
| 캠페인 | `tests/test_military_benchmark.py` |
| 보급 비교 | `tests/test_military_supply_comparisons.py` |

## 요청 35절 A–AE 대조

| 항목 | 실제 구현·검사 근거 | 검증 범위와 남은 경계 |
|---|---|---|
| **A. 좋은 보급과 나쁜 보급의 장기 수행능력 차이** | 보급 비교 `test_two_months_actual_rations_change_survival_readiness_and_operation_capacity`는 같은 seed·부대·명령에서 실제 식량만 다르게 두고 60일 소비·손실·준비율·실효 전투력·식량 보존을 비교한다. 통합 `test_firearms_and_artillery_cannot_fire_without_ammunition`도 실제 탄약의 효과를 확인한다. | 60일 집중 비교와 별도의 3년 캠페인이다. 이 60일 비교는 월별 경제·SQLite를 생략한다. 보급만 바꾼 수년간 전체 세계 쌍대 캠페인을 실행했다고 주장하지 않는다. |
| **B. 큰 병력도 병참 붕괴 시 공세가 어려움** | 보급 비교 `test_larger_force_cannot_cross_same_finite_route_as_fast_as_small_force`는 실제 전역으로 100명·1000명을 나누어 같은 노선을 진행한다. 소부대의 실제 도착과 대부대의 지연, 공유 유한 처리량 영수증을 비교한다. 실제 식량 수요와 실효 전투력도 인원·보급에 연결된다. | 부대 규모별 물리적 이동 제약과 A의 보급·능력 경로를 직접 검사한다. 규모별 전쟁 전체의 승패 통계를 역산하여 보정하지 않았다. |
| **C. 원정군의 해상 보급로 차단** | 보급 비교 `test_actual_naval_blockade_delays_cargo_and_starves_the_land_front`는 실제 전진 배치·함대 항해·명령 수신 후 봉쇄한다. 동일 화물의 도착 지연→육상 식량 소진→보급률·준비율·전투력 감소를 비교한다. 병참 경로 회복·실제 수송선 손실 및 해상 원정 캠페인도 연결된다. | 집중 비교는 실제 모듈을 사용하며 월별 경제·SQLite는 제외한다. 별도의 3년 원정은 전체 결산을 포함하지만 차단 유무만 바꾼 장기 쌍대시험은 아니다. |
| **D. 항만 처리량 병목** | 실제 출발·도착 항만 능력이 적재·하역과 해상 연결 용량을 제한한다. 병참 `test_ports_are_actual_loading_and_unloading_bottlenecks`. | 기능 검사. 항만이 있다는 이름만으로 무한 하역을 제공하지 않는다. |
| **E. 전근대 군대에 존재하지 않는 연료를 요구하지 않음** | 실제 차량·기계 장비가 있어야 연료 수요와 차량 속도를 적용한다. 병참 `test_fuel_and_motorized_label_cannot_create_nonexistent_vehicles`; 전근대 캠페인의 실제 초기 장비 검사. | 장비별 수요 경로 검사. 도보·범선에 현대식 연료를 자동 생성하거나 소비시키지 않는다. |
| **F. 차량군의 연료·부품 부족** | 차량 좌석·연료가 부족하면 도보 수준으로 제한한다. 규격별 부품이 맞아야 소비할 수 있다. 병참 `test_enough_trucks_without_fuel_fall_back_to_walking_not_motor_speed`, `test_parts_require_declared_family_not_generic_or_other_parts`. | 기능 검사. 차량·부품은 추상 수량이며 개별 엔진 고장률 모델은 아니다. |
| **G. 동원의 민간 노동·경제 비용** | 국가 부대는 출신 지역의 실제 노동 풀에서 인원을 가져온다. 통합 `test_new_mobilization_reduces_civilian_jobs_and_no_free_equipment`. 경제·GDP는 줄어든 민간 노동을 기존 Economy v3가 계산한다. | 노동 감소는 SQLite 통합 검사다. 특정 동원량에 대한 GDP 감소율을 직접 지정하거나 별도 목표값에 맞추지 않았다. |
| **H. 군사 손실의 인구·경제·Crisis 반영은 한 번만** | 실제 피해 영수증을 한 번 적용하고 사망을 기존 인구 결산으로 넘긴다. 통합 `test_fatal_loss_is_not_demobilized_and_is_applied_once`, `test_capture_is_unavailable_and_deserters_return_through_pipeline`. | 실제 SQLite에서 즉시 인력 차감·월별 사망·재적용 불변성을 검사한다. 포로·실종을 사망으로 자동 처리하지 않는다. |
| **I. 훈련·장비 없는 자동 보충 금지** | 훈련 집단은 별도 인원·예산·식량·무기를 사용하고 실제 도착 후 편입된다. 통합 `test_replacements_need_training_real_equipment_and_actual_arrival`, 작전 `test_training_cohort_cannot_be_allocated_to_an_operation_at_the_same_time`. | SQLite 통합 및 기능 검사. 손실 다음 날 병력 수를 복원하는 경로가 없다. |
| **J. 강행군의 속도와 피로·손실 비용** | 실제 육상 이동만 최대 1.35배로 빠르게 진행하고 거리 비례 피로·결속 비용·유한 추가 식량을 적용한다. 작전 `test_forced_march_is_faster_but_costs_finite_food_fatigue_and_cohesion`, `test_blocked_march_has_no_phantom_fatigue_or_extra_food`, `test_forced_foot_order_does_not_accelerate_ships_or_embarked_troops`. | 속도·재고·피로·결속은 직접 기능 검사다. 추가 피로는 기존 주간 행군·노출 손실률에 연결되지만 작은 집단에서 추가 사망이 반드시 발생하도록 강제하지 않는다. 보존 `test_completed_route_does_not_keep_killing_stationary_siege_troops`는 도착 뒤 행군 사망 반복을 막는다. |
| **K. 명령 지연과 작전 시작** | 발령·전달·수신·실행을 나눈다. 작전 `test_real_march_has_delay_distance_shared_capacity_and_receipts`, `test_planning_and_unsent_orders_do_not_move_or_complete`. | 기능 검사. 수신 전 이동·완성·명령 교체가 일어나지 않는다. |
| **L. 잘못된 적 정보에 따른 오판** | 현지 판단은 수신한 보고만 읽는다. 작전 `test_local_misinterpretation_can_produce_limited_trigger_but_not_national_war`, `test_unreceived_hidden_enemy_information_cannot_trigger_commander_decision`, `test_new_report_correction_preserves_belief_when_old_report_arrives_later`. | 정보·판단·위임 범위 검사. 오판이 반드시 전투 패배로 이어진다는 고정 규칙은 없다. |
| **M. 임무형 지휘의 통신 지연 완화** | 실제 훈련·참모·도입·자율성·처벌 문화가 지연에 따른 조정 손실을 일부 줄인다. 교리 `test_trained_mission_command_mitigates_delayed_coordination`, `test_mission_command_does_not_teleport_unreceived_orders`. | 전투 능력의 조정 계수와 실제 명령 대기를 검사한다. 받은 적 없는 명령을 아는 효과는 없다. |
| **N. 훈련·조직문화 없는 교리의 즉시 효과 금지** | 선언된 교리와 실효 숙련을 구분하고 유료 교육 기간을 둔다. 교리 `test_declaring_a_doctrine_without_training_cannot_instantly_use_it`, 혁신 `test_mission_command_actual_transition_culture_changes_training_speed_without_instant_effect`. | 기능 및 유료 사업 검사. 새로 들어온 병력의 공짜 교육도 `test_people_joining_mid_training_do_not_acquire_the_paid_cohorts_innovation`으로 막는다. |
| **O. 시대를 앞선 제안의 연도별 금지 없음** | 날짜 대신 실제 기술·조직·설비 조건을 확인한다. 혁신 `test_early_capable_state_and_late_incapable_state_follow_capabilities_not_year`. | 기능 검사. 이름이나 연도 때문에 자동 성공·실패시키지 않는다. |
| **P. 기반 없는 첨단 장비의 실패·개발 제약** | 엔진·변속기·궤도·연료·정밀 가공 등의 실제 격차를 반환한다. 혁신 `test_armour_concept_without_engine_transmission_fuel_is_not_usable`, 통합 `test_armored_production_needs_real_capabilities_not_calendar`. | 기능 및 SQLite 통합 검사. 미구현 기술을 장기간 기다리는 것만으로 자동 발명하지 않는다. |
| **Q. 실행 가능한 조직 혁신의 조기 시험** | 물리 장비와 조직 절차의 요구를 나누고 검토→시험→평가를 유료 사업으로 수행한다. 혁신 `test_five_concepts_have_actual_requirement_and_trial_records`, `test_common_clock_and_real_budget_resources_are_used`. | 유료 사업 검사. 초기에 시험할 수 있다는 뜻이며 전군 즉시 도입을 의미하지 않는다. |
| **R. 성공한 시험의 NPC 지지·도입 연결** | 실제 측정 결과를 전달받은 관찰자가 기억하고 기관 의견·후속 단계 기간에 반영한다. 혁신 `test_coordinator_publishes_actual_success_or_failure_only_to_delivered_observers`, `test_observed_ruler_and_faction_support_affect_real_paid_stage_duration`; 5종 실험의 성공 기억 사례. | 실제 시험·보고·기억·유료 도입 경로 검사. 제안 자체에 초자연적 신뢰 보너스를 주지 않는다. |
| **S. 실패한 시험의 저항·평가 변화** | 보고된 실패와 해석을 따로 저장하고 다음 제안의 의견에 반영한다. 혁신 `test_explicitly_delivered_failed_trial_can_have_different_institutional_lessons`, `test_five_funded_innovation_benchmarks_reproduce_actual_evidence_and_finite_costs`. | 유료 사업·기억 검사. 실패가 다음 아이디어를 정치적으로 자동 취소하지 않으며, 상세한 전국 제도 갈등은 아직 없다. |
| **T. 군주 평가·정통성·신뢰·파벌에 따른 도입 속도** | 관측된 통치 기록의 관계 평가와 기관·파벌 의견이 실제 유료 단계 기간에 영향을 준다. 혁신 `test_observed_ruler_and_faction_support_affect_real_paid_stage_duration`, `test_unobserved_ruler_achievements_cannot_secretly_accelerate_institutional_adoption`. | 관측된 평가의 도입 기간 반영 검사. 실제 성과를 몰래 읽지 않으며 반대도 강제 정치 취소 장치가 아니다. |
| **U. 규격 통일과 유지·보급 부담** | 탄약·부품 계열, 정밀도·호환성·규격별 교육/유지 부담을 구분한다. 혁신 `test_same_calibre_is_not_automatically_interchangeable_high_precision_parts`, `test_multiple_families_add_logistics_and_training_load_without_creating_supply`; 병참의 계열별 탄약·부품 검사. | 기능 검사. 같은 구경이나 명칭으로 재고를 다른 규격 물자로 바꾸지 않는다. |
| **V. 공성의 시간·보급·돌격/봉쇄 차이** | 실제 요새·수비 식량·공병/포병·고립·구원 조건과 경과 기간으로 판정한다. 작전 `test_unfinished_siege_investment_continues_with_unique_actual_results`, 병참 `test_arrived_siege_formation_receives_real_field_cargo`, 통합 `test_no_free_runtime_fortress_or_remote_combat`. | 실제 반복 공성·보급·유한 요새 검사. 성벽 구간별 공성전술과 개별 탄도는 계산하지 않는다. |
| **W. 해군의 수송 차단이 육상전선에 미치는 영향** | 보급 비교 `test_actual_naval_blockade_delays_cargo_and_starves_the_land_front`에서 실제 연결 처리량·운송 중 물자·육상 부대 보급률을 함께 확인한다. 작전 `test_only_present_supplied_ships_affect_blockaded_link_and_their_opponent`와 보존 `test_actual_sunk_transport_loses_cargo_once`도 실제 위치·피해를 검사한다. | 집중 비교 및 연결 검사. 차단 화물이 실제 운송 중 재고로 남으며, 한 번의 해전 결과를 모든 육상전선의 공통 패널티로 적용하지 않는다. |
| **X. 전술 승리와 목표 실패 분리** | 전술 승리 뒤에도 보급·도달 범위·목표 달성률을 따로 계산한다. 통합 `test_tactical_success_does_not_guarantee_objective`. | SQLite에서 준비한 실제 상태를 사용하는 판정 검사. 승리가 통상권·정권 굴복을 자동 성취시키지 않는다. |
| **Y. 전술 후퇴와 전략적 지연 성공 분리** | 실제 접촉·경과 시간·필요 지연일로 방어 목표를 판정한다. 작전 `test_defensive_delay_can_meet_aim_despite_tactical_setback`. | 실제 작전 시계를 사용하는 기능 검사. 피해와 전술 후퇴가 있는 상태에서도 지연 목표를 달성할 수 있다. |
| **Z. 같은 seed·상태·명령의 동일 결과** | 저장된 군사 전용 stream과 실제 실행을 비교한다. 통합 `test_same_seed_same_initial_conditions_and_orders`, 혁신 `test_trial_seed_and_saved_named_stream_are_reproducible`, `test_unrelated_trial_does_not_shift_this_innovation_stream`. | SQLite 세계·RNG·Ledger와 기능별 stream을 검사한다. 모든 seed에서 같은 역사 결과를 기대하지 않는다. |
| **AA. 저장·복원 뒤 군사 상태와 다음 실행 동일** | 통합 `test_save_load_military_streams_reports_memories_and_next_tick`; 작전 `test_save_restore_continues_same_journey_reports_commands_and_rng`; 병참 `test_world_save_load_preserves_transit_progress_and_next_delivery`; 혁신 `test_paid_cohort_and_stage_window_survive_save_load_exactly`; 캠페인 `test_actual_sqlite_save_load_reproduces_world_rng_ledger_and_pending_cohorts`. | 실제 SQLite save/load와 함수별 미완료 상태를 함께 검사한다. 세계 값뿐 아니라 RNG·Ledger·다음 실행까지 비교한다. |
| **AB. armed_clash/state_of_war와 Crisis 연결** | 두 사건 유형이 실제 전구·사건 식별자로 연결된다. 통합 `test_crisis_war_links_without_automatic_offensive`는 전쟁 사건 연결과 공격 명령이 자동 생성되지 않음을 검사한다. | `state_of_war` 직접 통합 검사이며 `armed_clash`는 같은 연결 분기를 사용한다. 국경 긴장만으로 군사 모듈이 선전포고하지 않는다. |
| **AC. 결과의 NPC 기억·Crisis 보고 연결** | 실제 군사 결과가 기존 Crisis 사건·Ledger·관찰자별 지연 보고로 전달된다. 통합 `test_save_load_military_streams_reports_memories_and_next_tick`, 혁신 `test_coordinator_publishes_actual_success_or_failure_only_to_delivered_observers`, 작전 `test_report_is_unknown_before_receipt_and_stays_old_news_after_delivery`. | 통합과 기능 검사. 보고받지 않은 NPC에게 적의 실제 내부 수치를 방송하지 않는다. |
| **AD. 일반 비국가 행위자의 부대·제한 작전** | 행위자 소유·위임·유한 인력·재고를 사용한다. 작전 `test_nonstate_actor_uses_finite_manpower_and_actual_operational_assets`, `test_country_linked_company_uses_real_government_ruler_authority`. | 기능 검사. 회사·용병·지역세력도 편제를 가질 수 있으나 권한 없는 개인이 국가 정책을 집행하지 않는다. |
| **AE. 국가 GDP/정부가 없는 행위자도 사용 가능** | 국가 연결 없는 행위자는 명시된 유한 인력·돈·재고만 사용한다. 통합 `test_nonstate_actor_has_finite_men_without_fake_national_economy`, 혁신 `test_nonstate_funded_resources_do_not_claim_unimplemented_project_execution`. | SQLite 통합 검사. 가짜 GDP·내각을 만들지 않는다. 국가 연결 없는 유료 전국 혁신 사업의 조달 어댑터는 미구현이며 실행 가능하다고 거짓 반환하지 않는다. |

## 실제 3종 캠페인

`worldline/military_benchmark.py`는 다음 실험을 **각각 36개월** 실행하도록 정의한다. 가상 4개국·서로 다른 20명의 NPC, 기존 경제 profile, 실제 SQLite와 일별 전체 Auditor를 사용한다. 연구용 실행자는 정해진 정책 목표를 공개 Engine API로 주문하고 실제 제약에 걸리면 그 이유를 기록한다. 이는 새로운 자율 군사 AI나 실제 역사 사건 스크립트가 아니다.

| 실험 | 초기 profile·작전 | 실제 결산에 포함되는 내용 |
|---|---|---|
| `preindustrial_border` | 전근대 국경·실제 성곽 공성 | 국가 기본 병력 500명. 공격측은 초기 화기 보병 300명과 냉병기 기병 200명·실제 말·사료를 분리한다. 도로, 별도 승무원 30명·운송선 4척, 동맹 항만의 실제 전진 창고와 해상→육상 보급, 7일 수신 지연, 14일 공성 준비·유한 성곽 식량, 실제 손실·유료 보충을 계산한다. |
| `industrial_front` | 공업국 전선·철도·성곽 공성 | 국가 기본 병력 1,000명·실제 포 10문과 화기. 실제 `rb↔rc` 철도 링크와 철도 기반시설, 유한 성곽, 1일 명령 수신 지연, 14일 공성 준비, 초기 포 1문당 일 0.05의 명시적 훈련·유지 탄약 수요와 전투 탄약, 실제 재고·비용을 사용하는 보급·보충·참모 혁신을 사용한다. 이 탄약 수요는 실험 입력이며 엔진 계수를 바꾸지 않는다. |
| `expeditionary_sea` | 초기 공업국 해상 원정·상륙·해상 경쟁 | 국가 기본 병력 600명. 별도 인구에서 실제 승무원을 차출한 상륙 함대 8척과 서로 다른 운송선 4척씩의 두 convoy, 실제 항만·전진 창고를 둔다. 실제 적 함대의 수신된 차단 명령, 동시 위치의 살아 있는 함대 간 해전, 본국→전진 창고→상륙군/함대 보급, 실제 귀항과 수신 후 해상 보충 이동을 명령한다. 선박·좌석·식량·현재 운송 배정이 부족하면 실행 실패나 대기 이유를 그대로 남긴다. |

초기 병력과 승무원은 경제 초기화 **전에** 지역 군 노동에 반영한다. 부대 휴대 물자, 성곽의 별도 30일 식량, 본국 창고용 유한 buffer는 서로 다른 초기 자산이며 `military_scenario_parameters` Ledger에 각각 기록한다. 창고의 초기 재고는 실제 행위자 재고에서 한 번만 옮긴다. 성곽 식량을 부대 식량에도 중복 주입하지 않는다. 이후 사람·장비·식량은 무료로 복구하지 않으며 실제 조달·생산·훈련·수송 경로를 거친다.

보충 목표는 각 부대의 실제 초기 인원에서 손실된 만큼이다. 한 번에 초기 인원의 최대 5%를 유료 120일 과정으로 교육하고 별도 유료 90일 무기 생산과 실제 이동을 수행한다. 완성 시점만으로 기존 부대에 자동 편입하지 않는다. 원정의 경우 실제 같은 출발 항만에 자유로운 수송 함대가 없으면 본국에서 기다릴 수 있다. 새 기병 보충자가 말까지 자동으로 얻는 것도 아니다.

실행 전 실제 1개월 저장과 6개월 SQLite 탐침으로 전체 Auditor·예산·재고·수신·경로를 검사했다. 초기 탐침에서 철도에 도보 최소기간을 잘못 적용한 도착 어댑터와 두 convoy가 공유 창고의 이전 재고를 재사용한 연구 controller를 발견해 수정했다. 실패·중단 자료는 별도로 보존하며 현재 소스의 최종 실행으로 재표시하지 않는다. 정치적 판단 연결을 추가하기 전 39개 소스의 세 실험은 실제 6개월을 완료했고, 독립적인 읽기 전용 검사에서 World·RNG·Ledger·소스 해시와 전체 Auditor가 일치했다. 당시 원정 보충병은 실제 수송 함대와 함께 6월 3일 수신 후 6월 8일 전선에 도착했으며, 도착만으로 편입하지 않았다. 이 이전 자료는 별도 기준 자료로 보존한다.

정치적 판단 연결을 포함한 40개 소스를 고정한 뒤 세 기준 실험과 독립 복원 실험을 각각 초기 상태부터 실제 36개월 실행했다. 네 실행 모두 1900-01-01에 도달했고, 실행 시작·종료 시 소스 40개가 같았다. A/C는 실제 6개월 JSON 저장본을 남겼으며, 산업 기준 실험과 별도 복원 실험은 실제 18개월 save/load 규칙을 유지하고 6개월에는 읽기 전용 SQLite 백업만 추가했다. 백업은 게임의 상태·명령·난수를 바꾸지 않고 새 증거 파일에 기록했으며, 추가 입출력 비용도 실행 시간에 포함했다. 이전 39개 소스의 자료를 현재 40개 소스의 완료 근거로 바꾸지 않았다.

현재 소스의 실제 결과는 다음과 같다. 병력은 선원·지원병과 별도 보충 집단까지 포함하므로 초기 전투병에서 사망자만 뺀 수치와 다르다. 사상·포로·실종·탈영도 서로 구분하며, 전투 결과를 원하는 수치로 다시 맞추지 않았다.

| 실험 | 실제 완료일 | 작전 수 | 현재 군 인원 | NPC 기억 | 사건 | Ledger 기록 |
|---|---|---:|---:|---:|---:|---:|
| `preindustrial_border` | 1900-01-01 | 65 | 1,867 | 1,901 | 113 | 17,316 |
| `industrial_front` | 1900-01-01 | 41 | 3,443 | 2,481 | 205 | 15,342 |
| `expeditionary_sea` | 1900-01-01 | 110 | 2,067 | 2,361 | 109 | 19,261 |
| `industrial_restore` | 1900-01-01 | 41 | 3,443 | 2,481 | 205 | 15,342 |

산업 복원 실험은 초기 상태부터 독립 실행하여 18개월에 실제 JSON 저장 → 새 SQLite 복원 → 18개월 추가 진행을 수행했다. 기준 실험과 복원 실험의 최종 World·RNG·Ledger 해시는 모두 `c694317c63a456239d1a5f1f88a4e13825c177ce85b540e1c797167b8e07e92b`이다. 현재 소스의 네 실제 실행 자료를 읽기 전용으로 검사한 결과 원시 JSON과 SQLite의 World·RNG·Ledger, 이전 Ledger prefix, 실제 유료 교육·자원·인원 보존이 모두 일치했다. 모든 압축 이력 객체의 길이·SHA-256·canonical JSON과 catalog/head 참조 및 정치 판단의 metadata/body identity도 전수 통과했다. 근거는 `benchmarks/military/archive_integrity.json`, `preflight_six_months.json`, `independent_qa.json`에 보존한다. 최종 pytest 결과는 실제 XML 집계와 `TEST_RESULTS.md`를 기준으로 확인한다.

캠페인은 국가별 인구·GDP·국고·부채·연체·신용·군 노동·무역과 군사 보급률·원인별 손실·조달비·보충 집단·사건·기억을 기록한다. `test_actual_three_year_campaigns_keep_consistent_cohorts_finance_and_history`는 36개월 결과와 현재 소스 해시, 실제 조달·손실·Ledger 증가, 최종 세계의 전체 Auditor를 검사한다.

과거 결과 파일은 **현재 소스 해시가 모두 같고 실제 36개월 실행한 경우에만** 재사용한다. 자료가 없거나 소스가 다르면 현재 소스로 다시 실행한다. 저장 재현성은 실제 중간 JSON 저장 → 새 SQLite 복원 → 계속 진행을 사용하고 세계·RNG·Ledger를 각각 비교한다. 짧은 save/load 회귀 검사와 최종 보고서의 장기 재실행 근거를 구분한다.

이 실험은 3년간 자산·인력·회계와 인과관계가 깨지지 않는지 확인한다. 모든 전쟁 전략이 합리적이라는 증명이나 1592년 실제 국가의 검증 자료는 아니다.

## 실제 5종 군사 혁신 실험

`worldline/military_innovation_benchmark.py`의 5종 사례는 **유료 사업 구간을 분리한 실험**이다. 기존 공통 Project Pipeline을 매일 진행하고 초기의 유한 국고·원료에서 실제 비용을 차감한다. 같은 seed로 두 번 실행하여 시험·회계·보고·기억 결과를 비교한다.

| 사례 | 실제로 비교·기록하는 것 |
|---|---|
| `alternate_volley_weak_organization_repeated_trials` | 훈련·조직 능력이 약한 부대의 교대 일제사격 개념을 실제로 3회 유료 시험한다. 돈과 반복만으로 부족한 훈련을 자동 보완하지 않는다. |
| `mission_command_low_and_high_culture` | 같은 기술·seed에서 자율성이 낮고 높은 조직을 비교한다. 실제 검토·시험·교리·훈련·도입·확산을 거치며 기존 문화가 사라지지 않는다. 별도의 교리 검사는 실제 조정 계수와 명령 지연도 확인한다. |
| `armoured_concept_weak_intermediate_strong_foundations` | 전근대·초기 공업·공업 기반의 엔진·변속기·궤도·연료·정밀 능력 차이를 시험한다. 특정 연도가 성공을 정하지 않는다. |
| `measured_trial_memory` | 실제 성공 시험의 보고를 받은 관찰자의 기억·기관 의견·군주 관계 평가를 기록한다. 실패나 난수 결과를 성공으로 덮어쓰지 않는다. |
| `failed_trial_memory_and_next_proposal` | 실제 실패의 보고·기관 의견과 다음 제안의 검토 근거를 기록한다. 다음 아이디어는 유료 실행 가능성 검토를 진행할 수 있고, 실패 기억이 자동 금지 장치가 되지 않는다. |

이 사례들은 실제 자금·원료·날짜·시험·보고·기억을 사용하지만 **전체 SQLite 실행, 월별 경제 결산, 군사 일일 유지비·전투, 자율 내각 일정은 포함하지 않는다.** 그러므로 이 사례의 실행 시간을 전체 세계 속도로 해석하거나 여기서 남은 국고를 장기 전쟁의 지속가능성으로 해석하면 안 된다. `test_five_funded_innovation_benchmarks_reproduce_actual_evidence_and_finite_costs`가 이 범위와 재현성·유한 비용·관찰하지 못한 외국 인물의 기억 부재를 직접 검사한다.

기본 개념 예시는 참모 계획, 분산 보병, 표준 포병, 제병 협동, 기동 장갑군의 다섯 가지다. 이 기본 데이터의 요구·시험 존재 검사와 위 다섯 가지 비교 실험은 목적이 다르다.

## 함께 검사하는 보존 조건

- 실제 자원이 없는 요새·무기·선박·인력을 런타임 호출로 만들지 않는다. 시나리오가 명시한 초기 자산과 실제 생산 사업을 구분한다.
- 같은 사람이 지원병·승무원·육상 전투병·교육집단에 중복 배정되지 않는다. 같은 선박을 호송과 독립 봉쇄에 동시에 배정하지 않는다.
- 이동 완료 증거는 저장하지만 도착한 부대의 보급·현지 교육·장비 이전을 계속 막지 않는다. 도착 이후 행군 손실을 반복하지 않는다.
- 실제 생산·조달·소비·화물·손실 영수증은 보존하며 같은 식별자를 재적용해 피해나 물자를 두 번 만들지 않는다.
- 실제 판정기만 적의 물리 상태를 읽는다. 지휘관의 결정에는 수신한 정보만 사용하며 최신 정정 뒤 도착한 옛 보고·명령이 판단을 되돌리지 않는다.
- 저장 규칙은 save schema **6**, World schema **5**, Military state **1**이다. 기존 save **1~5**는 불러오되 군사를 자동 활성화하지 않는다. 활성화 시 seed를 한 번만 파생하고 시스템 순서 **Projects → Economy → NPC → Crisis → Military**를 검증한다.

## 아직 입증하지 않은 것과 남겨 둔 기능

개별 기능·3년 캠페인·유료 연구 실험이 존재한다고 모든 요구사항에 장기간의 쌍대 비교가 존재하는 것은 아니다. 위 표의 구성 경로 항목은 이러한 한계를 표시한다. 고보급/저보급이나 차단/비차단 원정의 규모별 장기 민감도 통계는 후속 군사 캘리브레이션의 대상이다.

추가 보급 비교 3개는 실제 실행에서 **3개 통과, 실패 0개**를 확인했다. 60일 보급 비교·유한 수송망의 부대 규모 비교·실제 해상 봉쇄 뒤 육상 보급 악화는 위 표에 명시한 집중 실험이며, 별도 세계 전체의 장기 결과나 최종 전체 테스트 개수를 대신하지 않는다.

현재 기능에는 최적 전쟁 계획 AI, 전국 자동 군수 생산·장비 교체 AI, 정교한 전술·개별 함선 HP·탄도, 상세 부상자 회복 집단, 포로 자동 송환, 점령 주민의 정치·행정·조세 통합, 평화조약 영토 배분이 없다. 작전 판단·지역 통제·정치 결과 hook과 실제 유한 자원 경계가 있을 뿐이며 이 범위를 완전한 점령·외교 AI로 부르지 않는다.

가상 편제·자원·수송은 게임의 추상 단위다. 실제 1592년 인물·국가·지도·군수 통계를 대량 입력하지 않았다. 군사의 기술·경제 제약과 재현성을 확인하는 로컬 엔진이며 UI·MCP·외부 API를 추가하지 않았다.
