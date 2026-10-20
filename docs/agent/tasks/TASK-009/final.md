# TASK-009 최종 보고서

작업 ID: TASK-009
명세 버전: 1
기준 코드 버전: a82d1db3354c3b0afb2b0052f0c7d58d6f8e6243
대상 코드 버전: eb4ce02f93812258fb0b05ad1797046b4384c4f0
상태: ready_for_user
요약: Langfuse generation의 `usage_details`에서 캐시 적중 입력 토큰을 `input_cached_tokens`로 분리하고 `input`에는 캐시 미적중 입력만 남긴다. 필수 완료 조건 AC-1~AC-4를 충족했고 리뷰 판정은 `pass`다. 선택 조건 AC-5(실제 Langfuse 전송)는 미검증이다.
근거: 아래 코드 위치·검증 결과·리뷰 결론을 직접 기록했다.
미확정 사항: 없음.

## 변경 내용과 설계 결정
- `agent/tracing.py` `_cached_input_tokens()` 추가: `prompt_tokens_details`가 dict이고 `cached_tokens`가 bool이 아닌 정수이며 `0 <= cached_tokens <= prompt_tokens`일 때만 그 값을, 그 외에는 0을 돌려준다.
- `agent/tracing.py` `_usage_details()`: 입력 수를 읽은 필드를 기억하고, `prompt_tokens`에서 읽었으며 캐시 수가 0보다 클 때만 `input -= cached`, `input_cached_tokens = cached`를 추가한다. `input_tokens` 경로는 분리하지 않는다. `output`·`total`은 기존과 같다.
- 설계 선택: `cached_tokens == 0`이면 `input_cached_tokens`를 생략한다(명세가 구현자 선택으로 허용). 기존 `usage_details`와 동일하고 비용 차이가 없다.
- 유지한 계약: 결과 JSON `adk_events`의 원본 `usage`는 변형하지 않는다(새 dict만 수정). 가격·`cost_details`는 코드에 넣지 않고 Langfuse 모델 설정에 맡긴다. 에이전트 동작·모델 요청·채점·키 없을 때의 no-op 동작은 변경 없음.
- `_usage_details()` 호출처는 `_LangfuseGeneration.finish()` 한 곳뿐이다.
- `tests/test_tracing.py`: `model_reply()`에 usage 주입 인자 추가, `UsageDetailsTests`(분리·전량 캐시·0·잘못된 값 9종·`input_tokens` 경로) 및 세션 trace 테스트 추가.

작업 코드 커밋: eb4ce02f93812258fb0b05ad1797046b4384c4f0 (`[TASK-009] feat: split cached prompt tokens in Langfuse usage`)

