# TASK-012 구현 보고서

작업 ID: TASK-012
명세 버전: 1
대상 코드 버전: feafe1852dad0cfdf53dc688efc2592f99e08d6f
상태: ready_for_review
요약: 중복 제출 차단과 정보 조회 캐시를 루프에서 제거했다. 리뷰 R-1에 따라 하네스 폴더 안의 링크·junction과 폴더 밖으로 해석되는 항목을 거부한다(후속 커밋 feafe18). 시스템 프롬프트는 코드가 만드는 계약과 하네스 폴더(`system.md`, 선택 `memory.md`, 선택 `tools/<도구>.md`)로 조립하며, 서버 시작 시 한 번 읽고 SHA-256으로 식별한다. 하네스 이름·해시는 `/health`, 세션 상태, 각 `model_call` event, Langfuse 세션 메타데이터에 남는다.
근거: `agent/harness.py`, `agent/tool_loop.py`의 `system_prompt()`·`_record_harness()`, `agent/server.py`, `agent/tracing.py`, `harness/baseline/system.md`, `tests/test_harness.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`.
미확정 사항: 없음. 실제 dev30 평가(AC-10)는 실행하지 않았다.

## 커밋과 검토 범위
기준 커밋: 15977f804d99b8bd914c94c5aa863d8d0f0c8fe1
코드 커밋: feafe1852dad0cfdf53dc688efc2592f99e08d6f
작업 커밋 목록:
- b901a55453dd3cef06856fa125a0e31032b3911f — `[TASK-012] feat: load behavioral guidance from a harness folder`
- feafe1852dad0cfdf53dc688efc2592f99e08d6f — `[TASK-012] fix: reject links and junctions in the harness folder` (리뷰 R-1)
검토 명령: `git diff 15977f804d99b8bd914c94c5aa863d8d0f0c8fe1 feafe1852dad0cfdf53dc688efc2592f99e08d6f --`
R-1 수정만 보기: `git diff b901a55453dd3cef06856fa125a0e31032b3911f feafe1852dad0cfdf53dc688efc2592f99e08d6f --`
현재 코드와 대상 SHA의 차이: 없음. 커밋 후 `git diff HEAD -- agent tests harness`가 비어 있고, 추적 변경은 위 커밋에 모두 들어 있다.
기존 사용자 변경: 없음. `docs/agent/tasks/TASK-012/`(spec.md, 이 보고서)만 미추적 로컬 기록이다.

## 변경 내용

### 1. 루프에서 제거한 행동 규칙
- `agent/tool_loop.py`: `_is_duplicate_submission()`과 제출 시의 차단 분기를 삭제했다. 같은 phase의 같은 SQL도 항상 공식 `/submit`으로 가고 `SUBMIT_COST`(3)가 차감된다.
- `agent/tool_loop.py`: `_cached_result()`와 정보 조회의 캐시 분기를 삭제했다. 같은 도구·인자도 항상 공식 서비스를 다시 호출하고 같은 비용을 차감한다.
- `agent/tools.py`: 중복 판정 전용이던 `submission_key()`와 `_starts_string_constant()`를 삭제했다. `statement_count()`는 `execute_sql` 여러 문장 거부를 위해 그대로 남겼다.
- `state["submit_attempts"]`는 제출 기록으로 계속 남긴다. 이제 루프 판단에는 쓰이지 않으며, 재제출 횟수 분석에 쓸 수 있다.
- `agent/tracing.py`: 캐시·중복 차단 전용이던 tool span metadata의 `cached`·`blocked`와 WARNING 분기를 삭제했다.

