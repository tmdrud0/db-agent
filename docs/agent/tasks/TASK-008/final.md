# TASK-008 최종 보고서

작업 ID: TASK-008
명세 버전: 2
기준 코드 버전: 9255f21b96891c5ed58df04ade15c5e37cecefc5
대상 코드 버전: c8db8f22e38a03616c1de21d54e014db087e9041
상태: ready_for_user
요약: 에이전트 세션마다 Langfuse trace 1개를 만든다. 그 안에 모델 호출(generation), 도구 실행(span), 공식 제출 결과, 세션 점수와 오류 상태가 남는다. 키가 없거나 Langfuse가 실패하면 no-op이며, 에이전트 응답·state·HTTP 상태·채점은 바뀌지 않는다. AC-1~AC-9를 모두 충족했고 최종 SHA의 리뷰는 pass다.
근거: 아래 코드 위치·검증·리뷰 결과에 직접 기록했다. 조율자가 최종 SHA에서 전체 테스트·diff 검사·변경 범위를 다시 확인했다.
미확정 사항: 없음. public key가 trace에 들어가는 문제는 명세 v2의 AC-6 명확화로 정리했다(아래 "리뷰 처리 결과" 참고). 사용자 확인 대상이다.

## 변경 내용과 설계 결정

작업 코드 커밋:
- dbac6a56ece5a24ee8c2f600c711d94058aa6c95 — `[TASK-008] feat: trace agent sessions in Langfuse`
- 04184831f2d758acdebd224e5e3e72ae728f7c10 — `[TASK-008] fix: keep test apps away from Langfuse and prove flush parity` (리뷰 R-1·R-2)
- c8db8f22e38a03616c1de21d54e014db087e9041 — `[TASK-008] fix: freeze model timing in flush parity tests` (리뷰 R-3, 테스트만 변경)

누적 변경(`git diff --stat 9255f21 c8db8f2`): 11개 파일, +1766 / −36. 그중 신규 파일은 `agent/tracing.py`(562줄)와 `tests/test_tracing.py`(759줄)다.

1. **추적 모듈 `agent/tracing.py`(신규)**
   - Langfuse 연동을 이 모듈에 모았다. 루프·모델·서버는 tracer 객체의 얇은 메서드만 호출한다.
   - `create_tracer()`는 `LANGFUSE_PUBLIC_KEY`와 `LANGFUSE_SECRET_KEY`가 모두 있을 때만 SDK를 import해 클라이언트를 만든다. 하나라도 없으면 SDK를 import하지 않고 `NoopTracer`를 돌려준다. 주소는 `LANGFUSE_BASE_URL`을 SDK의 `base_url`로 넘긴다.
   - Langfuse 전용 OpenTelemetry `TracerProvider`를 따로 만들어 넘긴다. 그래서 Langfuse가 프로세스 전역 OTel 설정을 바꾸지 않는다.
   - 추적 코드의 예외는 모두 내부에서 삼키고 경고는 한 번만 남긴다. SDK import·생성·span·flush 어디서 실패해도 에이전트 동작은 바뀌지 않는다.

2. **trace 구조**

   | Langfuse 항목 | 내용 |
   | --- | --- |
   | trace(root span) | 세션 1회. metadata에 `task_id`·에이전트 이름·모델·`eval_run_id`·`code_version`, 입력은 질문, 출력은 최종 응답 |
   | Langfuse session | 평가 실행 1회. 실행기가 `<ResultName>-<UTC 시각>`을 `AGENT_TRACE_SESSION_ID`로 넘긴다 |
   | generation | 모델 호출. 입력 메시지·phase·call index·모델·출력·usage(input/output/total)·소요 시간·절단 여부·**시도 횟수**·파싱된 action 또는 action_error. 실패 시 ERROR |
   | tool span | 도구 실행. 인자·결과·비용·예산 전후·cached/blocked/timed_out·소요 시간 |
   | submit_sql span | 위 항목 + 공식 `passed`·`reward`·`phase_completed`·`has_follow_up`·`message`. phase 2 전환은 child span `phase_transition` |
   | trace 점수 | `total_reward`(수치), `phase1_completed`·`phase2_completed`(불리언), `stop_reason`(범주). 모두 state의 공식 결과에서 가져온다 |

   - 모델·채점 오류로 실패한 세션은 root를 ERROR로 표시하고 **점수를 만들지 않는다**. 오답(0점)과 연결 오류를 Langfuse에서도 구분하기 위해서다.
   - 캐시된 응답 반환이나 실패 재현처럼 루프가 실제로 돌지 않는 재호출은 trace를 만들지 않는다.

