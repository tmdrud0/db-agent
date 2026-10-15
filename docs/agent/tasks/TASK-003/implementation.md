# TASK-003 구현 보고서

작업 ID: TASK-003
명세 버전: 1
대상 코드 버전: 5f881812347dc26d0a30165be365da9f5d60c048
상태: ready_for_review
요약: 공식 정보 3종(schema·전체 컬럼 설명·전체 지식)을 세션당 한 번씩 조회해 프롬프트에 넣고 Phase 1 모델 호출·제출을 한 번씩 수행한다. Phase 1 통과·후속 있음·잔액 3 이상이면 사용자 시뮬레이터 전환 1회 뒤 같은 정보·Phase 1 SQL·공식 결과·후속 질문으로 Phase 2 모델 호출·제출을 한 번씩 수행한다. 조회·제출 비용은 자체 state에서 차감하고 실행하지 않은 행동은 호출·차감하지 않으며 잔액은 음수로 내려가지 않는다(공식 무료 제출 예외 미사용).
근거: `agent/environment.py`, `agent/loop.py`, `agent/server.py`, `scripts/run-evaluation.ps1`, `tests/test_connection.py`, `results/deepseek_v41_flash_two_phase_calibrated_smoke.json`, `results/oracle_two_phase_calibrated_smoke.json`.
미확정 사항: 실제 Phase 2 실행은 이번 1문제가 Phase 1 오답으로 끝나 미실행이며 AC-3의 모의 검증과 구분한다. 30문제·원본 기본 스택·다중 동시성 실행은 하지 않았다.

## 커밋과 검토 범위

기준 커밋: 05b9fa1e7aac7fd43acde3c2a41bd0c83e808e87
코드 커밋: 5f881812347dc26d0a30165be365da9f5d60c048
작업 커밋 목록:
- 6a195e3d853b649fbf15b97dff2d455e3edc4bd0 — 명세 `docs/agent/tasks/TASK-003/spec.md` (조율자 문서 커밋)
- 5f881812347dc26d0a30165be365da9f5d60c048 — 코드·테스트
- 보고서 커밋 — `docs/agent/tasks/TASK-003/implementation.md` (별도 문서 커밋)

검토 명령: `git diff 05b9fa1e7aac7fd43acde3c2a41bd0c83e808e87 5f881812347dc26d0a30165be365da9f5d60c048 --`
현재 코드와 대상 SHA의 차이: 없음. 코드 커밋 직후 `git status --short` 출력이 없고, 커밋 이후 제품 코드·테스트를 수정하지 않았다.
기존 사용자 변경: 없음. 검토 범위의 문서 변경은 명세 커밋 1건뿐이다.

## 변경 내용

### 정보 조회·전환 — `agent/environment.py`

- `fetch_schema`·`fetch_column_meanings`·`fetch_knowledge`는 `POST /schema`·`/all_column_meanings`·`/knowledge`에 본문 `{"task_id": ...}`만 보내고 응답 필드(`schema`, `column_meanings`, `knowledge`) 문자열을 가공 없이 반환한다. 필드 누락·타입 불일치·HTTP 오류·비 JSON 응답은 라벨이 붙은 `GradingError`로 실패하며, 빈 정보로 위장하지 않는다.
- `transition_to_phase2`는 사용자 시뮬레이터(`USER_SIM_URL`, 기본 `http://127.0.0.1:6001`) `POST /phase_transition`을 1회 호출하고 `status == "ok"`를 확인한다.
- `_post`는 서비스별 라벨로 오류 메시지를 구분한다(Schema fetch, Column meanings fetch, Knowledge fetch, Phase transition, Grader). 제출 응답 계약 검사(passed면 phase_completed 1/2 필수)는 유지한다.

### 흐름·코인·기록 — `agent/loop.py`

