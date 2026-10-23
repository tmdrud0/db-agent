# TASK-010 구현 보고서

작업 ID: TASK-010
명세 버전: 1
대상 코드 버전: 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1
상태: ready_for_review
요약: 한 응답에 여러 행동이 있어도 첫 행동을 실행하고 나머지는 무시하며, DSML만 있는 응답은 첫 `invoke`로 해석한다. `execute_sql`에 문장이 둘 이상이면 서비스 호출과 코인 차감 전에 거부한다.
근거: `agent/tools.py`, `agent/tool_loop.py`, `agent/tracing.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`
미확정 사항: 없음

## 커밋과 검토 범위
기준 커밋: a5af7be3b12c5f43d92cbff765c688c7897e4d5a
코드 커밋: 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1
작업 커밋 목록:
- 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1 `[TASK-010] feat: parse the first action and reject multi-statement execute_sql`
검토 명령: `git diff a5af7be3b12c5f43d92cbff765c688c7897e4d5a 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1 --`
현재 코드와 대상 SHA의 차이: 없음. 커밋 후 추적 파일의 변경이 없고, 작업 폴더의 `spec.md`·`implementation.md`만 미추적 문서로 남아 있다.
기존 사용자 변경: 없음. 다른 작업이나 사용자 변경을 섞지 않았고, 문서 산출물은 커밋하지 않았다.

## 변경 내용

- `agent/tools.py`
  - `parse_response()`와 `ParsedAction(name, args, action_count, ignored_actions)`를 추가했다. `parse_action()`은 기존처럼 `(name, args)`를 반환하는 호환 함수로 남겼다.
  - `json.JSONDecoder().raw_decode()`로 응답의 모든 완전한 JSON 객체를 순서대로 훑는다. 문자열 값 안의 `{`, `}`, `"action"` 때문에 경계를 잘못 잡지 않는다. 첫 JSON 객체가 `action`을 가진 객체가 아니면 뒤에 올바른 행동이 있어도 거부한다(AC-4).
  - DSML 표기(`<｜｜DSML｜｜ invoke ...>`)를 해석한다. 전각 세로선 U+FF5C와 ASCII `|`를 모두 받아들이고, 실제 결과 파일에서 확인한 두 개 세로선 표기(`<｜｜DSML｜｜`)를 처리한다. JSON 행동이 없을 때만 첫 `invoke`를 행동으로 쓴다.
  - DSML 인자는 `parameter name="args"` 본문의 JSON 객체를 우선 사용하고, 없으면 이름 있는 `parameter`들을 인자로 모은다. 이 fallback은 관찰된 `parameter name="sql"` 형식을 받아들이기 위한 구현 선택이다. `invoke` 이름 속성이 닫히지 않거나 여는 태그에 인자가 섞이는 등 형식이 깨지면 JSON 형식 안내와 함께 거부한다.
  - `submission_key()`와 SQL 토큰 보조 함수(`_quoted_span`, `_dollar_span`, `_block_comment_end`, `_starts_string_constant`, `DOLLAR_QUOTE`, `SQL_WHITESPACE`)를 `tool_loop.py`에서 옮겨 왔다. `agent.tool_loop.submission_key` import 경로는 유지된다.
  - `statement_count()`를 추가했다. 문자열 리터럴, `E'...'` 등 접두 문자열, 따옴표 식별자, 달러 인용, 줄 주석, 중첩 블록 주석 안의 `;`를 무시한다. 닫히지 않은 따옴표·주석·달러 인용처럼 경계를 안전하게 판단할 수 없으면 `None`을 반환한다.
  - `validate_args()`에서 `execute_sql`의 `sql` 문장이 둘 이상이면 `ActionError`로 거부한다. `submit_sql`은 검사하지 않는다.

