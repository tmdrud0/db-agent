# TASK-013 구현 보고서

작업 ID: TASK-013
명세 버전: 1
대상 코드 버전: 8cd1c6bae0e387611b56222b4b7a2786df4b1944
상태: ready_for_review
요약: 하네스가 `sub_agents/<이름>.md`로 서브에이전트를 정의할 수 있고, 그런 하네스에서만 로컬 행동 `consult`가 노출된다. `consult`는 코인·공식 도구·공식 서비스 호출 없이 LLM 호출 한 번으로 서브에이전트의 답을 받아 관찰로 돌려준다. 서브에이전트가 없는 하네스의 시스템 프롬프트와 동작은 TASK-012와 같다.
근거: `agent/harness.py`, `agent/subagent.py`, `agent/tool_loop.py`, `agent/tools.py`, `agent/tracing.py`, `agent/server.py`, `harness/sql_review/`, `tests/test_harness.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`
미확정 사항: 없음. AC-11(실제 평가)은 실행하지 않았다.

## 커밋과 검토 범위
기준 커밋: 00cb7c3e7ea23357ecd2071a482df6a249577166
코드 커밋: 8cd1c6bae0e387611b56222b4b7a2786df4b1944
작업 커밋 목록: `8cd1c6b` `[TASK-013] feat: add harness sub-agents and the consult action`
검토 명령: `git diff 00cb7c3e7ea23357ecd2071a482df6a249577166 8cd1c6bae0e387611b56222b4b7a2786df4b1944 --`
현재 코드와 대상 SHA의 차이: 없음. 코드·테스트 커밋 후 변경한 파일은 이 보고서(`implementation.md`, 미커밋 로컬 기록)뿐이다.
기존 사용자 변경: 없음. `docs/agent/tasks/TASK-013/spec.md`는 전달받은 미추적 로컬 파일이며 커밋하지 않았다.

## 변경 내용

### 1. 하네스 서브에이전트 정의 (`agent/harness.py`)

- 위치: `<하네스>/sub_agents/<이름>.md`. `sub_agents`는 선택 디렉터리다.
- 이름 규칙: 정규식 `^[a-z][a-z0-9_]{0,39}$`. 파일은 `.md`여야 한다.
- 디렉터리 규칙은 `tools/`와 같다. 링크·junction과 루트 밖 경로를 `_check_inside()`로 거부하고, 하위 디렉터리나 `.md`가 아닌 파일은 `HarnessError`다.
- `Harness.sub_agents: dict[str, SubAgent]`가 추가됐다. `SubAgent`는 `name`, `description`, `context: tuple[str, ...]`, `system`을 가진다.
- 파일은 기존처럼 경로(`sub_agents/<이름>.md`)와 정규화된 내용으로 해시에 들어간다. 이름이나 내용을 바꾸면 해시가 달라진다.

머리말 파서 규칙(구현자가 정한 값):

1. 파일 첫 줄이 `---`여야 한다(앞뒤 공백 허용). 아니면 `HarnessError`.
2. 다음 `---` 줄까지가 머리말이고, 닫는 줄이 없으면 `HarnessError`.
3. 머리말의 빈 줄은 무시한다. 각 줄은 `key: value`여야 하며, `:`가 없거나 키가 비면 `HarnessError`.
4. 허용 키는 `description`, `context`뿐이다. 다른 키나 중복 키는 `HarnessError`.
5. `description`은 필수이고 strip 후 비어 있으면 `HarnessError`.
6. `context`는 쉼표로 나눈 항목을 strip해 선언 순서대로 저장하고 중복은 첫 항목만 남긴다. 빈 항목이나 `task`, `dialogue`, `observations` 밖의 값은 `HarnessError`. 생략하면 `("task", "dialogue")`.
7. 닫는 `---` 뒤 본문은 strip 후 비어 있으면 `HarnessError`. 저장되는 `system`은 strip된 본문이다.
8. YAML 라이브러리는 쓰지 않는다.

### 2. `consult` 행동 (`agent/tools.py`, `agent/tool_loop.py`)

