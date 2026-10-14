# TASK-001 구현 보고서

작업 ID: TASK-001
명세 버전: 1
대상 코드 버전: c5f8f8aa1dc3236b128a8555df918e7fbce20c4a
상태: ready_for_review
요약: 빈 SQL 제출 스텁을 Command Code `deepseek/deepseek-v4.1-flash` 기준선으로 교체했다.
한 세션에서 모델 호출 1회와 공식 `/submit` 1회만 수행하고, 공식 응답으로 상태를 기록한 뒤
Phase 2를 호출하지 않고 멈춘다. 모델·채점 오류는 정상 오답과 구분해 반환하고 실패한 시도는
같은 세션에서 재시도하지 않는다. 실제 키로 한 문제와 고정 30문제를 실행해 호출·제출·채점
결과 수신을 확인했다.
근거: `agent/model.py`, `agent/loop.py`, `agent/environment.py`, `agent/server.py`,
`tests/test_model.py`, `tests/test_connection.py`, `scripts/run-evaluation.ps1`,
`scripts/run-dev30.ps1`, `README.md`, `docs/setup.md`,
`results/deepseek_v41_flash_phase1_smoke.json`, `results/deepseek_v41_flash_phase1_dev30.json`.
미확정 사항: 없음.

## 커밋과 검토 범위

기준 커밋: 560c04c7c4cb0c42bbcd85e1e34721d59f04157c
코드 커밋: c5f8f8aa1dc3236b128a8555df918e7fbce20c4a
작업 커밋 목록: `aa6b527`(기준선 구현), `3643101`(dev30 결과 이름), `c5f8f8a`(추론 토큰 상한 수정),
`b83d9ed`(1차 보고서 문서 커밋)
검토 명령: `git diff 560c04c7c4cb0c42bbcd85e1e34721d59f04157c c5f8f8aa1dc3236b128a8555df918e7fbce20c4a --`
현재 코드와 대상 SHA의 차이: 없음. 코드 커밋 이후 `git diff HEAD --stat`이 비어 있었다.
미커밋 항목은 조율자 명세 `docs/agent/tasks/TASK-001/spec.md`(untracked, 이번 작업에서 작성하지 않아
커밋하지 않음)와 `.gitignore` 대상 파일(`.env`, `results/`, `.runtime-tmp/`)뿐이다.
기존 사용자 변경: 없음. 명세 작성 전 기준으로 미커밋 제품 코드 변경은 없었다.

## 변경 내용

- `agent/model.py`(신규): 비스트리밍 Chat Completions 호출 1회. 엔드포인트·모델 ID·timeout·
  출력 토큰 상한을 상수로 고정하고 `CMD_API_KEY`를 호출 시점에 읽는다. 응답은
  `choices[0].message.content`에서만 SQL을 얻고, 응답 전체를 감싸는 단일 코드 펜스와 앞뒤
  공백만 제거한다. 키 누락, HTTP 오류·timeout, 잘못된 JSON, choices 누락, 빈 content,
  `finish_reason == "length"`(잘림)를 `ModelError`로 구분한다. 대체 모델·재시도·추론 설정·
  reasoning/tool call 복구는 넣지 않았다.
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
- `scripts/run-dev30.ps1`: 30문제 결과 이름을 `deepseek_v41_flash_phase1_dev30`으로 바꿔
  이전 빈 SQL 기준선 결과와 구분한다.
- `README.md`, `docs/setup.md`, `requirements.benchmark-smoke.txt`: API 키 설정 방법,
  엔드포인트·모델·timeout·토큰 상한, Phase 1 기준선의 호출·제출 횟수, 한 문제 실행과 결과
  JSON 확인 항목, Phase 2 점수를 이 기준선의 지표로 쓰지 않는다는 설명을 반영했다.

## 완료 조건별 근거

- AC-1: 모델 HTTP 모의 테스트에서 `POST https://api.commandcode.ai/provider/v1/chat/completions`,
  `Bearer <키>`, `model=deepseek/deepseek-v4.1-flash`, `stream=false`,
  `max_tokens=16384`, system/user 메시지 구성을 확인한다(`test_request_matches_the_provider_contract`).
  state의 임의 필드(`internal_note=do-not-send`)가 요청 본문에 들어가지 않음은
  `test_model_request_carries_only_the_question_and_database`로 확인한다.
