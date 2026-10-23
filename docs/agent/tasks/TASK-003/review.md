# TASK-003 리뷰 보고서

작업 ID: TASK-003
명세 버전: 1
대상 코드 버전: 5f881812347dc26d0a30165be365da9f5d60c048
상태: pass
요약: 정상적인 고정 정보 조회·조건부 두 단계 풀이·코인 경계·캐시 동작과 기존 78개 테스트를 확인했다. 최초 R-1/R-2는 고정된 공식 서비스에서 해당 응답이 발생할 경로를 입증하지 않고 모의 응답만으로 필수 수정으로 분류한 지적이었다. 2026-10-15 생산자 코드와 공식 러너 경로를 다시 확인해 두 지적을 철회하고, 동일 코드 SHA에 대한 판정을 pass로 정정한다. 코드 수정으로 해결됐다는 뜻이 아니다.
근거: 기준/구현 SHA의 누적 diff, agent와 평가 스크립트 전체 코드, 관련 공식 서비스 코드, 기존 계약 테스트, 추가 모의 재현, 실제 smoke 결과 파일의 호출·입력·비용·공식 결과 확인.
미확정 사항: 실제 Phase 2 모델/DB 경로는 기존 실행에서 진입하지 않았으며 이번 리뷰에서도 실행하지 않았다. 명세가 허용한 제한으로 기록하며, 이를 별도 수정 차단 사유로 삼지 않는다. 응답 생산자나 운영 계약이 달라지는 환경까지 확인한 판정은 아니다.

## 검토 범위

검토일: 2026-10-15 (Asia/Seoul)
기준 커밋: 05b9fa1e7aac7fd43acde3c2a41bd0c83e808e87
검토 코드 커밋: 5f881812347dc26d0a30165be365da9f5d60c048
검토 명령: `git -c core.fsmonitor=false diff 05b9fa1e7aac7fd43acde3c2a41bd0c83e808e87 5f881812347dc26d0a30165be365da9f5d60c048 --`
검증한 작업 공간 코드 버전: HEAD e77686cc958c5de904a97f420aae9959675dc400.

작업 공간의 제품 코드·테스트·스크립트는 대상 SHA와 일치한다. 대상 SHA 이후 추적 파일 차이는 TASK-003/implementation.md뿐이며, 검토 시작 시 작업 트리는 깨끗했다. 기준/대상 SHA 모두 commit 객체임을 확인했다. 누적 diff에는 명세 문서와 제품 코드·테스트·스크립트 6개가 포함된다.

제품 코드·테스트·명세·기존 결과 파일은 수정하지 않았다. API 키·GT를 읽거나 모델 호출을 반복하지 않았고, 기존 서버를 종료하거나 스택을 전환하지 않았다. TASK-001/002의 보류 항목은 자동 승계하지 않았다.

### 2026-10-15 판정 정정 근거

사용자가 R-1의 실제 발생 가능성을 재확인해 달라고 요청해 응답 생산자를 추적했다.

- 공식 `db_environment/server.py:335`, `:344`, `:379-385`는 각각 schema·column_meanings·knowledge 키를 가진 dict를 반환한다. 빈 지식도 최상위 배열이 아니라 knowledge 값에 JSON 문자열로 들어간다.
- 공식 `user_simulator/server.py:155-161`의 전환 성공은 항상 status=ok, phase=2인 dict다. 미초기화는 HTTP 404이고 처리되지 않은 예외는 오류 상태 응답이다. 에이전트 `_post`는 raise_for_status를 JSON 처리 전에 실행해 HTTP 오류를 GradingError로 처리한다. 연결 오류도 이미 처리한다.
- TASK-002 보정 wrapper는 DB 생성/reset 함수를 감싸고 /calibration 경로를 추가하며 위 응답 핸들러를 변경하지 않는다. 최상위 배열을 반환하도록 바꾸는 middleware/exception handler도 확인되지 않았다.
- 공식 `db_environment/server.py:282-298`는 Phase 1 성공에 후속 문제가 있으면 `_current_phase=2`를 설정한 후 phase_completed=1을 반환하고, 다음 성공은 phase_completed=2를 반환한다. 공식 러너는 task를 초기화하고 세션 실행을 한 번 요청한다. 이번 범위에서 동일 task 상태를 외부에서 교란하는 경로는 제시하지 못했다.