- `CONSULT_SPEC`은 `ALLOWED_TOOLS`에 넣지 않았다. 그래서 `TOOL_COSTS`, `tool_metadata()`(“/health.tools”), 공식 가격 계산은 바뀌지 않는다.
- `parse_response(content, allowed=None)`/`parse_action(content, allowed=None)`가 호출 시점의 행동 집합을 받는다. `allowed`가 없으면 기존 `ALLOWED_TOOLS`다.
- `_allowed_actions(harness)`가 서브에이전트가 있을 때만 `{**ALLOWED_TOOLS, "consult": CONSULT_SPEC}`를 만든다. 없으면 `ALLOWED_TOOLS` 그대로라 `consult`는 `Unknown action 'consult'`로 거부된다(코인 없음).
- 인자 검증은 기존 `validate_args`를 그대로 쓴다. 필수 `agent`, `request`, 둘 다 비어 있지 않은 문자열, 추가 인자 거부.
- 없는 `agent` 이름은 파싱 직후 `ActionError`로 바꿔 기존 거부 경로(관찰 `rejected`, cost 0, `action_error`)로 보낸다. 모델 호출은 일어나지 않는다.
- 비용은 `CONSULT_COST = 0.0`이다. `_observation()` 머리말과 `tool_trajectory` 모두 0을 쓴다. `budget_remaining`은 바뀌지 않는다.
- `consult`를 고른 응답 자체는 기존처럼 `_model_call()`이 `model_call` event를 남기므로 행동 1회로 센다. 서브에이전트 호출은 `subagent_call` event라 `_model_actions()`에 들어가지 않는다.

코드가 만드는 카탈로그 줄(`consult_catalog()`):

```
- consult: ask one harness sub-agent for an answer; the answer is an observation and never changes the score. arguments: {"agent": "<sub-agent name>", "request": "<what the sub-agent should do>"}. Cost: 0
  Available sub-agents:
  - <이름>: <description>
```

코드가 추가하는 계약 문구(`system_prompt()`):

```
- consult calls one sub-agent defined by the harness. It is not an official tool
  and costs no bird-coins, but the response that chooses it still counts as one model
  action.
- A sub-agent cannot use the database, the user simulator or the grader. It sees only
  the context listed with its name and the request, and its answer is an observation
  that never changes the score.
```

이 줄은 `harness.sub_agents`가 비어 있으면 붙지 않는다. `harness/baseline/`의 시스템 프롬프트는 기존 `EXPECTED_BASELINE_PROMPT`와 문자 단위로 같다(테스트로 고정).

### 3. 서브에이전트 입력 조립 (`agent/subagent.py`)

서브에이전트 호출은 `ModelClient.chat(messages)` 한 번이다. messages는 정확히 두 개다.

- system: 정의 파일 본문(`sub_agent.system`).
- user: 아래 섹션을 `"\n\n"`로 이어 붙이고 마지막에 `"\n"`을 붙인다. 섹션은 `context`에 선언된 순서대로만 들어가고, 마지막은 항상 `Request:`다.

섹션 형식:

```
Task:
Database: <db_name>
Phase: <1|2>
Phase 1 question:
<user_query>
Follow-up question:
<follow_up_query>
```

`Database` 줄은 `db_name`이 비어 있으면 생략한다. `Follow-up question` 두 줄은 phase 2이고 `follow_up_query`가 있을 때만 붙는다.

```
Dialogue:
Agent: <ask_user 질문>
User: <공식 답변>
```

`dialogue_history`가 비어 있으면 `Dialogue:\nNo questions or answers yet.`

```
Observations:
Assistant action:
<실행된 행동의 표준 JSON; 거부된 응답은 모델 원문>
Observation:
[action N | phase P | tool | cost C | budget B]
<관찰 본문>
```

`Observations`는 현재 실행 중인 `consult` 행동을 제외한 직전까지의 대화 기록(`messages[2:-1]`)을 그대로 옮긴다. 실행된 행동의 표준 JSON, 거부된 응답 원문과 그 거부 관찰, 이전 `consult`의 행동 JSON·답 관찰이 모두 포함된다. 기록이 없으면 `Observations:\nNo actions yet.`

```
Request:
<consult args.request>
```

`state`에서 꺼내는 값은 `db_name`, `current_phase`, `user_query`(또는 run message), `follow_up_query`, `dialogue_history`뿐이다. `state`의 다른 키, GT, 테스트 케이스, 로컬 파일은 읽지 않는다. 테스트에서 `state`의 숨은 키가 서브에이전트 messages에 들어가지 않는 것을 확인한다.

서브에이전트 응답은 파싱하지 않고 실행하지 않는다. 응답 전체를 다음 관찰 본문으로 돌려준다.

```
Sub-agent '<이름>' answer:
<응답 텍스트>
```

