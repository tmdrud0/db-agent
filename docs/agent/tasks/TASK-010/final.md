# TASK-010 최종 보고서

작업 ID: TASK-010
명세 버전: 1
기준 코드 버전: a5af7be3b12c5f43d92cbff765c688c7897e4d5a
대상 코드 버전: 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1
상태: ready_for_user
요약: 한 응답에 행동이 여러 개 있거나, 같은 행동이 반복되거나, DSML 도구 호출만 있어도 첫 행동을 실행한다. 여러 문장을 담은 `execute_sql`은 서비스 호출과 코인 차감 없이 거부한다. 필수 완료 조건 AC-1~AC-8을 충족했고, 같은 명세 버전과 코드 SHA에 대한 리뷰 판정은 `pass`다. 선택 항목 AC-9(실제 평가)는 실행하지 않았다.
근거: 아래에 코드 위치, 테스트, 기존 결과 파일 재해석, 리뷰 결론을 직접 기록했다. 조율자가 대상 SHA에서 전체 테스트를 다시 실행했다(178 OK).
미확정 사항: 없음.

## 변경 내용과 설계 결정

작업 코드 커밋: 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1 `[TASK-010] feat: parse the first action and reject multi-statement execute_sql`
변경 파일: `agent/tools.py`, `agent/tool_loop.py`, `agent/tracing.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py` (+716/−178)

**행동 해석** (`agent/tools.py`)
- `parse_response()`가 `ParsedAction(name, args, action_count, ignored_actions)`를 반환한다. 기존 `parse_action()`은 `(name, args)`를 반환하는 호환 함수로 남겼다.
- `json.JSONDecoder().raw_decode()`로 응답의 완전한 JSON 객체를 앞에서부터 훑는다. 그래서 문자열 값 안의 `{`, `}`, `"action"`에 경계가 흔들리지 않는다.
- 첫 행동만 사용한다.
  - 첫 JSON 객체에 `action`이 없거나, 첫 행동이 알 수 없는 이름이거나 인자가 잘못됐으면 뒤에 올바른 행동이 있어도 거부한다. 기존 거부 결과를 유지하기 위해서다.
- DSML 해석: JSON 행동이 없을 때만 첫 `invoke`를 행동으로 읽는다.
  - 표기는 전각 `｜`(U+FF5C)과 ASCII `|`를 모두 받는다.
  - 인자는 `parameter name="args"`의 JSON 본문을 우선한다. 없으면 이름 있는 `parameter`들을 인자로 모은다(구현자 선택, 관찰된 `parameter name="sql"` 형식을 받아들이기 위함).
  - 형식이 깨진 DSML은 JSON 형식 안내와 함께 거부한다.

**여러 문장 거부** (`agent/tools.py`)
- `statement_count()`가 문장 수를 센다.
  - 문자열(`E'...'` 포함), 따옴표 식별자, 달러 인용, 줄 주석, 중첩 블록 주석 안의 `;`는 세지 않는다.
  - 경계를 안전하게 판단할 수 없으면 `None`을 돌려주고, 이 경우 거부하지 않는다.
- `validate_args()`는 `execute_sql`의 문장이 둘 이상이면 `ActionError`를 낸다. 기존 거부 경로를 그대로 타므로 비용 0이고 모델 행동 1회로 센다. `submit_sql`은 검사하지 않는다.
- 같은 토큰 규칙을 쓰려고 `submission_key()`와 SQL 토큰 보조 함수를 `tool_loop.py`에서 `tools.py`로 옮겼다(내용 변경 없음). `agent.tool_loop.submission_key` import 경로는 유지된다.

**세션 루프와 기록** (`agent/tool_loop.py`, `agent/tracing.py`)
- 다른 행동을 무시한 경우 첫 행동의 관찰 끝에 `The response contained N actions; only the first was executed. Send exactly one action per response.`를 붙인다. `submit_sql`의 중복 차단, 오답, phase 전환 관찰에도 같은 안내가 붙는다. 같은 행동만 반복된 경우에는 붙이지 않는다.
- `adk_events`에 `action_count`(행동이 둘 이상일 때)와 `ignored_actions`(다른 행동을 무시했을 때)를 새 필드로 기록한다. Langfuse generation 메타데이터에도 같은 값을 남긴다.
- **설계 변경 1건**: 출력 한도로 잘린 응답(`reply.truncated`)은 파싱 전에 거부한다. 새 파서는 잘린 응답의 앞부분에 완전한 행동이 있으면 받아들이기 때문에, 명세 규칙 5("잘린 응답은 지금처럼 거부")를 지키려면 명시적으로 검사해야 했다. 리뷰어가 결과 파일의 잘린 응답 19건을 확인했고, 모두 기존 코드에서도 거부됐다. 관찰된 실행 결과는 바뀌지 않는다.

