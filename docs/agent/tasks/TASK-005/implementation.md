# TASK-005 구현 보고서

작업 ID: TASK-005
명세 버전: 1
대상 코드 버전: a93c905ecb226f9b94afd16fe02490ca3185db15
상태: ready_for_review
요약: 모델이 공식 도구 7개 중 하나를 매 턴 JSON 행동으로 고르는 자체 루프를 추가했다. execute_sql·ask_user는
제공·실행되지 않고, 도구 가격은 공식 1/0.5/3 코인이다. 기존 고정 모드는 그대로 보존되고 `AGENT_MODE`와
health로 두 모드가 구분된다. 보정 스택의 고정 30문제에서 oracle 30/30, 자체 루프 Phase 1 4/30·Phase 2 4/30,
보상 4.0을 인프라 오류 없이 기록했고 기존 고정 기준선(Phase 1 5/30, 보상 4.7)과 비교했다.
리뷰 R-1·R-2를 후속 커밋에서 수정했다: 중복 제출 판정이 문자열·주석의 의미를 보존하고(2차 수정에서 개행으로
연결되는 인접 문자열 상수와 비ASCII 달러 인용 태그까지 보존), 세션 기한이 모델 응답 뒤와 진행 중 대기까지 적용된다.
근거: 결과 JSON `results/deepseek_v41_flash_tool_loop_dev30_calibrated.json`,
`results/oracle_tool_loop_dev30_calibrated.json`, 계약 테스트 39개(전체 121개), 이 보고서의 실행·검증 기록.
미확정 사항: 없음. 한 회차 비교라 모델 변동을 분리하지 못한 점과 수정 커밋에 대한 실제 DB 재실행이 없다는 점은 제한으로 기록한다.

## 커밋과 검토 범위

기준 커밋: 7e7e462732aa6303efd949279202d71f743ba624 (spec.md의 대상 코드 버전과 일치, 리뷰 수정에서도 유지)
코드 커밋: a93c905ecb226f9b94afd16fe02490ca3185db15
작업 커밋 목록:
- abca8a5988d6a67e1f2f3443d0cddb41af94dbcf `[TASK-005] feat: add the tool-selecting two-phase loop`
- 421b4b0bd3e00d768551f4b5a6980d48b3517a71 `[TASK-005] fix: run the evaluation script without an interactive console`
- bb6bbe42b38cad05734622225daed468ec09cd47 `[TASK-005] fix: preserve SQL meaning and enforce the session deadline` (리뷰 R-1·R-2 1차 수정)
- a93c905ecb226f9b94afd16fe02490ca3185db15 `[TASK-005] fix: keep newline-adjacent strings and non-ASCII dollar tags distinct` (리뷰 R-1 2차 수정)
검토 명령: `git diff 7e7e462732aa6303efd949279202d71f743ba624 a93c905ecb226f9b94afd16fe02490ca3185db15 --`
리뷰 대응 범위: 1차 검토 대상은 421b4b0, 1차 수정은 bb6bbe4, 2차(재검토) 수정은 a93c905다.
`git diff --stat bb6bbe4 a93c905`는 `agent/tool_loop.py`, `tests/test_tool_loop.py` 두 파일이다(제품 동작 변화는 중복 키 판정으로 한정).
현재 코드와 대상 SHA의 차이: 없음. 추적 파일의 미커밋 변경 없음(`git status --short`는 미추적 문서만 보고).
`git diff --name-only abca8a5988d6a67e1f2f3443d0cddb41af94dbcf 421b4b0b`는 `scripts/run-evaluation.ps1` 하나이며,
Python 제품 코드는 그 두 커밋에서 동일하다. 30문제 실행은 이 스크립트 수정이 워킹 트리에 있던 시점(아래 실행 기록)에
돌았고, 제품 코드 기준으로는 abca8a5 = 421b4b0이다. bb6bbe4·a93c905는 실제 DB 평가를 다시 실행하지 않았다(사용자 지시).
기존 사용자 변경: `docs/agent/tasks/TASK-004/`(미추적)를 건드리지 않았다. 커밋에 포함하지도 않았다.

## 변경 내용

