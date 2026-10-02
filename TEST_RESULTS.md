# 실제 테스트 결과 — Military v1 성능 개선

최종 생산 코드 43개 파일에서 **기존 1,136개 + 신규 117개 = 1,253개 통과**, 실패·오류·건너뜀 0개입니다. 기존 검사 Python 파일 69개(보존된 fixture 소스 포함)의 SHA-256을 유지했습니다.

서로 겹치지 않는 네 실제 pytest 실행의 합집합입니다. 합친 XML을 별도 테스트 실행으로 가장하지 않습니다.

| 실제 실행 | 통과 | pytest wall |
|---|---:|---:|
| 일반 검사와 신규 검사 | 1,197 | 654.44초 |
| 추가 same-connection SQL read 회귀 | 2 | 1.42초 |
| 기존 30년 Crisis / 20년 NPC 및 checkpoint replay | 35 | 3,898.26초 |
| 기존 군사 캠페인 검사 | 19 | 58.41초 |

군사 검사에는 최종 코드로 완주한 실제 36개월 전근대·산업전·해상원정의 원본 결과를 그대로 캐시했습니다. 이전 Military v1 결과와 원본 테스트는 수정하지 않았습니다. 별도 산업전 verification 36개월도 완주했으며, 원본/최종 및 runtime/verification의 총 405개 독립 비교에서 World·RNG·Ledger·전체 committed Engine·archive bytes·월별 결과·명령 결과가 같았습니다. 3·12·36개월 산업전 save는 원본과 파일 bytes까지 같았습니다.

신규 검사는 incremental persistence, rollback와 다일 실패, dirty-state isolation, archive 보존, active set·동률·overflow, runtime/full-audit·save/resume 동일성, 중복 피해·자원 소비 거부, RNG·pending·fog 경계를 포함합니다.

- [실제 JUnit 합집합](../measurements/pytest-completed-union.xml)
- [실행별 원시 JUnit](../measurements/pytest-completed-core.xml) / [SQL read](../measurements/pytest-completed-sql-read.xml) / [Crisis·NPC](../measurements/pytest-completed-crisis-npc.xml) / [Military](../measurements/pytest-completed-military.xml)
- [개수·출처·code hash](../measurements/completed-test-summary.json)
- [원본 검사 보존과 최종 source hash](../measurements/final-source-manifest.json)
- [성능 개선 보고](MILITARY_PERFORMANCE_OPTIMIZATION_REPORT.md)

## 개선 전 Military & Logistics Engine v1 보존 기록

최종 생산 코드에서 **1136개 통과**, 실패·오류·건너뜀 모두 0개입니다. 기존 **751개**의 원본 파일을 그대로 유지하고 신규 **385개**를 추가했습니다.

기존 검사·군사 신규 기능·군사 신규 캠페인을 세 실제 pytest 실행으로 나누었습니다. 세 JUnit의 testcase가 겹치지 않는 합집합이며, 합친 XML을 별도 실행으로 가장하지 않았습니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

세 실제 묶음의 JUnit 시간 합계는 1248.67초입니다. 기존 27개 검사 파일, 보존 체크포인트, 최종 40개 생산 코드 해시를 자동 확인했습니다.

| 신규 검사 파일 | 통과 |
|---|---:|
| test_military_benchmark | 19 |
| test_military_cargo_capacity | 14 |
| test_military_command_doctrine | 8 |
| test_military_innovation | 68 |
| test_military_integration | 30 |
| test_military_logistics | 67 |
| test_military_movement_receipts | 15 |
| test_military_operations | 82 |
| test_military_persistence_cost | 3 |
| test_military_physical_receipts | 4 |
| test_military_politics | 67 |
| test_military_politics_storage | 3 |
| test_military_supply_comparisons | 3 |
| test_military_training_trials | 2 |

세 가상 전쟁은 실제 SQLite로 각각 36개월 진행하고 매일 전체 Auditor를 적용했습니다. 산업전 18개월 저장·복원 후 결과도 전체 세계·RNG·Ledger가 일치했습니다. 기존 30년 Crisis/20년 NPC 검사도 원래 검사 조건으로 실행했습니다. 신규 검사에는 유한 보급·항만·해상 차단·권한·정보 격리·동원/사망 중복 방지·사업 지연·교리·혁신·기존 저장 호환·rollback이 포함됩니다.

- [기존 실제 로그](benchmarks/military/pytest-original-751.log)
- [신규 실제 로그](benchmarks/military/pytest-new.log)
- [실제 JUnit 합집합](benchmarks/military/pytest-combined.xml)
- [자동 검증 원시 결과](benchmarks/military/final_verification.json)
- [군사 완료 보고](MILITARY_LOGISTICS_REPORT.md)
- [군사 요구사항 대응표](MILITARY_REQUIREMENTS_CHECK.md)
