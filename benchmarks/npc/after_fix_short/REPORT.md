# NPC_SIMULATION_REPORT

가상 4개국 NPC 자율 의사결정 캠페인

실제로 진행한 기간: **0.25년**. seed: **731**. pytest 최종 결과: **캠페인 단기 16개 통과, 장기 2개 대기**.

## 검증 범위

A/B/C/D는 역사 국가가 아닌 가상국이며, 각 인구 10,000·국고 1,000·상비군 100에서 시작한다. 기존 preindustrial/early_industrial/industrial profile의 공개 기본값을 사용했다. 경제계수·GDP 목표·전쟁 결과를 국가별로 지정하지 않았다. 교육·인프라·정부 제도·파벌 영향력·인물의 선호와 편향·받은 보고·과거 경험이 초기조건으로 다르다.

A는 개발과 교육을 중시하는 내각, B는 군사 위신과 위험 감수를 중시하는 군주, C는 상환능력을 중시하는 과두정부, D는 외교와 협의를 중시하는 연합 내각이다. 각 정부에는 군주/재정/군사/교육 담당자와 개발·군사 파벌이 있다. 이름 자체는 계산에 보너스를 주지 않는다.

특히 B는 상대 병력을 실제보다 낮게 평가한 불완전한 외교관 보고를 받는다. 객관적 기록과 그 기록을 인물이 어떻게 기억하는지를 분리했다. 전쟁준비와 군사·외교 압박을 선택할 수 있지만, 이번 캠페인은 전투 판정이나 전쟁 승패를 구현하지 않는다. **전투 해결 횟수는 0**이며 전쟁준비를 실제 전쟁 발생으로 세지 않는다.

## 구조 검증에서 고친 문제

단기 실행으로 발견한 결함마다 회귀 검사를 추가했다. 월별 보고일과 내각 회의일이 어긋나 회의가 누락되는 문제, 국가 사이 행동 ID 중복, 군주의 중복 투표, 비어 있는 군주·평가일의 실행 오류, 실제 지출을 줄이는 미래 예산 결정까지 당장의 자금 부족으로 막는 문제를 검사한다. 정부의 정책 승인과 월별 실제 지급을 분리하고, 실제 자원·자금 제약은 지급·건설·동원 단계에 계속 적용한다.

경제 검사용 복사에는 경제 계산에 필요 없는 NPC 과거 기록을 제외해 장기 실행 비용을 줄였다. 경제계수나 GDP 목표를 조정하는 방식은 사용하지 않았다.

## 경제와 재정 결과

| 국가 | 경과기간 | 인구 | GDP | 부채 | 국고 | 신용/100 | 미지급 | 재정상태 | 문해율 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 가상 개발국 A | 1개월 | 10,007 | 4,056 | 0 | 978 | 93.5 | 0 | normal | 30.0% |
| 가상 위신국 B | 1개월 | 10,007 | 6,275 | 0 | 547 | 91.7 | 0 | normal | 65.0% |
| 가상 신중국 C | 1개월 | 10,006 | 2,295 | 0 | 1,007 | 94.3 | 0 | normal | 12.0% |
| 가상 연합국 D | 1개월 | 10,007 | 4,059 | 0 | 978 | 91.6 | 0 | normal | 30.0% |


## 실제 결정의 다양성

인물 판단 72건, 정부 실행 시도 16건을 기록했다. 실행권한이 없는 인물의 제안은 국가 명령과 구분한다.

| 선택 종류 | 인물 선택 횟수 |
| --- | --- |
| development | 16 |
| fiscal_consolidation | 21 |
| industrialization | 10 |
| military_expansion | 15 |
| war_preparation | 10 |


| 국가:명령:실행 결과 | 횟수 |
| --- | --- |
| a:development:accepted | 4 |
| b:war_preparation:accepted | 4 |
| c:fiscal_consolidation:accepted | 4 |
| d:industrialization:accepted | 4 |


| 인물 | 판단 횟수 |
| --- | --- |
| a:education | 4 |
| a:finance | 4 |
| a:military | 5 |
| a:ruler | 5 |
| b:education | 4 |
| b:finance | 4 |
| b:military | 5 |
| b:ruler | 5 |
| c:education | 4 |
| c:finance | 4 |
| c:military | 5 |
| c:ruler | 5 |
| d:education | 4 |
| d:finance | 4 |
| d:military | 5 |
| d:ruler | 5 |


