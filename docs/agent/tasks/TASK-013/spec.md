# TASK-013 작업 명세

작업 ID: TASK-013
명세 버전: 1
대상 코드 버전: 00cb7c3e7ea23357ecd2071a482df6a249577166
상태: planned
요약: 하네스에 서브에이전트 정의 파일(`sub_agents/<이름>.md`)을 둘 수 있게 하고, 주 에이전트가 로컬 도구 `consult`로 서브에이전트에게 일을 맡길 수 있게 한다. 서브에이전트는 공식 도구 없이 LLM 호출 한 번으로 답한다. 서브에이전트를 정의하지 않은 하네스에서는 동작과 프롬프트가 TASK-012와 같다.
근거: 사용자 요청(2026-10-21 조율자 채팅, "서브에이전트 추가한 명세 작성해줘"). 앞선 논의와 task011 dev30 실패 분석에서 다음 결론이 나왔다.
- 실패 18건 모두 예산 소진으로 끝났다. 첫 제출이 틀린 문제는 재시도로 거의 복구되지 않았다(1/19).
- 주요 원인은 A(사용자 답변·질문 조건을 SQL에 반영하지 않음)와 B(정의를 추측함)다.
- 이를 겨냥해 코인을 쓰지 않는 제출 전 검토 같은 서브 호출이 후보로 정리됐다.
- TASK-012에서 사용자가 정한 원칙을 따른다. 도메인 검사를 코드에 넣지 않고, 실험은 하네스 파일 차이로 비교한다.
- 조사한 코드는 아래 "조사한 기준 코드"에 적었다.
미확정 사항: 없음. 루프가 서브에이전트를 강제로 부르는 훅은 이번 범위가 아니다(아래 "범위 밖"). 넣으려면 명세 버전을 올린다.

## 목표

1. **정의:** 서브에이전트는 하네스 파일 하나로 정의한다. 하네스 해시에 포함돼 실험을 비교할 수 있다.
2. **호출:** 주 에이전트가 `consult`로 서브에이전트를 부르면, 서브에이전트가 정해진 맥락과 요청만 보고 답한다. 그 답은 주 에이전트에게 관찰로 돌아간다.
3. **계약 유지:** 공식 도구, 비용, 예산, 채점은 바뀌지 않는다. 서브에이전트가 없는 하네스(`harness/baseline/`)에서는 TASK-012와 동작·프롬프트가 같다.
4. **기록:** 서브에이전트 호출의 입력, 출력, 토큰, 시간이 결과 JSON과 Langfuse에 남는다. 그래서 비용과 효과를 따로 셀 수 있다.

사용자가 확인할 동작:
- 예시 하네스 `harness/sql_review/`로 평가 1문제를 실행하면, 주 에이전트가 `consult`로 `sql_reviewer`를 부를 수 있다.
- 결과 JSON의 `tool_trajectory`에 `consult` 항목(코인 0)이 생기고, `adk_events`에 서브에이전트 호출 기록이 생긴다.
- `harness/baseline/`으로 실행하면 `consult`가 카탈로그에 나오지 않고, 시스템 프롬프트가 TASK-012와 같다.

## 조사한 기준 코드

기준 SHA 기준의 코드 위치다.

- **`agent/harness.py`:** 하네스 폴더에서 `system.md`, `memory.md`, `tools/<공식 도구>.md`만 인식하고, 그 밖의 항목은 `HarnessError`로 거부한다. 링크와 루트 밖 경로를 막는 `_check_inside()`가 있고, 해시는 경로와 내용으로 계산한다.
- **`agent/tool_loop.py` `system_prompt()`:** `system.md` → 계약(응답 형식, `catalog(tool_notes)`, 규칙) → `memory.md` 순서로 조립한다.
- **`agent/tool_loop.py` `_session_loop()`:** 응답 하나에 행동 하나를 실행한다.
  - 행동 한도는 `_model_actions()`가 `adk_events`의 `type == "model_call"`만 세서 판정한다(24회).
  - 종료 조건은 세션 1500초와 코인 부족이다.
  - 한 행동의 관찰은 `_observation()` 형식의 user 메시지로 붙는다.