## 설정과 실행 방법
- 코드 설정 변경 없음. Langfuse 키는 기존대로 `.env`의 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`·`LANGFUSE_BASE_URL`.
- 비용 계산에는 사용자가 Langfuse Cloud 모델 설정(`deepseek/deepseek-v4.1-flash`)에 세 가격을 등록해야 한다. 사용자가 정한 DeepSeek 공식 비피크 기준(토큰당 USD): `input` 0.00000015, `output` 0.0000006, `input_cached_tokens` 0.000000003. `total`에는 가격을 넣지 않는다.
- 테스트: `.\.venv\Scripts\python.exe -m unittest discover -s tests`

## 완료 조건별 결과
| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `UsageDetailsTests.test_cached_prompt_tokens_are_billed_separately`: 767/640/22/789 → `{"input": 127, "input_cached_tokens": 640, "output": 22, "total": 789}` |
| AC-2 | 충족 | `test_invalid_cached_tokens_keep_the_current_behavior`(details 없음·dict 아님·bool·실수·문자열·음수·초과·None), `test_the_input_tokens_path_is_not_split`가 기준 결과 `{"input": 11, "output": 3, "total": 14}` 유지 확인 |
| AC-3 | 충족 | `SuccessfulTraceTests.test_cached_prompt_tokens_are_split_in_the_generation_only`: 세션 경로의 generation 2개에 분리 값, `adk_events` usage는 원본과 동일 |
| AC-4 | 충족 | 구현자·리뷰어 각각 대상 SHA 코드로 전체 스위트 164 tests OK |
| AC-5 (선택) | 미검증 | 실제 Langfuse 전송·비용 표시 미실행 |

## 검증 결과
| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` (구현자) | 커밋 전 작업 트리, 커밋 후 `eb4ce02` 대비 두 파일 diff 없음 확인, Python 3.12 | exit 0, 164 OK | 전체 계약 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` (리뷰어) | `eb4ce02f…`, `git diff --quiet eb4ce02 -- agent tests` exit 0 | 164 OK | 전체 계약 |
| `python -m unittest tests.test_tracing.UsageDetailsTests tests.test_tracing.SuccessfulTraceTests` (리뷰어) | 같음 | 9 OK | 분리 규칙·세션 trace |
| 실제 usage 383건 변환 (리뷰어): `results/tools_dev30_calibrated_task008.json`의 `usage`에 `_usage_details()` 적용 | 같음, 로컬 결과 읽기만 | 383건 모두 분리, `input + input_cached_tokens == prompt_tokens`·`output`·`total` 불일치 0 | Command Code 실제 응답 형태에서 동작 |

검증 코드와 최종 SHA 일치 근거: 조율자 확인 시 HEAD = `eb4ce02f93812258fb0b05ad1797046b4384c4f0`, `git diff --quiet eb4ce02 -- agent tests` exit 0, 미추적 파일은 `docs/agent/tasks/TASK-009/`뿐.
검증 범위: 계약 테스트(가짜 Langfuse client)와 기존 결과 파일의 오프라인 변환만 수행. 실제 Langfuse 전송과 실제 DB 채점은 실행하지 않았다(채점 경로는 변경 없음).

## 리뷰 처리 결과
검토 명세 버전: 1
검토한 코드 SHA: eb4ce02f93812258fb0b05ad1797046b4384c4f0
리뷰 판정: pass
판정 근거·독립 검증: 누적 diff(`agent/tracing.py` +22, `tests/test_tracing.py` +119/−2, 범위 밖 변경 없음)가 명세의 적용 조건·예외·유지 계약과 일치. 리뷰어가 대상 SHA에서 전체 테스트와 실제 usage 383건 변환을 재실행.
지적 처리: 수정 요청 없음. 별도 제안 1건(아래 남은 제한에 반영).

## 남은 제한
- AC-5 미검증: Langfuse 서버가 `input_cached_tokens`에 등록 가격을 적용해 비용을 표시하는지는 실제 전송으로만 확인할 수 있다. 필수 조건·유지 계약에는 영향 없음.
- Langfuse 모델 설정에 `input_cached_tokens` 가격 행이 없으면, 분리 이후 캐시 적중분(실측 입력의 약 91%) 비용이 0으로 계산되어 과소 집계된다.
- 등록 가격은 DeepSeek 공식 비피크 요금(가격 비교 사이트 인용)이며 실제 호출 제공자인 Command Code의 청구 단가·피크 요금과 다를 수 있다.
- 이미 저장된 trace에는 소급 적용되지 않는다.

## 필요한 보완·판단
없음. 권장: 가격 행 등록 후 평가 1문제로 AC-5 확인.

## 직접 확인 방법
1. `.\.venv\Scripts\python.exe -m unittest discover -s tests` → 164 tests OK.
2. Langfuse 모델 설정에 `input_cached_tokens` = `0.000000003` 등록.
3. `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated ...` 실행 후 Langfuse의 `model_call` generation에서 usage에 `input_cached_tokens`가 보이고 Cost가 표시되는지 확인.

## 사용자 결정
결정: 승인
대상: 명세 버전 1, 최종 코드 eb4ce02f93812258fb0b05ad1797046b4384c4f0
확인 근거: 사용자가 조율자 채팅에서 "TASK-009 승인. spec.md와 final.md를 문서 커밋해줘."라고 승인(2026-10-20). AC-5 미검증 상태를 알린 뒤의 승인.
보존 문서: spec.md, final.md