위 집계는 엔진이 반환한 실제 판단과 실행 사건에서 계산했다. 위험한 선택을 없애기 위해 경제 위험을 행동금지로 바꾸지 않았다. 실제 부족한 사람·자재·조달수단 때문에 실행이 불가능하면 이유를 기록한다. 정책의 의도·제안·정부 승인·실제 실행을 혼동하지 않는다.

## 군주를 평가하는 서로 다른 기준

| 평가자 | 호감 | 존경 | 신뢰 | 두려움 | 정당성 인식 | 원한 |
| --- | --- | --- | --- | --- | --- | --- |
| a:education | 0.508 | 0.500 | 0.500 | 0.000 | 0.500 | 0.000 |
| a:finance | 0.508 | 0.500 | 0.500 | 0.000 | 0.500 | 0.000 |
| a:military | 0.508 | 0.500 | 0.500 | 0.000 | 0.500 | 0.000 |
| a:ruler | 0.508 | 0.500 | 0.500 | 0.000 | 0.500 | 0.000 |
| b:education | 0.470 | 0.469 | 0.362 | 0.000 | 0.450 | 0.131 |
| b:finance | 0.470 | 0.469 | 0.362 | 0.000 | 0.450 | 0.131 |
| b:military | 0.470 | 0.469 | 0.362 | 0.000 | 0.450 | 0.131 |
| b:ruler | 0.470 | 0.469 | 0.362 | 0.000 | 0.450 | 0.131 |
| c:education | 0.490 | 0.499 | 0.500 | 0.000 | 0.500 | 0.023 |
| c:finance | 0.490 | 0.499 | 0.500 | 0.000 | 0.500 | 0.023 |
| c:military | 0.490 | 0.499 | 0.500 | 0.000 | 0.500 | 0.023 |
| c:ruler | 0.490 | 0.499 | 0.500 | 0.000 | 0.500 | 0.023 |
| d:education | 0.507 | 0.514 | 0.542 | 0.000 | 0.500 | 0.000 |
| d:finance | 0.507 | 0.514 | 0.542 | 0.000 | 0.500 | 0.000 |
| d:military | 0.507 | 0.514 | 0.542 | 0.000 | 0.500 | 0.000 |
| d:ruler | 0.507 | 0.514 | 0.542 | 0.000 | 0.500 | 0.000 |


같은 군주라도 평가자의 경험과 이해관계에 따라 다른 값이 나온다. 호감·존경·신뢰·복종은 서로 다른 기준으로 유지한다. 최종 World에는 그 값을 만든 알려진 사건과 원인이 보존된다.

## 선택을 설명하는 내부 근거

### development: a:education / 1897-01-02