따라서 기존 모의 검증은 계약을 벗어난 응답을 주입했을 때의 방어 한계를 보여줄 뿐, 현재 고정 서비스/공식 러너 환경의 요구사항 위반을 입증하지 못한다. 추가 방어 검사는 선택 가능한 보완이며 이번 작업의 필수 수정으로 요구하지 않는다. 기존 테스트 결과와 실제 smoke 확인 근거는 그대로 유효하다.

## 완료 조건별 판정

| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 정상 응답에서 세 경로에 task_id만 보내고 각 1회 조회·각 1코인 차감을 확인했다. 각 응답 문자열 전체가 프롬프트에 포함되며 임의 state 필드와 로컬 정답 입력이 없다. 빈 정보 문자열도 그대로 사용한다. |
| AC-2 | 충족 | Phase 1 오답·후속 없는 성공의 1회 종료와 SQL·공식 응답·보상·상태 기록을 확인했다. 공식 초기화/제출 경로에서 첫 성공은 Phase 1에 대응한다. R-2의 반대 모의 응답을 필수 계약으로 취급한 판정을 철회했다. |
| AC-3 | 충족 | 조회 각 1회·모델/제출 각 2회·전환 1회, Phase 2 프롬프트와 실패 시 Phase 1 보상 유지가 모의 검증됐다. 공식 서비스도 후속 단계의 성공 순서 [1,2]를 보장한다. 실제 Phase 2 실행은 아래 제한과 같이 미검증이다. |
| AC-4 | 충족 | 0·2·3·4·6·9코인 입력, 조회 중 중단·제출 전 중단·Phase 2 전 중단, 정확한 비용 경계 테스트 통과. 시도하지 않은 행동의 호출/차감 없음, 음수/무료 제출 없음, 모델 실패 시 제출 비용 미차감 확인. |
| AC-5 | 충족 | 기존 78개 테스트와 지원되는 오류·reset·정상 종료 캐시·동시 요청 테스트는 통과했다. 고정 서비스가 최상위 배열을 반환할 경로를 입증하지 못했으므로 R-1을 근거로 미충족 처리한 판정을 철회했다. |
| AC-6 | 충족 — 기존 실제 실행 근거 확인 | 보정 입력 mental_1의 기존 결과에서 전체 조회 3회 → 모델 1회 → 제출 1회 → 공식 오답 수신을 확인했다. 비용 6, 잔액 16, error 없음. 실제 Phase 2 미실행은 명세상 허용되며 모의 검증과 구분했다. 이번 리뷰의 실제 유료 재실행을 뜻하지 않는다. |
| AC-7 | 충족 | 구현 보고서의 SHA·명령·환경·설정·횟수·공식 결과·제한 확인. 입력 해시와 실제 모델/도구 기록이 일치한다. 제품 커밋 6개 파일에 금지 파일은 포함되지 않는다. |

## 수정 요청

없음. 아래는 최초 지적과 모의 재현의 이력을 보존한 것이며 현재 수정 요청이 아니다.

### R-1: 철회 — 조회·전환의 최상위 JSON 타입 검사

위치: `agent/environment.py:41-45`, `agent/environment.py:59-64`.
관련 조건: AC-5; 정보 조회의 응답 계약 오류를 명시적으로 처리하고 실제 시도 비용·결과를 기록하는 계약.

현재 동작과 영향: 두 경로는 `_post`의 JSON 결과가 dict인지 검사하지 않고 `.get`을 호출한다. `/schema`가 HTTP 200, JSON `[]`를 반환하면 AttributeError가 발생한다. `/run_session`은 502의 라벨이 붙은 Benchmark service error 대신 500을 반환한다. 직접 loop 상태를 확인하면 1코인을 차감했지만 tool_trajectory에는 그 시도가 없고 failure도 기록되지 않는다. `/phase_transition`의 배열 응답도 같은 예외로 빠져 지원되는 transition 오류 처리에 들어가지 않는다. 그 경우 Phase 2 생성은 하지 않지만 오류 출처 기록이 누락된다.

기대 동작: JSON 객체가 아닌 조회·전환 응답을 해당 서비스 라벨이 붙은 GradingError로 반환한다. 기존 information/transition 오류 처리 경로를 거쳐 다음 단계로 진행하지 않고, 조회 시도는 비용과 오류를 기록한다. 새로운 영속화나 임의 예외의 자동 재시도 보장을 요구하는 것이 아니다.

