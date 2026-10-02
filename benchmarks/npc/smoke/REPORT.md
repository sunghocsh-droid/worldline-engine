# NPC_SIMULATION_REPORT

가상 4개국 NPC 자율 의사결정 캠페인

실제로 진행한 기간: **0.166667년**. seed: **731**. pytest 최종 결과: **최종 테스트 대기**.

## 검증 범위

A/B/C/D는 역사 국가가 아닌 가상국이며, 각 인구 10,000·국고 1,000·상비군 100에서 시작한다. 기존 preindustrial/early_industrial/industrial profile의 공개 기본값을 사용했다. 경제계수·GDP 목표·전쟁 결과를 국가별로 지정하지 않았다. 교육·인프라·정부 제도·파벌 영향력·인물의 선호와 편향·받은 보고·과거 경험이 초기조건으로 다르다.

A는 개발과 교육을 중시하는 내각, B는 군사 위신과 위험 감수를 중시하는 군주, C는 상환능력을 중시하는 과두정부, D는 외교와 협의를 중시하는 연합 내각이다. 각 정부에는 군주/재정/군사/교육 담당자와 개발·군사 파벌이 있다. 이름 자체는 계산에 보너스를 주지 않는다.

특히 B는 상대 병력을 실제보다 낮게 평가한 불완전한 외교관 보고를 받는다. 객관적 기록과 그 기록을 인물이 어떻게 기억하는지를 분리했다. 전쟁준비와 군사·외교 압박을 선택할 수 있지만, 이번 캠페인은 전투 판정이나 전쟁 승패를 구현하지 않는다. **전투 해결 횟수는 0**이며 전쟁준비를 실제 전쟁 발생으로 세지 않는다.

## 경제와 재정 결과

| 국가 | 경과년 | 인구 | GDP | 부채 | 국고 | 신용/100 | 미지급 | 재정상태 | 문해율 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 가상 개발국 A | 0.08333333333333333 | 10,007 | 4,056 | 0 | 978 | 93.5 | 0 | normal | 30.0% |
| 가상 위신국 B | 0.08333333333333333 | 10,007 | 6,275 | 0 | 547 | 91.7 | 0 | normal | 65.0% |
| 가상 신중국 C | 0.08333333333333333 | 10,006 | 2,295 | 0 | 1,007 | 94.3 | 0 | normal | 12.0% |
| 가상 연합국 D | 0.08333333333333333 | 10,007 | 4,059 | 0 | 978 | 91.6 | 0 | normal | 30.0% |


## 실제 결정의 다양성

인물 판단 48건, 정부 실행 시도 8건을 기록했다. 실행권한이 없는 인물의 제안은 국가 명령과 구분한다.

| 선택 종류 | 인물 선택 횟수 |
| --- | --- |
| development | 12 |
| fiscal_consolidation | 15 |
| industrialization | 6 |
| military_expansion | 9 |
| war_preparation | 6 |


| 인물 | 판단 횟수 |
| --- | --- |
| a:education | 3 |
| a:finance | 3 |
| a:military | 3 |
| a:ruler | 3 |
| b:education | 3 |
| b:finance | 3 |
| b:military | 3 |
| b:ruler | 3 |
| c:education | 3 |
| c:finance | 3 |
| c:military | 3 |
| c:ruler | 3 |
| d:education | 3 |
| d:finance | 3 |
| d:military | 3 |
| d:ruler | 3 |


위 집계는 엔진이 반환한 실제 판단과 실행 사건에서 계산했다. 위험한 선택을 없애기 위해 경제 위험을 행동금지로 바꾸지 않았다. 실제 부족한 사람·자재·조달수단 때문에 실행이 불가능하면 이유를 기록한다. 정책의 의도·제안·정부 승인·실제 실행을 혼동하지 않는다.

## 선택을 설명하는 내부 근거

### development: a:education / 1897-01-02