- **`agent/tools.py`:**
  - `ALLOWED_TOOLS`에 공식 도구 9개가 있다.
  - `parse_response()`는 `ALLOWED_TOOLS`에 없는 이름을 `Unknown action`으로 거부한다(`:440`, `:454`). 이 거부는 코인을 쓰지 않는다.
  - `tool_metadata()`가 `/health.tools`를 만든다.
- **`scripts/run-evaluation.ps1`의 `Assert-BaselineAgent`:** `/health.tools`의 도구 수와 이름·가격이 공식 9개와 정확히 같은지 검사한다. **`consult`를 `/health.tools`에 넣으면 평가가 시작되지 않는다.**
- **`agent/model.py`:** `ModelClient.chat(messages)`가 `ChatReply`(`content`, `truncated`, `usage`, `elapsed_seconds`, `response_model`)를 돌려준다. 모델은 `MODEL_ID`, 출력 한도는 `MAX_OUTPUT_TOKENS`(16384)다.
- **`agent/tracing.py`:** `SessionTrace.model_start()`(generation)와 `tool_start()`(tool span)가 있다.
- **벤더 러너(`orchestrator/ainteract.py:127-155`):** 결과 JSON에 `tool_trajectory`, `adk_events`, `dialogue_history` 등 정해진 필드만 복사한다.

## 범위

### 1. 서브에이전트 정의 파일
- **위치:** `<하네스>/sub_agents/<이름>.md`.
  - `<이름>`은 소문자, 숫자, `_`로만 이루어지고 소문자로 시작한다(정규식 `^[a-z][a-z0-9_]{0,39}$`).
  - 하네스 로더가 이 디렉터리를 새로 인식한다. 그 밖의 규칙(미인식 항목 거부, 링크 거부, UTF-8)은 기존과 같다.
- **형식:** 파일 맨 앞에 머리말 블록을 두고, 그 뒤 본문이 서브에이전트의 시스템 프롬프트다.
  ```markdown
  ---
  description: <주 에이전트의 카탈로그에 보일 한 줄 설명>
  context: task, dialogue
  ---
  <서브에이전트 시스템 프롬프트 본문>
  ```
  - `description`(필수): 비어 있지 않은 한 줄.
  - `context`(선택): 서브에이전트가 받을 맥락 항목을 쉼표로 나열한다. 허용 값은 아래 "3. 서브에이전트에게 주는 입력"의 항목 이름뿐이다. 생략하면 `task, dialogue`다.
  - 본문(필수): 비어 있지 않아야 한다.
  - 머리말에 다른 키가 있거나, 필수 값이 없거나, 허용하지 않는 `context` 값이 있으면 `HarnessError`로 실패한다.
  - 머리말 파서는 위 형식만 지원하면 된다. YAML 라이브러리를 새로 추가하지 않는다.
- **해시:** 파일은 다른 하네스 파일처럼 경로(`sub_agents/<이름>.md`)와 내용으로 하네스 해시에 들어간다.

### 2. `consult` 로컬 도구
- **노출 조건:** 하네스에 서브에이전트가 하나 이상 있을 때만 행동 목록에 `consult`가 생긴다. 서브에이전트가 없으면 `consult`는 알 수 없는 행동으로 거부된다(기존 `Unknown action`과 같은 처리, 코인 없음).
- **인자:** `{"agent": "<서브에이전트 이름>", "request": "<맡길 일>"}`. 둘 다 비어 있지 않은 문자열이다.
  - 없는 `agent` 이름은 행동 거부(코인 없음, 거부 관찰)로 처리한다.
  - 인자 검증은 기존 `validate_args` 규칙(누락·추가 인자, 빈 문자열)을 따른다.
