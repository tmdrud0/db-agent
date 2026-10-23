# TASK-014 구현 보고서

작업 ID: TASK-014
명세 버전: 1
대상 코드 버전: 715393884553b71f2251258aae97b77a3fdf0d9b
상태: ready_for_review
요약: 서브에이전트 정의에 선택 키 `fetch`를 추가했다. `fetch`가 있으면 `consult` 때 공식 서비스를 한 번 호출해 공식 비용을 차감하고, 원문은 서브에이전트 user 메시지에만 넣는다. 주 에이전트는 서브에이전트 답과 조회 안내 한 줄만 관찰로 받는다. `fetch`가 없는 하네스의 프롬프트·기록은 TASK-013과 같다.
근거: `agent/harness.py`, `agent/tools.py`, `agent/subagent.py`, `agent/tool_loop.py`, `tests/test_harness.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`, `harness/context_finder/`.
미확정 사항: 없음.

## 커밋과 검토 범위
기준 커밋: 438f50951b19fb453efc0645a4cefab3fe791a6b
코드 커밋: 715393884553b71f2251258aae97b77a3fdf0d9b
작업 커밋 목록:
- 715393884553b71f2251258aae97b77a3fdf0d9b `[TASK-014] feat: let sub-agents fetch one official lookup on consult`

검토 명령: `git diff 438f50951b19fb453efc0645a4cefab3fe791a6b 715393884553b71f2251258aae97b77a3fdf0d9b --`
현재 코드와 대상 SHA의 차이: 없음. HEAD가 대상 SHA이고 추적 파일 변경이 없다. 미추적 항목은 `docs/agent/tasks/TASK-014/`(이 명세와 이 보고서)뿐이며 테스트·실행 코드에 쓰이지 않는다.
기존 사용자 변경: 없음. 작업 시작 시 `git status --short`는 `?? docs/agent/tasks/TASK-014/`만 보였다.

## 변경 내용

### 1. `fetch` 머리말(`agent/harness.py`, `agent/tools.py`)
- `SUB_AGENT_FETCH_TOOLS`(`agent/tools.py:94`)는 인자 없는 공식 조회 네 개다: `get_schema`, `get_all_column_meanings`, `get_all_external_knowledge_names`, `get_all_knowledge_definitions`.
- `SUB_AGENT_HEADER_KEYS`에 `fetch`를 추가했다(`agent/harness.py:33`). `SubAgent.fetch: str | None`은 기본 `None`이다(`:46`).
- `_parse_sub_agent()`는 기존 `key: value` 규칙과 중복 키 거부를 그대로 적용한 뒤 `fetch`를 검증한다(`:215-228`).
  - 빈 값, 쉼표가 있는 다중 값, 네 도구가 아닌 이름(인자 필요 조회, `execute_sql`, `ask_user`, `submit_sql`, 없는 이름)은 `HarnessError`다.
  - 머리말 키 순서는 자유다. `description`은 여전히 필수다.
- 정의 파일 내용이 해시 입력에 그대로 들어가므로 `fetch` 추가·변경도 하네스 해시를 바꾼다(기존 규칙).

### 2. `consult` 실행 순서(`agent/tool_loop.py`)
`_consult_action()`(`:576`)은 다음 순서로 동작한다.
1. 서브에이전트 이름과 인자는 기존 `_session_loop` 검증 경로에서 확인한다. 없는 이름은 코인 없는 거부다.
2. `fetch`가 있으면 `_information()`을 재사용해 공식 조회를 한 번 실행한다(`:611-619`).
   - `requested_by={"action": "consult", "agent": "<이름>"}`를 넘겨 기존 정보 조회 기록에 요청자를 추가한다(`_information`의 `:265,283`).
   - 예산 차감, tool span, 오류 처리, `tool_trajectory` 형식은 기존 정보 조회와 같다. 캐시는 없다.
   - 재사용을 택한 이유: 예산 차감·span·`record_action`·`SessionExpired`/`GradingError` 처리를 복제하지 않기 위해서다. 조회 원문은 반환값으로 그대로 받는다.