새 파일:
- `agent/tools.py`: 허용 도구 7개의 이름·가격(1/0.5/3)·인자·설명·실행 함수. `UNAVAILABLE_TOOLS`로
  execute_sql·ask_user를 명시적으로 제외하고, 단일 JSON 행동 파싱(`parse_action`)과 인자 검증을 담당한다.
- `agent/tool_loop.py`: 자체 루프. 시스템 프롬프트에 도구 목록·가격·행동 형식·상한·캐시·중복 정책을 넣고,
  모델 대화 메시지로 행동/관측을 주고받는다. 예산·상한·캐시·중복 차단·페이즈 전환을 코드가 통제한다.
- `agent/session.py`: 고정/자체 루프가 공유하는 세션 상태 헬퍼(예산, 질의 해석, 종료 기록, 행동 기록, 실패 복원).
- `tests/test_tool_loop.py`: 자체 루프 계약 테스트 28개.

수정 파일:
- `agent/loop.py`: 공유 헬퍼를 `agent/session.py`로 옮기고 이름만 공개 이름으로 바꿨다. 동작 변화 없음.
- `agent/model.py`: 한 번의 왕복 호출을 `_complete`로 분리하고 다중 메시지 `chat()`과 `ChatReply`(truncated 포함)를
  추가했다. 기존 `generate_sql()`의 요청·오류 계약은 그대로다.
- `agent/environment.py`: `/column_meaning`, `/knowledge_names`, `/knowledge`(단일 항목) 호출과 응답 검증 추가.
- `agent/server.py`: `AGENT_MODE`(fixed|tools)와 `create_app(mode=...)`로 모드를 명시 선택하고 `/health`에
  `agent`·`mode`를 보고한다. 실행기는 모드별로 고정 루프/자체 루프를 고른다.
- `scripts/run-evaluation.ps1`: `-AgentMode`(기본 fixed)와 모드별 health 검증, 에이전트 기동 시 `AGENT_MODE` 전달.
  비대화형 호스트에서 docker compose의 stderr가 종료 오류로 승격돼 스크립트가 죽던 문제를 `Invoke-External`로 고쳤다.

루프 정책(명세 범위):
- 가격: get_schema 1, get_all_column_meanings 1, get_column_meaning 0.5, get_all_external_knowledge_names 0.5,
  get_knowledge_definition 0.5, get_all_knowledge_definitions 1, submit_sql 3. 공식 `TOOL_COSTS`와 동일하다.
- 예산: 행동 실행 전에 확인한다. 남은 코인이 제출 가격(3, 도구 중 최고가)보다 적으면 아무 호출도 하지 않고
  `budget_exhausted_before_submit`으로 끝낸다. 음수 예산이 생기지 않는다.
- 상한: 모델 행동 24회, 세션 1500초. 기한은 행동 사이, 모델 응답 직후, 각 외부 호출 직전에 확인하고
  대기 자체도 남은 시간으로 제한한다(리뷰 R-2 수정).
- 중복 정보 조회: 같은 도구·인자는 세션 캐시(첫 성공 기록)로 응답하고 서비스를 다시 부르지 않는다. 공식 가격은 그대로 차감한다.
- 같은 단계 동일 SQL: 토큰 사이 공백만 다른 같은 SQL은 제출하지 않고 비용 없이 거부한다. 문자열·인용 식별자·주석·
  달러 인용 본문의 내용이 다르거나, 인접한 두 문자열 상수 사이의 공백 구간이 다르면 다른 SQL로 보고 제출한다
  (2차 수정에서 `'a' 'b'` vs `'a'\n'b'`, `$한$…$한$` 포함). 단계가 다르면 허용한다(리뷰 R-1 수정).
- 잘못된 행동(JSON 아님, 다중 객체, 금지 도구, 인자 누락·초과): 비용 없이 그 턴을 소비하고 오류를 모델에 돌려준다.
  출력 상한으로 잘린 응답도 같은 경로로 처리하되, `finish_reason=length`여도 완전한 JSON 행동이면 유효한 행동으로 실행한다.
- 페이즈: Phase 1 성공 + 후속 있음일 때만 공식 user simulator 전환을 정확히 한 번 호출한다. 전환 결과는
  `state.phase_transition`에 기록한다. 보상은 공식 응답 값만 합산한다.
