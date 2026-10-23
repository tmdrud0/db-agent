# TASK-010 리뷰 보고서

작업 ID: TASK-010
명세 버전: 1
대상 코드 버전: 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1
상태: pass
요약: 첫 행동 해석, 무시 안내·기록, DSML 해석, 여러 문장 `execute_sql`의 비용 없는 거부가 명세대로 구현됐고 AC-1~AC-8을 충족한다. 수정을 요구할 결함은 없다. 잘린 응답 처리 방식의 변경과 배열로 감싼 행동의 거부는 조율자 확인용으로 아래에 적었다.
근거: 누적 diff 검토, 대상 SHA에서 전체 테스트 실행(178 OK), 기존·신규 파서로 `results/`의 전체 모델 응답을 다시 해석, 경계 사례 직접 실행.
미확정 사항: 없음.

## 검토 범위
기준 커밋: a5af7be3b12c5f43d92cbff765c688c7897e4d5a
검토 코드 커밋: 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1
검토 명령: git diff a5af7be3b12c5f43d92cbff765c688c7897e4d5a 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1 --
검증한 작업 공간 코드 버전: HEAD = 3c204dfef9f45be7cac6e743fafa8cad4e84d4b1. `git diff 3c204df -- agent tests` 출력 없음. 미추적 파일은 `docs/agent/tasks/TASK-010/`뿐이다.

변경 파일: `agent/tools.py`, `agent/tool_loop.py`, `agent/tracing.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`(+716/−178). `submission_key()`와 SQL 토큰 보조 함수는 `tool_loop.py`에서 `tools.py`로 옮겼고 내용은 바뀌지 않았다. `agent.tool_loop.submission_key` import 경로는 유지된다.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `_json_objects()`가 `raw_decode`로 객체 단위로 훑어 문자열 안의 중괄호에 영향받지 않는다. `test_several_actions_in_one_response_use_the_first`가 (a)~(e), `test_braces_and_action_words_inside_string_values_are_one_action`이 (f)를 확인한다. |
| AC-2 | 충족 | `_ignored_notice()`가 일반 관찰과 `submit_sql`의 세 관찰 경로(중복 차단, 오답, phase 전환)에 붙는다. 세션 테스트에서 예산 18→17→14, `action_count=2`, `ignored_actions=1`을 확인하고, 같은 행동 반복은 안내와 `ignored_actions`가 없음을 확인한다. |
| AC-3 | 충족 | 전각 `｜`과 ASCII `|`, 두 개 세로선 표기를 처리한다. args 있음, 인자 없음, `invoke` 여러 개는 첫 `invoke`로 해석하고, 관찰된 깨진 두 형식과 JSON 객체가 아닌 args는 `DSML_GUIDANCE`(JSON 형식 안내)와 함께 거부한다. |
| AC-4 | 충족 | 기존 `ActionParsingTests` 그대로 통과. `json_objects[0] is not json_actions[0]` 검사로 행동 없는 첫 JSON 뒤 행동을 거부하고, 알 수 없는 행동·잘못된 인자를 가진 첫 행동은 다음 후보로 넘어가지 않는다(`test_an_invalid_first_action_is_not_replaced_by_a_later_valid_one`). |
| AC-5 | 충족 | `statement_count()`가 명세의 거부·허용 사례를 모두 테스트로 확인하고, 경계 판단 불가 시 `None`을 반환해 거부하지 않는다. `submit_sql`은 검사하지 않는다. |
| AC-6 | 충족 | `test_multiple_statement_execute_sql_is_rejected_without_a_service_call`: 서비스 경로는 `/submit`뿐, 거부 관찰에 `| cost 0 |`, `budget 18]`, 첫 trajectory의 `budget_before`가 18이다. 기존 `ActionError` 경로를 그대로 쓰므로 행동 1회로 센다. |
| AC-7 | 충족 | 리뷰어가 직접 실행: `Ran 178 tests`, `OK`. |
| AC-8 | 충족 | implementation.md에 유형별·거부 이유·여러 문장 `execute_sql` 표가 있다. 리뷰어가 별도로 전체 결과 파일을 재해석한 결과도 보고서 결론과 일치한다(아래 독립 검증). 명세의 task009 "8회"와 실측 7회 차이는 보고서에 근거와 함께 기록돼 있다. |
| AC-9 | 미검증(선택) | 실제 평가는 실행하지 않았다. 명세상 선택 항목이다. |