### 2. 하네스 파일 형식과 로딩 (`agent/harness.py`)
- 기본 위치: `harness/baseline/`. 환경 변수 `AGENT_HARNESS_DIR`로 바꿀 수 있다. 상대 경로는 저장소 루트(`agent/`의 부모) 기준이다. 하네스 이름은 폴더 이름이다.
- 인식하는 항목은 `system.md`(필수), `memory.md`(선택), `tools/<공식 도구 이름>.md`(선택)뿐이다. 그 밖의 항목은 `README.md`를 포함해 모두 `HarnessError`로 서버 시작을 실패시킨다. `tools/` 안의 비-`.md` 파일, 하위 디렉터리, 공식 9개 도구가 아닌 이름도 실패한다.
- 파일은 바이트로 읽어 엄격한 UTF-8로 디코딩한다. 디코딩 실패는 `HarnessError`다.
- 링크 처리(R-1): `system.md`, `memory.md`, `tools/`, `tools/*.md` 각각이 심볼릭 링크(`is_symlink()`)이거나 junction(`is_junction()`)이면 `HarnessError`다. 해석한 경로(`resolve()`)가 해석한 하네스 루트 아래에 있지 않아도 `HarnessError`다. 끊어진 `tools` 링크도 거부한다. 하네스 루트 자체는 지정된 폴더이므로 링크여도 허용하며, 이때 해시는 실제 폴더를 직접 지정한 경우와 같다. 하드링크는 일반 파일과 구분할 수 없어 검사 대상이 아니다.
- `create_app()`이 하네스를 한 번 읽어 앱에 보관한다. 실행 중 파일을 바꿔도 그 서버의 세션에는 반영되지 않는다(테스트로 고정).
- `system.md`가 공백뿐이면 시작 문단 없이 계약부터 시작한다. `memory.md`가 없거나 공백뿐이면 메모리 섹션을 넣지 않는다. 비어 있는 `tools/*.md`는 카탈로그를 바꾸지 않는다.

### 3. 프롬프트 조립 순서 (`agent/tool_loop.py: system_prompt()`)
1. `system.md` 내용(앞뒤 줄바꿈만 제거)
2. 빈 줄
3. 코드가 만드는 계약: 응답 형식, `catalog(tool_notes)`, 규칙(도구·비용·한도·보상·phase)
4. 빈 줄 + `Harness memory:\n<memory.md 내용>` (내용이 있을 때만)
5. 끝에 줄바꿈 하나

도구 안내는 해당 도구의 카탈로그 줄 바로 아래에 붙인다.

```
- execute_sql: ... Cost: 1
  Usage note:
  <tools/execute_sql.md의 각 줄, 두 칸 들여쓰기>
```

계약 문구는 하네스 내용과 무관하게 항상 코드가 만든 그대로 들어간다. 기준 하네스로 조립한 프롬프트는 task011의 `SYSTEM_PROMPT`에서 정보 조회 캐시 항목과 중복 제출 항목만 뺀 텍스트와 문자 단위로 같다(아래 AC-4). 이 때문에 첫 문단의 역할·전략 문구만 `harness/baseline/system.md`로 옮기고, `ask_user` 항목의 "decide yourself whether and when to ask"는 계약 텍스트에 남겼다. 그 문장을 하네스로 옮기면 규칙 목록 중간에 삽입 지점이 필요해져 명세가 요구한 문자 단위 일치가 구조적으로 깨진다. `ask_user` 응답의 해석 규칙으로 보고 코드에 두었다.

### 4. 하네스 해시
- 인식한 파일의 상대 경로(`system.md`, `memory.md`, `tools/<name>.md`)를 사전순으로 정렬한다.
- 각 파일마다 `상대 경로 UTF-8` + `0x00` + `정규화된 내용 UTF-8` + `0x00`을 SHA-256에 넣는다.
- 내용은 CRLF/CR을 LF로 정규화한 뒤 해시한다. 같은 텍스트면 운영체제의 줄바꿈이 달라도 같은 해시다.
- 존재하지만 빈 파일도 경로와 함께 해시에 포함한다. 파일 경로를 바꾸면 내용이 같아도 해시가 달라진다.
- 기준 하네스 해시: `22e2b82d97a4c33a526e147449dc4302d7021e463d41af9b39668482173b3cba` (`system.md` 195 bytes만 포함).

