# TASK-006 구현 보고서

작업 ID: TASK-006
명세 버전: 1
대상 코드 버전: 71a3ec1091028708b3f090420c34dbc19dd87315
상태: ready_for_review
요약: tools 모드가 공식 9개 도구(정보 6 + execute_sql + ask_user + submit_sql)를 안내·실행하고, 사용자 시뮬레이터는 Command Code 모델 어댑터로 기동하며 모델 실패를 502 서비스 오류로 알린다. 실제 보정 smoke에서 모델이 두 새 도구를 자율적으로 선택했고, /execute·모델 포함 /ask 실연결을 확인했다. 리뷰 R-1 수정으로 연결 확인 메타데이터가 실행 코드 SHA·작업 트리 청결 상태·시뮬레이터 호출/토큰 델타를 기록하고, smoke 실행 메타데이터를 `results/smoke_task006.metadata.json`에 남겼다.
근거: agent/tools.py, agent/tool_loop.py, agent/environment.py, agent/server.py, benchmark/user_sim/command_code_simulator.py, compose.benchmark.yml, scripts/run-evaluation.ps1, scripts/verify_tool_connection.py, tests/test_tool_loop.py, tests/test_command_code_simulator.py, tests/test_verify_tool_connection.py. 실행 산출물: results/tools_smoke_task006.json, results/oracle_smoke_task006.json, results/tool_connection.json, results/smoke_task006.metadata.json(모두 미커밋).
미확정 사항: 없음. 아래 미검증 항목 참고.

## 커밋과 검토 범위

기준 커밋: a93c905ecb226f9b94afd16fe02490ca3185db15
코드 커밋: 71a3ec1091028708b3f090420c34dbc19dd87315
작업 커밋 목록:
- b8de9889a38535fa4b7c285053be2959cab45327 — 최초 구현
- f978552847f31eba78f0070259e07cda2da1abf7 — R-1: 연결 확인에 작업 트리 상태 기록
- 71a3ec1091028708b3f090420c34dbc19dd87315 — R-1: 시뮬레이터 호출/토큰 델타 기록

검토 명령: `git diff a93c905ecb226f9b94afd16fe02490ca3185db15 71a3ec1091028708b3f090420c34dbc19dd87315 --`

현재 코드와 대상 SHA의 차이: 추적 파일 중 사용자 소유의 `.gitignore` 변경(`.claude/` 한 줄)만 남아 있다. 제품 코드·테스트·실행 설정은 대상 SHA와 일치한다(`git diff --quiet 71a3ec1 -- agent benchmark scripts tests compose.benchmark.yml compose.benchmark.calibrated.yml` 통과). 미추적 작업 문서와 results 산출물은 커밋에 포함하지 않았다. 최종 연결 확인 결과의 `code_dirty: true`는 이 `.gitignore` 한 줄만 가리키며 실행 코드와 무관하다.

기존 사용자 변경: `.gitignore` 수정, 미추적 `docs/agent/tasks/TASK-004/`, `TASK-005/`, `TASK-006/`, results 산출물. 보존했고 이번 커밋에 섞지 않았다.

## 변경 내용

### 1. 도구 집합과 실행 (agent/tools.py, agent/tool_loop.py)

- tools 모드에 `execute_sql`(1코인, `{"sql": ...}`)과 `ask_user`(2코인, `{"question": ...}`)를 추가해 총 9개 공식 도구를 카탈로그·단일 JSON 행동 프로토콜로 안내한다. `UNAVAILABLE_TOOLS` 거부 분기는 삭제했다.
- 새 두 도구는 `ToolSpec.run=None`으로 두고 루프가 전용 함수 `_execute_action`/`_ask_action`에서 처리한다. 모델이 임의 `task_id`·URL·예산을 지정할 수 없고 인자는 비어 있지 않은 문자열로만 검증된다(검증 실패 시 외부 무호출·무차감).
- `execute_sql` 공식 결과(success=true)와 공식 SQL 오류·공식 실행 timeout 응답(success=false)을 관측으로 전달하고 다음 행동을 허용한다. HTTP 연결 실패·비정상 상태·계약 위반 응답은 `GradingError` → `failure.stage="execute_sql"` 서비스 오류로 구분한다.
- `ask_user`의 유효 답변만 관측 메시지와 `dialogue_history`(질문/답변 쌍 + `phase`)에 기록한다. 호출 실패·기한 취소는 답변을 만들지 않고 service_error/cancelled로만 기록한다.
- 새 두 도구는 정보 캐시를 조회하지 않는다(같은 SQL·같은 질문도 매번 실제 호출·과금). 기존 정보 캐시와 `submit_sql` 단계별 중복 판정은 그대로다.
- 도구 기록에 `phase`·`elapsed_seconds`·결과 종류(`kind`: result/sql_error/answer/service_error/cancelled)와 코인 전후를 남긴다. 정보 조회·제출 기록에도 `elapsed_seconds`를 추가했다(캐시·중복 차단 기록은 제외).
- SYSTEM_PROMPT에 새 도구의 목적·인자·가격, 캐시 제외, 성공이 제출·점수가 아님, 시작 시 과금·환불 없음, 도구 선택은 모델 자율임을 안내한다. 24회 행동·1500초·잔여 3코인 미만 종료 정책은 그대로다.

