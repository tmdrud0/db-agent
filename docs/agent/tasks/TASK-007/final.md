# TASK-007 최종 보고서

작업 ID: TASK-007
명세 버전: 2
기준 코드 버전: 157a57a148bff68612f5b242e06c43a076250db3
대상 코드 버전: 87f7b9f487dc9032105d102fd1fd6840a2b2a253
상태: ready_for_user
요약: fixed 고정 컨텍스트 루프와 모드 선택 경로를 제거해 TASK-006의 9개 도구 선택 루프를 유일한 기본 에이전트로 만들었다. 제거된 모드를 서버(`AGENT_MODE`/`create_app(mode=)`)나 실행기 환경에 남겨 두면 시작 전에 명확한 오류로 거부한다. 사용자 추가 요청(명세 v2)으로 에이전트 모델 호출의 일시 실패를 최대 3회 재시도한다. 완료 조건 AC-1~AC-6을 모두 충족했고 같은 명세 범위·최종 SHA의 리뷰가 pass다.
근거: 아래 코드 위치·검증·리뷰 결과에 직접 기록했다. 조율자가 최종 SHA에서 전체 테스트·diff 검사를 다시 실행해 확인했다.
미확정 사항: 없음.

## 변경 내용과 설계 결정

작업 코드 커밋:
- 1f66d46f5e10bc50ba207824e586013994730620 — `[TASK-007] refactor: make the tool-selecting agent the only mode`
- 6840ff615418491cf1aeee1d1475d0872790ac12 — `[TASK-007] fix: reject a stale AGENT_MODE before starting` (리뷰 R-1)
- 87f7b9f487dc9032105d102fd1fd6840a2b2a253 — `[TASK-007] feat: retry transient model calls up to three times` (사용자 추가 요청, 명세 v2)

누적 변경(`git diff --stat 157a57a 87f7b9f`): 9개 파일, +295 / −1166.

1. **fixed 모드 제거**
   - `agent/loop.py` 삭제(fixed 루프·전용 프롬프트·상수).
   - `agent/model.py`에서 fixed 전용 `SYSTEM_PROMPT`·`ModelReply`·`extract_sql`·`ModelClient.generate_sql` 삭제. tools 루프가 쓰는 `chat`·상수·`ModelError`·`ChatReply`는 유지.
   - `agent/server.py`에서 `DEFAULT_MODE`·`RUNNERS`·fixed 이름을 없애고 `/run_session`이 항상 `agent.tool_loop.run`을 호출한다. `InputError`는 정의 위치인 `agent.session`에서 import한다(타입·422 매핑 무변경).
   - 결정: 모드 선택 인터페이스는 단일 값 가드로 남겼다. 미설정·빈 값·`tools`는 tools로 기동하고, `fixed`·알 수 없는 값은 `ValueError("Unknown agent mode '<값>'; only 'tools' is supported")`로 거부한다. 명시적 fixed 요청을 조용히 tools로 실행하지 않기 위해서다.
   - `/health`는 `agent=deepseek-v4.1-flash-tool-selecting-two-phase`, `mode=tools`, 9개 도구·가격, execute_sql 120초·ask_user 660초 timeout을 그대로 보고한다. 실행기가 오래된 서비스를 재사용하지 않게 하는 기존 식별 계약을 보존한다.

2. **실행기 기본 tools** (`scripts/run-evaluation.ps1`)
   - `-AgentMode`·`$agentNames`·`$env:AGENT_MODE` 설정 제거. `Assert-BaselineAgent`(mode·이름·모델·9개 도구·가격)와 `Assert-Simulator`(Command Code 어댑터·모델·v2·재시도 0·출력 상한 16384·키 존재) 검사를 항상 수행한다.
   - R-1 수정: `Assert-AgentModeEnvironment`가 키 로딩·compose·에이전트 기동 전에 실행된다. `AGENT_MODE`가 비어 있지 않고 `tools`가 아니면 값과 `Remove-Item Env:AGENT_MODE` 안내를 담아 중단하며 값을 지우거나 덮어쓰지 않는다. 이전 실행기가 남긴 `AGENT_MODE=fixed` 때문에 원인 없는 기동 timeout이 나던 문제를 막는다.
   - `run-smoke.ps1`·`run-dev30.ps1`은 수정하지 않았고 이제 옵션 없이 tools 에이전트를 실행한다. `run-dev30-calibrated.ps1`(oracle 전용)은 범위 밖이라 변경하지 않았다.

