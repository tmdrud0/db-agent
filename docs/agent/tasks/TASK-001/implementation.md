# TASK-001 구현 보고서

작업 ID: TASK-001
명세 버전: 1
대상 코드 버전: aa6b527a8aab157ec7aa6b5dbb5719d488a5b575
상태: needs_decision
요약: 빈 SQL 제출 스텁을 Command Code `deepseek/deepseek-v4.1-flash` 기준선으로 교체했다.
한 세션에서 모델 호출 1회와 공식 `/submit` 1회만 수행하고, 공식 응답으로 상태를 기록한 뒤
Phase 2를 호출하지 않고 멈춘다. 모델·채점 오류는 정상 오답과 구분해 반환하고 실패한 시도는
같은 세션에서 재시도하지 않는다.
근거: `agent/model.py`, `agent/loop.py`, `agent/environment.py`, `agent/server.py`,
`tests/test_model.py`, `tests/test_connection.py`, `scripts/run-evaluation.ps1`,
`README.md`, `docs/setup.md`.
미확정 사항: AC-7 실제 키 실행 미검증. 이 환경에 `CMD_API_KEY`가 없고 `.env`도 없어
실제 모델 호출·DB 채점을 실행하지 못했다(아래 "미검증 항목과 제한" 참조).

## 커밋과 검토 범위

기준 커밋: 560c04c7c4cb0c42bbcd85e1e34721d59f04157c
코드 커밋: aa6b527a8aab157ec7aa6b5dbb5719d488a5b575
작업 커밋 목록: `aa6b527` (구현 코드·테스트·스크립트·문서)
검토 명령: `git diff 560c04c7c4cb0c42bbcd85e1e34721d59f04157c aa6b527a8aab157ec7aa6b5dbb5719d488a5b575 --`
현재 코드와 대상 SHA의 차이: 없음. `git diff HEAD --stat`과 `git diff --cached --stat`가
비어 있어 검증한 작업 트리 내용이 코드 SHA와 일치한다. 미커밋 항목은 조율자 명세
`docs/agent/tasks/TASK-001/spec.md`(untracked, 이번 작업에서 작성하지 않아 커밋하지 않음)뿐이다.
기존 사용자 변경: 없음. 명세 작성 전 기준으로 미커밋 제품 코드 변경은 없었다.

## 변경 내용

- `agent/model.py`(신규): 비스트리밍 Chat Completions 호출 1회. 엔드포인트·모델 ID·timeout·
  출력 토큰 상한을 상수로 고정하고 `CMD_API_KEY`를 호출 시점에 읽는다. 응답은
  `choices[0].message.content`에서만 SQL을 얻고, 응답 전체를 감싸는 단일 코드 펜스와 앞뒤
  공백만 제거한다. 키 누락, HTTP 오류·timeout, 잘못된 JSON, choices 누락, 빈 content,
  `finish_reason == "length"`(잘림)를 `ModelError`로 구분한다. 대체 모델·재시도·추론 설정·
  tool call 복구는 넣지 않았다.
- `agent/loop.py`: `state.user_query`(없으면 `/run_session`의 `message`)와 `state.db_name`만
  사용자 메시지로 구성한다. state 전체 직렬화나 로컬 문제 파일 접근은 없다. 모델 호출
  기록(요청·응답 모델, 호출 순번, 생성 SQL, usage, 설정값, 소요 시간)을 `adk_events`에,
  제출 인자·공식 응답·비용 3·예산 변화를 `tool_trajectory`에 남긴다. 모델 생성 실패에는
  제출 비용을 부과하지 않는다. Phase 1 성공에 후속 문제가 있으면 후속 질문만 기록하고
  `/phase_transition`과 Phase 2 제출을 하지 않는다. 실패한 시도는 `state["failure"]`로
  고정해 같은 세션의 재호출이 외부 호출을 반복하지 않게 했다.
- `agent/environment.py`: 공식 `/submit` 계약 유지. HTTP·JSON 오류와 응답 검증 실패
  (`passed`인데 `phase_completed`가 1/2가 아님)를 `GradingError`로 구분한다. 사용하지 않게 된
  `/phase_transition` 호출과 `USER_SIM_URL` 설정을 제거했다.
- `agent/server.py`: `/init_session`, `/run_session`, `/health`의 응답 구조와 상태 필드 유지.
  health는 `agent: deepseek-v4.1-flash-phase1-baseline`, `model: deepseek/deepseek-v4.1-flash`를
  보고하고 서버 제목도 기준선 이름을 쓴다. 오류는 입력 422, 모델 502("Model service error"),
  채점 502("Benchmark service error")로 출처를 구분하며 가짜 `passed: false` 결과를 만들지 않는다.
