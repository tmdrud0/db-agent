# TASK-002 구현 보고서

작업 ID: TASK-002
명세 버전: 1
대상 코드 버전: 377db5550bb5a32ec74d6463ddc1395d233a3e2d
상태: ready_for_review
요약: oracle Phase 2 실패 4건의 원인을 격리 DB에서 확정하고, 공식 파이프라인이 무시하는 공개 준비 필드(`preprocess_sql`, `clean_up_sqls`)를 평가 시점에 적용하는 선택적 보정 wrapper와 cross_db_M_5의 record 표현 typo 1건 데이터 보정으로 보정본 30문제 oracle을 Phase 1 30/30·Phase 2 30/30·평균 보상 1.0으로 만들었다. GT 3건은 수정하지 않았고 원본 입력·vendor 코드·기본 실행은 그대로다.
근거: `benchmark/calibration/`, `compose.benchmark.calibrated.yml`, `scripts/`, `tests/test_calibration.py`, `private/calibration/TASK-002.calibration.json`, `results/dev30_oracle_calibrated.json`.
미확정 사항: 없음.

## 커밋과 검토 범위

기준 커밋: 78d68541c6653c3d74776d135463f1afd8cdfd28
코드 커밋: 377db5550bb5a32ec74d6463ddc1395d233a3e2d
작업 커밋 목록:
- 377db5550bb5a32ec74d6463ddc1395d233a3e2d — 코드·테스트 (`[TASK-002] feat: calibrate the four oracle Phase 2 failures`)
- 보고서 커밋 — `docs/agent/tasks/TASK-002/implementation.md` (별도 문서 커밋)
검토 명령: `git diff 78d68541c6653c3d74776d135463f1afd8cdfd28 377db5550bb5a32ec74d6463ddc1395d233a3e2d --`
현재 코드와 대상 SHA의 차이: 없음. 검증 후 코드·테스트를 수정하지 않았고, 코드 커밋 직후 작업 트리는 깨끗하다(`git status --short` 출력 없음). 대상 SHA 이후 추가된 것은 이 보고서뿐이다.
기존 사용자 변경: 없음. 기준 SHA 이후 문서 커밋 5건(7c1db1d, 2971419, 343e2ef, 8b6765e, 4d4cdde)이 검토 범위에 함께 포함되며, 그중 제품 코드 변경은 없고(2971419는 AGENTS.md·역할 지침), 실제 코드 변경은 이번 커밋이 전부다.

## 변경 내용

### 원인 확정

`/init_task` → `/submit`(Phase 1 GT) → `/submit`(Phase 2 GT)를 기본 스택에서 재현하고, virtual_M_4는 태스크 DB·스냅숏 DB를 psql로 직접 조회했다.

| 문제 | 공식 Phase 2 응답 | 확정한 원인 |
| --- | --- | --- |
| museum_M_4 | `[exec_err_flg] ... constraint "hist_sign_rating_check" ... already exists` | Phase 1이 만든 제약이 스냅숏에 남아 있는데 후속 문구는 “I have dropped this constraint”를 전제한다. 동일 이름 ADD가 실행 오류가 되어 테스트 전에 실패한다. |
| news_M_4 | `[exec_err_flg] ... relation "idx_users_user_preferences_interests" already exists` | Phase 1 GIN 인덱스가 스냅숏에 남아 후속 GT의 동일 이름 CREATE가 실행 오류가 된다. 수정(재생성) 전제가 DB에 없다. |
| cross_db_M_5 | `[exec_err_flg] ... operator does not exist: numeric / record` | 후속 GT 분모가 `(ac.findtally + 1, 0)`으로 record 표현이 되어 AFS 연산이 성립하지 않는다. Phase 1 정의 `(ac.findtally + 1)`과 불일치. |
| virtual_M_4 | `SQL failed Phase 2. Your SQL is not correct.` | ① `fans_bak`이 어느 DB에도 없다(`to_regclass` 결과 없음, `preprocess_sql` 미적용) → 후속 테스트가 내부 예외로 실패해 원인이 메시지에 드러나지 않는다. ② Phase 2 시작 DB에서 `fans`가 복원되지 않는다(Phase 1 후 799행, churn 0행) → 재삭제 결과가 0이 되어 테스트가 `fans_bak` 조인으로 계산한 194와 비교된다. |

