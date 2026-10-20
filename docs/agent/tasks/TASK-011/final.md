# TASK-011 최종 보고서

작업 ID: TASK-011
명세 버전: 1
기준 코드 버전: 75e42aaa7f316a1504434590b49896082c5130a8
대상 코드 버전: 2693210401691c14f1ca57891cfb2d5539ee26ce
상태: ready_for_user
요약: 받아들인 모델 응답은 다음 호출의 대화 기록에 실행한 행동의 표준 JSON(`{"action": ..., "args": {...}}`)만 남긴다. 거부된 응답은 지금처럼 원문을 남기고, 원래 응답은 `adk_events[].content`와 Langfuse generation 출력에 그대로 보존한다. 필수 완료 조건 AC-1~AC-7을 충족했고, 같은 명세 버전과 코드 SHA에 대한 리뷰 판정은 `pass`다. 선택 항목 AC-8(실제 평가)은 실행하지 않았다.
근거: 아래에 코드 위치, 테스트, 결과 파일 집계, 리뷰 결론을 직접 기록했다. 대상 SHA에서 전체 테스트 182개가 통과했다.
미확정 사항: 없음.

## 변경 내용과 설계 결정

작업 코드 커밋: 2693210401691c14f1ca57891cfb2d5539ee26ce `[TASK-011] feat: keep only the executed action in the assistant history`
변경 파일: `agent/tool_loop.py`(+12), `tests/test_tool_loop.py`(+126)

**대화 기록 교체** (`agent/tool_loop.py`)
- `_action_history(name, args)`가 `{"role": "assistant", "content": json.dumps({"action": name, "args": args}, ensure_ascii=False)}`를 만든다. `args`는 검증을 거친 인자(`parsed.args`)라서, `args` 없이 인자를 바로 쓴 응답과 DSML 응답도 같은 형식으로 통일된다.
- 방식: 응답을 받으면 기존처럼 원문을 `messages`에 먼저 추가한다(`:677`). `parse_response()`가 성공하면 그 메시지(`messages[-1]`)를 표준 JSON으로 바꾼다(`:708`).
  - 이 방식을 고른 이유: 거부 경로와 응답 직후 시간 종료 경로(`:679-686`)의 코드를 건드리지 않아 기존 동작(원문 유지)이 그대로 남는다.
  - 응답 직후 시간 종료 경로는 다음 호출이 없으므로 원문을 그대로 두었다(명세 기록 규칙 4의 구현자 재량). 결과 JSON과 종료 사유는 기존과 같다.
- 교체는 행동 실행 전에 일어난다. 그래서 일반 관찰과 `submit_sql`의 오답·중복·phase 전환 관찰 모두 표준 JSON 뒤에 붙고, phase 2에서도 같은 `messages`를 쓰므로 그대로 적용된다.
- 이전 호출 기록은 바뀌지 않는다. `adk_events[].prompt`와 Langfuse generation 입력은 호출 시점에 메시지를 복사한다. 교체도 기존 dict를 수정하지 않고 새 dict를 넣는다.
- 원래 응답은 `adk_events[].content`와 generation 출력(`reply.content`)에 그대로 남는다. `action`·`action_count`·`ignored_actions` 기록도 바뀌지 않았다.

**바꾸지 않은 것**: `SYSTEM_PROMPT`, 관찰 문구(무시 안내 포함), 도구 목록과 코인 비용, `MAX_MODEL_ACTIONS = 24`, 세션 시간 제한, 중복 제출 규칙, 채점·보상 기록, 모델 요청 설정, `parse_response()`. diff에는 위 두 파일만 있다.

## 설정과 실행 방법
새 설정은 없다. 테스트는 저장소의 기존 방식으로 실행한다.

```
.venv\Scripts\python.exe -m unittest discover -s tests
```

## 완료 조건별 결과
| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `AssistantHistoryTests.test_fabricated_observations_and_extra_actions_never_enter_the_history`: 행동 JSON + 가짜 관찰 `[action 2 | ...]` + 두 번째 행동이 든 응답 뒤, 다음 호출의 assistant 메시지가 정확히 `{"action": "get_schema", "args": {}}`다. 이어지는 user 메시지에는 실제 schema 관찰과 `contained 2 actions` 안내가 들어 있다. |
| AC-2 | 충족 | `test_every_accepted_shape_becomes_the_standard_json`: 단일 JSON, 펜스, 앞뒤 설명문, `args` 없는 인자, DSML만 있는 응답 다섯 가지 모두 표준 JSON이 된다. |
| AC-3 | 충족 | `test_rejected_responses_stay_in_the_history_verbatim`: 형식 오류, 여러 문장 `execute_sql`, 출력 한도 잘림 세 경우 모두 원문이 남고 `action=None`, `action_error`가 기록된다. 거부 관찰 코드는 바뀌지 않았다. |
| AC-4 | 충족 | `test_retries_and_phase_two_keep_the_standard_json_in_the_history`: `submit_sql` 오답 뒤 재시도, phase 2 전환 뒤 호출에서 직전 assistant 메시지가 표준 JSON이다. 보상 1.0, `stop_reason=phase2_completed`, 잔액 8. |
| AC-5 | 충족 | AC-1 테스트가 원문 `content`, `action`·`action_count=2`·`ignored_actions=1`, 잔액 14, `adk_events[1].prompt`가 실제 요청 messages와 같음을 확인한다. generation 입력·출력 계약은 기존 `tests/test_tracing.py` 테스트가 그대로 통과한다. |
| AC-6 | 충족 | 전체 테스트 182개 통과(기존 178 + 신규 4). |
| AC-7 | 충족 | `results/tools_dev30_calibrated_task010.json`에서 받아들인 응답 382건 중 62건이 표준 JSON보다 길고, 대화 기록에서 빠지는 글자 수는 114,146자다. 구현자와 리뷰어가 각각 집계했고 조율자 집계와 일치한다. |
| AC-8 | 미검증(선택) | 실제 dev30 보정 평가를 실행하지 않았다. |

