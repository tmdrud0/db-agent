# TASK-016 작업 명세

작업 ID: TASK-016
명세 버전: 1
대상 코드 버전: 0c00526c153c34e2caac44913721ecb4f4845411
작업 브랜치: exp/staged-flow (기준 SHA에서 새로 만든다. main·exp/oracle-context에는 커밋하지 않는다)
상태: planned
요약: 에이전트 루프에 "이해 → 질문 → 검증 → 제출" 단계를 코드로 두는 단계형 흐름 모드를 추가한다. 같은 조건에서 기존 자유 루프와 반복 비교한다. LangGraph 같은 외부 프레임워크는 쓰지 않는다.

근거: 사용자 요청(2026-10-22 채팅): "호출 흐름 어느 정도 강제하는 명세 작성. LangGraph는 일단 쓰지 말고."
- **TASK-015 진단 결과:** 정보를 미리 정확히 줘도 평균 보상이 기존 실행 간 편차(0.317~0.463) 안에 머물렀다. 즉 정보 검색보다 모호성 해소와 SQL 작성이 병목으로 보인다.
- **dev30 문제당 모호성:** 중요 모호성 평균 3.17개, 삭제된 지식 0.83개. 대부분 실행 전에 질문·스키마·지식만으로 알아챌 수 있는 유형이다(knowledge_linking 37, intent 29, knowledge 25, schema_linking 17).
- **질문 시점:** GPT-6 Luna는 실행 전에 먼저 질문했다(Luna A 54회 중 37회). DeepSeek은 실행 뒤에 질문했다(baseline 40회 중 24회). Luna는 첫 제출 통과율 0.38~0.40, phase 1 통과 15~17개였다. DeepSeek은 0.27~0.37, 12~14개였다(1회 실행. 모델 차이와 섞여 있음).
- **제출 피드백:** 실패 시 "not correct"뿐이다. phase 1 통과 중 첫 제출 실패 뒤 통과한 경우는 3~5건이다.
- **phase 2 손실:** 실험군의 20~23개 문제가 예산 소진으로 끝났다. Luna A에서는 phase 1 통과 시점에 남은 코인이 3 미만이라 phase 2에서 행동하지 못한 문제가 5개였다.
- **조사한 코드(기준 SHA):**
  - `agent/tool_loop.py`: `_allowed_actions()`(220행), `system_prompt()`(93행), `_observation()`(182행), `_session_loop()`(840행), 제출 가능 예산 검사(878행), `MAX_MODEL_ACTIONS = 24`
  - `agent/server.py`: `create_app()`의 하네스·oracle 설정
  - `harness/baseline`
  - `agent/model.py`: `AGENT_MODEL_ID`, `AGENT_REASONING_EFFORT`

미확정 사항: 작업 브랜치(아래 "전달 정보"의 기본값은 `exp/staged-flow`. 사용자가 다른 브랜치를 지정하면 따른다).

## 목표
- `AGENT_FLOW=staged`로 켜면, phase마다 에이전트가 단계 순서를 따른다.
  1. 이해: 정보 조회 + 열린 질문 목록 작성
  2. 질문: 묶어서 질문
  3. 검증: SQL 시험
  4. 제출
- 각 단계에서 허용하지 않은 행동은 코인 없이 거부되고, 현재 단계와 허용 행동이 안내된다.
- 사용자는 결과 JSON에서 단계 전환 기록과 선언된 질문 목록을 보고, 자유 루프와 점수·행동·예산을 비교할 수 있다.
- 변수가 없으면 기준 SHA와 같은 자유 루프로 동작한다.

## 범위

### 1. 단계형 흐름 모드 (코드)
**켜는 방법**
- `AGENT_FLOW=staged`. 값이 없거나 `free`이면 기존 루프다.
- 그 외 값이면 서버 시작 시 오류를 낸다.
- `/health`에는 모드가 켜졌을 때만 `flow` 필드(모드, 설정값)를 추가한다. `tools`는 공식 9개 그대로다.

**단계와 허용 행동(phase마다 처음부터 적용)**

| 단계 | 허용 행동 | 다음 단계로 가는 조건 |
|---|---|---|
| understand | 정보 조회 6개, `plan` | `plan` 실행. 열린 질문이 있으면 ask, 없으면 verify |
| ask | `ask_user`, `plan_done` | 선언한 질문 수만큼 묻거나 질문 상한 도달, 또는 `plan_done` → verify |
| verify | `execute_sql`, `get_column_meaning`, `get_knowledge_definition`, `reopen`(phase당 1회), `ready` | `ready` → submit. `reopen` → ask |
| submit | `submit_sql` | 통과하면 phase 전환(phase 2는 understand부터). 실패하면 verify로 돌아감 |

