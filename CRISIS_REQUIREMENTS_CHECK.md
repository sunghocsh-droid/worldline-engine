# Crisis & Event Engine v1 요구사항 검증 연결표

이 문서는 현재 코드와 자동 테스트를 읽고 작성한 구조 점검표다. 요구사항을 어떤 계산과 테스트가 확인하는지 연결하며, 테스트의 존재만으로 모든 역사적 상황에 대한 정확성을 주장하지 않는다. 코드 줄 단위 coverage 비율을 측정한 문서도 아니다.

이 점검표 작성 자체는 실행 코드·계수·기존 테스트를 변경하지 않았다. 최종 소스로 30년 연속 실행과 15년째 저장·복원한 별도 30년 실행을 실제 완료했으며, 최종 세계·난수·기억·월별 결과·시스템 기록 순서가 일치했다. 기존 413개와 새 검사 229개를 합한 pytest 642개가 모두 통과했고 실패·오류·건너뜀은 0개다. 최종 실행 결과는 `CRISIS_EVENT_REPORT.md`, `TEST_RESULTS.md`, `benchmarks/crisis/`의 실제 산출물과 함께 확인해야 한다.

## 근거 파일 표기

| 표기 | 테스트 파일 | 주로 확인하는 것 |
|---|---|---|
| R | [test_crisis_risk.py](tests/test_crisis_risk.py) | 실제 상태에서 얻은 pressure와 연속적 발생 확률 |
| C | [test_crisis_core.py](tests/test_crisis_core.py) | 사건 생성·진행·확대·종료와 경제 결산 연결 |
| P | [test_crisis_response.py](tests/test_crisis_response.py) | 정보 전달·NPC 판단·권한·조달·플레이어 결정 |
| S | [test_crisis_state_effects.py](tests/test_crisis_state_effects.py) | 저장 자료·인과관계·결과 대기열·중복 피해 방지 |
| I | [test_crisis_integration.py](tests/test_crisis_integration.py) | 실제 SQLite 거래·Ledger·저장 호환·복원 |
| B | [test_crisis_benchmark.py](tests/test_crisis_benchmark.py) | 가상 세계·장기 재현·극단조건·집계 검증 |

표의 `R: test_...`는 해당 파일의 실제 테스트 함수 이름이다. 하나의 함수가 여러 입력으로 실행되는 경우도 있으므로 표의 행 수는 pytest 테스트 개수와 다르다.

최종 통신 감사에서 발견한 수신 순서와 정보 신선도 문제는 P의 다음 회귀 검사로 확인한다. Q·S·V와 완료 보고 11·12·15의 추가 근거다.

- `test_delayed_observation_stays_stale_while_memory_begins_on_receipt`: 늦게 온 보고의 관측 기준일과 실제 수신일, 기억 시작일을 구분한다.
- `test_message_nine_precedes_ten_so_same_arrival_correction_is_latest`: 같은 날 도착하는 9번 원본과 10번 정정의 실제 생성 순서를 보존한다.
- `test_late_obsolete_report_preserves_newer_belief_and_memory_after_load`: 복원 뒤에도 옛 보고가 최신 판단 근거와 기억을 덮어쓰지 않는다.
- `test_late_obsolete_command_does_not_reverse_current_standing_order`: 늦게 도착한 옛 명령이 현행 명령을 되돌리지 않는다.
- `test_measured_evidence_keeps_measurement_date_separate_from_delivery`: 대응의 실제 측정 기준일과 보고 도착일을 분리한다.

위기의 확대·재발 경로에 대한 최종 추가 근거도 C에 있다.