### 2. HTTP 계약과 시간 (agent/environment.py, agent/server.py)

- `Environment.execute_sql`이 `/execute` 응답을 `ExecuteResult(success, result, error)`로 검증하고, `ask_user`가 `/ask`의 비어 있지 않은 `answer` 문자열을 요구한다(빈 답변·깨진 응답은 GradingError).
- 호출별 HTTP 대기: execute_sql 기본 120초(`DB_EXECUTE_SQL_TIMEOUT_SECONDS`), ask_user 기본 660초(`USER_SIM_ASK_TIMEOUT_SECONDS`). 두 값 모두 세션 deadline과 함께 더 짧은 쪽으로 제한되며 재시도는 0회다.
- `/health`에 도구 목록·가격(`tools`)과 두 timeout 값을 노출한다. 실행기(scripts/run-evaluation.ps1)가 이 목록을 기대 9개·공식 가격과 대조해 오래된 7개 도구 서비스 재사용을 거부한다.

### 3. 시뮬레이터 Command Code 어댑터 (benchmark/user_sim/command_code_simulator.py, 신규)

- `compose.benchmark.yml`의 user-simulator가 공식 `user_simulator.server:app` 대신 이 모듈을 `--factory`로 기동한다. vendor 파일은 수정하지 않는다(마운트는 읽기 전용 유지).
- provider 확장점: `shared._local_provider`를 `sys.modules`에 등록해 `shared.llm.call_llm`이 Command Code API(`deepseek/deepseek-v4.1-flash`, temperature 0, 출력 상한 16384, timeout 300초, 자동 재시도 0, effort 미지정)를 사용하게 한다. 요청 model_name은 무시한다(공식 기본값이 서비스 불가한 Anthropic id).
- 실패 가시화: 공식 `_call_llm` seam을 기동 시 1회 교체해(요청마다 전역 함수 교체 아님) 모델 오류가 빈 문자열로 삼켜지지 않고 예외로 올라오게 한다. `/ask`는 이를 HTTP 502로 전달하고 정상 답변을 가장하지 않는다. 빈 완성·빈 답변도 502다.
- `/init_task`는 공식 핸들러를 그대로 호출한 뒤, 컨테이너 내부 localhost 대신 `DB_ENV_URL`(compose에서 `http://db-environment:6002`)에서 스키마를 다시 로드해 상태에 넣는다. 공식 핸들러의 컨테이너 내부 시도는 실패 경고 1건을 남기지만 실제 스키마는 항상 설정된 DB에서 온다(실측 schema_len=12909). 로드 실패는 502다.
- `/phase_transition`, v2 프롬프트, 행동 해석·답변 생성, `_task_states` 세션 계약, `/debug_state`는 공식 그대로다. `/ask`만 교체한다.
- 계측: 모델 호출마다 contextvars 기반 요청별 리스트에 usage·소요 시간을 기록하고(동시 task 혼입 없음), 요청 종료 시 `model_calls·prompt_tokens·completion_tokens·elapsed`를 컨테이너 로그에 남긴다. `/health`는 설정과 누적 카운터(model_calls, model_failures, tokens, schema_failures)와 `api_key_present`(불리언)만 노출하고 키·프롬프트·정답은 로그·응답·결과에 남기지 않는다.

### 4. 실행기·설정 (scripts/run-evaluation.ps1, compose.benchmark.yml)