- `plan`, `plan_done`, `reopen`, `ready`는 코드가 소유하는 **로컬 행동**이다.
  - 공식 도구가 아니고 코인 0이며, 응답 1회는 모델 행동 1회로 센다(기존 `consult`와 같은 취급).
  - `/health.tools`에는 넣지 않는다. 이름은 구현자가 정해도 되며, 정한 이름은 테스트로 고정한다.
- **`plan` 인자:** 열린 질문 목록과, 묻지 않고 가정할 사항 목록이다. 형식은 구현자가 정하고 테스트로 고정한다.
  - 예: `{"questions": ["..."], "assumptions": ["..."]}`
  - 이 목록은 state와 결과 JSON에 기록하고, 다음 관찰에 확인용으로 다시 보여 준다.
- **질문 상한:** phase당 `ask_user` 횟수 상한을 둔다. 기본값과 근거는 구현자가 정하고, 환경 변수로 바꿀 수 있게 한다.
- **`reopen`:** 실행 결과로 새 모호성이 드러났을 때 질문 단계로 한 번 돌아간다. 인자로 이유와 질문 목록을 받는다.
- **제출 전 시험:** submit 단계로 가려면 그 phase에서 `execute_sql`을 1회 이상 실행해야 한다.
  - 예외: 남은 예산이 `SUBMIT_COST + EXECUTE_SQL_COST`보다 적으면 시험 없이 제출할 수 있다.
- **막힘 방지:** 현재 단계에서 쓸 수 있는(살 수 있는) 행동이 없으면 코드가 submit 단계로 자동 전환하고 그 사실을 관찰에 알린다.
  - 예: understand에서 남은 예산이 제출 비용뿐인 경우
- **허용하지 않은 행동:** 기존 거부 경로처럼 코인 없이 거부한다. 거부 관찰에는 현재 단계와 허용 행동 목록을 적는다. 거부 응답도 모델 행동 1회로 센다.

**예산 규칙**
- 어느 단계에서든, 실행 후 남는 예산이 `SUBMIT_COST`보다 적어지는 비제출 행동은 실행하지 않고 거부한다(코인 0). 그러면 제출 기회가 항상 남는다.
- phase 1에 한해, 비제출 행동이 남은 예산을 `SUBMIT_COST + PHASE2_RESERVE` 아래로 떨어뜨리면 거부한다.
  - `PHASE2_RESERVE`는 환경 변수로 정하고 기본값은 0이다. 이 규칙은 비교 실험의 별도 조건으로 켠다.
  - 제출 행동에는 예약을 적용하지 않는다.

**기록**
- `state["flow"]`에 다음을 기록한다: 모드, 설정값, phase별 단계 전환 목록(행동 번호, phase, from, to, 이유), 선언된 질문·가정, `reopen` 사용 여부.
- 모든 `model_call` event에 현재 `stage`와 `flow` 설정 요약을 넣는다. 러너는 `adk_events`를 결과 JSON에 복사하므로 결과 JSON에 남는다.
- 자유 루프에서는 이 필드들이 생기지 않는다.

**프롬프트**
- system 계약의 Rules에, 모드가 켜졌을 때만 단계 규칙을 덧붙인다.
- 관찰 머리말(`[action N | phase P | ...]`)에 현재 단계와 허용 행동을 넣는다.
- 문구는 구현자가 정하고 테스트에 고정한다. 기존 계약 문구는 바꾸지 않는다.

**다른 모드와의 조합**
- oracle-context 모드와 함께 쓰면 understand 단계에는 `plan`만 허용한다(정보 조회 도구가 없으므로). 이 조합이 작동함을 테스트한다.
- 서브에이전트가 있는 하네스와는 함께 쓰지 않는다. 시작 시 명확한 오류를 낸다.

### 2. 단계형 하네스 (`harness/staged/system.md`)
- 기준 3줄에 단계별 지침을 붙인다.
  - understand: 정의되지 않은 용어, 임계값, 의도가 열린 부분을 `plan`에 모은다. 칼럼명·출력 형식은 묻지 않는다.
  - ask: 주제별로 묶어서 묻는다.
  - verify: 값·형식·NULL을 확인하고 후보 SQL을 시험한다.
  - submit: 시험한 SQL을 제출한다.
- 서브에이전트는 두지 않는다.

### 3. 비교 분석 (코드)
- 결과 JSON만 읽는 분석 스크립트를 추가하거나 기존 `scripts/analyze_oracle_context.py`를 일반화한다.
- 기존 지표에 다음을 더한다.
  - 단계별 행동 수
  - 단계 위반 거부 수
  - `plan` 질문 수와 실제 `ask_user` 수
  - `reopen` 사용 수
  - phase 1 통과 시점의 남은 코인
  - phase 2에서 행동을 1회 이상 한 문제 수
  - 예산 규칙으로 거부된 행동 수
