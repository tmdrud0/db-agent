# TASK-004 평가 보고서

작업 ID: TASK-004
명세 버전: 2
최초 기준 프로젝트 코드 버전: 7e7e462732aa6303efd949279202d71f743ba624
원본 BIRD 코드 버전: 451fe2c3518ee1cf908d8139e2913483bd519381
상태: needs_decision (실평가·복구 집계 완료. 단일 회차 비교가 아니고 질문 timeout 2건이 남아 해석에 제한이 있음)
요약: BIRD 기본 에이전트는 복구 집계에서 Phase 1 10/30·Phase 2 6/30·평균 보상 0.293333으로, 기존 자체 고정 루프의 5/30·4/30·0.156667보다 높은 관측 점수를 기록했다. 공유 DB 재생성 영향을 제거한 재실행 9문제도 모두 0점이었다. 질문 timeout 2건은 오답과 구분해 남긴다.

## 실행 조건과 근거

- 일시: 2026-10-15, Asia/Seoul. 실행 시작 14:24:24, 본 평가 시작 약 14:25:36.
- 모델: Command Code `deepseek/deepseek-v4.1-flash`. `max_tokens=16384`, 요청 timeout 300초, effort 미지정, 비스트리밍.
- 입력: `private/lite30.calibrated.jsonl`, 고정 30문제, 동시성 10, patience 기본 3.
- 입력 SHA-256: `55ca576ea1c2942b53aab5700c781c50e4cf1660dff2b25c9fdf24ac835de3ce`.
- 비교 대상: `results/two_phase_manual_30.json`. 30개 문제 ID 집합의 일치를 실행 전에 검증했다. 기존 결과 파일에는 입력 해시·실행 SHA·명령이 내장돼 있지 않아 이전 실행 조건의 완전한 귀속에는 한계가 있다.
- DB: 기존 TASK-002 보정 DB(6002). `/calibration`으로 보정 스택을 확인했고 이번 oracle에서 Phase 1·2 각각 30/30, 평균 보상 1.0, 오류 0건을 확인했다.
- 기본 에이전트와 공식 v2 사용자 시뮬레이터를 6010·6011 포트에 별도 기동했다. 기존 6001 시뮬레이터는 유지했다. 기본 에이전트는 자체 에이전트와 달리 사용자 질문 도구를 사용하므로 시뮬레이터 모델 호출 경로가 새로 필요하다.
- 사용자 시뮬레이터에도 같은 DeepSeek 모델을 연결했다. 원본 v2의 행동 파싱 500·응답 생성 1024 출력 상한 대신 추론 모델의 빈 응답을 피하도록 16384를 사용했다. 시뮬레이터 프롬프트·판정·도구 코드는 변경하지 않았다.
- 원본 에이전트의 temperature 0, API 재시도 상한 5, 도구별 timeout, 탐색·질문·재제출·예산 처리·60회 모델 턴 제한을 유지했다. 자체 에이전트는 temperature를 지정하지 않고 API 재시도도 없다. 모든 생성 옵션이 완전히 동일한 실험으로 해석하지 않는다.
- API 연결은 원본 `shared._local_provider` 확장점에 메모리 모듈을 주입했다. LiteLLM의 `openai/` 접두사는 제공자 어댑터 선택이며 실제 API 모델 ID는 위 DeepSeek ID다.
- 별도 가상환경의 주요 패키지: google-adk 2.12.0, google-genai 2.31.0, litellm 1.105.1, httpx 0.28.1. 전체 패키지 버전은 결과 metadata에 보존한다.

## 실행 방법과 산출물

로컬 실행기: `.runtime-tmp/bird_original_eval.py`. 제품 코드·vendor 코드 수정 없이 이번 실험용으로 작성했다.

```powershell
.\.runtime-tmp\bird-original-venv\Scripts\python.exe .runtime-tmp/bird_original_eval.py run
```

실행기는 기존 결과 덮어쓰기를 거부한다. 재실행하려면 새 결과 접두사를 정한다. 키는 기존 환경 또는 프로젝트 dotenv에서 읽으며 문서·로그에 출력하지 않는다.

- 결과: `results/bird_original_deepseek_dev30_20261015.json` 및 `.html`.
- oracle: `results/bird_original_deepseek_dev30_20261015_oracle.json` 및 `.html`.
- 설정·코드 SHA·입력 해시·문제 ID·패키지 버전: `results/bird_original_deepseek_dev30_20261015.metadata.json`.
- 비교 집계: `results/bird_original_deepseek_dev30_20261015.summary.json`.
- 복구 9문제: `results/bird_original_deepseek_dev30_20261015_repair9.json` 및 `.html`, 해당 `.metadata.json`·`_oracle.json`·`_oracle.html`.
- 최종 30문제 복구 집계: `results/bird_original_deepseek_dev30_20261015_recovered.json` 및 `.html`. 각 문제의 `evaluation_source`에 최초 실행 또는 복구 실행 출처를 기록한다.
- 복구 DB 실행기: `.runtime-tmp/bird_original_isolated_db.py`; 합산기: `.runtime-tmp/merge_bird_original_recovery.py`. 복구 실행 때 `BIRD_RUN_PREFIX=bird_original_deepseek_dev30_20261015_repair9`, `BIRD_DB_PORT=6012`, `BIRD_DATA_PATH=<프로젝트>/private/lite30.task004-repair.jsonl`을 설정한다. 원본 shared 서비스 대신 같은 이미지·원본 vendor·보정 모듈·설정을 마운트한 별도 컨테이너가 필요하다.
- 실행 로그: `.runtime-tmp/bird-original-evaluation.log` 및 접두사의 `.agent.log`, `.simulator.log`.