- `.env`의 `CMD_API_KEY`를 스택 기동 전에 로드해 compose 보간(`CMD_API_KEY: ${CMD_API_KEY:-}`)과 에이전트 프로세스가 같은 값을 쓰게 했다. 키는 로그·결과에 출력하지 않는다.
- `Assert-BaselineAgent`가 tools 모드에서 `/health.tools`의 이름·가격 9개를 대조하고, `Assert-Simulator`가 tools 모드에서 시뮬레이터가 `user_simulator_command_code`·deepseek-v4.1-flash·v2·retries 0·상한 16384·키 존재를 보고하는지 확인한다. 비 tools 모드는 기존 검증만 한다.
- 시뮬레이터 컨테이너에는 필요한 값만 전달한다(CMD_API_KEY, DB_ENV_URL, PYTHONPATH, USER_SIM_MODEL, USER_SIM_TIMEOUT_SECONDS=300, USER_SIM_MAX_OUTPUT_TOKENS=16384 + 공통 환경). YAML merge 시퀀스로 공통 environment를 상속한다(`docker compose config -q`로 기본·보정 결합 모두 검증).
- 기본 스택(db-environment 원본)과 보정 스택이 모두 같은 어댑터를 사용하며, db-environment의 보정 override는 그대로다.

### 5. 실연결 확인 스크립트 (scripts/verify_tool_connection.py, 신규)

한 task를 DB 환경·시뮬레이터에 init한 뒤 허용 조회 SQL 1회(`information_schema` 메타데이터)와 공개 질문 1회로 `/execute`·`/ask`를 직접 호출하고(모델 호출 포함, mock 아님) 코드 SHA·vendor SHA·입력 해시·서비스 설정·호출 수·소요·검사 결과를 `results/tool_connection.json`에 남긴다. GT로 질문·SQL을 만들지 않고 제출·채점은 하지 않는다.

### 6. 리뷰 R-1 수정 (scripts/verify_tool_connection.py, tests/test_verify_tool_connection.py)

- 코드 버전 식별: `git rev-parse HEAD`만 기록하던 것을 `git status --porcelain --untracked-files=no` 기반의 추적 파일 변경 목록과 함께 기록한다. 결과에 `code_dirty`, `code_uncommitted_paths`, `vendor_dirty`, `vendor_uncommitted_paths`를 추가해, 미커밋 상태에서 실행해도 HEAD SHA를 실행 코드로 단정하지 않게 표시한다.
- 시뮬레이터 계측: 호출 전 `/health` 스냅샷만 저장하던 것을 호출 후 스냅샷(`simulator_after`)과 함께 저장하고, `simulator_calls_during_check`·`simulator_tokens_during_check`(prompt/completion)를 전후 델타로 기록한다. 카운터가 없거나 정수가 아니면 추정하지 않고 `null`이다.
- 테스트: `tests/test_verify_tool_connection.py`가 clean/modified/staged/rename/quoted porcelain 파싱, HEAD와 dirty 상태, git 부재, 카운터 델타·결측을 검증한다(5개).
- 증거: `results/tool_connection.json`을 71a3ec1에서 다시 생성했고, `results/smoke_task006.metadata.json`에 원 smoke의 코드·vendor·입력·명령·설정·호출/토큰·채점·산출물 해시를 비밀 없이 기록했다.

## 완료 조건별 근거