- `agent/tool_loop.py`
  - `parse_response()`를 사용하고, 첫 행동만 실행한다. 같은 행동의 반복(펜스 반복, 같은 이름·인자의 DSML 반복)은 안내 없이 받아들인다.
  - 다른 행동이 더 있으면 첫 행동의 관찰 끝에 `The response contained N actions; only the first was executed. Send exactly one action per response.` 안내를 붙인다. `submit_sql`의 실패·중복·phase 전환 관찰에도 같은 안내를 붙인다.
  - 받아들인 응답이 행동을 둘 이상 포함하면 `adk_events`에 `action_count`를, 서로 다른 행동을 무시했으면 `ignored_actions` 수를 기록한다. 같은 행동만 반복된 경우 `ignored_actions`는 기록하지 않는다.
  - 응답이 출력 한도로 잘린 경우(`reply.truncated`)에는 완전한 행동이 들어 있어도 파싱 전에 거부한다. 명세의 유지할 거부 항목을 코드에서 명시적으로 보장한다.
  - `submission_key`는 `agent.tools`에서 가져온다. `re` import는 더 이상 필요하지 않아 제거했다.

- `agent/tracing.py`
  - `Generation.finish()`에 `action_count`, `ignored_actions`를 추가했다. Langfuse generation 메타데이터에 두 값이 `None`이 아닐 때만 기록한다. 분석 편의를 위한 기록이며 모델 동작이나 세션 상태에는 영향을 주지 않는다.

- `tests/test_tool_loop.py`, `tests/test_tracing.py`
  - 여러 JSON 행동, 펜스 반복, 같은 DSML 반복, 다른 DSML, 문자열 내부 중괄호/`action` 사례를 추가했다.
  - DSML 인자 있음/없음/이름 있는 인자/여러 `invoke`, 관찰된 형식 깨짐 사례 두 개, 첫 행동이 잘못됐을 때 fallback하지 않는 사례를 추가했다.
  - `execute_sql` 문장 수 허용·거부, 경계 판단 불가 시 기존 동작 유지, `submit_sql` 미검사를 단위 테스트로 확인한다.
  - 세션 단위로 무시 안내·비용·`adk_events` 기록, 동일 행동 무안내, DSML 실행, 여러 문장 `execute_sql`의 무호출·비용 0 거부를 확인한다.
  - Langfuse generation 메타데이터에 무시 정보가 전달되는지 확인한다.

`SYSTEM_PROMPT`, 도구 목록·코인 비용, 모델 행동 한도(24회), 세션 제한, 중복 제출 규칙, 채점·보상 기록, 모델 요청 설정은 바꾸지 않았다. 결과 JSON의 기존 필드는 유지하고 `adk_events`에 필드만 추가했다.

## 완료 조건별 근거

- AC-1: `ActionParsingTests.test_several_actions_in_one_response_use_the_first`
  - 같은 JSON + 같은 JSON 펜스, 빈 줄을 둔 같은 JSON 두 번, 서로 다른 JSON 행동 둘, JSON + 같은 DSML, JSON + 다른 DSML을 모두 확인했다.
  - 문자열 값 안에 `{`, `}`, `"action"`이 든 단일 행동은 `test_braces_and_action_words_inside_string_values_are_one_action`에서 확인했다.
- AC-2: `MultipleActionAndStatementTests.test_only_the_first_of_several_actions_runs_and_the_rest_are_noticed`
  - `/schema`만 호출되고 `/submit` 비용만 추가되어 예산이 18→17→14가 된다. 관찰에 안내가 붙고 `adk_events[0]`에 `action_count=2`, `ignored_actions=1`이 기록된다.
  - 같은 행동만 반복된 `test_a_repeated_identical_action_is_not_announced`에서는 안내가 없고 `ignored_actions`가 기록되지 않는다.
- AC-3: `ActionParsingTests.test_dsml_only_responses_are_read_from_the_first_invoke`, `test_malformed_dsml_only_responses_are_rejected_with_json_guidance`
  - `parameter name="args"` 본문, 인자 없음, `parameter name="sql"` 이름 인자, `invoke` 여러 개를 첫 `invoke`로 해석한다.
  - 관찰된 `invoke name="get_all_knowledge_definitions"}`와 `invoke name="...", "args": {}}` 형식, JSON 객체가 아닌 args 본문은 `{"action": ...}` JSON 형식 안내와 함께 거부한다.