3. 조회 뒤 `consult` tool span과 `subagent_call` event를 만들고 서브에이전트를 한 번 호출한다.
4. `build_user_message()`에 조회 원문을 넘겨 `Fetched <도구>: ...` 섹션을 `context` 섹션 뒤, `Request:` 앞에 넣는다(`agent/subagent.py:56-80`).
5. 답은 기존 형식으로 돌려주고, `fetch`가 있으면 관찰 끝에 `"<도구> was fetched for the sub-agent (cost <n>)."`를 붙인다(`agent/tool_loop.py:727`).

예산 부족 방어(`:604-610`): `consult` 시작 시 남은 코인이 `fetch` 비용보다 적으면 서비스·모델을 호출하지 않고 `"The sub-agent '<이름>' was not consulted. Its <도구> lookup costs <n> bird-coins, but only <m> remain. Nothing was executed."`를 관찰 본문으로 돌려준다. 이 경로는 tool 항목을 남기지 않고 관찰 머리말 비용은 0이다. 실제 루프는 코인 3 미만에서 먼저 종료하므로 정상 실행에서는 도달하지 않는다.

### 3. 기록 형식
- `tool_trajectory`는 `consult` 하나에 두 항목을 순서대로 남긴다.
  1. 공식 조회 항목: 기존 정보 조회와 같은 키(`tool`, `args: {}`, `result`=원문, `cost`, `budget_before`, `budget_after`, `phase`, `elapsed_seconds`) + `requested_by`.
  2. `consult` 항목: TASK-013 형식 + `fetch: "<도구>"`, `cost: 0`, `budget_before == budget_after`(조회 차감 후 값).
- 실패·시간 종료 경로의 `consult` 항목에도 `fetch`가 붙는다. 조회 비용은 1번 항목에만 있다.
- `subagent_call` event에 `fetch: {"tool": "<도구>", "cost": <공식 비용>}`를 추가했다(`:650-651`). `prompt`에는 원문이 포함된 실제 전송 messages가 그대로 남는다.
- 관찰 머리말의 `cost`는 `consult` 전후 예산 차이로 계산한다(`:887,921`). 조회가 실행됐으면 공식 비용, 아니면 0이다.
- Langfuse: 공식 조회는 기존 정보 조회와 같은 tool span(`get_schema` 등), 서브에이전트 호출은 TASK-013과 같은 `subagent:<이름>` generation이다. 이번 작업에서 tracing 코드는 수정하지 않았다.

### 4. 프롬프트(`agent/tools.py`, `agent/tool_loop.py`)
- 카탈로그: `  - <이름>: <설명> Fetches <도구> (cost <n>).` (`consult_catalog`, `agent/tools.py:193-209`).
- 계약: `fetch`가 있는 서브에이전트가 하나 이상일 때만 다음 4줄을 기존 규칙 뒤에 붙인다(`agent/tool_loop.py:125-131`).
  ```
  - A sub-agent listed with "Fetches <tool> (cost <n>)" receives that official
    lookup when it is consulted. The lookup runs once, is charged its official coin
    cost, and its raw output is shown only to the sub-agent; the main agent receives
    only the sub-agent's answer.
  ```
- `fetch`가 없는 하네스는 카탈로그 접미사와 계약 문구가 모두 붙지 않는다.

### 5. 오류
- 조회 `GradingError`: `_information()`이 `state["failure"] = {"stage": "information", ...}`를 기록하고 세션 실패로 올린다. 조회 항목만 남고 `consult` 항목은 없다.
- 조회 중 시간 종료: 기존 정보 조회와 같이 `timed_out` 항목을 남기고 `session_time_limit`으로 끝난다.
- 조회 성공 후 서브에이전트 `ModelError`: 세션 유지. 조회 비용은 차감된 채 남고(`consult` 항목 cost 0, 조회 항목 cost 1), 실패 관찰에 `"The <도구> lookup cost <n> bird-coins and was not refunded."`를 넣는다(`:692-697`). `fetch`가 없으면 TASK-013 문구 그대로다.
- 조회 성공 후 서브에이전트 시간 종료: TASK-013과 같이 `cancelled` 항목을 남기고 `session_time_limit`으로 끝난다. 환불은 없다.

