# TASK-006 리뷰 보고서

작업 ID: TASK-006
명세 버전: 1
대상 코드 버전: 71a3ec1091028708b3f090420c34dbc19dd87315
상태: pass
요약: 재검토. R-1(결과 메타데이터의 코드 SHA 오기록·smoke 메타데이터 부재)이 해결됐다. 후속 커밋은 연결 확인 스크립트의 메타데이터 기록만 바꾸고 제품 동작은 바꾸지 않았다. 누적 diff 기준으로 AC-1~AC-7을 모두 충족하고, 수정을 요구할 결함이 없다.
근거: `git diff b8de988 71a3ec1`(후속 수정)과 `git diff a93c905 71a3ec1`(누적) 검토, 158개 테스트 재실행, 재생성된 `results/tool_connection.json`·신규 `results/smoke_task006.metadata.json`의 내용·산출물 해시·토큰 합계 대조. 1차 검토(b8de988)의 vendor 대조·해시 검증은 제품 코드가 그 뒤 바뀌지 않았으므로 유효하다.
미확정 사항: 없음.

## 검토 범위
기준 커밋: a93c905ecb226f9b94afd16fe02490ca3185db15
검토 코드 커밋: 71a3ec1091028708b3f090420c34dbc19dd87315 (이전 검토 b8de9889a38535fa4b7c285053be2959cab45327, 후속 f978552847f31eba78f0070259e07cda2da1abf7·71a3ec1)
검토 명령: git diff a93c905ecb226f9b94afd16fe02490ca3185db15 71a3ec1091028708b3f090420c34dbc19dd87315 -- / git diff b8de988 71a3ec1 --
검증한 작업 공간 코드 버전: HEAD = 71a3ec1. `git diff --quiet 71a3ec1 -- agent benchmark scripts tests compose*.yml` 통과. 작업 공간의 추적 변경은 이 작업과 무관한 `.gitignore`(`.claude/`) 한 줄뿐이다. 후속 diff는 `scripts/verify_tool_connection.py`와 신규 `tests/test_verify_tool_connection.py` 두 파일이며 agent·어댑터·compose·실행기는 b8de988과 같다.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `agent/tools.py`가 9개 도구·공식 가격(1/2코인)·인자를 카탈로그와 `tool_metadata()`로 제공. `UNAVAILABLE_TOOLS` 제거, `validate_args`로 빈/잘못된 인자 거부. `EnvironmentContractTests`가 `/execute`·`/ask` 경로·`task_id`·본문·응답 형식을 검증하고 `test_invalid_new_tool_arguments_cost_nothing_and_call_nothing`이 무호출·무차감 확인. fixed 회귀(tests/test_connection.py) 통과. |
| AC-2 | 충족 | `_execute_action`이 success/sql_error/service_error/cancelled를 구분 기록하고 `_execute_observation`이 탐색 결과임을 명시. 공식 `/execute`는 SQL 오류·timeout을 `success=false` 200으로 반환함을 vendor 코드에서 확인. `test_execute_sql_result_feeds_the_next_action_and_scores_nothing`, `test_official_sql_error_is_an_observation_without_a_refund`, `test_execute_sql_http_failure_is_a_service_error_without_a_retry`가 흐름과 보상 미기록·서비스 오류 구분 확인. |
| AC-3 | 충족 | `_ask_action`이 유효 답변만 관측·`dialogue_history`(phase 포함)에 기록. `test_phase2_question_reaches_the_same_simulator_session`(전환 1회 → 같은 task_id로 /ask, phase=2), 실패·빈 답변·기한 취소 시 대화 미생성 테스트, 어댑터 `test_concurrent_questions_keep_their_own_answers`(contextvars 요청별 기록), 비허용 state 비노출 회귀 통과. |
| AC-4 | 충족 | 루프 진입 시 잔여 3코인 하한 때문에 1/2코인 도구는 항상 실행 전 예산을 만족한다. 시작 시 차감·환불 없음, 새 도구는 `_information`/캐시 경로를 거치지 않음, 동일 SQL·질문 재전송(`test_repeated_execute_sql_and_ask_user_are_sent_and_charged_again`), 기한 취소(`test_deadline_cancels_a_slow_execute_sql_without_a_refund`, `..._ask_user_without_inventing_an_answer`), 실패 후 재호출 무중복을 확인. phase 변경 뒤 같은 SQL 재실행은 별도 테스트가 없으나 새 도구에 캐시 키 자체가 없으므로 동작상 보장된다. |
| AC-5 | 충족 | compose `x-user-simulator`가 키·모델·DB 대상·timeout·출력 상한만 전달, vendor 마운트 읽기 전용. 어댑터가 provider 확장점 + 기동 시 1회 seam 교체로 모델 오류를 502로 전달하고 빈 답변도 502. `/health`·로그에 키·프롬프트 없음(`test_health_reports_the_connection_without_the_key`, `test_calls_are_recorded_per_request_without_prompt_text`). 실행기가 `/health.tools` 9개·가격과 시뮬레이터 연결을 대조. |
| AC-6 | 충족 | 보정 smoke(mental_1, oracle 1.0 / tools 0.0 정상 오답, execute_sql 4·ask_user 4·submit_sql 2 자율 선택)와 /execute·모델 포함 /ask 직접 확인을 결과 파일로 재확인했다. R-1 수정 후 `results/tool_connection.json`이 `code_sha=71a3ec1`·`code_dirty=true`(`.gitignore`만)·vendor clean·입력 SHA-256·시뮬레이터 호출 2·토큰 8813/1401을 기록한다. `results/smoke_task006.metadata.json`이 실행 콘텐츠 SHA(b8de988)·당시 HEAD·해시 근거·입력 해시·명령·설정·에이전트 17회/토큰·도구별 비용·채점·산출물 해시를 기록한다. smoke의 시뮬레이터 내부 호출·토큰은 복구할 수 없어 `null`과 사유로 남겼다(아래 미검증). |
| AC-7 | 충족 | 158개 테스트 통과, `git diff --check a93c905 71a3ec1` 통과, 후속 커밋 2개 모두 `[TASK-006] fix: ...` 제목, 누적 변경은 코드·테스트·실행 설정 11개 파일뿐이며 문서·results·private 미포함. |