- **비용:** bird-coin 0. 예산을 바꾸지 않는다. 주 에이전트가 `consult`를 고른 응답은 기존처럼 모델 행동 1회로 세므로 행동 한도 24회에 포함된다. 서브에이전트 자신의 LLM 호출은 행동 한도에 포함하지 않는다.
- **카탈로그와 계약 문구:** 카탈로그에 `consult` 줄과 사용 가능한 서브에이전트 목록(이름과 `description`)을 코드가 만든다. 계약 규칙에 다음 취지의 문구를 코드로 추가한다. 정확한 문장은 구현자가 정하고 테스트에 고정한다.
  - `consult`는 공식 도구가 아니다.
  - 코인이 들지 않는다.
  - 행동 1회로 센다.
  - 서브에이전트는 데이터베이스나 사용자에게 접근하지 못하고, 받은 맥락만 본다.
  - 서브에이전트의 답은 관찰일 뿐 채점에 영향이 없다.
- **공식 도구와의 분리:** `/health.tools`와 `tool_metadata()`는 지금처럼 공식 9개만 보고한다. 서브에이전트 정보는 별도 필드(예: `/health.harness.sub_agents`에 이름 목록)로 보고한다. `scripts/run-evaluation.ps1`의 검사가 그대로 통과해야 한다.
- **서브에이전트가 없는 하네스:** `harness/baseline/`으로 조립한 시스템 프롬프트는 TASK-012의 `EXPECTED_BASELINE_PROMPT`와 문자 단위로 같아야 한다. `consult` 관련 문구는 서브에이전트가 있을 때만 들어간다.

### 3. 서브에이전트에게 주는 입력
서브에이전트 호출은 공식 도구 없이 LLM 호출 한 번이다. 메시지는 다음과 같다.
- **system:** 정의 파일 본문
- **user:** 코드가 정한 형식으로 조립한 맥락 + 주 에이전트의 `request`. 맥락 항목은 다음뿐이다.
  - `task`: 데이터베이스 이름, 현재 phase, phase 1 질문, phase 2라면 후속 질문. 주 에이전트가 받은 것과 같은 텍스트다.
  - `dialogue`: 지금까지의 `ask_user` 질문과 공식 답변(`dialogue_history`)
  - `observations`: 지금까지 주 에이전트가 실행한 행동과 받은 관찰 전체. 대화 기록의 행동 JSON과 관찰 메시지를 옮긴다. 이전 `consult` 결과도 포함한다.

맥락 조립 순서와 구분 표기는 구현자가 정하고 implementation.md와 테스트에 고정한다.

- **금지:** 서브에이전트 입력에는 위 항목과 `request` 외의 정보를 넣지 않는다. GT, 테스트 케이스, 공식 응답에 없는 숨은 정보, 로컬 파일 내용(하네스 파일 제외)은 절대 넣지 않는다.
- **모델 설정:** 주 에이전트와 같은 `ModelClient` 설정(`MODEL_ID`, 출력 한도, 타임아웃, 재시도)을 쓴다. 서브에이전트별 모델 지정은 이번 범위가 아니다.
- **출력 처리:** 서브에이전트의 응답 텍스트 전체를 주 에이전트의 관찰로 돌려준다. 관찰 머리말은 기존 `_observation()` 형식에 `consult`, 비용 0을 쓴다.
  - 출력 한도에 걸려 잘린 경우 관찰에 잘렸다는 안내를 붙인다.
  - 서브에이전트 응답은 파싱하지 않고, 응답에서 행동을 실행하지 않는다.

### 4. 오류와 시간
- **시간 제한:** 서브에이전트 호출에도 세션 시간 제한(`_bounded`)을 적용한다. 시간이 다 되면 기존 시간 종료 경로로 세션을 끝낸다.
- **호출 실패:** 재시도 후에도 서브에이전트 모델 호출이 실패(`ModelError`)하면 세션을 중단하지 않는다.
  - 관찰로 "서브에이전트 호출이 실패했다"는 안내와 오류 요약을 돌려준다.
  - `tool_trajectory` 항목에 오류를 기록한다.
  - 이 실패는 채점이나 보상과 무관하며, 결과에서 서브에이전트 실패로 구분할 수 있어야 한다.
  - 주 에이전트 모델 호출의 실패 처리(세션 실패, 502)는 바꾸지 않는다.

