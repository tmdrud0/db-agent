# TASK-011 구현 보고서

작업 ID: TASK-011
명세 버전: 1
대상 코드 버전: 2693210401691c14f1ca57891cfb2d5539ee26ce
상태: ready_for_review
요약: 파싱에 성공한 모델 응답은 대화 기록에서 실행한 첫 행동의 표준 JSON(`{"action": ..., "args": ...}`)으로 대체된다. 원래 응답 전체는 `adk_events[].content`와 Langfuse generation 출력에 그대로 남는다.
근거: `agent/tool_loop.py:137` `_action_history()`, `agent/tool_loop.py:708` 기록 대체, `tests/test_tool_loop.py:1242` `AssistantHistoryTests`.
미확정 사항: 없음. 응답 직후 세션 시간이 끝난 경로는 기존처럼 원래 응답을 기록한다(명세 기록 규칙 4의 구현자 재량).

## 커밋과 검토 범위
기준 커밋: 75e42aaa7f316a1504434590b49896082c5130a8
코드 커밋: 2693210401691c14f1ca57891cfb2d5539ee26ce
작업 커밋 목록:
- 2693210401691c14f1ca57891cfb2d5539ee26ce — `[TASK-011] feat: keep only the executed action in the assistant history`
검토 명령: git diff 75e42aaa7f316a1504434590b49896082c5130a8 2693210401691c14f1ca57891cfb2d5539ee26ce --
현재 코드와 대상 SHA의 차이: 없음. 커밋 후 `git diff HEAD -- agent tests`가 비어 있고, 추적 파일은 `agent/tool_loop.py`·`tests/test_tool_loop.py` 두 개만 바뀌었다.
기존 사용자 변경: 없음. 작업 폴더 `docs/agent/tasks/TASK-011/`(spec.md, 이 보고서)만 미추적 로컬 기록이다.

## 변경 내용
- `agent/tool_loop.py`: `_action_history(name, args)` 헬퍼를 추가해 assistant 메시지를 `json.dumps({"action": name, "args": args}, ensure_ascii=False)`로 만든다. `parse_response()` 성공 뒤 `name`, `args`가 확정되면 직전에 추가한 원문 assistant 메시지(`messages[-1]`)를 이 표준 JSON으로 교체한다.
- 응답을 받자마자 원문을 추가하고 파싱 뒤 교체하는 방식을 택했다. 거부 경로와 응답 후 시간 종료 경로는 추가·기록 코드를 건드리지 않아 기존 동작(원문 유지)이 그대로 유지된다.
- 모델 행동/예산/채점/관찰 문구/`SYSTEM_PROMPT`/`parse_response()`는 변경하지 않았다. 원래 응답은 `adk_events[].content`, generation 출력, `action`·`action_count`·`ignored_actions` 기록에 그대로 남고, 다음 호출의 `adk_events[].prompt`와 generation 입력에는 바뀐 대화 기록이 들어간다.
- `tests/test_tool_loop.py`: `AssistantHistoryTests` 4개 테스트를 추가했다.

## 완료 조건별 근거
- AC-1: `AssistantHistoryTests.test_fabricated_observations_and_extra_actions_never_enter_the_history` — 행동 JSON + 가짜 관찰(`[action 2 | ...]`) + 두 번째 행동 응답을 받은 뒤 다음 호출 기록의 assistant 메시지가 정확히 `{"action": "get_schema", "args": {}}`이고, user 메시지는 실제 schema 관찰과 `contained 2 actions` 안내임을 확인했다.
- AC-2: `AssistantHistoryTests.test_every_accepted_shape_becomes_the_standard_json` — 단일 JSON, 펜스, 앞뒤 설명문, `args` 없는 인자, DSML 전용 응답 각각에서 assistant 메시지와 `adk_events[].action`이 표준 JSON/인자로 정규화됨을 확인했다.
- AC-3: `AssistantHistoryTests.test_rejected_responses_stay_in_the_history_verbatim` — 형식 오류, 여러 문장 `execute_sql`, 출력 한도(`finish_reason=length`) 응답 각각에서 다음 호출의 assistant 메시지가 원문 그대로이고 `action`이 `None`, `action_error`가 기록됨을 확인했다.
- AC-4: `AssistantHistoryTests.test_retries_and_phase_two_keep_the_standard_json_in_the_history` — `submit_sql` 오답 재시도, phase 1 통과 후 phase 2 전환, phase 2 호출까지 이전 assistant 메시지가 모두 표준 JSON임을 확인했다. 총 보상 1.0, `stop_reason=phase2_completed`, 잔액 8도 확인했다.
- AC-5: AC-1 테스트에서 원문 `content`, `action`/`action_count`/`ignored_actions`, 잔액 14, 다음 호출 `prompt` 일치를 확인했다. Langfuse generation 입력/출력 계약은 기존 `tests/test_tracing.py`의 `test_one_session_records_model_calls_tool_spans_scores_and_phase_two`(generation input = 실제 요청 messages, output = 원문 응답)가 그대로 통과한다.
- AC-6: 전체 테스트 스위트 182개 통과(아래 표).
- AC-7: `results/tools_dev30_calibrated_task010.json`을 스크립트로 집계했다. 받아들인 응답 382건 중 표준 JSON보다 긴 응답 62건, 양수 길이 차이 합계 114,146자(전체 순차이 114,114자)로 조율자 집계와 일치했다. 스크립트는 커밋하지 않았다.
- AC-8: 실행하지 않았다. 실제 dev30 평가와 task010 비교는 미검증이다.

## 실행한 검증
| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest tests.test_tool_loop.AssistantHistoryTests -v` | 작업 트리(커밋 전), 새 테스트 4개 | exit 0, 4 tests OK | AC-1~AC-4 계약 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | 작업 트리(커밋 전) | exit 0, 182 tests OK | 기존 178개 + 새 4개 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 커밋 2693210, 추적 변경 없음 | exit 0, 182 tests OK | 최종 코드 SHA 재검증 |
| `git diff --check` | 작업 트리(커밋 전) | exit 0 | 공백 오류 없음 |
| 결과 JSON 집계 스크립트(비커밋) | `results/tools_dev30_calibrated_task010.json`, 코드 변경과 무관한 기존 결과 | 382 accepted / 62 longer / 114,146 chars | AC-7, 조율자 집계와 일치 |

검증 코드와 최종 SHA 일치 근거: 테스트는 커밋 전 변경된 두 추적 파일 내용으로 실행했고, 커밋 후 같은 SHA에서 전체 스위트를 다시 실행해 182개 통과를 확인했다. 커밋 후 `git diff HEAD -- agent tests`가 비어 있으며, 미추적 `docs/agent/tasks/TASK-011/`은 테스트 실행에 쓰이지 않는다. 실제 Langfuse·공식 DB·모델에는 전송하지 않았다.

## 미검증 항목과 제한
- AC-8(선택): 실제 dev30 보정 평가를 실행하지 않아 가짜 관찰 응답 수, 출력·입력 토큰, 비용, 보상 변화는 확인하지 않았다.
- 프롬프트 캐시 적중률은 실제 평가를 하지 않아 확인하지 않았다. 표준 JSON은 결정적이므로 이전 호출 접두부는 호출마다 동일하게 유지된다.
- 응답 직후 세션 시간이 끝난 경로는 다음 호출이 없어 기록 방식을 바꾸지 않았다. 결과 JSON 필드와 종료 사유는 기존과 같다.

## 리뷰 지적 처리
해당 없음(최초 구현).

## 필요한 판단
없음.