### 5. 실행 기록
- `/health`에 `harness: {"name": "baseline", "sha256": "..."}`를 추가했다.
- `/init_session`이 만드는 세션 상태와 `run()` 시작 시 `state["harness"]`에 같은 값을 기록한다.
- 각 `model_call` event에도 `"harness": {...}`를 넣는다. **조사 결과:** 벤더 러너(`vendor/.../orchestrator/ainteract.py:127-155`)는 `phase1_completed`, `phase2_completed`, `total_reward`, `budget_remaining`, `dialogue_history`, `tool_trajectory`, `adk_events`만 결과 JSON의 문제별 항목으로 복사한다. 따라서 `state["harness"]`는 결과 JSON 최상위에 남지 않는다. `adk_events`는 복사되므로 각 `model_call` event의 `harness`가 실제 결과 파일에 남는 위치다. `adk_events`의 순서와 `model_call` 의미는 바꾸지 않았다.
- Langfuse 세션 trace metadata에 `harness_name`, `harness_sha256`을 추가했다. `create_app()`이 로드한 값을 `create_tracer()`에 넘긴다. Langfuse를 쓰지 않거나 실패해도 결과에는 영향이 없다(기존 best-effort 경로 그대로).
- `scripts/run-evaluation.ps1`은 수정하지 않았다. `Wait-Service`는 `status`, `Assert-BaselineAgent`는 `service`·`mode`·`agent`·`model`·`tools`만 읽으므로 추가된 `harness` 필드를 무시한다. `/health`의 기존 필드가 그대로임을 테스트로 확인했다.
- 하네스 지정 실행 예:

```powershell
$env:AGENT_HARNESS_DIR = 'harness/exp1'   # 저장소 루트 기준 상대 경로
.\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1 -ResultName 'tools_harness_exp1'
```

`AGENT_HARNESS_DIR`는 에이전트 프로세스가 시작되기 전에 설정해야 한다. 포트 6000에 이미 떠 있는 에이전트를 스크립트가 재사용하면 그 서버가 시작할 때 읽은 하네스가 계속 쓰인다. `/health.harness`로 실제 값을 확인할 수 있다. 절대 경로도 허용한다.

### 6. 테스트 변경
- `tests/test_harness.py` 추가: AC-4 기준 프롬프트 고정, AC-5 조립, AC-6 검증, AC-7 해시 성질, 환경 변수·상대 경로·로딩 시점.
- `test_duplicate_key_keeps_meaningful_sql_changes_submittable`는 삭제하고 `test_same_sql_is_submitted_and_charged_every_time`으로 바꿨다. 중복 키 자체가 없어져 "의미 있는 변경"을 구분할 대상이 사라졌기 때문이다.
- `test_duplicate_sql_is_blocked_per_phase_without_a_charge`는 `test_the_same_sql_is_submitted_again_in_phase_two`로 바꿨다.
- `test_repeated_information_lookup_is_cached_and_still_charged`는 `test_repeated_information_lookup_is_sent_and_charged_again`으로 바꿨다.
- `SubmissionKeyTests`(6개)는 `submission_key()` 삭제와 함께 삭제했다. 대신 SQL 토큰 경계를 쓰는 `ExecuteSqlStatementTests`는 그대로 통과한다.
- `test_action_limit_stops_the_session`의 서비스 호출 기대값을 `["/schema"]`에서 `["/schema"] * 24`로 고쳤다. 캐시가 없어져 24번 모두 공식 서비스를 호출하기 때문이다.
- `adk_events`를 모델 호출로만 세던 단언은 `model_events()` 헬퍼로 유지했다. `model_call` event에 `harness` 필드를 추가한 것 외에 event 순서·내용은 바꾸지 않았다.
- `tests/test_tracing.py`: 가짜 클라이언트 trace metadata에 `harness_name`/`harness_sha256`을 확인하고, lifespan이 `create_tracer()`에 하네스 값을 넘기는지 확인하는 테스트를 추가했다.