### 5. 기록
- **`tool_trajectory`:** `consult` 항목을 남긴다. 남길 내용은 `tool: "consult"`, `args`(agent, request), `cost: 0`, 예산 전후(같은 값), `phase`, `elapsed_seconds`, `result`(서브에이전트 응답 텍스트 또는 오류)다.
- **`adk_events`:** 서브에이전트 호출마다 event를 하나 남긴다.
  - 내용은 `type: "subagent_call"`, 서브에이전트 이름, `prompt`(실제로 보낸 messages), `content`, `truncated`, `usage`, `elapsed_seconds`, `harness`다.
  - `type`이 `model_call`이 아니므로 행동 한도 계산(`_model_actions`)에 들어가지 않아야 한다.
  - 기존 `model_call` event의 형식과 순서 의미는 바꾸지 않는다.
- **Langfuse:** 서브에이전트 호출을 별도 generation으로 남긴다(이름에 서브에이전트 이름 포함). 사용량은 기존 방식으로 기록한다. Langfuse를 쓰지 않거나 실패해도 결과에는 영향이 없어야 한다.

### 6. 예시 하네스 `harness/sql_review/`
- `system.md`: 기준 하네스의 `system.md` 내용에, 제출 전에 `sql_reviewer`에게 후보 SQL 검토를 맡기라는 행동 지침을 덧붙인다.
- `sub_agents/sql_reviewer.md`: 질문 조건, 사용자 답변, 주 에이전트가 조회한 지식 정의와 후보 SQL을 대조해 누락·불일치를 구체적으로 짚는 검토자 역할이다. `context: task, dialogue, observations`.
  - 출력 형식(예: 문제점 목록 또는 "문제 없음")을 프롬프트에 정한다.
  - 채점기나 정답을 언급하지 않는다.
- 예시 하네스 내용의 품질 자체는 완료 조건이 아니다. 형식이 유효하고 로드되는지만 확인한다. 문구는 구현자가 작성한다.

### 문서
문서 산출물: docs/agent/tasks/TASK-013/implementation.md. 다음을 기록한다.
- 정의 파일 형식, 머리말 파서 규칙
- `consult` 카탈로그·계약 문구, 맥락 조립 형식
- 기록 형식, 실행 방법, 검증 결과

보존 정책: implementation.md·review.md는 로컬 작업 기록. 사용자 확인 후 spec.md·final.md를 한 번에 커밋.

보고서 외 문서 수정: 없음. `harness/sql_review/` 파일은 코드 산출물로 구현 커밋에 포함한다. README·setup 갱신이 필요하면 implementation.md에 후속 사항으로 남긴다.

## 제약

- 공식 도구의 이름·인자·비용, `/health.tools`, 예산 계산, 채점·보상 기록, phase 전환, 결과 JSON의 기존 필드는 바꾸지 않는다. 필드 추가는 가능하다.
- 행동 한도 24회, 세션 1500초, 코인 부족 종료, 응답 해석(첫 행동만 실행), 대화 기록 방식(TASK-011), `execute_sql` 여러 문장 거부는 바꾸지 않는다.
- 서브에이전트는 공식 서비스(DB 환경, 사용자 시뮬레이터, 채점)를 호출하지 않는다. 서브에이전트 응답으로 행동을 실행하지 않는다.
- 루프가 서브에이전트를 자동으로 부르는 경로(제출 전 강제 검토 등)를 만들지 않는다. 호출은 주 에이전트의 `consult` 행동으로만 일어난다.
- 서브에이전트가 없는 하네스의 동작·프롬프트·기록은 TASK-012와 같다. 단, `/health.harness`에 빈 서브에이전트 목록 같은 필드가 추가되는 것은 허용한다.
- 서브에이전트 입력과 하네스 로딩은 GT, `private/`, `results/`, `.env`, `vendor/`의 정답 자료를 읽지 않는다.
- 테스트는 실제 모델·Langfuse·DB 서비스를 호출하지 않고, `results/`를 읽지 않는다.