제약 확인: `SYSTEM_PROMPT`, 도구 목록·비용, `MAX_MODEL_ACTIONS`, 세션 제한, 중복 제출 규칙, 채점·보상 기록, 모델 요청 설정은 diff에서 바뀌지 않았다. 결과 JSON은 `adk_events`에 `action_count`·`ignored_actions` 필드만 추가했다. 테스트는 `results/`를 읽지 않고 실제 Langfuse에 전송하지 않는다.

## 수정 요청
없음.

## 독립 검증
- `.venv/Scripts/python.exe -m unittest discover -s tests` (Windows, 대상 SHA와 같은 작업 공간): `Ran 178 tests`, `OK`.
- 기존 파서(`git show a5af7be:agent/tools.py`)와 새 파서로 `results/*.json`의 잘리지 않은 모든 `model_call` 응답 2,001개를 다시 해석(미커밋 스크래치 스크립트, 결과물 저장 없음):
  - 기존 수락→신규 거부 27건은 모두 `execute_sql`이며 여러 문장 거부다.
  - 기존 수락 응답 중 첫 행동의 이름·인자가 달라진 사례 0건, 그 밖의 기존 수락→신규 거부 0건.
- 잘린(`truncated`) `model_call` 19건(여러 결과 파일)은 기존 코드에서도 모두 거부됐다. 따라서 잘린 응답을 파싱 전에 거부하는 변경은 관찰된 실행에서 결과를 바꾸지 않는다.
- 경계 사례 직접 실행:
  - `[{"action": "get_schema", "args": {}}]`: 기존 수락 → 신규 거부(아래 별도 제안 1).
  - DSML 호출 뒤 JSON 행동: DSML의 args 본문 `{"sql": ...}`이 첫 JSON 객체로 잡혀 거부된다(아래 별도 제안 2).
  - `Result was {"count": 3}. {"action": ...}`: 기존·신규 모두 거부.
  - 같은 행동인데 SQL 끝 공백만 다른 반복: `ignored_actions=1`로 안내가 붙는다. 실행은 첫 행동이고 해가 없어 지적하지 않는다.
  - `statement_count`: `SELECT 1;;` → 1, `BEGIN; SELECT 1; COMMIT` → 3(거부, 의도대로), `a$b$c, d$b$e; SELECT 2` → 2.
- 실행하지 못한 것: AC-9 실제 평가, 실제 Langfuse 서버에서 메타데이터 표시 확인.

## 이전 지적 처리
최초 검토이므로 해당 없음.

## 별도 제안·판단 요청
명세 밖이거나 통과를 막지 않는 사항이다.

1. **잘린 응답 처리 변경(조율자 확인 권장)**: 기존에는 잘린 응답도 파싱에 성공하면 실행했고, 이제는 `reply.truncated`면 파싱 전에 항상 거부한다(`agent/tool_loop.py:679`). 새 파서는 잘린 응답의 앞쪽에 완전한 행동이 있으면 받아들이므로, 명세 규칙 5("잘린 응답은 지금처럼 거부")를 지키려면 필요한 처리다. 위 재해석에서 기존 코드가 잘린 응답을 받아들인 사례는 0건이라 실제 영향은 관찰되지 않았다. 다만 "완전한 행동 + 잘림 → 거부"를 확인하는 세션 테스트는 없다. 기존 테스트(`tests/test_tool_loop.py:908`)는 불완전 JSON만 다룬다. 이 정책을 유지한다면 회귀 테스트 추가를 후속으로 고려할 수 있다.
2. **배열로 감싼 행동과 DSML 뒤 JSON**: `[{"action": ...}]`은 기존에 첫 `{`~마지막 `}` 추출로 받아들였으나 이제 거부된다. `_json_objects()`가 배열을 통째로 소비하기 때문이다. 명세가 나열한 유지 형식에는 없고 결과 파일에서도 관찰되지 않았다. DSML 호출 뒤에 JSON 행동이 오는 응답은 DSML의 args 본문이 "첫 JSON 객체"가 되어 거부된다. 이 순서도 관찰되지 않았다. 두 경우 모두 현재 근거로는 수정을 요구하지 않는다.
3. 후속 작업 후보(보고서와 같음): 여러 문장 `submit_sql`의 채점기 동작 확인.