## 완료 조건별 근거
- AC-1: `ToolLoopContractTests.test_same_sql_is_submitted_and_charged_every_time` — 같은 SQL(앞뒤 공백만 다른 두 번째 제출 포함)을 두 번 보내면 `/submit`이 두 번 호출되고, 비용이 각각 3, 잔액이 15→12→9로 줄며, `blocked`/`duplicate_sql` 표시가 없다. `test_the_same_sql_is_submitted_again_in_phase_two`는 phase 1과 phase 2에서 같은 SQL을 다시 제출해도 둘 다 채점되고 각각 3코인이 차감됨을 확인한다. 기존 중복 차단 테스트 2개는 위 두 테스트로 대체했다.
- AC-2: `ToolLoopContractTests.test_repeated_information_lookup_is_sent_and_charged_again` — `get_schema`를 두 번 호출하면 `/schema`가 두 번 호출되고, 두 항목 모두 비용 1, 결과가 `SCHEMA_TEXT`, `cached` 키가 없으며, 두 번째 관찰에 캐시 안내가 없고 schema 원문이 들어 있다. 잔액은 18→13(두 조회 2 + 제출 3).
- AC-3: 기존 계약 테스트가 그대로 통과한다. `ExecuteSqlStatementTests`(여러 문장 거부), `test_action_limit_stops_the_session`, `test_session_time_limit_*`, `test_budget_*`, `MultipleActionAndStatementTests`(첫 행동만 실행·무시 안내), `AssistantHistoryTests`(표준 행동 JSON), `test_failed_submission_feedback_retry_and_single_transition`(phase 전환·보상) 모두 통과했다.
- AC-4: `tests/test_harness.py:EXPECTED_BASELINE_PROMPT`에 기대 텍스트를 고정했고 `BaselinePromptTests.test_the_baseline_builds_the_frozen_contract`가 문자 단위 일치를 확인한다. 추가로 `git show 15977f8:agent/tool_loop.py`의 `SYSTEM_PROMPT` AST를 실행해 두 항목을 제거한 텍스트와 `system_prompt(load_harness("harness/baseline"))`을 비교했고 `exact match: True`(old 2784자, new 2384자, 6줄 제거)를 확인했다. 명세의 "캐시 항목 세 줄" 표기와 달리 실제 코드에서는 그 항목이 네 줄(첫 줄 + 연속 3줄)이며, 두 항목 전체(4+2줄)를 제거한 결과가 기대 텍스트와 정확히 같다.
- AC-5: `AssemblyTests` — 임시 하네스의 `system.md`가 프롬프트 선두에 들어가고, `memory.md`가 있을 때만 `Harness memory:` 섹션이 들어가며(없거나 공백이면 없음), `tools/execute_sql.md`의 안내가 해당 카탈로그 줄 아래에 붙는다. 어떤 경우에도 응답 형식, 9개 도구의 이름·인자·비용(`catalog()` 전 줄 포함), 24회·1500초·3코인 문구, 0.7/0.3 보상이 그대로 있음을 확인한다. `system.md`에 계약을 덮어쓰려는 문구를 넣어도 코드 계약이 그대로 남는 테스트도 있다.
- AC-6: `ValidationTests` — 폴더 없음, `system.md` 없음, 알 수 없는 항목(`README.md`, `notes.txt`, `tools/drop_table.md`, `tools/sub/...`, `tools`가 파일인 경우), 잘못된 UTF-8 각각에서 `HarnessError`가 난다. `README.md`는 허용하지 않기로 했고 테스트로 고정했다.
- AC-7: `ToolLoopContractTests.test_health_reports_the_tool_selecting_agent`가 `/health.harness`를, `test_the_harness_is_recorded_in_every_session`이 세션 상태와 `model_call` event의 `harness`를 확인한다. `HashTests`가 같은 내용=같은 해시, 한 파일 내용 변경=다른 해시, 파일 경로 변경=다른 해시, 줄바꿈 차이=같은 해시를 확인한다. Langfuse는 `tests/test_tracing.py`의 가짜 클라이언트 테스트(`test_one_session_records_model_calls_tool_spans_scores_and_phase_two`, `test_lifespan_passes_the_harness_identity_to_the_tracer`)로 확인했다.
- AC-8: `scripts/run-evaluation.ps1`의 `/health` 확인은 `Wait-Service`(`status`)와 `Assert-BaselineAgent`(`service`, `mode`, `agent`, `model`, `tools`의 이름·가격)만 읽으므로 새 `harness` 필드의 영향을 받지 않는다. `test_health_reports_the_tool_selecting_agent`가 기존 필드가 모두 그대로임을 확인한다. 실제 Docker 평가는 실행하지 않았다.
- AC-9: 전체 스위트 196개 통과. 명령과 결과는 아래 표와 같다.
- AC-10(선택): 실행하지 않았다. 미검증으로 남긴다.

