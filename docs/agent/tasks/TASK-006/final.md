# TASK-006 최종 보고서

작업 ID: TASK-006
명세 버전: 1
기준 코드 버전: a93c905ecb226f9b94afd16fe02490ca3185db15
대상 코드 버전: 71a3ec1091028708b3f090420c34dbc19dd87315
상태: ready_for_user
요약: tools 모드가 공식 9개 도구(정보 6 + execute_sql + ask_user + submit_sql)를 안내·실행하고, 사용자 시뮬레이터는 프로젝트 소유 Command Code 어댑터로 기동해 모델 실패를 502 서비스 오류로 알린다. 필수 완료 조건 AC-1~AC-7이 충족됐고, 같은 명세 버전·최종 SHA의 독립 리뷰가 pass(1차 지적 R-1 해결)다. 실제 보정 smoke 1문제에서 모델이 새 두 도구를 자율 선택했으며 결과는 정상 오답이었다. 정답률 목표는 범위 밖이다.
근거: 아래에 코드 위치·검증 명령·결과·리뷰 결론을 직접 기록했다. 조율자가 최종 SHA에서 전체 테스트를 다시 실행해 158개 통과를 확인했다.
미확정 사항: 없음. 후속 판단 후보는 "필요한 보완·판단"에 정리했다(완료를 막지 않음).

## 변경 내용과 설계 결정

작업 코드 커밋:
- b8de9889a38535fa4b7c285053be2959cab45327 — `[TASK-006] feat: offer the official nine-tool set with a Command Code simulator`
- f978552847f31eba78f0070259e07cda2da1abf7 — `[TASK-006] fix: record the worktree state in the connection check` (R-1)
- 71a3ec1091028708b3f090420c34dbc19dd87315 — `[TASK-006] fix: record simulator token deltas in the connection check` (R-1)

누적 변경 11개 파일(+1695/−61): `agent/environment.py`, `agent/server.py`, `agent/tool_loop.py`, `agent/tools.py`, `benchmark/user_sim/command_code_simulator.py`(신규), `compose.benchmark.yml`, `scripts/run-evaluation.ps1`, `scripts/verify_tool_connection.py`(신규), `tests/test_command_code_simulator.py`(신규), `tests/test_tool_loop.py`, `tests/test_verify_tool_connection.py`(신규). 문서·results·vendor·private은 포함되지 않았다.

### 도구 집합과 루프 (agent/tools.py, agent/tool_loop.py)
- `execute_sql({"sql"})` 1코인, `ask_user({"question"})` 2코인을 추가하고 `UNAVAILABLE_TOOLS` 거부 분기를 삭제했다. 단일 JSON 행동 프로토콜은 유지하며, 모델은 task_id·URL·예산을 지정할 수 없다. 빈/잘못된 인자는 외부 무호출·무차감으로 거부된다.
- 두 도구는 정보 캐시를 거치지 않는 전용 경로(`_execute_action`, `_ask_action`)로 처리한다. 같은 SQL·질문도 매번 실제 호출·과금하며, 기존 정보 캐시와 submit_sql 단계별 중복 판정은 그대로다.
- 결과 종류를 `result / sql_error / answer / service_error / cancelled`로 구분해 기록하고 phase·소요 시간·코인 전후를 남긴다. 공식 SQL 오류·실행 timeout(`success=false`)은 관측으로 전달되어 다음 행동이 가능하고, HTTP·계약 위반은 `failure.stage="execute_sql"` 서비스 오류다.
- ask_user의 유효 답변만 관측과 `dialogue_history`(질문/답변 + phase)에 기록한다. 실패·취소 시 답변을 만들지 않는다.
- 시스템 프롬프트에 새 도구의 목적·인자·가격, 탐색 성공이 제출·점수가 아님, 시작 시 과금·환불 없음, 사용 여부는 모델 자율임을 안내한다. 24회 행동·1500초·잔여 3코인 미만 종료 정책은 유지했다.

