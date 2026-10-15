# TASK-002 리뷰 보고서

작업 ID: TASK-002
명세 버전: 1
대상 코드 버전: 377db5550bb5a32ec74d6463ddc1395d233a3e2d
상태: changes_requested
요약: 보정 자체의 효과는 독립 실제 DB 검증으로 확인했다. 원본 4건의 실패, 보정 4건의 최초 제출 통과, 의미 검증 29건, 동시성 10의 30문제 반복 통과가 모두 재현됐다. 다만 검증 wrapper의 사용자 지정 설정이 데이터 생성·DB 준비에 전달되지 않고, 설정 검증을 통과한 SQL 생략 시나리오가 실행 중 KeyError로 중단된다. R-1, R-2 수정 후 새 코드 SHA를 재검토해야 한다.
근거: 기준 SHA부터 대상 SHA까지 누적 diff, 보정 코드·테스트·스크립트, 공식 DB 서비스의 생성·reset·채점 경로, private 보정 설정과 원본/보정 입력 비교, 독립 계약 테스트 및 격리 task DB 실제 채점.
미확정 사항: 기본 TASK-002 설정의 보정 효과에 미확정 사항은 없다. 사용자 지정 설정 경로의 신뢰성과 검증기 입력 계약은 수정이 필요하다.

## 검토 범위

검토일: 2026-10-14 (Asia/Seoul)
기준 커밋: 78d68541c6653c3d74776d135463f1afd8cdfd28
검토 코드 커밋: 377db5550bb5a32ec74d6463ddc1395d233a3e2d
검토 명령: `git -c core.fsmonitor=false diff 78d68541c6653c3d74776d135463f1afd8cdfd28 377db5550bb5a32ec74d6463ddc1395d233a3e2d --`
검증한 작업 공간 코드 버전: HEAD는 1b11c21e5f74c01b534f1a22107ee3f1665af5e7. 대상 코드 SHA와 작업 공간의 추적 파일 차이는 TASK-002/implementation.md뿐이다. 제품 코드·테스트·스크립트는 대상 SHA와 일치하며 검토 시작 시 작업 트리는 깨끗했다. 기준/대상 SHA 모두 commit 객체임을 확인했다.

기준 이후 포함된 이전 작업·역할 운영 문서 변경은 누적 diff에서 확인하되 TASK-002 제품 변경과 구분했다. TASK-001의 이전 지적은 이 작업에 새로운 수정 요구로 승계하지 않았다. 제품 코드·명세·GT·vendor·기존 결과 파일·공용 서버 설정은 수정하지 않았다. 원격 push와 모델 API 호출은 수행하지 않았다.

## 완료 조건별 판정

| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 원본 4건을 리뷰 전용 task DB에서 재현: Phase 1 통과·Phase 2 실패. virtual은 최초 993행, Phase 1 후 799행, fans_bak 부재 확인. 원본 후속 SQL을 한 번만 실행하면 결과 0; 원본 테스트의 백업 조회 오류와 test_case 33행 TypeError를 별도 진단으로 확인했다. |
| AC-2 | 충족 | 보정 4건 모두 초기화 후 단계별 최초 제출로 Phase 1 reward=0.7·Phase 2 reward=0.3, 총 1.0. passed와 phase_completed는 공식 응답에서 확인했으며 모델 호출은 없다. |
| AC-3 | 충족 | private 설정의 의미·반례 검증 29개 전부 통과. 4개 반례 모두 공식 `SQL failed Phase 2. Your SQL is not correct.`로 거부됐다. 제약 NOT VALID·기존 행/INSERT 결과, interests expression GIN, 정확한 집계 179와 반례 152, 복원된 993행/백업과 고유 삭제 194/잔여 799를 확인했다. |
| AC-4 | 충족 | 리뷰 전용 ID로 보정 30문제, 동시성 10, 2회 실제 채점. 두 회차 모두 Phase 1 30/30·Phase 2 30/30·총 보상 30.0·오류 0. 문제별 공식 결과가 완전히 동일했다. |
| AC-5 | 미충족 | 기본 설정의 재실행·원본 보존·보정 해시는 확인했다. 그러나 공개된 -ConfigPath 옵션을 사용하면 지정한 설정과 실제 입력/DB 설정이 달라질 수 있고, 검증된 설정 중 sql 생략은 실행할 수 없다(R-1, R-2). |
| AC-6 | 충족 | 구현 SHA·명령·원인·반례·시간 의존 제한 기록 확인. 입력/설정/GT/vendor combined 해시를 독립 확인했다. TASK-002 코드 변경은 일반 처리 코드와 모의 테스트이며 GT·테스트 본문·private 데이터·비밀값은 포함되지 않는다. 외부 SHA 한 곳의 오기는 아래 별도 제안에 기록했다. |