## 수정 요청
없음.

## 독립 검증
- `git rev-parse HEAD` → 71a3ec1. `git diff --name-only a93c905 71a3ec1` → 11개 파일(코드·테스트·실행 설정).
- `.\.venv\Scripts\python.exe -m unittest discover -s tests` → `Ran 158 tests ... OK` (작업 공간 = 71a3ec1 코드).
- `git diff --check a93c905 71a3ec1` → 출력 없음.
- `results/tool_connection.json`(2026-10-20 09:09:15, 커밋 09:08:55 이후): code_sha 71a3ec1, code_dirty true / `[".gitignore"]`, vendor 451fe2c clean, data_sha256 55ca576e…, 5/5 검사 true, /execute 성공, /ask 200(11.7초), 시뮬레이터 호출 델타 2·토큰 8813/1401. 파일 SHA-256 `01ee6309…`가 smoke 메타데이터 기록과 일치.
- `results/smoke_task006.metadata.json`: 산출물 해시(tools `ada182d7…`, oracle `ded92560…`)를 리뷰어가 재계산해 일치 확인. 에이전트 17회·토큰 226627/24163/250790을 `tools_smoke_task006.json`의 model_call usage 합계로 재계산해 일치 확인. 도구별 호출·비용은 1차 검토의 trajectory 집계와 같다.
- 두 메타데이터 파일에서 키 형태 문자열(`Bearer `, `sk-…`, `CMD_API_KEY=`)과 `sol_sql` 문자열이 없음을 확인.
- 실행하지 않은 것: 리뷰어는 Docker 스택 기동·유료 모델 호출을 하지 않았다. 실DB Phase 2 ask_user와 원 smoke의 시뮬레이터 내부 호출·토큰은 미검증이다(구현 보고서와 메타데이터에 `null`과 사유로 명시). smoke 내 ask_user 4회는 HTTP 200 답변으로 확인됐고, 시뮬레이터 토큰 계측 경로 자체는 재실행한 연결 확인에서 확인됐다.

