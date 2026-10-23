# TASK-016 구현 보고서

작업 ID: TASK-016
명세 버전: 1 (`docs/agent/tasks/TASK-016/spec.md`, 미커밋 로컬 파일)
대상 코드 버전: `47b73064992a76fb38d78a096342dba02bc000d7`
상태: ready_for_review
요약: `AGENT_FLOW=staged`일 때 phase마다 understand → ask → verify → submit 순서를 코드가
강제하는 모드, 단계형 하네스, 결과 JSON 비교 스크립트, 합성 fixture 테스트를 추가했다. 변수가
없거나 `free`이면 프롬프트·행동 집합·`/health`·기록이 기준 SHA와 같다.
근거: `agent/flow.py`, `agent/tool_loop.py`, `agent/server.py`, `agent/tools.py`,
`harness/staged/system.md`, `scripts/analyze_staged_flow.py`, `tests/test_staged_flow.py`
미확정 사항: 없음. AC-10(실제 dev30 2회 이상 실행·비교표)은 사용자가 실행한다.

## 커밋과 검토 범위

기준 커밋: `0c00526c153c34e2caac44913721ecb4f4845411`
코드 커밋: `47b73064992a76fb38d78a096342dba02bc000d7`
작업 커밋 목록: `47b7306` (1개)
검토 명령: `git diff 0c00526c153c34e2caac44913721ecb4f4845411 47b73064992a76fb38d78a096342dba02bc000d7 --`
현재 코드와 대상 SHA의 차이: 없음. 커밋 직후 추적 파일 변경 없음(아래 `git status` 참고).
기존 사용자 변경: `docs/agent/tasks/TASK-015/spec.md`, `docs/agent/tasks/TASK-016/spec.md`
(둘 다 미추적). stage·커밋하지 않았고 코드 커밋에 섞이지 않았다.
브랜치: `exp/staged-flow` (main·exp/oracle-context에 커밋하지 않음).

## 변경 내용

### 1. 단계 규칙 (phase마다 처음부터)

| 단계 | 허용 행동 | 다음 단계 |
|---|---|---|
| understand | 정보 조회 6개 + `plan` | `plan` 실행. 질문이 있으면 ask, 없으면 verify |
| ask | `ask_user` + `plan_done` | 선언한 질문 수만큼 묻거나 phase당 상한 도달, 또는 `plan_done` → verify |
| verify | `execute_sql`, `get_column_meaning`, `get_knowledge_definition`, `reopen`(phase당 1회), `ready` | `reopen` → ask, `ready` → submit |
| submit | `submit_sql` | 통과 시 phase 전환(phase 2는 understand부터), 실패 시 verify |

- `reopen`은 phase당 1회. 2회째와 `ask_user` 상한 초과는 `flow` 거부(코인 0)로 처리한다.
- phase 1 통과 후 `state["current_phase"]`가 2가 되면 `on_phase1_passed()`가 stage를
  understand로 되돌리고 `tested`, 질문 수, 선언 목록을 초기화한다.
- `ready`는 그 phase에서 `execute_sql`을 1회 이상 실행했을 때만 submit으로 이동한다.
  예외: 남은 예산이 `SUBMIT_COST + EXECUTE_SQL_COST`(4)보다 적으면 시험 없이 허용.
  `execute_sql`의 공식 SQL 오류도 "실행 1회"로 센다(서비스 오류·시간 초과는 세션 종료).
- 막힘 방지: 현재 단계의 **유료 공식 행동**(로컬 행동 제외)이 하나도 예산 규칙을 지키며
  실행될 수 없으면, 모델 호출 전에 코드가 submit으로 전환하고
  `[flow notice | next action N | phase P]` 관찰로 알린다.
  oracle-context 조합의 understand처럼 공식 행동이 아예 없는 단계는 전환하지 않는다
  (`plan`이 출구).

### 2. 로컬 행동 이름·인자 (테스트로 고정)