- AC-2: 코드 펜스로 감싼 SQL이 펜스만 제거된 채 `/submit`에 그대로 전달되고(변경 SQL 포함,
  차단 없음) 공식 결과가 state·응답에 기록됨을 확인한다(`test_fenced_dml_response_is_submitted_unchanged`,
  `test_official_result_and_model_call_are_recorded_in_state`). 빈 content와 잘린 응답은 제출 없이
  실패로 기록한다(`test_generation_failures_never_reach_the_grader`). 실제 실행에서도 생성 SQL이
  수정 없이 제출되었고 공식 응답 원문이 `tool_trajectory`에 남았다.
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
  `reset: true`일 때만 새 시도가 허용된다. 실제 30문제에서도 문제당 모델 호출 1회, 제출 1회,
  `budget_used=3.0`으로 동일했다(30/30).
- AC-5: 키 누락(`test_missing_api_key_is_reported_and_makes_no_request`,
  `test_missing_api_key_fails_before_any_request`), 모델 HTTP 오류·timeout·잘못된 응답
  (`test_model_http_errors_and_timeouts_are_explicit`,
  `test_invalid_or_incomplete_payloads_are_generation_failures`), 채점 HTTP·응답 계약 오류
  (`test_grader_failures_are_explicit_and_never_faked`)가 명시적 오류로 반환된다. 모델 실패 시
  제출 0회, 오류·state에 키가 남지 않음도 함께 확인한다. 추론 토큰이 상한을 소모한 실제 잘림도
  제출 없이 502("Model service error")로 반환됐다(아래 "추가 확인" 1번).
- AC-6: `test_health_identifies_the_phase1_baseline`과 실제 uvicorn 기동 확인, 기본 결과 이름
  `deepseek_v41_flash_phase1`·30문제 `deepseek_v41_flash_phase1_dev30`, `adk_events`·
  `tool_trajectory` 기록 검증으로 기준선 식별과 호출·SQL·공식 결과 기록을 확인한다.