## 범위 밖

- 루프가 강제로 서브에이전트를 부르는 훅(예: `before_submit` 검토)
- 서브에이전트에게 공식 도구나 로컬 실행 도구를 주는 것, 서브에이전트끼리의 호출, 여러 턴 대화
- 서브에이전트별 모델·온도·출력 한도 지정
- 서브에이전트 호출 횟수 상한. 행동 한도와 세션 시간으로만 제한한다.
- 예시 하네스 문구의 성능 개선
- 하네스 자동 개선 루프, 경험 라이브러리
- README·setup 문서 갱신

## 완료 조건

- AC-1 (기준 하네스 불변): `harness/baseline/`으로 조립한 시스템 프롬프트가 TASK-012의 `EXPECTED_BASELINE_PROMPT`와 문자 단위로 같다. `consult` 행동은 `Unknown action`으로 거부되고 코인을 쓰지 않는다. 테스트로 확인한다.
- AC-2 (정의 파일 검증): 다음 경우에 각각 `HarnessError`가 나는 것을 테스트로 확인한다.
  - 이름 규칙 위반, 머리말 없음
  - `description` 없음 또는 빈 값, 본문 없음
  - 알 수 없는 머리말 키, 허용하지 않는 `context` 값
  - `sub_agents/` 안의 미인식 항목(하위 디렉터리, `.md` 아닌 파일)
  - `sub_agents/` 또는 그 안 파일의 링크·junction(기존 링크 테스트 방식)

  정상 파일은 로드되고 하네스 해시에 반영된다. 파일 내용이나 경로를 바꾸면 해시가 달라진다.
- AC-3 (카탈로그·계약): 서브에이전트가 있는 하네스의 시스템 프롬프트에 다음이 들어간다. 테스트에 고정한다.
  - `consult` 줄(인자, `Cost: 0`)
  - 서브에이전트 이름·설명 목록
  - `consult` 계약 문구

  공식 도구 9개의 줄과 기존 계약 문구는 그대로다. `/health.tools`는 공식 9개 그대로이고, 서브에이전트 이름은 별도 필드에 나온다.
- AC-4 (호출과 입력): 가짜 모델을 쓰는 세션 테스트로 다음을 확인한다.
  - 주 에이전트가 `consult`를 고르면 서브에이전트 호출이 한 번 일어난다.
  - 그 messages의 system이 정의 파일 본문이다.
  - user에 `context`로 지정한 항목과 `request`만 들어간다(`task`, `dialogue`, `observations` 각각 포함·제외 확인).
  - 서브에이전트 응답이 다음 주 에이전트 호출의 관찰로 들어간다.
  - 공식 서비스 호출이 늘지 않는다.
- AC-5 (비용·한도): `consult`는 예산을 바꾸지 않는다. `consult`를 고른 응답은 모델 행동 1회로 세어 24회 한도에 포함되고, 서브에이전트 호출 자체는 포함되지 않는다. 테스트로 확인한다. 예: 서브에이전트 호출이 있어도 `_model_actions`가 주 에이전트 호출 수와 같다.
- AC-6 (오류 거부): 다음 각각을 테스트로 확인한다.
  - 없는 서브에이전트 이름, 인자 누락·추가는 코인 없는 행동 거부다.
  - 서브에이전트 모델 호출 실패는 세션을 끝내지 않고 실패 관찰과 `tool_trajectory` 오류 기록을 남긴다.
  - 출력이 잘리면 잘림 안내를 붙인다.
  - 서브에이전트 호출 중 세션 시간이 끝나면 기존 시간 종료 사유로 세션이 끝난다.