| 이름 | 단계 | 인자 | 기록 |
|---|---|---|---|
| `plan` | understand | `{"questions": ["..."], "assumptions": ["..."]}` (둘 다 배열, 빈 배열 허용) | `state["flow"]["plans"]`, `questions`, `assumptions`, `tool_trajectory` |
| `plan_done` | ask | 없음 | 전환 기록, `tool_trajectory` |
| `reopen` | verify | `{"reason": "...", "questions": ["..."]}` (questions는 1개 이상) | `state["flow"]["reopen"]`, 전환 기록, `tool_trajectory` |
| `ready` | verify | 없음 | 전환 기록, `tool_trajectory` |

- 전부 `cost 0.0`. 사용한 응답은 기존 `_model_actions()`가 세는 모델 행동 1회다.
- `ALLOWED_TOOLS`에 넣지 않았으므로 `/health.tools`와 `tool_metadata()`는 공식 9개 그대로다.
- `consult`처럼 `tool_trajectory`에 `type="tool"`, `cost=0`, `budget_before/after`,
  `phase`, `stage`를 남긴다. 공식 도구 이름과 겹치지 않아 기존 분석 필터에 영향이 없다.
- JSON·DSML·MiMo 모두 `args` 객체 형태로 온 로컬 행동을 기존 `parse_response()`로 읽는다.
  DSML/MiMo에서 인자를 이름별 `parameter`로 보내면 배열을 문자열로만 전달할 수 없어
  `plan`/`reopen`은 거부된다(명세의 "기존 파서로 읽히는지" 확인 결과, `args` 객체 형식은 통과).

### 3. 설정값과 기본값 근거

| 변수 | 값 | 기본값 | 근거 |
|---|---|---|---|
| `AGENT_FLOW` | `free`(또는 없음) / `staged` | 없음=free | 명세. 그 외 값은 `FlowError`로 서버 시작 실패 |
| `AGENT_ASK_LIMIT` | 양의 정수 | 4 | dev30 문제당 평균 모호성 3.17(중요)+0.83(삭제 지식)=4.0. ask_user 1회=2코인이므로 평균 4문항이면 8코인. 상한이 없으면 예산을 질문에 모두 쓰고 제출 기회를 잃는다. |
| `AGENT_PHASE2_RESERVE` | 0 이상의 수 | 0 | 명세: 기본 0, 비교 조건 S+R에서만 3으로 켠다. phase 1의 비제출 행동에만 적용 |

- 상한은 **phase 단위**로 센다(`asked_in_phase`). `reopen`으로 ask에 다시 들어가도
  이미 쓴 횟수는 줄지 않으며, 상한이 소진된 ask에서는 `ask_user`가 `flow` 거부되고
  `plan_done`으로 나간다.
- `ask` 단계 진입 시 목표 질문 수는 `min(선언 질문 수, 남은 상한)`이다.

### 4. 예산 규칙

- 어느 단계든 비제출 행동이 실행 후 `SUBMIT_COST`(3) 미만을 남기면 코인 0으로 거부한다.
- phase 1에서는 `AGENT_PHASE2_RESERVE`가 0보다 클 때 `SUBMIT_COST + reserve` 미만을
  남기는 비제출 행동도 거부한다. 예: reserve=3이면 3.5코인 남은 상태의 `get_schema`(1)는
  거부되고, 7코인에서 6코인이 남는 `get_schema`는 허용된다.
- `submit_sql`에는 예약을 적용하지 않는다. 루프 상단의 `budget < SUBMIT_COST` 검사가
  제출 가능 여부를 이미 보장한다.
- 거부는 공식 서비스 호출 없이 `ActionError` 계열(`FlowRejection`)로 처리한다.
  `event["rejection"]`에 `"stage"`, `"budget"`, `"flow"` 중 하나를 남기고,
  `state["flow"]["rejections"]`에도 종류별 횟수를 기록한다.

