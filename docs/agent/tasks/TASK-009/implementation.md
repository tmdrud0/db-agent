# TASK-009 구현 보고서

작업 ID: TASK-009
명세 버전: 1
대상 코드 버전: eb4ce02f93812258fb0b05ad1797046b4384c4f0
상태: ready_for_review
요약: 모델 usage의 캐시 적중 입력 토큰을 Langfuse generation의 `input_cached_tokens`로 분리하고 `input`은 비캐시 입력만 남긴다. 결과 JSON의 `adk_events` usage는 원본 그대로 유지한다.
근거: `agent/tracing.py:50-88`(분리 규칙), `agent/tracing.py:224`(generation 전달), `tests/test_tracing.py:321-361`(세션 trace), `tests/test_tracing.py:580-650`(단위 경계 사례).
미확정 사항: 없음.

## 커밋과 검토 범위
기준 커밋: a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243
코드 커밋: eb4ce02f93812258fb0b05ad1797046b4384c4f0
작업 커밋 목록: eb4ce02f93812258fb0b05ad1797046b4384c4f0
검토 명령: `git diff a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243 eb4ce02f93812258fb0b05ad1797046b4384c4f0 --`
현재 코드와 대상 SHA의 차이: 없음. `git diff eb4ce02f93812258fb0b05ad1797046b4384c4f0 -- agent/tracing.py tests/test_tracing.py` 출력이 비어 있다.
기존 사용자 변경: 없음. `docs/agent/tasks/TASK-009/`의 `spec.md`·이 보고서는 untracked 로컬 작업 기록으로 커밋에서 제외했다.

## 변경 내용
- `agent/tracing.py:50-59` `_cached_input_tokens()`를 추가했다. `prompt_tokens_details`가 dict이고 `cached_tokens`가 bool이 아닌 정수이며 `0 <= cached_tokens <= prompt_tokens`일 때만 값을 돌려주고, 그 외에는 0이다.
- `agent/tracing.py:62-88` `_usage_details()`가 입력 토큰을 실제로 읽은 필드를 기억한다. 입력이 `prompt_tokens`에서 왔을 때만 `input`에서 캐시 수를 빼고 `input_cached_tokens`를 추가한다. `input_tokens` 경로는 분리하지 않는다.
- `cached_tokens == 0`이면 `input_cached_tokens`를 생략한다. 기존 `usage_details`와 동일한 결과를 유지하고, 캐시 할인 대상이 0이라 가격 계산에도 차이가 없기 때문이다.
- `output`·`total`은 기존과 같이 `output_tokens`/`completion_tokens`, `total_tokens`에서만 만든다. 다른 상세 필드와 `cost_details`는 보내지 않는다.
- `_usage_details()`의 다른 사용처를 확인했다. 호출은 `_LangfuseGeneration.finish()`의 `agent/tracing.py:224` 한 곳뿐이다.
- `tests/test_tracing.py`에 단위 경계 사례와 세션 trace 테스트를 추가하고, `model_reply()`가 usage를 주입받을 수 있게 확장했다.

## 완료 조건별 근거
- AC-1: `UsageDetailsTests.test_cached_prompt_tokens_are_billed_separately`(`tests/test_tracing.py:581`)가 767/640/22/789 usage를 `{"input": 127, "input_cached_tokens": 640, "output": 22, "total": 789}`로 확인한다.
- AC-2: `test_invalid_cached_tokens_keep_the_current_behavior`(`tests/test_tracing.py:617`)가 상세 필드 없음, dict 아님, bool, 실수, 문자열, 음수, `prompt_tokens` 초과, None을 확인하고, `test_the_input_tokens_path_is_not_split`(`tests/test_tracing.py:641`)이 `input_tokens` 경로가 기준 동작과 같음을 확인한다. `test_zero_cached_tokens_keeps_the_existing_details`(`tests/test_tracing.py:605`)는 0일 때 생략 규칙을 고정한다.
- AC-3: `SuccessfulTraceTests.test_cached_prompt_tokens_are_split_in_the_generation_only`(`tests/test_tracing.py:321`)가 모델 호출 2회의 generation `usage_details`에 분리 값이 들어가고 응답 JSON `state.adk_events`의 `model_call` usage가 원본 dict와 같은지 확인한다.
- AC-4: 전체 스위트 164개 테스트 통과. 아래 표에 명령과 결과를 기록했다.
- AC-5: 실행하지 않았다. 아래 미검증 항목에 적었다.

## 실행한 검증
| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `git status --short`, `git rev-parse HEAD` | 작업 시작 시 | HEAD `a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243`, untracked `docs/agent/tasks/TASK-009/`만 존재 | 명세 기준 SHA와 일치, 기존 사용자 변경 없음 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_tracing.py" -v` | 작업 트리 변경(커밋과 동일 내용), Python 3.12 `.venv` | exit 0, 29 tests OK | tracing 계약·새 캐시 분리 테스트 통과 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | 위와 같은 코드 내용, Python 3.12 `.venv` | exit 0, 164 tests OK | 전체 계약 스위트 통과 |
| `git diff --check` | 커밋 전 변경 | 출력 없음 | 공백·줄바꿈 오류 없음 |
| `git diff eb4ce02f93812258fb0b05ad1797046b4384c4f0 -- agent/tracing.py tests/test_tracing.py` | 커밋 후 | 출력 없음 | 검증한 코드 내용과 대상 SHA 일치 |

검증 코드와 최종 SHA 일치 근거: 테스트는 커밋 전 작업 트리에서 실행했고, 이후 `agent/tracing.py`·`tests/test_tracing.py`를 수정하지 않고 그대로 stage·커밋했다. 커밋 후 대상 SHA와 작업 트리의 두 파일 diff가 비어 있음을 확인했다. 테스트 실행에 사용한 미추적 파일이나 제외한 사용자 변경은 없으며, `docs/agent/tasks/TASK-009/`의 문서는 테스트에 쓰이지 않았다.

## 미검증 항목과 제한
- AC-5(선택, 실제 전송): Langfuse Cloud로 실제 generation을 보내 `input_cached_tokens` 표시와 사용자가 등록한 가격 기반 비용을 확인하지 않았다. 로컬 계약 테스트는 가짜 client로 `usage_details` 전달까지만 확인한다.
- 실제 모델 응답에서 온 `usage` 형태는 명세에 기록된 기존 결과 JSON 근거를 따랐고, 이번 작업에서 새로 수집하지 않았다.

## 리뷰 지적 처리
해당 없음(최초 구현).

## 필요한 판단
없음.
