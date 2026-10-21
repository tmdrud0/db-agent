# TASK-014 작업 명세

작업 ID: TASK-014
명세 버전: 1
대상 코드 버전: 438f50951b19fb453efc0645a4cefab3fe791a6b
상태: planned
요약: 서브에이전트 정의에 `fetch` 머리말을 추가한다. 서브에이전트마다 인자 없는 공식 정보 조회 도구를 최대 하나 지정할 수 있다. `consult` 때 루프가 그 조회를 공식 비용으로 한 번 실행하고, 원문은 서브에이전트에게만 준다. 주 에이전트에게는 서브에이전트가 추려 낸 부분만 돌아간다.
근거: 사용자 요청(2026-10-21 조율자 채팅).
- 요청 원문: "조회 종류만 딱 하나만 호출할 수 있게 서브에이전트를 확장해보자".
- 배경: 주 에이전트 컨텍스트가 길어지는 문제다. TASK-013 구현자 측정으로 dev30의 대화 기록 길이 중앙값은 약 6.5만 자, 최대 약 7.9만 자다. 큰 부분은 스키마(약 1.5만 자)와 칼럼·지식 전체 조회다.
- 사용자 의도 해석: 서브에이전트 하나가 정보 조회 도구 한 종류만, `consult` 한 번에 한 번만 부른다. 서브에이전트가 조회 도구를 고르거나 인자를 정하는 여러 턴 루프는 만들지 않는다.
미확정 사항: 없음. 인자가 필요한 단건 조회(`get_column_meaning`, `get_knowledge_definition`)와 `execute_sql`, `ask_user`, `submit_sql`은 대상이 아니다(아래 "범위 밖"). 넣으려면 명세 버전을 올린다.

## 목표

1. **정의:** `sub_agents/<이름>.md`의 머리말에 `fetch: <도구 이름>`을 쓰면, 그 서브에이전트는 `consult`마다 해당 공식 조회를 한 번 받아 본다.
2. **컨텍스트 분리:** 조회 원문은 주 에이전트 대화 기록에 들어가지 않는다. 주 에이전트는 서브에이전트의 답(추출본)만 관찰로 받는다.
3. **계약 유지:** 조회는 공식 서비스를 호출하고 공식 비용을 세션 예산에서 차감한다. 기존 공식 조회와 같은 형식으로 `tool_trajectory`에 남는다. 코인을 우회하는 경로가 생기지 않는다.
4. **기존 하네스 불변:** `fetch`가 없는 하네스(`harness/baseline`, `harness/sql_review`)의 프롬프트와 동작은 TASK-013과 같다.

사용자가 확인할 동작:
- 예시 하네스 `harness/context_finder/`로 평가 1문제를 실행하면, 주 에이전트가 `consult`로 `knowledge_finder` 등을 부를 수 있다.
- 결과 JSON의 `tool_trajectory`에 공식 조회 항목(예: `get_all_knowledge_definitions`, 1코인)이 `consult` 항목과 함께 남는다.
- 다음 주 에이전트 호출의 대화 기록(`adk_events[k].prompt`)에는 조회 원문이 없고 서브에이전트의 답만 있다.

## 조사한 기준 코드

기준 SHA 기준의 코드 위치다.

- **`agent/harness.py`:** `_parse_sub_agent()`(`:169`)가 머리말 키 `description`, `context`만 허용한다. 허용 맥락은 `SUB_AGENT_CONTEXT_ITEMS = ("task", "dialogue", "observations")`(`:33`)다. `SubAgent`(`:38`)에는 `name`, `description`, `context`, `system`이 있다.
- **`agent/tools.py`:**
  - `INFORMATION_TOOLS`에 정보 조회 도구 6개가 있다.
  - 인자 없는 도구는 `get_schema`(1), `get_all_column_meanings`(1), `get_all_external_knowledge_names`(0.5), `get_all_knowledge_definitions`(1)다.
  - `consult_catalog()`(`:183`)가 `consult` 줄과 `  - <이름>: <description>` 목록을 만든다.
- **`agent/tool_loop.py`:**
  - `_information()`: 공식 정보 조회를 실행한다. 예산 차감, tool span, `record_action`, `GradingError`·`SessionExpired` 처리를 맡는다.
  - `_consult_action()`: 서브에이전트 호출 경로다. 코인 0, `subagent_call` event, `consult` 항목을 기록한다.
  - `_session_loop()`: 관찰 머리말의 비용을 `CONSULT_COST if name == "consult" else ALLOWED_TOOLS[name].cost`로 정한다.