### 5. 기록·프롬프트·하네스

- `state["flow"]`: `mode`, `setting`, `ask_limit`, `phase2_reserve`, `stage`, `phase`,
  `transitions`(행동 번호·phase·from·to·reason), `plans`, `questions`, `assumptions`,
  `pending_questions`, `asked_in_stage`, `asked_in_phase`, `tested`, `reopen`,
  `rejections`, `next_stage`가 담긴 로컬 행동 결과.
- 모든 `model_call` event에 `stage`와 `flow={"mode": "staged", "setting": "staged"}`를
  넣는다. 자유 루프에서는 두 필드가 생기지 않는다.
- 시스템 계약의 기존 Rules 뒤에만 단계 규칙을 덧붙이고, catalog 뒤에 로컬 행동 4개를
  별도 절로 붙인다. 관찰 머리말은 staged 모드에서
  `[action N | phase P | stage S | allowed ... | name | cost C | budget B]` 형식이다.
- `harness/staged/system.md`는 baseline 3줄로 시작하고 understand/ask/verify/submit
  지침을 붙인다. 서브에이전트는 없다. DML·DDL도 변경 대상 행을 SELECT로 시험하도록
  verify 지침에 한 줄을 넣었다.
- `create_app()`은 `AGENT_FLOW`를 시작 시 한 번 읽는다. `staged` + 서브에이전트 하네스는
  `FlowError`로 시작 실패한다. staged + oracle-context는 허용하며 understand에는
  `plan`만 남는다.

### 6. 결과 JSON 가시성

- 공식 러너(`orchestrator/ainteract.py`)는 `adk_events`, `tool_trajectory`,
  `dialogue_history`만 결과 JSON에 복사하고 `state["flow"]`는 복사하지 않는다.
- 따라서 결과 JSON에서 단계 전환은 `adk_events[*].stage` 순서와
  `adk_events[*].action.name`으로, `plan` 질문/가정과 `reopen`은 `tool_trajectory`의
  로컬 행동 항목으로, 거부는 `adk_events[*].rejection`/`action_error`로 확인한다.
  전환 목록 자체는 `/run_session` 응답 state의 `flow.transitions`에 남는다.
- 기존 필드의 의미·순서는 바꾸지 않았고, 추가 필드만 생긴다(`stage`, `flow`,
  `rejection`, 로컬 행동 tool_trajectory 항목).

### 7. 비교 분석 스크립트

`scripts/analyze_staged_flow.py`는 결과 JSON만 읽는다. 같은 라벨을 여러 번 주면 조건별
평균과 회차 간 최소~최대를 함께 보여 준다.

- 단계별 행동 수(understand/ask/verify/submit), 단계 위반 거부 수, 예산 규칙 거부 수,
  기타 flow 거부 수, plan 질문 수, 실제 `ask_user` 수, `reopen` 수,
  phase 1 통과 시점의 남은 코인, phase 2에서 행동을 1회 이상 한 문제 수.
- 머리말에 조건별 실행 횟수와 기존 dev30 편차(0.317~0.463)를 적는다.
- 거부 응답은 model_call이지만 실행 행동이 아니므로 단계별 행동 수에서 제외한다.

## 완료 조건별 근거

- AC-1 (모드 선택): `FlowSettingTests` — 없음/`free`/공백은 off, `staged`는 on,
  잘못된 값·숫자는 `FlowError`. `/health.tools`는 `tool_metadata()` 9개,
  staged일 때만 `flow` 필드. `test_health_adds_flow_only_when_the_mode_is_on`,
  `test_an_unknown_value_or_number_stops_the_server`.
- AC-2 (단계 전환): `StageTransitionTests` — 질문 있음 경로, 질문 없음 경로,
  submit 실패 → verify, reopen 1회/2회째 거부, phase 2 understand 재시작,
  phase별 `tested` 초기화. `test_plan_questions_lead_through_ask_verify_and_submit` 등 6개.