3. **모델 호출 재시도** (`agent/model.py`, 명세 v2 범위 4)
   - `MAX_RETRIES = 3`: 최초 호출 포함 최대 4회. `ModelClient._complete` 내부에서 처리해 `chat`과 tools 루프의 계약(성공 응답 1건 = 모델 행동 1회, `adk_events` 1건)은 바뀌지 않는다.
   - 재시도: HTTP 408·429·5xx, transport 오류·timeout, JSON 아닌 응답, `choices` 누락·깨진 성공 payload. 제외: 키 부재, 그 밖의 4xx, 빈 content·절단 응답.
   - 소진 시 `ModelError("... (after 4 attempts)")`로 기존 실패 경로(무과금·`failure.stage="model"`·HTTP 502·재실행 없음)를 따른다. 전체 재시도는 세션 기한(`_bounded`) 안에서 끊긴다. backoff는 없다.
   - 사용자 시뮬레이터 내부 모델은 `ModelClient`를 쓰지 않으며 `USER_SIM_MAX_RETRIES`(compose 기본 0)를 유지한다.

4. **테스트 재구성** (158 → 131개)

| 구분 | 테스트 | 처리 |
| --- | --- | --- |
| 삭제 | `tests/test_connection.py` 31개(fixed 루프 동작·fixed 실패 state·세션 계약 중복) | fixed 전용이거나 `test_tool_loop.py`에 같은 세션 계약이 있음 |
| 이전 | 동시 요청 실패 후 무재실행 1개 | `ToolLoopConcurrencyTests.test_simultaneous_requests_after_model_retries_are_exhausted_do_not_replay` |
| 삭제 | `tests/test_model.py` `ExtractSqlTests` 4개 | `extract_sql` 제거 |
| 재구성 | `tests/test_model.py` `generate_sql` 5개 → `chat` 기준 8개 | 요청 계약·키 부재·HTTP 오류/timeout·깨진 payload·빈/절단 응답·키 비노출 + 재시도 |
| 보강 | `tests/test_tool_loop.py` 53 → 55개 | 단일 모드 health, 제거 모드 거부, 동시 실패 무재실행. 모델 실패 시도 횟수 기대값만 4회로 갱신 |
| 추가 | `tests/test_evaluation_env.py` 4 → 7개 | `AGENT_MODE` 가드 허용·거부·환경 보존 |
| 유지 | calibration 31, command_code_simulator 18, dev_set 4, verify_calibration 3, verify_tool_connection 5 | 변경 없음 |

서버 세션 계약(init/reset/재사용·캐시된 결과·init 전 404·입력 오류·동시 요청 무중복·실패 후 무재시도·`.env` 키 로딩)은 `test_tool_loop.py`·`test_model.py`·`test_evaluation_env.py`에서 tools 루프 기준으로 유지된다.

## 설정과 실행 방법

- 에이전트 서비스: 별도 설정 없이 tools 루프로 기동한다. `AGENT_MODE`는 설정하지 않거나 `tools`여야 한다.
- 평가 실행: 모드 옵션이 없다. `CMD_API_KEY`가 `.env`에 필요하고, 시뮬레이터가 Command Code 어댑터로 기동된다. 이제 `run-smoke.ps1`·`run-dev30.ps1`도 같은 조건이 필요하다.
- 이전 셸에 `AGENT_MODE=fixed`가 남아 있으면 `Remove-Item Env:AGENT_MODE` 후 다시 실행한다.

## 완료 조건별 결과

| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `agent/loop.py` 삭제, server·실행기의 fixed 분기·기본값·`-AgentMode` 제거. `git grep -n -i fixed -- agent scripts tests compose*.yml`의 남은 hit는 고정 예산·"prefixed"·고정 30문제 선택·제거 모드 거부 테스트 입력 등 다른 의미뿐. `git grep AGENT_MODE`는 server 가드(`agent/server.py:50`)·실행기 가드(`scripts/run-evaluation.ps1:71-76,141`)·그 테스트만 남음 |
| AC-2 | 충족 | 기본 `create_app()`·빈/`tools` `AGENT_MODE`가 tools로 기동, `/health`가 기존 이름·9개 도구·가격·timeout 보고. `fixed`·알 수 없는 값 ValueError(`test_health_reports_the_tool_selecting_agent`, `test_removed_modes_are_rejected_instead_of_silently_running_tools`) |
| AC-3 | 충족 | 모드 옵션 없는 보정 1문제 smoke(1f66d46) exit 0, 9개 도구·에이전트·시뮬레이터 검증 통과. 파서 검사 4개 스크립트 ok. 잔존 `AGENT_MODE=fixed`는 Docker 시작 전 명확한 오류로 중단(구현자·리뷰어 각각 재현) |
| AC-4 | 충족(v2 기준) | `git diff --quiet 157a57a 87f7b9f -- agent/tool_loop.py agent/tools.py agent/environment.py benchmark/user_sim/command_code_simulator.py` exit 0(조율자 재확인). 동작 변경은 `agent/model.py` 재시도뿐. 503×3 후 성공, 500/timeout/깨진 payload 4회 소진, 401 1회, 키 부재 0회, 소진 후 무과금·`failure.stage=model`·동시 요청 무재실행 테스트 통과 |
| AC-5 | 충족 | 131 tests OK, `git diff --check 157a57a 87f7b9f` 출력 없음. 삭제·이전·유지 목록은 위 표 |
| AC-6 | 충족 | `[TASK-007]` 제목 로컬 커밋 3개, 코드·테스트·스크립트만 포함, 문서·results·vendor·private 미포함, push 없음. 명세 v2·최종 SHA 87f7b9f에 대한 리뷰 pass |

## 검증 결과

| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | 87f7b9f, `git diff --quiet HEAD` 통과 (조율자·리뷰어·구현자 각각 실행) | Ran 131 tests, OK | 전체 계약·회귀 |
| `git diff --check 157a57a 87f7b9f` | 동일 | 출력 없음 | 공백 오류 없음 |
| `git diff --quiet 157a57a 87f7b9f -- agent/tool_loop.py agent/tools.py agent/environment.py benchmark/user_sim/command_code_simulator.py` | 동일 | exit 0 | tools 루프·도구·환경·어댑터 무변경 |
| `git diff --stat 1f66d46 87f7b9f -- agent/` | 리뷰어 | `agent/model.py`만 변경 | 후속 커밋 영향 범위 |
| PowerShell 파서: `run-evaluation.ps1`·`run-smoke.ps1`·`run-dev30.ps1`·`run-dev30-calibrated.ps1` | 최종 코드 | 4개 ok | 구문 오류 없음 |
| `$env:AGENT_MODE='fixed'; .\scripts\run-evaluation.ps1 -Limit 1 -Calibrated` | 최종 코드, 구현자·리뷰어 자식 PowerShell | 즉시 중단, `AGENT_MODE='fixed' is set, but only the tool-selecting agent remains. Run 'Remove-Item Env:AGENT_MODE' and retry.`, 값 보존, 기존 컨테이너 가동 시간 무변화 | R-1 해결 |
| `docker compose ... config -q` (기본·보정) | 최종 코드 | 둘 다 exit 0 | compose 설정 유효 |
| `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task007 -OracleResultName oracle_smoke_task007` | 1f66d46, 추적 변경 없음, TASK-006 30문제 평가 종료 후 | exit 0. oracle reward 1.0(phase1·2 각 1/1), tools reward 0.0 정상 오답, error·failure 없음, 240.2초, 예산 22→0 | 옵션 없는 tools 기본 실행·실제 DB 채점 |

smoke 세부(task `mental_1`): execute_sql 10(공식 SQL 오류 1), ask_user 2, submit_sql 2(공식 실패), 정보 도구 2, 거부 1(무차감). 에이전트 모델 17회·total tokens 223597, 시뮬레이터 내부 모델 4회. 산출물 SHA-256: oracle `ded92560…7f46d`, tools `353c10fb…4f701` (미커밋 `results/`).

검증 코드와 최종 SHA 일치 근거: HEAD = 87f7b9f, 추적 변경 없음 상태에서 131개 테스트·diff 검사를 실행했다. 미추적 파일은 `docs/agent/tasks/TASK-007/`뿐이다. 실제 DB smoke는 1f66d46에서 실행했고, 이후 변경은 실행기 앞단 가드(실제 재현으로 확인)와 모델 실패 경로 재시도(mock 계약 테스트로 확인)다.
검증 범위: 계약 테스트는 최종 SHA, 실제 DB 채점은 1문제 smoke(1f66d46). 최종 SHA smoke·30문제 평가·실제 공급자 오류 재시도는 미실행.