- **`agent/session.py` `record_action()`:** `tool_trajectory` 항목을 만든다. 추가 키는 `**extra`로 넣는다.
- **`agent/subagent.py` `build_user_message()`:** `context` 순서의 섹션 뒤에 `Request:`를 붙인다.

## 범위

### 1. `fetch` 머리말
- `sub_agents/<이름>.md` 머리말에 선택 키 `fetch`를 추가한다. 값은 도구 이름 **하나**이고 다음 네 가지 중 하나여야 한다.
  - `get_schema`
  - `get_all_column_meanings`
  - `get_all_external_knowledge_names`
  - `get_all_knowledge_definitions`
- 다음 경우에는 `HarnessError`로 실패한다. 기존 머리말 규칙(빈 값·중복 키·알 수 없는 키 거부)도 그대로 적용한다.
  - 값이 비어 있다.
  - 쉼표 등으로 여러 개를 썼다.
  - 위 네 가지가 아닌 이름이다(인자가 필요한 조회, `execute_sql`, `ask_user`, `submit_sql`, 없는 이름).
- `SubAgent`에 `fetch: str | None`을 추가한다. `fetch`가 없는 정의는 TASK-013과 똑같이 동작한다.
- 정의 파일 내용이 바뀌므로 해시도 달라진다(기존 규칙 그대로).

### 2. `consult` 실행 순서(`fetch`가 있는 서브에이전트)
1. 기존처럼 행동을 해석하고 인자를 검증한다. 없는 서브에이전트 이름은 코인 없는 거부다.
2. **공식 조회 실행:** 기존 `_information()`과 같은 계약으로 `fetch` 도구를 공식 서비스에 한 번 호출한다.
   - 공식 비용을 세션 예산에서 차감한다.
   - tool span을 남긴다.
   - `tool_trajectory`에 그 도구 이름의 항목을 남긴다(아래 "4. 기록").
   - 캐시는 없다. `consult`마다 다시 호출하고 다시 차감한다.
3. **서브에이전트 호출:** user 메시지에 기존 섹션들(`context` 순서) 뒤, `Request:` 앞에 조회 원문 섹션을 넣는다. 섹션 제목 형식(예: `Fetched get_all_knowledge_definitions:`)은 구현자가 정하고 테스트에 고정한다. 그 뒤 LLM을 한 번 호출한다.
4. **답 반환:** 서브에이전트의 답만 주 에이전트에게 관찰로 돌려준다. 관찰 본문은 TASK-013 형식에 조회 사실을 알리는 한 줄을 더한다. 예: `get_all_knowledge_definitions was fetched for the sub-agent (cost 1).` 정확한 문구는 구현자가 정하고 테스트에 고정한다.

**조회 원문이 들어가지 않는 곳**
- 주 에이전트의 `messages`(대화 기록)
- 다른 서브에이전트에게 넘기는 `observations` 맥락. `observations`는 주 에이전트 대화 기록에서 만들기 때문이다.

**관찰 머리말의 비용:** `consult` 관찰의 `_observation()` 머리말 `cost`는 이번 `consult`로 차감된 총 코인(= `fetch` 도구의 공식 비용)이다. 이렇게 해야 머리말의 `budget`과 맞는다. `fetch`가 없는 서브에이전트는 기존처럼 0이다.

### 3. 오류와 예산
- **예산 부족:** `consult` 시작 시 남은 코인이 `fetch` 도구 비용보다 적으면, 조회와 서브에이전트 호출을 하지 않고 코인 없는 거부 관찰로 처리한다. 지금 루프는 코인이 3 미만이면 행동 전에 종료하므로 실제로는 거의 일어나지 않지만, 방어적으로 둔다.
- **공식 조회의 서비스 오류(`GradingError`):** 기존 정보 조회와 같다. `state["failure"]`를 기록하고 세션 실패로 올린다. 연결 오류를 오답과 구분하는 계약이므로, 서브에이전트 실패처럼 관찰로 바꾸지 않는다.
- **공식 조회 중 시간 종료:** 기존 정보 조회와 같다(`timed_out`, `session_time_limit` 종료).
- **조회는 성공했지만 서브에이전트 모델 호출이 실패하거나 시간이 끝난 경우:** TASK-013의 서브에이전트 실패·시간 종료 처리를 따른다. 차감된 조회 비용은 환불하지 않는다(기존 "실패하거나 중단된 호출은 환불하지 않는다" 규칙). 실패 관찰에 조회 비용이 이미 차감됐다는 사실을 적는다.