- `test_funded_bilateral_mobilization_can_causally_escalate_an_armed_clash_to_war_reproducibly`: 실제 자금 지급과 양측 동원 후 저장된 난수 stream의 연속 확률로 무력충돌이 전쟁상태에 확대된다. 전쟁을 직접 입력하거나 난수를 강제로 고르지 않고 복원 경로도 비교한다.
- `test_resolved_child_advances_its_escalated_parents_expired_cooldown_without_erasing_pressure`: 실제 협상으로 종료된 자식 사건의 재발 방지 기간을 갱신한다.
- `test_new_resolution_advances_older_family_cooldown_and_cannot_shorten_it`: 과거 종료 이력이 새 종료의 재발 기간을 가로막거나 줄이지 않는다.
- `test_helper_specific_cooldown_duration_is_preserved_without_extending_on_review`: 기존 60·180일 종료 방식과 실제 종료일 기준 날짜를 유지하며 매일 기간을 연장하지 않는다.

## 사용자 A–Z 시나리오

| 항목 | 구현과 검증 근거 | 해석 범위·주의점 |
|---|---|---|
| A. 흉작이 자동 기근은 아님 | R: `test_actual_food_reserves_prevent_famine_without_changing_crop_event`; C: `test_bad_harvest_is_not_direct_famine_or_immediate_mass_death`; R: `test_paid_relief_is_a_real_signal_but_an_unfunded_policy_is_not` | 실제 식량 재고와 결산된 식량안보를 사용한다. 재고라는 표시만으로 식량을 만들지 않으며 수입을 이중 계산하지 않는다. 세밀한 식량 가격·가계별 구매력 모델은 없다. |
| B. 같은 흉작이 취약국에서 기근으로 확대 | R: `test_same_crop_failure_in_vulnerable_country_can_form_causal_famine` | 식량 부족과 기근을 부모·자식으로 연결할 수 있다. 낮은 식량안보 등 공급 실패가 근거이며 흉작 발생만으로 기근을 확정하지 않는다. |
| C. 홍수의 실제 자본·인프라 피해 | C: `test_flood_has_one_monthly_capital_and_infrastructure_damage_owner`; S: `test_food_inventory_damage_is_real_conserved_and_applied_once` | 기존 경제의 월별 자본파괴 처리가 자본·기반시설 피해를 한 번 소유한다. 식량 재고 피해도 실제 재고를 줄인다. |
| D. 같은 홍수라도 대응능력에 따라 피해 차이 | C: `test_same_flood_better_administration_and_infrastructure_reduces_planned_damage`; C: `test_funded_relief_reduces_delayed_mortality_without_undoing_past_damage` | 첫 검사는 피해 계획의 방향성을, 둘째는 실제 조달한 대응이 아직 발생하지 않은 손실을 줄이는 것을 확인한다. 이미 발생한 사망·파괴를 되돌리는 효과는 없다. |
| E. 전염병의 위생·의료 조건 차이 | C: `test_epidemic_damage_uses_existing_sanitation_and_health_not_future_medicine`; R: `test_local_existing_epidemic_creates_spread_pressure_and_sanitation_reduces_it` | 유입·확산 위험과 예상 피해의 근사 모델이다. 감염자·회복자별 역학이나 시대별 의료기술의 상세 자료는 구현하지 않았다. |
| F. 실제 미지급·default가 위기로 연결 | R: `test_authoritative_credit_default_and_arrears_create_financial_causes`; C: `test_actual_arrears_expand_fiscal_stress_and_recovery_can_end_it` | Economy v3의 신용 상태·미지급금·지급 실패를 읽는다. 은행 파산·주가·환율 같은 없는 시장을 계산하지 않는다. |
| G. 낮은 국경 긴장에서 대형위기로 직행하지 않음 | R: `test_low_border_pressure_never_directly_creates_war`; C: `test_pressure_alone_cannot_jump_to_war_or_invent_military_mobilization` | 낮은 긴장은 최초 사건 위험을 작게 만든다. pressure만으로 선전포고·무료 동원·전투 결과를 생성하지 않는다. |
| H. 고긴장·동원·오정보가 충돌·확대에 기여 | R: `test_high_tension_and_mobilization_make_an_incident_possible_not_automatic_war`; R: `test_bad_local_intelligence_is_an_explicit_incident_risk_input`; C: `test_authorized_ultimatum_creates_causal_child_not_unconnected_lottery` | 최초 사건과 단계별 확대의 근거를 별도로 검사한다. 고긴장이라고 전쟁을 확정하지 않는다. 장기 실험에서 각 경로가 얼마나 자주 발생하는지는 최종 집계를 확인해야 한다. |
| I. 중앙의 평화 의도와 다른 현지 행동 | P: `test_local_patrol_requires_owned_region_force_and_delegation`; P: `test_commands_are_delayed_separate_from_event_reports_and_create_no_force` | 전달된 중앙 명령과 현지의 오정보·위임 권한을 구분한다. 제한 순찰 행동은 후속 사건 trigger이며 실제 전투 판정은 아니다. |
| J. 권한 없는 현지인은 국가 동원·전쟁을 집행 못함 | P: `test_authority_cannot_be_inferred_from_an_opinion`; P: `test_local_patrol_requires_owned_region_force_and_delegation`; P: `test_both_border_officers_can_act_only_on_their_explicit_border_regions` | 의견·공포·위신이 권한을 만들어내지 않는다. 실제 위임 지역·가용 인력과 국가 차원의 집행 권한을 따로 확인한다. |
| K. 국경 사건의 중재·철수·완화 | P: `test_unilateral_mediation_does_not_force_counterpart_to_make_peace`; P: `test_mutual_mediation_can_end_a_crisis_with_residual_memories`; C: `test_mutual_ceasefire_ends_actual_war_status_without_territory_or_battle_result` | 일방 중재가 상대의 선택을 취소하지 않는다. 상호 완화는 종료로 이어질 수 있으며 불신·기억은 남는다. 영토 분배·평화조약 판정은 없다. |
| L. 시위가 협상으로 종료 가능 | P: `test_negotiation_and_repression_have_different_actual_effect_paths` | 협상·양보의 비용·pressure 감소·종료 경로를 확인한다. 모든 사회갈등이 실제로 합의에 이를 것을 보장하지 않는다. |
| M. 강경진압이 더 큰 불안·폭동의 원인이 될 수 있음 | P: `test_negotiation_and_repression_have_different_actual_effect_paths`; C: `test_coercion_is_a_risk_driver_not_a_guaranteed_riot` | 진압은 후속 확대 위험의 한 요인이다. 진압을 선택했다고 폭동을 즉시 확정하거나 현대적 도덕 판단으로 자동 행정 실패를 부여하지 않는다. |
| N. 자연 발생 자체를 통치자 책임으로 기록하지 않음 | P: `test_natural_occurrence_has_no_automatic_ruler_blame`; C: `test_natural_damage_without_any_response_never_gets_measured_ruler_blame` | 사건 발생 기록과 통치 대응 평가를 분리한다. |
| O. 실패한 대응이 평가 근거가 될 수 있음 | P: `test_response_execution_is_separate_evidence_delivered_later`; P: `test_measured_aftermath_can_correct_initial_response_capacity_evidence` | 실제 대응 기록이 전달된 뒤 평가한다. 최초 대응능력 추정과 이후 결과 근거를 분리한다. |
| P. 성공적인 대응이 긍정 평가 근거가 될 수 있음 | P: `test_response_execution_is_separate_evidence_delivered_later`; C: `test_measured_response_evidence_waits_for_real_population_account_and_delivery` | 계획된 사망 감소를 이미 실현한 성과처럼 기록하지 않는다. 실제 월별 손실과 계산상 예방 추정은 다른 값이다. |
| Q. 모르는 사건은 NPC 판단에 사용되지 않음 | P: `test_report_is_frozen_and_unknown_until_received`; P: `test_unknown_event_cannot_trigger_executive_action`; C: `test_unobserved_player_crisis_does_not_leak_through_pending_choices` | 실제 사건 표를 NPC 의사결정에 직접 주지 않는다. 중요한 플레이어 선택지도 보고가 도착하기 전에 정답을 누설하지 않는다. |
| R. 잘못된 보고가 잘못된 판단을 유발할 수 있음 | P: `test_false_information_and_correction_replace_memory_without_truth_broadcast`; P: `test_perceived_cost_does_not_change_when_hidden_truth_changes` | 같은 실제 위협이라도 보고에 따라 동원 대안의 평가점수가 달라진다. 객관적 비용과 인식 비용을 분리한다. |
| S. 정보 정정 뒤 판단 변화 가능 | P: `test_false_information_and_correction_replace_memory_without_truth_broadcast`; P: `test_corrected_enemy_report_changes_perceived_military_balance` | 정정 보고가 알려진 위협·전력과 대안 평가를 바꾼다. 정정이 언제나 정치적 입장 전환까지 강제하지는 않는다. |
| T. 직접 피해자와 먼 관료의 기억이 다름 | P: `test_direct_exposure_and_distant_news_create_different_memories` | 같은 사건에도 중요도·개인 영향·감정·해석이 다를 수 있다. 전체 인구의 정밀 정치심리는 없다. |
| U. 같은 사건의 중복 피해 방지 | S: `test_duplicate_submission_does_not_destroy_capital_twice`; S: `test_applied_receipt_cannot_be_reset_to_damage_again`; C: `test_one_occurrence_has_one_demographic_loss_owner` | 결과의 고유 키·적용 영수증과 월별 경제의 피해 소유자를 함께 검사한다. 전달 완료와 실제 결산 완료도 구분한다. |
| V. 저장·복원 후 active crisis/RNG/기억/미결 결정 동일 | I: `test_save_load_preserves_active_event_queues_memories_pending_and_next_result`; B: `test_crisis_thirty_year_same_seed_and_midpoint_save_reproduce_all_state` | 실제 SQLite 저장 복원 뒤 다음 결과·Ledger가 같은지 검사한다. 별도 실제 30년 두 실행의 전체 상태·시스템 기록 순서 비교도 통과했다. |
| W. 플레이어 최후통첩은 자동 결정하지 않음 | P: `test_player_ultimatum_remains_pending_and_timeout_is_explicit_once`; P: `test_player_can_answer_without_engine_selecting_political_option` | 대응 목록·기한·미대응 결과를 가진 pending decision을 보존한다. 보고 전에는 대기 결정을 공개하지 않는다. |
| X. 결정기한 무시 시 정의된 결과 적용 | P: `test_player_ultimatum_remains_pending_and_timeout_is_explicit_once` | 기한 경과를 한 번만 미대응으로 기록하고 그 대응의 pressure·후속 원인을 적용한다. 시간 전체를 정지해 플레이어를 기다리지 않는다. |
| Y. 사건이 없다고 평화 보너스를 제공하지 않음 | C: `test_no_event_grants_no_peace_bonus_or_free_resources` | 사건 계층이 GDP·국고·자원을 무료로 증가시키지 않는다. 기존 경제의 정상 생산과 회복은 별도 계산이다. |
| Z. 사건 수를 늘리기 위한 근거 없는 위기 금지 | R: `test_no_source_means_no_crisis_despite_centuries_of_clock`; R: `test_configured_disaster_can_occur_and_active_event_prevents_spam`; R: `test_cooldown_blocks_duplicate_event_but_not_its_pressure` | 무원인 fixture와 중복 방지를 확인한다. 재난 발생에는 명시적인 지역 위험률이 필요하며 cooldown으로 실제 위험을 삭제하지 않는다. |

