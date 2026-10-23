# TASK-008 구현 보고서

작업 ID: TASK-008
명세 버전: 1
대상 코드 버전: c8db8f22e38a03616c1de21d54e014db087e9041
상태: ready_for_review
요약: 세션마다 Langfuse trace 1개를 만들고 모델 호출(generation), 도구 실행(span), 공식
제출 결과, 세션 점수와 오류 상태를 남긴다. 키가 없거나 Langfuse가 실패하면 완전한 no-op이며
에이전트 응답·state·HTTP 상태·채점 결과는 바뀌지 않는다. 실제 클라이언트는 서버 lifespan
시작 시에만 만들고, lifespan 없이 만든 테스트 앱은 셸에 키가 있어도 no-op이다.
근거: `agent/tracing.py`, `agent/tool_loop.py`, `agent/server.py`, `agent/model.py`,
`scripts/run-evaluation.ps1`, `tests/test_tracing.py`, `tests/test_evaluation_env.py`,
requirements/lock.
미확정 사항: Langfuse SDK가 span instrumentation scope에 프로젝트 라우팅용 public key를
자동 첨부한다(`scope.attributes.public_key`). secret key는 trace에 없다. 이 공개 키를
trace metadata에서도 완전히 제거하려면 SDK 내부 OTLP exporter를 대체해야 한다. 아래
"필요한 판단" 참고.

## 커밋과 검토 범위
기준 커밋: 9255f21b96891c5ed58df04ade15c5e37cecefc5
코드 커밋: c8db8f22e38a03616c1de21d54e014db087e9041
작업 커밋 목록:
- dbac6a56ece5a24ee8c2f600c711d94058aa6c95: 최초 구현
- 04184831f2d758acdebd224e5e3e72ae728f7c10: R-1·R-2 리뷰 수정
- c8db8f22e38a03616c1de21d54e014db087e9041: R-3 테스트 안정화
검토 명령:
- `git diff 9255f21b96891c5ed58df04ade15c5e37cecefc5 dbac6a56ece5a24ee8c2f600c711d94058aa6c95 --`
- `git diff dbac6a56ece5a24ee8c2f600c711d94058aa6c95 04184831f2d758acdebd224e5e3e72ae728f7c10 --`
- `git diff 04184831f2d758acdebd224e5e3e72ae728f7c10 c8db8f22e38a03616c1de21d54e014db087e9041 --`
현재 코드와 대상 SHA의 차이: 없음. `git status --short`는 untracked
`docs/agent/tasks/TASK-008/`만 보이며 추적 파일 변경은 없다.
기존 사용자 변경: `.env`(Langfuse 키 포함, 커밋 금지), `results/`, `.runtime-tmp/`의 실행
기록. stage·커밋하지 않았다. `private/`, `vendor/`, GT는 건드리지 않았다.

## 변경 내용

### 1. 추적 모듈 `agent/tracing.py` (신규)
- `Tracer`/`SessionTrace`/`Generation`/`ToolSpan`과 no-op 구현, Langfuse 구현을 분리했다.
- `create_tracer()`는 `LANGFUSE_PUBLIC_KEY`와 `LANGFUSE_SECRET_KEY`가 모두 있을 때만 SDK를
  import·생성한다. 하나라도 없으면 SDK를 import하지 않고 no-op을 돌려준다.
- `LANGFUSE_BASE_URL`을 SDK `base_url`로 넘긴다(SDK v5는 같은 이름의 환경변수도 지원).
- private `opentelemetry.sdk.trace.TracerProvider`를 만들어 넘겨 Langfuse가 프로세스 전역
  OTel provider를 교체하지 않게 격리했다. 기존 httpx 계측 등에 영향이 없다.
- 세션마다 root span을 만들고 `propagate_attributes(session_id=AGENT_TRACE_SESSION_ID,
  trace_name=...)`으로 평가 실행 묶음과 trace 이름을 지정한다. root metadata에 `task_id`,
  `agent`, `model`, `eval_run_id`, `code_version`을 남긴다.
- 모델 호출 generation: 입력 메시지, phase, call index, request/response model, 출력, usage
  (`prompt/completion/total` → `input/output/total`), 소요 시간, truncation, 시도 횟수,
  파싱된 action 또는 action_error를 남긴다. 실패 시 `level=ERROR`와 오류 메시지를 남긴다.