## 결과

| 실행 | Phase 1 | Phase 2 | 평균 보상 | 해석 |
| --- | --- | --- | --- | --- |
| 기존 자체 고정 루프 | 5/30 | 4/30 | 0.156667 | 기존 결과 파일의 집계 |
| 기본 에이전트 최초 실행 | 10/30 | 6/30 | 0.293333 | 공유 DB 재생성으로 후반 결과 오염; 정상 성능 점수로 확정하지 않음 |
| 기본 에이전트 복구 9문제 | 0/9 | 0/9 | 0.0 | 별도 DB에서 다시 수행한 결과. 질문 timeout 2건 포함 |
| 기본 에이전트 30문제 복구 집계 | 10/30 | 6/30 | 0.293333 | 최초 21개 + 복구 9개, 서로 다른 두 회차의 집계 |
| 최초 oracle | 30/30 | 30/30 | 1.0 | DB 재생성 이전 정상 대조 |
| 복구 대상 oracle | 9/9 | 9/9 | 1.0 | 별도 DB 서비스·이름 공간에서 정상 대조 |

최초 실행은 14:24:24~15:01:43에 끝났고 프로세스 exit code는 0이다. 본 모델 평가 완료는 15:01:42다. 최초 결과 JSON의 문제별 최상위 error는 0건이지만 이것만으로 정상 평가라고 판단하면 안 된다. 원본 도구가 HTTP 오류를 문자열로 반환하거나 제출 실패 응답을 충분히 검사하지 않아 세션이 0점으로 끝날 수 있다.

### 오류 원인과 복구 원칙

- Docker inspect에서 공유 `db-agent-smoke-db-environment-1`의 시작 시각이 2026-10-15 14:58:15 KST로 바뀐 것을 확인했다. 새 서비스의 도구 로그에 `/execute`, `/submit` 404가 기록됐다. 컨테이너 재생성으로 메모리의 task 상태가 유실된 정황이 일치한다.
- 그 시각에 완료되지 않은 문제는 fake_3, vaccine_5, gaming_2, mental_10, mental_2, fake_6, cross_db_4, vaccine_4의 8개다. 이 중 gaming_2·mental_10·mental_2·fake_6·cross_db_4·vaccine_4의 6문제에 명시적 `Task not initialized` 실행 도구 오류 총 24건이 있다. 나머지 2개도 같은 시점에 미완료였으므로 보수적으로 복구 대상에 포함했다.
- 사용자 질문 도구 timeout은 fake_11·vaccine_5에서 각각 1회다. fake_11을 추가해 9개 고유 문제를 재평가한다. SQL 탐색 자체의 timeout·문법 오류·SELECT 전용 도구 오용은 별도 기록하며 모델 행동에서 나온 오류로 구분한다.
- 최초 결과를 보존하고, 복구 9문제는 성패와 관계없이 모두 새 결과로 교체한다. 나머지 21문제는 최초 결과를 사용한다. 점수가 높은 회차를 선택하지 않는다. 합계는 단일 회차 30문제 점수와 구분해 복구 집계로 표시한다.
- 독립 Docker 컨테이너 `db-agent-task004-repair`, 호스트 포트 6012에서 같은 보정 DB evaluator를 실행한다. 같은 PostgreSQL·템플릿을 쓰되 물리 작업 DB 이름에 `t004repair_`를 넣어 다른 평가의 같은 task_id와 충돌하지 않게 한다. 공식 task_id·SQL·채점·프롬프트·예산은 그대로다. 기존 공유 DB 서비스는 수정하거나 중지하지 않았다.
- 최초 실행 중 별도 TASK-005 개발·평가가 시작돼 자체 agent·스크립트 파일에 변경이 생겼다. 이 변경들은 이번 원본 에이전트 실행 경로에 포함되지 않고 이번 작업에서 수정·되돌림·stage하지 않았다. vendor 코드는 계속 깨끗한 상태다.

### 최종 검증과 제한

