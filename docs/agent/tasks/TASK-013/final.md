# TASK-013 최종 보고서

작업 ID: TASK-013
명세 버전: 1
기준 코드 버전: 00cb7c3e7ea23357ecd2071a482df6a249577166
대상 코드 버전: 8cd1c6bae0e387611b56222b4b7a2786df4b1944
상태: ready_for_user
요약: 하네스에 서브에이전트 정의 파일(`sub_agents/<이름>.md`)을 둘 수 있고, 그런 하네스에서만 로컬 행동 `consult`가 노출된다. `consult`는 코인 0으로, 공식 도구와 공식 서비스 없이 LLM 호출 한 번으로 서브에이전트의 답을 받아 관찰로 돌려준다. 서브에이전트가 없는 `harness/baseline/`의 프롬프트와 동작은 TASK-012와 같다. 예시 하네스는 `harness/sql_review/`(`sql_reviewer`)다. 필수 완료 조건 AC-1~AC-10을 충족했고, 같은 명세 버전과 코드 SHA에 대한 리뷰 판정은 `pass`다. 선택 항목 AC-11(실제 평가)은 실행하지 않았다.
근거: 아래에 코드 위치, 테스트, 리뷰 결론, 조율자 재확인 결과를 직접 기록했다. 대상 SHA에서 전체 테스트 230개가 통과했다(3개 skip).
미확정 사항: 없음.

## 변경 내용과 설계 결정
작업 코드 커밋: 8cd1c6bae0e387611b56222b4b7a2786df4b1944 `[TASK-013] feat: add harness sub-agents and the consult action`

변경 파일(11개, +1166/−30):
- `agent/harness.py`, `agent/server.py`, `agent/subagent.py`(신규), `agent/tool_loop.py`, `agent/tools.py`, `agent/tracing.py`
- `harness/sql_review/system.md`, `harness/sql_review/sub_agents/sql_reviewer.md`(신규)
- `tests/test_harness.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`

`scripts/`, `vendor/`, 결과·GT 파일은 바뀌지 않았다.

### 1. 서브에이전트 정의(`agent/harness.py`)
- **파일 위치와 이름:** `sub_agents/<이름>.md`. 이름은 `^[a-z][a-z0-9_]{0,39}$`를 따른다. 링크·미인식 항목 거부 규칙은 `tools/`와 같다.
- **머리말 블록:** 첫 줄이 `---`이고, 다음 `---` 줄까지가 머리말이다. 그 뒤 본문이 서브에이전트의 시스템 프롬프트다(strip 후 비면 오류).
  - 각 줄은 `key: value` 형식이다.
  - 허용 키는 `description`(필수, 한 줄)과 `context`(선택, 기본 `task, dialogue`)뿐이다. 다른 키·중복 키·빈 값은 `HarnessError`다.
  - `context` 값은 `task`, `dialogue`, `observations`만 허용한다.
  - YAML 라이브러리는 추가하지 않았다.
- **해시:** 정의 파일도 경로와 정규화된 내용으로 하네스 해시에 들어간다. `/health.harness`에 `sub_agents` 이름 목록이 추가됐다.

### 2. `consult` 행동(`agent/tools.py`, `agent/tool_loop.py`)
- **공식 도구와의 분리:** `CONSULT_SPEC`은 `ALLOWED_TOOLS`(공식 9개) 밖에 있다. 그래서 `TOOL_COSTS`, `tool_metadata()`, `/health.tools`는 바뀌지 않는다.
- **노출 조건:** `parse_response(content, allowed)`가 호출 시점의 행동 집합을 받는다. 루프는 서브에이전트가 있을 때만 `consult`를 넣어 넘긴다. 없으면 `consult`는 `Unknown action`(코인 0)으로 거부된다.
- **인자:** `{"agent", "request"}`. 기존 `validate_args`로 검증한다. 없는 서브에이전트 이름은 코인 없는 거부 관찰이 된다.
- **비용과 한도:** 비용 0, 예산 불변. `consult`를 고른 주 응답은 `model_call`로 1회 센다. 서브에이전트 호출은 `subagent_call` event라 행동 한도 24회에 들어가지 않는다.
- **카탈로그와 계약 문구:** 카탈로그에 `consult` 줄과 서브에이전트 이름·설명 목록이 붙는다. 계약에 규칙 2줄이 붙는다(공식 도구가 아님, 코인 없음, 행동 1회, DB·사용자·채점 접근 불가, 받은 맥락만 봄, 점수 무관). 둘 다 서브에이전트가 있을 때만 붙는다.