### HTTP 계약과 시간 (agent/environment.py, agent/server.py)
- `/execute` 응답을 `ExecuteResult(success, result, error)`로, `/ask` 응답을 비어 있지 않은 `answer`로 검증한다.
- 호출별 대기: execute_sql 120초(`DB_EXECUTE_SQL_TIMEOUT_SECONDS`), ask_user 660초(`USER_SIM_ASK_TIMEOUT_SECONDS`), 둘 다 세션 deadline과의 최솟값이며 재시도 0회.
- `/health`에 도구 목록·가격·두 timeout을 노출한다.

### 사용자 시뮬레이터 어댑터 (benchmark/user_sim/command_code_simulator.py)
- 공식 `user_simulator.server` 대신 이 모듈을 `--factory`로 기동한다. vendor는 수정하지 않으며 읽기 전용 마운트를 유지한다.
- 공식 provider 확장점(`shared._local_provider`)으로 Command Code `deepseek/deepseek-v4.1-flash`(temperature 0, 출력 상한 16384, timeout 300초, 재시도 0, effort 미지정)를 연결한다.
- 공식 `_call_llm`이 모델 오류를 빈 문자열로 삼키므로, 기동 시 1회 동일 인자로 위임하되 예외를 전파하는 seam을 설치했다. `/ask`는 모델 실패·빈 답변을 502로 전달한다. 요청별 계측은 contextvars로 격리해 동시 task 혼입이 없다.
- `/init_task`는 공식 핸들러를 호출한 뒤 `DB_ENV_URL`에서 스키마를 재로드한다(컨테이너 내부 localhost 문제 해결, 실측 schema_len 12909). `/phase_transition`·v2 프롬프트·세션 상태는 공식 그대로다.
- `/health`는 설정·누적 카운터·`api_key_present`(불리언)만 노출하고 키·프롬프트·정답은 응답·로그·결과에 남기지 않는다.

### 실행기·설정 (scripts/run-evaluation.ps1, compose.benchmark.yml)
- `.env`의 `CMD_API_KEY`를 스택 기동 전에 로드해 compose 보간과 에이전트가 같은 값을 쓴다.
- tools 모드에서 `/health.tools`의 9개 이름·가격과 시뮬레이터 연결 설정을 대조해 오래된 7개 도구 서비스 재사용을 거부한다. 비 tools 모드는 기존 검증만 한다.
- 시뮬레이터 컨테이너에는 필요한 값만 전달한다(CMD_API_KEY, DB_ENV_URL, USER_SIM_MODEL, USER_SIM_TIMEOUT_SECONDS=300, USER_SIM_MAX_OUTPUT_TOKENS=16384 등). 기본·보정 스택 모두 같은 어댑터를 사용한다.

### 실연결 확인 (scripts/verify_tool_connection.py)
- 한 task를 init한 뒤 `information_schema` 조회 1회와 공개 질문 1회로 `/execute`·`/ask`를 직접 호출한다. GT로 질문·SQL을 만들지 않고 제출·채점하지 않는다.
- 코드 SHA·추적 변경 여부(`code_dirty`)·vendor SHA·입력 해시·서비스 설정·시뮬레이터 호출/토큰 델타를 `results/tool_connection.json`에 기록한다(R-1).

### 주요 설계 결정
- provider 확장점만으로는 모델 오류 은폐를 막을 수 없어 기동 시 1회 seam을 함께 사용했다(요청별 전역 교체 아님).
- `/init_task`는 공식 경로를 유지하고 스키마만 실제 DB 대상에서 재로드해 "공식 경로 유지"와 "스키마 대상 일치"를 동시에 충족했다.
- R-1에서 미커밋 상태 실행 시 거부 대신 dirty 표시를 택했다. 사용자 변경을 stash/삭제하지 않고도 실행 내용을 식별할 수 있게 하기 위해서다.

## 설정과 실행 방법

