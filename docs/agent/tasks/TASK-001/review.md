# TASK-001 리뷰 보고서

작업 ID: TASK-001
명세 버전: 1
대상 코드 버전: c5f8f8aa1dc3236b128a8555df918e7fbce20c4a
상태: changes_requested
요약: 정상적인 Phase 1 생성·제출·종료 흐름과 기존 31개 테스트는 확인했다. 모델 응답의 잘못된 message 타입이 처리되지 않아 같은 세션에서 모델 호출이 반복되고, 기존 서버 재사용 시 요청 설정을 확인하지 않아 이전 토큰 상한·timeout으로 평가할 수 있다. R-1, R-2 수정 후 새 코드 SHA를 재검토해야 한다.
근거: 기준 SHA부터 대상 SHA까지 누적 diff, agent 전체 코드, 테스트, 평가 스크립트와 문서, 공식 러너의 상태·오류 집계 경로, 기존 실제 실행 결과 파일, 독립 계약 검증 및 추가 재현.
미확정 사항: 실제 모델·DB 실행은 이번 리뷰에서 다시 수행하지 않았다. 기존 실제 실행의 코드 SHA 연결은 구현 보고서의 실행 기록을 근거로 한다.

## 검토 범위

검토일: 2026-10-14 (Asia/Seoul)
기준 커밋: 560c04c7c4cb0c42bbcd85e1e34721d59f04157c
검토 코드 커밋: c5f8f8aa1dc3236b128a8555df918e7fbce20c4a
검토 명령: `git -c core.fsmonitor=false diff 560c04c7c4cb0c42bbcd85e1e34721d59f04157c c5f8f8aa1dc3236b128a8555df918e7fbce20c4a --`
검증한 작업 공간 코드 버전: HEAD는 78d68541c6653c3d74776d135463f1afd8cdfd28. 대상 SHA 이후 추적 파일의 차이는 `docs/agent/tasks/TASK-001/implementation.md`뿐이며 제품 코드·테스트·스크립트는 대상 SHA와 일치한다. 리뷰 시작 시 미추적 항목은 `docs/agent/tasks/TASK-001/spec.md`뿐이었다. 두 SHA가 실제 commit 객체임을 확인했다.

제품 코드·테스트·명세·공식 러너·GT는 수정하지 않았다. 원격 작업과 실제 유료 API 호출을 수행하지 않았다.

## 완료 조건별 판정

| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 모델 HTTP 계약 테스트가 엔드포인트·모델·인증·비스트리밍·출력 상한·메시지를 확인한다. 모델 입력은 user_query/message와 db_name에서 구성하며 state의 추가 필드를 보내지 않는다. |
| AC-2 | 충족 | 정상 SQL 및 전체 코드 펜스로 감싼 DML을 그대로 제출한다. 빈 content·잘림은 ModelError이며 제출하지 않는다. 공식 응답은 last_submit과 tool_trajectory에 저장한다. |
| AC-3 | 충족 | 오답·Phase 1 성공의 후속 유무·저예산에 대한 계약 테스트 통과. 후속 문제 있음에서는 phase1_completed=true, phase2_completed=false, task_done=false, loop_stopped=true를 확인했다. phase_transition과 Phase 2 제출은 없다. |
| AC-4 | 미충족 | 정상 및 기존 ModelError/GradingError 경로의 반복·동시 요청·reset 테스트는 통과한다. 그러나 잘못된 message 타입에서는 실패 상태가 저장되지 않아 같은 세션의 두 요청이 모델을 각각 호출한다(R-1). |
| AC-5 | 미충족 | 키 누락·HTTP/timeout·잘못된 JSON·빈 content 등의 기존 테스트는 통과한다. message가 비어 있지 않은 문자열·배열·숫자인 응답은 ModelError 대신 AttributeError/500으로 빠진다(R-1). |
| AC-6 | 미충족 | 31개 테스트, PowerShell 문법 검사, 결과 이름·기록은 확인했다. 기존 서버의 이름·모델만 확인하므로 현 명세의 16384토큰·300초 설정과 다른 서버를 재사용한다(R-2). |
| AC-7 | 충족 — 기존 실행 근거 확인 | 구현 보고서와 기존 smoke 결과의 alien_1에서 모델 기록 1건, SQL이 같은 제출 기록 1건, 비용 3, 공식 passed=false/reward=0, error 없음, Phase 2 제출 없음 확인. 요청 설정은 16384토큰·300초이며 응답 모델도 지정 모델이다. 리뷰에서 실제 호출을 재실행한 판정은 아니다. |

## 수정 요청