- 고정 흐름: 입력 검증 → 3개 조회(고정 순서: schema → all_column_meanings → knowledge) → Phase 1 모델 호출·제출 → 통과·후속 있음이면 전환 → Phase 2 모델 호출·제출 → 종료.
- 프롬프트: Phase 1은 DB 이름·문제·세 조회 결과를 구분해 포함한다. Phase 2는 원래 문제, 같은 세 조회 결과, Phase 1 제출 SQL, 공식 결과 JSON, 후속 질문을 넣고 "Phase 1 SQL이 이미 적용된 뒤의 후속"임을 명시한다. state 전체 직렬화나 로컬 문제·GT·테스트 파일 사용은 없다.
- 코인: 조회 각 1, 제출 각 3을 자체 state에서 차감한다. 조회 전 잔액<1이면 조회 없이, 조회 후 잔액<3이면 Phase 1 호출·제출 없이, Phase 1 통과 후 잔액<3이면 Phase 2 호출·제출 없이 종료한다. 잔액이 정확히 3이면 제출하며 제출 후 0이 정상 종료값이다. 음수·무료 제출 없음. 모델 생성 실패로 제출하지 않았다면 제출 비용을 차감하지 않는다.
- 기록: `tool_trajectory`에 조회/제출마다 시도한 도구명·args(task_id 또는 sql)·결과 또는 오류·cost·budget_before/after를 남긴다. `adk_events`에 phase·call_index·모델·usage·설정(endpoint·timeout·max tokens)·시간·생성 SQL·실제 프롬프트를 남긴다.
- 종료: `stop_reason`은 `phase1_failed`·`phase1_completed`·`phase2_failed`·`phase2_completed`·`budget_exhausted_before_information`·`budget_exhausted_before_phase1`·`budget_exhausted_before_phase2`로 오답·코인 부족·완료를 구분한다. 보상은 공식 응답의 reward 증분을 합산한다. 오류는 stage(`model`·`information`·`grader`·`transition`)로 구분해 재호출 시 같은 오류를 다시 던지고 추가 호출을 만들지 않는다.

### 기준선 식별·실행 — `agent/server.py`, `scripts/`

- `AGENT_NAME`을 `deepseek-v4.1-flash-fixed-context-two-phase`로 바꾼다. 세션 API·lock·reset·worker 1개 동작은 그대로다.
- `scripts/run-evaluation.ps1`: 새 기준선 이름 검증, 기본 `-ResultName`을 `deepseek_v41_flash_two_phase`로 변경, `-Calibrated` 스위치 추가(보정 compose 병합, `/calibration` 신원 확인, 미지정 시 기본 입력 `private/lite30.calibrated.jsonl`). 원본 경로는 기본 compose와 vendor combined 데이터를 유지한다. 스택이 요청과 다르면(보정 서비스인데 `-Calibrated` 없음, 그 반대) 중단한다.
- `scripts/run-dev30.ps1`: 30문제 결과 이름을 `deepseek_v41_flash_two_phase_dev30`으로 바꾼다.

### 테스트 — `tests/test_connection.py`

- 모의 HTTP로 DB 서비스(3개 조회·제출)·사용자 시뮬레이터(전환)·모델(2회 호출)를 구성한다. 기존 Phase 1 전용 기대값(중단 사유·코인·기록)을 새 구조에 맞게 갱신하고, 예산 경계·Phase 2 흐름·오류 stage·동시성·캐시 검증을 추가해 전체 78개 테스트를 통과한다.

## 완료 조건별 근거