- AC-1: 카탈로그가 9개 도구의 인자·가격을 안내하고 `parse_action`이 `execute_sql`/`ask_user`를 수용한다. 빈/잘못된 인자는 `ActionError`로 외부 무호출·무차감이며 HTTP 경로·요청 본문·응답 검증은 계약 테스트로 확인했다. fixed 모드 회귀는 tests/test_connection.py 31개가 유지된다(tools 계약 53개 + 어댑터 18개 + 연결 확인 5개 포함 전체 158개 통과).
- AC-2: `test_each_allowed_tool_uses_its_official_endpoint_and_price`, `ExploreAndAskTests`, `ToolLoopFailureTests`가 execute_sql 성공 → 다음 행동 → submit, 공식 SQL 오류 → 수정 행동 → submit, HTTP/계약 오류 → `failure.stage="execute_sql"` 구분을 검증한다. 탐색 성공·오류는 `total_reward`·phase·last_submit에 기록되지 않는다.
- AC-3: `test_phase2_question_reaches_the_same_simulator_session`이 Phase 1 성공 → 전환 1회 → 같은 시뮬레이터 세션(task_id 동일)에서 Phase 2 질문과 phase=2 기록을 확인한다. 실패·취소 시 dialogue_history 미생성, 빈 답변 서비스 오류, 비허용 state 미노출 회귀(`test_state_beyond_the_allowed_inputs_never_reaches_the_model`)를 확인했다.
- AC-4: 호출 시작 시 1/2코인 차감·환불 없음, 같은 execute_sql/ask_user 재호출이 실제 재전송, 정보 캐시는 유지, 제출 중복 차단, 24회·1500초 상한, 기한 취소 시 `timed_out`·결과 미상·확정 보상 보존, 실패 세션 재호출 무중복·reset·동시 요청 계약을 테스트로 확인했다.
- AC-5: compose가 모델·키·DB 대상·timeout·출력 상한을 시뮬레이터에 전달하고, `/health`로 확인 가능하다. 연결·모델 실패는 502이며 정상 답변으로 위장하지 않는다. 키·프롬프트가 health/응답/상태에 없음을 테스트가 확인한다. 실행기는 도구 9개·시뮬레이터 연결을 대조하고, 기본/보정 스택 기동과 `.env` 키 로딩 회귀(tests/test_evaluation_env.py)를 유지한다.
- AC-6: 최종 제품 코드와 일치하는 콘텐츠(b8de988 blob 해시 10/10 일치)로 보정 smoke를 수행했다. oracle은 phase1/phase2 1/1·reward 1.0, tools 에이전트는 정상 오답 reward 0.0(오류·failure 없음, 449.7초, 예산 22→0.5)이었다. 에이전트가 execute_sql 4회(성공)·ask_user 4회(정답 수신)·submit_sql 2회(공식 실패)를 자율 선택했다. R-1 재실행(71a3ec1)에서 `/execute` 성공(0.156초)·`/ask` 정상 답변(11.719초, 시뮬레이터 내부 모델 호출 2회, prompt 8813/completion 1401 토큰)을 확인했고, 결과 파일은 `code_sha=71a3ec1`, `code_dirty=true`(경로는 `.gitignore` 하나), `vendor_dirty=false`, 입력 SHA-256 `55ca576e…`를 기록한다. smoke 메타데이터는 `results/smoke_task006.metadata.json`에 있다. 상세 수치는 아래 표와 results/ 파일에 있다.
- AC-7: 전체 회귀 + 신규 테스트 158개 통과, `git diff --check a93c905 71a3ec1` 통과, 코드·테스트·실행 설정만 명시 stage해 `[TASK-006]` 제목의 로컬 커밋 3개를 만들었다. 문서 커밋·원격 push는 하지 않았다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | HEAD 71a3ec1, 추적 변경은 사용자 `.gitignore`뿐 | 158 tests, exit 0 | 도구 9개 계약·예산·캐시·기한·동시성·시뮬레이터 어댑터·연결 확인 5개·기존 fixed 회귀 |
| `docker compose -p db-agent-smoke -f compose.benchmark.yml config -q` / `-f compose.benchmark.calibrated.yml` 결합 | 작업 트리 | 둘 다 exit 0 | YAML merge로 공통 환경 상속 + 시뮬레이터 설정 전달 |
| `docker compose ... -f compose.benchmark.calibrated.yml up --build -d --wait` | 보정 스택 | 3개 서비스 healthy, `/calibration`이 `db_environment_calibration` 보고 | 보정 모드 배선·시뮬레이터 기동 |
| `.\.venv\Scripts\python.exe scripts\verify_tool_connection.py` | 보정 스택, 71a3ec1, `mental_1`, 실제 모델 호출 | 5/5 검사 true, `code_sha=71a3ec1`, `code_dirty=true`(`.gitignore`만), `vendor_dirty=false`, 시뮬레이터 호출 2·prompt 8813·completion 1401, /execute 0.156s, /ask 11.719s | /execute·모델 포함 /ask 실연결(결과: results/tool_connection.json) |
| `.\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1 -AgentMode tools -Calibrated -ResultName tools_smoke_task006 -OracleResultName oracle_smoke_task006` | b8de988과 동일 콘텐츠(해시 10/10), 보정 스택, 동시성 1 | oracle reward 1.0(1/1, 1/1), tools reward 0.0 정상 오답, `error=None`, `failure=None`, 17회 모델 행동, 예산 22→0.5, 449.7초 | 자율 도구 선택 smoke(결과: results/tools_smoke_task006.json, oracle_smoke_task006.json) |
| 도구 기록 집계(스모크) | 위 실행 | execute_sql 4(모두 success), ask_user 4(모두 answer), submit_sql 2(공식 실패), 정보 4, 잘못된 행동 3(무차감 거부), 도구 비용 합 21.5 | tool_trajectory·adk_events |
| 에이전트 usage 집계(스모크) | 위 실행 | 모델 17회, prompt 226627·completion 24163·total 250790, endpoint·timeout 300·상한 16384 | adk_events |
| smoke 메타데이터 작성 | 위 실행 산출물 | `results/smoke_task006.metadata.json`에 코드 SHA·dirty 여부·vendor SHA·입력 SHA-256·명령·모드·에이전트/시뮬레이터 설정·호출/토큰·채점·산출물 해시 기록. 시뮬레이터 내부 호출/토큰은 `null`(아래 미검증 항목) | results/smoke_task006.metadata.json |
| `git diff --check a93c905 71a3ec1` | 최종 코드 | 출력 없음(exit 0) | 공백 오류 없음 |
| SHA-256 재대조 | `.runtime-tmp/task006-code-hashes.txt` vs `git show b8de988:<path>` | 10/10 blob 일치. 현재 작업 트리는 9/10(수정한 verify 스크립트만 다름) | 원 smoke가 b8de988 콘텐츠로 실행됐다는 근거 |