- `tests/test_model.py`(신규): 모델 요청 계약(엔드포인트·인증·모델 ID·`stream: false`·
  `max_tokens`·메시지 역할), SQL 추출 규칙, 키 누락·HTTP 오류·timeout·잘못된 응답·잘림,
  오류 메시지에 키가 남지 않음을 검증한다.
- `tests/test_connection.py`: 빈 SQL 기대값을 새 동작으로 교체하고 모델·채점 모의 계약,
  AC-3~AC-5 시나리오, 동일 세션 동시 요청(같은 이벤트 루프에서 `asyncio.gather`)을 추가했다.
- `scripts/run-evaluation.ps1`: 기본 결과 이름을 `deepseek_v41_flash_phase1`로 바꾸고,
  6000 포트의 기존 에이전트를 재사용할 때 health의 기준선 이름·모델이 일치하는지 확인한다.
  에이전트를 직접 시작할 때 `CMD_API_KEY`가 없으면 안내 메시지와 함께 중단한다.
- `README.md`, `docs/setup.md`, `requirements.benchmark-smoke.txt`: API 키 설정 방법,
  엔드포인트·모델·timeout·토큰 상한, Phase 1 기준선의 호출·제출 횟수, 한 문제 실행과 결과
  JSON 확인 항목, Phase 2 점수를 이 기준선의 지표로 쓰지 않는다는 설명을 반영했다.

## 완료 조건별 근거

- AC-1: 모델 HTTP 모의 테스트에서 `POST https://api.commandcode.ai/provider/v1/chat/completions`,
  `Bearer <키>`, `model=deepseek/deepseek-v4.1-flash`, `stream=false`,
  `max_tokens=2048`, system/user 메시지 구성을 확인한다.
  `tests/test_model.py::ModelRequestTests::test_request_matches_the_provider_contract`,
  state의 임의 필드(`internal_note=do-not-send`)가 요청 본문에 들어가지 않음은
  `tests/test_connection.py::SessionContractTests::test_model_request_carries_only_the_question_and_database`.
- AC-2: 코드 펜스로 감싼 SQL이 펜스만 제거된 채 `/submit`에 그대로 전달되고(변경 SQL 포함,
  차단 없음) 공식 결과가 state·응답에 기록됨을 확인한다.
  `test_fenced_dml_response_is_submitted_unchanged`,
  `test_official_result_and_model_call_are_recorded_in_state`.
  빈 content와 잘린 응답은 제출 없이 실패로 기록한다.
  `test_generation_failures_never_reach_the_grader`.
- AC-3: 오답(`test_official_result_and_model_call_are_recorded_in_state`),
  Phase 1 성공·후속 문제 있음(`test_phase1_success_with_follow_up_skips_phase2` — Phase 2 호출·제출 0회,
  `phase1_completed=true`, `phase2_completed=false`, `task_done=false`, 후속 질문 기록),
  성공·후속 없음(`test_phase1_success_without_follow_up_completes_the_task`),
  저예산(`test_low_budget_still_allows_the_final_submission`)을 모의 채점으로 검증한다.
  보상은 공식 응답 값만 더하고 추정하지 않는다.
- AC-4: 반복 실행(`test_repeated_run_returns_cached_outcome_without_extra_calls`),
  오류 후 재호출(`test_failed_attempts_are_not_retried_without_a_reset`,
  `test_model_failure_is_not_retried_without_a_reset`), 동일 세션 동시 요청
  (`ConcurrentSessionTests` 2건)에서 모델·제출 호출 횟수가 1회로 유지됨을 확인한다.
  `reset: true`일 때만 새 시도가 허용된다.
- AC-5: 키 누락(`test_missing_api_key_is_reported_and_makes_no_request`,
  `ModelRequestTests::test_missing_api_key_fails_before_any_request`),
  모델 HTTP 오류·timeout·잘못된 응답(`test_model_http_errors_and_timeouts_are_explicit`,
  `test_invalid_or_incomplete_payloads_are_generation_failures`),
  채점 HTTP·응답 계약 오류(`test_grader_failures_are_explicit_and_never_faked`)가 명시적 오류로
  반환된다. 모델 실패 시 제출 0회, 오류·state에 키가 남지 않음도 함께 확인한다.
- AC-6: `tests/test_connection.py::test_health_identifies_the_phase1_baseline`과 실제 uvicorn
  기동 확인, 기본 결과 이름 `deepseek_v41_flash_phase1`, `adk_events`·`tool_trajectory` 기록
  검증으로 기준선 식별과 호출·SQL·공식 결과 기록을 확인한다. 문서 명령과 실제 스크립트
  파라미터가 일치한다.