- 도구 실행 span: 인자, 결과, 비용, 예산 전후, cached/blocked/timed_out, 소요 시간을 남긴다.
  submit_sql은 `passed`·`reward`·`phase_completed`·`has_follow_up`·`message`를 추가로 남기고,
  phase 2 전환은 submit span의 child `phase_transition` span으로 표시한다.
- 세션 종료 시 `total_reward`(NUMERIC), `phase1_completed`·`phase2_completed`(BOOLEAN),
  `stop_reason`(CATEGORICAL) 점수를 만든다. 실패 세션은 점수를 만들지 않고 root를 ERROR로
  표시한다.
- 모든 추적 호출은 내부에서 예외를 삼키고 경고를 최대 한 번만 남긴다. SDK import·클라이언트
  생성·span 생성·flush 실패가 에이전트 동작을 바꾸지 않는다.

### 2. 루프·모델·서버 연결
- `agent/model.py`: `ChatReply.attempts`와 `ModelError.attempts`를 추가해 재시도 횟수를
  내부에서 전달한다. state 형식은 바꾸지 않았다.
- `agent/session.py`: `record_action()`이 방금 기록한 entry를 반환하도록 했다. state 형식은
  그대로다.
- `agent/tool_loop.py`: `run(..., tracer=None)`이 early-return(캐시·실패 재현) 후 실제 루프를
  돌 때만 trace를 시작한다. 모델 호출·정보 도구·execute_sql·ask_user·submit_sql의 모든
  `record_action` 경로에서 span을 시작·종료한다. phase 전환은 submit span 아래 child span으로
  남긴다. 정상 종료 시 state에서 점수를 만들고, ModelError/GradingError 시 trace를 오류로
  마킹한 뒤 예외를 다시 던진다. `finally`에서 root span을 항상 닫는다.
  `_session_loop()`은 기존 루프 본문을 옮긴 것으로 동작은 동일하다.
- `agent/server.py`: `create_app(..., tracer=None)`로 tracer를 주입할 수 있다. 주입하지 않으면
  실제 클라이언트를 앱 생성 시점에 만들지 않고 **lifespan startup**에서 만든다. lifespan 없이
  만든 앱(테스트의 `TestClient(app)`)은 `NoopTracer`를 쓰므로 셸에 Langfuse 키가 있어도 실제
  전송을 하지 않는다. `/run_session`은 응답을 반환하기 전에(오류 응답 포함)
  `tracer.flush(5초)`를 시도하고 예외를 무시한다. lifespan 종료 시에도 10초 상한으로 flush한다.
- `scripts/run-evaluation.ps1`: `.env`에서 세 Langfuse 값을 읽어 환경변수로 넘긴다(이미 있는
  환경값 우선, 값 출력 없음). `ResultName-UTC timestamp`를 `AGENT_TRACE_SESSION_ID`로,
  `git rev-parse HEAD`(+추적 파일 변경 시 `-dirty`)를 `AGENT_CODE_VERSION`으로 넘긴다.
  키가 있으면 trace session ID만 출력한다(키 아님).

### 3. 의존성
- `requirements.txt`에 `langfuse>=5,<6` 추가.
- `requirements.lock.txt`, `requirements.benchmark.lock.txt`를 실제 설치 결과로 갱신:
  `langfuse==5.0.2`와 전이 의존성(`backoff`, `wrapt`, `packaging`, `protobuf`,
  `requests`, `urllib3`, `charset-normalizer`, `googleapis-common-protos`,
  opentelemetry sdk/exporter/proto/semantic-conventions)을 고정했다.
- fresh venv에서 `requirements.benchmark.lock.txt` 설치 exit 0, `langfuse 5.0.2` import 확인.
  리뷰 수정은 의존성을 바꾸지 않았다.

## 완료 조건별 근거
- AC-1: `create_tracer()`는 키가 하나라도 없으면 SDK를 import하지 않고 no-op을 반환한다.
  실제 클라이언트는 lifespan startup에서만 만들어지고, lifespan 없이 만든 테스트 앱은
  `NoopTracer`를 쓴다. `DisabledTracingTests`(키 없음·키 하나·SDK import 실패)와
  `ServerTracerLifecycleTests`(lifespan 없는 앱에서 `create_tracer` 미호출, lifespan에서 1회
  생성·사용, 주입 tracer 미교체)가 확인한다. 가짜 `LANGFUSE_*` 환경변수와
  `server.create_tracer` 금지 상태에서 전체 158개 테스트가 통과했다(12.408초). 같은 조건의
  `tests.test_tool_loop`는 55개 5.634초로, 리뷰어 재현(49개 실제 tracer·230초)과 대조된다.
