# TASK-008 작업 명세

작업 ID: TASK-008
명세 버전: 2
대상 코드 버전: 9255f21b96891c5ed58df04ade15c5e37cecefc5
상태: planned
요약: 9개 도구 에이전트의 세션·모델 호출·도구 실행·공식 제출 결과를 Langfuse Cloud에 trace로 남긴다. 키가 없거나 전송이 실패해도 에이전트 동작·결과·채점은 바뀌지 않는다.
근거: 사용자의 "langfuse 붙이기" 요청과 조율자 채팅의 결정(2026-10-20). 사용자는 다음을 정했다. Langfuse Cloud 사용(질문·스키마·시뮬레이터 답변·SQL 외부 전송 허용), 최종 점수는 에이전트가 받은 공식 제출 결과로 기록, 이번 범위는 에이전트만(시뮬레이터 내부 모델 제외), `langfuse` 의존성 추가 허용. 사용자가 `.env`에 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`·`LANGFUSE_BASE_URL`을 넣었다(값은 조율자도 보지 않았다).
미확정 사항: 없음. SDK 버전·API 형태 등 일상적 구현 선택은 구현자가 정한다.

## 목표

평가를 실행하면 문제(세션)마다 Langfuse trace 하나가 생기고, 그 안에서 다음을 볼 수 있다.
- 모델 호출: phase, 입력 메시지, 응답, usage, 소요 시간, 시도 횟수.
- 도구 실행(execute_sql·ask_user·정보 도구·submit_sql): 인자, 결과, 비용, 예산 전후, 소요 시간.
- 제출마다 공식 채점의 `passed`·`reward`·`message`. 세션 종료 시 `total_reward`·phase1/phase2 완료 여부·`stop_reason`.
- 한 번의 평가 실행(예: 30문제)이 Langfuse에서 하나의 묶음으로 조회된다.

사용자가 확인할 동작: `.env`에 키가 있는 상태에서 `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated ...`를 실행하면 Langfuse 프로젝트에 해당 문제 trace가 보이고, 결과 JSON의 reward·stop_reason과 trace 점수가 일치한다.

## 조사한 기준 코드

- `agent/server.py`: `/run_session`이 `agent.tool_loop.run`을 세션 lock 안에서 호출한다. `ModelError`·`GradingError`는 HTTP 502, `InputError`는 422. 반복 호출은 `loop_stopped`로 캐시된 응답을 돌려주고, 실패한 세션은 `restore_failure`로 같은 오류를 다시 낸다.
- `agent/tool_loop.py`: `run()`이 세션 1개를 처리한다. `_model_call()`(약 288행)이 모든 모델 호출의 길목이며 `adk_events`에 `model_call`을 기록한다. 도구 실행은 `_information`·`_execute_action`·`_ask_action`·`_submit_action`을 거쳐 `agent/session.py`의 `record_action()`으로 `tool_trajectory`에 남는다. `_submit_action()`이 공식 `SubmitResult`(`passed`·`message`·`reward`·`phase_completed`)를 받고, phase 1 통과 시 phase 2로 전환한다. `stop()`이 `session_elapsed_seconds`와 `stop_reason`을 기록한다.
- `agent/model.py`: httpx로 Command Code를 직접 호출하며 `_complete`가 최대 4회 시도한다. `ChatReply`에는 시도 횟수 정보가 없다.
- `scripts/run-evaluation.ps1`: `.env`에서 `CMD_API_KEY`만 읽어 환경변수로 넘기고(`Import-AgentApiKey`), 호스트에서 `uvicorn agent.server:app`을 `Start-Process`로 띄운 뒤 끝나면 `Stop-Process`로 강제 종료한다. 따라서 서버 종료 시점 flush에 기대면 trace가 유실된다.
- 의존성: `requirements.txt`(fastapi·httpx·uvicorn), `requirements.lock.txt`, 벤치마크용 `requirements.benchmark*.txt`. `opentelemetry-api`가 이미 lock에 있다.
- 테스트는 `.env`가 있는 저장소 루트에서 실행된다.

## 범위

### 1. 추적 모듈
- Langfuse 연동을 한 모듈(예: `agent/tracing.py`)에 모으고, 루프·모델·서버는 얇은 호출만 한다.
- `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`가 모두 있을 때만 활성화한다. 하나라도 없으면 완전한 no-op이며 네트워크 호출·경고 반복·예외가 없다. 주소는 `LANGFUSE_BASE_URL`을 사용한다. SDK가 다른 이름(예: `LANGFUSE_HOST`)을 쓰면 `LANGFUSE_BASE_URL`을 그쪽으로 전달한다.
- `.env`의 Langfuse 키를 에이전트 프로세스에 전달하는 방법(실행기에서 `CMD_API_KEY`처럼 읽어 넘기기, 또는 다른 방식)은 구현자가 정한다. 키 값을 로그·결과 파일·trace·예외 메시지에 남기지 않는다.
- 테스트 실행 시에는 `.env`에 키가 있어도 실제 전송이 일어나지 않아야 한다.

### 2. trace 구조
- 세션(`/run_session`의 루프 실행 1회) = trace 1개. 이름·메타데이터에 `task_id`, 에이전트 이름, 모델 ID를 둔다. 입력은 질문, 출력은 최종 응답.
- 평가 실행 1회를 묶는 식별자(Langfuse session 등)를 둔다. 실행기가 결과 이름·시작 시각 등으로 값을 만들어 넘기고, 없으면 서버 기동 단위 값을 쓴다.
- 코드 버전을 trace 메타데이터에 남긴다(예: 실행기가 `git rev-parse HEAD`와 워킹트리 변경 여부를 넘김). 비교 시 코드 SHA로 구분하기 위해서다.
- 모델 호출 = generation. phase, 입력 메시지, 응답, `response_model`, usage(입력·출력 토큰), 소요 시간, 절단 여부, 시도 횟수(재시도 포함)를 남긴다. 모델 호출이 실패하면 오류로 표시한다.
- 도구 실행 = span. 도구 이름, 인자, 결과(관찰 본문 또는 구조화 결과), 비용, 예산 전후, 중복 차단·캐시·거부 여부, 소요 시간.
- submit_sql span에는 공식 응답의 `passed`·`reward`·`phase_completed`·`message`를 남긴다. phase 2 전환도 보이게 한다.
- 세션 종료 시 trace 점수: `total_reward`(수치), `phase1_completed`·`phase2_completed`(불리언), `stop_reason`(범주). 값은 모두 `state`에 기록된 공식 결과에서 가져온다.

### 3. 오류와 재호출
- 모델·채점·전환 오류로 세션이 실패하면 trace를 오류 상태와 `failure.stage`·메시지로 표시하고, `total_reward` 0점 같은 점수를 만들지 않는다. 연결 오류와 오답을 구분하는 계약을 Langfuse에서도 유지한다.
- 캐시된 응답 반환(`loop_stopped`)이나 실패 재현(`restore_failure`)으로 루프가 실제로 돌지 않은 호출은 새 trace나 점수를 만들지 않는다.

### 4. 전송 보장
- 실행기가 서버를 강제 종료하므로, 세션 하나의 trace는 `/run_session` 응답이 나가기 전에 flush를 시도한다. flush는 시간 상한을 두고, 실패·timeout이 응답·state·HTTP 상태를 바꾸지 않는다. 서버 정상 종료 시에도 flush한다.
- flush 시간은 `session_elapsed_seconds`와 세션 기한(1500초) 계산에 들어가지 않는다.

### 5. 의존성
- `langfuse`를 `requirements.txt`에 추가하고 `requirements.lock.txt`(와 이를 포함하는 벤치마크 lock이 있다면 그것)를 실제 설치 결과로 갱신한다. 설치한 버전을 보고서에 적는다.

## 제약

- 에이전트의 행동·프롬프트·도구 목록·가격·예산·캐시·기한·재시도·세션 계약·결과 state(`adk_events`·`tool_trajectory` 등) 형식을 바꾸지 않는다. 시도 횟수를 trace에 남기려고 내부 반환값(`ChatReply` 등)에 필드를 더하는 것은 허용하되 state 형식은 그대로 둔다.
- Langfuse 쪽 실패(키 오류·네트워크·SDK 예외)는 에이전트 결과를 바꾸지 않는다. 추적 코드의 예외는 잡아서 무시하거나 경고 한 번만 남긴다.
- 시뮬레이터 내부 모델·oracle·채점기·vendor·GT·원본 문제·보정 데이터는 수정하지 않는다. GT·테스트 케이스를 trace나 에이전트 입력에 넣지 않는다(에이전트가 원래 보지 않는 정보다).
- `.env`·API 키·`results/`·`private/`·`vendor/`를 커밋하지 않는다.
- 보고서 외 문서 수정: 없음. README·`docs/setup.md`·상태 기록·지침을 수정하지 않는다. Langfuse 설정 안내가 필요하면 implementation.md에 후속 사항으로 남긴다.

문서 산출물: docs/agent/tasks/TASK-008/implementation.md (변경 내용·설정·실행 방법·검증 결과·정확한 SHA).
보존 정책: implementation.md·review.md는 로컬 작업 기록. 사용자 확인 후 spec.md·final.md를 한 번에 커밋.

## 완료 조건

- AC-1: Langfuse 키가 없으면 추적이 완전히 꺼지고, 기존 전체 테스트가 키·네트워크 없이 통과한다. 테스트는 `.env`에 키가 있어도 실제 전송하지 않는다. 키 미설정 상태에서 Langfuse 클라이언트가 만들어지지 않거나 아무 것도 보내지 않음을 테스트로 확인한다.
- AC-2: 가짜(테스트용) Langfuse 클라이언트나 SDK의 테스트 수단으로, 성공 세션 하나가 trace 1개·모델 호출 수만큼의 generation·도구 실행 수만큼의 span을 만들고, submit span의 `passed`·`reward`와 trace 점수(`total_reward`·phase 완료·`stop_reason`)가 state와 일치함을 테스트한다. phase 2 전환 세션을 포함한다.
- AC-3: 모델 실패·채점 오류 세션은 trace가 오류로 표시되고 점수 0을 만들지 않는다. 캐시된 재호출·실패 재현 호출은 새 trace를 만들지 않는다. 각각 테스트한다.
- AC-4: Langfuse 전송·flush가 예외·timeout을 내도 `/run_session` 응답 본문·HTTP 상태·state가 추적 없는 경우와 같다. flush 시간은 상한이 있고 `session_elapsed_seconds`에 포함되지 않는다. 테스트로 확인한다.
- AC-5: 에이전트 동작이 바뀌지 않는다. `agent/tools.py`·`agent/environment.py`·시뮬레이터 어댑터는 무변경이고, `agent/tool_loop.py`·`agent/model.py`·`agent/server.py`의 변경은 추적 호출(과 내부 시도 횟수 전달)뿐임을 diff로 설명한다. 기존 tools·세션·모델 계약 테스트가 기대값 변경 없이 통과한다.
- AC-6: 키 값이 로그·결과 JSON·예외 메시지에 나오지 않는다. secret key는 Langfuse trace에도 나오지 않는다. public key는 Langfuse SDK가 프로젝트 라우팅용으로 span의 instrumentation scope 속성에 자동으로 붙이는 경우에만 trace 안에 허용하고, 코드가 직접 trace metadata·입력·출력에 넣지 않는다. 결과 파일·서버 로그·trace를 키 문자열로 검색해 확인한다(검색 결과 출력에 키를 찍지 않는 방식으로).
- AC-7: 실제 실행: 사용자의 `.env` 키로 보정 1문제 smoke를 실행해 exit 0, oracle 1.0을 확인하고, Langfuse에 해당 trace가 생겼는지 API(또는 SDK 조회)로 확인한다. trace의 점수가 결과 JSON의 reward·stop_reason과 일치하는지, generation·span 수가 `adk_events`·`tool_trajectory`와 맞는지 보고한다. 같은 실행을 키 없이(추적 꺼짐) 수행할 필요는 없다.
- AC-8: `langfuse` 의존성이 requirements·lock에 반영되고 새 가상환경 설치가 가능하다(설치 명령과 버전 기록). 전체 테스트와 `git diff --check`가 통과한다.
- AC-9: 코드·테스트·스크립트·requirements만 명시적으로 stage해 `[TASK-008]` 제목의 로컬 커밋을 만들고 전체 SHA·명세 버전을 implementation.md에 기록한다. 문서 커밋·원격 push는 하지 않는다.

## 조사할 사항

- 현재 설치 가능한 `langfuse` Python SDK 버전과 API(트레이스·generation·span·score·flush), `LANGFUSE_BASE_URL` 지원 여부, asyncio 동시 세션 10개에서 trace 맥락이 섞이지 않는지.
- SDK가 OpenTelemetry 전역 설정을 바꿔 기존 동작(httpx 계측 등)에 영향을 주는지. 영향이 있으면 끄거나 격리한다.
- 테스트에서 `.env` 키를 읽지 않게 하는 가장 단순한 방법(에이전트가 `.env`를 직접 읽지 않고 실행기만 넘기면 자연히 해결되는지).
- 동시성 10 평가에서 세션별 flush가 다른 세션을 막거나 늦추는지.

## 명세 변경 이력

| 버전 | 변경 내용·이유 | 구현·리뷰에 미치는 영향 |
| --- | --- | --- |
| 1 | 최초 명세. 사용자 요청에 따라 에이전트 Langfuse 추적 추가 | agent/tracing.py(신규)·tool_loop.py·model.py·server.py·run-evaluation.ps1·requirements·테스트 |
| 2 | AC-6 명확화: Langfuse SDK 5.0.2가 모든 span의 `scope.attributes.public_key`에 public key를 자동으로 넣는다는 사실이 구현·리뷰에서 확인됨. public key는 같은 Langfuse 프로젝트의 공개 식별자이고 비밀값이 아니며, 사용자는 trace 데이터의 외부 노출을 허용했다. 제거하려면 SDK 내부 exporter를 대체해야 해 범위가 커진다. secret key 비노출과 로그·결과·예외의 세 키 비노출은 그대로 유지 | 코드 변경 없음. 리뷰어가 같은 기준으로 c8db8f2를 pass 판정해 재검토 불필요 |

## 전달 정보

명세 경로·버전: docs/agent/tasks/TASK-008/spec.md, 2
기준 커밋: 9255f21b96891c5ed58df04ade15c5e37cecefc5
작업 폴더: docs/agent/tasks/TASK-008/
커밋 제목 형식: [TASK-008] <type>: <변경 요약>
기존 미커밋 변경: 추적 파일 변경 없음. `.env`(사용자가 Langfuse 키 추가, 커밋 금지)와 무시된 로컬 기록·results만 있음.
직접 확인 방법: `.\.venv\Scripts\python.exe -m unittest discover -s tests` 통과, `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task008 -OracleResultName oracle_smoke_task008` 실행 후 Langfuse 프로젝트에서 해당 trace와 점수 확인.