- AC-7: 미충족(미검증). 아래 "미검증 항목과 제한" 참조.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | 코드 SHA `aa6b527`, Python 3.12 venv, Windows | Ran 31 tests, OK (exit 0) | 모델·채점 모의 계약과 세션·예산·오류·동시성 검증 |
| `uvicorn agent.server:app` 기동 후 `GET /health` | 코드 SHA `aa6b527`, 모델 호출 없음 | `{"status":"healthy","service":"system_agent","agent":"deepseek-v4.1-flash-phase1-baseline","model":"deepseek/deepseek-v4.1-flash"}` | 실제 서버 기동과 health 표시(검증 후 프로세스 종료) |
| PowerShell 파서 검사 `Parser::ParseFile` | `run-evaluation.ps1`, `run-smoke.ps1`, `run-dev30.ps1` | 3개 모두 syntax OK | 수정 스크립트 문법 |
| `git diff --check` | 코드 SHA `aa6b527` | 출력 없음(exit 0) | 공백 오류 없음 |
| `git diff HEAD --stat`, `git diff --cached --stat` | 코드 SHA `aa6b527` | 출력 없음 | 검증한 작업 트리 = 대상 SHA |
| `git status --porcelain=v1` | 코드 SHA `aa6b527` | `?? docs/agent/tasks/`만 존재 | 금지 파일·결과물 미커밋 |

검증 코드와 최종 SHA 일치 근거: 모든 테스트와 기동 확인은 `aa6b527` 커밋 이후 작업 트리에서
실행했고 같은 시점의 `git diff HEAD`·`git diff --cached`가 비어 있음을 확인했다.
검증에 사용한 파일은 모두 해당 커밋에 포함되어 있다. 커밋 이후 실행한 검증에서 추적 파일
변경은 없었다. `results/`, `private/`, `.env`, GT 파일은 생성·커밋하지 않았고 diff에 없다.
모델 호출에 사용한 실제 설정값: timeout 120초, `max_tokens` 2048, `stream: false`,
추론 관련 파라미터 없음.

## 미검증 항목과 제한

- AC-7(실제 모델 1회 → 공식 제출 1회 → 채점 결과 수신)을 실행하지 못했다.
  이유: 이 실행 환경에 `CMD_API_KEY`가 없고 프로젝트에 `.env`도 없다. 모델 호출 없이는
  기준선의 핵심 동작을 확인할 수 없어 필요한 부분을 대체 실행으로 갈음하지 않았다.
  모델 모의 계약 테스트와 실제 서버 기동만으로는 AC-7을 충족으로 쓸 수 없다.
- 사용자 키가 준비되면 다음 한 명령으로 확인할 수 있다(키는 채팅·보고서에 남기지 않는다).

  ```powershell
  $env:CMD_API_KEY = '<Command Code API 키>'
  .\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1 `
      -ResultName 'deepseek_v41_flash_phase1_smoke' -OracleResultName 'oracle_phase1_smoke'
  ```

  기대 결과: 모델 호출 1회, 공식 제출 1회, Phase 2 제출 0회, 채점 응답 수신(정답 여부 무관).
  확인 위치: `results/deepseek_v41_flash_phase1_smoke.json`의 `adk_events`(model_call),
  `tool_trajectory`(submit_sql), `error` 필드.
- `scripts/verify-smoke.py`는 2026-10-14 빈 SQL 연결 검증 기록용으로 `results/empty_submit.json`과
  빈 SQL 제출을 전제로 한다. 이번 명세 범위(run-evaluation.ps1과 래퍼)가 아니어서 수정하지 않았고,
  새 기준선에서는 그대로 실행할 수 없다. 필요하면 후속 작업에서 갱신하거나 제거해야 한다.
- 공식 집계에서 오류 항목도 `total_reward: 0`으로 들어갈 수 있다. 스크립트는 기존대로
  각 결과의 `error` 필드가 있으면 예외를 발생시켜 정상 0점과 섞이지 않게 한다. 실제 30문제·300문제
  오류 건수는 이번에 측정하지 않았다.

## 리뷰 지적 처리

해당 없음(최초 구현 검토).

## 필요한 판단

- AC-7을 어떻게 처리할지 결정이 필요하다. 선택지는 (1) 사용자가 위 명령을 실행하고 결과 JSON을
  전달하면 구현자가 보고서에 기록하고 `ready_for_review`로 갱신, (2) 키·환경 문제로 실행이
  불가능하면 AC-7을 미검증으로 남긴 채 코드 리뷰를 진행하고 최종 판단을 사용자에게 넘김이다.
  권장안은 (1)이다. 코드·테스트·문서 검증은 `aa6b527`에서 완료되어 있어, AC-7 결과만 추가하면
  완료 조건 전체를 확인할 수 있다.