### R-1: [P2] 잘못된 message 타입을 ModelError로 처리하고 실패를 고정해야 한다

위치: `agent/model.py:93` (`ModelClient.generate_sql`), 연계 경로 `agent/loop.py:61`의 ModelError 처리.
관련 조건: AC-4, AC-5; 응답 형식 오류를 명시적으로 반환하고 같은 세션에서 외부 요청을 반복하지 않는 계약.

현재 동작과 영향: `choices[0]`은 dict인지 확인하지만 그 안의 `message`는 확인하지 않고 `.get("content")`를 호출한다. `message: "invalid-message"`, `message: ["invalid-message"]`, `message: 1`인 HTTP 200 JSON은 AttributeError를 발생시킨다. 이는 ModelError 처리에 들어가지 않아 state.failure가 저장되지 않는다. 같은 세션에서 `/run_session`을 두 번 호출하면 `[500, 500]`, 모델 호출 2회, 제출 0회가 된다. 외부 응답 형식 오류 한 번으로 세션의 1회 호출 보장이 깨진다.

기대 동작: 잘못된 message 타입을 안전한 ModelError로 분류하고 최초 요청은 명시적인 모델 오류 502를 반환한다. 두 번째 요청도 저장된 오류를 반환하며 모델 총 1회·제출 0회를 유지한다. reset=true 이후에만 새 시도를 허용한다.

재현·검증 방법: 아래 PowerShell 명령은 실제 API·DB를 사용하지 않는다. 대상 SHA와 일치하는 작업 공간에서 실행했다.

```powershell
@'
import asyncio
import httpx
from agent.environment import Environment
from agent.model import ModelClient
from agent.server import create_app

async def main():
    for bad_message in ['invalid-message', ['invalid-message'], 1]:
        calls = {'model': 0, 'grader': 0}
        def model_handler(request):
            calls['model'] += 1
            return httpx.Response(200, json={
                'choices': [{'message': bad_message, 'finish_reason': 'stop'}]
            })
        def grader_handler(request):
            calls['grader'] += 1
            return httpx.Response(200, json={'passed': False, 'message': 'wrong'})
        app = create_app(
            Environment(httpx.MockTransport(grader_handler)),
            ModelClient(httpx.MockTransport(model_handler), api_key='fake-key'),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url='http://agent',
        ) as client:
            await client.post('/init_session', json={
                'task_id': 'review-repro',
                'state': {'user_query': 'Q', 'db_name': 'demo', 'budget_remaining': 18},
            })
            payload = {'task_id': 'review-repro', 'message': 'Q'}
            responses = [await client.post('/run_session', json=payload) for _ in range(2)]
            print(type(bad_message).__name__, [r.status_code for r in responses], calls)
asyncio.run(main())
'@ | .\.venv\Scripts\python.exe -
```

실행 결과(3가지 타입 모두): `[500, 500] {'model': 2, 'grader': 0}`.

필요한 수정: message가 dict인지 검증한 뒤 content를 추출하고, 형식 오류는 ModelError로 통일한다. 잘못된 message 타입에 대해 최초·반복·동시 요청과 reset의 호출 횟수를 검증하는 회귀 테스트를 추가한다.

### R-2: [P2] 기존 서버 재사용 시 토큰 상한과 timeout도 확인해야 한다

위치: `scripts/run-evaluation.ps1:44-46` (`Assert-BaselineAgent`), `agent/server.py:49-55` (`health`).
관련 조건: AC-6; 명세의 기존 서버 재사용 시 실제 모델·설정 확인 요구.

현재 동작과 영향: health는 service·agent·model만 반환하고 스크립트도 이 값만 비교한다. 실제 작업의 초기 구현 커밋 aa6b527에서는 MAX_OUTPUT_TOKENS=2048, REQUEST_TIMEOUT_SECONDS=120.0이었지만 health의 이름·모델은 최종 구현과 같다. 이 버전의 서버가 6000 포트에 남아 있으면 최종 코드를 checkout한 뒤 평가해도 검사에 통과해 재사용된다. 구현 보고서가 실제로 확인한 2048토큰 잘림 문제를 다시 겪거나, 16384토큰·300초 설정의 평가로 잘못 해석할 수 있다.

기대 동작: 기존 서버의 실제 요청 설정이 이번 기준선의 기대 설정과 일치할 때만 재사용한다. 설정이 다르거나 필요한 식별 정보가 없으면 사용자의 서버를 종료하지 않고 명확한 오류로 중단한다.