## 수정 요청

### R-1: [P2] 지정한 보정 설정을 준비·입력 생성·검증에서 일관되게 사용해야 한다

위치: `scripts/verify-calibration.ps1:21-28`; 연계 위치 `scripts/verify_calibration.py:52-55`, `compose.benchmark.calibrated.yml:15`.
관련 조건: AC-5; 실제 적용한 보정 설정과 검증 근거를 일치시키는 계약.

현재 동작과 영향: -ConfigPath는 검증기 마지막 실행의 --config 인자로만 전달된다. 데이터 생성기는 --patches를 받지 않아 기본 TASK-002 설정으로 입력을 만들고, compose의 CALIBRATION_CONFIG도 기본 파일로 고정돼 있다. 따라서 다른 patch나 준비 SQL을 지정한 설정을 검증한다고 실행해도 실제 채점 대상과 준비는 기존 설정이다. check_service는 eval_prep의 task ID 목록만 비교하므로 같은 ID의 다른 phase/SQL 설정을 검출하지 않는다. 사용자 지정 설정을 적용하지 않았는데도 그 설정을 검증했다고 결론낼 수 있다.

기대 동작: 데이터 생성·DB 준비·시나리오 검증이 같은 설정을 사용하며, 활성 설정/입력이 의도한 설정과 다르면 제출 전에 중단한다. 사용자 지정 설정을 지원하지 않기로 한다면 해당 옵션을 명확히 거부해야 한다.

재현·검증 방법: 스크립트에서 -ConfigPath가 사용되는 세 위치를 추적했고, 실제 준비 phase가 다른 서비스가 검사를 통과하는 것을 모의 HTTP로 재현했다. 실제 서비스를 교체할 필요가 없다.

```powershell
@'
import httpx
from scripts.verify_calibration import check_service
expected = {'eval_prep': {'task_a': {'phase2': ['SELECT 2;']}}, 'dataset_patches': {}}
active = {'service': 'db_environment_calibration', 'config': 'other.json',
          'eval_prep': {'task_a': ['phase1']}}
with httpx.Client(transport=httpx.MockTransport(
    lambda request: httpx.Response(200, json=active)
), base_url='http://mock') as client:
    print(check_service(client, expected))
'@ | .\.venv\Scripts\python.exe -
```

실행 결과: `[]`. 기대 phase2와 실제 phase1이 다른데 문제가 없다고 반환한다. 같은 phase에서 SQL만 달라진 경우는 현재 summary로 식별조차 할 수 없다.

필요한 수정: 선택한 설정을 생성기와 DB wrapper에도 전달하고 활성 설정을 내용 해시 등으로 대조한다. SQL/기대값 본문을 health나 에이전트에 노출하지 않아도 해시로 확인할 수 있다. 생성 입력의 설정 귀속도 일치시킨다. task ID가 같지만 phase·SQL·patch가 다른 설정 및 현재 기본 설정에 대한 회귀 검증을 추가한다.

### R-2: [P2] 검증된 시나리오의 sql 생략을 실행기가 처리해야 한다

위치: `scripts/verify_calibration.py:68`; 연계 위치 `benchmark/calibration/calibration_config.py:259-261` (`_validate_submission`).
관련 조건: AC-5; 검증을 통과한 보정 설정의 재실행 가능성.

현재 동작과 영향: 설정 검증기는 `submission.get("sql")`로 검사해 sql 키가 없는 제출을 유효하게 받아들인다. 실행기는 `submission["sql"]`로 접근한다. 단계의 데이터 SQL을 쓰기 위해 sql을 생략한 시나리오는 DB 초기화 후 KeyError('sql')로 중단하며 제출은 한 번도 하지 않는다. 현재 private 설정은 명시적인 sql:null을 사용하므로 기본 실행에서는 드러나지 않는다.