- AC-3 (단계 위반): `StageViolationTests` — ask에서 `execute_sql`, verify에서
  `ask_user`, submit에서 `plan`을 거부. 서비스 요청 없음, 예산 불변, 거부도 model_call
  1회, 관찰에 현재 단계·허용 행동 표시.
- AC-4 (제출 전 시험·막힘 방지): `SubmitReadinessTests` — 시험 없는 `ready` 거부,
  3.5코인 예외, 3코인에서 understand → submit 자동 전환과 관찰 통지.
- AC-5 (예산 규칙): `BudgetRuleTests` — reserve 0 경계(4 허용/3.5 거부),
  reserve 3 경계(7 허용/6.5 거부), 제출 예외, reserve 3 통합 시나리오.
- AC-6 (기록): `FlowRecordTests` — `state["flow"]` 필드와 모든 model_call의 `stage`,
  `flow` 설정 요약. `FreeSessionTests` — 자유 루프에는 `flow`/`stage` 필드가 없음.
- AC-7 (조합): `OracleStagedTests` — oracle+staged에서 understand 허용 행동이 `plan`뿐,
  정상 완주, `/health`에 experiment+flow, 위반 시 oracle 행동 목록 유지.
  `SubAgentConflictTests` — `harness/sql_review`와 함께 시작하면 `FlowError`.
- AC-8 (기존 동작 불변·전체 테스트): `FreeModeParityTests`가 baseline 시스템 프롬프트를
  기준 SHA에서 캡처한 문자열과 정확히 비교하고, `_initial_prompt`·`_observation`도 고정.
  전체 스위트 308개 통과(3 skip). 명령·결과는 아래 표.
- AC-9 (실행 경로): 아래 "AC-9 실행 명령"에 조건 F·S·S+R(선택 F+T)의 env·하네스·
  결과 이름(`flow_<조건>_r<회차>`)과 1문제 smoke 확인 방법을 정리했다. 실행은 사용자.
- AC-10 (실행·분석): 사용자 실행. 사용자 요청으로 아래 "AC-10 중간 실행"에서
  DeepSeek S와 Luna A S를 1회씩(30문제, 동시성 15) 실행했지만, 조건별 2회 이상과
  최종 비교표는 아직 아니다.

## 실행한 검증

| 명령·검증 | 실행 조건 | 결과 | 근거 |
|---|---|---|---|
| `.venv\Scripts\python.exe -m compileall -q agent scripts\analyze_staged_flow.py` | 구현 중, Windows Python 3.12 | exit 0 | 문법·import 가능 |
| `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_staged_flow.py"` | 최종 코드, 합성 fixture | 35 tests OK | staged 전용 계약 |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | 최종 코드 | 308 tests OK (skipped=3) | 기존 273 + 신규 35, 회귀 없음 |
| `git diff --check` | 스테이징 전 | 출력 없음 | 공백 오류 없음 |
| staged `/health` 앱 생성 | `AGENT_FLOW=staged`, `AGENT_HARNESS_DIR=harness/staged` | `tools=9`, `flow={mode: staged, setting: staged, ask_limit: 4, phase2_reserve: 0.0}`, harness=staged | AC-1 env 경로 |
| 잘못된 값 시작 | `AGENT_FLOW=bogus` | `FlowError: AGENT_FLOW='bogus' is not supported; use 'free' or 'staged'` | AC-1 실패 경로 |
| `git status --short --untracked-files=all` | 커밋 후 | 추적 변경 없음. 미추적: `docs/agent/tasks/TASK-015/spec.md`, `docs/agent/tasks/TASK-016/spec.md`. `implementation.md`는 `.gitignore`의 로컬 작업 기록 규칙으로 제외 | 작업 산출물만 남음 |