출력 한도로 잘린 경우 뒤에 `\n[The sub-agent response was cut off by the output token limit.]`를 붙인다.

### 4. 오류와 시간 (`agent/tool_loop.py`)

- 호출은 `_bounded(model.chat(...), _remaining(deadline), ...)`로 세션 시간 제한 안에서 기다린다. 시간이 끝나면 `SessionExpired`를 올려 기존 `session_time_limit` 종료 경로로 간다. 이때 `tool_trajectory`에 `timed_out: true`, `result.kind: "cancelled"`가 남는다.
- `ModelError`(재시도 소진)는 세션을 끝내지 않는다. `state["failure"]`도 설정하지 않는다. 관찰 본문은 다음과 같다.
  ```
  The sub-agent '<이름>' call failed and no answer is available. The failure does not affect the budget or the score.
  Error: <오류 요약>
  ```
- `tool_trajectory` 결과 종류: 성공 `{"kind": "answer", "content", "error": null, "truncated"}`, 모델 실패 `{"kind": "model_error", "content": null, "error"}`, 시간 종료 `{"kind": "cancelled", "content": null, "error"}`.
- 주 에이전트 모델 호출 실패 경로(세션 실패, `/run_session` 502)는 바꾸지 않았다.

### 5. 기록

- `tool_trajectory` 항목: `tool: "consult"`, `args: {agent, request}`, `cost: 0.0`, `budget_before == budget_after`, `phase`, `elapsed_seconds`, `result`.
- `adk_events`: 호출마다 `{"type": "subagent_call", "harness", "agent", "prompt", "content", "truncated", "usage", "elapsed_seconds"}`. 실패·시간 종료 시 `content: ""`, `usage: null`, `error`가 붙는다. `type`이 `model_call`이 아니므로 행동 한도에 들어가지 않는다.
- Langfuse: 서브에이전트 generation을 **세션 trace 바로 아래**에 둔다(tool span 아래가 아님). 이름은 `subagent:<이름>`이고 `metadata.subagent`에 이름을 넣는다. 입력은 실제로 보낸 messages, 출력·모델·usage·truncated·elapsed·attempts를 기록한다. Langfuse가 없거나 실패하면 기존처럼 no-op이며 결과에 영향이 없다.
- `orchestrator/report.py` 확인: `_build_timeline()`은 모르는 `type`을 만나면 그 event만 건너뛰고 오류를 내지 않는다. `subagent_call`이 든 합성 결과 JSON으로 `generate_html()`을 실행해 exit 0과 task card 생성을 확인했다(스크래치패드, 저장소 파일 아님).

### 6. 예시 하네스 `harness/sql_review/`

- `system.md`: 기준 하네스 문단 뒤에 “제출 전에 후보 SQL을 `sql_reviewer`에게 검토받으라”는 지침을 붙였다.
- `sub_agents/sql_reviewer.md`: `description` 한 줄, `context: task, dialogue, observations`, 본문에 질문·사용자 답변·관찰의 지식 정의와 후보 SQL을 대조해 문제를 번호 목록으로 쓰고, 문제가 없으면 `No problems found.`라고 답하도록 정했다. 채점기·정답·점수는 언급하지 않는다.
- 문구 품질은 완료 조건이 아니며 형식·로드만 검증했다.

## 완료 조건별 근거