### 3. 서브에이전트 입력(`agent/subagent.py`)
- **메시지:** system(정의 본문)과 user 한 개로 이루어진다. user는 `context` 선언 순서의 섹션들 뒤에 `Request:`가 붙는다.
  - `Task:`: DB 이름, phase, phase 1 질문, phase 2라면 후속 질문
  - `Dialogue:`: `ask_user` 질문과 공식 답변
  - `Observations:`: 현재 `consult`를 제외한 직전까지의 대화 기록(`messages[2:-1]`). 실행된 행동 JSON, 관찰, 거부된 원문 응답, 이전 consult 답이 포함된다.
- **읽는 값:** `state`에서는 `db_name`, `current_phase`, 질문, `follow_up_query`, `dialogue_history`만 읽는다. 숨은 `state` 키가 들어가지 않는 것을 테스트로 확인했다.
- **응답 처리:** 응답은 파싱하거나 실행하지 않는다. `Sub-agent '<이름>' answer:` 아래 원문 그대로 관찰로 돌려주고, 잘렸으면 안내를 붙인다.

### 4. 오류·시간·기록
- **시간 제한:** 서브에이전트 호출에도 세션 시간 제한(`_bounded`)이 걸린다. 시간이 다 되면 기존 `session_time_limit`으로 끝난다.
- **호출 실패:** 서브에이전트 `ModelError`는 세션을 끝내지 않는다. 실패 관찰을 돌려주고 `tool_trajectory`에 `kind: model_error`를 기록한다. 주 에이전트의 모델 실패 처리는 바뀌지 않았다.
- **`tool_trajectory`:** `consult` 항목(args, cost 0, 예산 전후, phase, elapsed, result)을 남긴다.
- **`adk_events`:** `subagent_call`(harness, agent, prompt, content, truncated, usage, elapsed, 실패 시 error)을 남긴다.
- **Langfuse:** 세션 trace 바로 아래에 generation `subagent:<이름>`을 남긴다. 실패하면 ERROR로 표시된다.
- **결과 보고서:** 벤더 `orchestrator/report.py`는 모르는 event type을 건너뛴다. 합성 결과로 HTML 생성이 정상임을 확인했다.

### 5. 예시 하네스 `harness/sql_review/`
- `system.md`: 기준 하네스와 같은 3줄 뒤에, 제출 전에 `sql_reviewer`에게 검토를 맡기라는 지침을 붙였다.
- `sub_agents/sql_reviewer.md`: `context: task, dialogue, observations`. 질문 조건, 사용자 답변, 관찰의 정의·스키마·결과를 후보 SQL과 대조해 근거를 인용한 번호 목록으로 답하고, 문제가 없으면 `No problems found.`라고 답한다. 대체 쿼리는 쓰지 않으며, 채점기와 정답은 언급하지 않는다.

## 설정과 실행 방법
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests

# 기준 하네스(consult 없음)
Remove-Item Env:AGENT_HARNESS_DIR -ErrorAction SilentlyContinue
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName task013_baseline_smoke

