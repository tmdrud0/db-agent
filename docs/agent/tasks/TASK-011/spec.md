# TASK-011 작업 명세

작업 ID: TASK-011
명세 버전: 1
대상 코드 버전: 75e42aaa7f316a1504434590b49896082c5130a8
상태: planned
요약: 실행한 행동이 있는 모델 응답은 대화 기록(다음 모델 호출의 assistant 메시지)에 그 행동의 JSON만 남기고, 응답의 나머지 텍스트는 넣지 않는다.
근거: 사용자 요청(2026-10-20 조율자 채팅). task010 dev30 결과 분석에서 조율자가 제안한 개선안 3개 중 1번을 사용자가 채택했다. 관찰 내용은 아래 "조사한 기준 코드와 관찰"에 적었다.
미확정 사항: 없음. 거부된 응답의 기록 방식은 이번 범위에서 바꾸지 않는다(아래 "범위 밖"). 바꾸려면 명세 버전을 올린다.

## 목표

모델이 한 응답에 행동 JSON과 함께 지어낸 도구 결과, 추가 행동, 설명문을 쓰더라도 다음 호출의 컨텍스트에는 실제로 실행한 행동 하나와 실제 관찰만 남게 한다. 이렇게 해서 지어낸 결과가 진짜 관찰처럼 뒤따르는 추론에 쓰이는 일을 막는다.

사용자가 확인할 동작: 평가 1문제를 실행한 뒤 결과 JSON의 `adk_events[k].prompt`(k번째 호출이 받은 대화 기록)를 보면, 이전 호출들의 assistant 메시지가 모두 `{"action": "...", "args": {...}}` 형태다. 같은 호출의 원래 응답 전체는 `adk_events[k-1].content`에 그대로 남아 있다.

## 조사한 기준 코드와 관찰

코드(기준 SHA 기준):
- `agent/tool_loop.py:668`: 모델 응답을 받자마자 파싱 전에 `messages.append({"role": "assistant", "content": reply.content})`로 응답 전체를 대화 기록에 넣는다. 받아들인 응답과 거부된 응답 모두 같다.
- `agent/tool_loop.py:684` `parse_response()` 뒤에 `parsed.name`, `parsed.args`가 확정된다. 받아들인 응답의 무시 안내(`_ignored_notice`)는 관찰 끝에 붙는다(TASK-010).
- `agent/tool_loop.py:200-217` `_model_call()`: `adk_events`에 원래 응답(`content`)과 그 호출이 받은 대화 기록(`prompt`)을 저장한다. Langfuse generation은 같은 `messages`를 입력으로, 원래 응답을 출력으로 기록한다.
- `submit_sql` 경로(`_submit_action`)는 `messages`에 관찰을 직접 추가하며, phase 2로 넘어가도 같은 `messages`를 계속 쓴다.

관찰(`results/tools_dev30_calibrated_task010.json`, 조율자 집계):
- 모델이 행동 JSON 뒤에 관찰 형식(`[action N | phase 1 | ...]`)을 흉내 내 가짜 도구 결과를 쓰고 대화를 혼자 이어 간 응답이 20건이었다. 출력은 66,761토큰이다(task008 2건, task009 1건).
  - 예: robot_8 2번째 호출. 행동 16개와 "No external knowledge entries for this database." 같은 지어낸 결과가 들어 있었다.
- 받아들인 응답 중 62건이 표준 행동 JSON보다 길었다. 대화 기록에서 빠질 글자 수는 114,146자이고, 이후 호출마다 반복해서 실린 양으로 치면 약 109만 자다.
- 거부된 응답 중 긴 것은 cybermarket_4 2번째 호출 1건(64,631자, 출력 한도로 잘림)이다. 이번 범위에서는 다루지 않는다.

## 범위

- `agent/tool_loop.py`의 대화 기록 구성: 받아들인 응답의 assistant 메시지를 표준 행동 JSON으로 바꾼다.
- `tests/test_tool_loop.py`(필요하면 `tests/test_tracing.py`) 테스트 추가·갱신.

문서 산출물: docs/agent/tasks/TASK-011/implementation.md.
보존 정책: implementation.md·review.md는 로컬 작업 기록. 사용자 확인 후 spec.md·final.md를 한 번에 커밋.
보고서 외 문서 수정: 없음.

## 기록 규칙

1. **받아들인 응답**: 파싱에 성공해 행동이 확정되면, 그 호출의 assistant 메시지 내용은 `json.dumps({"action": name, "args": args}, ensure_ascii=False)` 하나다.
   - `args`는 검증을 거친 인자(`parsed.args`)다. `args` 없이 인자를 바로 쓴 응답이나 DSML로 온 행동도 이 형식으로 통일한다.
   - 키 순서는 `action`, `args`다. 구분자는 `json.dumps` 기본값으로 `SYSTEM_PROMPT`의 예시 형식과 같다.
2. **나머지 텍스트**: 설명문, 펜스, 추가 행동, 지어낸 관찰, DSML 표기는 대화 기록에 넣지 않는다. 다른 행동을 무시했다는 안내는 TASK-010대로 관찰 끝에 계속 붙인다.
3. **거부된 응답**: 지금처럼 원래 응답 전체를 assistant 메시지로 남긴다(변경 없음).
4. **응답 직후 세션 시간이 끝난 경우**(`agent/tool_loop.py:670-677`): 다음 호출이 없으므로 기록 방식은 구현자가 정한다. 다만 결과 JSON과 종료 사유는 지금과 같아야 한다.
5. **원래 응답 보존**: `adk_events[].content`, Langfuse generation 출력, `action`·`action_count`·`ignored_actions` 기록은 원래 응답 기준 그대로 둔다. `adk_events[].prompt`와 Langfuse generation 입력은 모델이 실제로 받은 대화 기록이므로, 바뀐 기록이 들어가는 것이 맞다.