기대 동작: 유효하게 받아들인 sql 생략을 sql:null과 같이 phase_sql로 처리하거나, sql 키가 필수라면 설정 로딩 때 명확한 CalibrationError로 거부한다. DB 초기화 후 KeyError로 끝나서는 안 된다.

재현·검증 방법: 아래 명령은 가짜 설정과 모의 HTTP만 사용한다.

```powershell
@'
import json
from pathlib import Path
import httpx
from scripts import verify_calibration as v
scenario = {'task_id': 'test', 'submissions': [
    {'id': 'p1', 'phase': 1, 'expect_passed': True}
], 'checks': []}
p = Path('.runtime-tmp/task002-review/omitted-sql.json')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({'verification': [scenario]}))
loaded = v.calibration_config.load_config(str(p))
calls = []
def handler(req):
    calls.append(req.url.path)
    return httpx.Response(200, json={'status': 'ok'})
with httpx.Client(transport=httpx.MockTransport(handler), base_url='http://mock') as client:
    try:
        v.run_scenario(client, loaded['verification'][0],
                       {'instance_id': 'test', 'sol_sql': ['SELECT 1;']})
    except Exception as exc:
        print(type(exc).__name__, str(exc), calls)
'@ | .\.venv\Scripts\python.exe -
```

실행 결과: `KeyError 'sql' ['/init_task', '/cleanup_task']`.

필요한 수정: validator와 실행기의 sql 키 계약을 일치시킨다. 키 생략·null·명시 SQL을 각각 검증하는 테스트를 추가한다.

## 독립 검증