- 모델 입력: 질문, DB 이름, 예산, 허용 도구 목록, 모델 자신의 행동, 공식 관측만 넣는다. state의 다른 키는 보내지 않는다.

리뷰 R-1·R-2 수정(커밋 bb6bbe4):
- R-1: `_normalize_sql`(모든 공백 축약)을 없애고 `submission_key()`로 바꿨다. 토큰 사이의 공백 연속만 하나로 줄이고,
  문자열 리터럴(`''` 이스케이프 포함)·인용 식별자(`""`)·달러 인용(`$tag$…$tag$`)·줄 주석(개행 포함)·중첩 블록 주석의
  내용은 그대로 둔다. 이스케이프 문자열(`E'…'` 등 백슬래시가 있는 인용)과 닫히지 않은 인용·주석은 범위를 확정할 수
  없으므로 원문 그대로를 키로 삼는다(차단 범위가 좁아질 뿐 잘못 차단하지 않는다). 제출 이력에는 실제 제출한 SQL을 남기고
  비교할 때만 키를 쓴다.
- R-2: `_bounded()`가 모든 외부 대기(모델 응답, 정보 조회, 제출, 단계 전환)를 남은 세션 시간으로 제한하고, 초과 시
  `SessionExpired`로 정상 종료(`session_time_limit`)한다. 기한 확인을 모델 응답 직후와 각 호출 직전에 추가했다.
  진행 중 취소된 제출·전환은 통과·오답·서비스 오류로 기록하지 않고 `timed_out`과 "outcome is unknown"으로 남긴다.
  이미 확정된 Phase 1 보상은 유지한다. 취소가 공식 서비스 상태를 되돌리지 않는다는 점을 코드 주석과 기록으로 남겼다.

R-1 2차 수정(커밋 a93c905):
- 인접 문자열 상수 사이의 공백 구간은 원문 그대로 키에 남긴다. PostgreSQL에서 이 구간에 개행이 있으면 두 상수가 하나로
  연결되지만(`'a'\n'b'` = `ab`), 개행 없이 공백만 있으면 문법 오류다(`'a' 'b'`). 개행 유무가 구문을 바꾸므로 공백 축약으로는
  같은 SQL이 될 수 없다. 새 헬퍼 `_starts_string_constant()`가 다음 토큰이 문자열 상수로 시작하는지 판단하며 접두 형태
  (`E'…'`, `B'…'`, `X'…'`, `N'…'`, `U&'…'`)도 포함한다. 이 판단은 넓게 잡을수록 키가 더 세분화돼(차단 범위가 좁아져)
  잘못 차단할 가능성이 줄어드는 방향으로만 작동한다.
- `DOLLAR_QUOTE` 태그 정규식을 미인용 식별자 문자 규칙(첫 글자 영문·`_`·비ASCII, 이후 영문·숫자·`_`·비ASCII)으로 넓혀
  `$한$…$한$` 같은 유효한 비ASCII 태그의 본문을 문자열로 인식해 보존한다.
- 다른 토큰 사이의 공백 축약, 문자열·인용 식별자·주석·달러 인용 본문 보존, 모호한 구문(이스케이프 문자열, 닫히지 않은
  인용·주석)의 원문 대체는 1차 수정 그대로다.

## 완료 조건별 근거

- AC-1: `tests/test_tool_loop.py::ToolLoopContractTests::test_each_allowed_tool_uses_its_official_endpoint_and_price`가
  7개 도구의 경로·인자·응답 파싱·가격·예산 변화를 확인한다. `ActionParsingTests::test_only_the_official_prices_of_the_seven_tools_are_registered`가
  가격표와 금지 도구 부재를, `test_unavailable_actions_are_rejected_without_calling_them`이 execute_sql·ask_user·미지
  행동이 호출 없이 거부됨을 확인한다. 같은 테스트에서 모델 요청 본문에 `execute_sql`·`ask_user`가 없음도 확인한다.
- AC-2: `test_failed_submission_feedback_retry_and_single_transition`이 오답 → 수정 제출 → Phase 1 성공 → 전환 1회 →
  Phase 2 성공의 순서와 보상 1.0을 확인한다. 실제 30문제에서 Phase 1을 통과한 4문제는 모두 Phase 2까지 통과했다.
