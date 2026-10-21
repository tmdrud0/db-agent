# TASK-014 최종 보고서

작업 ID: TASK-014
명세 버전: 1
기준 코드 버전: 438f50951b19fb453efc0645a4cefab3fe791a6b
대상 코드 버전: 715393884553b71f2251258aae97b77a3fdf0d9b
상태: ready_for_user
요약: 서브에이전트 정의에 선택 머리말 `fetch`를 추가했다. 값은 인자 없는 공식 조회 하나다. `consult` 때 루프가 그 조회를 공식 서비스로 한 번 실행하고 공식 비용을 차감한다. 원문은 서브에이전트 입력에만 넣고, 주 에이전트에게는 서브에이전트의 답과 조회 안내 한 줄만 관찰로 돌려준다. `fetch`가 없는 하네스(`baseline`, `sql_review`)의 프롬프트와 기록은 TASK-013과 같다. 예시 하네스는 `harness/context_finder/`다. 필수 완료 조건 AC-1~AC-10을 충족했고, 같은 명세 버전과 코드 SHA에 대한 리뷰 판정은 `pass`다. 선택 항목 AC-11(실제 평가)은 실행하지 않았다.
근거: 아래에 코드 위치, 테스트, 리뷰어의 독립 검증, 조율자 재확인 결과를 직접 기록했다. 대상 SHA에서 전체 테스트 247개가 통과했다(3개 skip).
미확정 사항: 없음.

## 변경 내용과 설계 결정
작업 코드 커밋: 715393884553b71f2251258aae97b77a3fdf0d9b `[TASK-014] feat: let sub-agents fetch one official lookup on consult`

변경 파일(11개, +794/−25):
- `agent/harness.py`, `agent/subagent.py`, `agent/tool_loop.py`, `agent/tools.py`
- `harness/context_finder/`(신규: `system.md`, `sub_agents/schema_finder.md`, `column_finder.md`, `knowledge_finder.md`)
- `tests/test_harness.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`

`agent/tracing.py`, `agent/server.py`, `scripts/`, `vendor/`는 바뀌지 않았다.

### 1. `fetch` 머리말
- **허용 도구:** `SUB_AGENT_FETCH_TOOLS`(`agent/tools.py:94`)에 있는 다음 4개다.
  - `get_schema`
  - `get_all_column_meanings`
  - `get_all_external_knowledge_names`
  - `get_all_knowledge_definitions`
- **거부:** 빈 값, 쉼표 다중 값, 위 4개가 아닌 이름(인자가 필요한 조회, `execute_sql`·`ask_user`·`submit_sql`, 없는 이름), 중복 키는 `HarnessError`다(`agent/harness.py` `_parse_sub_agent`).
- **데이터 모델:** `SubAgent.fetch: str | None`이 추가됐다.
- **해시:** 정의 파일 내용이 해시에 그대로 들어가므로 `fetch`를 추가하거나 바꾸면 하네스 해시도 바뀐다.

### 2. `consult` 실행 순서(`agent/tool_loop.py` `_consult_action`)
1. 서브에이전트 이름·인자 검증은 기존 경로를 쓴다. 없는 이름은 코인 없는 거부다.
2. **예산 확인:** 남은 코인이 `fetch` 비용보다 적으면 아무것도 호출하지 않고 거부 관찰을 돌려준다. 정상 루프는 코인 3 미만에서 먼저 끝나므로 실제로는 도달하지 않는 방어 경로다.
3. **공식 조회:** 기존 `_information()`을 재사용해 공식 조회를 한 번 실행한다. 예산 차감, tool span, 기록, 오류 처리가 일반 정보 조회와 같은 코드다. 캐시는 없다. 기록에는 `requested_by: {"action": "consult", "agent": ...}`가 붙는다.
4. **서브에이전트 호출:** 원문을 `Fetched <도구>:` 섹션으로 `context` 섹션들 뒤, `Request:` 앞에 넣고 서브에이전트를 한 번 호출한다(`agent/subagent.py`).
5. **답 반환:** 주 에이전트 관찰은 TASK-013 형식의 답 뒤에 `<도구> was fetched for the sub-agent (cost <n>).`을 붙인 것이다. 관찰 머리말의 `cost`는 consult 전후 예산 차이(= 공식 비용)다.

**설계 선택:** 차감·span·기록·오류 처리를 복제하지 않으려고 `_information()`을 재사용했다. 그래서 공식 비용을 우회하는 별도 경로가 생기지 않는다.

### 3. 기록
- **`tool_trajectory`:** `consult` 하나에 항목 두 개가 순서대로 남는다.
  1. 공식 조회 항목: 기존 형식(원문 `result`, 비용, 예산 전후, phase, elapsed)에 `requested_by`가 붙는다.
  2. `consult` 항목: cost 0, 차감 후 예산(전후 같음), `fetch` 도구 이름.
