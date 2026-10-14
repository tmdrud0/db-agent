# DB Agent

BIRD-Interact Lite를 이용해 자체 에이전트 루프를 개발하고 평가하는 프로젝트다.
현재 구현은 **Phase 1 문제를 Command Code의 DeepSeek V4.1 Flash에 한 번 묻고 생성 SQL을
한 번 제출하는 최소 모델 기준선**이다. 스키마 탐색이나 모호성 해소 없이 문제와 DB 이름만
모델에 전달하므로 SQL이 틀릴 수 있으며, 이 결과를 이후 도구 사용 루프와 비교할 출발점으로 삼는다.
Phase 2 풀이, 실패 SQL 수정, 재시도, 사용자 질문 도구는 아직 구현하지 않았다.

## 시작하기

Windows PowerShell, Python 3.12, Git, Docker Desktop의 Linux 컨테이너 환경을 사용한다.
모델 호출에는 Command Code API 키가 필요하다. 키는 현재 PowerShell 세션의 환경 변수로
설정하고 저장소·결과·보고서에 남기지 않는다.

```powershell
$env:CMD_API_KEY = '<Command Code API 키>'
```

정답·테스트 케이스 파일은 별도로 받아 프로젝트 루트에
`bird_interact_gt_kg_testcases_1008.jsonl`이라는 이름으로 놓는다.
이 파일은 저장소에 포함하지 않는다.

```powershell
.\scripts\prepare-benchmark.ps1
.\scripts\run-dev30.ps1
```

준비 스크립트는 고정한 공식 코드·공개 데이터를 내려받고 Python 환경과 평가 입력을 만든다.
평가 스크립트는 Docker 서비스를 준비하고 에이전트 서버를 자동으로 실행한다.
6000 포트에 이미 떠 있는 에이전트는 health의 기준선 이름·모델이 다르면 재사용하지 않고 오류를 낸다.
설치 조건과 파일 준비는 [실행 환경 안내](docs/setup.md)를 따른다.

## 평가 명령

```powershell
# 첫 문제로 기준선 확인(문제당 모델 호출 1회, 공식 제출 1회, Phase 2 없음)
.\scripts\run-smoke.ps1

# 고정한 개발용 30문제
.\scripts\run-dev30.ps1

# 전체 300문제
.\scripts\run-smoke.ps1 -Limit 300
```

모두 기본 동시성은 10이다. 같은 문제들에 대해 정답 SQL을 제출하는 oracle 평가를 먼저
실행하고, 자체 에이전트를 평가한다. 결과 JSON·HTML은 `results/`에 저장한다.
기본 결과 이름은 `deepseek_v41_flash_phase1`이며 `-ResultName`으로 바꿀 수 있다.
에이전트 결과에는 문제별 모델 호출 기록(`adk_events`: 요청·응답 모델, 생성 SQL, usage, 설정,
소요 시간)과 제출 인자·공식 응답·예산(`tool_trajectory`)이 남는다.
모델 생성 실패나 채점 오류는 `error` 항목으로 기록되며, 결과의 오류 필드와 오류 건수를
정상 0점 문제와 구분해서 확인한다. 전체 300문제 실행 명령은 제공하지만,
전체 평가 완료를 확인한 상태는 아니다.

개발 세트는 공개 메타데이터만으로 선택한다. 원래 Phase 1 유형은 조회 195개·변경 105개이며,
이를 30개로 배분한 조회 20개·변경 10개를 고정 시드로 추출했다.
선택 목록은 [manifest](eval_sets/lite30.manifest.json)에 있다.
문제별 추가 가중치는 없으며 공식 Phase 1/2 보상 0.7/0.3을 유지한다.

## 코드 구조

```text
공식 평가 러너
  → 자체 에이전트 API (6000)
    → Command Code Provider API: 문제당 모델 호출 1회
    → 공식 DB 환경 (6002) → PostgreSQL 실행·채점: 제출 1회
  ← 응답과 state를 받아 결과 집계
```