## 제약

- `SYSTEM_PROMPT`, 관찰 문구(무시 안내 포함), 도구 목록과 코인 비용, 모델 행동 한도(24회), 세션 시간 제한, 중복 제출 규칙, 채점·보상 기록, 모델 요청 설정(`stop` 시퀀스 추가 등 포함)은 바꾸지 않는다. 평가 차이를 이 변경 탓으로 돌릴 수 있게 하기 위해서다.
- 행동 해석 규칙(TASK-010의 `parse_response`)은 바꾸지 않는다.
- 결과 JSON 구조는 기존 필드를 유지한다. 필요하면 필드를 추가할 수 있으나, 기록된 대화 기록에서 알 수 있는 정보를 중복해 넣을 필요는 없다.
- 테스트는 `results/`를 읽지 않고, 실제 Langfuse에 전송하지 않는다.

## 범위 밖

- 거부된 응답의 기록 방식(원문 유지, 일부만 남기기, 자리표시 문구로 바꾸기 등). task010에서는 64K자짜리 1건이 관찰됐다. 필요하면 후속 작업으로 다룬다.
- `stop` 시퀀스로 가짜 관찰 생성을 멈추는 방안, 무시된 관찰에 대한 추가 안내(분석에서 제안한 2·3번안).
- `adk_events[].prompt`가 호출마다 전체 대화 기록을 복사해 결과 JSON이 커지는 문제.

## 완료 조건

- AC-1: 행동 JSON 뒤에 지어낸 관찰(`[action 2 | ...]` 형식)과 다른 행동이 이어진 응답을 받으면, 다음 모델 호출이 받는 대화 기록의 해당 assistant 메시지가 정확히 첫 행동의 표준 JSON이다. 그 다음 user 메시지는 실제 관찰과 무시 안내다. 가짜 모델로 대화 기록을 잡아 내는 세션 단위 테스트로 확인한다.
- AC-2: 단일 JSON, 펜스로 감싼 JSON, 앞뒤 설명문이 있는 JSON, `args` 없이 인자를 바로 쓴 JSON, DSML만 있는 응답 각각에 대해 대화 기록의 assistant 메시지가 표준 JSON(`{"action": ..., "args": {...}}`)이다. 단위 또는 세션 테스트로 확인한다.
- AC-3: 거부된 응답(형식 오류, 여러 문장 `execute_sql`, 출력 한도로 잘림)은 대화 기록에 원래 응답 전체가 남는다. 거부 관찰도 기준 코드와 같다.
- AC-4: `submit_sql` 오답 뒤 재시도와 phase 2 전환 뒤 호출에서도 이전 assistant 메시지가 표준 JSON이다. 세션 테스트로 확인한다.
- AC-5: 받아들인 응답의 `adk_events[].content`와 Langfuse generation 출력은 원래 응답 그대로다. 다음 호출의 `adk_events[].prompt`에는 표준 JSON이 들어 있다. 코인 잔액, 모델 행동 수, `action`·`action_count`·`ignored_actions` 기록은 기준 코드와 같다.
- AC-6: 전체 테스트 스위트를 저장소의 기존 방식으로 실행해 통과한다. 명령과 결과를 implementation.md에 기록한다.
- AC-7: 오프라인 확인. 로컬의 `results/tools_dev30_calibrated_task010.json`에서 받아들인 응답마다 원래 응답과 표준 JSON의 길이 차이를 세어, 대화 기록에서 빠지는 글자 수를 implementation.md에 남긴다(조율자 집계 기준: 62건, 114,146자). 확인 스크립트는 커밋하지 않아도 된다.
- AC-8 (선택, 실제 평가): 사용자가 원하면 dev30 보정 평가를 실행해 task010과 비교한다. 비교 항목은 가짜 관찰이 든 응답 수, 출력 토큰, 입력 토큰(캐시 미적중·적중), 비용, 보상이다. 실행 간 편차가 크므로(세 실행 중 12개 태스크의 결과가 매번 달랐다) 보상보다 앞의 지표를 주로 기록한다. 실행하지 않으면 미검증으로 적는다.

## 조사할 사항

- assistant 메시지를 응답 직후 넣었다가 나중에 바꾸는 방식과, 파싱 뒤에 넣는 방식 가운데 어느 쪽이 거부 경로와 시간 종료 경로를 덜 건드리는지 확인한다.
- 대화 기록을 바꿔도 프롬프트 캐시 적중이 유지되는지 확인한다. 표준 JSON은 결정적이므로 이전 호출들의 접두부는 그대로 유지될 것으로 예상한다. AC-8을 실행하면 캐시 적중률로 확인한다.

## 명세 변경 이력
| 버전 | 변경 내용·이유 | 구현·리뷰에 미치는 영향 |
| --- | --- | --- |
| 1 | 최초 명세 | 대화 기록 구성, 관련 테스트 |

## 전달 정보
명세 경로·버전: docs/agent/tasks/TASK-011/spec.md, 1
기준 커밋: 75e42aaa7f316a1504434590b49896082c5130a8
작업 폴더: docs/agent/tasks/TASK-011/
커밋 제목 형식: [TASK-011] <type>: <변경 요약>
기존 미커밋 변경: 없음(이 명세 파일만 로컬에 있음. TASK-010의 implementation.md·review.md는 로컬 기록으로 남아 있음)
직접 확인 방법: 전체 테스트 스위트 실행. 평가 1문제를 실행한 뒤 결과 JSON의 `adk_events[1].prompt`에서 첫 assistant 메시지가 표준 행동 JSON이고, `adk_events[0].content`는 원래 응답인지 확인.