3. **루프·모델·서버 연결**
   - `agent/tool_loop.py`: `run(..., tracer=None)`은 early-return 뒤 실제 루프를 돌 때만 trace를 시작한다. 모든 `record_action` 경로에 span이 짝지어 있다. 기존 루프 본문은 `_session_loop()`로 옮겼고 동작은 같다.
   - `agent/model.py`: 재시도 횟수를 trace에 넘기려고 `ChatReply.attempts`·`ModelError.attempts` 내부 필드를 추가했다. state 형식은 그대로다.
   - `agent/session.py`: `record_action()`이 방금 기록한 entry를 반환한다(state 형식 무변경).
   - `agent/server.py`
     - `create_app(..., tracer=None)`으로 tracer를 주입할 수 있다.
     - 주입하지 않으면 실제 클라이언트는 **lifespan startup에서만** 만든다. 그래서 lifespan을 돌리지 않는 테스트 앱은 셸에 키가 있어도 항상 no-op이다(R-1 수정).
     - `/run_session`은 응답 직전에 `flush(5초 상한)`를 시도한다. 오류 응답도 마찬가지다. 서버 종료 시에는 10초 상한으로 flush한다.
     - 응답 전에 flush하는 이유: 실행기가 평가 후 서버를 `Stop-Process`로 강제 종료하므로, 종료 시점 flush에만 기대면 trace가 유실된다.
   - `scripts/run-evaluation.ps1`
     - `.env`에서 Langfuse 세 값을 읽어 환경변수로 넘긴다. 이미 있는 환경값을 우선하고, 값은 출력하지 않는다.
     - 평가 실행 묶음 ID와 `git rev-parse HEAD`(추적 파일이 바뀌었으면 `-dirty`)를 `AGENT_CODE_VERSION`으로 넘긴다.

4. **의존성**: `requirements.txt`에 `langfuse>=5,<6`을 추가했다. `requirements.lock.txt`·`requirements.benchmark.lock.txt`에는 `langfuse==5.0.2`와 전이 의존성(OpenTelemetry SDK·OTLP exporter·protobuf·requests 등)을 고정했다.

## 설정과 실행 방법

