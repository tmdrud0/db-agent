# TASK-009 작업 명세

작업 ID: TASK-009
명세 버전: 1
대상 코드 버전: a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243
상태: planned
요약: Langfuse generation의 usage에서 캐시 적중 입력 토큰을 `input_cached_tokens`로 분리해, Langfuse 모델 가격 설정으로 캐시 할인을 반영한 비용이 계산되게 한다.
근거: 사용자 요청(2026-10-20 조율자 채팅). 사용자는 Langfuse Cloud에 `deepseek/deepseek-v4.1-flash` 모델 가격을 `input`·`output`·`input_cached_tokens` 세 키로 직접 등록한다(DeepSeek 공식 비피크 기준 1M당 $0.15·$0.60·$0.003). 현재 코드는 캐시 토큰을 보내지 않아 캐시 적중분도 `input` 가격으로 계산된다.
미확정 사항: 없음.

## 목표

모델 응답 usage에 캐시 적중 토큰 수가 있으면 Langfuse generation의 `usage_details`가 다음과 같이 된다.
- `input` = `prompt_tokens` − `prompt_tokens_details.cached_tokens` (캐시 미적중 입력)
- `input_cached_tokens` = `prompt_tokens_details.cached_tokens`
- `output`·`total`은 현재와 같다.

`input` + `input_cached_tokens`가 원래 입력 토큰 수와 같아 이중 계산이 없다.

사용자가 확인할 동작: 수정 후 평가를 1문제 실행하면 Langfuse의 `model_call` generation usage에 `input_cached_tokens`가 보이고, 사용자가 등록한 가격으로 계산한 비용이 그 generation에 표시된다.

## 조사한 기준 코드

- `agent/model.py:116-117`: Command Code 응답의 `model`·`usage`를 가공 없이 `ChatReply`에 담는다.
- `agent/tracing.py:50-65` `_usage_details()`: `input`(`input_tokens`/`prompt_tokens`)·`output`(`output_tokens`/`completion_tokens`)·`total`(`total_tokens`)만 만든다. 음수·bool·비정수는 버린다. 결과가 비면 `None`.
- `agent/tracing.py:197-210` `_LangfuseGeneration.finish()`: `usage_details=_usage_details(reply.usage)`로 전달한다.
- `agent/tool_loop.py:324`: `adk_events`의 `model_call`에 원본 `usage`를 그대로 저장한다(결과 JSON).
- `tests/test_tracing.py:52, 278`: 가짜 usage `{"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}`와 기대값 `{"input": 11, "output": 3, "total": 14}`.
- 실제 응답 형태(`results/tools_dev30_calibrated_task008.json`의 모델 호출 383건, 조율자 확인): 모든 응답에 `prompt_tokens`·`completion_tokens`·`total_tokens`·`prompt_tokens_details`·`completion_tokens_details`가 있다. `prompt_tokens_details.cached_tokens`에 캐시 적중 수가 오고 `cache_write_tokens`·`cache_creation_input_tokens`·`cache_read_input_tokens`는 모두 0이었다. 예: `{"prompt_tokens": 767, ..., "prompt_tokens_details": {"cached_tokens": 640, ...}}`. 전체 입력의 91%가 캐시 적중이었다.

## 범위

- `agent/tracing.py`의 `_usage_details()`(또는 그 호출부)에서 캐시 적중 토큰을 분리한다.
- `tests/test_tracing.py`에 분리 규칙과 경계 사례 테스트를 추가·갱신한다.

문서 산출물: docs/agent/tasks/TASK-009/implementation.md.
보존 정책: implementation.md·review.md는 로컬 작업 기록. 사용자 확인 후 spec.md·final.md를 한 번에 커밋.
보고서 외 문서 수정: 없음.

## 제약

- 분리 규칙은 입력 수가 `prompt_tokens`에서 왔고 `prompt_tokens_details`가 dict이며 `cached_tokens`가 0 이상 `prompt_tokens` 이하의 정수(bool 제외)일 때만 적용한다. 그 외(필드 없음, 비정수, 음수, `prompt_tokens` 초과, `input_tokens` 경로)는 현재 동작과 같은 결과를 낸다.
- `cached_tokens`가 0이면 `input_cached_tokens: 0`을 함께 보내도 되고 생략해도 된다. 구현자가 정하고 보고서에 기록한다.
- 다른 캐시·상세 필드(`cache_write_tokens`, `cache_read_input_tokens`, `reasoning_tokens`, 오디오·이미지 등)는 보내지 않는다. 추론 토큰은 이미 `completion_tokens`에 포함된다.
- `total`은 계속 `total_tokens` 그대로 보낸다.
- 결과 JSON의 원본 `usage`(`adk_events`), 에이전트 동작, 모델 요청, 채점, Langfuse 키가 없을 때의 no-op 동작은 바꾸지 않는다.
- Langfuse 모델 가격 등록은 사용자가 UI에서 직접 하며 이 작업 범위가 아니다. 코드에 가격을 넣거나 `cost_details`를 계산해 보내지 않는다.
- 테스트는 실제 Langfuse에 전송하지 않는다(TASK-008의 테스트 격리 유지).

## 완료 조건

- AC-1: `prompt_tokens=767`, `cached_tokens=640`, `completion_tokens=22`, `total_tokens=789`인 usage가 `{"input": 127, "input_cached_tokens": 640, "output": 22, "total": 789}`로 변환된다. 단위 테스트로 확인한다.
- AC-2: `prompt_tokens_details`가 없거나 `cached_tokens`가 비정수·bool·음수·`prompt_tokens` 초과인 경우, `input_tokens` 경로인 경우 결과가 기준 코드와 같다. 단위 테스트로 확인한다.
- AC-3: 에이전트 세션 trace 테스트(`tests/test_tracing.py`의 generation 검증)에서 캐시 필드가 있는 usage를 쓰면 generation `usage_details`에 분리된 값이 들어가고, 결과 JSON의 `adk_events` usage는 원본 그대로다.
- AC-4: 전체 테스트 스위트(`python -m unittest discover -s tests` 또는 저장소의 기존 방식)가 통과한다. 명령과 결과를 implementation.md에 기록한다.
- AC-5 (선택, 실제 전송): 사용자가 원하면 `.env` 키가 있는 상태에서 평가 1문제를 실행해 Langfuse generation에 `input_cached_tokens`가 보이는지 확인한다. 실행하지 않으면 미검증으로 기록한다.

## 조사할 사항

- `_usage_details()`의 반환값을 사용하는 다른 곳이 있는지 확인한다.
- 0인 `cached_tokens` 처리 방식을 정하고 이유를 기록한다.

## 명세 변경 이력
| 버전 | 변경 내용·이유 | 구현·리뷰에 미치는 영향 |
| --- | --- | --- |
| 1 | 최초 명세 | `agent/tracing.py`, `tests/test_tracing.py` |

## 전달 정보
명세 경로·버전: docs/agent/tasks/TASK-009/spec.md, 1
기준 커밋: a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243
작업 폴더: docs/agent/tasks/TASK-009/
커밋 제목 형식: [TASK-009] <type>: <변경 요약>
기존 미커밋 변경: 없음 (이 명세 파일만 로컬에 있음)
직접 확인 방법: 테스트 통과 확인 후, 사용자가 Langfuse 모델 가격에 `input_cached_tokens`를 등록하고 평가 1문제를 실행해 `model_call` generation의 usage와 비용을 본다.