## 여덟 극단조건

공통 구현은 `worldline/crisis_benchmark.py::run_stress_cases`다. `B: test_all_eight_synthetic_extremes_have_real_conditions_and_audited_paths`가 실제 초기 조건·경제 준비기간·부모/자식 연결을 확인하고, `B: test_extreme_conditions_keep_finite_resources_and_do_not_invent_battle_deaths`가 유한한 재정·인구와 가짜 전투 사망의 부재를 확인한다.

이 검증은 각 국가가 반드시 무너지거나 반드시 살아남는다는 목표값을 사용하지 않는다. 인위적인 초기 조건과 외부 충격을 넣은 뒤, 조건이 어떤 pressure와 사건 경로로 이어지는지 기록한다. 기본 실행은 준비 이후 3개월이며 일부 사례는 충격 전 경제상황을 실제로 형성하기 위한 추가 준비기간이 있다. 이것은 30년 세계 실험과 다른 검사다.

| 사용자 조건 | 실험 ID와 실제 입력 | 검증 범위·남은 한계 |
|---|---|---|
| 이미 default인 정부 + 전쟁위기 | `default_and_war`: 실제 부채·미지급금·과거 default, 고갈된 준비자산과 국경 사건·잘못된 보고·늦는 회피 명령 | 무한 비상재원이나 무료 동원을 허용하지 않는다. 전투 승패·배상·정복자원 수입은 생성하지 않는다. |
| 흉작 + 봉쇄 + 낮은 준비자산 | `harvest_blockade_low_reserves`: 농업 충격·실제 교역 차질·국고 고갈 | 실제 식량 재고와 공급 결산에 따라 확대 여부가 달라진다. 식량 가격·각 가구의 식량 접근은 근사 지표다. |
| 대규모 동원 + 노동불안 | `mobilization_and_labour`: 실제 지역 민간 노동에서 인력을 동원한 뒤 노동불안 pressure 부여 | 동일 노동의 민간·군사 중복 계산을 기존 노동 엔진이 차단한다. 세부 병참·전투는 없다. |
| 다민족 지역의 높은 불만 | `identity_grievance`: 가상 지역 소수집단 차별 사건·정체성 갈등·자치/분리 pressure | 집단·지역별 원인 연결점이다. 정교한 민족·종교 인구 구성, 내전·정권 교체 판정은 없다. |
| 강경 지휘관 둘 + 고긴장 국경 | `hardline_border_commanders`: 높은 국경 긴장, 높은 위험·위신 민감도, 과장된 위협 보고와 늦는 중앙 명령 | 실제 위임 지역의 제한 행동과 후속 원인을 검사한다. 높은 긴장만으로 전쟁을 강제하지 않는다. |
| 온건정부 둘 + 현지 우발충돌 | `moderate_governments_local_incident`: 현지 강경 조건을 유지하면서 중앙 성향·내각 의사결정·군부 영향력 변경 | 중앙과 현지가 다른 판단을 할 수 있다. 모든 온건정부가 항상 완화에 성공한다는 규칙은 없다. |
| 경제호황 중 자연재해 | `growth_and_flood`: 유한한 외부 민간투자와 실제 건설 지연 뒤 관측된 성장에 홍수 투입 | GDP를 직접 올려 호황을 연출하지 않는다. 성장·자본 증가를 먼저 검사한 뒤 재난 손실을 추적한다. |
| 장기 불황 중 작은 정치사건 | `recession_and_small_petition`: 실제 자본파괴 뒤 준비기간 동안 측정된 생산 감소, 그 손실에 연결된 작은 청원 | GDP 감소를 직접 지정하지 않는다. 작은 정치사건이 항상 대규모 봉기로 이어지는 것도 아니다. |

