# TASK-009 리뷰 보고서

작업 ID: TASK-009
명세 버전: 1
대상 코드 버전: eb4ce02f93812258fb0b05ad1797046b4384c4f0
상태: pass
요약: 캐시 적중 입력을 `input_cached_tokens`로 분리하는 규칙이 명세의 적용 조건·예외·유지 계약과 일치한다. 필수 완료 조건 AC-1~AC-4를 충족하고 수정 요청은 없다. AC-5(선택, 실제 전송)는 미검증이다.
근거: 누적 diff 검토, 대상 SHA에서 전체 테스트 재실행(164 OK), 기존 결과 JSON의 실제 usage 383건 변환 확인.
미확정 사항: 없음.

## 검토 범위
기준 커밋: a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243
검토 코드 커밋: eb4ce02f93812258fb0b05ad1797046b4384c4f0
검토 명령: git diff a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243 eb4ce02f93812258fb0b05ad1797046b4384c4f0 --
검증한 작업 공간 코드 버전: HEAD = eb4ce02f93812258fb0b05ad1797046b4384c4f0. `git diff --quiet eb4ce02... -- agent tests`가 차이 없음(exit 0). 미추적 파일은 `docs/agent/tasks/TASK-009/`뿐이며 테스트에 쓰이지 않는다.

변경 파일: `agent/tracing.py`(+22), `tests/test_tracing.py`(+119/−2). 명세 범위 밖 파일 변경 없음.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `_usage_details()`가 입력을 `prompt_tokens`에서 읽었을 때만 `_cached_input_tokens()` 값을 `input`에서 빼고 `input_cached_tokens`로 추가한다(`agent/tracing.py` diff). `UsageDetailsTests.test_cached_prompt_tokens_are_billed_separately`가 767/640/22/789 → `{"input": 127, "input_cached_tokens": 640, "output": 22, "total": 789}`를 확인하고 통과. |
| AC-2 | 충족 | `_cached_input_tokens()`는 details가 dict가 아니거나, `cached_tokens`가 bool·비정수이거나, `0..prompt_tokens` 범위를 벗어나면 0을 반환하고, 0이면 분리하지 않아 기준 결과와 같다. `input_tokens`가 먼저 매칭되면 `input_source != "prompt_tokens"`라 분리하지 않는다. `test_invalid_cached_tokens_keep_the_current_behavior`(9개 subTest), `test_the_input_tokens_path_is_not_split` 통과. |
| AC-3 | 충족 | `SuccessfulTraceTests.test_cached_prompt_tokens_are_split_in_the_generation_only`가 실제 세션 경로(TestClient → tool loop → 가짜 Langfuse)에서 generation 2개의 `usage_details` 분리 값과 `adk_events` `model_call` usage가 원본 dict와 같음을 확인. 변경은 `_usage_details()` 반환값에만 있고 `ChatReply.usage`는 변형하지 않는다(새 dict `details`만 수정). |
| AC-4 | 충족 | 리뷰어가 대상 SHA에서 `.\.venv\Scripts\python.exe -m unittest discover -s tests` 실행: 164 tests OK. |
| AC-5 | 미검증 (선택) | Langfuse Cloud 실제 전송·비용 표시는 구현자·리뷰어 모두 실행하지 않음. |

설계 선택 확인: `cached_tokens == 0`이면 `input_cached_tokens`를 생략한다. 명세가 구현자 선택으로 허용했고, 기존 `usage_details`와 동일하며 비용 차이가 없다는 이유가 보고서에 기록돼 있다. `cost_details`·가격 하드코딩·다른 상세 필드 전송 없음.

## 수정 요청
없음

## 독립 검증
| 명령·검증 | 코드 버전·조건 | 결과 |
| --- | --- | --- |
| `git diff --quiet eb4ce02f93812258fb0b05ad1797046b4384c4f0 -- agent tests` | 작업 공간 | exit 0 (대상 SHA와 일치) |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | eb4ce02f…, Python 3.12 `.venv` | 164 tests OK (13.3s) |
| `python -m unittest tests.test_tracing.UsageDetailsTests tests.test_tracing.SuccessfulTraceTests` | 같음 | 9 tests OK |
| 실제 usage 변환 확인: `results/tools_dev30_calibrated_task008.json`의 `usage` 383건에 `_usage_details()` 적용 | 같음, 로컬 결과 파일 읽기만(커밋 대상 아님) | 383건 모두 분리됨, `input + input_cached_tokens == prompt_tokens`·`output`·`total` 불일치 0건 |

실행하지 못한 사항: AC-5 실제 Langfuse 전송. Langfuse 서버가 `input_cached_tokens` 키에 사용자가 등록한 가격을 적용하는지는 서버 측 동작이라 로컬 테스트로 확인할 수 없다.

## 이전 지적 처리
해당 없음(최초 검토).

## 별도 제안·판단 요청
- 통과를 막지 않음: Langfuse 모델 가격에 `input_cached_tokens` 행이 없으면 분리 후 캐시 적중분 비용이 0으로 계산되어 오히려 과소 집계된다. 사용자가 가격 행을 등록한 뒤 평가 1문제로 AC-5를 확인하는 것을 권장한다.