- 같은 조건의 여러 회 실행을 받아 조건별 평균과 범위(최소~최대)를 보여 준다.
- 머리말에 실행 횟수와 기존 편차 범위를 적는다.

### 4. 실행 (사용자가 실행, AC-9)
- 공통 조건: DeepSeek V4.1 Flash, dev30 calibrated, 전체 툴(oracle 모드 끔), 동시성은 사용자 선택.
- 비교 조건:
  - F: 자유 루프 + `harness/baseline`
  - S: 단계형 + `harness/staged`, `PHASE2_RESERVE=0`
  - S+R: 단계형 + `harness/staged`, `PHASE2_RESERVE=3`
  - 선택 조건 F+T: 자유 루프 + `harness/staged`의 지침 문구. 흐름 강제와 지침 문구의 효과를 분리하려면 돌린다.
- 조건마다 2회 이상 실행한다. 기존 F 결과(`tools_dev30_calibrated_task013_baseline_r1`)는 1회분으로 쓸 수 있다.

문서 산출물: docs/agent/tasks/TASK-016/implementation.md. 다음을 기록한다.
- 단계 규칙, 로컬 행동 이름과 인자, 설정값과 기본값의 근거, 예산 규칙
- 실행 명령(조건별 환경 변수, 하네스, 결과 이름), 검증 결과

보존 정책: implementation.md·review.md는 로컬 작업 기록이다. 사용자 확인 후 spec.md·final.md를 한 번에 커밋한다(브랜치 `exp/staged-flow`). final.md에는 조건별 결과 표와 해석을 담는다.

보고서 외 문서 수정: 없음.

## 제약
- `AGENT_FLOW`가 없거나 `free`이면 기준 SHA와 다음이 같아야 한다: 에이전트 동작, 프롬프트, 행동 집합, `/health`, 결과 기록. 기존 테스트가 전부 통과해야 한다.
- 공식 도구 이름·인자·비용, `/health.tools`(공식 9개), 채점·보상 기록, phase 전환, 결과 JSON의 기존 필드는 바꾸지 않는다. 필드 추가는 가능하다.
- 행동 한도 24회, 세션 1500초, 응답 해석(JSON·DSML·MiMo 형식), 대화 기록 방식, `execute_sql` 여러 문장 거부는 바꾸지 않는다.
- 단계 규칙은 에이전트 행동만 제한한다. 공식 서비스에 보내는 요청 형식과 비용은 그대로다.
- 정답·테스트 케이스·GT 파생 정보는 에이전트 입력에 넣지 않는다. 테스트는 GT·`private/`·`results/`를 읽지 않는다.
- LangGraph 등 새 에이전트 프레임워크 의존성을 추가하지 않는다. 기존 `tool_loop` 안의 상태 기계로 구현한다.
- `AGENT_MODEL_ID`, `AGENT_REASONING_EFFORT`, `AGENT_ORACLE_CONTEXT_DIR`의 기존 동작은 바꾸지 않는다.

## 범위 밖
- main 병합, LangGraph 도입, 의미 계층(semantic layer) 도구
- 하네스 서브에이전트와의 조합
- 하네스 지침 내용의 반복 튜닝
- 원본(비 calibrated) 평가, dev30 밖의 문제

## 완료 조건
- AC-1 (모드 선택): `AGENT_FLOW` 없음·`free`면 자유 루프, `staged`면 단계형으로 동작한다. 잘못된 값이면 시작 시 오류가 난다. `/health.tools`는 공식 9개이고, 모드가 켜졌을 때만 `flow` 필드가 생긴다. 테스트로 확인한다.
- AC-2 (단계 전환): mock 모델·서비스로 다음 경로가 기대대로 동작함을 테스트한다.
  - understand → (질문 있음) ask → verify → submit → 통과
  - (질문 없음) understand → verify
  - submit 실패 → verify 복귀
  - `reopen` 1회 허용, 2회째 거부
  - phase 2가 understand부터 다시 시작