## 완료 보고 20항목의 근거

| 번호 | 보고할 내용 | 구현·검증·산출물 근거 |
|---|---|---|
| 1 | 자동 발생할 수 있는 사건 | `crisis_state.EVENT_TYPES`, `crisis_risk.detect`, `crisis.create_event`. 재난·흉작·전염병 유입·공급/재정/사회 위기·국경 사건을 지원한다. 일부 정체성·정치 사건은 외부 입력 또는 후속 단계의 연결점이다. 모든 enum이 독립 자동 발생기인 것은 아니다. |
| 2 | pressure가 사건으로 변하는 방식 | `crisis_risk.update_pressures`, `probability`, `detect`; R의 연속 hazard·원인·경과시간·17범주 검사. 실제 원인과 우발 계기를 분리한다. |
| 3 | 모든 위기가 전쟁으로 가지 않는 이유 | C/P의 중재·철수·상호 합의·실제 동원 검증. 단계마다 조건과 판단이 필요하며 지출·인력·권한을 확인한다. |
| 4 | 우발전쟁의 인과 경로 | `crisis._process_triggers`, `_border_review`; 사건의 부모/자식, 현지 행동, 보고와 국가 대응으로 단계가 이어진다. 30년의 실제 경로 예시는 최종 집계 이후 제시한다. |
| 5 | 현지 지휘관 독단 | `crisis_response._local`, `_at_incident_border`, `queue_command`; P의 위임·통신·오정보·인력·국가 권한 분리 검사. |
| 6 | 흉작과 기근의 차이 | `crisis_risk.food_state`, `detect`, `crisis._damage_plan`; R/C의 A·B 근거. 실제 재고·식량안보·대응을 사용한다. |
| 7 | 재난·전염병 피해 계산 | `crisis._capacity`, `_damage_plan`, `crisis_effects`; C/S의 피해 소유자·월결산·위생/행정·완화 검사. 역학·정밀 건축 피해 모델은 아니다. |
| 8 | 재정위기의 사건 연결 | `crisis_risk.update_pressures`, `crisis._review_events`, `crisis_response.respond`; R/C/P의 실제 미지급금·default·재조정·유한 재원 검사. |
| 9 | NPC 위기 대응 | `crisis_response.responses_for`, `process_due`, 기존 `npc_decision.decide`; 알려진 정보와 기관별 선호를 판단하고 실제 집행의 권한·조달은 별도 확인한다. |
| 10 | 플레이어에게 남기는 결정 | `schedule_pending`, 기한 처리, 기존 `Engine.pending_decisions`/`respond_to_event`; W·X 검사. 중요한 국가 대응을 자동 선택하지 않는다. |
| 11 | NPC별 다른 정보 전달 | `queue_report`, `deliver_due`, `communication_delay`; P/C의 보고 snapshot·지연·비공개 관측·왜곡·정정 검사. |
| 12 | 기억과 통치자 평가 | 기존 NPC memory·record, `record_response_outcome`, `crisis._measured_aftermath`; 자연 발생과 대응 근거를 분리하고 월별 실현 손실을 대조한다. |
| 13 | 30년 자율세계 결과 | B의 `long_campaigns`와 `run_campaign(months=360)`; 4개국·20명·seed 941의 실제 실행에서 사건 120개. 15년 저장·복원 경로와 월별 기록·최종 상태·전체 중요 기록 digest가 일치했다. |
| 14 | 충돌·전쟁위기·완화의 실제 사례 | `event_paths`, 최종 세계 사건의 `parent_event`/`child_events`, trace의 `crisis_escalated`·`local_action`·실제 대응 기록. 검증 fixture 사례와 30년 실제 사례를 구분해야 한다. |
| 15 | 발견하고 수정한 문제 | 회귀 테스트 근거: 일회성 손실 중복, 결산 전 평가, 무료 식량, 보고를 통한 정답 누설, 무권한 집행, 오래된 사건의 잘못된 부모 연결, 이미 전쟁 중인 국가의 반복 평시 사건, 저장 조작·반복 집행. 기존 경제 계수를 새 목표값에 맞춰 보정한 것은 아니다. |
| 16 | 전체 테스트 개수·결과 | 최종 실제 pytest 결과와 `TEST_RESULTS.md`. 함수 이름 수·이 문서 행 수를 통과 테스트 개수로 대신 사용하지 않는다. |
| 17 | 저장 호환 | I의 opt-in 기존 저장·버전/실행순서·실제 save/load·재해시 조작 거부 검사. Crisis 사용 시 save schema 5 / World schema 4 / Crisis state 1, Economy v3 / NPC v1 유지. |
| 18 | 성능 | 위험 평가는 기본 주간, 경제는 월간, 전달·기한은 가벼운 일간 검사. 실제 30년 연속 실행 2,853.8초, 저장·복원 경로 2,707.0초. 두 경로는 같은 PC에서 병행했으며 연구 실행 시간은 매일의 SQLite 저장 비용을 포함하지 않는다. 별도 profiling의 계측 오버헤드도 정상 실행 속도와 구분한다. |
| 19 | 현 버전 한계 | 이 문서의 근사 모델·연결점 목록과 `CRISIS_DESIGN.md`, `CRISIS_EVENT_GUIDE.md`. 통계 검증과 실제 역사 calibration을 혼동하지 않는다. |
| 20 | Military & Logistics 연결 준비 | 무장 충돌·전쟁 상태·당사자·동원·통신·현지 행동·원인/결과/보고 연결점이 있다. 전투 결과·병력 손실·상세 보급을 연결한 후에는 인구/경제/사건의 중복 피해 통합 검사가 추가로 필요하다. |