- AC-1: `test_fixed_information_round_and_phase1_prompt`가 3개 엔드포인트·task_id만 전달·조회 각 1회·비용 각 1, 세 응답의 Phase 1 프롬프트 포함, state의 임의 필드(`internal_note` 등) 비노출을 확인한다. `test_empty_information_values_are_used_as_returned`가 빈 지식 목록 `[]`·빈 컬럼 설명 `{}`의 그대로 사용을, `test_information_fetch_failures_are_explicit_and_stop_the_run`이 응답 계약 오류가 빈 정보로 대체되지 않음을 확인한다.
- AC-2: `test_phase1_failure_stops_with_one_model_call_and_one_submission`과 `test_phase1_success_without_follow_up_completes_the_task`가 1회 종료를, `test_official_result_and_model_call_are_recorded_in_state`가 제출 SQL·공식 응답·보상·상태 기록을, `test_fenced_dml_response_is_submitted_unchanged`가 제출 SQL 원형 유지를 확인한다.
- AC-3: `test_phase1_success_with_follow_up_runs_phase2`가 모델/제출 각 2회·전환 1회·조회 각 1회와 두 번째 프롬프트의 조회 정보·원래 문제·제출 SQL·공식 결과·후속 질문 포함을 확인한다. `test_phase2_failure_keeps_phase1_reward_and_stops`가 Phase 2 실패 시 Phase 1 보상·성공 유지와 무재시도를, `test_rewards_are_summed_from_official_responses`가 0.7/0.3 직접 부여 없이 증분 합산(0.6+0.15=0.75)을 확인한다. `LoopFailureStateTests.test_transition_failure_keeps_the_phase1_result`가 전환 오류 시 Phase 2 미진행을 확인한다.
- AC-4: `test_no_budget_stops_before_the_information_fetches`(0코인), `test_budget_runs_out_during_the_information_fetches`(2코인, 2회 조회 후 중단), `test_budget_below_the_submission_cost_stops_after_the_fetches`(3·4코인), `test_budget_of_six_allows_phase1_then_stops_before_phase2`(6코인), `test_budget_of_nine_runs_both_phases`(9코인)가 미실행 행동의 무호출·무차감, 음수 없음, 무료 제출 없음, 경계값 동작을 확인한다. `LoopFailureStateTests.test_model_failure_does_not_charge_a_submission`이 제출 비용 미차감을 확인한다.
- AC-5: `test_failed_attempts_are_not_retried_without_a_reset`·`test_model_failure_is_not_retried_without_a_reset`·`test_repeated_run_returns_cached_outcome_without_extra_calls`·`test_reset_false_reuses_the_session_and_reset_true_starts_over`·`ConcurrentSessionTests`(동시 호출 중복 방지)·`test_information_fetch_failures_are_explicit_and_stop_the_run`·`test_grader_failures_are_explicit_and_never_faked`가 기존 오류·reset·종료 결과 캐시 동작 유지와 오류/오답 구분을 확인한다. 전체 78개가 통과한다.
- AC-6: 아래 실제 실행에서 보정 스택·동시성 1로 1문제를 실행해 3개 정보 조회 → 모델 생성 → 공식 제출 결과 수신을 확인했다. Phase 1이 오답이라 종료됐고 전환·Phase 2 호출이 없음을 사용자 시뮬레이터 상태로 교차 확인했다.
- AC-7: 이 보고서에 코드 SHA·명령·입력/환경·모델 설정·단계별 호출 수·비용·공식 결과·미검증 사항을 기록했다. 커밋에는 GT·API 키·인증 헤더·`.env`·`private/`·`results/`·`vendor/`·가상환경·로컬 설정이 포함되지 않았다(코드 커밋 파일 6개 확인).

## 실제 실행 (AC-6)

실행 명령(커밋 5f88181 이후, 작업 트리 깨끗):

```powershell
.\scripts\run-evaluation.ps1 -Calibrated -Limit 1 -Concurrency 1 `
  -ResultName 'deepseek_v41_flash_two_phase_calibrated_smoke' `
  -OracleResultName 'oracle_two_phase_calibrated_smoke'
```

