# TASK-007 구현 보고서

작업 ID: TASK-007
명세 버전: 1 (사용자 추가 요청 포함)
대상 코드 버전: 87f7b9f487dc9032105d102fd1fd6840a2b2a253
상태: ready_for_review
요약: fixed 고정 컨텍스트 루프와 모드 선택 경로를 제거하고 TASK-006의 9개 도구 선택 루프를 유일한 에이전트로 만들었다. 에이전트 서비스는 별도 설정 없이 tools 루프로 기동하고, 제거된 모드를 명시하면 ValueError로 거부한다. 실행기는 `-AgentMode` 없이 9개 도구·에이전트·시뮬레이터 검증을 항상 수행하며, 이전 실행기가 남긴 `AGENT_MODE`도 시작 전에 명확한 오류로 거부한다. 사용자 추가 요청으로 에이전트 Command Code 모델 호출은 최초 포함 최대 4회(재시도 3회)까지 재시도한다. 모드 옵션 없는 실제 보정 1문제 smoke에서 oracle 1.0, tools 정상 오답 0.0, 오류·failure 없음, 9개 도구·시뮬레이터 검증 통과를 확인했다.
근거: agent/server.py, agent/session.py, agent/model.py, scripts/run-evaluation.ps1, tests/test_tool_loop.py, tests/test_model.py, tests/test_evaluation_env.py. 삭제: agent/loop.py, tests/test_connection.py. 실행 산출물: results/oracle_smoke_task007.json, results/tools_smoke_task007.json(미커밋).
미확정 사항: 없음. 명세 v1의 "tools 동작 무변경" 제약 중 모델 실패 시도 횟수는 사용자 추가 요청으로 확장됐다(아래 7절). 아래 미검증 항목 참고.

## 커밋과 검토 범위

기준 커밋: 157a57a148bff68612f5b242e06c43a076250db3
코드 커밋: 87f7b9f487dc9032105d102fd1fd6840a2b2a253
작업 커밋 목록:
- 1f66d46f5e10bc50ba207824e586013994730620 — 최초 구현(fixed 제거·tools 단일화)
- 6840ff6 — 리뷰 R-1: 잔존 `AGENT_MODE` 시작 전 거부
- 87f7b9f — 사용자 추가 요청: 모델 호출 재시도 3회

검토 명령(누적): `git diff 157a57a148bff68612f5b242e06c43a076250db3 87f7b9f487dc9032105d102fd1fd6840a2b2a253 --`
리뷰 수정·추가 범위: `git diff 1f66d46f5e10bc50ba207824e586013994730620 87f7b9f487dc9032105d102fd1fd6840a2b2a253 --`

현재 코드와 대상 SHA의 차이: 없음. 최종 테스트 실행 시점과 현재 모두 `git diff --quiet HEAD` 통과(추적 파일 변경 없음)이며, 미추적 `docs/agent/tasks/TASK-007/`와 기존 results 산출물만 있다.
기존 사용자 변경: 30문제 평가 결과 `results/tools_dev30_calibrated_task006.*`, `results/oracle_dev30_calibrated_task006.*`는 수정·삭제하지 않았다. 해당 평가는 2026-10-20 09:40경 종료된 뒤 smoke를 실행했고, 09:53 smoke의 compose 기동으로 db-environment·user-simulator 컨테이너는 재생성됐지만 PostgreSQL 볼륨과 결과 파일은 그대로다. smoke는 새 결과 이름(`*_task007`)만 사용했다.

## 변경 내용

### 1. 단일 tools 모드 (agent/server.py, agent/loop.py 삭제)

- `agent/loop.py`의 fixed 고정 컨텍스트 루프(정보 3종 1회 → phase별 모델 1회·제출 1회)와 전용 프롬프트·상수를 삭제했다.
- `AGENT_NAMES`의 fixed 항목, `DEFAULT_MODE`, `RUNNERS`를 제거하고 `AGENT_NAME = "deepseek-v4.1-flash-tool-selecting-two-phase"`, `SUPPORTED_MODE = "tools"`만 남겼다.
- `/run_session`은 항상 `agent.tool_loop.run`을 호출한다. `/health`는 항상 위 이름, `mode: "tools"`, 9개 도구 목록·가격, execute_sql/ask_user timeout을 보고한다.
- `InputError`는 `agent.loop` 대신 정의 위치인 `agent.session`에서 import한다.
- `create_app(mode=...)`/`AGENT_MODE`는 단일 값 가드로만 남겼다. 미설정·빈 값·`tools`는 tools로 기동하고, `fixed`나 알 수 없는 값은 `ValueError("Unknown agent mode '<값>'; only 'tools' is supported")`로 명확히 거부한다.