### 6. `/health`
`/health.tools`는 바뀌지 않았다. `/health.harness.sub_agents`는 기존 이름 목록 형식을 유지했다. 저장소에서 이 필드를 소비하는 곳은 테스트(`tests/test_harness.py`, `tests/test_tool_loop.py`)뿐이고, `scripts/run-evaluation.ps1`의 `Assert-BaselineAgent`는 `/health.tools`만 확인한다. 따라서 형식을 바꾸지 않았다.

### 7. 예시 하네스 `harness/context_finder/`
- `system.md`: 기준 하네스 3줄 뒤에 "전체 조회가 필요하면 finder 서브에이전트에게 질문에 필요한 부분만 요청하고, 빠진 것이 있으면 단건 조회나 직접 조회를 쓰라"는 지침을 붙였다.
- `sub_agents/schema_finder.md`(`fetch: get_schema`), `column_finder.md`(`fetch: get_all_column_meanings`), `knowledge_finder.md`(`fetch: get_all_knowledge_definitions`). 셋 모두 `context: task, dialogue`다.
- 공통 본문 지침: 필요한 항목만 원문 그대로 옮기고 요약·의역 금지, 애매하면 포함, 원문에 없는 내용 생성 금지, 원문에 정의가 없는 참조 용어는 미정의로 표시.
- `sql_reviewer`는 넣지 않았다.

## 완료 조건별 근거
- **AC-1 (기존 하네스 불변)**
  - `harness/baseline` 프롬프트: 기존 `EXPECTED_BASELINE_PROMPT` 테스트가 그대로 통과한다.
  - `harness/sql_review` 프롬프트: `EXPECTED_SQL_REVIEW_PROMPT`(`tests/test_harness.py:86`)와 문자 단위 동일을 `test_the_sql_review_prompt_is_the_task_013_text`(`:442`)로 고정했다. 이 상수는 기준 SHA 438f509의 코드로 생성한 문자열이다.
  - `fetch` 없는 `consult` 기록: `test_a_consult_without_fetch_keeps_the_task_013_record`가 cost 0, 단일 항목, `fetch` 키 없음, 이벤트 `fetch` 없음, 관찰 머리말 cost 0을 확인한다. 기존 `ConsultTests`도 그대로 통과한다.
- **AC-2 (머리말 검증)**
  - `test_the_four_argument_free_lookups_load_as_fetch`가 네 도구 각각의 로드를 확인한다.
  - `test_invalid_fetch_values_fail`이 빈 값, 쉼표 다중 값, `get_column_meaning`, `get_knowledge_definition`, `execute_sql`, `ask_user`, `submit_sql`, 없는 이름, 중복 `fetch` 키에서 `HarnessError`를 확인한다.
- **AC-3 (실행·비용)**
  - `test_one_consult_fetches_once_charges_once_and_returns_only_the_answer`: `/schema` 요청 1회, 예산 18→17, 서브에이전트 모델 호출 1회, `Fetched get_schema:`가 `Request:` 앞, 이후 관찰 반영.
  - `test_two_consults_fetch_and_charge_twice`: `/schema` 2회, 조회 항목 2개, 예산 18→16→13(제출 포함 13).
  - `test_a_phase_two_consult_fetches_the_current_state_once`: phase 2에서도 같은 공식 경로로 1회 조회(`phase: 2`).
- **AC-4 (컨텍스트 분리)**
  - `test_the_raw_output_never_enters_the_main_dialogue_or_another_sub_agent`: `RAW-SCHEMA-MARKER`가 다음 주 호출 messages와 다른 서브에이전트의 `Observations:` 맥락에 없고, 답과 조회 안내 줄은 있다. `subagent_call.prompt`에는 명세대로 원문이 남는다.