## 리뷰 처리 결과

검토 명세 버전: 1 + 사용자 확정 재시도 범위(= 명세 v2의 범위 4·AC-4 문구와 동일)
검토한 코드 SHA: 87f7b9f487dc9032105d102fd1fd6840a2b2a253
리뷰 판정: pass (1차 1f66d46 changes_requested → 2차 87f7b9f needs_decision → 사용자 확정 후 같은 SHA pass)
판정 근거·독립 검증: 누적·후속 diff 검토, 131 tests OK, `git diff --check` 통과, 재시도가 `agent/model.py`에만 있고 시뮬레이터·실행기에서 `ModelClient` 미사용 확인, `AGENT_MODE=fixed` 실제 재현. 87f7b9f 기준 실제 스택 smoke와 실제 공급자 오류 재시도는 실행하지 않음.
지적 처리:
- R-1(잔존 `AGENT_MODE=fixed`에서 원인 없는 기동 실패): 6840ff6에서 해결. 가드 테스트 3개와 실제 재현 일치.
- D-1(재시도 범위): 사용자가 TASK-007 포함을 확정(선택지 a). 조율자가 spec v2로 범위 4 추가·AC-4 조정을 기록했고, 문구가 리뷰 판정 기준과 같아 코드 재검토는 필요 없다.
- S-1(README·setup 갱신 후보): 명세상 보고서 외 문서 수정 제외. 아래 후속 사항으로 남긴다.

## 남은 제한

- 최종 SHA 87f7b9f의 end-to-end smoke는 재실행하지 않았다. 성공 경로 코드는 smoke한 1f66d46 이후 바뀌지 않았다.
- 재시도에는 backoff가 없고, 재시도 후 성공해도 `adk_events`에 시도 횟수·실패 시도 오류가 남지 않는다(`elapsed_seconds`만 합산). 실제 HTTP 요청 수가 과소 기록될 수 있다. 시도당 timeout 300초×4로 한 행동이 세션 기한(1500초)의 대부분을 쓸 수 있다. 명세 요구는 아니며 필수 조건·계약을 위반하지 않는다.
- TASK-006 결과와 이후 결과는 모델 실패 처리 조건이 달라 비교 시 코드 SHA로 구분해야 한다. 기존 `results/tools_dev30_calibrated_task006.*`는 TASK-007 성능으로 승계하지 않는다.
- `run-smoke.ps1`·`run-dev30.ps1`은 끝까지 실행하지 않았다(파서 검사만). 이제 `CMD_API_KEY`·시뮬레이터 모델 호출이 필요하다.
- smoke 실행 시 compose가 db-environment·user-simulator 컨테이너를 재생성했다(PostgreSQL 볼륨·기존 결과 파일은 그대로).

## 필요한 보완·판단

필수 보완 없음. 후속 후보(사용자 판단):
- 문서: `README.md` 65·71·72행이 삭제된 fixed 구성("모델 호출 1회", `agent/loop.py`)을 설명한다. README·`docs/setup.md`에 래퍼의 `CMD_API_KEY`·시뮬레이터 요구와 재시도 동작 안내가 필요하다.
- 재시도 개선: 시도별 기록(횟수·오류·usage), 429/503 backoff, 재시도 전체 시간 상한.
- 평가: 최종 SHA로 30문제 보정 평가(`-Limit 30 -Concurrency 10 -Calibrated -ResultName tools_dev30_calibrated_task007 -OracleResultName oracle_dev30_calibrated_task007`).

## 직접 확인 방법

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
기대: `Ran 131 tests ... OK`.

```powershell
.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task007b -OracleResultName oracle_smoke_task007b
```
기대: 모드 옵션 없이 9개 도구·시뮬레이터 검증 통과, oracle reward 1.0, tools 결과에 연결 오류 없음(reward는 모델 실행에 따라 달라짐).

## 사용자 결정

결정: 승인
대상: 명세 v2, 최종 코드 87f7b9f487dc9032105d102fd1fd6840a2b2a253
확인 근거: 2026-10-20 조율자 채팅에서 사용자가 final.md 내용 설명을 들은 뒤 "ㅇㅋ 커밋해줘"로 승인
보존 문서: spec.md·final.md