- `agent/server.py`: `/init_session`, `/run_session`, `/health`와 세션 관리, 오류 출처 구분.
- `agent/model.py`: Command Code 모델 호출 1회, SQL 추출, 모델 오류 구분.
- `agent/loop.py`: 한 세션에서 모델 1회·제출 1회. Phase 1 결과만 기록하고 멈춘다.
- `agent/environment.py`: 공식 `/submit` 호출과 채점 오류 구분.
- `scripts/`: 환경 준비, 개발 세트 생성, 평가 실행, 결과 확인.
- `benchmark/`, `compose.benchmark.yml`: 로컬 채점 서비스 구성.
- `tests/`: 모의 모델·채점 API 계약과 개발 세트 선택 검증.
- `docs/`: 환경 준비와 프로젝트 문서.

프로젝트 개발 공통 지침은 [AGENTS.md](AGENTS.md), 조율자·구현자·리뷰어의 Skill은
`.agents/skills/`에 있다. 역할 문서와 새 채팅 시작 메시지는
[역할별 개발 운영](docs/agent/README.md)에 있다.
구현자가 로컬 커밋을 만들고 리뷰어가 기준 SHA와 구현 SHA의 diff를 검토하는 수동 흐름이다.

세션은 `(mode, task_id)`별로 메모리에 보관하므로 Uvicorn worker는 **1개**로 실행한다.
한 worker에서도 비동기 요청으로 여러 문제를 처리한다. 같은 세션의 실행은 lock으로 보호한다.
공식 제출 응답으로 점수와 완료 여부를 기록하며, 모델·채점 오류를 정상적인 0점으로 취급하지 않는다.
한 세션은 모델 호출 1회와 제출 1회 뒤 멈추고, 재호출에는 저장된 결과를 반환한다.
실패한 요청은 자동 재시도하지 않으며 새 시도는 `reset: true`로 새 세션을 만들 때만 허용한다.
Phase 1 성공에 후속 문제가 있어도 Phase 2를 풀지 않고 기록만 한다.
Phase 2 풀이, 실패 SQL 수정, 스키마 조회·사용자 질문 도구는 앞으로 구현할 부분이다.

## 확인한 결과

2026-10-14, 받은 GT와 공개 Lite 데이터 300개를 매칭하고 실제 DB 채점을 확인했다.
아래 표는 모델 없이 빈 SQL을 제출하던 연결 확인 단계의 기록이다.

| 평가 범위 | Oracle Phase 1 | Oracle Phase 2 | Oracle 평균 보상 | 빈 SQL 평균 보상 |
| --- | --- | --- | --- | --- |
| 첫 문제 `alien_1` | 1/1 | 1/1 | 1.00 | 0.00 |
| 개발용 30문제 | 30/30 | 26/30 | 0.96 | 0.00 |
| 전체 입력의 첫 10문제 | 10/10 | 9/10 | 0.97 | 0.00 |

30문제·10문제 평가에서 동시성 10으로 실행했고 서비스 연결 오류는 없었다.
30문제 oracle의 Phase 2 실패는 `museum_M_4`, `cross_db_M_5`, `virtual_M_4`, `news_M_4`였다.
원본 데이터·정답·채점기를 수정하거나 실패 문제를 제외하지 않았다.
이 표는 실행 환경 연결 확인이며, 모델 성능 평가 결과가 아니다.

새 모델 기준선은 한 문제 실행으로 확인한다.
기대 흐름은 모델 호출 1회, 공식 제출 1회, Phase 2 제출 0회이며 정답이어야 한다는 조건은 없다.
문제 ID·모델·요청 설정·공식 결과는 `results/`의 결과 JSON에 남는다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

로컬 테스트는 모의 모델·채점 HTTP 계약, 세션·상태 처리, 예산, 오류 구분,
고정 세트 선택을 검증한다. 실제 모델 호출과 PostgreSQL 채점은 위 평가 명령으로 확인한다.
패키지 버전은 `requirements.lock.txt`와 `requirements.benchmark.lock.txt`에 기록했다.

## 공식 자료와 제외 파일

[공식 BIRD-Interact 코드](https://github.com/bird-bench/BIRD-Interact),
[공개 Lite 데이터](https://huggingface.co/datasets/birdsql/bird-interact-lite)를 별도로 사용한다.
공식 코드는 `vendor/`에 내려받으며 이 저장소에는 복사해 올리지 않는다.
메일로 받은 정답·테스트 케이스, `private/`, `results/`, `.env`, 로컬 개발 도구 설정은
Git에서 제외한다. 내려받은 외부 자료의 이용 조건은 해당 원본을 따른다.