## 장기 결과를 읽을 때의 검증 범위

`crisis_benchmark.summarize`는 사건 종류·위기 수·확대/완화·국가별 전쟁 상태 진입·대응 종류·실현 손실·오정보 관련 사건·현지 독단·중재 종료를 집계한다. `measure`는 국가별 인구·GDP·부채·국고·미지급금·신용·식량안보·노동·자본·교역을 월별로 남긴다.

장기 자동 검사는 같은 seed의 전체 상태·월별 기록·중요 시스템 기록 순서가 중간 저장/복원 후 같다는 점과, 인구·부채·준비자산·미지급금·신용·식량안보·군민 인력의 불변조건, 실제 사건/자본 피해의 존재, 가짜 전투 사망의 부재를 확인한다. SQLite 엔진과 연구용 실행기의 짧은 구간 일치도 별도 검사한다.

모든 국가가 같은 정책으로 수렴하는지, 모든 위기가 전쟁으로 끝나는지, 재난/전쟁이 지나치게 자주 발생하는지에 대한 판단은 집계와 원인 경로를 읽어야 한다. 자동 테스트가 사건 수·전쟁 비율을 미리 정한 목표값으로 강제하지 않는다. 하나의 가상 세계와 하나의 seed가 모든 초기조건의 장기 안정성을 보증하지도 않는다. 따라서 최종 보고에서는 관측 결과와 모델 전체에 대한 일반화의 한계를 구분한다.