검증 코드와 최종 SHA 일치 근거: 위 테스트는 마지막 코드 수정(ask phase 상한, `STAGES`
정리) 후에 실행했고, 그 상태를 그대로 stage해 `47b7306`으로 커밋했다. 커밋 후 추적 파일
변경이 없어 `47b73064992a76fb38d78a096342dba02bc000d7`의 내용과 검증 대상이 일치한다.
`implementation.md`는 코드 커밋 뒤 작성한 로컬 작업 기록(`.gitignore` 대상)이며 코드에는
영향이 없다.

## AC-9 실행 명령

공통: 프로젝트 루트의 PowerShell, calibrated 스택. `run-evaluation.ps1`은 포트 6000에
에이전트가 이미 있으면 **재사용**하므로, 조건을 바꾸기 전에 에이전트를 반드시 내린다.
(스크립트의 `/health` 검사는 `flow`/`harness`까지 비교하지 않는다.)

```powershell
# 조건 전환 전: 이전 에이전트 종료 (run-evaluation.ps1이 시작한 것은 스크립트가 종료한다)
$listener = Get-NetTCPConnection -LocalPort 6000 -State Listen -ErrorAction SilentlyContinue
if ($listener) { Stop-Process -Id $listener.OwningProcess -Force }

# 공통 모델 (기본값과 같으므로 생략 가능)
$env:AGENT_MODEL_ID = 'deepseek/deepseek-v4.1-flash'
```

### 조건 F — 자유 루프 + `harness/baseline`

```powershell
Remove-Item Env:AGENT_FLOW -ErrorAction SilentlyContinue
Remove-Item Env:AGENT_HARNESS_DIR -ErrorAction SilentlyContinue   # 기본 harness/baseline
Remove-Item Env:AGENT_PHASE2_RESERVE -ErrorAction SilentlyContinue
.\scripts\run-evaluation.ps1 -Calibrated -Limit 30 -Concurrency 10 `
  -ResultName tools_dev30_calibrated_flow_F_r1 `
  -OracleResultName dev30_oracle_calibrated
# 기존 tools_dev30_calibrated_task013_baseline_r1.json을 F 1회분으로 재사용 가능
```

### 조건 S — 단계형 + `harness/staged`, `PHASE2_RESERVE=0`

```powershell
$env:AGENT_FLOW = 'staged'
$env:AGENT_HARNESS_DIR = 'harness/staged'
Remove-Item Env:AGENT_PHASE2_RESERVE -ErrorAction SilentlyContinue
$env:AGENT_ASK_LIMIT = '4'   # 기본값. 생략 가능
.\scripts\run-evaluation.ps1 -Calibrated -Limit 30 -Concurrency 10 `
  -ResultName tools_dev30_calibrated_flow_S_r1 `
  -OracleResultName dev30_oracle_calibrated
```

### 조건 S+R — 단계형 + `harness/staged`, `PHASE2_RESERVE=3`

```powershell
$env:AGENT_FLOW = 'staged'
$env:AGENT_HARNESS_DIR = 'harness/staged'
$env:AGENT_PHASE2_RESERVE = '3'
.\scripts\run-evaluation.ps1 -Calibrated -Limit 30 -Concurrency 10 `
  -ResultName tools_dev30_calibrated_flow_SR_r1 `
  -OracleResultName dev30_oracle_calibrated
```

### 선택 조건 F+T — 자유 루프 + `harness/staged` 지침

```powershell
Remove-Item Env:AGENT_FLOW -ErrorAction SilentlyContinue
$env:AGENT_HARNESS_DIR = 'harness/staged'
Remove-Item Env:AGENT_PHASE2_RESERVE -ErrorAction SilentlyContinue
.\scripts\run-evaluation.ps1 -Calibrated -Limit 30 -Concurrency 10 `
  -ResultName tools_dev30_calibrated_flow_FT_r1 `
  -OracleResultName dev30_oracle_calibrated
```

조건마다 `_r1`, `_r2`로 2회 이상 반복한다(`-ResultName`은 `^[A-Za-z0-9_-]+$`만 허용,
`+`를 쓰지 않는다).