- 모델: 에이전트·시뮬레이터 모두 Command Code `deepseek/deepseek-v4.1-flash`. 에이전트 출력 상한 16384·timeout 300초·effort/temperature 미지정. 시뮬레이터 내부 호출 출력 상한 16384·timeout 300초·temperature 0·재시도 0(공식 단계별 기본 상한 500/1024와 다른 실행 설정).
- 키: 프로젝트 `.env`의 `CMD_API_KEY`(값은 기록하지 않음).
- vendor SHA: 451fe2c3518ee1cf908d8139e2913483bd519381.
- 계약 테스트: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
- 도구 실연결 확인(보정 스택 기동 후): `.\.venv\Scripts\python.exe scripts\verify_tool_connection.py`
- 보정 smoke: `.\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1 -AgentMode tools -Calibrated -ResultName tools_smoke_task006 -OracleResultName oracle_smoke_task006`
- 30문제 후속 평가(범위 밖, 선택): `.\scripts\run-evaluation.ps1 -Limit 30 -Concurrency 10 -AgentMode tools -Calibrated -ResultName tools_dev30_calibrated_task006 -OracleResultName oracle_dev30_calibrated_task006`

## 완료 조건별 결과

| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 9개 도구 카탈로그·가격·인자 안내, `/execute`·`/ask` 경로·task_id·본문·응답 검증 계약 테스트(`EnvironmentContractTests`), 잘못된 인자 무호출·무차감(`test_invalid_new_tool_arguments_cost_nothing_and_call_nothing`), fixed 회귀(`tests/test_connection.py`) 통과. |
| AC-2 | 충족 | 성공 결과 → 다음 행동 → submit, 공식 SQL 오류 → 수정 → submit, HTTP 실패 → 서비스 오류·재시도 없음 테스트. 탐색 결과가 reward·phase·last_submit에 기록되지 않음. 공식 `/execute`가 SQL 오류·timeout을 `success=false` 200으로 반환함을 리뷰어가 vendor 코드로 확인. |
| AC-3 | 충족 | `test_phase2_question_reaches_the_same_simulator_session`(전환 1회 → 같은 task_id로 Phase 2 질문, phase=2), 실패·빈 답변·기한 취소 시 대화 미생성, 어댑터 동시 질문 격리(`test_concurrent_questions_keep_their_own_answers`), 비허용 state 비노출 회귀 통과. |
| AC-4 | 충족 | 1/2코인 시작 시 차감·환불 없음, 캐시 제외·동일 SQL/질문 재전송(`test_repeated_execute_sql_and_ask_user_are_sent_and_charged_again`), 제출 중복 차단 유지, 24회·1500초 상한, 기한 취소 시 결과 미상·확정 보상 보존, 재호출 무중복·reset·동시 요청 계약 테스트. phase 변경 후 동일 SQL 재실행은 전용 테스트가 없으나 새 도구에 캐시 키 자체가 없어 구조적으로 보장된다(리뷰어 확인). |
| AC-5 | 충족 | compose가 모델·키·DB 대상·timeout·출력 상한을 시뮬레이터에 전달, 모델·연결 실패는 502, 키·프롬프트 비노출 테스트(`test_health_reports_the_connection_without_the_key`, `test_calls_are_recorded_per_request_without_prompt_text`), 실행기 9개 도구·시뮬레이터 대조, `.env` 키 로딩 회귀(`tests/test_evaluation_env.py`) 통과. |
| AC-6 | 충족 | 보정 smoke(mental_1): oracle reward 1.0, tools 정상 오답 reward 0.0(error·failure 없음), execute_sql 4·ask_user 4·submit_sql 2 자율 선택. 71a3ec1에서 `/execute` 성공·모델 기반 `/ask` 정상 답변(시뮬레이터 내부 호출 2회). 메타데이터 두 파일에 SHA·입력 해시·명령·설정·호출/토큰·채점 기록. 원 smoke의 시뮬레이터 내부 토큰은 미검증(`null`과 사유 기록). |
| AC-7 | 충족 | 158개 테스트 통과(조율자 재실행 포함), `git diff --check a93c905 71a3ec1` 통과, 코드·테스트·실행 설정만 담은 `[TASK-006]` 로컬 커밋 3개, 문서 커밋·push 없음, 같은 버전·최종 SHA 리뷰 pass. |