- AC-1: `BaselinePromptTests.test_the_baseline_builds_the_frozen_contract`가 `harness/baseline` 프롬프트 == `EXPECTED_BASELINE_PROMPT`를 확인한다. `ToolLoopContractTests.test_consult_is_unknown_in_a_harness_without_sub_agents`가 `Unknown action 'consult'`, 코인·서비스 호출 없음, 거부 관찰을 확인한다. `test_a_harness_without_sub_agents_has_no_consult_text`가 consult 문구 부재를 확인한다.
- AC-2: `SubAgentTests`가 이름 규칙, 머리말 없음·닫힘 없음, `description` 없음/빈 값, 본문 없음, 알 수 없는 키, 잘못된·빈 `context`, 중복 키, 잘못된 줄, `sub_agents/` 안의 하위 디렉터리·비 `.md`, `sub_agents`가 파일인 경우를 각각 확인한다. `LinkTests.test_a_sub_agents_directory_link_is_rejected`, `test_a_link_inside_sub_agents_is_rejected`가 링크를 확인한다. `HashTests.test_changing_a_sub_agent_file_changes_the_hash`, `test_changing_a_sub_agent_name_changes_the_hash`가 해시 반영을 확인한다.
- AC-3: `AssemblyTests.test_sub_agents_add_the_consult_line_rules_and_agent_list`가 `consult` 줄(인자, `Cost: 0`), 이름·설명 목록, 계약 문구, 공식 9개 줄 유지를 확인한다. `ExampleHarnessTests.test_health_reports_sub_agents_without_changing_the_tool_set`가 `/health.tools`가 공식 9개 그대로이고 `/health.harness.sub_agents`에 이름이 나오는 것을 확인한다.
- AC-4: `ConsultTests.test_consult_sends_the_selected_context_and_returns_an_observation`(호출 1회, system=본문, task/dialogue/observations+request, 다음 호출 관찰, 공식 서비스 불변), `test_only_the_configured_context_items_are_sent`, `test_each_context_item_can_be_included_or_left_out`, `test_the_phase_two_follow_up_is_part_of_the_task_context`가 입력 구성을 확인한다.
- AC-5: 같은 테스트에서 `tool_trajectory`의 `consult`가 `cost 0`, `budget_before == budget_after`임을 확인한다. `test_consult_counts_one_model_action_and_the_sub_agent_call_none`이 24회 consult에서 `_model_actions(state) == MAX_MODEL_ACTIONS`, `subagent_call` 24개, 모델 요청 48개, `action_limit_reached`를 확인한다.
- AC-6: `test_an_unknown_sub_agent_is_rejected_without_a_model_call`, `test_invalid_consult_arguments_are_rejected_before_any_call`(코인 없는 거부), `test_a_sub_agent_model_failure_keeps_the_session_alive`(세션 지속, 실패 관찰, `model_error` 기록), `test_a_truncated_sub_agent_answer_is_announced`, `test_the_session_time_limit_ends_a_slow_sub_agent_call`(기존 `session_time_limit` 종료)이 확인한다.
- AC-7: `ConsultTests`가 `tool_trajectory` consult 항목과 `adk_events`의 `subagent_call`(이름, prompt, content, usage, harness)을 확인한다. `SubAgentTraceTests.test_a_consult_generation_is_recorded_under_the_session`가 `subagent:sql_reviewer` generation의 입력·출력·usage를, `test_a_failed_sub_agent_call_marks_the_generation_as_an_error`가 ERROR 표시를 확인한다. 기존 `model_call` event 형식은 `ToolLoopContractTests`의 기존 테스트가 그대로 통과한다.
- AC-8: `ExampleHarnessTests.test_the_sql_review_example_loads_and_reaches_the_prompt`가 로드, `sql_reviewer`, `consult` 줄을 확인한다.
- AC-9: `scripts/run-evaluation.ps1`은 수정하지 않았다. `Assert-BaselineAgent`는 `/health.tools`의 개수·이름·가격만 보며, `tool_metadata()`와 `/health.tools`는 공식 9개 그대로다. `ToolLoopContractTests.test_health_reports_the_tool_selecting_agent`와 `ExampleHarnessTests.test_health_reports_sub_agents_without_changing_the_tool_set`가 두 하네스에서 이를 확인한다. 스크립트 자체를 실제 서비스에 실행하지는 않았다(미검증 항목 참조).
- AC-10: 아래 전체 테스트 스위트를 코드 SHA에서 통과했다.
- AC-11: 실행하지 않았다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | 코드 SHA `8cd1c6b`, Python 3.12.10, 가짜 모델·서비스·Langfuse 클라이언트 | exit 0, `Ran 230 tests`, `OK (skipped=3)` | 전체 계약·하네스·루프·추적 테스트. skip 3건은 Windows 파일 심볼릭 링크 권한 없음(기존과 동일) |
| `.venv\Scripts\python.exe -m unittest tests.test_harness tests.test_tool_loop tests.test_tracing` | 같은 SHA, 변경 모듈 | exit 0, `Ran 150 tests`, `OK (skipped=3)` | 하네스 39 + 루프 78 + 추적 33 |
| `AGENT_HARNESS_DIR=harness/sql_review`로 `create_app()`의 `/health` 확인 | 같은 SHA, 모델·서비스 호출 없음 | `harness.name=sql_review`, `sub_agents=['sql_reviewer']`, `tools`는 공식 9개 그대로 | 환경 변수 하네스 선택과 `/health` 계약 |
| 합성 결과 JSON으로 `orchestrator/report.py` `generate_html()` | `subagent_call` event + `consult` tool_trajectory 포함 | exit 0, HTML 8,695 bytes, task card 생성 | `subagent_call`을 만나도 보고서가 실패하지 않음 |
| `observations` 맥락 크기 측정 | `results/deepseek_v41_flash_tool_loop_dev30_calibrated.json`(커밋·테스트 밖 로컬 결과) 30문제, 353 model_call | `messages[2:]` JSON 길이 min 17,069 / median 64,770.5 / p90 71,037 / max 78,516 chars. 최대 단일 관찰 26,603 chars(archeology_3) | 잘라 내기는 범위 밖. 늦은 단계 consult 입력이 대략 1.7만~7.9만 자(대략 5천~2만 토큰)가 될 수 있음 |
| `git diff --check` | 코드 커밋 전 | 출력 없음 | 공백 오류 없음 |