**바꾸지 않은 것**: `SYSTEM_PROMPT`(조율자가 diff로 확인), 도구 목록과 코인 비용, `MAX_MODEL_ACTIONS = 24`, 세션 시간 제한, 중복 제출 규칙, 채점·보상 기록, 모델 요청 설정.

## 설정과 실행 방법

새 설정이나 환경 변수는 없다. 에이전트 실행과 평가 명령은 기존과 같다(`docs/setup.md`).
결과 JSON에서 이번 변경의 효과는 `adk_events[].action_count`, `adk_events[].ignored_actions`, 그리고 `action_error`에 기록된 여러 문장 거부 메시지로 확인할 수 있다.

## 완료 조건별 결과
| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `ActionParsingTests.test_several_actions_in_one_response_use_the_first`: (a) JSON + 같은 JSON 펜스, (b) 같은 JSON 두 번, (c) 서로 다른 JSON 행동, (d) JSON + 같은 DSML, (e) JSON + 다른 DSML. `test_braces_and_action_words_inside_string_values_are_one_action`: (f) |
| AC-2 | 충족 | `MultipleActionAndStatementTests.test_only_the_first_of_several_actions_runs_and_the_rest_are_noticed`: `/schema`만 호출, 예산 18→17→14, 안내 문구, `action_count=2`, `ignored_actions=1`. `test_a_repeated_identical_action_is_not_announced`: 안내와 `ignored_actions` 없음 |
| AC-3 | 충족 | `test_dsml_only_responses_are_read_from_the_first_invoke`: 인자 있음, 인자 없음, 이름 인자, `invoke` 여러 개. `test_malformed_dsml_only_responses_are_rejected_with_json_guidance`: 관찰된 깨진 형식 2개와 JSON 객체가 아닌 args |
| AC-4 | 충족 | 기존 `ActionParsingTests` 전체 통과. `test_an_invalid_first_action_is_not_replaced_by_a_later_valid_one` |
| AC-5 | 충족 | `ExecuteSqlStatementTests`: 명세의 거부 2건과 허용 사례 전부, `SELECT 1; /* note */`, `SELECT E'a;b'`, 경계 판단 불가 4종(닫히지 않은 `'`, 블록 주석, `$$`, 백슬래시가 든 E 문자열), `submit_sql` 미검사 |
| AC-6 | 충족 | `test_multiple_statement_execute_sql_is_rejected_without_a_service_call`: 서비스 호출은 `/submit`뿐, 관찰 `cost 0`, `budget 18`, 다음 trajectory의 `budget_before` 18 |
| AC-7 | 충족 | 전체 178개 테스트 통과(구현자, 리뷰어, 조율자가 각각 실행) |
| AC-8 | 충족 | 아래 "기존 결과 파일 재해석" |
| AC-9 (선택) | 미검증 | 실제 dev30 평가는 실행하지 않음 |

### 기존 결과 파일 재해석 (AC-8)

구현자가 커밋하지 않은 로컬 스크립트로 `action_error`가 있던 응답을 새 파서로 다시 해석했다.

| 파일 | 기존 거부 | 새 파서에서 수락 | 계속 거부 |
| --- | ---: | ---: | --- |
| `results/tools_smoke_task009.json` | 4 | 4 | 없음 |
| `results/tools_dev30_calibrated_task009.json` | 17 | 15 | 깨진 DSML 2 (mental_2, fake_6) |
| `results/tools_dev30_calibrated_task008.json` | 15 | 13 | 깨진 DSML 1 (fake_6), 잘린 응답 1 (museum_M_4) |

- 여러 문장 `execute_sql`: smoke 4/4, task009 7/7, task008 9/9가 새 검증에서 거부된다. 경계를 판단할 수 없어 통과시킨 사례는 0건이다.
- 명세에는 task009가 8회로 적혀 있었다. 조율자가 `;`만으로 세면서 문자열 리터럴 안의 `;`(`'...; phq9=' || ...`)까지 센 오류이고, 실측 7회가 맞다.
- 리뷰어가 별도로 확인했다. 기존 파서와 새 파서로 결과 파일의 잘리지 않은 모델 응답 2,001건을 모두 다시 해석했다.
  - 기존에 수락됐다가 새로 거부되는 27건은 모두 여러 문장 `execute_sql`이었다(이번 작업이 의도한 거부).
  - 기존에 수락된 응답 가운데 첫 행동의 이름이나 인자가 바뀐 사례는 0건이다.