virtual_M_4 실측 근거(2026-10-14): 초기 `fans` 993행, Phase 1이 고유 194계정 삭제 후 799행, 스냅숏도 799행, `fans_bak` 없음, `fans_bak` 조인 `COUNT(*)`와 `COUNT(DISTINCT userregistry)`는 각각 194로 중복 조인 행 없음.

### 보정 방식

공식 코드는 공개 데이터의 `preprocess_sql`·`clean_up_sqls`를 실행하지 않는다. museum_M_4·news_M_4·virtual_M_4의 후속 전제는 이 공개 필드에 이미 정의되어 있으므로, GT를 고치지 않고 평가 환경에 적용했다. cross_db_M_5만 데이터로 해결되지 않아 보정 입력에서 분모 표기를 고쳤다.

- `benchmark/calibration/db_env_calibration.py`: 공식 `db_environment.server`를 import해 `create_task_db`/`reset_task_db`만 감싼다(vendor 파일 수정 없음, `git status` 깨끗). 태스크 DB를 만들거나 스냅숏에서 초기화한 직후 해당 단계의 준비 SQL을 실행한다. Phase 1→2 전환 시점(스냅숏 생성 직후)에는 살아 있는 태스크 DB에도 Phase 2 준비를 적용해, 후속 질문이 전달된 순간부터 전제가 참이 되게 한다. Uvicorn factory(`db_env_calibration:create_app`)로만 노출한다.
- `benchmark/calibration/calibration_config.py`: 설정 검증, 준비 SQL 참조 해석(`{"field": ...}` → 공개 준비 필드), 보정 패치 적용(문자열 1회 치환·`set`), `/execute` 결과 파싱. GT·기대값은 담지 않는다.
- `compose.benchmark.calibrated.yml`: 보정 스택을 명시적으로 선택한다. 기본 `compose.benchmark.yml`은 수정하지 않았고 vendor 마운트도 그대로 병합된다.
- `scripts/build_calibrated_set.py`: 고정 30문제 원본과 private 보정 설정으로 `private/lite30.calibrated.jsonl`을 만들고 원본·설정·출력 해시와 변경 요약을 `private/lite30.calibrated.manifest.json`에 남긴다. 선언되지 않은 변경·존재하지 않는 경로·치환 대상 불일치는 중단한다.
- `scripts/probe_oracle_submissions.py`: 지정 문제의 GT를 공식 `/submit`으로 제출해 응답을 출력한다. 기본 스택 원인 재현과 보정 스택 단일 문제 확인에 쓴다.
- `scripts/verify_calibration.py` + `scripts/verify-calibration.ps1`: `/calibration`으로 보정 서비스 신원을 확인한 뒤 private 설정의 시나리오(GT 제출, 반례 제출, DB 상태 조회)를 실행한다.
- `scripts/run-dev30-calibrated.ps1`: 보정 스택 기동 → oracle 30문제 실행 → 오류·Phase 카운트·평균 보상을 검사하고 실패 시 중단한다.
- `tests/test_calibration.py`: 설정 검증·패치 적용·준비 SQL 해석·wrapper 배선·요약 비노출·기본 compose 보존을 확인하는 31개 테스트.
- `private/calibration/TASK-002.calibration.json`(미커밋): 보정 대상 4건의 준비 참조, cross_db 치환 1건, 검증 시나리오와 기대값.

보정 입력은 원본과 다른 이름으로 저장했고, 원본 30문제와의 차이는 `cross_db_M_5.follow_up.sol_sql`의 `, 0` 제거 1곳뿐임을 필드 단위 비교로 확인했다. 그 외 29문제 행은 객체 동일성(`is`)까지 유지된다.

## 완료 조건별 근거