- AC-4: 기존 `ActionParsingTests` 전체가 그대로 통과한다. `test_an_invalid_first_action_is_not_replaced_by_a_later_valid_one`에서 행동 없는 JSON, 알 수 없는 행동, 잘못된 인자를 가진 첫 행동 뒤에 올바른 행동이 있어도 거부한다.
- AC-5: `ExecuteSqlStatementTests`
  - 거부: `SELECT 1; SELECT 2`, `SELECT 1; SELECT 2;`
  - 허용: `SELECT 1`, `SELECT 1;`, `SELECT 1; -- note`, `SELECT 1; /* note */`, `SELECT ';'`, `SELECT E'a;b'`, `SELECT "a;b" FROM t`, `SELECT $$;$$`, `SELECT $x$;$x$`, `SELECT 1 /* ; /* ; */ */`
  - 경계 판단 불가: 닫히지 않은 `'`, 블록 주석, `$$`, 백슬래시가 있는 E 문자열은 거부하지 않는다.
  - `submit_sql`의 여러 문장은 통과한다.
- AC-6: `MultipleActionAndStatementTests.test_multiple_statement_execute_sql_is_rejected_without_a_service_call`
  - 서비스 경로는 `/submit` 하나뿐이고, 거부 관찰은 `| cost 0 |`, `budget 18`이며, `tool_trajectory[0]`의 `budget_before`가 18이다.
- AC-7: 전체 테스트 스위트 178개 통과(아래 표).
- AC-8: 아래 재해석 표. smoke 4/4, task009 15/17, task008 13/15를 받아들였다. 거부는 형식이 깨진 DSML 3건과 잘린 응답 1건이다.
- AC-9: 선택 사항이며 실제 평가를 실행하지 않았다. 아래 미검증 항목에 적었다.

## 오프라인 재해석(AC-8)

새 파서에 `action_error`가 있는 `adk_events[].content`를 다시 넣었다. 잘림(`truncated`) 여부는 새 세션 루프와 동일하게 파싱 전에 거부로 처리했다. 커밋하지 않은 로컬 스크립트를 사용했다.

### 유형별 결과

| 파일 | 유형 | 받아들임 | 거부 | 거부 이유 |
| --- | --- | ---: | ---: | --- |
| `results/tools_smoke_task009.json` | 같은 JSON 반복(펜스) | 4 | 0 | - |
| `results/tools_dev30_calibrated_task009.json` | 다른 JSON 행동 | 5 | 0 | - |
| `results/tools_dev30_calibrated_task009.json` | JSON + DSML | 6 | 0 | - |
| `results/tools_dev30_calibrated_task009.json` | DSML만 | 4 | 2 | 형식이 깨진 DSML |
| `results/tools_dev30_calibrated_task008.json` | 다른 JSON 행동 | 8 | 0 | - |
| `results/tools_dev30_calibrated_task008.json` | JSON + DSML | 1 | 0 | - |
| `results/tools_dev30_calibrated_task008.json` | DSML만 | 4 | 1 | 형식이 깨진 DSML |
| `results/tools_dev30_calibrated_task008.json` | 출력 한도로 잘림 | 0 | 1 | 잘린 응답 |

### 거부된 응답

| 파일 | 태스크 | 내용 | 이유 |
| --- | --- | --- | --- |
| task009 | mental_2 | `<｜｜DSML｜｜ invoke name="get_all_knowledge_definitions"}` | `invoke` 여는 태그가 닫히지 않음 |
| task009 | fake_6 | `<｜｜DSML｜｜ invoke name="get_all_external_knowledge_names", "args": {}}` | 여는 태그에 인자가 섞임 |
| task008 | fake_6 | 위와 같은 형식 | 여는 태그에 인자가 섞임 |
| task008 | museum_M_4 | DSML이 출력 한도에서 잘림 | `truncated` |

### 여러 문장 `execute_sql`

| 파일 | 실행 기록의 여러 문장 `execute_sql` | 새 검증에서 거부 | 경계 판단 불가 |
| --- | ---: | ---: | ---: |
| smoke task009 | 4 | 4 | 0 |
| dev30 task009 | 7 | 7 | 0 |
| dev30 task008 | 9 | 9 | 0 |

명세의 관찰에는 task009가 8회로 적혀 있으나, `adk_events`의 실행 행동과 `tool_trajectory`를 모두 세어도 7회다. 두 경로의 결과가 같으므로 이 보고서는 실측 7회를 기록한다.

### 전체 응답 전환 확인

기존 결과 파일의 모든 `model_call` 응답을 새 파서로 다시 해석해 전환을 확인했다.