재현·검증 방법: 아래 공통 재현 명령의 array_schema_http 결과는 500이다. 추가 직접 loop 재현에서도 배열 schema는 AttributeError, budget 18→17, tool_trajectory=[], failure 없음이었다. 배열 transition은 Phase 1 보상 0.6과 성공을 유지하지만 AttributeError, failure 없음으로 끝났다. 모두 모의 HTTP이며 외부 요청은 없다.

최초 수정 제안(철회): `.get` 전에 최상위 응답 타입을 검증하고 GradingError로 통일한다. 현재 고정 서비스의 반환 경로상 필수 수정으로 요구할 근거가 부족하다.

### R-2: 철회 — 성공 응답의 단계 불일치 방어

위치: `agent/loop.py:210-215`, `agent/loop.py:240-244`.
관련 조건: AC-2, AC-3; 공식 제출 응답을 기준으로 단계 성공·전체 완료·보상을 기록하는 계약.

현재 동작과 영향: Environment.submit은 성공 단계가 1 또는 2인지만 확인한다. loop는 응답의 실제 phase_completed를 확인하지 않고 첫 제출 성공이면 Phase 1, 두 번째 제출 성공이면 Phase 2로 확정한다. 첫 제출에 passed=true, phase_completed=2를 반환하면 공식 응답과 달리 phase1_completed=true, phase2_completed=false, task_done=true가 된다. 두 제출 모두 phase_completed=1인 경우에도 phase2_completed=true와 task_done=true를 기록하고 Phase 2 점수로 집계한다. 이전 코드는 적어도 응답 단계에 따라 성공 플래그를 선택했으나 이번 코드는 호출 순서로 대체했다.

기대 동작: 현재 풀이 단계와 공식 성공 단계가 일치할 때만 해당 성공·보상·완료를 적용한다. 단계가 어긋나는 응답은 명시적인 채점 계약/상태 오류로 중단하고 다음 모델·전환·제출로 진행하지 않아야 한다. Phase 2 응답 불일치에서는 이미 확인한 Phase 1 성공과 보상은 유지한다.

재현·검증 방법: 아래 명령에서 첫 제출 phase2는 HTTP 200·phase1_completed=true·task_done=true, 두 번째 제출 phase1은 HTTP 200·phase2_completed=true·task_done=true였다. 정상 공식 서비스에서 이 불일치를 실제 관찰했다는 주장이 아니라, 공식 응답과 달라도 성공을 확정하는 새 상태 처리의 재현이다.

최초 수정 제안(철회): 성공 처리 전에 기대 단계와 phase_completed를 대조하고 불일치를 GradingError로 처리한다. 현재 공식 초기화/단계 전환 경로에서는 반대 응답의 발생 근거가 부족하며 필수 수정으로 요구하지 않는다.

### 공통 재현 명령

프로젝트 루트에서 실행한다. 기존 모의 테스트 fixture만 사용하며 제품 코드와 테스트를 수정하지 않는다.

```powershell
@'
import sys
sys.path.insert(0, 'tests')
from fastapi.testclient import TestClient
from test_connection import SessionContractTests, PHASE1_WITH_FOLLOW_UP, PHASE2_PASS

t = SessionContractTests(); t.setUp()
t.info_payloads = {'/schema': []}
t.client = TestClient(t.app, raise_server_exceptions=False)
t.initialize(); print('array_schema_http', t.execute().status_code)

t = SessionContractTests(); t.setUp()
t.grader_payloads = [dict(PHASE2_PASS)]
t.initialize(); r = t.execute(); s = r.json()['state']
print('first_submit_phase2', r.status_code,
      {k: s[k] for k in ['phase1_completed', 'phase2_completed', 'task_done', 'stop_reason']},
      'official_phase', s['last_submit']['phase_completed'])

t = SessionContractTests(); t.setUp()
t.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), {**PHASE2_PASS, 'phase_completed': 1}]
t.initialize(); r = t.execute(); s = r.json()['state']
print('second_submit_phase1', r.status_code,
      {k: s[k] for k in ['phase1_completed', 'phase2_completed', 'task_done', 'stop_reason']},
      'official_phase', s['last_submit']['phase_completed'])
'@ | .\.venv\Scripts\python.exe -
```