| 요인 | 점수 기여 | 근거 |
| --- | --- | --- |
| outcome:education | +1.8169 | {'perceived_outcome': 0.75, 'actor_weight': 2.4225, 'role': 'education'} |
| outcome:fiscal | -0.0504 | {'perceived_outcome': -0.12, 'actor_weight': 0.41999999999999993, 'role': 'education'} |
| outcome:prestige | +0.0360 | {'perceived_outcome': 0.12, 'actor_weight': 0.3, 'role': 'education'} |
| outcome:prosperity | +0.5197 | {'perceived_outcome': 0.55, 'actor_weight': 0.9449999999999998, 'role': 'education'} |
| outcome:security | -0.0300 | {'perceived_outcome': -0.1, 'actor_weight': 0.3, 'role': 'education'} |
| perceived_cost | -0.0393 | {'cost': 66.0, 'perceived_budget_scale': 861.7751999999996, 'normalized_cost': 0.07113792220356831} |
| perceived_risk | -0.0494 | {'risk': 0.05314083106073923, 'risk_tolerance': 0.2, 'fiscal_weight': 0.41999999999999993} |
| trait:prestige_sensitivity | -0.0252 | {'perceived_prestige': 0.12, 'sensitivity': 0.2} |
| trait:patience | +0.0557 | {'perceived_delay_months': 30, 'delayed_gain': 0.65, 'patience': 0.7} |
| information:staleness_uncertainty | -0.0071 | {'report_quality': 0.6000000000000001, 'basis': 'confidence, reliability and report age only'} |
| group:a:development:education | +0.3000 | {'group': 'a:development', 'interest': 'education', 'alignment': 0.75, 'weight': 0.8} |
| group:a:development:fiscal | -0.0480 | {'group': 'a:development', 'interest': 'fiscal', 'alignment': -0.12, 'weight': 0.8} |
| group:a:development:prosperity | +0.2200 | {'group': 'a:development', 'interest': 'prosperity', 'alignment': 0.55, 'weight': 0.8} |
| memory:initial-memory-a | +0.0315 | {'interpreted_valence': 0.6, 'confidence': 0.85, 'salience': 0.6, 'interpretation': {'lesson': '이전 교육 투자에 대한 성공 경험', 'policy_preferences': {'education': 0.7}}} |
| bounded_deliberation_variation | +0.0051 | {'range': [-0.02, 0.02], 'purpose': 'small tie/persuasion variation, never crisis creation'} |


### fiscal_consolidation: a:finance / 1897-01-02