- AC-1: 기본 스택에서 4문제 모두 Phase 1 통과·Phase 2 실패를 재현하고 공식 메시지를 기록했다(위 표, 2026-10-14 재확인). virtual_M_4는 실패 위치와 복원·백업·중복 집계 상태까지 조회로 확정했다.
- AC-2: 보정 스택에서 `scripts/probe_oracle_submissions.py --data private/lite30.calibrated.jsonl`의 최초 제출로 4문제 모두 Phase 1 0.7 + Phase 2 0.3 = 1.0을 실제 DB에서 확인했다. 모델 API는 호출하지 않았다. 실행 오류(`exec_err_flg`)·연결 오류·테스트 실패는 응답 메시지로 구분된다.
- AC-3: `scripts/verify_calibration.py` 29개 항목 전부 통과(실패 0). 반례 4건이 모두 거부됐다 — museum은 1~10 범위 제약(새 값 4 허용), news는 전체 JSON 인덱스, cross_db는 다른 AFS 임계값(0.6 → 152 ≠ 179), virtual은 잘못된 개수 193. 의미 확인도 함께 통과했다 — museum 5~10 NOT VALID(`convalidated=False`)·기존 5 미만 366행 보존·거부된 INSERT 무흔적·허용 INSERT 저장, news `USING gin` + `user_preferences -> 'interests'` expression 인덱스 1건, cross_db Overdue·AFS>0.5 정확히 179건, virtual 고유 삭제 계정 194 = 결과 194, Phase 2 시작 시점 `fans` 993·`fans_bak` 993.
- AC-4: `scripts/run-dev30-calibrated.ps1 -Limit 30 -Concurrency 10` oracle이 `phase1_count=30`, `phase2_count=30`, `average_reward=1.0`, `error` 항목 0으로 끝났다. 기존 통과 26문제는 모두 1.0을 유지했다. 동일 조건 반복 실행의 문제별 결과가 완전히 동일해 상태가 섞이지 않았다.
- AC-5: 실행 전후 원본 입력 해시가 동일하다(아래 표). 보정 입력은 매번 같은 해시로 재생성된다. 기본 `compose.benchmark.yml`은 수정되지 않았고, 보정 스택을 기본 compose로 다시 올리면 `/calibration`이 404가 되어 원본 서비스로 복귀함을 확인했다.
- AC-6: 이 보고서에 코드 SHA, 입력 해시, 외부 버전, 실행 명령, 문제별 원인·결과·반례·제한을 기록했다. GT·테스트 본문·비밀값은 커밋에 포함되지 않았다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | 코드 커밋 377db55, 작업 트리 깨끗 | 62 tests OK | 기존 계약 테스트 31개 + 보정 테스트 31개 통과 |
| `python scripts\probe_oracle_submissions.py --data private\lite30.combined.jsonl --tasks museum_M_4,news_M_4,cross_db_M_5,virtual_M_4` | 기본 스택, 원본 입력 | 4건 Phase 1 통과·Phase 2 실패 | AC-1 재현, 응답 메시지가 위 표와 일치 |
| psql 조회(`virtual_template`, 태스크 DB, 스냅숏) | 기본 스택 | `fans_bak` 없음, 삭제 후 799행, 조인 194 = 고유 194 | virtual_M_4 원인 확정 |
| `docker compose ... -f compose.benchmark.calibrated.yml up --build -d --wait` | 보정 스택 기동 | 3개 서비스 healthy, `/calibration`에 eval_prep 3건·patch 1건 보고 | 보정 적용 확인 |
| `python scripts\verify_calibration.py` | 보정 스택, 보정 입력, 4문제 | `{"checks": 29, "failed": 0, "passed": true}` | AC-3 반례·의미 검증 |
| 동일 명령 + `private\lite30.combined.jsonl` | 보정 스택, 원본 입력 | museum·news·virtual Phase 2 통과, cross_db만 record 오류로 실패 | 환경 보정 3건·데이터 보정 1건의 원인 분리 |
| `python scripts\probe_oracle_submissions.py --data private\lite30.calibrated.jsonl --tasks ...` | 보정 스택, 보정 입력, 최초 제출 | 4문제 0.7 + 0.3 = 1.0 | AC-2 |
| `powershell .\scripts\run-dev30-calibrated.ps1 -Limit 30 -Concurrency 10` (2회) | 보정 스택, 동시성 10, 2026-10-14 | `phase1_count=30`, `phase2_count=30`, `average_reward=1.0`, 오류 0, 두 회차 문제별 결과 동일 | AC-4 |
| `python scripts\build_calibrated_set.py` (반복) | 원본 30문제 + private 설정 | 출력 해시 동일, 변경 1건(`, 0` 제거, 361→358자) | AC-5, `changed_tasks=["cross_db_M_5"]` |
| 필드 단위 비교(원본 vs 보정 30행) | 전체 필드 순회 | 차이 1곳, 나머지 행 동일 객체 | GT 3건·타 26문제 불변 확인 |
| `docker compose ... -f compose.benchmark.yml up` | 기본 스택 복귀 | `/calibration` 404, 원본 4문제 실패 재현 | 기본 실행 보존 |
| `git -C vendor/BIRD-Interact/BIRD-Interact-ADK status --short` / `rev-parse HEAD` | 검증 후 | 변경 없음, `451fe2c3518ee1cf908d8139e2913483bd519481` | vendor 코드 수정 없음 |