- AC-2: `SuccessfulTraceTests.test_one_session_records_model_calls_tool_spans_scores_and_phase_two`
  가 phase 1 실패 → phase 1 통과+follow-up → phase 2 통과 세션에서 trace 1개, 모델 호출 수만큼의
  generation(3), 도구 실행 수만큼의 span(3), phase_transition child span, submit span의
  passed/reward, root 점수 4개가 state와 일치함을 검증한다.
- AC-3: `FailureTraceTests`가 모델 500·grader 500에서 root/generation/span이 ERROR로 표시되고
  score가 비어 있음을 확인한다. 캐시 재호출(`run_again`)과 실패 재현 두 번째 호출이 새 trace를
  만들지 않음을 각각 확인한다.
- AC-4: `FlushTests`가 flush 예외·tracer flush 예외에서도 200 응답이 유지됨을 확인한다.
  `FlushParityTests`가 같은 가짜 서비스·모델로 (a) 성공 실행의 `NoopTracer` vs flush 예외/지연
  실행, (b) 모델 500의 예외/지연 실행과 grader 500의 예외 실행을 `NoopTracer`와 비교해
  status와 `response.json()`(성공은 무작위 `session_id`만 제외)이 완전히 같음을 확인한다.
  비교 전에 `tool_loop._now`와 `agent.model.time.monotonic`을 각각 `StepClock`으로 고정해
  루프 시간과 모델 응답 `elapsed_seconds`를 모두 결정적으로 만든다(R-3).
  `test_flush_is_bounded...`는 실제 시간에서
  0.4초 지연·0.05초 상한으로 응답이 0.35초 안에 오고 `session_elapsed_seconds < 0.2`임을,
  `test_a_completed_flush...`는 0.6초 flush를 2초 상한으로 실제 완료시키면서 응답 wall time은
  0.5초 이상이지만 `session_elapsed_seconds`는 0.5초 미만임을 확인한다.
- AC-5: `agent/tools.py`·`agent/environment.py`·시뮬레이터 어댑터는 무변경. `agent/tool_loop.py`의
  diff는 trace 인자·호출, 세션 루프 분리, phase 전환 span, generation 종료뿐이다.
  `agent/model.py`는 attempts 내부 필드만, `agent/server.py`는 tracer 주입·lifespan 생성·flush만
  바뀌었다. 기존 tools·session·model 계약 테스트가 기대값 변경 없이 통과한다.
- AC-6: 최종 결과 JSON·oracle JSON·agent stdout/stderr에서 세 키 문자열이 모두 0회다.
  trace 38개 observation에서 secret key와 base URL은 0회다. public key는 SDK가 각 span의
  `scope.attributes.public_key`에 자동으로 넣는다(프로젝트 라우팅용, 비밀값 아님). 아래
  "필요한 판단" 참고.
- AC-7: 커밋 `0418483...`에서 보정 1문제를 실행해 exit 0, oracle 1.0을 확인했다.
  agent 결과는 reward 0.0, budget_used 21.5, budget_remaining 0.5,
  `adk_events` 19, `tool_trajectory` 18이었다. Langfuse trace
  `c7749f5781952fb8e3f45fbcf21f3d79`(session `tools_smoke_task008_review-20261020T030714Z`)에
  observation 38개(1 root + 19 generation + 18 tool span)가 생겼고, generation/tool 수가
  결과 JSON과 정확히 일치한다. 점수는 total_reward 0.0, phase1/2 false,
  stop_reason `budget_exhausted_before_submit`이며 결과 JSON의 reward 0.0 및 final_response
  ("Stopped: 0.5 bird-coins remain and a submission costs 3")와 일치한다. trace metadata의
  `code_version`은 `04184831f2d758acdebd224e5e3e72ae728f7c10`다. lifespan startup에서 실제
  클라이언트가 만들어져 trace가 전송된 것도 이 실행으로 확인했다. 최종 커밋 `c8db8f2`는
  `tests/test_tracing.py`만 바꾸므로 제품 코드와 trace 근거는 이 smoke와 동일하다.