### 1문제 smoke 확인 (AC-9의 사용자 확인)

```powershell
$env:AGENT_FLOW = 'staged'
$env:AGENT_HARNESS_DIR = 'harness/staged'
Remove-Item Env:AGENT_PHASE2_RESERVE -ErrorAction SilentlyContinue
.\scripts\run-evaluation.ps1 -Calibrated -Limit 1 -Concurrency 1 `
  -ResultName flow_smoke -OracleResultName dev30_oracle_calibrated

$r = Get-Content results/flow_smoke.json -Raw | ConvertFrom-Json
# 단계 순서와 실행 행동
$r.results[0].adk_events | Where-Object type -eq 'model_call' |
  Select-Object call_index, stage, @{n='action';e={$_.action.name}},
                @{n='rejected';e={$_.rejection}}, action_error
# plan 질문·가정 (tool_trajectory 항목)
$r.results[0].tool_trajectory | Where-Object tool -eq 'plan' |
  Select-Object phase, stage, @{n='result';e={$_.result}}
```

`rejected`가 채워진 model_call은 `tool_trajectory`에 항목이 없고 다음 호출의 예산도
줄지 않는다(코인 0). `stage`는 understand → ask/verify → submit 순서로 바뀐다.

### 비교표

```powershell
.venv\Scripts\python.exe scripts\analyze_staged_flow.py `
  --run F=results/tools_dev30_calibrated_task013_baseline_r1.json `
  --run F=results/tools_dev30_calibrated_flow_F_r2.json `
  --run S=results/tools_dev30_calibrated_flow_S_r1.json `
  --run S=results/tools_dev30_calibrated_flow_S_r2.json `
  --run SR=results/tools_dev30_calibrated_flow_SR_r1.json `
  --run SR=results/tools_dev30_calibrated_flow_SR_r2.json