# 예시 하네스(consult + sql_reviewer)
$env:AGENT_HARNESS_DIR = 'harness/sql_review'
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName task013_sql_review_smoke
```
- `AGENT_HARNESS_DIR`는 에이전트 프로세스가 시작되기 전에 설정해야 한다. 포트 6000의 기존 서버가 재사용되면, 그 서버가 시작할 때 읽은 하네스가 쓰인다.
- 실제로 쓰인 하네스는 `/health.harness`(`name`, `sha256`, `sub_agents`)로 확인한다.
- 서브에이전트는 주 에이전트와 같은 모델 설정(`MODEL_ID`, 출력 한도, 타임아웃, 재시도)을 쓴다.

## 완료 조건별 결과
| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `EXPECTED_BASELINE_PROMPT`는 diff에서 바뀌지 않았고 `BaselinePromptTests`가 통과한다. `test_consult_is_unknown_in_a_harness_without_sub_agents`(Unknown action, 코인·서비스 호출 없음), `test_a_harness_without_sub_agents_has_no_consult_text` |
| AC-2 | 충족 | `SubAgentTests`(이름 규칙, 머리말 없음·미닫힘, `description` 없음/빈 값, 본문 없음, 미지원·중복 키, 잘못된·빈 `context`, 하위 디렉터리·비 `.md`, `sub_agents`가 파일인 경우), `LinkTests`의 sub_agents junction 2건(skip 없이 통과), `HashTests`의 내용·이름 변경 2건 |
| AC-3 | 충족 | `test_sub_agents_add_the_consult_line_rules_and_agent_list`가 문구를 고정한다. `test_health_reports_sub_agents_without_changing_the_tool_set`: `/health.tools`는 공식 9개, `/health.harness.sub_agents`에 이름 |
| AC-4 | 충족 | `ConsultTests` 4건: system=정의 본문, 항목별 포함·제외, phase 2 후속 질문, 이전 consult 답 포함, 숨은 `state` 키 미포함, 다음 주 호출 관찰 반영, 공식 서비스 호출 경로 불변 |
| AC-5 | 충족 | consult 항목 cost 0·예산 불변. `test_consult_counts_one_model_action_and_the_sub_agent_call_none`: consult 24회 → `action_limit_reached`, `subagent_call` 24개, 모델 요청 48개 |
| AC-6 | 충족 | 없는 agent·잘못된 인자는 코인 없는 거부. 모델 실패 시 세션 유지와 `model_error` 기록. 잘림 안내. 시간 초과 시 `session_time_limit` |
| AC-7 | 충족 | `tool_trajectory`·`subagent_call` 테스트, `SubAgentTraceTests` 2건(generation 입력·출력·usage, 실패 ERROR). 기존 `model_call` 생성 코드 불변 |
| AC-8 | 충족 | `ExampleHarnessTests.test_the_sql_review_example_loads_and_reaches_the_prompt` |
| AC-9 | 충족(코드·테스트 근거) | `Assert-BaselineAgent`(`scripts/run-evaluation.ps1:128-145`)는 `/health.tools`의 개수·이름·가격만 본다. 두 하네스 모두 `/health.tools`가 공식 9개임을 테스트로 확인. 실제 스크립트 실행은 미실행 |
| AC-10 | 충족 | 구현자, 리뷰어, 조율자가 각각 대상 SHA에서 230 tests OK(skipped=3) 확인 |
| AC-11(선택) | 미검증 | 실제 dev30 평가 미실행 |

## 검증 결과
| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` (구현자) | 8cd1c6b, Python 3.12.10, 가짜 모델·서비스·Langfuse | exit 0, 230 tests OK (skipped=3) | AC-1~AC-10 |
| 같은 명령 (리뷰어) | 메인 체크아웃, HEAD 8cd1c6b, 코드 변경 없음 | 230 tests OK (skipped=3) | 독립 재실행 |
| 같은 명령 (조율자) | HEAD 8cd1c6bae0e387611b56222b4b7a2786df4b1944, `git diff HEAD -- agent tests harness` 비어 있음 | 230 tests OK (skipped=3), 14.8s | 최종 확인 |
| 머리말 파서 경계 probe (리뷰어, 미커밋) | 임시 하네스 | CRLF 정상, `description: a: b` 허용, 중복 `context` 항목 제거, 빈 `context`·대문자 키·첫 줄 앞 빈 줄·UTF-8 BOM은 `HarnessError` | AC-2 경계 |
| 합성 결과 JSON으로 `orchestrator/report.py` `generate_html()` (구현자) | `subagent_call`·`consult` 포함 | exit 0, HTML 생성 | 보고서 호환 |
| `observations` 크기 측정 (구현자) | 로컬 `results/deepseek_v41_flash_tool_loop_dev30_calibrated.json`, 353 model_call | `messages[2:]` 길이 min 17,069 / median 64,771 / max 78,516자 | 서브 호출 입력 규모 추정 |
| `git diff --check 00cb7c3 8cd1c6b` (리뷰어) | — | 출력 없음 | 공백 오류 없음 |

검증 코드와 최종 SHA 일치 근거: HEAD가 대상 SHA이고 추적 코드 변경이 없다. 미추적 파일은 `docs/agent/tasks/TASK-013/`의 문서뿐이며 테스트에 쓰이지 않는다. skip 3건은 TASK-012부터 있던 Windows 파일 심볼릭 링크 권한 문제(WinError 1314)다.
검증 범위: 가짜 모델·환경을 쓰는 계약 테스트와 로컬 probe다. 실제 모델로 `consult`를 호출하는 것, Langfuse 전송, DB 채점, `run-evaluation.ps1` 실행, AC-11은 하지 않았다.