| 요인 | 점수 기여 | 근거 |
| --- | --- | --- |
| outcome:education | +1.8169 | {'perceived_outcome': 0.75, 'actor_weight': 2.4225, 'role': 'education'} |
| outcome:fiscal | -0.0504 | {'perceived_outcome': -0.12, 'actor_weight': 0.41999999999999993, 'role': 'education'} |
| outcome:prestige | +0.0360 | {'perceived_outcome': 0.12, 'actor_weight': 0.3, 'role': 'education'} |
| outcome:prosperity | +0.5197 | {'perceived_outcome': 0.55, 'actor_weight': 0.9449999999999998, 'role': 'education'} |
| outcome:security | -0.0300 | {'perceived_outcome': -0.1, 'actor_weight': 0.3, 'role': 'education'} |
| perceived_cost | -0.0377 | {'cost': 66.0, 'perceived_budget_scale': 861.7751999999996, 'normalized_cost': 0.07113792220356831, 'finance_ability': 0.4} |
| perceived_risk | -0.0474 | {'risk': 0.05314083106073923, 'risk_tolerance': 0.2, 'fiscal_weight': 0.41999999999999993} |
| constraint:financing_access | +0.0000 | {'normal_financial_feasibility': True, 'emergency_financial_feasibility': True, 'meaning': 'Emergency finance permits a dangerous choice; no accessible finance is an execution cons |
| trait:prestige_sensitivity | -0.0252 | {'perceived_prestige': 0.12, 'sensitivity': 0.2} |
| trait:patience | +0.0557 | {'perceived_delay_months': 30, 'delayed_gain': 0.65, 'patience': 0.7} |
| information:staleness_uncertainty | -0.0071 | {'report_quality': 0.6000000000000001, 'basis': 'confidence, reliability and report age only'} |
| group:a:development:education | +0.3000 | {'group': 'a:development', 'interest': 'education', 'alignment': 0.75, 'weight': 0.8} |
| group:a:development:fiscal | -0.0480 | {'group': 'a:development', 'interest': 'fiscal', 'alignment': -0.12, 'weight': 0.8} |
| group:a:development:prosperity | +0.2200 | {'group': 'a:development', 'interest': 'prosperity', 'alignment': 0.55, 'weight': 0.8} |
| memory:initial-memory-a | +0.0315 | {'interpreted_valence': 0.6, 'confidence': 0.85, 'salience': 0.6, 'interpretation': {'education': 0.7, 'lesson': '이전 교육 투자에 대한 성공 경험'}, 'attention_fraction': 1.0, 'attention_budget |
| memory:initial-memory-a:education | +0.1334 | {'interpretation': 0.7, 'perceived_alternative': 0.75, 'effective_memory_weight': 0.2622116678616372, 'lesson': '이전 교육 투자에 대한 성공 경험'} |
| bounded_deliberation_variation | +0.0051 | {'range': [-0.02, 0.02], 'purpose': 'small tie/persuasion variation, never crisis creation'} |


### fiscal_consolidation: a:finance / 1897-01-02

| 요인 | 점수 기여 | 근거 |
| --- | --- | --- |
| outcome:education | -0.0200 | {'perceived_outcome': -0.1, 'actor_weight': 0.2, 'role': 'finance'} |
| outcome:fiscal | +2.0591 | {'perceived_outcome': 0.85, 'actor_weight': 2.4225, 'role': 'finance'} |
| outcome:prestige | -0.0200 | {'perceived_outcome': -0.1, 'actor_weight': 0.2, 'role': 'finance'} |
| outcome:prosperity | -0.0972 | {'perceived_outcome': -0.12, 'actor_weight': 0.81, 'role': 'finance'} |
| outcome:security | -0.0162 | {'perceived_outcome': -0.18, 'actor_weight': 0.09000000000000001, 'role': 'finance'} |
| perceived_cost | -0.0572 | {'cost': 25.0, 'perceived_budget_scale': 864.0, 'normalized_cost': 0.0281214848143982, 'finance_ability': 0.9} |
| perceived_risk | -0.1477 | {'risk': 0.056900795522583994, 'risk_tolerance': 0.2, 'fiscal_weight': 2.4225} |
| constraint:financing_access | +0.0000 | {'normal_financial_feasibility': True, 'emergency_financial_feasibility': True, 'meaning': 'Emergency finance permits a dangerous choice; no accessible finance is an execution cons |
| trait:prestige_sensitivity | +0.0210 | {'perceived_prestige': -0.1, 'sensitivity': 0.2} |
| information:staleness_uncertainty | -0.0043 | {'report_quality': 0.6960000000000002, 'basis': 'confidence, reliability and report age only'} |
| group:a:development:education | -0.0400 | {'group': 'a:development', 'interest': 'education', 'alignment': -0.1, 'weight': 0.8} |
| group:a:development:fiscal | +0.3400 | {'group': 'a:development', 'interest': 'fiscal', 'alignment': 0.85, 'weight': 0.8} |
| group:a:development:prosperity | -0.0480 | {'group': 'a:development', 'interest': 'prosperity', 'alignment': -0.12, 'weight': 0.8} |
| bounded_deliberation_variation | +0.0015 | {'range': [-0.02, 0.02], 'purpose': 'small tie/persuasion variation, never crisis creation'} |


### military_expansion: a:military / 1897-01-02

| 요인 | 점수 기여 | 근거 |
| --- | --- | --- |
| outcome:education | -0.0200 | {'perceived_outcome': -0.2, 'actor_weight': 0.1, 'role': 'military'} |
| outcome:fiscal | -0.2200 | {'perceived_outcome': -0.55, 'actor_weight': 0.4, 'role': 'military'} |
| outcome:prestige | +0.6000 | {'perceived_outcome': 0.5, 'actor_weight': 1.2000000000000002, 'role': 'military'} |
| outcome:prosperity | -0.1050 | {'perceived_outcome': -0.35, 'actor_weight': 0.3, 'role': 'military'} |
| outcome:security | +1.4535 | {'perceived_outcome': 0.6, 'actor_weight': 2.4225, 'role': 'military'} |
| perceived_cost | -0.0420 | {'cost': 76.0, 'perceived_budget_scale': 861.7751999999996, 'normalized_cost': 0.08104287680032489, 'finance_ability': 0.4} |
| perceived_risk | -0.0296 | {'risk': 0.05314083106073923, 'risk_tolerance': 0.7, 'fiscal_weight': 0.4} |
| constraint:financing_access | +0.0000 | {'normal_financial_feasibility': True, 'emergency_financial_feasibility': True, 'meaning': 'Emergency finance permits a dangerous choice; no accessible finance is an execution cons |
| trait:prestige_sensitivity | +0.1400 | {'perceived_prestige': 0.5, 'sensitivity': 0.9} |
| information:staleness_uncertainty | -0.0075 | {'report_quality': 0.6000000000000001, 'basis': 'confidence, reliability and report age only'} |
| group:a:military_faction:prestige | +0.2250 | {'group': 'a:military_faction', 'interest': 'prestige', 'alignment': 0.5, 'weight': 0.9} |
| group:a:military_faction:security | +0.2700 | {'group': 'a:military_faction', 'interest': 'security', 'alignment': 0.6, 'weight': 0.9} |
| bounded_deliberation_variation | +0.0083 | {'range': [-0.02, 0.02], 'purpose': 'small tie/persuasion variation, never crisis creation'} |


### industrialization: a:ruler / 1897-01-02

| 요인 | 점수 기여 | 근거 |
| --- | --- | --- |
| outcome:education | +0.1417 | {'perceived_outcome': 0.35, 'actor_weight': 0.40499999999999997, 'role': 'ruler'} |
| outcome:fiscal | -0.1680 | {'perceived_outcome': -0.2, 'actor_weight': 0.8400000000000001, 'role': 'ruler'} |
| outcome:prestige | +0.0525 | {'perceived_outcome': 0.25, 'actor_weight': 0.20999999999999996, 'role': 'ruler'} |
| outcome:prosperity | +0.9113 | {'perceived_outcome': 0.75, 'actor_weight': 1.215, 'role': 'ruler'} |
| outcome:security | +0.0810 | {'perceived_outcome': 0.15, 'actor_weight': 0.5399999999999999, 'role': 'ruler'} |
| perceived_cost | -0.0549 | {'cost': 66.0, 'perceived_budget_scale': 861.7751999999996, 'normalized_cost': 0.07113792220356831, 'finance_ability': 0.4} |
| perceived_risk | -0.0546 | {'risk': 0.05314083106073923, 'risk_tolerance': 0.35, 'fiscal_weight': 0.8400000000000001} |
| constraint:financing_access | +0.0000 | {'normal_financial_feasibility': True, 'emergency_financial_feasibility': True, 'meaning': 'Emergency finance permits a dangerous choice; no accessible finance is an execution cons |
| trait:prestige_sensitivity | -0.0525 | {'perceived_prestige': 0.25, 'sensitivity': 0.2} |
| trait:patience | +0.0471 | {'perceived_delay_months': 30, 'delayed_gain': 0.55, 'patience': 0.7} |
| information:staleness_uncertainty | -0.0071 | {'report_quality': 0.6000000000000001, 'basis': 'confidence, reliability and report age only'} |
| group:a:military_faction:prestige | +0.1125 | {'group': 'a:military_faction', 'interest': 'prestige', 'alignment': 0.25, 'weight': 0.9} |
| group:a:military_faction:security | +0.0675 | {'group': 'a:military_faction', 'interest': 'security', 'alignment': 0.15, 'weight': 0.9} |
| bounded_deliberation_variation | +0.0069 | {'range': [-0.02, 0.02], 'purpose': 'small tie/persuasion variation, never crisis creation'} |


### war_preparation: b:military / 1897-01-02

| 요인 | 점수 기여 | 근거 |
| --- | --- | --- |
| outcome:education | -0.0250 | {'perceived_outcome': -0.25, 'actor_weight': 0.1, 'role': 'military'} |
| outcome:fiscal | -0.3200 | {'perceived_outcome': -0.8, 'actor_weight': 0.4, 'role': 'military'} |
| outcome:prestige | +0.8400 | {'perceived_outcome': 0.7, 'actor_weight': 1.2000000000000002, 'role': 'military'} |
| outcome:prosperity | -0.1650 | {'perceived_outcome': -0.55, 'actor_weight': 0.3, 'role': 'military'} |
| outcome:security | +2.4225 | {'perceived_outcome': 1.0, 'actor_weight': 2.4225, 'role': 'military'} |
| perceived_cost | -0.0910 | {'cost': 254.1, 'perceived_budget_scale': 1193.2271999999994, 'normalized_cost': 0.17556500009120268, 'finance_ability': 0.4} |
| perceived_risk | -0.0028 | {'risk': 0.004974623961423453, 'risk_tolerance': 0.7, 'fiscal_weight': 0.4} |
| constraint:financing_access | +0.0000 | {'normal_financial_feasibility': True, 'emergency_financial_feasibility': True, 'meaning': 'Emergency finance permits a dangerous choice; no accessible finance is an execution cons |
| bias:overconfidence | +0.0008 | {'discounts': 'perceived downside', 'strength': 0.6400000000000001} |
| bias:optimism | +0.0005 | {'discounts': 'perceived downside', 'strength': 0.5599999999999999} |
| trait:prestige_sensitivity | +0.1960 | {'perceived_prestige': 0.7, 'sensitivity': 0.9} |
| trait:ambition | +0.0700 | {'perceived_prestige': 0.7, 'ambition': 0.9} |
| information:staleness_uncertainty | -0.0074 | {'report_quality': 0.6000000000000001, 'basis': 'confidence, reliability and report age only'} |
| belief:military_balance | +0.4627 | {'own_strength': 200.0, 'enemy_strength': 25.0, 'ally_strength': 0, 'threat': 0.8, 'perceived_balance': 0.7777777777777778, 'report_quality': 0.4038934426229508} |
| bias:enemy_underestimation | +0.0380 | {'biased_enemy_strength': 17.2, 'strength': 0.52} |
| group:b:military_faction:prestige | +0.3150 | {'group': 'b:military_faction', 'interest': 'prestige', 'alignment': 0.7, 'weight': 0.9} |
| group:b:military_faction:security | +0.4500 | {'group': 'b:military_faction', 'interest': 'security', 'alignment': 1.0, 'weight': 0.9} |
| memory:initial-memory-b | -0.1010 | {'interpreted_valence': -0.8, 'confidence': 0.85, 'salience': 0.9, 'interpretation': {'lesson': '회담 결렬은 개인과 국가의 위신에 대한 모욕', 'prestige': 0.7, 'security': 0.8}, 'attention_fraction': |
| memory:initial-memory-b:security | +0.4892 | {'interpretation': 0.8, 'perceived_alternative': 1.0, 'effective_memory_weight': 0.6311204965919784, 'lesson': '회담 결렬은 개인과 국가의 위신에 대한 모욕'} |
| memory:initial-memory-b:prestige | +0.1484 | {'interpretation': 0.7, 'perceived_alternative': 0.7, 'effective_memory_weight': 0.6311204965919784, 'lesson': '회담 결렬은 개인과 국가의 위신에 대한 모욕'} |
| bias:historical_analogy_bias | +0.0682 | {'memory': 'initial-memory-b', 'interpreted_valence': -0.8, 'dimension_lesson': 0.6376841497565351} |
| bounded_deliberation_variation | +0.0041 | {'range': [-0.02, 0.02], 'purpose': 'small tie/persuasion variation, never crisis creation'} |


각 평가점수는 표시한 reason_trace 기여 합과 일치하는지 자동 검사한다. 선택된 설명 역시 같은 평가의 설명을 복사한 값이다. 보고서의 예시는 원시 사건을 축약한 것이며, 상세 입력·perceived 비용/위험·제약·정부 승인·실제 명령 결과는 campaign.json에 있다.

## 지도부 예상과 실제 경제 제약

| 국가 | 명령 | 예상 비용 | 실제 상태 기준 견적 | 예상 위험 | 실제 상태 위험 | 실행 |
| --- | --- | --- | --- | --- | --- | --- |
| a | development | 66.0 | 66.0 | 0.053 | 0.051 | accepted |
| b | war_preparation | 254.1 | 308.0 | 0.005 | 0.086 | accepted |
| c | fiscal_consolidation | 18.0 | 18.0 | 0.060 | 0.057 | accepted |
| d | industrialization | 66.0 | 66.0 | 0.074 | 0.070 | accepted |


위 표는 첫 실행 시도의 서로 다른 두 견적을 보여준다. 실제 상태 기준 견적은 현재 실제 세계·재정·조달수단으로 계산한 요청 비용이며, 예산 정책은 향후 월별 지급 과정에서 실제 지급액과 미지급액이 다시 정해진다. 객관적 위험은 해당 명령의 조달 견적이고, 예상 위험은 지도부가 받은 국내 재정 보고·능력·편향을 적용한 값이다. 위험 점수는 상대적 모델 위험을 표현한다.

## 수정 전후 단기 비교

| 실험 | 인물 판단 | 정부 실행 시도 |
| --- | --- | --- |
| 수정 전 3개월 | 72 | 8 |
| 수정 후 같은 3개월 | 72 | 16 |


이 비교는 동일한 가상국·seed의 첫 3개월을 사용한다. 종료일에도 날짜 진행과 월별 결산에 따른 판단이 실행된다. 수정 전 20년 결과를 완성했다고 주장하지 않는다. 장기 실행 도중 단기 결함을 확인해 그 실행을 중단했고, 수정 전 원시 자료는 before_fix 폴더에 따로 보존했다.

## 재현성 및 정보 분리

모든 판단의 난수는 게임 RNG를 사용한다. 프로젝트→경제→NPC→압력 순서로 실제 tick 함수를 호출하고 매월 세계 validator를 실행한다. 연구 실행은 SQLite IO만 생략하며, 연구 체크포인트는 World·RNG·외부 수집 사건·코드 해시를 보존한다. JSON 체크섬이나 코드 버전이 다르면 조용히 계속하지 않는다. 실제 게임 저장본 호환성과 실행 순서는 별도 Engine 테스트로 검증한다.

NPC 판단에는 명시적으로 받은 보고와 행위자의 기억만 전달한다. 다른 국가의 실제 세계 상태를 무료로 읽는 센서를 추가하지 않았다. trusted 실행 계층이 확인하는 실제 자원·비용과 인물이 예상하는 비용·위험을 분리한다. 경제 엔진이 정치적으로 위험하다는 이유로 선택을 스스로 취소하지 않는다.

## 한계

이 실험은 네 가상정부의 하나의 초기 배치와 seed에 대한 검증이다. 모든 시대·국가·제도·전쟁 선택의 역사적 현실성을 입증하지 않는다. 전투, 군사 AI, 외교 승패, 혁명·쿠데타·자동 위기, 가격·환율, 실제 1897 국가자료, MCP와 UI는 추가하지 않았다. 실제 위협·전쟁 결과가 NPC의 기억과 판단에 전달되는 후속 통합은 별도의 검증이 필요하다.

원시 JSON에는 매월 경제 지표와 모든 중요한 NPC 사건, 최종 세계·RNG가 들어 있다. 현재 세계의 판단/실행 표시 목록은 유한한 최근 기록만 보관하고, 연구 로그와 게임 Event Ledger가 영구 사건 기록을 담당한다. 새로운 정부·인물·집단·기억을 추가해 같은 harness를 재사용할 수 있다.

장기 기록량은 여전히 늘어난다. 판단 기록에 그 시점의 보고 메타데이터가 포함되므로 캠페인이 더 길어지면 보고 참조를 별도 저장하고 압축하는 검토가 필요하다. 중요한 사건이나 평생 기억을 임의로 삭제해 속도를 맞추지는 않았다.