### 4. 기록
- **`tool_trajectory`:** `consult` 하나에 항목이 두 개 순서대로 남는다.
  1. 공식 조회 항목: 기존 정보 조회 항목과 같은 형식(`tool`, `args: {}`, `result`, `cost`, `budget_before`, `budget_after`, `phase`, `elapsed_seconds`)이다. 여기에 `requested_by: {"action": "consult", "agent": "<이름>"}`을 추가한다. `result`에는 기존처럼 조회 원문 전체를 남긴다.
  2. `consult` 항목: TASK-013 형식이다. `cost`는 0이고(조회 비용은 1번 항목에 있으므로), `fetch: "<도구 이름>"`을 추가한다. `budget_before`/`budget_after`는 조회 차감 후 값으로 같다.

  비용 합계가 공식 도구 항목들의 `cost` 합과 맞아야 한다. 예산 차감은 1번 항목에서만 일어난다.
- **`adk_events`:** `subagent_call` event에 `fetch: {"tool": ..., "cost": ...}`를 추가한다. `prompt`에는 실제로 보낸 messages(조회 원문 포함)가 그대로 남는다.
- **Langfuse:** 공식 조회는 기존 정보 조회처럼 tool span으로 남고, 서브에이전트 generation은 TASK-013과 같다.
- **`/health`:** `/health.tools`는 공식 9개 그대로다. `/health.harness.sub_agents`는 지금처럼 이름 목록이거나, 이름과 `fetch`를 담은 형식이어도 된다. 바꾼다면 기존 소비처가 없음을 확인하고 implementation.md에 적는다.

### 5. 프롬프트
- **카탈로그:** `fetch`가 있는 서브에이전트 줄에 조회 도구와 비용을 표시한다. 예: `  - knowledge_finder: <description> Fetches get_all_knowledge_definitions (cost 1).` 형식은 구현자가 정하고 테스트에 고정한다.
- **계약 문구:** `fetch`가 있는 서브에이전트가 하나 이상일 때만, 다음 취지의 규칙을 코드가 추가한다.
  - 그런 서브에이전트를 consult하면 표시된 공식 조회가 실행되고 그 공식 비용이 차감된다.
  - 조회 원문은 서브에이전트만 보고, 주 에이전트는 답만 받는다.
- **기존 하네스 불변:** `harness/baseline`의 프롬프트는 `EXPECTED_BASELINE_PROMPT`와 같다. `harness/sql_review`의 프롬프트도 TASK-013과 문자 단위로 같다.

### 6. 예시 하네스 `harness/context_finder/`
- **`system.md`:** 기준 하네스의 3줄에 지침을 덧붙인다. 지침 내용은 "전체 조회가 필요하면 직접 `get_all_*`를 부르기 전에 finder 서브에이전트에게 질문에 필요한 부분만 요청하라. 추출에 빠진 것이 있으면 단건 조회나 직접 조회를 써라"이다.
- **`sub_agents/`:** 다음 셋을 둔다.

  | 서브에이전트 | `fetch` | `context` |
  |---|---|---|
  | `schema_finder` | `get_schema` | `task, dialogue` |
  | `column_finder` | `get_all_column_meanings` | `task, dialogue` |
  | `knowledge_finder` | `get_all_knowledge_definitions` | `task, dialogue` |

  - 공통 지침: request와 질문에 필요한 항목만 원문 그대로 옮긴다(요약이나 의역 금지). 관련이 애매하면 포함한다. 원문에 없는 내용은 만들지 않는다. 다른 항목이 참조하지만 원문에 정의가 없는 용어는 따로 표시한다(삭제된 지식 대비).
- `sql_reviewer`는 이 하네스에 넣지 않는다. 효과를 따로 비교하기 위해서다.
- 예시 문구의 품질 자체는 완료 조건이 아니다. 형식이 유효하고 로드되는지만 확인한다.

### 문서
문서 산출물: docs/agent/tasks/TASK-014/implementation.md. 다음을 기록한다.
- `fetch` 규칙, 실행 순서, 원문 섹션·관찰·카탈로그·계약 문구
- 기록 형식, 실행 방법, 검증 결과