- **AC-5 (기록)**
  - 위 실행 테스트가 `requested_by`, 비용, 예산 전후, `consult` cost 0·`fetch`, 항목 순서를 확인한다.
  - `subagent_call.fetch`와 관찰 머리말 `cost 1`을 같은 테스트에서 확인한다.
  - `FetchConsultTraceTests.test_the_lookup_is_a_tool_span_and_the_sub_agent_is_a_generation`이 가짜 Langfuse에서 `get_schema` tool span, `consult` tool span, `subagent:schema_finder` generation을 확인한다.
- **AC-6 (오류)**
  - `test_a_fetch_service_error_is_a_session_failure`: `failure.stage == "information"`, 예산 17, 조회 항목 1개, `consult` 항목 없음.
  - `test_a_fetch_timeout_ends_the_session_with_the_standard_reason`: `session_time_limit`, `timed_out`, 예산 17.
  - `test_a_sub_agent_model_error_after_a_fetch_keeps_the_session_and_the_charge`: 세션 유지, 예산 14(조회 1 + 제출 3), `model_error`, 차감 사실 문구.
  - `test_a_budget_below_the_fetch_cost_refuses_without_any_call`: 내부 `_consult_action`을 직접 호출해 코인 0.5 상태에서 거부 본문, 예산 불변, 서비스·모델·기록 없음.
- **AC-7 (프롬프트)**
  - `test_a_fetch_sub_agent_shows_its_lookup_and_the_fetch_contract`가 카탈로그 접미사, 4줄 계약, 공식 9개 줄 보존을 확인한다.
  - `test_sub_agents_without_fetch_keep_the_task_013_prompt`가 접미사·계약 문구가 없음을 확인한다.
- **AC-8 (예시 하네스)**
  - `test_the_context_finder_example_loads_with_its_three_fetches`가 세 서브에이전트, `fetch`, `context`, 프롬프트의 도구·비용 표시를 확인한다.
- **AC-9 (평가 실행 경로)**
  - `test_all_three_harnesses_expose_only_the_official_tools`가 `harness/baseline`, `harness/sql_review`, `harness/context_finder` 각각의 `/health.tools`가 공식 9개(이름·가격) 그대로임을 확인한다.
  - `Assert-BaselineAgent`는 `/health`의 `service`, `mode`, `agent`, `model`과 이 9개 도구 이름·가격만 검사하므로(`scripts/run-evaluation.ps1:129-145`) 통과 조건이 바뀌지 않았다. 실제 스크립트 실행은 하지 않았다.
  - 실행 명령은 아래 "설정과 실행 방법"에 적었다.
- **AC-10 (전체 테스트)**
  - `.venv\Scripts\python.exe -m unittest discover -s tests` → `Ran 247 tests`, `OK (skipped=3)`.
- **AC-11 (선택, 실제 평가)**: 미검증. 실행하지 않았다.

## 실행한 검증
| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | HEAD `7153938`, Python 3.12.10, 가짜 모델·환경·Langfuse | exit 0, `Ran 247 tests`, `OK (skipped=3)` | AC-1~AC-10 |
| `.venv\Scripts\python.exe -m unittest tests.test_harness tests.test_tool_loop tests.test_tracing` | 구현 중 대상 작업 트리(커밋 전 동일 내용) | exit 0, 166 tests OK (skipped=3) | 변경 영역 집중 확인 |
| `git diff --check 438f509 7153938` | — | 출력 없음 | 공백 오류 없음 |
| 프롬프트 고정 문자열 생성 | 기준 SHA 438f509 코드, `system_prompt(load_harness('harness/sql_review'))` | 3,370자 | AC-1 기준 프롬프트 |
| 도구별 원문 길이 측정 | `results/deepseek_v41_flash_tool_loop_dev30_calibrated.json`의 `tool_trajectory.result` JSON 길이(읽기만 함) | 아래 표 | 조사 항목: 서브에이전트 입력 크기 |