- `.env`에 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`·`LANGFUSE_BASE_URL`을 둔다. 커밋 금지.
- 둘 중 하나라도 없으면 추적이 자동으로 꺼지고, 평가 결과에는 영향이 없다.
- 평가 명령은 그대로다. 실행기가 키·실행 묶음 ID·코드 SHA를 에이전트 프로세스에 넘긴다. Langfuse UI에서는 Sessions의 `<ResultName>-<UTC 시각>`으로 한 번의 평가를 묶어 볼 수 있다.
- 기존 가상환경은 `pip install -r requirements.lock.txt`(또는 benchmark lock)로 `langfuse`를 설치해야 한다.

## 완료 조건별 결과

| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 키 미설정 시 SDK 미로드 no-op(`DisabledTracingTests`: 키 없음·하나만·SDK import 실패). 실제 클라이언트는 lifespan에서만 생성(`ServerTracerLifecycleTests`). 리뷰어가 가짜 키를 셸에 넣고 전체 158개를 3회 돌렸을 때 SDK 클라이언트 생성 0, `create_tracer` 호출 0 |
| AC-2 | 충족 | `SuccessfulTraceTests`: phase 1 실패 → phase 1 통과·후속 질문 → phase 2 통과 세션에서 trace 1, generation 3, tool span 3, `phase_transition` child, submit의 passed/reward, 점수 4개가 state와 일치 |
| AC-3 | 충족 | `FailureTraceTests`: 모델 500·grader 500에서 root/generation/span ERROR, 점수 없음. 캐시 재호출·실패 재현 호출은 새 trace 없음 |
| AC-4 | 충족 | `FlushParityTests`: flush 예외·지연 tracer와 NoopTracer의 응답 status·본문이 성공 200, 모델 500, grader 500 모두 동일(시계 고정). 실제 시계 테스트로 flush 상한(0.05초 상한에서 wall < 0.35초)과 `session_elapsed_seconds` 미포함 확인. 리뷰어 30회 반복 0 실패 |
| AC-5 | 충족 | `git diff --quiet 9255f21 c8db8f2 -- agent/tools.py agent/environment.py benchmark/` exit 0(조율자 재확인). 제품 변경은 추적 호출, `attempts` 내부 전달, `record_action` 반환값, tracer 주입·lifespan·flush뿐. 기존 tools·session·model 테스트는 기대값 변경 없이 통과 |
| AC-6 | 충족(명세 v2 기준) | 결과 JSON·oracle JSON·에이전트 stdout/stderr에서 세 키 모두 0회. trace observation 38개에서 secret key·base URL 0회. public key는 SDK가 붙이는 `scope.attributes.public_key`에만 있음(v2에서 허용) |
| AC-7 | 충족 | 0418483(제품 코드가 c8db8f2와 동일)에서 보정 1문제 smoke exit 0, oracle 1.0. Langfuse trace `c7749f5781952fb8e3f45fbcf21f3d79`에 root 1·generation 19·tool span 18. 결과 JSON의 `adk_events` 19·`tool_trajectory` 18과 일치. 점수 total_reward 0.0·phase1/2 false·stop_reason `budget_exhausted_before_submit`가 결과 JSON(reward 0.0, 예산 0.5 남음 응답)과 일치. trace의 `code_version`=0418483. 리뷰어가 API로 재조회해 같은 결과 확인 |
| AC-8 | 충족 | requirements·두 lock에 `langfuse==5.0.2`. 새 Python 3.12 venv에서 benchmark lock 설치 exit 0·import 확인(구현자). 전체 158 tests OK, `git diff --check` 통과 |
| AC-9 | 충족 | `[TASK-008]` 로컬 커밋 3개, 코드·테스트·스크립트·requirements만 포함(`.env`·results·private·vendor 없음, 조율자 재확인). 문서 커밋·push 없음 |

## 검증 결과

| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | c8db8f2, Langfuse 환경변수 없음(조율자·리뷰어·구현자) | Ran 158 tests, OK (약 13초) | 전체 계약·추적 테스트 |
| 같은 전체 테스트 + 가짜 `LANGFUSE_*` + `create_tracer` 금지/계측 | c8db8f2(구현자 1회·리뷰어 3회) | 158 OK, SDK 클라이언트 0 | 셸 키 상속 시 테스트 전송 없음(R-1) |
| `tests.test_tracing` 반복 | c8db8f2(구현자 20회, 리뷰어 30회) | 0 실패 | parity 테스트 안정성(R-3) |
| `git diff --check 9255f21 c8db8f2` | 최종 | exit 0 | 공백 오류 없음 |
| `git diff --quiet 9255f21 c8db8f2 -- agent/tools.py agent/environment.py benchmark/` | 최종(조율자) | exit 0 | 도구·환경·시뮬레이터 무변경 |
| `git diff --name-only 0418483 c8db8f2` | 최종(조율자) | `tests/test_tracing.py`만 | smoke 이후 제품 코드 무변경 |
| 새 venv + `pip install -r requirements.benchmark.lock.txt` | 새 Python 3.12 venv(구현자) | exit 0, langfuse 5.0.2 import | 의존성 재현 |
| `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task008_review -OracleResultName oracle_smoke_task008_review` | 0418483, `.env` 키, 보정 스택 | exit 0, oracle 1.0, agent reward 0.0(정상 오답, 예산 소진 종료) | 실제 DB 채점 + 실제 전송 |
| Langfuse API `observations.get_many` / `scores.get_many` | 위 실행의 session·trace | observation 38(1+19+18), 점수 4개가 결과와 일치 | trace 구조·점수 |
| 결과·로그·trace 키 문자열 검색(값 비출력) | 위 실행 산출물 | 결과·로그 세 키 0회, trace secret 0회·public 38회(SDK scope) | 키 비노출 |

검증 코드와 최종 SHA 일치 근거: HEAD = c8db8f2이고 추적 파일 변경이 없는 상태에서 전체 테스트·diff 검사를 실행했다. 미추적 파일은 `docs/agent/tasks/TASK-008/`뿐이다. 실제 smoke는 0418483에서 실행했다. 0418483..c8db8f2 diff는 테스트 파일뿐이므로 smoke의 제품 코드는 최종과 같다.
검증 범위: 계약 테스트는 최종 SHA에서 했고, 실제 DB 채점과 Langfuse 전송은 1문제 smoke 1회로 확인했다. 실제 phase 2 전환 trace, 동시성 10 평가의 flush 지연, c8db8f2에서의 smoke 재실행은 하지 않았다.

## 리뷰 처리 결과

검토 명세 버전: 1. AC-6은 리뷰어가 public key 허용 해석으로 판정했고, 이 해석이 명세 v2의 AC-6과 같다.
검토한 코드 SHA: c8db8f22e38a03616c1de21d54e014db087e9041
리뷰 판정: pass (1차 dbac6a5 수정 요청 R-1·R-2 → 0418483 수정 요청 R-3 → c8db8f2 pass)
판정 근거·독립 검증: 누적·후속 diff 검토, 전체 테스트 6회(정상 3·키 상속 3), `tests.test_tracing` 30회 반복, Langfuse API 재조회 2회로 trace 구조·점수·secret 비노출 확인. c8db8f2 smoke 재실행·새 venv 설치·동시성 10 측정은 하지 않음.
지적 처리:
- R-1(셸에 Langfuse 키가 있으면 테스트가 실제 전송을 시도해 `test_tool_loop` 230초): tracer 생성을 lifespan startup으로 옮겨 해결(0418483). 키 상속 환경에서 실제 클라이언트 0.
- R-2(AC-4 테스트가 "추적 없는 경우와 같다"를 직접 비교하지 않음): `FlushParityTests`와 실제 시계 elapsed 테스트를 추가해 해결(0418483).
- R-3(parity 테스트가 모델 시간 측정 때문에 20회 중 4회 간헐 실패): 모델 시계도 고정해 해결(c8db8f2). 30회 0 실패.
- 판단 요청(AC-6 public key): Langfuse SDK 5.0.2가 모든 span의 `scope.attributes.public_key`에 public key를 자동으로 넣는다. 조율자 판단은 이렇다. public key는 같은 Langfuse 프로젝트의 공개 식별자로 비밀값이 아니고, 사용자가 trace 데이터 외부 노출을 허용했다. 제거하려면 SDK 내부 exporter를 대체해야 해 버전 변경에 취약해진다. 그래서 명세 v2에서 AC-6을 명확히 했다. secret key는 trace 어디에도 없어야 하고, 로그·결과·예외에는 세 키 모두 없어야 하며, public key는 SDK scope 속성으로만 trace에 허용한다. 코드 변경이 없고 리뷰어 판정 기준과 같아 재검토는 필요 없다.

## 남은 제한

- **flush 지연**: `client.flush()`는 프로세스 전체 버퍼를 비우므로, 동시성 10 평가에서 응답마다 최대 5초가 더해질 수 있다. 세션 시간(`session_elapsed_seconds`)과 기한에는 포함되지 않지만 평가 전체 wall time은 늘 수 있다. 아직 측정하지 않았다.
- **실제 phase 2 trace 미확인**: 실제 smoke는 phase 1을 통과하지 못했다. phase 2 전환·child span·phase 2 점수는 가짜 클라이언트 테스트로만 확인했다.
- **결과 JSON에 `stop_reason` 없음**: 실행기 결과 JSON에는 `stop_reason`이 실리지 않는다. 그래서 trace 점수와의 대조는 `final_response` 문구·예산으로 했다.
- **예상하지 못한 예외**: `ModelError`·`GradingError` 외의 예외에서는 trace가 ERROR로 표시되지 않는다. root span은 닫히고 점수는 만들지 않는다.
- **환경변수 복원**: 실행기가 설정한 `LANGFUSE_*`·`AGENT_TRACE_SESSION_ID`·`AGENT_CODE_VERSION`은 실행 후 PowerShell 세션 환경에 남는다.
- **결과 비교**: TASK-007 이전 결과와 성능을 비교할 때는 코드 SHA로 구분한다. 추적 자체는 에이전트 행동을 바꾸지 않는다.

이 제한들은 필수 완료 조건과 유지할 계약(동작·state·채점·오류 구분)을 위반하지 않는다.

## 필요한 보완·판단

필수 보완 없음. 후속 후보(사용자 판단):
- 동시성 10 평가에서 flush 지연 측정. 필요하면 세션 단위 flush 방식 개선.
- 예상하지 못한 예외도 trace를 ERROR로 표시.
- 실행기 종료 후 `$env:` 값 복원.
- 결과 JSON에 `stop_reason` 포함(실행기 변경).
- `docs/setup.md`에 Langfuse 설정 안내 추가(이번 명세의 보고서 외 문서 수정 제외 범위).

## 직접 확인 방법

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
기대: `Ran 158 tests ... OK`.

```powershell
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task008 -OracleResultName oracle_smoke_task008
```
기대: exit 0, oracle 1.0. 실행기가 출력한 trace session ID(`tools_smoke_task008-<UTC 시각>`)로 Langfuse UI의 Sessions에서 trace 1개를 찾을 수 있다. trace의 generation 수는 결과 JSON의 `adk_events` 수와, tool span 수는 `tool_trajectory` 수와 같다. 점수 `total_reward`는 결과의 reward와 같다.

## 사용자 결정

결정: 승인
대상: 명세 v2, 최종 코드 c8db8f22e38a03616c1de21d54e014db087e9041
확인 근거: 2026-10-20 조율자 채팅에서 사용자가 final.md 설명(AC-6 public key 허용 판단 포함)을 들은 뒤 "커밋 ㄱㄱ"로 승인
보존 문서: spec.md·final.md