- AC-3 (단계 위반): 각 단계에서 허용하지 않은 행동은 서비스 호출 없이 코인 0으로 거부된다. 관찰에 현재 단계와 허용 행동이 나온다. 거부도 모델 행동 1회로 센다. 테스트로 확인한다.
- AC-4 (제출 전 시험과 막힘 방지): 그 phase에서 `execute_sql` 없이 submit으로 가는 것은 거부된다(예산 예외 포함). 살 수 있는 행동이 없으면 submit으로 자동 전환된다. 테스트로 확인한다.
- AC-5 (예산 규칙): 비제출 행동이 제출 비용 이하로 예산을 떨어뜨리면 거부된다. `PHASE2_RESERVE`가 0과 3일 때 phase 1 거부 경계가 각각 맞다. 제출에는 예약이 적용되지 않는다. 테스트로 확인한다.
- AC-6 (기록): `state["flow"]`와 모든 `model_call` event의 `stage`가 기록된다. 선언한 질문·가정과 전환 목록이 결과 상태에 남는다. 자유 루프에서는 이 필드가 없다. 테스트로 확인한다.
- AC-7 (조합): oracle-context 모드와 함께 쓰면 understand 단계에 `plan`만 허용되고 정상 진행된다. 서브에이전트 하네스와 함께 쓰면 시작 시 오류가 난다. 테스트로 확인한다.
- AC-8 (기존 동작 불변·전체 테스트): 자유 루프에서 `harness/baseline` 프롬프트와 행동 집합이 기준 SHA와 같다. 전체 스위트(`.venv\Scripts\python.exe -m unittest discover -s tests`)가 통과한다. 명령과 결과를 implementation.md에 기록한다.
- AC-9 (실행 경로): 조건 F·S·S+R(선택 F+T)의 실행 명령을 implementation.md에 적는다(환경 변수, 하네스, 결과 이름에 `flow_<조건>_r<회차>` 포함). 사용자가 1문제 smoke로 단계 기록과 거부 동작을 결과 JSON에서 확인한다.
- AC-10 (실행·분석): 사용자가 조건별로 2회 이상 실행하고, 분석 스크립트로 비교표를 만든다. 조건별 평균·범위, 첫 제출 통과율, phase 1 통과 시점의 남은 코인, phase 2 시도 수, 단계 지표를 final.md에 담는다. 차이가 회차 간 범위보다 작으면 결론을 내리지 않는다. 일부만 실행되면 실행된 범위만 보고한다.

## 조사할 사항
- 기존 거부 경로(`ActionError`)와 `consult` 처리 방식을 재사용해, 로컬 행동과 단계 위반 거부를 같은 기록 형식으로 남길 수 있는지 확인한다.
- 질문 상한 기본값: 예산 공식(6 + 2 × 모호성 수 + 2 × patience)과 dev30의 문제당 모호성 수(중요 3.17 + 지식 0.83)를 근거로 정한다.
- 24회 행동 한도 안에서 로컬 행동(`plan`, `ready` 등)이 차지하는 비율. 한도에 걸리는 문제가 늘지 않는지 smoke로 확인한다.
- 관리(DML·DDL) 문제에서 "제출 전 시험" 규칙이 적절한지 확인한다. 변경 대상 행을 SELECT로 시험하는 기존 하네스 지침과 맞는지 본다.
- DSML·MiMo 형식 응답으로 온 로컬 행동도 기존 파서로 읽히는지 확인한다.

## 명세 변경 이력
| 버전 | 변경 내용·이유 | 구현·리뷰에 미치는 영향 |
| --- | --- | --- |
| 1 | 최초 명세 | 단계형 흐름 모드(단계·로컬 행동·예산 규칙·기록), 단계형 하네스, 비교 분석, 관련 테스트 |

## 전달 정보
명세 경로·버전: docs/agent/tasks/TASK-016/spec.md, 1
기준 커밋: 0c00526c153c34e2caac44913721ecb4f4845411 (`exp/oracle-context` HEAD. 모델 선택 환경 변수와 MiMo 응답 파서를 포함)
작업 브랜치: `exp/staged-flow` (기준 SHA에서 생성)
작업 폴더: docs/agent/tasks/TASK-016/
커밋 제목 형식: [TASK-016] <type>: <변경 요약>
기존 미커밋 변경: 메인 체크아웃의 `docs/agent/tasks/TASK-015/`(TASK-015 로컬 문서). 섞지 않는다.
명세 파일 위치: 이 명세는 세션 worktree(`.claude/worktrees/task-015-spec-v2-b9d9c6/docs/agent/tasks/TASK-016/spec.md`)에 작성됐다. 구현 전에 메인 체크아웃의 같은 경로로 옮긴다.
직접 확인 방법:
1. 전체 테스트 스위트를 실행한다.
2. `AGENT_FLOW=staged`, `AGENT_HARNESS_DIR=harness/staged`로 1문제 smoke를 실행한다.
3. 결과 JSON에서 다음을 확인한다.
   - `adk_events[*].stage`가 understand → ask/verify → submit 순서로 바뀐다.
   - `plan` 질문 목록이 기록된다.
   - 단계 위반 거부의 비용이 0이다.
4. 조건별 2회 이상 실행 후 분석 스크립트로 비교표를 만든다.