### 2. fixed 전용 모델·세션 정리 (agent/model.py, agent/session.py)

- fixed 루프 전용이던 `SYSTEM_PROMPT`, `ModelReply`, `extract_sql`, `ModelClient.generate_sql`을 삭제했다.
- tools 루프가 쓰는 `API_URL`, `MODEL_ID`, `REQUEST_TIMEOUT_SECONDS`, `MAX_OUTPUT_TOKENS`, `ModelError`, `ChatReply`, `ModelClient.chat`은 유지했다. `_complete`는 7절의 재시도 외에 성공 경로 동작이 같다.
- `agent/session.py`는 tools 루프가 쓰는 `InputError`, `budget`, `resolve_query`, `database_line`, `finish`, `record_action`, `restore_failure`를 그대로 유지하고 모듈 설명만 단일 루프에 맞췄다.

### 3. 실행기 기본 tools (scripts/run-evaluation.ps1)

- `-AgentMode` 매개변수와 `$agentNames` 매핑, `$env:AGENT_MODE` 설정을 제거했다. 에이전트 이름은 상수로 두고, 옵션 없이 tools 에이전트를 기동·재사용한다.
- `Assert-BaselineAgent`는 mode=tools, 에이전트 이름, 모델, 9개 도구 이름·가격을 항상 검증한다. `Assert-Simulator`는 `user_simulator_command_code`·모델·v2·재시도 0·출력 상한 16384·키 존재를 항상 검증한다. fixed 분기에서 생략되던 검사가 사라지는 경로는 없다.
- `run-smoke.ps1`, `run-dev30.ps1`은 수정하지 않았다. 이제 이 래퍼들은 별도 옵션 없이 tools 에이전트를 실행하며, `CMD_API_KEY`와 시뮬레이터 모델 호출 환경(보정/기본 스택의 Command Code 어댑터)이 필요하다. `run-dev30-calibrated.ps1`은 oracle 전용이라 변경하지 않았다.

### 4. 테스트 재구성

- `tests/test_connection.py`(fixed 루프 기준 동작·세션 계약 31개)를 삭제했다. 그중 tools 루프에 없던 "동시 요청이 실패한 뒤 재시도하지 않음" 계약 1개를 `tests/test_tool_loop.py`의 `ToolLoopConcurrencyTests.test_simultaneous_requests_after_model_retries_are_exhausted_do_not_replay`로 옮겼다.
- `tests/test_model.py`는 fixed 전용 `extract_sql`·`generate_sql` 검증을 제거하고, tools 루프가 쓰는 `chat`의 요청 계약·키 부재·HTTP 오류/timeout·깨진 payload·빈/절단 응답·키 비노출 검증으로 재구성했다. 7절의 재시도 예산·일시 실패 복구·4xx 무재시도 검증을 추가했다.
- `tests/test_tool_loop.py`의 health 테스트를 단일 tools 모드 기준으로 바꾸고, `fixed`/알 수 없는 모드의 명시적 거부 테스트를 추가했다. 기존 tools 계약·예산·캐시·기한·동시성 테스트는 유지하고, 모델 실패 시도 횟수 기대값만 재시도 예산에 맞췄다.
- `tests/test_evaluation_env.py`의 `.env` 키 로딩 검증은 그대로 통과하며, 6절의 `AGENT_MODE` 가드 검증 3개를 추가했다.

### 5. 테스트 삭제·이전·유지 목록