## 미구현 사항과 근사 모델

- 실제 전투·해전·상세 보급·점령·영토 분배·배상 조약은 없다. 무장 충돌과 전쟁 상태는 후속 군사 엔진의 입력이다. 사건 보고의 주장·추정 사상자는 실제 사망자 생성과 다르다.
- 혁명·쿠데타·분리주의·군 명령 불복종은 pressure와 전조/행동 연결점을 제공한다. 완전한 정권 교체·내전 군사 판정·선거/의회·계승 시스템은 아니다.
- 자연재해 위험률·통신 기본 지연은 가상 scenario/profile의 설명 가능한 입력이다. 실제 1897 지역·인물 자료나 특정 역사 연도의 강제 사건은 없다.
- 흉작/기근은 실제 경제 재고·식량안보·수송·교역·조달한 대응을 사용하지만, 상세 식량 가격·가계소득·지역별 배급 경로를 재현하지 않는다. 구휼은 돈만 써서 이미 죽은 인구를 복구하거나 가짜 식량 재고를 생성하지 않는다.
- 전염병은 유입·확산 위험과 피해/대응의 근사 모델이며 정밀 역학은 아니다. 금융 위기는 구현된 신용·미지급·준비자산·투자 차질만 사용하며 은행·물가·환율·주식시장 모델은 없다.
- 보고·소문·선전의 출처/대상/왜곡/지연 연결점은 있으나 독립 언론·선전·정보기관 전체 시스템은 없다. NPC는 전달된 기록으로 판단하며 평가 결과가 언제나 정확한 것도 아니다.
- ‘대응이 없었을 경우의 예상 손실’과 ‘예방 손실’은 모델 내부의 반사실 추정이다. 실제 월결산 손실·초기 대응능력 근거와 분리되어 있으며 현실의 인과효과를 실증한 값은 아니다.
- 위기 종료 뒤 자본 파괴·인구 손실·잔여 불만·불신과 기억은 남는다. 자동 종료가 피해를 즉시 원상복구한다는 뜻이 아니다.
- UI, ChatGPT/MCP, 외부 LLM 의사결정은 구현하지 않았다. 핵심 계산과 결과는 인터페이스 계층과 독립적이다.