- 환경: 보정 스택(compose.benchmark.yml + compose.benchmark.calibrated.yml), `/calibration`이 `db_environment_calibration`·`TASK-002.calibration.json`·eval_prep 3건·patch 1건 보고. 입력은 `private/lite30.calibrated.jsonl`(sha256 `55ca576e…dece3ce`) 첫 문제 `mental_1`(db `mental`, 후속 있음). 모델 `deepseek/deepseek-v4.1-flash`, 16384 토큰·300초·비스트리밍, `CMD_API_KEY`(.env에서 로드, 기록·출력 없음). 스크립트가 6000 포트에 새 기준선을 기동하고 health에서 `deepseek-v4.1-flash-fixed-context-two-phase`를 확인한 뒤 종료 시 정리했다.
- Oracle 양성 대조(모델 호출 없음): `mental_1` Phase 1·2 모두 pass, 보상 1.0, 오류 0.
- 에이전트: 정보 조회 3회(schema 12,909자 → column meanings 16,087자 → knowledge 24,479자, 각 1코인, 22→19), Phase 1 모델 호출 1회(프롬프트 54,194자, usage prompt 14,171·completion 7,617(reasoning 7,266)·total 21,788, 41.7초), 제출 1회(3코인, 19→16). 공식 응답은 `passed=false`, "SQL failed Phase 1. Your SQL is not correct.", 보상 0.
- 결과 상태: `phase1_completed=false`, `phase2_completed=false`, `task_done=false`, 보상 0, 예산 사용 6·남음 16, 문제 소요 44.6초, 후속 호출 없음. 사용자 시뮬레이터 `/debug_state/mental_1`이 `current_phase=1`로 전환이 호출되지 않았음을 보여준다.
- 결과 파일: `results/deepseek_v41_flash_two_phase_calibrated_smoke.json`·`.html`, `results/oracle_two_phase_calibrated_smoke.json`·`.html`(모두 gitignore 대상).
- 1문제 실행으로 정답률이나 Phase 2 실제 동작을 확인한 것이 아니며, 유료 호출을 반복하지 않았다. 보정 스택 컨테이너는 실행 후에도 기동 상태로 남겼고(사용자 운영 방식) 6000 에이전트 프로세스는 스크립트 finally에서 종료됐다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 커밋 5f88181과 같은 내용의 작업 트리, Windows/Python 3.12 | `Ran 78 tests`·`OK`, exit 0 | 모의 계약 검증(AC-1~AC-5) |
| `git diff --check` / staged diff 확인 | 코드 커밋 직전 | 공백 오류 0, staged 파일 6개 | 커밋 위생 |
| `scripts\run-evaluation.ps1 -Calibrated -Limit 1 -Concurrency 1 …` | 커밋 5f88181, 보정 스택, `mental_1`, 동시성 1 | exit 0, oracle 1.0, 에이전트 Phase 1 오답·6코인·미전환 | AC-6 |
| `GET http://127.0.0.1:6002/calibration` | 실행 직후 | `db_environment_calibration`, prep 3건·patch 1건 | 보정 스택 신원 |
| `GET http://127.0.0.1:6001/debug_state/mental_1` | 실행 직후 | `current_phase=1` | 전환 미호출 교차 확인 |
| 결과 JSON 판독(로컬 요약 스크립트) | 위 결과 파일 | 조회 3회·제출 1회·비용 6·프롬프트에 세 조회 포함 | 단계별 호출·비용·기록 |

검증 코드와 최종 SHA 일치 근거: 계약 테스트는 마지막 코드 수정(loop.py 줄바꿈 정리) 이후·커밋 직전 작업 트리에서 실행했고, 그 내용이 그대로 커밋 5f88181이 됐다. 커밋 후 `git status --short`·`git diff --check`에 변경이 없고, 실제 평가도 커밋 이후 깨끗한 트리에서 스크립트가 같은 내용의 에이전트를 기동해 수행했다. 검증·판독에 쓴 미추적 코드는 없으며, 결과 판독용 요약 스크립트는 gitignore된 `.runtime-tmp`의 로컬 보조 도구로 제품 코드가 아니다.

## 미검증 항목과 제한

- 실제 Phase 2 실행: `mental_1`이 Phase 1 오답으로 끝나 전환·Phase 2가 실제 DB에서 실행되지 않았다. AC-3의 모의 검증과 구분하며, 실제 Phase 2 경로의 확증으로 쓰지 않는다.
- 실제 문제 1건·동시성 1만 실행했다. 30문제·전체 300문제·다른 동시성·원본 기본 스택 실행은 하지 않았고 모델 정답률 목표를 두지 않는다.
- 원본 스택의 기존 30문제 결과(과거 기록)는 이번 코드로 재실행하지 않았다.
- `-Calibrated` 실행 중 스크립트 stdout은 백그라운드 래퍼 로그에 남지 않았고(빈 파일), exit code·결과 파일·서비스 상태·시뮬레이터 상태로 성공을 확인했다.
- `CURRENT_DATE` 의존 등 TASK-002 보정 제한은 그대로다.
- `README.md`(48·77행)와 `docs/setup.md`(70·77행)의 기본 결과 이름이 아직 `deepseek_v41_flash_phase1`로 남아 있다. 보고서 외 문서 수정 제한에 따라 갱신하지 않았고 후속 문서 갱신이 필요하다.

## 리뷰 지적 처리

해당 없음(최초 검토).

## 필요한 판단

없음.
