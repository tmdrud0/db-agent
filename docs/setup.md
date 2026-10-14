# 새 PC에서 평가 환경 준비하기

현재 지원·검증 환경은 Windows, Python 3.12, PowerShell, Git, Docker Desktop의
Linux 컨테이너 엔진이다. Phase 1 문제를 Command Code의 DeepSeek V4.1 Flash에 한 번 묻고
생성 SQL을 한 번 제출하는 최소 모델 기준선이므로 Command Code API 키가 필요하다.

## API 키 설정

모델 호출은 Command Code Provider API를 직접 사용한다. 키는 프로세스 환경 변수
`CMD_API_KEY`에서만 읽고 `.env` 자동 로딩이나 SDK는 쓰지 않는다.

- 엔드포인트: `POST https://api.commandcode.ai/provider/v1/chat/completions`
- 모델: `deepseek/deepseek-v4.1-flash`
- 요청: `Authorization: Bearer <키>`, 비스트리밍, timeout 300초, 최대 출력 토큰 16384
  (추론 토큰이 상한을 먼저 소모하므로 작은 상한은 SQL 생성 전에 잘릴 수 있다)

키는 [Command Code Studio의 API keys](https://commandcode.ai/settings/keys)에서 만든다.
평가를 실행할 PowerShell 세션에서 설정한다.

```powershell
$env:CMD_API_KEY = '<Command Code API 키>'
```

세션을 닫으면 사라진다. 영구 설정이 필요하면 사용자 환경 변수로 등록한다.
키는 저장소·결과·보고서·로그에 남기지 않는다.
평가 스크립트가 에이전트를 직접 시작할 때 `CMD_API_KEY`가 없으면 실행을 중단한다.
6000 포트의 기존 에이전트를 재사용할 때는 health의 기준선 이름과 모델이 다르면 오류를 낸다.

## 저장소를 내려받은 뒤

저장소에는 자체 코드·스크립트·Docker 설정·고정 30문제 ID 목록이 들어 있다.
공식 코드와 데이터, GT, 가상환경, DB 볼륨, 실행 결과는 별도로 준비한다.

1. Python 3.12, Git, Docker Desktop을 설치하고 Docker의 Linux 엔진을 켠다.
2. 메일로 받은 Lite 정답 JSONL을 프로젝트 루트에 둔다. 기본 파일 이름은
   `bird_interact_gt_kg_testcases_1008.jsonl`이다.
3. 프로젝트 루트의 PowerShell에서 다음을 실행한다.

```powershell
.\scripts\prepare-benchmark.ps1
.\scripts\run-dev30.ps1
```

정답 파일이 다른 위치에 있으면:

```powershell
.\scripts\prepare-benchmark.ps1 -GroundTruthPath 'C:\path\to\lite_gt.jsonl'
```

준비 스크립트는 가상환경과 고정 버전 의존성을 설치하고, 공식 코드·공개 데이터를
확인한 커밋으로 내려받아 GT와 병합한다. 기존 `vendor/`의 커밋이 다르면 파일을
덮어쓰지 않고 오류를 알린다. 첫 평가에서는 Docker 이미지 다운로드와 18개 DB의
초기 import가 필요하다. 이후에는 보존된 DB 볼륨을 재사용한다.

GT는 공식 [BIRD-Interact](https://github.com/bird-bench/BIRD-Interact)의 안내에 따라
`bird.bench25@gmail.com`에 제목 `[bird-interact-lite GT&Test Cases]`로 요청한다.
메일로 받은 정답과 테스트 케이스는 공개 저장소에 포함하지 않는다.

## 평가와 결과

```powershell
# 최초 한 문제 기준선 확인(모델 호출 1회, 제출 1회, Phase 2 없음)
.\scripts\run-smoke.ps1
# 고정 30문제
.\scripts\run-dev30.ps1
# 전체 300문제
.\scripts\run-smoke.ps1 -Limit 300
# 결과 이름 지정
.\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1 `
    -ResultName 'deepseek_v41_flash_phase1_smoke' -OracleResultName 'oracle_phase1_smoke'
```

동시성은 기본 10, 현재 허용 범위는 1~16이다. Uvicorn worker는 1개다.
각 스크립트는 같은 입력의 oracle 정답 제출을 먼저 실행하고 자체 에이전트를 평가한다.
보고서는 `results/`의 JSON·HTML로 저장한다. 같은 이름의 이전 결과는 갱신된다.
에이전트가 없으면 스크립트가 시작하고 끝난 뒤 그 프로세스만 종료한다.
기본 결과 이름은 `deepseek_v41_flash_phase1`이다.

한 문제 기준선 결과 JSON에서 확인할 항목:

- `adk_events`의 `model_call`: 요청·응답 모델, 생성 SQL, usage, 설정(timeout·토큰 상한), 소요 시간.
- `tool_trajectory`의 `submit_sql`: 제출 SQL과 인자, 공식 응답, 제출 비용 3과 예산 변화.
- `error`: 모델 생성 실패·채점 오류 항목이며 정상 0점 문제와 구분해 확인한다.
- 문제당 모델 호출 1회, 제출 1회, Phase 2 제출 0회여야 한다. Phase 2 점수는 이 기준선의 지표가 아니다.

Docker 서비스까지 종료하려면:

```powershell
docker compose -p db-agent-smoke -f compose.benchmark.yml stop
```

## 검증 범위

로컬 계약 테스트:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

이 테스트는 가짜 모델·채점 응답으로 모델 요청 계약, SQL 추출, 세션 API, 호출 횟수,
오류 구분과 고정 세트의 추출·재현성을 확인한다. 실제 모델 호출과 PostgreSQL 채점은
위 공식 러너 실행으로 확인한다. 한 문제 기준선은 문제당 모델 호출 1회와 공식 제출 1회를
수행하며 정답률 목표는 없다. oracle은 모델 API를 호출하지 않는다. 사용자 질문 도구를
추가하려면 사용자 시뮬레이터 컨테이너에 모델 호출 의존성과 API 설정을 추가해야 한다.

## 고정 세트와 재현성

첫 단계 유형의 전체 비율은 Query 195개, Management 105개다. 고정 seed `20261014`로
Query 20개, Management 10개를 선택했다. 문제별 추가 집계 가중치는 없고 공식
Phase 보상 0.7/0.3을 유지한다. 목록은 `eval_sets/lite30.manifest.json`에 있다.
줄바꿈 차이는 정규화한 공개 데이터 해시로 확인해 Windows와 다른 checkout에서
같은 ID를 유지한다. DB 이미지 digest, 공식 코드·데이터 커밋, Python 의존성은 고정했다.
날짜에 의존하는 SQL이나 향후 모델 응답까지 동일한 결과를 보장하는 것은 아니다.

2026-10-14의 실제 30문제 연결 검증에서 빈 SQL은 평균 0.0점이었다. oracle은 Phase 1
30/30, Phase 2 26/30으로 평균 0.96이었다. 후속 단계 실패 ID는 `museum_M_4`,
`cross_db_M_5`, `virtual_M_4`, `news_M_4`다. 해당 문제나 GT를 수정·제외하지 않았다.