검증 코드와 최종 SHA 일치 근거: 코드·테스트를 모두 stage한 뒤 커밋 `8cd1c6b`를 만들었고, 그 커밋에서 전체 스위트를 다시 실행해 통과했다. 실행 시 미추적 파일은 이 작업의 `spec.md`·`implementation.md`뿐이며 테스트는 이를 읽지 않는다. `results/`·`private/`·`vendor/` 정답 자료를 읽는 테스트는 없다.

## 조사한 사항과 결정

- **허용 행동 집합:** `parse_response()`에 선택적 `allowed` 매핑을 추가하고 루프가 하네스에 따라 넘긴다. `ALLOWED_TOOLS`는 공식 9개 그대로라 비용·`/health`·여러 문장 거부 로직이 바뀌지 않는다.
- **`observations` 크기:** 위 측정 참조. 이번 범위에서 자르지 않았다. 후속 작업에서 크기 상한이나 요약이 필요할 수 있음을 조율자에게 알린다.
- **Langfuse 위치:** 세션 trace 바로 아래 generation(`subagent:<이름>`). tool span 아래에 두지 않아 consult span과 서브에이전트 호출을 각각 독립적으로 볼 수 있다.
- **결과 보고서:** `vendor/.../orchestrator/report.py`는 새 `type`을 무시하므로 수정이 필요 없다. 합성 결과로 확인했다.

## 미검증 항목과 제한

- AC-11(dev30 calibrated 실제 평가)은 실행하지 않았다. `harness/baseline`과 `harness/sql_review`의 성능 비교는 별도 작업이다.
- `scripts/run-evaluation.ps1`의 `Assert-BaselineAgent`를 실제 기동한 에이전트에 실행하지 않았다. `/health.tools` 계약 불변은 테스트와 코드 확인으로만 검증했다.
- 실제 Langfuse 서비스에 generation을 전송하지 않았다. 가짜 클라이언트로 기록 형식만 검증했다.
- 실제 모델로 `consult`를 호출해 답변 품질을 확인하지 않았다. 예시 하네스 문구의 성능은 완료 조건이 아니다.

## 실행 방법 (직접 확인)

```powershell
# 기준 하네스(consult 없음)
Remove-Item Env:AGENT_HARNESS_DIR -ErrorAction SilentlyContinue
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName task013_baseline_smoke

# 예시 하네스(consult + sql_reviewer)
$env:AGENT_HARNESS_DIR = 'harness/sql_review'
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName task013_sql_review_smoke
```

- `AGENT_HARNESS_DIR`는 에이전트 프로세스가 시작되기 전에 설정해야 한다. 포트 6000의 기존 서버를 스크립트가 재사용하면 그 서버가 시작할 때 읽은 하네스가 계속 쓰인다. `/health.harness`(`name`, `sha256`, `sub_agents`)로 실제 값을 확인할 수 있다.
- 결과 JSON에서 `tool_trajectory`의 `"tool": "consult"` 항목과 `adk_events`의 `"type": "subagent_call"` 항목을 확인한다. 기준 하네스 결과에는 둘 다 없어야 하고, 시스템 프롬프트(`adk_events[0].prompt[0].content`)에 `consult`가 없어야 한다.

## 리뷰 지적 처리

해당 없음(최초 구현).

## 필요한 판단

없음.

## 후속 문서 갱신 사항

- README·`docs/setup.md`에 `sub_agents/<이름>.md` 형식과 `consult` 사용법을 추가하는 것이 좋다. 이번 명세가 보고서 외 문서 수정을 제외해 반영하지 않았다.