## 검증 결과

| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` (조율자 재실행) | HEAD 71a3ec1, 추적 변경은 사용자 `.gitignore`뿐 | Ran 158 tests, OK | 9개 도구 계약·예산·캐시·기한·동시성·어댑터·연결 확인·기존 fixed 회귀 |
| `git diff --check a93c905 71a3ec1` (조율자 재실행) | 누적 diff | exit 0 | 공백 오류 없음 |
| `docker compose ... config -q` (기본 / 보정 결합) | 작업 트리 | 둘 다 exit 0 | 시뮬레이터 설정 전달·공통 환경 상속 |
| 보정 스택 `up --build -d --wait` | 보정 스택 | 3개 서비스 healthy | 보정 배선·어댑터 기동 |
| `scripts\verify_tool_connection.py` | 71a3ec1, 보정 스택, mental_1, 실제 모델 | 5/5 true, `/execute` 0.156초, `/ask` 11.719초, 시뮬레이터 호출 2·prompt 8813·completion 1401, `code_dirty=true`(`.gitignore`만), vendor clean | `/execute`·모델 포함 `/ask` 실연결(`results/tool_connection.json`) |
| `run-evaluation.ps1 -Limit 1 -Concurrency 1 -AgentMode tools -Calibrated ...` | b8de988과 동일 콘텐츠(사전 고정 해시 10/10 일치), 보정 스택 | oracle 1.0(1/1, 1/1), tools 0.0 정상 오답, 17회 모델 행동, 예산 22→0.5, 449.7초, 토큰 226627/24163/250790 | 자율 도구 선택 smoke(`results/tools_smoke_task006.json`, `oracle_smoke_task006.json`, `smoke_task006.metadata.json`) |
| smoke 도구 기록 집계 | 위 실행 | execute_sql 4(모두 성공), ask_user 4(모두 답변), submit_sql 2(공식 실패), 정보 4, 잘못된 행동 3(무차감 거부) | 새 도구의 실제 사용 경로 |

검증 코드와 최종 SHA 일치 근거: 최종 테스트와 연결 확인은 HEAD 71a3ec1에서 실행했고, 추적 변경은 실행 코드가 아닌 사용자 `.gitignore` 한 줄뿐이다. 원 smoke는 커밋 전 작업 트리(HEAD a93c905)에서 실행됐으나 사전 고정한 10개 파일 해시가 b8de988 blob과 모두 일치하고, b8de988→71a3ec1 사이에는 연결 확인 스크립트·그 테스트만 바뀌어 제품 동작은 같다.
검증 범위: 계약 테스트(모의 HTTP·모델)와 실제 DB/모델 검증(연결 확인 1회, smoke 1문제)을 구분한다. 자율 행동(smoke)과 직접 기능 확인(연결 스크립트)을 구분한다. 30문제 재평가·ablation·성능 비교는 명세대로 수행하지 않았다.

## 리뷰 처리 결과

검토 명세 버전: 1
검토한 코드 SHA: 71a3ec1091028708b3f090420c34dbc19dd87315 (1차 검토 b8de988 → 후속 재검토)
리뷰 판정: pass
판정 근거·독립 검증: 리뷰어가 누적 diff(a93c905..71a3ec1)와 후속 diff(b8de988..71a3ec1)를 검토하고 158개 테스트를 재실행했다. 결과 메타데이터의 산출물 해시와 에이전트 토큰 합계를 재계산해 일치를 확인했고, 메타데이터에 키 형태 문자열·`sol_sql`이 없음을 확인했다. 리뷰어는 Docker 기동·유료 모델 호출을 하지 않았다.
지적 처리:
- R-1(결과 메타데이터의 코드 SHA가 실제 실행 코드와 다르고 smoke 메타데이터가 없음) — 해결. f978552에서 추적 변경 여부를 `code_dirty`·`vendor_dirty` 등으로 기록, 71a3ec1에서 시뮬레이터 호출/토큰 전후 델타 기록, 71a3ec1로 연결 확인 재실행, `results/smoke_task006.metadata.json` 작성. 신규 테스트 5개 포함 158개 통과.

## 남은 제한

- 원 smoke의 시뮬레이터 내부 호출 수·토큰은 컨테이너 재생성으로 복구할 수 없어 `null`로 남겼다. 계측 경로 자체는 71a3ec1 연결 확인에서 동작을 확인했다. AC-6의 필수 항목(정상 응답 확인·기록)에는 영향이 없다.
- 실제 DB에서 Phase 2 ask_user는 실행되지 않았다(smoke가 Phase 1을 통과하지 못함). 전환 계약은 oracle 실행과 계약 테스트로만 확인했다.
- 공식 `/execute`는 시작 키워드만 검사하고 commit하므로 `WITH ... DELETE ... RETURNING` 또는 `EXPLAIN ANALYZE UPDATE` 같은 SQL이 task DB를 바꿀 수 있다. 명세가 서버 제한을 그대로 따르라고 했으므로 결함은 아니지만, 대규모 평가에서 채점 DB 오염 가능성이 있다.
- 어댑터 `/init_task`는 스키마 재로드 실패 시 502를 반환해 fixed·oracle 모드에서도 인프라 오류가 될 수 있다(같은 스택 내 조회라 발생 가능성 낮음).
- 공식 `init_task`의 컨테이너 내부 localhost 조회 경고 로그 1건이 남는다(vendor 수정 없이 제거 불가, 실제 스키마는 어댑터가 로드).
- `/ask`는 최대 600초 걸릴 수 있고 에이전트는 660초까지 대기한다. 실측 최장은 84.1초였다.
- `tracked_changes()`는 `git status` 실패 시 `code_dirty=false`로 기록될 수 있다(드문 경우).
- smoke 1문제는 기능 확인이며 9개 도구 구현의 성능 근거가 아니다. TASK-005의 7개 도구 30문제 수치를 승계하지 않는다.

## 필요한 보완·판단

필수 보완 없음. 사용자가 선택할 후속 후보:
1. 9개 도구로 고정 30문제·보정 DB·동시성 10 재평가(위 명령). 권장 — 새 도구의 실제 효과·비용을 보려면 필요하다.
2. 30문제 평가 전 execute_sql의 쓰기 가능 SQL 감시(도구 기록에서 DML 키워드 집계) 또는 차단 여부 결정. 평가 해석에 영향이 있어 1번보다 먼저 정하는 것을 권장한다.
3. 소규모 보완: phase 변경 후 동일 SQL 재실행 회귀 테스트, `code_dirty` 판정 불가 시 `null` 기록.
4. `docs/setup.md`의 "사용자 질문 도구를 추가하려면 시뮬레이터에 모델 의존성과 API 설정을 추가해야 한다" 문장이 이제 구현으로 대체됐다. 사용자가 요청할 때만 갱신한다.

## 직접 확인 방법

1. `.\.venv\Scripts\python.exe -m unittest discover -s tests` → `Ran 158 tests ... OK`
2. 보정 스택 기동 후 `.\.venv\Scripts\python.exe scripts\verify_tool_connection.py` → `results/tool_connection.json`의 검사 5개가 모두 true, `code_sha`가 71a3ec1…
3. `results/smoke_task006.metadata.json`과 `results/tools_smoke_task006.json`의 tool_trajectory에서 execute_sql·ask_user 호출과 정상 오답 채점 확인.

## 사용자 결정

결정: 승인
대상: 명세 버전 1, 최종 코드 71a3ec1091028708b3f090420c34dbc19dd87315
확인 근거: 2026-10-20 09:22 사용자가 채팅에서 "TASK-006 final.md 승인합니다. spec.md와 final.md를 문서 커밋해 주세요."라고 확인. 승인 시점 HEAD는 71a3ec1이며 제품 코드·테스트·실행 설정은 대상 SHA와 같다.
보존 문서: spec.md, final.md