## 실행한 검증
| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 커밋 feafe1852dad0cfdf53dc688efc2592f99e08d6f, 추적 변경 없음 | exit 0, 202 tests OK (skipped=3) | R-1 수정 후 전체 스위트 |
| `.\.venv\Scripts\python.exe -m unittest tests.test_harness` | 커밋 전 작업 트리(커밋 내용과 동일) | 24 tests OK (skipped=3) | `LinkTests` 6개 |
| 리뷰어 R-1 재현(`tools/` junction → 폴더 밖 디렉터리) | 커밋 feafe18, PowerShell junction | `rejected: Harness entry tools is a link; ...` | 재현 사례가 거부됨 |
| `.\.venv\Scripts\python.exe -m unittest tests.test_harness tests.test_tool_loop tests.test_tracing` | 커밋 전 작업 트리 | exit 0, 116 tests OK | AC-1~AC-7 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 커밋 전 작업 트리 | exit 0, 196 tests OK | AC-9 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 커밋 b901a55, 추적 변경 없음 | exit 0, 196 tests OK | 최종 코드 SHA 재검증 |
| `git show 15977f8:agent/tool_loop.py`의 `SYSTEM_PROMPT` AST 실행 + 기준 하네스 비교(비커밋 스크립트) | 기준 커밋 15977f8, 현재 코드의 `catalog`·상수 | `exact match: True`, old 2784자, new 2384자, 6줄 제거 | AC-4 독립 확인 |
| `git diff --check` | 커밋 전 | exit 0 | 공백 오류 없음 |
| `.\.venv\Scripts\python.exe -c "from agent.harness import load_harness; ..."` | 커밋 b901a55 | name `baseline`, sha256 `22e2b82d...b3cba`, system.md 195 bytes | AC-7 기준 하네스 |

검증 코드와 최종 SHA 일치 근거: 최초 구현은 커밋 b901a55에서 196개 통과를 확인했다. R-1 수정 후에는 커밋 feafe1852dad0cfdf53dc688efc2592f99e08d6f에서 추적 변경이 없는 상태(`git status --short`에 미추적 작업 문서만 있음)로 전체 스위트를 실행해 202개 통과(skipped=3)를 확인했다. 미추적 `docs/agent/tasks/TASK-012/`는 테스트 실행에 쓰이지 않는다. 실제 모델·Langfuse·DB 서비스에는 전송하지 않았다.