## 검증 결과
| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.venv/Scripts/python.exe -m unittest discover -s tests` | 3c204df, Windows, Python 3.12, 조율자 실행 | exit 0, `Ran 178 tests`, `OK` | 기존 164개 + 신규 14개 |
| 같은 명령 | 3c204df, 리뷰어 실행 | `Ran 178 tests`, `OK` | 리뷰어 독립 확인 |
| 같은 명령 | 커밋 직전 추적 파일(= 3c204df), 구현자 실행 | `Ran 178 tests`, `OK` | |
| AC-8 재해석 스크립트(미커밋) | 3c204df, `results/`의 세 파일 | 위 표 | 거부 응답 재해석, 여러 문장 검증 |
| 리뷰어 전체 응답 재해석(미커밋) | a5af7be와 3c204df의 파서, 결과 파일의 응답 2,001건 | 의도하지 않은 전환 0건 | 회귀 없음 |
| `git diff 3c204df -- agent tests` | 조율자 확인 | 출력 없음 | 작업 공간과 대상 SHA 일치 |
| `git diff a5af7be 3c204df -- agent/tool_loop.py` | 조율자 확인 | `SYSTEM_PROMPT`·상수 변경 없음 | 제약 유지 |

검증 코드와 최종 SHA 일치 근거: HEAD는 `3c204dfef9f45be7cac6e743fafa8cad4e84d4b1`이고 `agent`·`tests`에 미커밋 변경이 없다. 미추적 파일은 `docs/agent/tasks/TASK-010/`의 문서뿐이며, 테스트는 이 문서나 `results/`를 읽지 않는다.
검증 범위: 계약 테스트(가짜 모델·환경)와 기존 결과 파일을 이용한 오프라인 재해석. 실제 DB 채점, 실제 모델 실행, 실제 Langfuse 표시는 이번에 실행하지 않았다.

## 리뷰 처리 결과
검토 명세 버전: 1
검토한 코드 SHA: 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1
리뷰 판정: pass
판정 근거·독립 검증: 누적 diff 검토, 전체 테스트 178 OK, 결과 파일 응답 2,001건 재해석, 경계 사례 직접 실행(예: `SELECT 1;;` → 1문장, `BEGIN; SELECT 1; COMMIT` → 3문장으로 거부). AC-9와 실제 Langfuse 표시는 확인하지 않았다.
지적 처리: 수정 요청 없음. 별도 제안은 다음과 같이 정리했다.
- 제안 1 (잘린 응답을 파싱 전에 거부): 명세 규칙 5를 지키기 위한 처리로 받아들인다. "완전한 행동 + 잘림 → 거부"를 확인하는 세션 회귀 테스트가 없으므로 후속 작업 후보로 둔다.
- 제안 2 (`[{"action": ...}]` 배열 형식과 "DSML 뒤 JSON" 순서는 이제 거부): 결과 파일에서 관찰되지 않았고 명세의 유지 형식에도 없다. 지금은 보류하고, 실제 평가에서 나타나면 다시 다룬다.
- 제안 3 (여러 문장 `submit_sql`의 채점기 동작): 후속 작업 후보.

## 남은 제한
- 실제 평가(AC-9)를 하지 않았다. 따라서 실행에서 거부와 낭비 행동이 실제로 줄었는지, 보상에 영향이 있는지는 확인하지 않았다. 같은 코드로 실행한 두 번에서도 13/30 태스크 결과가 바뀌었으므로, 효과를 볼 때는 보상보다 거부 횟수와 여러 문장 `execute_sql` 횟수를 먼저 봐야 한다.
- 실제 Langfuse 서버에서 `action_count`·`ignored_actions` 메타데이터가 보이는지는 확인하지 않았다(fake client로 전달 경로만 검증).
- 배열로 감싼 단일 행동은 이전과 달리 거부된다. 관찰된 사례는 없다.
- 위 제한은 필수 완료 조건이나 유지할 계약을 위반하지 않는다.

## 필요한 보완·판단
없음. 후속 작업 후보:
1. 잘린 응답 정책의 세션 회귀 테스트 추가.
2. 여러 문장 `submit_sql`을 채점기가 어떻게 처리하는지 확인하고 처리 방침 결정.
3. 선택: dev30 calibrated 평가로 실제 거부·낭비 행동 감소 확인(AC-9).

## 직접 확인 방법
```
.venv/Scripts/python.exe -m unittest discover -s tests
```
기대 결과: `Ran 178 tests`, `OK`.
실제 실행을 원하면 평가 1문제 이상을 실행한 뒤 결과 JSON의 `adk_events`에서 `action_count`·`ignored_actions` 필드와 여러 문장 `execute_sql` 거부 메시지를 확인한다.

## 사용자 결정
결정: 승인
대상: 명세 버전 1, 최종 코드 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1
확인 근거: 사용자가 2026-10-20 조율자 채팅에서 "TASK-010 명세 v1, 코드 3c204df 기준으로 승인해. spec.md와 final.md를 문서 커밋해줘."라고 승인함.
보존 문서: spec.md, final.md