- **`adk_events`:** `subagent_call` event에 `fetch: {"tool", "cost"}`가 붙는다. `prompt`에는 실제로 보낸 messages가 원문을 포함해 그대로 남는다.
- **Langfuse:** 공식 조회는 기존 정보 조회와 같은 tool span이다. 서브에이전트 호출은 TASK-013과 같은 `subagent:<이름>` generation이다.
- **`/health`:** `/health.tools`는 공식 9개 그대로이고, `/health.harness.sub_agents`는 기존 이름 목록 형식 그대로다.

### 4. 오류
- **조회 중 서비스 오류(`GradingError`):** 세션 실패다. 기존 정보 조회와 같이 `failure.stage = "information"`이 기록되고, `consult` 항목은 남지 않는다.
- **조회 중 시간 종료:** `timed_out` 항목이 남고 `session_time_limit`으로 끝난다.
- **조회 성공 후 서브에이전트 `ModelError`:** 세션은 이어지고 조회 비용은 차감된 채로 남는다. 실패 관찰에 "was not refunded"라는 차감 사실을 적는다.
- **조회 성공 후 서브에이전트 시간 종료:** TASK-013과 같다. `cancelled` 항목이 남고 `session_time_limit`으로 끝나며, 환불은 없다.

### 5. 프롬프트
- **카탈로그:** `fetch`가 있는 서브에이전트 줄 끝에 `Fetches <도구> (cost <n>).`이 붙는다.
- **계약 문구:** `fetch`가 있는 서브에이전트가 하나 이상일 때만 4줄짜리 규칙이 붙는다. 표시된 공식 조회를 1회 실행하고 공식 비용을 차감하며, 원문은 서브에이전트만 보고 주 에이전트는 답만 받는다는 내용이다.
- **불변 확인:** `fetch`가 없으면 둘 다 붙지 않는다. `baseline`과 `sql_review`의 프롬프트는 기준 SHA와 문자 단위로 같다.

### 6. 예시 하네스 `harness/context_finder/`
- **`system.md`:** 기준 3줄 뒤에 지침을 붙였다. 전체 조회가 필요하면 finder에게 필요한 부분만 요청하고, 빠진 것이 있으면 단건 조회나 직접 조회를 쓰라는 내용이다.
- **서브에이전트:** 세 개를 두었고, 모두 `context: task, dialogue`다.

  | 서브에이전트 | `fetch` |
  |---|---|
  | `schema_finder` | `get_schema` |
  | `column_finder` | `get_all_column_meanings` |
  | `knowledge_finder` | `get_all_knowledge_definitions` |

- **공통 지침:** 필요한 항목을 원문 그대로 옮긴다(요약·의역 금지). 애매하면 포함한다. 원문에 없는 내용은 만들지 않는다. 정의 없이 참조만 되는 용어는 미정의로 표시한다.
- `sql_reviewer`는 넣지 않았다.

## 설정과 실행 방법
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests

$env:AGENT_HARNESS_DIR = 'harness/context_finder'
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName 'task014_context_finder_smoke'
```
- `AGENT_HARNESS_DIR`는 에이전트 프로세스가 시작되기 전에 설정해야 한다. 포트 6000의 기존 서버가 재사용되면, 그 서버가 시작할 때 읽은 하네스가 쓰인다.
- 실제로 쓰인 하네스는 `/health.harness`로 확인한다.
- 모델 설정과 평가 조건은 바뀌지 않았다.

## 완료 조건별 결과
| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 리뷰어가 기준 SHA를 `git archive`로 저장소 밖에 풀어 프롬프트를 만들었다. baseline(2,384자)과 sql_review(3,370자) 모두 대상 SHA와 문자 단위로 같다. 테스트 상수 `EXPECTED_BASELINE_PROMPT`·`EXPECTED_SQL_REVIEW_PROMPT`와도 같다. `test_a_consult_without_fetch_keeps_the_task_013_record`: `fetch`가 없으면 항목 1개, cost 0 |
| AC-2 | 충족 | `test_the_four_argument_free_lookups_load_as_fetch`, `test_invalid_fetch_values_fail`(빈 값, 쉼표 다중 값, 단건 조회 2개, execute/ask/submit, 없는 이름, 중복 키). 리뷰어 probe에서 공백 구분 다중 값과 대소문자 변형도 거부됨 |
| AC-3 | 충족 | `test_one_consult_fetches_once_charges_once_and_returns_only_the_answer`(조회 1회, 18→17, 서브 호출 1회, 원문이 `Request:` 앞), `test_two_consults_fetch_and_charge_twice`, `test_a_phase_two_consult_fetches_the_current_state_once` |
| AC-4 | 충족 | `test_the_raw_output_never_enters_the_main_dialogue_or_another_sub_agent`(`RAW-SCHEMA-MARKER`). 리뷰어 독립 probe: 세 finder를 연속 consult했을 때 도구별 표식이 주 에이전트 요청·`model_call.prompt`·다른 서브에이전트의 `Observations:`에 없고, 각 서브에이전트에는 자기 표식만 있음 |
| AC-5 | 충족 | 조회 항목(`requested_by`) → `consult` 항목(cost 0, `fetch`) 순서, `subagent_call.fetch`, 관찰 머리말 `cost 1`. `FetchConsultTraceTests`: `get_schema` tool span, `consult` span, `subagent:schema_finder` generation |
| AC-6 | 충족 | `test_a_fetch_service_error_is_a_session_failure`, `test_a_fetch_timeout_ends_the_session_with_the_standard_reason`, `test_a_sub_agent_model_error_after_a_fetch_keeps_the_session_and_the_charge`, `test_a_budget_below_the_fetch_cost_refuses_without_any_call`(내부 함수 직접 호출) |
| AC-7 | 충족 | `test_a_fetch_sub_agent_shows_its_lookup_and_the_fetch_contract`, `test_sub_agents_without_fetch_keep_the_task_013_prompt`. 리뷰어 probe에서 0.5코인 표시 확인 |
| AC-8 | 충족 | `test_the_context_finder_example_loads_with_its_three_fetches` |
| AC-9 | 충족(코드·테스트 근거) | `test_all_three_harnesses_expose_only_the_official_tools`: 세 하네스의 `/health.tools`가 공식 9개. `Assert-BaselineAgent`(`scripts/run-evaluation.ps1:129-145`) 검사 항목은 바뀌지 않음. 실제 스크립트 실행은 미실행 |
| AC-10 | 충족 | 구현자, 리뷰어, 조율자가 각각 대상 SHA에서 전체 스위트 통과 확인(247 tests OK, skipped=3) |
| AC-11(선택) | 미검증 | 실제 dev30 평가 미실행 |

## 검증 결과
| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` (구현자) | 7153938, Python 3.12.10, 가짜 모델·환경·Langfuse | exit 0, 247 tests OK (skipped=3) | AC-1~AC-10 |
| 같은 명령 (리뷰어) | HEAD 7153938, `git diff HEAD -- agent tests harness` 비어 있음 | 247 tests OK (skipped=3), 15.6s | 독립 재실행 |
| 같은 명령 (조율자) | HEAD 715393884553b71f2251258aae97b77a3fdf0d9b, 추적 변경 없음 | OK (skipped=3) | 최종 확인 |
| 기준 SHA 프롬프트 비교 (리뷰어, 저장소 밖 `git archive`) | 438f509 대 7153938 | baseline·sql_review 모두 일치 | AC-1 |
| 독립 probe (리뷰어, 가짜 모델·서비스) | `harness/context_finder` 세 finder 연속 consult, 0.5코인 fetch 재호출 | 서비스 경로 각 1회, 비용 합계 = 예산 변화, 관찰 머리말 cost·budget 일치, 원문 표식 비누출, `/health.tools` 9개 | AC-3~AC-7, AC-9 |
| 도구별 원문 길이 측정 (구현자, 로컬 결과 파일 읽기) | dev30 calibrated 결과의 `tool_trajectory.result` | 아래 표 | 서브 입력 크기 |
| `git diff --check 438f509 7153938` (구현자) | — | 출력 없음 | 공백 오류 없음 |

도구별 원문 길이(문자, 구현자 측정):

| 도구 | 최소 | 중앙값 | 최대 |
| --- | ---: | ---: | ---: |
| `get_schema` | 13,160 | 15,660 | 25,791 |
| `get_all_column_meanings` | 13,219 | 17,819 | 27,296 |
| `get_all_knowledge_definitions` | 17,633 | 24,571 | 26,963 |
| `get_all_external_knowledge_names` | 1,673 | 2,173 | 2,765 |

가장 큰 원문(약 2.7만 자, 대략 7천~9천 토큰)도 출력 한도 16,384 토큰 안에서 추출할 수 있는 규모로 판단했다.

검증 코드와 최종 SHA 일치 근거: HEAD가 대상 SHA이고 추적 코드 변경이 없다. 미추적 파일은 `docs/agent/tasks/TASK-014/spec.md`뿐이다(implementation.md·review.md는 .gitignore된 로컬 기록). 리뷰어의 probe와 압축 해제본은 저장소 밖에 있고, 저장소에 남은 파일은 없다. skip 3건은 TASK-012부터 있던 Windows 파일 심볼릭 링크 권한 문제다.
검증 범위: 가짜 모델·환경을 쓰는 계약 테스트와 로컬 probe다. 실제 모델, 실제 공식 서비스 조회, Langfuse 전송, `run-evaluation.ps1` 실행, AC-11은 하지 않았다.