## 검증 결과
| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` (구현자) | 2693210401691c14f1ca57891cfb2d5539ee26ce, Windows | exit 0, 182 tests OK | AC-1~AC-6 |
| `.venv/Scripts/python.exe -m unittest discover -s tests` (리뷰어) | 같은 SHA, `git diff HEAD -- agent tests` 비어 있음 | 182 tests OK | 독립 재실행 |
| 결과 파일 집계 스크립트(미커밋, 구현자·리뷰어 각각) | `results/tools_dev30_calibrated_task010.json` | 382 / 62 / 114,146자 | AC-7 |

검증 코드와 최종 SHA 일치 근거: HEAD가 2693210401691c14f1ca57891cfb2d5539ee26ce이고 추적 코드·테스트에 변경이 없다. 미추적 파일은 `docs/agent/tasks/TASK-011/`의 문서뿐이며 테스트에 쓰이지 않는다.
검증 범위: 가짜 모델·환경을 쓰는 계약 테스트와 기존 결과 파일의 오프라인 집계다. 실제 모델·공식 DB 채점·Langfuse 서버는 사용하지 않았다.

## 리뷰 처리 결과
검토 명세 버전: 1
검토한 코드 SHA: 2693210401691c14f1ca57891cfb2d5539ee26ce
리뷰 판정: pass
판정 근거·독립 검증: 리뷰어가 누적 diff와 `_model_call`·tracing의 메시지 복사 방식을 확인했다. 그 결과 교체가 이전 호출의 `prompt`와 generation 입력을 바꾸지 않고, 원문은 교체 전까지 어떤 모델 요청에도 실리지 않음을 확인했다. 대상 SHA에서 전체 테스트(182 OK)와 AC-7 집계를 직접 다시 실행했다.
지적 처리: 수정 요청 없음. 통과를 막지 않는 제안 2건은 아래 "남은 제한"에 적었다.

## 남은 제한
- AC-8(선택) 미실행. 가짜 관찰이 든 응답 수, 출력·입력 토큰, 비용, 보상의 실제 변화는 확인하지 않았다.
- 프롬프트 캐시 적중률은 실제 평가로 확인하지 않았다. 표준 JSON은 결정적이고 원문이 요청에 실린 적이 없으므로 이전 호출들의 접두부는 유지될 것으로 본다.
- 테스트 보강 제안(리뷰어, 필수 아님)
  - 테스트 기대값이 `ensure_ascii` 기본값으로 만들어져, 비ASCII 인자에서 `ensure_ascii=False` 형식을 고정하지 못한다.
  - "완전한 행동이 있는데 잘린 응답"의 거부 경로에는 테스트가 없다(TASK-010부터). 코드상 파싱 전에 거부되므로 원문이 남는다.
- 범위 밖으로 남긴 것: 거부된 긴 응답의 기록 방식(task010에서 64,631자 1건), `stop` 시퀀스로 가짜 관찰 생성을 막는 방안, `adk_events[].prompt` 중복으로 결과 JSON이 커지는 문제.

필수 완료 조건과 유지할 계약에는 영향이 없다.

## 필요한 보완·판단
없음. 원하면 AC-8 실제 평가를 실행해 task010과 비교할 수 있다.

## 직접 확인 방법
1. 전체 테스트를 실행한다. 기대 결과: `Ran 182 tests`, `OK`.
2. 평가 1문제를 실행한다. 결과 JSON의 `adk_events[1].prompt`에서 assistant 메시지가 `{"action": "...", "args": {...}}` 형태이고, `adk_events[0].content`는 원래 응답 전체인지 확인한다.

## 사용자 결정
결정: 승인
대상: 명세 버전 1, 최종 코드 2693210401691c14f1ca57891cfb2d5539ee26ce
확인 근거: 사용자가 2026-10-20 조율자 채팅에서 최종 보고서 설명을 들은 뒤 "커밋 가자"라고 승인함. AC-8(선택)은 실행하지 않은 상태로 승인함.
보존 문서: spec.md, final.md