```

## AC-10 중간 실행 (사용자 요청, 1회 파일럿)

실행 조건: `scripts/run-evaluation.ps1 -Calibrated -Limit 30 -Concurrency 15`,
코드 `47b7306`, `AGENT_PHASE2_RESERVE` 없음(0), `AGENT_REASONING_EFFORT` 없음.
두 실행 모두 30/30 완료, `results[*].error` 0. Luna A는 `AGENT_MODEL_ID=gpt-6-luna`,
`AGENT_ORACLE_CONTEXT_DIR=private/oracle_context/A`, `AGENT_HARNESS_DIR=harness/oracle_context`.
DeepSeek S는 `AGENT_HARNESS_DIR=harness/staged`.

| 조건 | 결과 파일 | 평균 보상 | P1 | P2 | 첫 P1 제출 통과 | P1 통과 시 코인 | P2 행동 문제 | 입력 토큰/문제 | 초/문제 |
|---|---|---|---|---|---|---|---|---|---|
| F DeepSeek (기존 r1) | `tools_dev30_calibrated_task013_baseline_r1.json` | 0.393 | 13 | 9 | 0.33 | 7.19 | 12 | 141,970 | 144.2 |
| S DeepSeek | `tools_dev30_calibrated_flow_S_r1.json` | 0.507 | 17 | 11 | 0.47 | 6.62 | 13 | 268,893 | 191.3 |
| F Luna A (기존 r1) | `tools_dev30_calibrated_gpt6luna_oracle_context_A_r1.json` | 0.477 | 17 | 8 | 0.40 | 6.41 | 12 | 51,085 | 184.4 |
| S Luna A | `tools_dev30_calibrated_gpt6luna_staged_oracle_context_A_r1.json` | 0.460 | 15 | 11 | 0.43 | 8.60 | 14 | 80,644 | 148.8 |

단계 지표(S 실행만):

| 지표 | S DeepSeek | S Luna A |
|---|---|---|
| understand / ask / verify / submit 행동 수(문제당) | 5.30 / 2.17 / 7.93 / 1.90 | 1.47 / 1.57 / 6.80 / 2.50 |
| 단계 위반 거부(문제당) | 1.27 | 0.03 |
| plan 질문(문제당) | 2.60 | 1.03 |
| ask_user(문제당) | 1.40 | 1.57 |
| reopen(문제당) | 0.27 | 0.53 |
| 예산 규칙 거부 | 0 | 0 |
| 기타 flow 거부 | 0 | 0.07 |
| stop: action_limit_reached | 5 | 0 |
| stop: budget_exhausted_before_submit | 14 | 19 |
| stop: phase2_completed | 11 | 11 |

관찰(1회 파일럿, 결론 아님):

- DeepSeek S는 같은 모델의 F(r1)보다 평균 보상 +0.113, P1 +4, 첫 제출 통과 +0.14였다.
  대신 5문제가 24행동 한도에서 끝났고(그중 4문제 보상 0), 입력 토큰이 약 1.9배,
  문제당 시간이 +33%였다.
- Luna A S는 F(r1) 대비 -0.017로 회차 편차 안이다. P1은 -2, P2는 +3, P1 통과 시 남은
  코인은 +2.19였다. 단계 위반은 0.03/문제로 DeepSeek(1.27)보다 훨씬 적다.
- DeepSeek S의 위반 38회는 verify에서 `submit_sql` 14회, ask/understand에서
  `execute_sql` 20회 등 "먼저 시험·제출하려는" 시도다. Luna는 reopen 재사용 2회와
  submit 1회뿐이다.
- oracle-context A는 GT로 고른 컨텍스트를 주는 진단 모드라 Luna A 수치는 에이전트
  성능이 아니다. F/S 비교도 같은 진단 조건 안에서만 해석한다.
- 기존 dev30 편차(0.317~0.463)와 1회 실행 조건 때문에 어느 차이도 결론으로 쓰지 않는다.
  조건별 2회 이상과 `analyze_staged_flow.py`의 회차 범위가 필요하다.

## 미검증 항목과 제한

- AC-10 전체(조건별 2회 이상 실행과 최종 비교표)는 아직이다. 위 파일럿은 1회씩이며
  실제 모델·DB·사용자 시뮬레이터를 호출한 유일한 검증이다. 단위 테스트는
  `httpx.MockTransport`와 스크립트 모델만 쓴다.
- 결과 JSON에는 `state["flow"]`가 복사되지 않는다(러너 화이트리스트). 전환 목록은
  `/run_session` 응답 state에서, 단계·질문·거부는 결과 JSON의 `adk_events`와
  `tool_trajectory`에서 확인한다. 이는 기존 러너를 수정하지 않기 위한 선택이다.
- DSML/MiMo가 인자를 이름별 `parameter`로 보내는 형태에서는 배열 인자(`plan`,
  `reopen`)가 문자열로 도착해 거부된다. `args` JSON 객체 형태는 통과한다(테스트 고정).
  실제 모델이 어느 형태를 쓰는지는 smoke에서 확인해야 한다.
- 24회 행동 한도: DeepSeek S 파일럿에서 30문제 중 5문제가 24회에서 종료됐다(그중 4문제
  보상 0). 로컬 행동과 단계 위반 응답도 모델 행동으로 세므로 자유 루프보다 한도에 닿는
  문제가 늘었다. 한도 계산 자체는 바꾸지 않았고, 이 수치는 1회 실행이다.
- `run-evaluation.ps1`의 `/health` 검사는 `flow`/`harness`를 비교하지 않는다. 조건 전환
  시 에이전트를 내리는 절차를 위에 적었다. 러너 스크립트 자체는 명세 범위 밖이라
  수정하지 않았다(후속 문서/스크립트 갱신 후보).

## 리뷰 지적 처리

해당 없음(최초 구현).

## 필요한 판단

없음.
