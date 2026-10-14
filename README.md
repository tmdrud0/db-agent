# DB Agent

BIRD-Interact Lite를 이용해 자체 에이전트 루프를 개발하고 평가하는 프로젝트다.
현재 구현은 **모델 없이 빈 SQL을 한 번 제출하는 연결 확인용 시작 코드**다.
공식 러너 → 자체 에이전트 → DB 실행·채점 → 결과 집계까지 연결했으며,
현재 점수는 LLM이나 완성된 에이전트의 성능 기준값이 아니다.

## 시작하기

Windows PowerShell, Python 3.12, Git, Docker Desktop의 Linux 컨테이너 환경을 사용한다.
정답·테스트 케이스 파일은 별도로 받아 프로젝트 루트에
`bird_interact_gt_kg_testcases_1008.jsonl`이라는 이름으로 놓는다.
이 파일은 저장소에 포함하지 않는다.

```powershell
.\scripts\prepare-benchmark.ps1
.\scripts\run-dev30.ps1
```

준비 스크립트는 고정한 공식 코드·공개 데이터를 내려받고 Python 환경과 평가 입력을 만든다.
평가 스크립트는 Docker 서비스를 준비하고 에이전트 서버를 자동으로 실행한다.
설치 조건과 파일 준비는 [실행 환경 안내](docs/setup.md)를 따른다.

## 평가 명령

```powershell
# 첫 문제로 연결 확인
.\scripts\run-smoke.ps1

# 고정한 개발용 30문제
.\scripts\run-dev30.ps1

# 전체 300문제
.\scripts\run-smoke.ps1 -Limit 300
```

모두 기본 동시성은 10이다. 같은 문제들에 대해 정답 SQL을 제출하는 oracle 평가를 먼저
실행하고, 자체 에이전트를 평가한다. 결과 JSON·HTML은 `results/`에 저장한다.
전체 300문제 실행 명령은 제공하지만, 전체 평가 완료를 확인한 상태는 아니다.

개발 세트는 공개 메타데이터만으로 선택한다. 원래 Phase 1 유형은 조회 195개·변경 105개이며,
이를 30개로 배분한 조회 20개·변경 10개를 고정 시드로 추출했다.
선택 목록은 [manifest](eval_sets/lite30.manifest.json)에 있다.
문제별 추가 가중치는 없으며 공식 Phase 1/2 보상 0.7/0.3을 유지한다.

## 코드 구조

```text
공식 평가 러너
  → 자체 에이전트 API (6000)
    → 공식 DB 환경 (6002) → PostgreSQL 실행·채점
    → 공식 사용자 시뮬레이터 (6001)
  ← 응답과 state를 받아 결과 집계
```

- `agent/server.py`: `/init_session`, `/run_session`, `/health`와 세션 관리.
- `agent/loop.py`: 현재는 빈 SQL을 한 번 제출한다. 에이전트 루프를 개발할 위치.
- `agent/environment.py`: 공식 `/submit`, `/phase_transition` HTTP 호출.
- `scripts/`: 환경 준비, 개발 세트 생성, 평가 실행, 결과 확인.
- `benchmark/`, `compose.benchmark.yml`: 로컬 채점 서비스 구성.
- `tests/`: API 계약과 개발 세트 선택 검증.
- `docs/`: 환경 준비와 프로젝트 문서.

세션은 `(mode, task_id)`별로 메모리에 보관하므로 Uvicorn worker는 **1개**로 실행한다.
한 worker에서도 비동기 요청으로 여러 문제를 처리한다. 같은 세션의 실행은 lock으로 보호한다.
공식 제출 응답으로 점수와 완료 여부를 기록하며, 연결 오류를 정상적인 0점으로 취급하지 않는다.
현재 구현은 한 번 제출한 뒤 멈춘다. Phase 2 풀이, 실패 후 재시도,
나머지 공식 도구와 모델 호출은 앞으로 구현할 부분이다.

## 확인한 결과

2026-10-14, 받은 GT와 공개 Lite 데이터 300개를 매칭하고 실제 DB 채점을 확인했다.

| 평가 범위 | Oracle Phase 1 | Oracle Phase 2 | Oracle 평균 보상 | 빈 SQL 평균 보상 |
| --- | --- | --- | --- | --- |
| 첫 문제 `alien_1` | 1/1 | 1/1 | 1.00 | 0.00 |
| 개발용 30문제 | 30/30 | 26/30 | 0.96 | 0.00 |
| 전체 입력의 첫 10문제 | 10/10 | 9/10 | 0.97 | 0.00 |

30문제·10문제 평가에서 동시성 10으로 실행했고 서비스 연결 오류는 없었다.
30문제 oracle의 Phase 2 실패는 `museum_M_4`, `cross_db_M_5`, `virtual_M_4`, `news_M_4`였다.
원본 데이터·정답·채점기를 수정하거나 실패 문제를 제외하지 않았다.
이 결과는 실행 환경 연결 확인이며, 모델 성능 평가 결과가 아니다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

로컬 테스트는 API 계약, 상태 처리, 예산, HTTP 오류, 고정 세트 선택을 검증한다.
실제 PostgreSQL 채점은 위 평가 명령으로 확인한다.
패키지 버전은 `requirements.lock.txt`와 `requirements.benchmark.lock.txt`에 기록했다.
현재는 LLM을 호출하지 않아 API 키가 필요하지 않다.

## 공식 자료와 제외 파일

[공식 BIRD-Interact 코드](https://github.com/bird-bench/BIRD-Interact),
[공개 Lite 데이터](https://huggingface.co/datasets/birdsql/bird-interact-lite)를 별도로 사용한다.
공식 코드는 `vendor/`에 내려받으며 이 저장소에는 복사해 올리지 않는다.
메일로 받은 정답·테스트 케이스, `private/`, `results/`, `.env`, 로컬 개발 도구 설정은
Git에서 제외한다. 내려받은 외부 자료의 이용 조건은 해당 원본을 따른다.