## 독립 검증

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`: Windows/Python 3.12 venv, 78 tests, OK, exit 0. Windows asyncio의 로컬 소켓을 사용하므로 자동 승인된 샌드박스 외 실행을 사용했다. 기존 Starlette/httpx deprecation 경고는 있었으나 실패는 없었다.
- 위 공통 재현 명령: R-1의 HTTP 500, R-2의 공식 단계와 불일치하는 성공 상태를 확인했다.
- `.runtime-tmp/task003-review/repro.py`: gitignore된 일시 모의 검증 코드. 배열 schema·배열 transition·첫 제출 phase2·두 번째 제출 phase1·정상 두 단계의 다섯 경로를 직접 loop로 확인했다. 정상 [1,2]에서는 공식 0.6+0.15=0.75를 합산하고 비용 9·조회 3/모델 2/제출 2/전환 1로 완료했다.
- `git -c core.fsmonitor=false diff --check <기준 SHA> <구현 SHA>`: 출력 없음, exit 0.
- PowerShell Parser::ParseFile: run-evaluation.ps1, run-dev30.ps1 모두 syntax OK. Calibrated 선택 시 compose override와 기본 보정 입력을 사용하고, 미선택 시 원본 compose/입력을 사용하는 분기를 확인했다. 새 모델 평가나 스택 전환을 실행하지 않았다.
- 공식 DB 서비스의 /knowledge가 문제별 가시성 필터를 거친 문자열을 반환하고 공식 사용자 시뮬레이터의 /phase_transition이 status=ok를 반환하는 계약을 로컬 고정 vendor 코드로 확인했다. vendor SHA는 451fe2c3518ee1cf908d8139e2913483bd519381, 작업 트리 clean이다.

### 기존 실제 실행 근거 확인

`results/deepseek_v41_flash_two_phase_calibrated_smoke.json`과 oracle 결과를 읽었다. 보정 입력 private/lite30.calibrated.jsonl의 SHA256은 55ca576ea1c2942b53aab5700c781c50e4cf1660dff2b25c9fdf24ac835de3ce로 구현 실행의 입력과 일치한다.

| 항목 | 확인한 결과 |
| --- | --- |
| 문제·환경 | mental_1, DB mental, 보정 입력, 구현 보고서상 동시성 1의 보정 스택 실행 |
| 정보 조회 | get_schema, get_all_column_meanings, get_all_knowledge_definitions 각 1회. 응답 길이는 12909·16087·24479자. 전체 문자열이 54194자 모델 프롬프트에 모두 포함됨. |
| 모델 | phase=1 기록 1건. 요청/응답 모델 deepseek/deepseek-v4.1-flash, timeout 300초, 출력 상한 16384. usage prompt=14171, completion=7617, total=21788; 소요 약 41.687초. |
| 제출 | 1회, 모델 SQL과 제출 SQL 동일, 공식 passed=false·reward=0·phase_completed=null. 메시지 SQL failed Phase 1. Your SQL is not correct. |
| 비용 | 조회 각 1: 22→21→20→19, 제출 3: 19→16. budget_used=6, budget_remaining=16. |
| 집계 | phase1_passed=false, phase2_passed=false, total_reward=0, error 없음. 추가 Phase 2 모델/제출 기록 없음. |
| Oracle 대조 | 같은 mental_1의 두 단계 성공, 평균 보상 1.0, error 없음. |

실제 실행의 코드 SHA·동시성·보정 서비스 기동 귀속은 구현 보고서를 근거로 하며, 결과 파일만으로 코드 SHA를 독립 증명한 것은 아니다. 실제 Phase 2·30문제 모델 평가·원본 스택 평가·다른 동시성·장기 실행은 재검증하지 않았다. GT·API 키·인증 헤더·기존 결과를 보고서나 커밋에 복사하지 않았다.

## 이전 지적 처리

TASK-003 최초 검토 후 동일 SHA의 지적 타당성을 재확인했다. R-1과 R-2 모두 구현 수정 횟수 0회, 판정 재검토 1회, 철회다. 사용자가 결함을 수용해 면제한 것이 아니라 리뷰어가 생산자 코드의 보장과 실제 발생 경로를 다시 확인해 필수 수정 근거가 부족하다고 정정한 것이다. 다른 TASK의 같은 번호와는 별도 지적이다.

## 별도 제안·판단 요청

없음. README/setup의 이전 기준선 설명, 기존 서버의 설정 전체 자동 검증, 사용자 지정 보정 ConfigPath, 기존 모델 message 타입 지적은 이번 범위의 수정 요청에 포함하지 않았다.