확인한 버전과 해시:

| 항목 | 값 |
| --- | --- |
| 기준 코드 SHA | `78d68541c6653c3d74776d135463f1afd8cdfd28` |
| 구현 코드 SHA | `377db5550bb5a32ec74d6463ddc1395d233a3e2d` |
| vendor BIRD-Interact | `451fe2c3518ee1cf908d8139e2913483bd519381` (clean) |
| 공개 Lite 데이터 | `f7881a9c2b9630cc4fc13b0c39279740b0a2fd87` (prepare-benchmark.ps1 고정) |
| 원본 30문제 입력 `private/lite30.combined.jsonl` | sha256 `2c02b9597d1a7e38ac5d7a961f40d37a871ea6d702133915997b133585628380` (실행 후에도 동일) |
| GT `bird_interact_gt_kg_testcases_1008.jsonl` | sha256 `92ead9c644d73d381f5b354bc11d7b6f59fcfa91e1baec3ea0fd1e3f26915762` (실행 후에도 동일) |
| vendor combined 데이터 | sha256 `089853d8366b8b7bce90cf0070fc8b6aa5fec138dca711e5484cfa132e80534d` (변경 없음) |
| 보정 설정(미커밋) | sha256 `62d513a4063cd462594d69a4fc69e2c983231ddaeb730c388909e1eab926e725` |
| 보정 입력 `private/lite30.calibrated.jsonl` | sha256 `55ca576ea1c2942b53aab5700c781c50e4cf1660dff2b25c9fdf24ac835de3ce`, canonical `4566edc449e6f89e463f4ee55df1db5ab2dc1d295fd12a972c7d39eb3c39c579` |
| 기존 30문제 oracle 결과(이전 기록) | `results/dev30_oracle.json` — Phase 1 30/30, Phase 2 26/30, 평균 0.96 |
| 보정 30문제 oracle 결과 | `results/dev30_oracle_calibrated.json`·`.html` — Phase 1 30/30, Phase 2 30/30, 평균 1.0 |

검증 코드와 최종 SHA 일치 근거: 위 테스트·평가·검증은 모두 커밋 377db55에 해당하는 작업 트리에서 실행했고, 그 시점 작업 트리에는 미커밋 코드·테스트가 없었다(추적 파일 변경 없음, 신규 파일은 커밋에 포함). 실행 중 생성된 태스크 DB·스냅숏은 `/cleanup_task`로 정리됐고 공유 베이스 DB와 템플릿은 변경되지 않았다. 결과 JSON·HTML과 private 입력은 모두 `.gitignore` 대상이다.

## 미검증 항목과 제한

- `CURRENT_DATE` 의존: cross_db_M_5(179/152), virtual_M_4(993/799/194) 기대값은 2026-10-14의 컨테이너 날짜 기준으로 측정했다. DB 데이터는 고정이지만 날짜가 지나면 조건을 만족하는 행이 늘 수 있으므로, private 설정의 기대값과 검증 결과는 재실행 시점의 날짜와 함께 해석해야 한다.
- 보정은 지정된 4건에만 적용된다. 전체 300문제 보정, 모델 기준선 개선, Phase 2 에이전트 루프 구현은 명세 범위 밖이어서 확인하지 않았다.
- 30문제 회귀는 동시성 10에서 2회만 확인했다. 다른 동시성 값과 장시간 반복은 확인하지 않았다.
- Phase 2 준비는 태스크 DB 생성·초기화 경로에서만 적용된다. 공식 서비스가 그 외 경로로 DB 상태를 바꾸는 경우(예: 외부에서 태스크 DB 직접 수정)를 가정한 검증은 하지 않았다.
- 에이전트 경로에서 후속 질문 수신 직후의 DB 상태는 전환 시점 준비 적용으로 확인했지만, 에이전트 Phase 2 풀이 루프 자체는 아직 구현되어 있지 않아 모델 관점의 사용성은 검증하지 않았다.

## 리뷰 지적 처리

해당 없음(최초 검토).

## 필요한 판단

없음.