### 코드·입력 검증

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`: Windows/Python 3.12 venv, 62 tests, OK, exit 0. Windows asyncio 로컬 소켓을 사용하는 테스트이므로 자동 승인된 샌드박스 외 실행을 사용했다. 실제 모델 API나 채점 HTTP는 호출하지 않는 계약 테스트다.
- `git -c core.fsmonitor=false diff --check <기준 SHA> <코드 SHA>`: 출력 없음, exit 0.
- PowerShell Parser::ParseFile: run-dev30-calibrated.ps1, verify-calibration.ps1 모두 syntax OK.
- 메모리에서 원본+private 설정으로 보정 입력을 재구성해 저장된 보정 입력과 객체 내용이 같음을 확인했다. ID·순서 유지, 변경은 cross_db_M_5.follow_up.sol_sql 한 곳(361→358자)뿐이며 나머지 29행은 동일하다. 원본/보정 문제 문구와 테스트 본문은 바뀌지 않았다.
- 기존 `results/dev30_oracle_calibrated.json`도 30/30·평균 1.0·오류 0이며 독립 실행 결과와 일치했다.

| 파일·외부 코드 | 확인한 SHA/해시 |
| --- | --- |
| vendor BIRD-Interact | 451fe2c3518ee1cf908d8139e2913483bd519381, git status clean |
| private/lite30.combined.jsonl | 2c02b9597d1a7e38ac5d7a961f40d37a871ea6d702133915997b133585628380 |
| private/lite30.calibrated.jsonl | 55ca576ea1c2942b53aab5700c781c50e4cf1660dff2b25c9fdf24ac835de3ce |
| private/calibration/TASK-002.calibration.json | 62d513a4063cd462594d69a4fc69e2c983231ddaeb730c388909e1eab926e725 |
| GT 파일 해시 | 92ead9c644d73d381f5b354bc11d7b6f59fcfa91e1baec3ea0fd1e3f26915762 |
| vendor combined 입력 | 089853d8366b8b7bce90cf0070fc8b6aa5fec138dca711e5484cfa132e80534d |

위 파일 해시는 독립 DB 검증 전후 모두 같았다. 공개 데이터 revision f7881a9c2b9630cc4fc13b0c39279740b0a2fd87은 기존 prepare-benchmark.ps1의 고정 설정으로 확인했으며, 외부 원격을 다시 조회하거나 내려받지는 않았다.

### 실제 DB 검증

기존 db-agent-smoke PostgreSQL의 고정 템플릿을 복사하는 별도 일회성 컨테이너를 실행했다. 기존 6001/6002 서비스는 기본 스택 그대로 유지했고 포트나 컨테이너를 교체하지 않았다. 공식 app의 `/init_task`·`/submit`·`/execute`·`/cleanup_task`를 ASGI transport로 호출했고 실제 SQL 실행·채점은 PostgreSQL에서 수행했다. 따라서 아래 결과는 모의 채점이 아니다.

일시 검증 코드: `.runtime-tmp/task002-review/db_review.py`, `diagnose_original.py` (gitignore 대상, 제품 변경 아님). 준비 코드·입력은 readonly로 마운트했다. DB prep의 task ID 키만 메모리에서 `r2rev_01a1390d_` 접두사로 바꿔 기존 사용자 task DB와 충돌하지 않게 했다. 문제·GT·테스트·SQL·공식 보상은 변경하지 않았다. 원본 진단은 별도 `r2diag_01a1390d_` ID를 사용했다.

실행 명령은 `docker run --rm --name db-agent-task002-review-01a1390d --network db-agent-smoke_default --env PG_HOST=postgresql --env DATASET=lite --env PYTHONPATH=/benchmark:/calibration`에 다음 readonly bind를 지정하고 `db-agent-benchmark-runtime:local python /review/db_review.py`를 실행했다: vendor/BIRD-Interact/BIRD-Interact-ADK→/benchmark, benchmark/calibration→/calibration, private→/private, scripts→/repo-scripts, .runtime-tmp/task002-review→/review. 추가 원본 진단은 동일 DB 네트워크·vendor/private/review 마운트로 `python /review/diagnose_original.py`를 실행했다. 실행일과 DB CURRENT_DATE는 모두 2026-10-14다.

| 실제 실행 | 독립 결과 |
| --- | --- |
| 원본 4문제 | 전부 Phase 1 0.7, Phase 2 0.0. museum 제약 중복, news 인덱스 중복, cross_db numeric/record 오류, virtual 테스트 실패. |
| virtual 원본 상세 진단 | 최초 fans 993행·fans_bak 없음, Phase 1 후 fans 799행·fans_bak 없음. 후속 SQL 1회 결과 [(0,)]. 테스트의 백업 조회에서 relation fans_bak does not exist, test_case 33행 TypeError: NoneType object is not subscriptable. 진단 중 DELETE를 정답 비교를 위해 반복 실행하지 않았다. |
| 보정 4문제 최초 제출 | 모두 passed=true, phase_completed=1/2, reward 0.7/0.3. |
| 의미·반례 검증 | museum 9개·news 7개·cross_db 5개·virtual 8개, 총 29개·실패 0. 4개 반례의 실패는 인프라/실행 오류가 아닌 공식 테스트의 불일치였다. |
| 보정 30문제 회차 1, 동시성 10 | Phase 1 30/30, Phase 2 30/30, 총 보상 30.0, 평균 1.0, 인프라 오류 0. |
| 보정 30문제 회차 2, 동시성 10 | 동일 수치, 문제별 공식 결과도 회차 1과 완전히 같음. |
| 종료 후 정리 확인 | PostgreSQL의 r2rev_01a1390d/r2diag_01a1390d 이름 DB 수 0. 일회성 컨테이너 --rm 종료, 기존 기본 서비스 유지. |

SQL·GT·테스트 본문을 리뷰 문서나 커밋에 복사하지 않았다. API 키와 인증 헤더를 사용하지 않았다. 날짜 의존 기대값은 2026-10-14 검증에 한정하며, 전체 300문제나 미래 날짜의 동일 집계값은 검증하지 않았다.

## 이전 지적 처리

해당 없음(TASK-002 최초 검토). R-1, R-2의 수정·재검토 횟수는 0회다.

## 별도 제안·판단 요청

- implementation.md의 실행한 검증 표에 vendor SHA가 한 곳 `...519481`로 적혀 있다. 실제는 `...519381`이며 명세와 보고서의 다른 버전 표도 후자로 일치한다. 보고서 갱신 시 이 오기를 정정하면 된다.
- 초기 `/init_task`에서는 공식 코드가 `_task_db`를 등록하기 전에 create_task_db를 호출하므로 wrapper의 초기 phase1 준비 lookup이 건너뛰어진다. 현재 virtual 준비는 첫 `/submit`의 reset에서 적용돼 이번 oracle 완료 조건은 충족한다. 향후 제출 전 탐색에서도 준비 상태가 필요하면 초기 등록 순서를 확인하는 실제 endpoint 검증을 추가하는 것이 좋다. 이 향후 에이전트 사용성 제안 자체를 이번 통과 차단 사유로 삼지는 않았다.