- AC-8: `langfuse`가 requirements와 두 lock에 반영됐고, 새 venv에서
  `python -m pip install -r requirements.benchmark.lock.txt`가 exit 0. 전체 테스트 158개와
  `tests.test_tracing` 20회 반복(0 실패), `git diff --check` 통과.
- AC-9: 코드·테스트·스크립트·requirements만 명시적으로 stage해 `[TASK-008]` 로컬 커밋
  `dbac6a5`·`0418483`·`c8db8f2`을 만들었다. 문서 커밋·원격 push는 하지 않았다.

## 실행한 검증
| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `git diff --check 9255f21 c8db8f2` | c8db8f2, 추적 파일 변경 없음 | exit 0 | whitespace 오류 없음 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | c8db8f2, 키 미설정 | 158 tests, OK (12.767s) | AC-1~AC-5, AC-8 |
| 같은 전체 테스트 + `LANGFUSE_PUBLIC_KEY/SECRET/BASE_URL` 가짜 값 + `server.create_tracer` 금지 | c8db8f2, 셸 키 상속 재현 | 158 tests, OK (12.871s) | R-1, AC-1 |
| `tests.test_tool_loop`만 같은 조건 | c8db8f2, 가짜 키 + `server.create_tracer` 금지 | 55 tests, OK (5.738s) | R-1 (리뷰어 재현은 230초) |
| `tests.test_tracing` 20회 반복 | c8db8f2, 키 미설정 | 20회 모두 OK, 0 실패 | R-3, AC-8 |
| `python -m venv <scratch>\venv-lock-check` + `pip install -r requirements.benchmark.lock.txt` | 새 Python 3.12 venv | exit 0, langfuse 5.0.2 import | AC-8 |
| `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task008_review -OracleResultName oracle_smoke_task008_review` | 0418483, `.env` 키 사용, calibrated stack | exit 0, oracle 1.0 | AC-7 |
| Langfuse API `observations.get_many(session_id=...)` | 위 실행 | trace 1개, observation 38 | AC-7 |
| Langfuse API `scores.get_many(trace_id=...)` | 위 trace | total_reward 0.0, phase1/2 false, stop_reason budget_exhausted_before_submit | AC-7 |
| 결과 JSON·oracle JSON·agent 로그 키 검색 | 위 실행 산출물 | 세 키 모두 0회 | AC-6 |
| trace observation 38개 키 검색 | 위 trace | secret key 0, base URL 0, public key 38(SDK scope 속성) | AC-6 |

검증 코드와 최종 SHA 일치 근거: 전체 테스트 2회와 `tests.test_tracing` 20회 반복은 최종
`c8db8f2` 트리에서 실행했다. `git status --short`는 untracked
`docs/agent/tasks/TASK-008/`만 보였다. smoke는 제품 코드 커밋 `0418483`에서 실행했고,
`0418483..c8db8f2` diff는 `tests/test_tracing.py`뿐이므로 smoke trace의
`code_version`(`0418483...`)이 최종 트리의 제품 코드와 일치한다. 결과 파일·로그는 ignored
경로이며 커밋에 포함되지 않았다.

## 미검증 항목과 제한
- 실제 smoke는 phase 1을 통과하지 못해 실제 phase 2 전환 trace는 만들지 못했다. phase 2
  전환·child span·phase2 점수는 가짜 Langfuse 클라이언트를 사용한
  `SuccessfulTraceTests`에서 검증했다.
- 결과 JSON에는 runner가 `stop_reason`을 싣지 않는다. trace 점수와 결과의 일치는
  `final_response` 문구·budget_remaining으로 대조했다.
- Langfuse trace에는 public key가 SDK의 instrumentation scope metadata로 들어간다. secret
  key는 들어가지 않는다.
- 리뷰의 비차단 제안 중 실행기 종료 후 `$env:` 복원, 예상하지 못한 예외의 trace 오류 표시,
  동시성 10에서 flush 지연 측정은 이번 수정 범위에 넣지 않았다.

## 리뷰 지적 처리
### R-1: 셸에 Langfuse 키가 있으면 테스트가 실제 전송을 시도한다 — 수정 완료
- 원인: `create_app()`이 호출 시점에 `create_tracer()`를 실행해, tracer를 주입하지 않은 기존
  테스트와 `agent.server` import 시의 전역 `app`도 셸 환경변수의 키로 실제 클라이언트를 만들었다.