## 리뷰 처리 결과
검토 명세 버전: 1
검토한 코드 SHA: 8cd1c6bae0e387611b56222b4b7a2786df4b1944
리뷰 판정: pass
판정 근거·독립 검증:
- 리뷰어(별도 리뷰 세션)가 누적 diff 전체와 주변 코드를 읽었다. 주변 코드: `_session_loop`, `_model_call`, `_bounded`, `resolve_query`, `Assert-BaselineAgent`, vendor `report.py`.
- 전체 스위트를 메인 체크아웃에서 재실행했다. 리뷰 worktree에는 `vendor/`가 없어 import 오류 2건이 났고, 판정 근거로 쓰지 않았다.
- 머리말 파서 경계 입력과 `/health.harness` 소비처를 확인했다.
- `messages[2:-1]`이 현재 consult 행동을 올바르게 제외하는 것도 코드로 확인했다.
- 조율자도 diff를 확인했다.
  - `consult` 실행 경로가 `_consult_action()` 안에서 공식 서비스를 부르지 않는다.
  - `EXPECTED_BASELINE_PROMPT`가 바뀌지 않았다.
  - `scripts/`·`vendor/`가 바뀌지 않았다.

지적 처리: 수정 요청 없음. 통과를 막지 않는 제안 4건은 "남은 제한"에 옮겼다.

## 남은 제한
- **AC-11 미실행.** `baseline`과 `sql_review` 비교는 아직 없다. TASK-012의 새 기준선 측정도 미실행이므로, 두 하네스를 같은 조건으로 각각 2회 이상 실행해 함께 측정할 것을 권한다.
- **서브 호출 비용.** 늦은 단계 `consult` 입력은 약 1.7만~7.9만 자(대략 5천~2만 토큰)다. `sql_review`는 제출 전마다 consult를 유도하므로, 문제당 주 에이전트 입력과 비슷한 규모의 토큰이 추가될 수 있다.
  - AC-11에서는 주·서브 토큰을 분리 집계한다(`adk_events`의 `model_call`과 `subagent_call`).
  - 필요하면 후속 작업에서 상한이나 요약을 검토한다.
- **거부된 원문 응답이 서브에이전트에게 간다.** `observations`에는 거부된 주 에이전트의 원문 응답도 들어간다. 여기에는 모델이 지어낸 관찰이 섞일 수 있어, 서브에이전트가 실제 관찰로 오인할 수 있다. 명세와 일치하는 동작이며, 실제 평가에서 문제가 보이면 후속 후보로 둔다.
- **BOM 오류 문구.** UTF-8 BOM으로 저장된 정의 파일은 거부되지만, 오류가 "머리말 없음"으로만 보고된다. 문서화할 때 BOM 없는 UTF-8을 안내하면 된다.
- **호출 횟수 상한 없음.** 명세대로 행동 한도 24회와 세션 시간으로만 제한된다.
- **실제 환경 미검증.** 실제 모델 응답 품질, Langfuse 실제 전송, `run-evaluation.ps1` 실행은 확인하지 않았다.
- **후속 문서.** README와 `docs/setup.md`에 `sub_agents/<이름>.md` 형식과 `consult` 안내를 추가하는 일이 남아 있다(이번 범위 밖).

필수 완료 조건과 유지할 계약에는 영향이 없다.

## 필요한 보완·판단
없음. 선택 사항은 다음과 같다.
- AC-11: `harness/baseline`과 `harness/sql_review`로 dev30 calibrated 평가를 각각 2회 이상 실행한다.

## 직접 확인 방법
1. `.\.venv\Scripts\python.exe -m unittest discover -s tests` → `Ran 230 tests`, `OK (skipped=3)`.
2. `harness/sql_review`로 평가 1문제를 실행한 뒤 다음을 확인한다.
   - `/health.harness.sub_agents`가 `["sql_reviewer"]`이다.
   - 결과 JSON에 `tool_trajectory`의 `"tool": "consult"`(cost 0) 항목과 `adk_events`의 `"type": "subagent_call"` 항목이 있다.
3. `harness/baseline`으로 실행하면 위 두 항목이 없고, `adk_events[0].prompt[0].content`에 `consult`가 없다.

## 사용자 결정
결정: 승인
대상: 명세 버전 1, 최종 코드 8cd1c6bae0e387611b56222b4b7a2786df4b1944
확인 근거: 2026-10-21 조율자 채팅에서 사용자가 ready_for_user 최종 보고서를 확인한 뒤 "승인한다. TASK-013 spec.md와 final.md를 문서 커밋해줘."라고 승인했다. AC-11(선택)은 실행하지 않은 상태로 승인했다.
보존 문서: spec.md, final.md