재현·검증 방법: `git show aa6b527:agent/model.py`로 초기 설정을 확인했다. 평가 스크립트의 AST에서 Assert-BaselineAgent 함수만 로드하고 Invoke-RestMethod를 초기 버전과 동일한 health 객체로 대체했을 때 예외 없이 통과했다. 실제 서버를 기동하거나 전체 평가 스크립트를 실행하지 않았다.

```powershell
$taskTokens=$null; $taskErrors=$null
$taskAst=[System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PWD 'scripts/run-evaluation.ps1'),[ref]$taskTokens,[ref]$taskErrors)
$taskFunction=$taskAst.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-BaselineAgent'
},$true)
Invoke-Expression $taskFunction.Extent.Text
$agentName='deepseek-v4.1-flash-phase1-baseline'
$modelId='deepseek/deepseek-v4.1-flash'
function Invoke-RestMethod {
    param($Uri,$TimeoutSec)
    [pscustomobject]@{
        status='healthy'; service='system_agent'
        agent='deepseek-v4.1-flash-phase1-baseline'
        model='deepseek/deepseek-v4.1-flash'
    }
}
Assert-BaselineAgent 'http://mock-agent'
```

필요한 수정: health에 실제 요청의 토큰 상한·timeout 등 기준선 설정을 추가하고 재사용 검사에서 기대값과 비교한다. 이전 health, 설정 불일치, 현재 설정 일치에 대한 검증을 추가한다. SHA 식별 등 추가 방법은 구현자가 선택할 수 있으며 사용자의 서버를 임의로 종료해서는 안 된다.

## 독립 검증

| 검증 | 결과·환경 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | Windows/Python 3.12 venv, 31 tests, OK, exit 0. 최초 샌드박스 실행은 Windows asyncio의 로컬 socketpair 생성에서 정지해 중단했다. 동일 명령을 자동 승인된 샌드박스 외 실행으로 수행해 통과했다. 테스트는 모의 HTTP만 사용한다. Starlette의 httpx 사용 deprecation 경고가 있었으나 실패는 없었다. |
| R-1 모의 ASGI 재현 | 자동 승인된 샌드박스 외 실행. str/list/int message 모두 HTTP 500 두 번·모델 2회·제출 0회. 실제 네트워크 요청 없음. |
| R-2 AST 함수 분리·모의 health 검사 | 초기 버전 health를 검사 함수가 허용하는 것을 확인, exit 0. 실제 HTTP 요청 없음. |
| PowerShell `Parser::ParseFile` | run-evaluation.ps1, run-smoke.ps1, run-dev30.ps1 모두 syntax OK. |
| `git -c core.fsmonitor=false diff --check <기준 SHA> <코드 SHA>` | 출력 없음, exit 0. |
| 기존 smoke 결과 파일 읽기 | alien_1, 모델 호출 1·제출 1, 생성 SQL=제출 SQL, 비용·budget_used=3, 요청 모델과 응답 모델 일치, 16384토큰·300초. usage의 completion_tokens=7253, reasoning_tokens=7157, elapsed_seconds 약 51.062. 공식 오답은 존재하지 않는 atmospheric_conditions 테이블 참조이며 인프라 오류와 구분된다. |
| 기존 dev30 결과 파일 읽기 | 30/30 문제에서 모델 1회·제출 1회·동일 SQL·비용 3·최종 요청 설정 일치·error 없음. 공식 passed 0/30, Phase 2 성공 0/30. 정답률은 완료 조건으로 삼지 않았다. |

실제 실행 파일은 gitignore 대상이며 리뷰 보고서에 추가하지 않았다. API 키·인증 헤더·GT·테스트 케이스를 읽거나 결과로 출력하지 않았다. 기존 결과 파일은 호출·제출·공식 결과와 설정 확인에만 사용했다. 결과 파일 자체에 코드 SHA가 없으므로 실행 SHA의 귀속은 구현 보고서와 일치하는 기록이라는 한계가 있다. 30문제 oracle Phase 2 실패의 원인 분석은 이번 Phase 1 코드 판정의 근거로 사용하지 않았다.

## 이전 지적 처리

해당 없음(최초 검토). R-1, R-2의 수정·재검토 횟수는 0회다.

## 별도 제안·판단 요청

명세 밖의 추가 기능 요청은 없다. 실패 실행의 관측은 정상 실행보다 제한적이다: model_call 기록은 정상 생성 이후에만 남고, 공식 러너는 502 응답에서 state를 수집하지 않아 오류 결과에 호출 상세가 없다. 후속 작업에서 실패 시 호출·usage·시간을 어디까지 남길지 정리하면 평가 비용과 실패 원인 분석에 도움이 된다. 이 제안 자체를 별도 수정 차단 사유로 삼지는 않았다.
