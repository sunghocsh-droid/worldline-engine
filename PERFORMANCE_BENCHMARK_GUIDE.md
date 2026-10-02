# 성능 측정 도구

이 도구는 게임 규칙을 바꾸지 않고 기존 경제·NPC·사건 시스템을 실제로 실행한다. 실행 시간과 메모리 측정치는 게임 상태에 넣지 않는다. 같은 세계가 더 빨리 계산되는지와 판단 결과가 그대로인지 함께 확인한다.

## 짧은 규모 비교

프로젝트 폴더에서 다음 명령을 실행한다. 4개국은 기존 30년 사건 캠페인과 정확히 같은 초기조건이다. 10·20개국은 같은 가상 국가의 공개 초기조건을 반복하고 실제 무역·국경·지역·인물을 연결한 별도 성능 실험이다. 각 국가에 인물 5명이 있다.

```powershell
.\.venv\Scripts\python.exe -m worldline.performance_benchmark --countries 4 --months 3 --output benchmarks/performance/repeated_scaling_4.json
.\.venv\Scripts\python.exe -m worldline.performance_benchmark --countries 10 --months 3 --output benchmarks/performance/repeated_scaling_10.json
.\.venv\Scripts\python.exe -m worldline.performance_benchmark --countries 20 --months 3 --output benchmarks/performance/repeated_scaling_20.json
```

`--original`을 더하면 동봉된 원래 Crisis v1 코드를 별도 이름공간에 불러와 같은 실험을 수행한다. 현재 엔진 대신 옛 코드를 게임에 연결하는 기능은 아니다. 원래 소스 파일이 체크섬과 다르면 비교를 거절한다.

## 30년 실행과 실제 중간 저장 복원

```powershell
.\.venv\Scripts\python.exe -m worldline.performance_benchmark --campaign --months 360 --output benchmarks/performance/repeated_30y
.\.venv\Scripts\python.exe -m worldline.performance_benchmark --resume --output benchmarks/performance/repeated_30y
```

첫 명령은 중단 없이 30년을 계산하면서 15년 시점에 연구용 체크포인트를 쓴다. 두 번째 명령은 그 저장 상태를 실제로 읽고 이후 15년을 다시 계산한다. 동일한 첫 15년을 공유하고, 두 경로의 최종 세계·RNG·사건 순서·NPC 판단을 비교한다. 원본을 복제해 최종 결과인 척하는 방식은 사용하지 않는다.

결과에는 1·10·15·20·30년의 실제 경과시간, 마지막 월 계산시간, 인구·경제를 포함한 세계 체크섬, 보고·기억·사건 개수, 프로세스 메모리와 논리 JSON 크기가 기록된다. 실행 중 소스가 바뀌면 결과를 유효한 캠페인으로 저장하지 않는다.

## 측정 범위

긴 연구 캠페인은 실제 프로젝트→경제→NPC→사건 tick과 기존 pressure 난수 호출을 유지하고 매월 Auditor를 실행한다. 전체 사건 로그도 압축 저장한다. 게임 SQLite의 매일 상태 저장과 매일 비교 감사는 이 연구 캠페인의 시간에 포함되지 않는다. 실제 SQLite 측정은 `storage_profile.json`과 `sqlite_day_comparison.json`으로 분리한다.

정밀 프로파일은 별도 연구 객체에서 수행한다. cProfile은 실행을 느리게 만들므로 그 시간과 일반 캠페인 시간을 섞지 않는다. 함수별 누적 시간은 서로 포함되어 있어 합산하면 안 된다. 인물 view·기억 조회의 새 내부 함수와 기존 공개 함수는 각각 실제 호출된 경계를 기록한다. serialization도 별도 단계로 기록한다.

`tests/fixtures/crisis_v1_source`는 원래 27개 소스와 체크섬을 보존한 비교용 자료다. `benchmarks/performance/baseline_checkpoint.json.gz`, `baseline_campaign.json.gz`는 원래 출처를 그대로 보관한다. 새 코드로 실행한 결과는 `optimized_*`에 별도로 저장하며, 이전 출처를 새 코드의 출처로 바꾸지 않는다.

원래 전체 프로젝트 복원 지점은 작업 폴더의 `outputs/checkpoints/crisis-v1-stable`이다. 프로젝트 폴더에서 보면 `../checkpoints/crisis-v1-stable`에 해당한다. 복원 방법은 그 폴더의 `RESTORE.md`에 있다.