- 복구 실행은 15:04:50~15:24:15, 본 모델 풀이 약 15:05:19~15:24:14이며 exit code 0이다. 9문제 모두 공식 SQL 제출을 수행했다. 복구 DB 컨테이너는 시작 시각 15:04:46을 유지했고 restart count는 0이었다.
- 원본 21문제와 복구 9문제를 합친 고유 30문제 ID가 기존 자체 결과와 일치한다. 전체 입력 파일의 해시는 최초 실행과 재확인 시 동일했다. 보상 합계 8.8이 공식 Phase 가중치 `0.7 * 10 + 0.3 * 6`과 일치한다. 결과 JSON·HTML 생성이 완료됐다.
- 복구 집계에 `Task not initialized` 오류는 0건, 최상위 문제별 error는 0건이다. 그러나 질문 도구 timeout이 vaccine_5·fake_6에서 각 1회 남았다. 이 두 문제는 모두 0점이며, 이를 정상 모델 오답만으로 해석하지 않는다. 질문 클라이언트의 원본 timeout 60초와 추론 모델을 쓰는 시뮬레이터 지연의 영향을 분리 측정하지 않았다. 더 높은 점수가 나올 때까지 재시도하지 않았다.
- SQL 탐색 오류는 15건이다: SELECT 전용 도구의 변경 SQL 거부 9건, ROUND 타입 오류 3건, 존재하지 않는 컬럼 1건, FROM 참조 오류 1건, SQL 실행 timeout 1건. 질문 연결 timeout과 분리해 기록한다.
- 모든 30문제가 submit_sql을 1회 이상 호출했다. 단순 종료를 문제 성공으로 기록하지 않았고 Phase 성공·보상은 원본 도구가 공식 제출 응답으로 갱신한 상태를 사용했다.
- 기존 자체 결과 대비 Phase 1에서 archeology_3·disaster_2·fake_2·mental_3·mental_M_2의 5문제를 추가 통과했다. Phase 2 추가 통과는 archeology_3·disaster_2다. 기존 성공 중 잃은 문제는 없다. 단일 모델 샘플, 서로 다른 루프·시뮬레이터 호출·일부 생성 설정, 복구 합산의 차이로 인과적인 성능 개선을 입증한 것은 아니다.
- 기본 원본 serializer는 모델 usage를 결과에 보존하지 않는다. 실제 API 토큰·금액은 정확히 비교하지 않았다. model 응답 이벤트 357개에는 callback의 합성 종료 응답도 들어 있으므로 실제 API 호출 횟수로 단정하지 않는다.

| 행동·자원 기록 | 기존 자체 고정 루프 | 기본 에이전트 복구 집계 |
| --- | --- | --- |
| 정보 조회 기록 | 90 | 246 |
| execute_sql | 0 | 148 |
| ask_user | 0 | 52 |
| submit_sql | 35 | 54 |
| 전체 도구 기록 | 125 | 500 |
| 에이전트 보고 코인 사용 합계 | 195 | 556.5 |

코인은 각 구현의 기록이며 원본의 예산 부족 무료 제출·중복 도구 호출 정책을 그대로 두었다. 동일한 비용 회계 구현이라고 단정하지 않는다. 문제별 경과 시간 합계는 기본 복구 집계 22,984.21초·기존 자체 1,116.87초다. 동기 도구 대기로 세션이 서로 지연되고 복구 회차도 달라 순수 모델 계산 시간 비교에 사용하지 않는다.

### 완료 조건별 결과

| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 최초·복구 metadata의 SHA·입력 해시·ID·모델 설정·동시성·전체 패키지 버전 |
| AC-2 | 충족 | 최초 30문제 및 9문제 복구 평가·oracle·JSON·HTML 보존. 두 회차 합산임을 명시 |
| AC-3 | 충족, 해석 제한 있음 | 공식 집계 대조 및 최상위 오류뿐 아니라 trajectory의 DB 404·질문 timeout·SQL 오류 검사. 질문 timeout 2건 남음 |
| AC-4 | 충족 | vendor git status 깨끗함. 제품 코드 수정 없음. API·시뮬레이터·DB 이름 공간·복구 회차 차이 기록 |

검증은 실제 모델·공식 PostgreSQL 채점이다. 제품 코드 변경이 없어 계약 테스트를 반복하거나 독립 코드 리뷰를 새로 수행하지 않았다. 복구용 DB 로그를 `.runtime-tmp/bird-original-isolated-db.log`에 보존했고, PostgreSQL에서 `t004repair_` 작업 DB가 남지 않은 것을 확인했다. 이번에 만든 `db-agent-task004-repair` 컨테이너만 종료·제거했고 기존 공유 서비스는 유지했다.

## 리뷰와 변경 범위

제품 코드·벤치마크 코드 변경과 구현 커밋은 없다. 독립 코드 리뷰를 수행하지 않았다. 실행 상태·설정·원본 코드 보존·공식 결과를 조율자가 확인하는 비교 실험이다. `ready_for_user`의 구현·독립 리뷰 pass 판정을 주장하지 않는다.

## 사용자 결정과 문서 보존

문서 보존 확인 대기. spec.md·final.md는 로컬 문서이며 커밋·push하지 않았다. 결과·GT·키·가상환경·vendor는 커밋 대상이 아니다. README·setup·상태 기록은 수정하지 않았다.