보존 정책: implementation.md·review.md는 로컬 작업 기록. 사용자 확인 후 spec.md·final.md를 한 번에 커밋.

보고서 외 문서 수정: 없음. `harness/context_finder/` 파일은 코드 산출물로 구현 커밋에 포함한다. README·setup 갱신이 필요하면 implementation.md에 후속 사항으로 남긴다.

## 제약

- 공식 도구 이름·인자·비용, `/health.tools`, 예산 계산 방식(공식 호출 시작 시 차감, 환불 없음), 채점·보상 기록, phase 전환, 결과 JSON 기존 필드는 바꾸지 않는다. 필드 추가는 가능하다.
- 서브에이전트를 통한 조회도 공식 서비스를 거쳐야 한다. 로컬 파일이나 `vendor/` 데이터에서 스키마·지식을 직접 읽지 않는다.
- 서브에이전트는 조회 도구를 고르거나 인자를 정하거나 여러 번 부르지 않는다. 조회는 정의 파일의 `fetch` 하나, `consult` 한 번에 한 번이다.
- 행동 한도 24회, 세션 1500초, 코인 부족 종료, 응답 해석, 대화 기록 방식, `execute_sql` 여러 문장 거부는 바꾸지 않는다.
- 루프가 서브에이전트를 자동으로 부르는 경로를 만들지 않는다.
- GT, `private/`, `results/`, `.env`, `vendor/`의 정답 자료를 읽지 않는다.
- 테스트는 실제 모델·Langfuse·DB 서비스를 호출하지 않고, `results/`를 읽지 않는다.

## 범위 밖

- 인자가 필요한 단건 조회(`get_column_meaning`, `get_knowledge_definition`)의 `fetch`. 서브에이전트가 인자를 정해야 하므로 도구 루프가 필요하다.
- 서브에이전트에게 `execute_sql`·`ask_user`·`submit_sql`을 주는 것
- 서브에이전트 하나에 여러 조회 도구 지정, 서브에이전트의 여러 턴 도구 사용
- 조회 결과 캐시, 주 에이전트 대화 기록의 압축·요약
- 예시 서브에이전트 문구의 성능 개선, README·setup 문서 갱신

## 완료 조건

- AC-1 (기존 하네스 불변): `harness/baseline`의 프롬프트가 `EXPECTED_BASELINE_PROMPT`와 같다. `harness/sql_review`의 프롬프트가 기준 SHA에서 만든 프롬프트와 문자 단위로 같다. `fetch` 없는 서브에이전트의 `consult` 기록(코인 0, 항목 1개)이 TASK-013과 같다. 테스트로 확인한다.
- AC-2 (머리말 검증): 다음 경우에 각각 `HarnessError`가 나는 것을 테스트로 확인한다. 허용 4개 도구는 각각 로드된다.
  - `fetch` 빈 값
  - 여러 값
  - 인자가 필요한 조회 도구
  - `execute_sql`·`ask_user`·`submit_sql`
  - 없는 이름
  - 중복 `fetch` 키
- AC-3 (실행·비용): 가짜 모델·환경 세션 테스트로 `fetch`가 있는 서브에이전트를 consult할 때 다음을 확인한다.
  - 공식 서비스에 해당 조회가 정확히 한 번 간다.
  - 공식 비용만큼 예산이 줄고, 서브에이전트 호출은 한 번이다.
  - 서브에이전트 user 메시지에 조회 원문이 `Request:` 앞에 들어간다.
  - 같은 서브에이전트를 두 번 consult하면 조회도 두 번, 차감도 두 번이다.
- AC-4 (컨텍스트 분리): 다음 주 에이전트 호출의 messages 어디에도 조회 원문이 없고, 서브에이전트 답과 조회 안내 줄은 있다. 이후 다른 서브에이전트의 `observations` 맥락에도 조회 원문이 없다. 원문에만 있는 표식 문자열로 테스트한다.
- AC-5 (기록): 다음을 테스트로 확인한다.
  - `tool_trajectory`에 공식 조회 항목(`requested_by` 포함, 비용, 예산 전후)과 `consult` 항목(cost 0, `fetch`)이 순서대로 남는다.
  - `subagent_call` event에 `fetch`가 있다.
  - Langfuse 가짜 클라이언트에 조회 tool span과 서브에이전트 generation이 남는다.
  - 관찰 머리말의 `cost`가 조회 비용과 같다.