- AC-3: 예산 경계(`test_budget_below_the_submission_price_stops_without_any_call`, `test_a_budget_of_three_submits_without_exploration`,
  `test_budget_spent_below_the_submission_price_stops_before_the_next_action`), 캐시(`test_repeated_information_lookup_is_cached_and_still_charged`),
  중복 SQL(`test_duplicate_sql_is_blocked_per_phase_without_a_charge`, `SubmissionKeyTests` 5개,
  `test_duplicate_key_keeps_meaningful_sql_changes_submittable`), 잘못된 행동(`test_unusable_model_responses_are_fed_back_without_charging`),
  상한(`test_action_limit_stops_the_session`, `test_session_time_limit_after_an_action_stops_before_the_next_model_call`,
  `test_session_time_limit_after_the_model_response_blocks_the_action`, `ToolLoopDeadlineTests` 3개), 모델·서비스 오류
  (`ToolLoopFailureTests`), 세션 재사용·reset·동시 요청(`test_repeated_run_returns_the_cached_outcome_without_extra_calls`,
  `test_reset_starts_a_new_session_and_reset_false_reuses_it`, `ToolLoopConcurrencyTests`)을 확인한다.
  R-1·R-2 관련 테스트는 1차 수정에서 11개를 추가했다(중복 키 단위 5, 중복 키 동작 1, 기한 5. 이 중 기한 테스트 1개가
  기존 `test_session_time_limit_stops_the_session`을 대체해 전체 테스트는 110 → 120개가 됐다). 2차 수정에서는 중복 키
  단위 테스트 1개(`test_adjacent_string_constants_keep_their_separating_whitespace`), 비ASCII 태그 단언, 동작 회귀 2개
  (`test_duplicate_key_keeps_meaningful_sql_changes_submittable` 하위 사례)를 추가·확장해 전체 테스트는 120 → 121개다.
- AC-4: `test_health_reports_the_selected_mode`가 `AGENT_MODE`·`create_app(mode=...)`·health의 agent/mode 구분과
  잘못된 모드 거부를, `test_state_beyond_the_allowed_inputs_never_reaches_the_model`이 임의 state 비노출을 확인한다.
  `scripts/run-evaluation.ps1`은 요청한 모드와 다른 에이전트가 6000에 있으면 오류를 낸다.