| 구분 | 테스트 | 처리 |
| --- | --- | --- |
| 삭제 | `tests/test_connection.py` 31개 — `SessionContractTests` 25개(fixed 정보 라운드·프롬프트·phase 흐름·fixed 실패 state), `LoopFailureStateTests` 4개(fixed 실패 내부 state), `ConcurrentSessionTests` 2개 | fixed 전용이거나 `test_tool_loop.py`에 동일 세션 계약이 이미 있어 삭제 |
| 이전 | `ConcurrentSessionTests.test_simultaneous_requests_after_a_failure_do_not_retry` | `ToolLoopConcurrencyTests.test_simultaneous_requests_after_model_retries_are_exhausted_do_not_replay`로 이동 |
| 삭제 | `tests/test_model.py`의 `ExtractSqlTests` 4개 | fixed 전용 `extract_sql` 제거에 따라 삭제 |
| 재구성 | `tests/test_model.py`의 fixed `generate_sql` 요청 테스트 5개 | `chat` 기준 테스트로 대체하고, 재시도 2개를 추가해 최종 8개 |
| 유지·보강 | `tests/test_tool_loop.py` 53 → 55개 | health를 단일 모드로 수정, 제거 모드 거부·동시 실패 무재시도 추가. 모델 실패 횟수 기대값만 갱신 |
| 추가 | `tests/test_evaluation_env.py` 4 → 7개 | 잔존 `AGENT_MODE` 허용·거부·환경 보존 검증 3개 |
| 유지 | `test_calibration` 31, `test_command_code_simulator` 18, `test_dev_set` 4, `test_verify_calibration` 3, `test_verify_tool_connection` 5 | 변경 없음 |
| 합계 | 158 → 131 | 삭제 31 + `test_model` 순감 1 + tools 테스트 증가 2 + 환경 가드 3 |

### 6. 리뷰 R-1 수정: 잔존 AGENT_MODE 거부 (scripts/run-evaluation.ps1)

- `Assert-AgentModeEnvironment`를 추가해, `AGENT_MODE`가 비어 있지 않고 `tools`가 아니면 컨테이너·에이전트를 시작하기 전에 중단한다. 오류 메시지에 실제 값과 `Remove-Item Env:AGENT_MODE` 안내를 포함한다.
- 가드는 값을 조용히 지우거나 `tools`로 덮어쓰지 않는다. `tools`(앞뒤 공백 포함)와 빈 값은 허용하고, `fixed`·알 수 없는 값은 거부한다.
- `tests/test_evaluation_env.py`가 스크립트에서 가드 함수를 추출해 빈 값·`tools`·공백 포함 `tools` 허용, `fixed`/`FIXED`/`unknown` 거부, 거부 시 환경값이 지워지지 않음을 검증한다.
- 실제 재현: `$env:AGENT_MODE='fixed'`에서 `run-evaluation.ps1 -Limit 1 -Calibrated`를 실행하면 Docker·에이전트 시작 전에 `AGENT_MODE='fixed' is set, but only the tool-selecting agent remains. Run 'Remove-Item Env:AGENT_MODE' and retry.`로 즉시 중단한다.

### 7. 사용자 추가 요청: 모델 호출 재시도 3회 (agent/model.py)

- `MAX_RETRIES = 3`, 즉 최초 호출 포함 최대 4회 시도한다. `ModelClient._complete`가 재시도 루프를 관리하고, `chat`의 외부 계약(한 번의 호출)은 유지된다.
- 재시도 대상: HTTP 408·429·5xx, transport 오류·timeout, JSON이 아닌 응답, 성공 응답이지만 `choices`가 없거나 형식이 깨진 경우. 즉 일시적일 수 있는 호출·응답 실패다.
- 재시도 제외: API 키 부재(호출 전 실패), 400·401·403 등 4xx 계약 오류, 빈 content·절단 응답(호출 자체는 성공했고 tools 루프가 행동 오류로 처리). 같은 요청을 즉시 재시도하며 별도 backoff는 없다. 전체 재시도 시퀀스는 tools 루프의 세션 deadline(`_bounded`)으로 제한된다.
- 성공 시 `elapsed_seconds`는 재시도 전체 시간을 포함한다. 최종 실패 메시지는 `... (after 4 attempts)`를 포함한다. 한 번의 성공 응답은 기존처럼 `adk_events` 1건·모델 행동 1회로 기록된다.
- 적용 범위는 에이전트의 Command Code 모델 호출이다. 사용자 시뮬레이터 내부 모델은 TASK-006에서 정한 별도 설정(`USER_SIM_MAX_RETRIES`, compose 기본 0)을 그대로 유지한다.
- `tests/test_model.py`가 503 3회 후 성공, 500/timeout/깨진 payload의 4회 소진, 401 무재시도, 키 부재 무호출을 검증한다. `tests/test_tool_loop.py`는 모델 실패 시 4회 시도 후 `failure.stage="model"`·무과금, 동시 요청은 재시도 소진 후 재실행하지 않음을 검증한다.