## 리뷰 처리 결과
검토 명세 버전: 1
검토한 코드 SHA: 715393884553b71f2251258aae97b77a3fdf0d9b
리뷰 판정: pass
판정 근거·독립 검증:
- 리뷰어(이 세션이 만든 별도 서브에이전트)가 누적 diff 전체와 주변 코드를 읽었다. 주변 코드: `_information`, `_consult_action`, `_session_loop`, `_observation`, `_bounded`, `_model_actions`, `harness.py`, `subagent.py`, `tools.py`, `session.py`, `server.py`, `Assert-BaselineAgent`.
- 기준 SHA 프롬프트를 직접 만들어 비교했고, 표식 문자열과 코인 정합성 probe를 따로 만들어 실행했다.
- 코인 점검 결과:
  - 차감은 `_information()` 한 곳에서만 일어나고 환불 경로가 없다.
  - 공식 서비스를 비용 없이 부르는 경로가 없다.
  - 관찰 머리말 cost는 실제 차감액과 같다.
  - 행동 한도 계산은 바뀌지 않았다.
  - 에이전트 코드가 GT·`private/`·`results/`·`vendor/`를 읽지 않는다.

지적 처리: 수정 요청 없음. 통과를 막지 않는 제안 2건은 "남은 제한"에 옮겼다.

## 남은 제한
- **AC-11 미실행.** 실제 비교가 아직 없다. `baseline`과 `context_finder`(필요하면 `sql_review`도)를 같은 조건으로 각각 2회 이상 측정할 것을 권한다. 주요 비교 항목은 주 에이전트 입력 토큰(대화 기록 길이)이다. 서브에이전트 토큰과 보상도 함께 봐야 한다.
- **추출 누락 위험.** finder가 필요한 항목을 빠뜨리면 주 에이전트가 정의를 모른 채 풀 수 있다. 하네스 지침은 이럴 때 직접 조회하라고 안내하지만, 실제 효과는 미검증이다.
- **비용 구조.** 코인은 직접 전체 조회할 때와 같다. 서브 호출 한 번에 원문(최대 약 2.7만 자)이 입력으로 들어가므로, 이득은 그 원문이 주 에이전트의 이후 호출에 반복해서 실리지 않는 데서 나온다.
- **예산 부족 거부 경로는 기록을 남기지 않는다.** `tool_trajectory`와 `adk_events`에 아무것도 남지 않는다. 정상 루프에서는 도달하지 않는다(리뷰 제안).
- **phase 2 실제 반영 미검증.** phase 1 SQL을 적용한 뒤 실제 DB 상태가 조회 결과에 반영되는지는 실제 스택에서 확인하지 않았다. 구현은 매번 공식 서비스를 호출하고 캐시가 없다.
- **실제 환경 미검증.** 실제 모델 응답 품질, Langfuse 전송, `run-evaluation.ps1` 실행은 확인하지 않았다.
- **리뷰 독립성.** 리뷰는 같은 세션이 만든 서브에이전트가 했다. 별도 지시문과 독립 probe로 수행했지만, 사람이나 별도 세션의 리뷰는 아니다.
- **후속 문서.** README와 `docs/setup.md`에 `fetch`와 `harness/context_finder` 사용법을 넣는 일이 남아 있다(이번 범위 밖).

필수 완료 조건과 유지할 계약에는 영향이 없다.

## 필요한 보완·판단
없음. 선택 사항은 다음과 같다.
- AC-11: dev30 calibrated로 `harness/baseline`과 `harness/context_finder`를 각각 2회 이상 실행해 비교한다.

## 직접 확인 방법
1. `.\.venv\Scripts\python.exe -m unittest discover -s tests` → `Ran 247 tests`, `OK (skipped=3)`.
2. `harness/context_finder`로 평가 1문제를 실행한 뒤 다음을 확인한다.
   - `/health.harness.sub_agents`가 `["column_finder", "knowledge_finder", "schema_finder"]`이다(이름 목록).
   - 결과 JSON의 `tool_trajectory`에서 `requested_by`가 있는 공식 조회 항목 바로 뒤에 `consult` 항목(cost 0, `fetch`)이 있다.
   - 그다음 `model_call`의 `prompt`에 조회 원문이 없고, 서브에이전트 답과 조회 안내 줄만 있다.

## 사용자 결정
결정: 승인
대상: 명세 버전 1, 최종 코드 715393884553b71f2251258aae97b77a3fdf0d9b
확인 근거: 2026-10-21 조율자 채팅에서 사용자가 ready_for_user 최종 보고서를 확인한 뒤 "승인한다. TASK-014 spec.md와 final.md를 문서 커밋해줘."라고 승인했다. AC-11(선택)은 실행하지 않은 상태로 승인했다.
보존 문서: spec.md, final.md