- AC-5: 이 보고서에 코드 SHA·검증 명령·결과·제한을 기록했다. 독립 리뷰 판정 전이므로 준비 완료로 표시하지 않는다.
- AC-6: 아래 실제 30문제 비교를 기록했다. 한 회차 비교이며 반복 측정이 아니다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 코드 bb6bbe4, 추적 파일 변경 없음, 모의 모델·채점 | 120 tests OK (exit 0) | 기존 82개 회귀 + 자체 루프 계약 38개(리뷰 회귀 11개 포함) |
| `.\.venv\Scripts\python.exe .runtime-tmp\task005-review\reproduce.py` | 코드 bb6bbe4, 리뷰어가 남긴 재현 스크립트(수정 전 동작을 assert) | exit 1, R-1 assert에서 실패 | 수정 전에는 제출 1회·차단 23회였고 이제 수정 SQL이 제출돼 R-1이 재현되지 않는다 |
| `verify_fixes.py`(같은 재현을 값으로 출력, 스크래치패드 로컬) | 코드 bb6bbe4, 모의 모델·서비스 | R-1: 제출 2회·차단 0·보상 0.7·`phase1_completed`. R-2: 제출 0회·예산 18 유지·`session_time_limit`·보상 0 | 리뷰 지적 두 건의 수정 후 동작 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 코드 a93c905(커밋 후), 추적 파일 변경 없음, 모의 모델·채점 | 121 tests OK (exit 0) | 2차 회귀(인접 문자열·비ASCII 태그, 동작 사례 2개) 포함, 기존 120개 유지 |
| `.\.venv\Scripts\python.exe .runtime-tmp\task005-review\recheck_bb6bbe4.py` | 코드 a93c905, 리뷰어가 남긴 잔여 재현 스크립트(수정 전 동작을 assert) | exit 1, "R-1 newline" 사례 assert에서 실패 | 잔여 버그가 더는 재현되지 않는다(수정 SQL이 차단되지 않아 제출이 2회로 늘었다) |
| `.\.venv\Scripts\python.exe .runtime-tmp\task005-fix2\verify_r1_residual.py` | 코드 a93c905, 리뷰어와 같은 mock 하네스에 수정 후 기대값으로 작성 | exit 0 | 세 R-1 사례 모두 제출 2회·중복 차단 0·보상 0.7·`phase1_completed`. 동일 SQL 24회는 제출 1회·차단 23회·`action_limit_reached` 유지. R-2 응답 후 차단·대기 취소도 유지 |
| `.\scripts\run-evaluation.ps1 -Calibrated -Limit 1 -Concurrency 1 -AgentMode tools -ResultName 'deepseek_v41_flash_tool_loop_calibrated_smoke' -OracleResultName 'oracle_tool_loop_calibrated_smoke'` | 코드 abca8a5, 보정 스택, 문제 mental_1 | exit 0, oracle Phase1·2 1/1 보상 1.0, 에이전트 0.0 | 모델 11회 행동, 도구 22코인 사용, 예산 소진 종료, 오류 0 |
| `.\scripts\run-evaluation.ps1 -Calibrated -Limit 30 -Concurrency 10 -AgentMode tools -ResultName 'deepseek_v41_flash_tool_loop_dev30_calibrated' -OracleResultName 'oracle_tool_loop_dev30_calibrated'` | 코드 421b4b0(제품 코드 abca8a5), 보정 스택, `private/lite30.calibrated.jsonl` sha256 `55ca576e…3ce`, 30문제·동시성 10 | exit 0, oracle 30/30·보상 30.0, 에이전트 Phase1 4/30·Phase2 4/30·보상 4.0, error 0 | 아래 비교표·결과 파일 |

검증 코드와 최종 SHA 일치 근거: 단위 테스트·재현 검증은 커밋 a93c905(121개 통과)와 bb6bbe4(120개 통과)에서 추적 파일
변경 없이 실행했고, `git diff --exit-code a93c905 -- agent tests scripts`가 차이 없이 종료했다. 실제 DB 실행의 제품 코드는
abca8a5와 421b4b0에서 동일하고(차이는 실행 스크립트뿐), 실행에 쓰인 스크래치패드 래퍼는 추적 스크립트를 문서화된 인자로
호출하기만 한다. 결과 JSON에는 코드 SHA가 없어 버전 귀속은 이 보고서 기록과 커밋 시각에 의존한다. bb6bbe4·a93c905는 실제
DB 평가를 다시 실행하지 않았으므로(사용자 지시) 아래 비교표는 수정 전 제품 코드의 기록이며, 두 커밋이 바꾼 경로(중복 키
판정, 기한)는 계약·회귀 테스트로만 확인했다.

### 실제 30문제 비교 (보정 스택, 동시성 10, 2026-10-15)

입력: `private/lite30.calibrated.jsonl`, sha256 `55ca576ea1c2942b53aab5700c781c50e4cf1660dff2b25c9fdf24ac835de3ce`,
ID 목록은 기준선 `results/two_phase_manual_30.json`과 동일하다. 모델 `deepseek/deepseek-v4.1-flash`,
출력 상한 16384, 요청 timeout 300초, effort·temperature 미지정. oracle은 두 실행 모두 30/30·Phase 2 30/30이다.

| 항목 | 고정 모드 기준선 | 자체 도구 루프 | 변화 |
| --- | --- | --- | --- |
| Phase 1 성공 | 5/30 | 4/30 | -1 |
| Phase 2 성공 | 4/30 | 4/30 | 0 |
| 총 보상 / 평균 | 4.7 / 0.157 | 4.0 / 0.133 | -0.7 |
| 모델 호출 | 35 | 353 | 10.1배 |
| 도구 호출 | 125 | 321 | 2.6배 |
| 제출 | 35 | 132 | 3.8배 |
| 사용 코인 | 195.0 | 531.5 | 2.7배 |
| 문제별 소요 합 | 1,116.9초 | 8,168.6초 | 7.3배 |
| 인프라 오류 | 0 | 0 | 0 |