## 완료 조건별 근거

- AC-1: `agent/loop.py`를 삭제했고 `agent/server.py`·`scripts/run-evaluation.ps1`에서 fixed 분기·기본값·`-AgentMode`·`AGENT_MODE` 설정이 사라졌다. `git grep -n -i fixed -- agent scripts tests compose.benchmark.yml compose.benchmark.calibrated.yml`의 남은 hit는 모두 다른 의미다: `agent/tool_loop.py`의 "fixed budget"과 "prefixed" 부분 문자열, `scripts/build_calibrated_set.py`·`scripts/prepare-benchmark.ps1`·`scripts/run-dev30*.ps1`·`scripts/select_dev_set.py`·`tests/test_dev_set.py`의 고정 30문제 선택, `tests/test_tool_loop.py`의 제거 모드 거부 입력("fixed")·"syntax error fixed" 문자열. fixed 에이전트를 실행하는 경로는 없다.
- AC-2: `create_app()` 기본값과 `AGENT_MODE` 미설정·빈 값이 tools로 기동하며, `/health`가 `agent=deepseek-v4.1-flash-tool-selecting-two-phase`, `mode=tools`, 9개 도구 목록·가격, execute_sql 120초·ask_user 660초를 보고한다. `create_app(mode="fixed")`와 `AGENT_MODE=fixed`, 알 수 없는 값은 ValueError로 거부한다(`test_health_reports_the_tool_selecting_agent`, `test_removed_modes_are_rejected_instead_of_silently_running_tools`). 실행기 가드도 시작 전에 같은 값을 거부한다(6절).
- AC-3: `run-evaluation.ps1`을 모드 옵션 없이 실행해 보정 1문제 smoke를 완료했다(exit 0, 1f66d46). 실행기가 항상 수행하는 9개 도구·에이전트·시뮬레이터 검증을 통과했고, oracle은 phase1/phase2 1/1·reward 1.0, tools는 정상 오답 reward 0.0(오류·failure 없음, 240.2초, 예산 22→0)이었다. R-1의 잔존 `AGENT_MODE=fixed` 경로는 시작 전 명확한 오류로 재현·검증했다(6절). 최종 SHA 87f7b9f에서는 happy path 변경이 없어 smoke를 다시 실행하지 않았다(아래 미검증).
- AC-4: `git diff --quiet 157a57a 87f7b9f -- agent/tool_loop.py agent/tools.py agent/environment.py benchmark/user_sim/command_code_simulator.py` 통과로 tools 루프·도구·환경·시뮬레이터 어댑터 파일의 변경이 없음을 확인했다. 명세 v1의 "tools 루프 동작 동일"은 성공 경로·도구 계약에 대해 유지된다. 단, 사용자 추가 요청으로 `agent/model.py`의 모델 실패 시도 횟수가 1회에서 최대 4회로 바뀌었으므로, 이 실패 경로는 명세 v1과 다르다(7절). 기존 tools 계약 55개는 통과한다.
- AC-5: 서버 세션 계약(init/reset/재사용·캐시된 결과·init 전 404·입력 오류·동시 요청 무중복·실패 후 무재시도·`.env` 키 로딩)은 `test_tool_loop.py`·`test_model.py`·`test_evaluation_env.py`에서 tools 루프 기준으로 유지된다. 삭제·이전·유지 목록은 위 표에 있다. 전체 131개 테스트와 `git diff --check`가 통과한다.
- AC-6: 작업별 `[TASK-007]` 제목의 로컬 커밋 3개를 만들었다. 최초 구현은 코드·테스트·스크립트 6개 수정·2개 삭제, R-1은 2개, 재시도는 3개 파일만 명시적으로 stage했다. 문서·results·vendor·private는 커밋하지 않았고 원격 push도 하지 않았다. 최종 SHA는 87f7b9f다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | HEAD 87f7b9f, 추적 변경 없음 | 131 tests, exit 0 | tools 계약·세션 계약·모델 chat/재시도 계약·어댑터·연결 확인·환경 가드·기존 회귀 |
| PowerShell 파서로 `run-evaluation.ps1`, `run-smoke.ps1`, `run-dev30.ps1`, `run-dev30-calibrated.ps1` 검사 | 최종 코드 | 4개 모두 ok | 구문 오류 없음 |
| `tests.test_evaluation_env` (7개) | 최종 코드 | exit 0 | 키 로딩 4 + `AGENT_MODE` 가드 3 |
| `$env:AGENT_MODE='fixed'`에서 `run-evaluation.ps1 -Limit 1 -Calibrated` 실행 | 최종 코드, Docker 시작 전 | 즉시 중단: `AGENT_MODE='fixed' is set, but only the tool-selecting agent remains. Run 'Remove-Item Env:AGENT_MODE' and retry.` | R-1 재현 |
| 모델 재시도 단위 검증 (`tests.test_model`) | 최종 코드 | 500/timeout/깨진 payload는 4회 소진, 503 3회 후 성공, 401은 1회, 키 부재는 0회 | 7절 |
| `git grep -n -i fixed -- agent scripts tests compose.benchmark.yml compose.benchmark.calibrated.yml` | 최종 코드 | 남은 hit는 다른 의미(고정 예산·고정 30문제 선택·문자열·제거 모드 입력) | fixed 실행 경로 없음 |
| `git grep -n "AgentMode\|AGENT_MODE"` | 최종 코드 | 실행기 hit 없음. server의 환경변수 가드·실행기 가드와 그 테스트만 존재 | 모드 옵션 제거 |
| `git diff --quiet 157a57a 87f7b9f -- agent/tool_loop.py agent/tools.py agent/environment.py benchmark/user_sim/command_code_simulator.py` | 최종 코드 | exit 0 | tools 루프·도구·환경·어댑터 무변경 |
| `docker compose -p db-agent-smoke -f compose.benchmark.yml config -q` / `-f compose.benchmark.calibrated.yml` 결합 | 최종 코드 | 둘 다 exit 0 | 기본·보정 compose 설정 유효 |
| `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task007 -OracleResultName oracle_smoke_task007` | HEAD 1f66d46, 추적 변경 없음, 30문제 평가 종료 후 | exit 0. oracle reward 1.0(phase1 1/1, phase2 1/1), tools reward 0.0 정상 오답, `error=None`, `failure=None`, 240.2초, 예산 22→0 | 모드 옵션 없는 tools 기본 실행·9개 도구·시뮬레이터 검증 통과(결과: results/oracle_smoke_task007.json, tools_smoke_task007.json) |
| smoke 도구 집계 | 위 실행 | execute_sql 10(성공 9, 공식 SQL 오류 1), ask_user 2(답변 2), submit_sql 2(공식 실패), 정보 2, 거부 1(무차감), 비용 합 22 | tool_trajectory·adk_events |
| smoke 모델·시뮬레이터 계측 | 위 실행 | 에이전트 17회·prompt 195737·completion 27860·total 223597, endpoint·timeout 300·상한 16384. 시뮬레이터 ask_user 2회, 내부 모델 4회, prompt 18036·completion 4593, 소요 34.577초 | adk_events, simulator 컨테이너 로그 |
| smoke 산출물 SHA-256 | 위 실행 | oracle `ded925609818d95ed0769bf79185cd0f277f5ff036b96fb517b141a863e7f46d`, tools `353c10fb78e34d8f5898fd3049f81cbcada4fc3528d24b481d5972f50854f701` | results/*.json |
| `git diff --check` | 최종 코드 | 출력 없음(exit 0) | 공백 오류 없음 |
| 최종 시점 `git diff --quiet HEAD` | 최종 코드 | exit 0 | 테스트한 워킹트리 = 커밋 87f7b9f |

검증 코드와 최종 SHA 일치 근거: 최종 커밋 87f7b9f 직후 작업 트리에서 131개 테스트를 실행했고 `git diff --quiet HEAD`가 비어 있음을 확인했다. 1문제 smoke는 이전 커밋 1f66d46에서 실행했으며, 이후 변경은 시작 전 `AGENT_MODE` 가드와 모델 실패 재시도뿐이다. 성공 경로의 서비스 기동·도구 선택·채점 흐름은 바뀌지 않았지만, 최종 SHA 자체의 smoke 재실행은 하지 않았다(아래 미검증).

## 미검증 항목과 제한

- 최종 SHA 87f7b9f의 1문제 smoke는 다시 실행하지 않았다. smoke는 1f66d46 산출물이며, 이후 변경은 R-1 시작 전 가드와 모델 실패 재시도다. 전자는 실제 재현으로, 후자는 mock 계약 테스트로 확인했다. 최종 SHA의 end-to-end happy path 재확인이 필요하면 같은 smoke 명령을 다시 실행하면 된다.
- 사용자 시뮬레이터 내부 모델 호출은 재시도 3회를 적용하지 않았다. TASK-006의 `USER_SIM_MAX_RETRIES`(compose 기본 0) 설정을 유지한다. 에이전트 측 Command Code 호출만 대상이다.
- 재시도는 즉시 수행하며 backoff/jitter가 없다. HTTP 408·429·5xx, transport/timeout, JSON/choices 형식 오류만 재시도하고 400·401·403 등 4xx와 키 부재는 재시도하지 않는다. 빈 content·절단 응답도 재시도하지 않는다(호출은 성공, tools 루프가 행동 오류로 처리).
- 래퍼 스크립트 `run-smoke.ps1`·`run-dev30.ps1` 자체를 끝까지 실행하지는 않았다. 두 스크립트는 `run-evaluation.ps1`을 옵션 없이 호출하며 파서 검사를 통과했다. 이제 두 스크립트 모두 `CMD_API_KEY`와 시뮬레이터 모델 호출이 필요하다.
- `AGENT_MODE=fixed`로 실제 Uvicorn을 기동해 프로세스가 실패하는 모습은 별도 실행하지 않았다. `create_app()` 단위 테스트가 ValueError를, 실행기 가드가 시작 전 중단을 각각 확인한다.
- 30문제 재평가·도구별 ablation·성능 비교는 수행하지 않았다. 기존 `results/tools_dev30_calibrated_task006.*`는 TASK-006 코드 결과이므로 TASK-007의 성능 결과로 승계하지 않는다.
- smoke는 단일 task(`mental_1`)이며 reward 0.0은 정상 오답이다. 모델이 선택하는 도구 조합은 실행마다 달라질 수 있으므로 도구별 분포를 일반화하지 않는다.
- `run-dev30-calibrated.ps1`은 oracle 전용이라 변경하지 않았고 실행하지 않았다.

## 명세 대비 구현 결정(보고)

- 모드 선택 인터페이스는 완전히 없애지 않고 단일 값 가드로 남겼다. 명세가 허용한 선택지 중, `AGENT_MODE=fixed` 같은 명시적 요청을 조용히 tools로 바꾸지 않고 오류로 알리기 위해서다.
- `/health`의 `mode: "tools"`와 `AGENT_NAME`은 유지했다. 실행기가 이전 7개 도구·fixed 서비스와 현재 서비스를 구분하는 기존 계약을 보존한다.
- fixed 전용 `generate_sql`/`extract_sql`/`SYSTEM_PROMPT`/`ModelReply`는 다른 사용처가 없어 삭제했다. tools 루프가 쓰는 chat 클라이언트와 상수는 유지했다.
- `InputError` 정의는 `agent/session.py`에 있으므로 server import를 정의 위치로 옮겼다. 예외 타입·메시지·HTTP 422 매핑은 바꾸지 않았다.
- R-1은 실행기에서 환경값을 검사·안내하는 방식으로 고쳤다. 값을 조용히 삭제하거나 tools로 덮어쓰면 "명시적 요청을 조용히 실행하지 않는다"는 계약이 흐려지기 때문이다.
- 모델 재시도는 `ModelClient` 내부에 두었다. tools 루프는 성공한 모델 응답 1건을 기존처럼 하나의 행동으로 기록하고, 세션 deadline·행동 상한·예산 계약을 바꾸지 않는다. "재시도 3번"은 최초 호출 뒤 3회로 해석해 총 4회 시도한다.
- 모델 재시도는 TASK-007 명세 v1의 "동작 변경 없음" 제약을 넘는 사용자 추가 요청이다. 조율자는 spec v2에 이 동작과 재시도 범위(에이전트 전용, 4xx 제외, backoff 없음)를 반영할 필요가 있다.

## 리뷰 지적 처리

### R-1: 기준 실행기가 남긴 `AGENT_MODE=fixed`에서 원인 없는 기동 실패

수정 횟수: 1회(커밋 6840ff6).

- `scripts/run-evaluation.ps1`에 `Assert-AgentModeEnvironment`를 추가하고, `Import-AgentApiKey`·compose 기동·에이전트 시작 전에 호출했다. `AGENT_MODE`가 비어 있지 않고 `tools`가 아니면 실제 값과 `Remove-Item Env:AGENT_MODE` 안내를 담아 즉시 중단한다. 값을 지우거나 tools로 바꾸지 않는다.
- `tests/test_evaluation_env.py`에 PowerShell 가드 테스트 3개를 추가했다: 빈 값·`tools`·공백 포함 `tools` 허용, `fixed`/`FIXED`/`unknown` 거부와 메시지, 거부 후 환경값 보존.
- 실제 재현: `$env:AGENT_MODE='fixed'`에서 `run-evaluation.ps1 -Limit 1 -Calibrated`가 Docker·에이전트를 건드리기 전에 `AGENT_MODE='fixed' is set, but only the tool-selecting agent remains. Run 'Remove-Item Env:AGENT_MODE' and retry.`로 중단하는 것을 확인했다.
- 검증: 최종 코드에서 전체 131개 테스트 통과, `git diff --check` 통과. 리뷰 지적에 대한 이견 없음.

## 사용자 추가 요청 처리

- 요청: "모델 호출 실패했을 때 재시도 3번까지" (TASK-007 추가 수정).
- 구현: 커밋 87f7b9f. `MAX_RETRIES = 3`(최초 포함 4회), 일시적 HTTP/transport/timeout/JSON·payload 오류만 재시도, 4xx·키 부재는 제외, 에이전트 모델 호출에만 적용.
- 검증: `tests/test_model.py`의 4회 소진·중간 성공·401 무재시도·키 부재 무호출, `tests/test_tool_loop.py`의 모델 실패 후 무과금·동시 요청 재실행 없음. 전체 131개 테스트 통과.
- 범위 영향: TASK-007 명세 v1의 "tools 동작 무변경" 중 모델 실패 경로가 확장됐다. tools 루프·도구·환경·시뮬레이터 파일은 무변경이다. 시뮬레이터 재시도는 별도 설정 그대로다.

## 필요한 판단

없음. 다만 모델 재시도는 명세 v1 범위 밖의 사용자 추가 요청이므로, 조율자가 spec v2(또는 후속 명세)에 이 동작을 반영할지 판단하면 된다.

### 후속 안내

- 재현: `.\.venv\Scripts\python.exe -m unittest discover -s tests`, 모드 옵션 없는 보정 smoke는 `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task007 -OracleResultName oracle_smoke_task007`.
- 래퍼 운영 시 `CMD_API_KEY`가 필요하고 시뮬레이터가 Command Code 어댑터로 기동돼야 한다. 기본/보정 스택 모두에 적용된다.
- 30문제 후속 평가(선택): `.\scripts\run-evaluation.ps1 -Limit 30 -Concurrency 10 -Calibrated -ResultName tools_dev30_calibrated_task007 -OracleResultName oracle_dev30_calibrated_task007`.
- 후속 문서 후보(리뷰 S-1): `README.md` 65행의 "문제당 모델 호출 1회", 71행의 "모델 호출 1회, SQL 추출", 72행의 `agent/loop.py` 설명이 삭제된 fixed 구성과 맞지 않는다. 또한 `README.md`·`docs/setup.md`에 `run-smoke.ps1`·`run-dev30.ps1`이 이제 `CMD_API_KEY`와 Command Code 시뮬레이터 모델 호출을 필요로 한다는 안내가 필요하다. 명세상 이번 작업에서는 보고서 외 문서를 수정하지 않았다.