- AC-7 (기록): 다음 기록이 남는 것을 테스트로 확인한다.
  - `tool_trajectory`의 `consult` 항목
  - `adk_events`의 `subagent_call` event(이름, prompt, content, usage, harness)
  - Langfuse 가짜 클라이언트의 서브에이전트 generation

  기존 `model_call` event 형식은 그대로다.
- AC-8 (예시 하네스): `harness/sql_review/`가 로드되고, 시스템 프롬프트에 `consult`와 `sql_reviewer`가 나온다. 테스트로 확인한다.
- AC-9 (평가 실행 경로): `scripts/run-evaluation.ps1`의 `Assert-BaselineAgent`가 기준·예시 하네스 모두에서 통과한다. `/health.tools` 불변 테스트와 코드 확인으로 근거를 남긴다. 예시 하네스로 실행하는 명령을 implementation.md에 적는다.
- AC-10: 전체 테스트 스위트를 저장소의 기존 방식(`.venv\Scripts\python.exe -m unittest discover -s tests`)으로 실행해 통과한다. 명령과 결과를 implementation.md에 기록한다.
- AC-11 (선택, 실제 평가): 사용자가 원하면 dev30 calibrated 평가를 `harness/baseline`과 `harness/sql_review`로 각각 실행해 비교한다.
  - 실행 간 편차가 크므로 각각 2회 이상을 권한다.
  - 비교 항목: 평균 보상, Phase 1·2 통과 수, 첫 제출 통과율, `consult` 호출 수, 주·서브 에이전트별 토큰, 문제당 시간.
  - 실행하지 않으면 미검증으로 적는다.

## 조사할 사항

- **허용 행동 집합:** `parse_response()`는 모듈 상수 `ALLOWED_TOOLS`로 행동을 판정한다. 하네스에 따라 `consult`를 받아들이려면 허용 행동 집합을 호출 시점에 넘기는 방식 등이 필요하다. 공식 도구 판정(비용, `/health`, 여러 문장 거부)이 바뀌지 않는 구조를 고른다.
- **`observations` 맥락의 크기:** schema와 지식 전체 조회가 들어가면 수만 자가 될 수 있다. 실제 결과 파일 기준 크기를 대략 확인해 implementation.md에 적는다. 잘라 내는 처리는 이번 범위가 아니다. 문제가 크면 조율자에게 알린다.
- **Langfuse 기록 위치:** 서브에이전트 generation을 tool span 아래에 둘지 세션 바로 아래에 둘지 정하고 기록한다.
- **결과 보고서:** `orchestrator.report`(HTML 보고서)가 `adk_events`의 새 `type`을 만났을 때 오류 없이 처리하는지 확인한다.

## 명세 변경 이력
| 버전 | 변경 내용·이유 | 구현·리뷰에 미치는 영향 |
| --- | --- | --- |
| 1 | 최초 명세 | 하네스 로더(`sub_agents/`), 프롬프트 조립(`consult` 카탈로그·계약), 행동 해석, 서브에이전트 호출·기록, 예시 하네스, 관련 테스트 |

## 전달 정보
명세 경로·버전: docs/agent/tasks/TASK-013/spec.md, 1
기준 커밋: 00cb7c3e7ea23357ecd2071a482df6a249577166
작업 폴더: docs/agent/tasks/TASK-013/
커밋 제목 형식: [TASK-013] <type>: <변경 요약>
기존 미커밋 변경: 없음(이 명세 파일만 로컬에 있음. 이전 작업의 implementation.md·review.md는 .gitignore된 로컬 기록)
직접 확인 방법:
1. 전체 테스트 스위트를 실행한다.
2. `$env:AGENT_HARNESS_DIR = 'harness/sql_review'`로 평가 1문제를 실행한다.
3. 결과 JSON에서 `tool_trajectory`의 `consult` 항목과 `adk_events`의 `subagent_call` event를 확인한다.
4. `harness/baseline`으로 실행했을 때 시스템 프롬프트(`adk_events[0].prompt[0].content`)에 `consult`가 없는지 확인한다.