문제별로 자체 루프가 새로 통과한 문제는 없고, 기준선이 Phase 1 부분 보상 0.7을 받은 `solar_4`만 0.0으로 내려갔다.
두 실행 모두 통과한 문제는 `mental_M_6`, `news_M_4`, `solar_M_5`, `vaccine_M_4`로 같다. 자체 루프의 통과 문제는
모델 행동 4~10회·7.5~12코인으로 끝났고, 실패 26문제는 모두 `budget_exhausted_before_submit`로 끝났다
(행동 상한 24회·1500초 상한 도달은 없음).

관찰된 실패 동작: 제출 132회 중 통과 8회(Phase 1 통과 4 + 그 Phase 2 4)뿐이고, 나머지 124회는 오답 제출이다.
같은 단계 중복 SQL 차단은 실제 실행에서 0회 발생했다(모델이 매번 SQL 내용을 바꿔 제출했고, 공백 정규화 기준으로도
같은 SQL이 아니었다 — 리뷰 R-1 수정 뒤 기준으로도 같은 결과다). 중복 정보 조회 캐시는 13회 적중했다. 모델 응답 353회 중
32회는 프로토콜 위반(한 응답에 JSON 객체 여러 개, `"action"` 대신 `"tool"` 키, DSML/XML 형태 도구 호출)으로 비용 없이
되돌려졌고, 9회는 출력 상한으로 잘렸다. 이 9회는 모두 완전한 JSON 행동이 아니어서 같은 경로로 거부됐다(완전한 JSON이면
실행하는 정책의 경계는 실제 실행에서 발생하지 않았다). 도구 사용은 get_schema 30, get_all_knowledge_definitions 27,
get_all_external_knowledge_names 25, get_all_column_meanings 25, get_knowledge_definition 77, get_column_meaning 5였다.

정보 부족이 실패 원인인지 확인하기 위해 실패 문제의 궤적을 확인했다. `solar_4`는 get_schema,
get_all_external_knowledge_names, get_all_knowledge_definitions, get_all_column_meanings와 지식 2건을 조회한 뒤
오답 SQL을 5회 제출했고, `mental_1`·`cross_db_M_5`도 같은 전체 정보를 조회한 뒤 각각 6회·4회 오답을 제출했다.
즉 고정 모드가 한 번에 넣어 주는 정보 집합을 조회한 뒤에도 제출을 반복한 것이며, 루프가 정보를 숨겨서 생긴 실패가
아니다. 남은 코인을 제출 반복에 쓰는 모델 행동이 관찰된 주된 실패 패턴이다.

결과 파일:
- `results/deepseek_v41_flash_tool_loop_dev30_calibrated.json`(18.7MB)·`.html`
- `results/oracle_tool_loop_dev30_calibrated.json`·`.html`
- 실행 로그 `.runtime-tmp/eval_tools_dev30.log`(에이전트 실행 15:46:37~16:02:33)

## 미검증 항목과 제한

- 한 회차 비교다. 모델 비결정성 때문에 4대 5 차이를 성능 차이로 해석하지 않는다. 반복 측정·다른 동시성·전체 300문제는
  실행하지 않았다.
- R-1·R-2 수정 커밋(bb6bbe4, a93c905)에 대한 실제 DB 평가는 실행하지 않았다(사용자 지시). 아래 비교표의 실제 실행 기록은
  수정 전 제품 코드(abca8a5 = 421b4b0)의 것이며, 수정된 중복 판정·기한 경로는 계약·회귀 테스트로만 확인했다. 실제 실행에서
  이 두 경로는 각각 0회(중복 차단)·0문제(시간 상한) 발생했으므로 이 차이가 위 수치에 미친 영향은 확인되지 않았다.
- 1500초·24회 상한은 이제 행동 사이·모델 응답 뒤·각 호출 직전에 확인하고 대기 자체도 남은 시간으로 제한한다. 다만 취소된
  공식 요청이 서비스에서 이미 처리됐을 수 있고, 그 경우 결과를 되돌릴 수단이 없다. 그래서 취소된 제출·전환은 결과 미상으로
  기록하며, 그런 문제의 보상은 0으로 남는다(과소 집계 가능성). 실제 실행에서는 발생하지 않았다.