| 요인 | 점수 기여 | 근거 |
| --- | --- | --- |
| outcome:education | -0.0200 | {'perceived_outcome': -0.1, 'actor_weight': 0.2, 'role': 'finance'} |
| outcome:fiscal | +2.0591 | {'perceived_outcome': 0.85, 'actor_weight': 2.4225, 'role': 'finance'} |
| outcome:prestige | -0.0200 | {'perceived_outcome': -0.1, 'actor_weight': 0.2, 'role': 'finance'} |
| outcome:prosperity | -0.0972 | {'perceived_outcome': -0.12, 'actor_weight': 0.81, 'role': 'finance'} |
| outcome:security | -0.0162 | {'perceived_outcome': -0.18, 'actor_weight': 0.09000000000000001, 'role': 'finance'} |
| perceived_cost | -0.0493 | {'cost': 25.0, 'perceived_budget_scale': 864.0, 'normalized_cost': 0.0281214848143982} |
| perceived_risk | -0.1274 | {'risk': 0.056900795522583994, 'risk_tolerance': 0.2, 'fiscal_weight': 2.4225} |
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
| perceived_cost | -0.0438 | {'cost': 76.0, 'perceived_budget_scale': 861.7751999999996, 'normalized_cost': 0.08104287680032489} |
| perceived_risk | -0.0308 | {'risk': 0.05314083106073923, 'risk_tolerance': 0.7, 'fiscal_weight': 0.4} |
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
| perceived_cost | -0.0572 | {'cost': 66.0, 'perceived_budget_scale': 861.7751999999996, 'normalized_cost': 0.07113792220356831} |
| perceived_risk | -0.0569 | {'risk': 0.05314083106073923, 'risk_tolerance': 0.35, 'fiscal_weight': 0.8400000000000001} |
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
| perceived_cost | -0.0948 | {'cost': 254.1, 'perceived_budget_scale': 1193.2271999999994, 'normalized_cost': 0.17556500009120268} |
| perceived_risk | -0.0029 | {'risk': 0.004974623961423453, 'risk_tolerance': 0.7, 'fiscal_weight': 0.4} |
| bias:overconfidence | +0.0008 | {'discounts': 'perceived downside', 'strength': 0.6400000000000001} |
| bias:optimism | +0.0006 | {'discounts': 'perceived downside', 'strength': 0.5599999999999999} |
| trait:prestige_sensitivity | +0.1960 | {'perceived_prestige': 0.7, 'sensitivity': 0.9} |
| trait:ambition | +0.0700 | {'perceived_prestige': 0.7, 'ambition': 0.9} |
| information:staleness_uncertainty | -0.0074 | {'report_quality': 0.6000000000000001, 'basis': 'confidence, reliability and report age only'} |
| belief:military_balance | +0.4627 | {'own_strength': 200.0, 'enemy_strength': 25.0, 'ally_strength': 0, 'threat': 0.8, 'perceived_balance': 0.7777777777777778, 'report_quality': 0.4038934426229508} |
| bias:enemy_underestimation | +0.0380 | {'biased_enemy_strength': 17.2, 'strength': 0.52} |
| group:b:military_faction:prestige | +0.3150 | {'group': 'b:military_faction', 'interest': 'prestige', 'alignment': 0.7, 'weight': 0.9} |
| group:b:military_faction:security | +0.4500 | {'group': 'b:military_faction', 'interest': 'security', 'alignment': 1.0, 'weight': 0.9} |
| bounded_deliberation_variation | +0.0041 | {'range': [-0.02, 0.02], 'purpose': 'small tie/persuasion variation, never crisis creation'} |


각 평가점수는 표시한 reason_trace 기여 합과 일치하는지 자동 검사한다. 선택된 설명 역시 같은 평가의 설명을 복사한 값이다. 보고서의 예시는 원시 사건을 축약한 것이며, 상세 입력·perceived 비용/위험·제약·정부 승인·실제 명령 결과는 campaign.json에 있다.

## 재현성 및 정보 분리

모든 판단의 난수는 게임 RNG를 사용한다. 프로젝트→경제→NPC→압력 순서로 실제 tick 함수를 호출하고 매월 세계 validator를 실행한다. 연구 실행은 SQLite IO만 생략하며, 연구 체크포인트는 World·RNG·외부 수집 사건·코드 해시를 보존한다. JSON 체크섬이나 코드 버전이 다르면 조용히 계속하지 않는다. 실제 게임 저장본 호환성과 실행 순서는 별도 Engine 테스트로 검증한다.

NPC 판단에는 명시적으로 받은 보고와 행위자의 기억만 전달한다. 다른 국가의 실제 세계 상태를 무료로 읽는 센서를 추가하지 않았다. trusted 실행 계층이 확인하는 실제 자원·비용과 인물이 예상하는 비용·위험을 분리한다. 경제 엔진이 정치적으로 위험하다는 이유로 선택을 스스로 취소하지 않는다.

## 한계

이 실험은 네 가상정부의 하나의 초기 배치와 seed에 대한 검증이다. 모든 시대·국가·제도·전쟁 선택의 역사적 현실성을 입증하지 않는다. 전투, 군사 AI, 외교 승패, 혁명·쿠데타·자동 위기, 가격·환율, 실제 1897 국가자료, MCP와 UI는 추가하지 않았다. 실제 위협·전쟁 결과가 NPC의 기억과 판단에 전달되는 후속 통합은 별도의 검증이 필요하다.

원시 JSON에는 매월 경제 지표와 모든 중요한 NPC 사건, 최종 세계·RNG가 들어 있다. 현재 세계의 판단/실행 표시 목록은 유한한 최근 기록만 보관하고, 연구 로그와 게임 Event Ledger가 영구 사건 기록을 담당한다. 새로운 정부·인물·집단·기억을 추가해 같은 harness를 재사용할 수 있다.