- AC-7: 충족. 사용자 키가 설정된 환경에서 실제 한 문제를 동시성 1로 실행했다.
  - 문제 ID: `alien_1` (db `alien`), 결과 파일 `results/deepseek_v41_flash_phase1_smoke.json`
  - 실행 명령(키는 `.env`에서 읽어 프로세스 환경 변수로만 주입, 값은 출력·기록하지 않음):

    ```powershell
    $env:CMD_API_KEY = '<키>'   # 또는 .env의 CMD_API_KEY= 줄을 읽어 설정
    .\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1 `
        -ResultName 'deepseek_v41_flash_phase1_smoke' -OracleResultName 'oracle_phase1_smoke'
    ```

  - 모델·요청 설정: `deepseek/deepseek-v4.1-flash`, `stream: false`, timeout 300초,
    `max_tokens` 16384, reasoning 파라미터 없음
  - 호출·제출 근거: `adk_events`의 `model_call` 1건(`response_model=deepseek/deepseek-v4.1-flash`,
    usage `prompt_tokens=111`, `completion_tokens=7253`, 그중 `reasoning_tokens=7157`,
    `elapsed_seconds=51.1`), `tool_trajectory`의 `submit_sql` 1건(비용 3.0, 예산 18→15).
    스크립트 종료 코드 0, `error` 항목 없음, 문제당 제출 1회·Phase 2 제출 0회.
  - 공식 결과: `passed=false`, `reward=0.0`, `phase_completed=null`,
    메시지 `SQL failed Phase 1. [exec_err_flg] Error executing submitted SQL:
    relation "atmospheric_conditions" does not exist ...`. 스키마 정보 없이 생성한 SQL이
    존재하지 않는 테이블을 참조한 정상적인 오답이며 인프라 오류가 아니다.
    같은 입력의 oracle은 Phase 1·2 모두 통과(`average_reward=1.0`)해 환경이 정상임을 확인했다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | 코드 SHA `c5f8f8a`, Python 3.12 venv, Windows | Ran 31 tests, OK (exit 0) | 모델·채점 모의 계약과 세션·예산·오류·동시성 검증 |
| `uvicorn agent.server:app` 기동 후 `GET /health` | 코드 SHA `aa6b527`, 모델 호출 없음 | `{"status":"healthy","service":"system_agent","agent":"deepseek-v4.1-flash-phase1-baseline","model":"deepseek/deepseek-v4.1-flash"}` | 실제 서버 기동과 health 표시 |
| `.\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1` (실제 키, 1문제) | 코드 SHA `c5f8f8a`, 모델 1회 호출 | exit 0, 모델 호출 1·제출 1·Phase 2 제출 0, 공식 결과 수신, `error` 없음 | AC-7 근거(위 완료 조건) |
| `.\scripts\run-dev30.ps1` (실제 키, 고정 30문제, 동시성 10) | 코드 SHA `c5f8f8a`, 모델 30회 호출 | exit 0, 오류 0/30, 문제당 모델 1회·제출 1회, Phase 1 0/30 | 추가 확인 2번 |
| `git diff --check` | 코드 SHA `c5f8f8a` | 출력 없음(exit 0) | 공백 오류 없음 |
| `git diff HEAD --stat`, `git diff --cached --stat` | 코드 SHA `c5f8f8a` | 출력 없음 | 검증한 작업 트리 = 대상 SHA |
| PowerShell 파서 검사 `Parser::ParseFile` | `run-evaluation.ps1`, `run-smoke.ps1`, `run-dev30.ps1` | 3개 모두 syntax OK | 수정 스크립트 문법 |
| `git status --porcelain=v1` | 코드 커밋 후 | `?? docs/agent/tasks/`만 존재 | 금지 파일·결과물 미커밋 |

검증 코드와 최종 SHA 일치 근거: 모의 테스트와 문법 검사는 `c5f8f8a` 커밋과 같은 내용의 작업 트리에서
실행했고, 실제 두 실행은 `c5f8f8a` 커밋 이후 변경 없는 작업 트리에서 수행했다(실행 전후
`git diff HEAD` 비어 있음). 검증에 사용한 파일은 모두 해당 커밋에 포함되어 있다.
`results/`, `private/`, `.env`, GT 파일은 생성·커밋하지 않았고 diff에 없다. 키와 인증 헤더는
state·결과·오류·로그·보고서에 남기지 않았다(모의 테스트로도 확인).

## 추가 확인

1. 추론 토큰과 출력 상한: 첫 실행에서 `max_tokens=2048`이 추론 토큰만으로 소모되어
   `finish_reason=length`, `content` 없음이 발생했고, 구현이 규약대로 제출 없이 502
   ("Model service error")로 반환했다. 실제 문제(`alien_1`)로 측정한 결과 `max_tokens=8192`에서
   `finish_reason=stop`, `completion_tokens=4227`(추론 4132)로 정상 SQL이 생성됐다.
   상한을 16384, timeout을 300초로 올린 뒤(`c5f8f8a`) 1문제와 30문제 모두 잘림 없이 끝났다.
2. 고정 30문제 실제 실행(사용자 요청, `results/deepseek_v41_flash_phase1_dev30.json`):
   - oracle: Phase 1 30/30, Phase 2 26/30, 평균 보상 0.96 — 이전 빈 SQL 기준선의 환경 확인과 동일.
   - 자체 에이전트: 평균 보상 0.00, Phase 1 0/30, `error` 0건, 문제당 모델 호출 1회·제출 1회.
   - 모델 호출 시간: 최소 4.8초, 최대 109.4초, 합계 1731.4초. completion 234,370토큰 중
     추론 231,648토큰(98.8%).
   - 오답 유형: SQL 실행 오류 28건(존재하지 않는 테이블 추정), 실행 후 테스트 실패 2건,
     타임아웃 0건. 스키마 탐색 도구가 없는 기준선의 예상된 결과이며 정답률 목표는 두지 않았다.
   - 이 수치는 기준선의 첫 측정값이다. Phase 2 점수는 이 기준선의 개선 지표로 쓰지 않는다.

## 미검증 항목과 제한

- `scripts/verify-smoke.py`는 2026-10-14 빈 SQL 연결 검증 기록용으로 `results/empty_submit.json`과
  빈 SQL 제출을 전제로 한다. 이번 명세 범위(run-evaluation.ps1과 래퍼)가 아니어서 수정하지 않았고,
  새 기준선에서는 그대로 실행할 수 없다. 필요하면 후속 작업에서 갱신하거나 제거해야 한다.
- 전체 300문제 실행과 다중 워커 확장은 확인하지 않았다. 이번 실행은 동시성 10, Uvicorn worker 1개다.
- Phase 2 풀이·후속 문제 처리·실패 SQL 수정은 명세 범위에서 제외되어 구현하지 않았고 검증하지 않았다.
- 공식 집계에서 오류 항목은 `total_reward: 0`으로 들어갈 수 있다. 이번 두 실행은 모두 오류 0건이었고,
  스크립트는 `error` 필드가 있으면 예외를 발생시켜 정상 0점과 섞이지 않게 한다.

## 리뷰 지적 처리

해당 없음(최초 구현 검토).

## 필요한 판단

없음. 참고로 `docs/agent/status.md`와 README의 평가 수치 스냅샷 갱신 여부는 조율자 판단으로 남긴다.