## 미검증 항목과 제한
- R-1의 실제 파일 심볼릭 링크 테스트 3건은 이 Windows 환경에 링크 생성 권한이 없어 건너뛰었다. 같은 분기는 `Path.is_symlink` 대체 테스트와 junction 테스트로 확인했다. 링크를 만들 수 있는 환경에서의 실행은 미검증이다.
- AC-10(선택): 기준 하네스로 dev30 calibrated 평가를 실행하지 않았다. 평균 보상, phase 통과 수, 재제출·반복 조회 횟수, 종료 사유, 토큰 변화는 확인하지 않았다.
- AC-8의 실제 평가 실행: Docker 기반 `run-evaluation.ps1`을 끝까지 실행하지 않았다. 위 코드·테스트 근거는 정적 확인과 `/health` 계약 테스트다.
- Langfuse 실제 전송: 가짜 클라이언트로만 확인했다. 실제 서버에서 `harness_name`/`harness_sha256`이 보이는지는 미검증이다.
- 결과 JSON의 위치: 하네스 정보는 각 문제 항목의 `adk_events[].harness`에 남고, 벤더 러너가 `state["harness"]`를 복사하지 않으므로 문제 항목 최상위 `harness` 필드는 없다. 위 "실행 기록"에 적었다. 결과 JSON만으로 하네스를 확인하려면 `adk_events[0].harness`를 보면 된다(모델 호출이 한 번도 없는 세션은 실제 평가 예산상 발생하지 않는다).
- 캐시 제거로 같은 정보 조회가 공식 서비스를 다시 호출하므로 실제 평가의 벽시계 시간과 서비스 부하가 늘 수 있다. 정량 측정은 하지 않았다.
- `submit_attempts`는 계속 기록되지만 이제 어떤 분기에도 쓰이지 않는다.
- 후속 문서 갱신: README·`docs/setup.md`에 하네스 폴더 형식과 `AGENT_HARNESS_DIR` 사용법, 기준 하네스 위치를 추가하는 것이 좋다. 이번 명세가 보고서 외 문서 수정을 제외해 반영하지 않았다.

## 리뷰 지적 처리
### R-1: 하네스 로더가 링크를 따라가 폴더 밖 파일을 읽음 — 해결(수정 1회)
- 수정 커밋: feafe1852dad0cfdf53dc688efc2592f99e08d6f
- 수정 위치: `agent/harness.py`의 `_check_inside()`를 새로 두고, `_files()`가 인식하는 모든 항목(`system.md`, `memory.md`, `tools/`, `tools/*.md`)을 읽기 전에 검사한다. 모듈 docstring에 링크 거부를 적었다.
- 테스트(`tests/test_harness.py`의 `LinkTests`):
  - `test_a_tools_directory_link_is_rejected`: `tools/`가 폴더 밖 디렉터리를 가리키는 junction(Windows는 `_winapi.CreateJunction`, 그 밖은 `os.symlink`)이면 거부. 이 환경에서 실행·통과.
  - `test_a_link_inside_tools_is_rejected`: `tools/execute_sql.md` 이름의 junction 거부. 실행·통과.
  - `test_a_file_reported_as_a_link_is_rejected_without_link_privileges`: 파일 링크 분기를 권한 없이 확인하려고 `Path.is_symlink`를 한 파일에 대해서만 참으로 바꿔 거부를 확인. 실행·통과.
  - `test_file_links_are_rejected`(`tools/ask_user.md`, `memory.md`), `test_a_linked_system_file_is_rejected`: 실제 파일 심볼릭 링크. 이 환경은 권한이 없어(WinError 1314) `skipTest`로 건너뛰었다(skipped=3). Linux나 Windows 개발자 모드에서는 실행된다.
  - `test_the_harness_root_itself_may_be_a_link`: 루트가 junction이어도 로드되고 해시가 같음. 실행·통과.
- 리뷰어의 재현 명령을 수정 후 코드로 다시 실행해 거부를 확인했다(위 표).
- 정리: 리뷰 재현 중 PowerShell `Set-Content` 인자 순서 오류로 저장소 루트에 생긴 미추적 파일 `SECRET-DIR`, `role`(경로 문자열만 담김)을 확인 후 삭제했다. 커밋에는 들어가지 않았다.

## 필요한 판단
없음. 위 "실행 기록"의 결과 JSON 위치(문제 항목 최상위 `harness` 없음, `adk_events[].harness`에 기록)와 실제 평가 미실행을 조율자에게 알린다.