- 중복 SQL 차단은 계약·회귀 테스트로만 확인했다. 실제 실행에서는 발생하지 않았다.
- user simulator 전환 호출은 수정 커밋부터 `state.phase_transition`에 기록한다. 아래 실제 실행 기록(수정 전 코드)에는
  이 키가 없어, 그 실행에서는 Phase 1을 통과한 4문제가 Phase 2 SQL까지 통과한 사실로 간접 확인된다.
- 기록 방식으로 모델 호출마다 당시 대화 전체를 `adk_events[].prompt`에 저장한다. 30문제 결과 JSON이 18.7MB가 됐다.
  검증 근거는 충분하지만 파일이 크다.
- `results/`·`private/`·`.env`·스크래치패드 래퍼는 커밋하지 않았다. 문서는 이 보고서만 작성했고 커밋하지 않는다.
- README·`docs/setup.md`의 실행 안내는 갱신하지 않았다. 모드 선택 명령은 이 보고서와 스크립트 파라미터에 있다.

## 리뷰 지적 처리

1차 검토 코드 SHA: 421b4b0bd3e00d768551f4b5a6980d48b3517a71 (changes_requested, 명세 버전 1). 1차 수정 커밋: bb6bbe42b38cad05734622225daed468ec09cd47.
2차(재검토) 검토 코드 SHA: bb6bbe42b38cad05734622225daed468ec09cd47 (2026-10-16, R-2 해결·R-1 잔여 사례 미해결). 2차 수정 커밋: a93c905ecb226f9b94afd16fe02490ca3185db15.
수정 횟수: R-1 2회, R-2 1회.

| 지적 | 판정 | 수정 위치·방법 | 검증 결과 |
| --- | --- | --- | --- |
| R-1 [P2] SQL 리터럴·주석의 의미를 보존한 중복 판정 | 2차 수정 | 1차: `submission_key()`가 토큰 사이 공백만 축약하고 문자열·인용 식별자·달러 인용·주석 본문을 보존, 모호하면 원문 키로 후퇴, 제출 이력은 실제 SQL 저장. 2차(`agent/tool_loop.py`): 인접 문자열 상수 사이 공백을 원문 보존(개행 유무가 구문을 바꿈), `_starts_string_constant()`가 다음 문자열 시작 판별(접두 형태 포함), `DOLLAR_QUOTE` 태그에 비ASCII 허용 | 1차: 리뷰 재현 스크립트 실패(수정 SQL 도달·보상 0.7), `SubmissionKeyTests` 5개·동작 회귀 1개. 2차: 리뷰 잔여 재현 스크립트가 "R-1 newline" assert에서 실패(수정 SQL 도달), 독립 mock 검증에서 세 사례 모두 제출 2회·차단 0·보상 0.7, 새 키·동작 회귀 포함 전체 121개 통과 |
| R-2 [P2] 모델 응답 후에도 세션 기한 적용 | 수정(해결) | `_bounded()`로 모델 응답·정보 조회·제출·전환 대기를 남은 시간으로 제한, 응답 직후·호출 직전 기한 확인, 초과 시 `session_time_limit` 정상 종료, 취소된 제출·전환은 결과 미상으로 기록 | 2차 재검토에서 해결 판정: 1501초 응답 뒤 제출 무호출·예산 18 유지·보상 0, 모델 대기 취소 시 재호출도 캐시 결과. 계약 테스트 5개와 리뷰 독립 재현 통과 |

리뷰의 추가 관찰(차단 사유 아님)도 반영했다: `finish_reason=length`여도 완전한 JSON 행동이면 실행하는 실제 정책에 맞춰
이 보고서의 정책 설명을 고쳤다. 실제 30문제에서 잘린 응답 9회는 모두 완전한 JSON이 아니어서 거부됐다.

R-1이 2차 재검토에서도 미해결로 판정되면 동일 지적 두 차례 수정 기준에 따라 조율자에게 needs_decision으로 전달한다.

## 필요한 판단

없음. 명세의 완료 조건을 확인했고 필수 검증을 실행했다. 독립 리뷰 판정 전이므로 `ready_for_review`로만 보고한다.