- AC-6 (오류): 다음 각각을 테스트로 확인한다.
  - 조회 `GradingError`는 세션 실패다(기존 정보 조회와 같은 `failure` 기록).
  - 조회 중 시간 종료는 `session_time_limit`이다.
  - 조회 성공 후 서브에이전트 `ModelError`는 세션을 유지한다. 조회 비용은 차감된 채 남고, 실패 관찰에 차감 사실이 있다.
  - 예산이 `fetch` 비용보다 적으면 조회 없이 거부된다(필요하면 상태를 직접 만들어 테스트).
- AC-7 (프롬프트): `fetch`가 있는 하네스의 프롬프트에 서브에이전트별 조회 도구·비용 표시와 조회 계약 문구가 들어간다. 공식 9개 줄과 기존 계약 문구는 그대로다. 테스트에 고정한다.
- AC-8 (예시 하네스): `harness/context_finder/`가 로드되고, 세 서브에이전트와 각 `fetch`가 프롬프트에 나온다. 테스트로 확인한다.
- AC-9 (평가 실행 경로): `/health.tools`가 세 하네스 모두에서 공식 9개 그대로이고 `Assert-BaselineAgent`가 통과할 근거를 남긴다. `harness/context_finder`로 실행하는 명령을 implementation.md에 적는다.
- AC-10: 전체 테스트 스위트를 저장소의 기존 방식(`.venv\Scripts\python.exe -m unittest discover -s tests`)으로 실행해 통과한다. 명령과 결과를 implementation.md에 기록한다.
- AC-11 (선택, 실제 평가): 사용자가 원하면 dev30 calibrated 평가를 `harness/baseline`과 `harness/context_finder`로 각각 실행해 비교한다.
  - 실행 간 편차가 크므로 각각 2회 이상을 권한다.
  - 비교 항목: 평균 보상, Phase 1·2 통과 수, 주 에이전트 입력 토큰(대화 기록 길이), 서브에이전트 토큰, `consult`·직접 전체 조회 횟수, 코인 사용, 문제당 시간.
  - 실행하지 않으면 미검증으로 적는다.

## 조사할 사항

- **조회 실행 경로 재사용:** `_information()`을 그대로 재사용할지, 공통 부분을 뽑아 관찰 문자열 대신 원문을 돌려받는 함수를 만들지 정한다. 어느 쪽이든 예산 차감·기록·오류 처리가 기존 정보 조회와 같아야 한다.
- **서브에이전트 입력 크기:** `get_all_column_meanings`·`get_schema` 원문은 수만 자일 수 있다. 실제 결과 파일 기준으로 도구별 원문 길이를 대략 확인해 implementation.md에 적는다. 출력 한도(16384 토큰) 안에서 추출이 가능한지 판단 근거로 쓴다. 잘라 내기는 범위 밖이다.
- **phase 2의 조회:** phase 1 SQL이 DB에 적용된 뒤의 조회도 기존처럼 공식 서비스가 현재 상태로 답하는지 확인한다(캐시 없음).

## 명세 변경 이력
| 버전 | 변경 내용·이유 | 구현·리뷰에 미치는 영향 |
| --- | --- | --- |
| 1 | 최초 명세 | 하네스 머리말(`fetch`), `consult` 실행 경로(공식 조회·비용·기록·오류), 프롬프트 표시, 예시 하네스, 관련 테스트 |

## 전달 정보
명세 경로·버전: docs/agent/tasks/TASK-014/spec.md, 1
기준 커밋: 438f50951b19fb453efc0645a4cefab3fe791a6b
작업 폴더: docs/agent/tasks/TASK-014/
커밋 제목 형식: [TASK-014] <type>: <변경 요약>
기존 미커밋 변경: 없음(이 명세 파일만 로컬에 있음. 이전 작업의 implementation.md·review.md는 .gitignore된 로컬 기록)
직접 확인 방법:
1. 전체 테스트 스위트를 실행한다.
2. `$env:AGENT_HARNESS_DIR = 'harness/context_finder'`로 평가 1문제를 실행한다.
3. 결과 JSON의 `tool_trajectory`에서 `requested_by`가 있는 공식 조회 항목과 바로 뒤의 `consult` 항목을 확인한다.
4. 다음 `adk_events`(`model_call`)의 `prompt`에 조회 원문이 없고 서브에이전트 답만 있는지 확인한다.