검증 코드와 최종 SHA 일치 근거: HEAD가 `715393884553b71f2251258aae97b77a3fdf0d9b`이고 추적 파일 변경이 없다(`git status --short`에서 `docs/agent/tasks/TASK-014/`만 미추적). 전체 스위트는 커밋 후 이 SHA에서 다시 실행했다. skip 3건은 TASK-012부터 있던 Windows 파일 심볼릭 링크 권한 문제(WinError 1314)다.

### 도구별 원문 길이(참고)
`results/deepseek_v41_flash_tool_loop_dev30_calibrated.json`, 도구 항목별 `result` JSON 길이(문자).

| 도구 | 횟수 | 최소 | 중앙값 | 최대 |
| --- | --- | --- | --- | --- |
| `get_schema` | 30 | 13,160 | 15,660 | 25,791 |
| `get_all_column_meanings` | 25 | 13,219 | 17,819 | 27,296 |
| `get_all_knowledge_definitions` | 27 | 17,633 | 24,571 | 26,963 |
| `get_all_external_knowledge_names` | 25 | 1,673 | 2,173 | 2,765 |

가장 큰 원문 약 2.7만 자는 대략 7천~9천 토큰 규모로, 출력 한도 16,384 토큰 안에서 필요한 부분만 추출하는 데 무리가 없다고 판단했다. 이번 범위에는 원문 자르기·요약이 없다.

## 설정과 실행 방법
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests

# fetch 하네스로 평가 1문제 실행
$env:AGENT_HARNESS_DIR = 'harness/context_finder'
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName task014_context_finder_smoke
```
- `AGENT_HARNESS_DIR`는 에이전트 프로세스가 시작되기 전에 설정해야 한다. 포트 6000의 기존 서버가 재사용되면 그 서버가 시작할 때 읽은 하네스가 쓰인다.
- 사용한 하네스는 `/health.harness`(`name`, `sha256`, `sub_agents`)로 확인한다.
- 결과 JSON 확인:
  - `tool_trajectory`에서 `requested_by`가 있는 공식 조회 항목 바로 뒤에 `consult` 항목(cost 0, `fetch`)이 있는지.
  - 다음 `adk_events`의 `model_call.prompt`에 조회 원문이 없고 서브에이전트 답과 조회 안내 줄만 있는지.
  - `subagent_call.fetch`와 `prompt`(원문 포함)가 있는지.

## 미검증 항목과 제한
- **AC-11(선택) 미실행.** `harness/baseline`과 `harness/context_finder`의 실제 dev30 calibrated 비교는 하지 않았다.
- **실제 모델·DB·Langfuse 미검증.** 테스트는 가짜 모델·환경·Langfuse 클라이언트만 사용했다. 실제 `consult` 응답 품질, 실제 공식 서비스 조회, Langfuse 전송은 확인하지 않았다.
- **`run-evaluation.ps1` 미실행.** AC-9는 코드·테스트 근거이며, 실제 `Assert-BaselineAgent` 실행은 하지 않았다.
- **phase 2의 실제 DB 상태 반영 미검증.** 구현은 `consult`마다 공식 서비스에 그때의 `task_id`로 조회하며 캐시가 없다(테스트로 2회 호출 확인). 실제 phase 1 SQL 적용 뒤 조회 결과가 바뀌는지는 실제 스택에서 확인하지 않았다.
- **예산 부족 거부 경로는 정상 루프에서 도달하지 않는다.** 코인 3 미만에서 루프가 먼저 끝나기 때문에 내부 함수 직접 호출로만 검증했다.
- **후속 문서.** README와 `docs/setup.md`에 `fetch` 머리말과 `harness/context_finder` 사용법을 추가하는 일이 남아 있다(이번 범위 밖).

## 리뷰 지적 처리
최초 검토 대상이다. 해당 없음.

## 필요한 판단
없음.