검증 코드와 최종 SHA 일치 근거: smoke·기능 확인 **이전에** 10개 제품/테스트 파일의 SHA-256을 고정(`.runtime-tmp/task006-code-hashes.txt`)했고, 재대조 결과 b8de988의 10개 blob과 모두 일치한다. 최종 연결 확인은 71a3ec1에서 실행했으며 `git diff --quiet 71a3ec1 -- scripts/verify_tool_connection.py tests/test_verify_tool_connection.py`가 통과한다. 최종 전체 테스트도 HEAD 71a3ec1에서 실행했고 추적 파일 변경은 사용자 `.gitignore`뿐이다. 즉 최종 검증 콘텐츠 = 커밋 콘텐츠다. `.gitignore`는 실행 코드가 아니며 연결 확인 결과에 dirty 경로로 명시했다.

## 미검증 항목과 제한

- smoke 시뮬레이터 내부 호출 수·토큰: 원 smoke 로그에는 `ask_user served ...` 형식만 확인했고 전후 `/health` 델타를 저장하지 않았다. 2026-10-20 스택 재기동 때 컨테이너가 재생성되어 이전 로그·카운터도 복구할 수 없었다. `ask_user` 4회는 확인했지만 내부 모델 호출·토큰은 추정하지 않고 `null`로 두었다. R-1 재실행의 `/ask` 1회는 호출 2·prompt 8813·completion 1401로 기록됐다.
- 원 smoke는 코드가 미커밋인 상태에서 실행됐다(실행 시 HEAD=a93c905). 실행 콘텐츠는 사전 고정 해시 10개가 b8de988 blob과 일치해 동일함을 확인했다. 이후 커밋이므로 메타데이터에 당시 head와 dirty 여부를 함께 적었다.
- 실 DB Phase 2 ask_user: smoke에서 phase 1을 통과하지 못해 실제 Phase 2 질문·전환 후 질문은 실행하지 않았다. 전환 계약은 oracle 실행(`/phase_transition` 200)과 계약 테스트로만 확인했다.
- 30문제 재평가·도구별 ablation·성능 비교는 명세대로 수행하지 않았다(후속 요청 시 `-AgentMode tools`로 수행 가능).
- 시뮬레이터 `/ask`는 내부 모델 2단계로 최대 600초까지 걸릴 수 있고, 에이전트는 660초까지 대기한다. 이번 실측 최장은 84.1초였다. timeout·출력 상한·재시도 설정은 env/compose로 변경 가능하며 실행 시 실제 값이 `/health`에 나타난다.
- 공식 `init_task`가 컨테이너 내부 localhost 스키마 조회를 시도해 경고 로그 1건을 남긴다(실제 스키마는 어댑터가 DB_ENV_URL에서 로드). vendor 수정 없이 제거할 수 없는 공식 경로다.
- 시뮬레이터 내부 모델은 출력 상한 16384·temperature 0으로 연결했고 이는 공식 단계별 기본 상한(500/1024)과 다르다(명세 지시). 실측 completion 토큰이 최대 9.8k까지 나와 상한 확대가 유효했다.

## 명세 대비 구현 결정(보고)