## 이전 지적 처리
### R-1: 결과 메타데이터의 코드 SHA가 실제 실행 코드와 다르고 smoke 메타데이터가 없음 — 해결
수정 횟수: 1회(커밋 f978552, 71a3ec1).
- 요청 1(dirty 기록): `tracked_changes()`가 `git status --porcelain --untracked-files=no` 결과를 파싱해 `code_dirty`·`code_uncommitted_paths`·`vendor_dirty`·`vendor_uncommitted_paths`를 기록한다. 실행 거부 대신 표시를 택했는데, 요청에서 허용한 선택지이고 사용자 변경을 보존하려는 근거가 타당하다. `tests/test_verify_tool_connection.py` 5개로 파싱(수정·staged·rename·따옴표)·git 부재·카운터 델타를 확인했다.
- 요청 2(재실행): 71a3ec1에서 재실행했고 결과가 정확한 SHA와 무관한 dirty 경로만 기록한다.
- 요청 3(smoke 메타데이터): 실행 콘텐츠 SHA와 당시 HEAD를 구분하고, 해시 근거·입력 해시·명령·설정·호출/코인/토큰·채점·산출물 해시를 기록했다. 복구 불가한 시뮬레이터 내부 토큰을 추정하지 않고 `null`로 둔 것은 명세의 기록 원칙에 맞는다.

## 별도 제안·판단 요청
명세 밖 제안이며 통과를 막지 않는다.
- execute_sql 쓰기 경로: 공식 `/execute`는 주석 제거 후 시작 키워드만 검사하고 `perform_query`가 commit한다. 따라서 `WITH d AS (DELETE ... RETURNING *) SELECT ...`나 `EXPLAIN ANALYZE UPDATE ...`는 허용되어 task DB를 바꿀 수 있다. 명세가 서버 제한을 그대로 따르라고 했으므로 결함은 아니다. 다만 30문제 평가에서 모델이 이런 SQL을 쓰면 채점 DB가 오염될 수 있다. 조율자가 후속으로 기록·모니터링(도구 기록에서 DML 키워드 집계) 여부를 판단하면 좋겠다.
- 어댑터 `/init_task`는 스키마 재로드에 실패하면 502를 반환한다. 공식 서버는 경고 후 빈 스키마로 진행했다. tools 모드에는 맞는 선택이지만 fixed·oracle 모드에도 같은 어댑터가 쓰이므로, 질문 도구를 쓰지 않는 모드에서도 스키마 조회 실패가 task 인프라 오류가 된다(oracle 러너는 예외를 삼킨다). 같은 스택에서 DB 환경 init 직후의 조회라 실제 발생 가능성은 낮다.
- phase 변경 후 동일 SQL execute_sql 재실행을 명시하는 테스트는 없다. 현재 구조상 보장되지만 회귀 방지용으로 한 줄 추가를 고려할 수 있다.
- 구현 보고서의 문서 후속 안내(`docs/setup.md` 문장 갱신)는 조율자 판단 사항으로 그대로 전달한다.
- (재검토 추가) `tracked_changes()`는 git 실행이 실패하면 빈 목록을 돌려줘 `code_dirty=false`가 된다. git 부재 시에는 `code_sha="unknown"`이 함께 기록되어 오해 소지가 작지만, `rev-parse`는 성공하고 `status`만 실패하는 드문 경우에는 깨끗한 것으로 기록될 수 있다. 상태를 알 수 없으면 `code_dirty: null`로 두는 방식을 후속으로 고려할 수 있다.