| 파일 | 기존 수락→신규 수락 | 기존 거부→신규 수락 | 기존 수락→신규 거부 | 기존 거부→신규 거부 |
| --- | ---: | ---: | ---: | ---: |
| smoke task009 | 12 | 4 | 4 | 0 |
| dev30 task009 | 366 | 15 | 7 | 2 |
| dev30 task008 | 359 | 13 | 9 | 2 |

기존 수락→신규 거부는 모두 여러 문장 `execute_sql`이며 이번 명세가 의도한 비용 없는 거부다. 그 밖의 응답에서 첫 행동 이름·인자가 달라진 사례는 0건이었다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | Windows, Python 3.12, 코드 커밋과 같은 추적 파일 내용, 추가 문서는 미추적 | exit 0, `Ran 178 tests`, `OK` | 기존 164개 + 신규 14개 전체 통과 |
| AC-8 재해석 스크립트(미커밋) | `results/`의 세 파일, 위 코드 SHA | 위 표와 동일 | 거부 응답 재해석, 여러 문장 `execute_sql` 재검증 |
| `git diff --check` | 커밋 전 staged 내용 | 출력 없음 | 공백 오류 없음 |

검증 코드와 최종 SHA 일치 근거: 전체 테스트와 재해석은 마지막 코드 수정 뒤, 커밋 직전의 추적 파일 내용으로 실행했다. 커밋 `3c204df`는 그 파일들과 같고, 커밋 뒤에는 미추적 문서만 추가했다. 제외한 사용자 변경은 없으며, 테스트가 읽지 않는 `results/`·`private/` 파일에 의존한 결과도 없다. `git diff 3c204df -- agent tests`가 비어 있음을 커밋 후 확인할 수 있다.

## 미검증 항목과 제한

- AC-9(선택) 실제 dev30 calibrated 평가를 실행하지 않았다. 따라서 거부 횟수·여러 문장 `execute_sql` 횟수·보상의 실제 실행 변화는 확인하지 않았다.
- 실제 Langfuse 서버에서 `action_count`·`ignored_actions` 메타데이터가 보이는지는 확인하지 않았다. 기록용 fake client를 쓰는 `test_ignored_actions_reach_the_generation_metadata`로 전달 경로만 검증했다.
- `results/` 파일은 재해석용 입력으로만 읽었고 테스트나 커밋 산출물에 포함하지 않았다.
- 여러 문장 `submit_sql`의 채점기 동작은 이번 범위가 아니며 확인하지 않았다. 후속 작업 후보로 남긴다.

## 리뷰 지적 처리

최초 검토이므로 해당 없음.

## 구현 결정과 후속 후보

- 잘린 응답은 완전한 행동이 보이더라도 세션 루프에서 거부한다. 명세의 "출력 한도로 잘린 응답은 지금처럼 거부" 항목을 파서 입력만으로는 보장할 수 없어, `reply.truncated`를 파싱 전에 검사했다. 이전에는 잘린 응답에 완전한 JSON이 있으면 실행될 수 있었으나, 신뢰할 수 없는 응답을 실행하지 않는 쪽을 택했다.
- DSML 인자는 `parameter name="args"` JSON을 우선하고, 없으면 이름 있는 `parameter`를 인자로 모은다. 이 fallback으로 task008 fake_3의 `parameter name="sql"` 응답도 실행 가능한 행동으로 해석했다. 명세가 정한 `args` 본문 경로는 그대로 유지한다.
- 첫 JSON 객체가 `action` 없는 JSON이면 뒤에 올바른 행동이 있어도 거부한다(AC-4). 이는 기존 거부 결과를 유지하기 위한 결정이며, DSML만 있는 응답은 JSON 객체가 있어도 `action` 객체가 없으면 DSML 경로로 처리한다.
- `submission_key()`와 SQL 토큰 보조 함수를 `agent/tools.py`로 옮겼다. `validate_args()`가 `submission_key()`와 같은 토큰 규칙으로 문장 수를 셀 수 있게 하기 위해서다. `agent.tool_loop.submission_key`는 계속 import할 수 있다.
- 후속 작업 후보: 여러 문장을 담은 `submit_sql`을 채점기가 어떻게 처리하는지 확인하고, 필요하면 `execute_sql`과 같은 비용 없는 거부를 적용할지 결정한다.