- 어댑터의 세부 배치: `benchmark/user_sim/command_code_simulator.py` + compose `x-user-simulator` 앵커(구현자 결정 범위).
- provider 확장점과 seam을 함께 사용: 확장점만으로는 공식 `_call_llm`이 오류를 빈 문자열로 삼켜 "정상 답변 위장" 요구를 충족할 수 없어, 동일 인자로 위임하되 예외를 전파하는 seam을 기동 시 1회 설치했다(요청별 전역 교체 아님, 동시 task 상태는 contextvars로 격리).
- `/init_task`는 공식 핸들러를 유지한 채 스키마만 실제 DB 대상에서 재로드한다(“공식 러너·루프 제어 경로 유지” + “스키마 대상 일치” 요구 동시 충족).
- R-1에서 dirty 표시와 실행 거부 중 표시를 택했다. 작업 트리에 사용자 변경이 남아 있어도 실행 내용을 오해 없이 식별할 수 있고, 사용자 변경을 stash/삭제하지 않기 위해서다.

## 리뷰 지적 처리

### R-1: 결과 메타데이터의 코드 SHA가 실제 실행 코드와 다르고 smoke 메타데이터가 없음

수정 횟수: 1회(후속 커밋 2개, f978552·71a3ec1).

1. **미커밋 상태의 코드 SHA 오기록**: `scripts/verify_tool_connection.py`가 HEAD와 함께 추적 파일 변경 여부·경로를 기록하도록 수정했다. 결과는 `code_sha`, `code_dirty`, `code_uncommitted_paths`, `vendor_dirty`, `vendor_uncommitted_paths`를 포함한다. `tests/test_verify_tool_connection.py`의 5개 단위 테스트로 파싱·dirty 판정·git 부재·카운터 델타를 확인했다.
2. **정확한 SHA로 재실행**: 71a3ec1에서 보정 스택에 `/execute`·`/ask`를 다시 호출했다. `results/tool_connection.json`은 `code_sha=71a3ec1091028708b3f090420c34dbc19dd87315`, `code_dirty=true`, `code_uncommitted_paths=[".gitignore"]`, `vendor_sha=451fe2c…`, `vendor_dirty=false`, `data_sha256=55ca576e…`, 5/5 검사 true, 시뮬레이터 호출 2·prompt 8813·completion 1401, /execute 0.156초·/ask 11.719초를 기록한다. 사용자 `.gitignore` 변경은 보존했으며 실행 코드와 무관함을 결과와 이 보고서에 명시했다.
3. **smoke 메타데이터**: `results/smoke_task006.metadata.json`에 원 smoke의 실행 콘텐츠 SHA(b8de988)·당시 HEAD(a93c905)·dirty 여부·해시 근거, vendor SHA, 입력 파일 SHA-256, 명령·모드·동시성, 에이전트/시뮬레이터 설정, 에이전트 17회·토큰 226627/24163/250790, `ask_user` 4회, 도구별 호출·비용, oracle/tools 채점, 산출물 SHA-256을 기록했다. 시뮬레이터 내부 호출·토큰은 로그·컨테이너 재생성으로 복구할 수 없어 `null`과 사유를 남겼다(추정값을 넣지 않음).

검증: R-1 수정 후 전체 158개 테스트 통과, `git diff --check a93c905 71a3ec1` 통과, 실제 연결 확인 5/5 true. 지적에 대한 이견·충돌 없음.

## 필요한 판단

없음.

### 후속 안내

- 재현 명령: 보정 smoke는 위 표의 `run-evaluation.ps1 -AgentMode tools -Calibrated` 한 줄, 도구 직접 확인은 `.\.venv\Scripts\python.exe scripts\verify_tool_connection.py`, 계약 테스트는 `-m unittest discover -s tests`.
- 30문제 후속 평가(선택): `.\scripts\run-evaluation.ps1 -Limit 30 -Concurrency 10 -AgentMode tools -Calibrated -ResultName tools_dev30_calibrated_task006 -OracleResultName oracle_dev30_calibrated_task006` (이번 작업 범위 밖).
- 문서 갱신 필요 사항: `docs/setup.md`의 "사용자 질문 도구를 추가하려면 시뮬레이터 컨테이너에 모델 호출 의존성과 API 설정을 추가해야 한다" 문장은 이제 구현으로 대체됐다. 조율자가 사용자 확인을 받은 뒤 반영 여부를 판단하면 된다(이번 작업에서 다른 문서는 수정하지 않았다).
