# TASK-011 리뷰 보고서

작업 ID: TASK-011
명세 버전: 1
대상 코드 버전: 2693210401691c14f1ca57891cfb2d5539ee26ce
상태: pass
요약: 받아들인 응답의 assistant 메시지만 표준 행동 JSON으로 바꾸고, 거부·시간 종료 경로와 원래 응답 기록(`adk_events[].content`, generation 출력)은 그대로 둔다. AC-1~AC-7을 충족하며 수정을 요구할 결함은 없다.
근거: 누적 diff 검토(`agent/tool_loop.py` +12, `tests/test_tool_loop.py` +126), `_model_call`·tracing의 메시지 복사 방식 확인, 대상 SHA에서 전체 테스트 실행(182 OK), AC-7 재집계.
미확정 사항: 없음.

## 검토 범위
기준 커밋: 75e42aaa7f316a1504434590b49896082c5130a8
검토 코드 커밋: 2693210401691c14f1ca57891cfb2d5539ee26ce
검토 명령: git diff 75e42aaa7f316a1504434590b49896082c5130a8 2693210401691c14f1ca57891cfb2d5539ee26ce --
검증한 작업 공간 코드 버전: HEAD = 2693210401691c14f1ca57891cfb2d5539ee26ce. `git diff HEAD -- agent tests` 출력 없음. 미추적 파일은 `docs/agent/tasks/TASK-011/`뿐이다.

## 구현 확인
- `agent/tool_loop.py:677`은 기존처럼 응답 원문을 먼저 추가한다. `parse_response()`가 성공한 뒤에만 `:708`에서 `messages[-1]`을 `_action_history(name, args)`로 교체한다. 그래서 거부 경로(`continue`)와 응답 직후 시간 종료 경로(`:679-686`)는 코드 변경 없이 원문을 유지한다.
- 교체는 기존 dict를 수정하지 않고 새 dict를 넣는 방식이다. `adk_events[].prompt`(`[dict(m) for m in messages]`)와 Langfuse generation 입력(`tracing.py:396`, 같은 방식으로 복사)은 호출 시점에 복사되므로, 이미 기록된 이전 호출의 프롬프트는 바뀌지 않는다.
- `generation.finish(reply, ...)`는 계속 `reply.content`를 출력으로 쓰고, `event["content"]`도 원문 그대로다.
- 교체 시점은 행동 실행 전이다. 그래서 `_submit_action`의 오답·중복·phase 전환 관찰과 일반 관찰 모두 표준 JSON 뒤에 붙는다. phase 2도 같은 `messages`를 쓰므로 그대로 적용된다.
- 원문은 교체 전까지 어떤 모델 요청에도 실리지 않는다. 따라서 대화 기록의 접두부는 호출마다 결정적으로 유지된다(캐시 관점에서 문제 없음, 실제 적중률은 미확인).
- `SYSTEM_PROMPT`, 관찰 문구, 도구·비용, 행동 한도, 시간 제한, 중복 제출 규칙, 채점·보상, 모델 요청 설정, `parse_response()`는 diff에 없다.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `test_fabricated_observations_and_extra_actions_never_enter_the_history`: 가짜 관찰과 두 번째 행동이 섞인 응답 뒤, 다음 호출의 assistant 메시지가 정확히 표준 JSON이다. 이어지는 user 메시지에 실제 schema 관찰, `[action 1 ... budget 17]`, `contained 2 actions` 안내가 들어 있음을 확인한다. |
| AC-2 | 충족 | `test_every_accepted_shape_becomes_the_standard_json`: 단일·펜스·설명문·`args` 없는 인자·DSML 다섯 형식을 subTest로 확인한다. |
| AC-3 | 충족 | `test_rejected_responses_stay_in_the_history_verbatim`: 형식 오류, 여러 문장 `execute_sql`, `finish_reason=length` 세 경우 모두 원문이 남고 `action=None`, `action_error`가 기록된다. 거부 관찰 코드는 diff에서 바뀌지 않았다. |
| AC-4 | 충족 | `test_retries_and_phase_two_keep_the_standard_json_in_the_history`: 오답 재시도, phase 1 통과 후 phase 2 호출에서 직전 assistant 메시지가 표준 JSON이다. 보상 1.0, `phase2_completed`, 잔액 8. |
| AC-5 | 충족 | AC-1 테스트가 원문 `content`, `action`·`action_count`·`ignored_actions`, 잔액 14, `adk_events[1].prompt == 실제 요청 messages`를 확인한다. generation 입력·출력 계약은 기존 `tests/test_tracing.py` 테스트가 그대로 통과하고, 코드상 출력은 `reply.content`다. |
| AC-6 | 충족 | 리뷰어 실행: `Ran 182 tests`, `OK`. |
| AC-7 | 충족 | 리뷰어가 별도 스크립트로 재집계: 받아들인 응답 382건 중 62건이 더 길고, 차이 합계 114,146자. 보고서·조율자 집계와 일치한다. |
| AC-8 | 미검증(선택) | 실제 평가 미실행. 명세상 선택 항목이다. |

## 수정 요청
없음.

## 독립 검증
- `.venv/Scripts/python.exe -m unittest discover -s tests` (Windows, HEAD = 대상 SHA, 추적 파일 변경 없음): `Ran 182 tests`, `OK`.
- AC-7 재집계: `results/tools_dev30_calibrated_task010.json`의 `action`이 있는 `model_call` 382건에서 `len(content) - len(json.dumps({"action", "args"}, ensure_ascii=False))`가 양수인 62건, 합계 114,146자(미커밋 스크래치 스크립트).
- 실행하지 못한 것: AC-8 실제 평가, 실제 Langfuse 서버의 표시, 프롬프트 캐시 적중률.

## 이전 지적 처리
최초 검토이므로 해당 없음.

## 별도 제안·판단 요청
통과를 막지 않는 사항이다.

1. 테스트의 `canonical()`은 `ensure_ascii` 기본값(True)으로 기대값을 만든다. 현재 사례가 모두 ASCII라 구현의 `ensure_ascii=False`와 차이가 드러나지 않는다. 한글 등 비ASCII 인자(예: `ask_user` 질문) 사례를 하나 넣으면 명세 기록 규칙 1의 형식을 직접 고정할 수 있다.
2. AC-3의 잘림 사례는 불완전한 JSON이다. TASK-010 리뷰에서 남긴 "완전한 행동 + 잘림 → 거부" 경로는 이번에도 테스트가 없다. 코드상 파싱 전에 거부되므로 원문이 남는 것은 확실하다.