- 수정: `create_app()`은 주입된 tracer가 없으면 `live_tracer=None`으로 두고 `NoopTracer`를
  fallback으로 쓴다. 실제 `create_tracer()`는 lifespan startup에서 한 번만 호출한다. lifespan을
  돌리지 않는 `TestClient(app)`과 ASGI transport 테스트는 항상 no-op이다. 주입한 tracer는
  lifespan이 교체하지 않는다.
- 검증: `ServerTracerLifecycleTests` 3개. 가짜 키 + `server.create_tracer` 금지 상태에서
  `tests.test_tool_loop` 55개(5.738초)와 전체 158개(12.871초) 통과. 리뷰어 재현(49개 실제
  tracer·230초·전송 시도 로그)은 재현되지 않는다.
- 수정 횟수: 1회. 수정 커밋: 0418483.

### R-2: AC-4 테스트가 "추적 없는 경우와 같다"를 직접 확인하지 않는다 — 수정 완료
- 수정: `FlushParityTests`를 추가했다. 같은 가짜 모델·서비스를 NoopTracer와 flush
  예외/지연 tracer로 각각 실행해 (1) 성공 200의 status·`response.json()`(무작위
  `session_id` 제외)과 (2) 모델 500(예외·지연)·grader 500(예외)의 502 status·본문이 완전히
  같음을 비교한다. 성공 비교는 `StepClock`으로 모든 시간 필드를 동일하게 고정했다.
- elapsed: `test_flush_is_bounded...`에서 상수 clock을 제거하고 실제 시간으로 바꿔
  0.4초 지연·0.05초 상한에서 wall time < 0.35초, `session_elapsed_seconds < 0.2`를 확인한다.
  `test_a_completed_flush...`는 0.6초 flush를 2초 상한으로 실제 완료시켜 wall time >= 0.5초,
  `session_elapsed_seconds < 0.5`를 확인한다.
- 검증: `tests.test_tracing` 23개, 전체 158개 통과.
- 수정 횟수: 1회. 수정 커밋: 0418483.

### R-3: 성공 parity 테스트가 실제 모델 시간 측정 때문에 간헐 실패한다 — 수정 완료
- 원인: `FlushParityTests.run_scenario`가 `tool_loop._now`만 `StepClock`으로 고정했다.
  `adk_events`의 모델 호출 `elapsed_seconds`는 `agent.model.time.monotonic()`으로 측정되어
  Windows 타이머 분해능 경계에서 두 실행이 `0.0`과 `0.015...`로 달라질 수 있었다.
- 수정: 같은 시나리오에서 `agent.model.time`을 `SimpleNamespace(monotonic=StepClock())`으로
  교체해 모델 응답 시간도 결정적으로 고정했다. 제품 코드는 바꾸지 않았다.
- 검증: `tests.test_tracing` 20회 반복 0 실패(수정 전 리뷰 재현은 20회 중 4회 실패), 전체
  158개 통과(12.767초). `git diff --check 9255f21 c8db8f2` exit 0.
- 수정 횟수: 1회. 수정 커밋: c8db8f2.

## 필요한 판단
AC-6의 "키 값"에 Langfuse public key까지 포함할지 판단이 필요하다. 현재 구현은 secret
key·base URL을 trace에 남기지 않고 결과/로그에는 세 값 모두 남기지 않는다. public key는
Langfuse SDK 5.0.2가 프로젝트 라우팅을 위해 모든 span의 `scope.attributes.public_key`에
자동으로 넣는 값이며, SDK 자체가 공개 클라이언트 식별자로 취급한다. 이를 trace metadata에서도
제거하려면 SDK 내부 OTLP exporter를 대체(헤더·endpoint 복제)해야 하고 SDK 버전 변경에 취약해진다.
권장안은 public key를 비밀값이 아닌 프로젝트 식별자로 허용하고, secret key 비노출을 AC-6의
기준으로 삼는 것이다. 엄격히 금지해야 한다면 후속 작업으로 exporter 격리를 진행해야 한다.

## 후속 문서 갱신 사항
- `docs/setup.md`에 Langfuse 설정 안내를 추가할 수 있다: `.env`의
  `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`·`LANGFUSE_BASE_URL`, 키가 없으면 추적이
  자동으로 꺼지며 실행 결과에는 영향이 없다는 점. 이번 명세에서 보고서 외 문서 수정은
  제외했으므로 반영하지 않았다.
