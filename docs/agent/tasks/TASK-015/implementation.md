# TASK-015 구현 보고서

작업 ID: TASK-015
명세 버전: 2
대상 코드 버전: c0af9cb49ef120f5466fd4648780afb189870634
상태: ready_for_review
요약: `AGENT_ORACLE_CONTEXT_DIR`로 켜는 진단 실험 모드를 추가했다. 컨텍스트 생성 스크립트, B 큐레이션 절차와 기록, 실험용 하네스, 분석 스크립트도 추가했다. dev30 × A/B/C 컨텍스트 파일과 B 큐레이션 기록 60건(phase 1 30건, phase 2 30건)을 만들었다. 실험 모드가 꺼져 있으면 기존 동작·프롬프트는 바뀌지 않는다.
근거: `agent/oracle_context.py`, `agent/tool_loop.py`, `agent/server.py`, `scripts/build_oracle_context.py`, `scripts/analyze_oracle_context.py`, `tests/test_oracle_context.py`
미확정 사항: 생성 파일 위치(아래 "필요한 판단" 1).

> 진단 실험이다. A/B/C 모두 GT로 고른 지식을 받는다. A는 정답 SQL의 테이블 이름도 받는다. 결과는 에이전트 성능이 아니다.

## 커밋과 검토 범위
기준 커밋: 4b25f83b12c9c7d5da23b4df6e2b39f98a60ff00
코드 커밋: c0af9cb49ef120f5466fd4648780afb189870634
작업 커밋 목록: c0af9cb `[TASK-015] feat: add the oracle-context diagnostic experiment mode`
검토 명령: `git diff 4b25f83b12c9c7d5da23b4df6e2b39f98a60ff00 c0af9cb49ef120f5466fd4648780afb189870634 --`
브랜치:
- 커밋은 세션 worktree 브랜치 `claude/task-015-spec-v2-b9d9c6`에서 만들었다.
- 메인 체크아웃의 `exp/oracle-context`를 `git merge --ff-only`로 같은 SHA까지 전진시켰다. 그래서 `exp/oracle-context` = c0af9cb이다.
- main에는 커밋하지 않았고 push도 하지 않았다.

현재 코드와 대상 SHA의 차이: 없음(작업 트리 clean, 추적 파일 변경 없음).
기존 사용자 변경: 메인 체크아웃의 미커밋 `docs/agent/tasks/TASK-015/spec.md`만 있었다. 손대지 않았다.

## 변경 내용

### 1. 에이전트 실험 모드 (`agent/oracle_context.py`, `agent/tool_loop.py`, `agent/server.py`, `agent/tools.py`)

**켜는 방법:** `AGENT_ORACLE_CONTEXT_DIR=<그룹 디렉터리>`. 상대 경로는 프로젝트 루트 기준이다.
- 서버 생성 시 디렉터리와 `manifest.json`(실험군 이름)을 확인한다. 없으면 `OracleContextError`로 시작에 실패한다.
- 변수가 없거나 비어 있으면 모드는 꺼진다. 이때 코드 경로는 기준 SHA와 같다.

**세션 시작 시(`_start_oracle_context`):**
- `<dir>/<task_id>.json`을 읽는다.
- 파일이 없거나, 실험군·task_id가 다르거나, `context_sha256`가 내용과 다르면 HTTP 500 `Oracle context error: ...`로 실패한다.
  - 모델·서비스 호출과 차감은 일어나지 않는다. 기존 모드로 대신 돌지도 않는다.
- task_id는 `^[A-Za-z0-9][A-Za-z0-9_.-]*$`만 허용한다. 경로 이탈을 막기 위해서다.

**허용 행동:**
- `execute_sql`, `ask_user`, `submit_sql`만 둔다(하네스에 서브에이전트가 있으면 `consult`도 둔다).
- 카탈로그(`catalog(..., specs)`)에도 이 셋만 나온다.
- 정보 조회 도구 6개는 기존 파서 경로에서 `Unknown action '...'. Available actions: execute_sql, ask_user, submit_sql.`로 거부된다. 코인은 0이다.
- 계약 문구(Rules)는 그대로다.

**`/health`:**
- `tools`는 공식 9개 그대로다.
- 실험 모드일 때만 `experiment: {"oracle_context": "<그룹>", "budget_offset": 3.0}` 필드를 추가한다. 모드가 꺼져 있으면 `/health`는 기존과 같다.

**예산:**
- 세션에서 한 번만 `budget_remaining -= 3`을 적용한다. `experiment` 키 유무로 판단한다.
- `initial_budget`(공식 값)은 바꾸지 않는다.
- 초기 프롬프트는 `initial_budget - 3`을 "You have N bird-coins"로 안내한다.

**기록:** `state["experiment"]`와 모든 `model_call` event의 `experiment` 필드에 다음을 남긴다.
`{"oracle_context", "budget_offset": 3.0, "context_sha256", "context_chars": {"phase1", "phase2"}}`
- 러너는 `adk_events`를 결과 JSON에 복사하므로 이 기록이 결과 JSON에 남는다.
- 모드가 꺼져 있으면 이 키는 생기지 않는다.

**주입 형식(테스트에 고정):**
- phase 1: 초기 user 메시지의 `User Query` 뒤, 예산 안내 앞에 다음 섹션을 넣는다. 빈 부분은 `(none provided)`로 표시한다.
  ```
  Provided database context:
  The schema, column meanings and external knowledge below are the parts provided as relevant to this task.

  Schema:
  ...
  Column meanings:
  ...
  External knowledge:
  ...
  ```
- phase 2: 전환 관찰의 `Follow-up question:\n<질문>` 뒤에 추가 블록을 붙인다.
  - 머리말은 `Additional database context for the follow-up question:`이다.
  - 비어 있지 않은 부분만 넣는다. 추가 항목이 하나도 없으면 아무것도 붙이지 않는다.
- 문구에 GT·정답·oracle이라는 표현은 없다. 테스트로 확인했다.

### 2. 실험용 하네스 (`harness/oracle_context/system.md`)
- 기준 3줄에 다음 단계를 붙였다.
  1. 제공된 컨텍스트를 읽는다. 정보 조회 도구는 없다. 컨텍스트 밖의 정보가 필요하면 `execute_sql`로 확인한다.
  2. 남은 모호성은 `ask_user`로 묻는다(칼럼명·출력 형식은 묻지 않는다).
  3. `execute_sql`(1코인)로 시험한 뒤 제출한다.
- 2·3단계는 review_finder_v2의 문구를 이 모드에 맞게 옮긴 것이다.
- 서브에이전트는 없다.

### 3. 컨텍스트 생성 (`scripts/build_oracle_context.py`)
명령: `curation-inputs --phase N`, `record-selections --phase N`, `check-curation-inputs`, `build [--groups ..] [--generated-at ..]`, `summary`.
기본 입력:
- `private/lite30.calibrated.jsonl`
- `eval_sets/lite30.manifest.json`
- `vendor/.../bird-interact-lite/<db>/`

출력은 `private/oracle_context/`이다.

**원문·직렬화 규칙(공식 `db_environment/server.py`와 같은 형식):**

| 부분 | 규칙 |
|---|---|
| schema | `<db>_schema.txt`를 텍스트 모드로 읽는다(CRLF→LF, Linux 컨테이너의 `open()`과 같다). 각 블록은 `CREATE TABLE` 줄부터 다음 `CREATE TABLE` 줄 직전까지이며, 샘플 행이 블록에 포함된다. 고른 블록을 스키마 순서로 이어 붙이고 끝 공백만 정리한다. C는 파일 원문 그대로다. |
| column meanings | 키를 소문자로 바꾼 dict에서 `db|table|column`의 table이 선택 테이블인 항목을 파일 순서대로 고른다. 직렬화는 `json.dumps(..., indent=2)`(`ensure_ascii` 기본값, `/all_column_meanings`와 같다)다. C는 전체 dict 그대로다. |
| knowledge | 선택 지식을 kb 파일 순서로 고르고 `KNOWLEDGE_VISIBLE_FIELDS`(id, knowledge, description, definition)만 남긴다. 직렬화는 `json.dumps([...], indent=2)`(`/knowledge` 전체 조회와 같다)다. |
| 빈 부분 | 선택 항목이 없으면 빈 문자열이다. |

**지식(세 실험군 공통):**
- phase 1: `external_knowledge`에 `children_knowledge`(int·list·-1)를 재귀로 더한다. 그다음 `knowledge_ambiguity[].deleted_knowledge`를 뺀다.
  - 삭제된 항목을 거쳐 내려가는 자식은 유지한다. 공식 도구에서도 보이기 때문이다.
- phase 2: `follow_up.external_knowledge`에 같은 규칙을 적용한다. phase 1에 없던 항목만 넣는다.

**A의 테이블 판정(`sql_tables`):**
- 주석과 작은따옴표 문자열을 제거한다. 달러 인용(함수·DO 본문)은 구분자만 지우고 본문을 SQL로 본다.
- 식별자(따옴표 식별자 포함, 소문자 비교)를 스키마 테이블 이름과 대조한다. `public.t` 같은 접두어는 각 부분을 따로 비교한다.
- 별칭은 테이블 이름 자체로 찾는다(`FROM t AS x`의 `t`).
- 보고용으로 `FROM/JOIN/UPDATE/INTO/TABLE/USING/ON/REFERENCES` 바로 뒤에 나온 이름을 따로 구해, 토큰으로만 일치한 경우를 표시한다.
- 처음 구현은 달러 인용 본문을 제거했다. 그 결과 함수 정의 문제 2개(mental_M_6, vaccine_M_4)에서 테이블이 0개였다. 본문을 남기도록 고쳐 해결했다(AC-2 확인 참조).

**파일 형식** `private/oracle_context/<A|B|C>/<task_id>.json`:
- 필드: `experiment`, `diagnostic`(실험 표시), `task_id`, `db_name`
- `phase1` / `phase2`: `{schema, column_meanings, knowledge, tables, knowledge_ids}`. phase2는 추가분만 담는다.
- `context_sha256`: 주입 텍스트 6개만 정규 JSON으로 해시한 값이다.
- `generation`: `{experiment, inputs: {상대경로: sha256}, generated_at, serialization}`
- 그룹마다 `manifest.json`(실험군, 실험 표시, 과제 목록, 생성 기록)이 있다.

**결정성:**
- 생성 시각 외의 모든 필드는 입력에서 결정적으로 나온다.
- `--generated-at`을 고정하면 파일 바이트가 같다(테스트로 확인).
- `context_sha256`는 생성 시각과 무관하다.
- 이번 생성은 `--generated-at 2026-10-22T00:00:00Z`로 했다.

### 4. B 큐레이션
- 지시문 `scripts/oracle_context_curator.md`를 커밋했다. GT는 없다.
- 입력 묶음은 `private/oracle_context/B/curation/<task>.phase<N>.md`이다. 구성:
  - 지시문
  - phase 1은 `amb_user_query`. phase 2는 `follow_up.query`와 phase 1에서 고른 테이블 목록.
  - GT 지식 텍스트. phase 2는 phase 1과 phase 2 지식을 합친 것.
  - 스키마 원문 전체, 칼럼 의미 원문 전체(공식 형식)
- 넣지 않은 것: 정답 SQL, 테스트 케이스, `sql_snippet`, 다른 실험군 선택, phase 1 질문(phase 2 큐레이터에게).
- 실행:
  - 이 Claude Code 세션이 문제·phase마다 서브에이전트 1개를 띄웠다(`general-purpose`, `model: opus`(Opus 5.5), `effort: high`). 모두 60건이다.
  - phase 1 30건을 기록한 뒤 phase 2 묶음을 만들고 phase 2 30건을 실행했다.
  - 서브에이전트에게는 묶음 파일 하나만 읽고 다른 파일을 읽거나 명령을 실행하지 말라고 지시했다. 각 서브에이전트의 도구 사용은 2~3회(Read, 첫 batch는 차단된 Write 포함)였다.
- 원문 기록:
  - 응답 JSON 원문은 `B/raw/<task>.phase<N>.txt`에 그대로 저장했다.
  - phase 1 첫 20건은 서브에이전트가 직접 쓰려다 hook에 막혔다. 그래서 응답 본문의 JSON을 세션이 그대로 옮겨 저장했다. 이후 40건은 처음부터 JSON만 답하게 했다.
- `record-selections`가 `B/selections/<task>.json`에 다음을 저장한다.
  - phase별 `input_file`, `input_sha256`, `model: opus`, `effort: high`
  - `raw_output_file`, `raw_output`, 파싱한 `tables`, 스키마에 없어 뺀 `excluded_tables`, `reason`
  - 이번 60건에서 `excluded_tables`는 모두 비어 있다.
- 기록 시 묶음을 다시 만들어 저장된 묶음과 같은지 확인한다.

### 5. 분석 스크립트 (`scripts/analyze_oracle_context.py`)
- 결과 JSON만 읽는다. 사용법: `--run LABEL=path`를 여러 번 준다.
- 출력하는 지표:
  - 평균 보상, Phase 1·2 통과 수, 첫 phase 1 제출 통과율
  - ask_user·execute_sql·submit_sql 문제당 횟수
  - 코인 사용(`budget_used - budget_offset`)
  - 주 에이전트 입력·출력 토큰(`model_call.usage`만, 서브에이전트 제외)
  - 문제당 시간, 종료 사유(`final_response` 문구로 분류, `error` 포함)
  - 제공 컨텍스트 길이(`experiment.context_chars`)
  - 문제별 보상(P1/P2)
- 머리말에 "에이전트 성능 아님"과 기존 dev30 편차 범위(0.327~0.463)를 적는다.

## 완료 조건별 근거

- **AC-1:** `tests/test_oracle_context.py` `GenerationRuleTests`(합성 fixture, CRLF 스키마, 대소문자 섞인 칼럼 키, 삭제 지식과 그 자식).
  - 하위 지식이 재귀로 포함되고 삭제 지식이 빠진다: `test_knowledge_adds_children_recursively_and_drops_deleted_entries`
  - phase 2에는 새 항목만 들어간다: `test_phase_two_blocks_hold_only_new_items`
  - A는 블록 전체, C는 원문 전체다: `test_a_uses_whole_blocks_of_the_solution_tables_and_c_the_full_text`
  - 테이블 판정(문자열·주석 무시, 접두어, 함수 본문, `ON`): `test_table_detection_ignores_strings_and_comments_and_reads_prefixes`
  - B는 선택 기록으로부터 만든다: `test_b_blocks_come_from_the_selection_record`, `test_curator_output_keeps_schema_tables_and_reports_the_rest`
  - 공식 형식과 일치한다(ASCII 이스케이프, 공개 필드만): `test_injected_text_matches_the_official_response_format`
  - 결정성과 로더 호환: `test_build_is_deterministic_and_loadable`
- **AC-2:** 30문제 × A/B/C 파일 90개와 그룹별 manifest를 생성했다. 아래 "생성 결과" 참조.
  - A에서 테이블 0개인 문제: 없음(수정 후).
  - 토큰으로만 일치한 의심 사례: 없음.
  - 세 실험군의 지식 블록(텍스트와 ID, phase 1·2)이 30문제 모두 같다: `summary` 출력 `knowledge identical across A/B/C for every task: True`.
- **AC-3:** 선택 기록 60건(30 × phase 1, 30 × phase 2. dev30은 모두 후속 질문이 있다)에 모델·effort·입력 해시가 있다.
  - `check-curation-inputs` → `checked 60 curation inputs; problems: 0`.
  - 확인 내용: 묶음이 재생성 결과와 바이트 단위로 같다. 기록된 `input_sha256`와 일치한다. 정답 SQL·테스트 케이스·`sql_snippet` 문자열이 들어 있지 않다.
  - `public` 표시 5건은 snippet 문자열이 질문이나 공개 스키마 원문 안에 원래 있는 경우다. 예: mental_M_2 phase 2의 따옴표 친 용어는 공식 후속 질문 문장에 그대로 있다. 누출이 아니다.
  - 변조된 묶음은 실패한다: `test_the_leak_check_fails_on_a_tampered_bundle`.
- **AC-4:** `OracleModeTests.test_only_the_allowed_actions_are_listed_and_lookups_are_rejected_free`, `test_health_keeps_the_nine_tools_and_reports_the_experiment`.
- **AC-5:**
  - 주입: `test_context_is_injected_in_the_first_message_and_the_follow_up`(전체 문자열 고정), `test_an_empty_follow_up_block_adds_nothing`
  - 오류: `test_a_missing_context_file_fails_the_session_clearly`, `test_a_tampered_context_file_is_rejected`, `OracleSetupTests.test_a_bad_directory_stops_the_server_at_startup`, `test_a_context_file_of_another_group_or_task_is_rejected`
- **AC-6:** `test_budget_starts_three_coins_lower_and_the_experiment_is_recorded`(`initial_budget`은 18 그대로, 시작 15, state와 모든 `model_call`에 기록), `test_the_offset_is_taken_once_per_session`.
- **AC-7:**
  - 모드가 꺼지면 `_initial_prompt`·`system_prompt`가 기존 출력과 같다: `test_without_the_mode_prompt_and_actions_are_the_base_ones`
  - 기존 테스트(`EXPECTED_BASELINE_PROMPT` 포함)가 모두 통과한다.
  - 꺼진 경로에서 바뀐 것은 기본 인자뿐이다. `catalog(specs=None)`은 `ALLOWED_TOOLS`를 쓰고, `follow_up_context=""`이면 본문이 같다.
- **AC-8:** 전체 스위트 271개 통과(아래 표).
- **AC-9:** 실행 명령은 아래 "실행 방법"에 적었다. 1문제 smoke는 사용자가 실행한다.
- **AC-10:** 미실행(사용자 실행 예정). 분석 명령은 아래에 적었다.

## 생성 결과 (AC-2)

열 설명:
- `p1/p2`: 선택 테이블 수(phase 2는 추가분)
- `C p1`: DB 전체 테이블 수
- `knowledge`: 지식 수(phase 2는 추가분)
- `chars`: 주입 텍스트 3부분의 글자 수 합(phase 1 + phase 2 추가)

| task | db | A p1/p2 | B p1/p2 | C p1 | knowledge p1/p2 | chars A | chars B | chars C |
|---|---|---|---|---|---|---|---|---|
| archeology_3 | archeology | 2/0 | 4/0 | 14 | 0/2 | 8493+1085 | 13736+1085 | 43562+1085 |
| cross_db_4 | cross_db | 2/0 | 4/3 | 7 | 2/1 | 9502+246 | 18547+15404 | 33707+246 |
| cross_db_M_2 | cross_db | 1/0 | 3/0 | 7 | 0/0 | 3571+0 | 12900+0 | 33131+0 |
| cross_db_M_5 | cross_db | 1/0 | 1/0 | 7 | 1/0 | 5559+0 | 5559+0 | 33331+0 |
| cybermarket_4 | cybermarket | 1/0 | 3/0 | 9 | 0/0 | 4072+0 | 16895+0 | 38251+0 |
| disaster_2 | disaster | 4/0 | 6/0 | 10 | 1/0 | 16264+0 | 22513+0 | 40741+0 |
| fake_11 | fake | 4/0 | 9/0 | 9 | 1/1 | 15963+305 | 33305+305 | 33306+305 |
| fake_2 | fake | 4/0 | 4/0 | 9 | 1/0 | 13114+0 | 13114+0 | 33351+0 |
| fake_3 | fake | 4/0 | 9/0 | 9 | 1/1 | 15963+305 | 33305+305 | 33306+305 |
| fake_6 | fake | 8/0 | 9/0 | 9 | 5/0 | 31793+0 | 34516+0 | 34517+0 |
| gaming_11 | gaming | 2/0 | 2/0 | 8 | 2/1 | 10345+401 | 10345+401 | 37313+401 |
| gaming_2 | gaming | 2/0 | 2/0 | 8 | 1/1 | 9958+401 | 9958+401 | 36926+401 |
| mental_1 | mental | 5/0 | 8/1 | 9 | 1/0 | 15376+0 | 24796+4542 | 29340+0 |
| mental_10 | mental | 6/0 | 8/0 | 9 | 6/0 | 21649+0 | 27551+0 | 31518+0 |
| mental_2 | mental | 4/0 | 8/0 | 9 | 2/0 | 13248+0 | 25787+0 | 29754+0 |
| mental_3 | mental | 3/1 | 7/1 | 9 | 3/1 | 10366+4221 | 22987+4139 | 30417+677 |
| mental_M_2 | mental | 2/0 | 5/2 | 9 | 0/0 | 6351+0 | 16451+5983 | 28996+0 |
| mental_M_6 | mental | 1/0 | 2/0 | 9 | 1/0 | 3729+0 | 7247+0 | 29684+0 |
| museum_M_4 | museum | 1/0 | 1/0 | 14 | 0/0 | 3209+0 | 3209+0 | 51645+0 |
| news_11 | news | 1/0 | 1/0 | 8 | 2/2 | 6873+578 | 6873+578 | 29407+578 |
| news_6 | news | 2/1 | 3/1 | 8 | 3/0 | 9650+1983 | 12466+1983 | 29556+0 |
| news_M_4 | news | 1/0 | 1/0 | 8 | 1/0 | 4216+0 | 4216+0 | 28976+0 |
| robot_8 | robot | 3/0 | 3/0 | 10 | 3/0 | 10762+0 | 10762+0 | 36796+0 |
| robot_M_3 | robot | 2/0 | 3/0 | 10 | 2/0 | 6165+0 | 6973+0 | 36609+0 |
| solar_4 | solar | 2/1 | 2/0 | 8 | 2/0 | 5973+2502 | 5973+0 | 26568+0 |
| solar_M_5 | solar | 1/0 | 1/0 | 8 | 1/0 | 2884+0 | 2884+0 | 26304+0 |
| vaccine_4 | vaccine | 2/0 | 3/0 | 7 | 1/1 | 11330+223 | 16295+223 | 32444+223 |
| vaccine_5 | vaccine | 2/0 | 4/0 | 7 | 2/0 | 10657+0 | 19181+0 | 32691+0 |
| vaccine_M_4 | vaccine | 1/0 | 1/0 | 7 | 1/0 | 5192+0 | 5192+0 | 32421+0 |
| virtual_M_4 | virtual | 5/0 | 8/1 | 14 | 5/0 | 17581+0 | 26450+3463 | 43450+0 |

초기 메시지 컨텍스트 섹션 길이(글자 수):

| 실험군 | 최소 | 평균 | 최대 | phase 2 블록이 붙는 문제 수 |
|---|---|---|---|---|
| A | 3,067 | 10,512 | 31,976 | 11 |
| B | 3,067 | 15,851 | 34,699 | 13 |
| C | 26,486 | 34,118 | 51,842 | 9 |

추가 관찰:
- B가 고른 테이블(phase 1 ∪ phase 2)은 30문제 모두에서 A의 테이블을 포함한다. 따라서 이번 데이터에서 A와 B의 차이는 B가 더한 테이블뿐이다(빠진 테이블 0건).
- 다만 시점은 다를 수 있다. 예: solar_4는 A가 phase 2에 추가하는 테이블을 B가 phase 1에서 이미 주므로, B의 phase 2 추가가 0이다.

조사할 사항 결과:
- **데이터 폴더:** compose가 `./vendor/BIRD-Interact/BIRD-Interact-ADK`를 `/benchmark`로 읽기 전용 마운트한다. `settings.db_data_path` = `/benchmark/bird-interact-lite`이므로 같은 파일이다.
  - calibrated wrapper(`benchmark/calibration/db_env_calibration.py`)는 `/schema`·`/all_column_meanings`·`/knowledge` 경로를 바꾸지 않는다.
  - 공식 응답을 직접 받는 방식은 쓰지 않았다. 실제 서비스 응답과 바이트 비교는 하지 않았다(미검증 항목 참조).
- **calibrated vs 원본 GT의 `sol_sql`:** 다른 곳은 1건(cross_db_M_5 phase 2)이다. 판정되는 테이블 집합은 같다. A는 calibrated 입력을 사용했다.
- **C 길이:** 초기 섹션은 2.6만~5.2만 자다. 출력 한도·세션 시간 영향은 smoke에서 확인해야 한다(미실행).
- **phase 2 스키마:** 공식 `/schema`도 파일 원문을 캐시해 돌려주므로 DB 변경 후에도 같다. 미리 만든 텍스트와 공식 응답은 같은 기준이다.

## 실행 방법

### 생성 파일 위치
- 이 세션은 worktree(`C:\Users\SSAFY\Desktop\db_agent\.claude\worktrees\task-015-spec-v2-b9d9c6`)에서 실행됐다. 메인 체크아웃 쓰기는 hook으로 막혀 있다.
- 그래서 생성 파일은 worktree의 `private\oracle_context\`(git ignore 대상)에 있다.
  - `A\`, `B\`, `C\`: 각 30개 + `manifest.json`
  - `B\curation\`: 60개, `B\raw\`: 60개, `B\selections\`: 30개
- 메인 체크아웃에서 실행하려면 둘 중 하나를 고른다.
  - 절대 경로를 그대로 지정한다.
  - 메인으로 복사한다. 메인의 `private\oracle_context\`에는 처음 시도 때 만든 phase 1 묶음 30개(내용 동일)와 빈 `raw\`가 남아 있다. 복사하면 함께 덮인다.
    ```powershell
    Copy-Item -LiteralPath 'C:\Users\SSAFY\Desktop\db_agent\.claude\worktrees\task-015-spec-v2-b9d9c6\private\oracle_context' -Destination 'C:\Users\SSAFY\Desktop\db_agent\private' -Recurse -Force
    ```
- worktree를 정리하면 worktree 쪽 파일은 사라진다. 복사하거나 다시 생성해 두는 것을 권한다.

### 재생성(메인 체크아웃에서, 선택)
```powershell
.venv\Scripts\python.exe scripts\build_oracle_context.py build --generated-at 2026-10-22T00:00:00Z
.venv\Scripts\python.exe scripts\build_oracle_context.py check-curation-inputs
.venv\Scripts\python.exe scripts\build_oracle_context.py summary
```
B 큐레이션을 처음부터 다시 하려면 다음 순서다.
1. `curation-inputs --phase 1`
2. 서브에이전트 30건, 응답을 `B\raw\<task>.phase1.txt`에 저장
3. `record-selections --phase 1`
4. `curation-inputs --phase 2`
5. 서브에이전트 30건, 응답을 `B\raw\<task>.phase2.txt`에 저장
6. `record-selections --phase 2`
7. `build`

### smoke와 실험 실행(AC-9/AC-10, 메인 체크아웃 PowerShell)
주의:
- `scripts/run-evaluation.ps1`은 6000 포트에 에이전트가 이미 떠 있으면 재사용한다. `Assert-BaselineAgent`는 `experiment` 필드를 확인하지 않는다.
- 실험군을 바꾸기 전에 6000 포트에 에이전트가 없는지 확인한다. 실행 후 `/health.experiment`로 실험군을 확인한다.
- 러너가 직접 띄운 에이전트는 종료 시 스스로 내린다.

```powershell
# 실험군 A 1문제 smoke (B, C는 디렉터리와 이름만 바꾼다)
$env:AGENT_ORACLE_CONTEXT_DIR = 'private\oracle_context\A'   # 복사하지 않았다면 worktree 절대 경로
$env:AGENT_HARNESS_DIR = 'harness\oracle_context'
.\scripts\run-evaluation.ps1 -Calibrated -Limit 1 -ResultName tools_dev30_calibrated_oracle_context_A_smoke -OracleResultName oracle_dev30_calibrated_oracle_context_A_smoke
```
smoke 결과 JSON(`results\tools_dev30_calibrated_oracle_context_A_smoke.json`)에서 확인할 것:
- `adk_events[0].prompt[1].content`에 `Provided database context:` 섹션이 있고, `You have <initial_budget-3> bird-coins`로 시작 예산이 안내된다.
- `adk_events[0].prompt[0].content`의 `Available actions:`에 `execute_sql`, `ask_user`, `submit_sql`만 있다.
- `adk_events[*].experiment`가 `{"oracle_context": "A", "budget_offset": 3.0, "context_sha256": ..., "context_chars": ...}`이다.

본 실행(실험군당 1회, 30문제):
```powershell
$env:AGENT_HARNESS_DIR = 'harness\oracle_context'
foreach ($g in 'A','B','C') {
    $env:AGENT_ORACLE_CONTEXT_DIR = "private\oracle_context\$g"
    .\scripts\run-evaluation.ps1 -Calibrated -Limit 30 -ResultName "tools_dev30_calibrated_oracle_context_${g}_r1" -OracleResultName "oracle_dev30_calibrated_oracle_context_${g}_r1"
}
Remove-Item Env:AGENT_ORACLE_CONTEXT_DIR; Remove-Item Env:AGENT_HARNESS_DIR
```
분석:
```powershell
.venv\Scripts\python.exe scripts\analyze_oracle_context.py `
  --run baseline=results\tools_dev30_calibrated_task013_baseline_r1.json `
  --run A=results\tools_dev30_calibrated_oracle_context_A_r1.json `
  --run B=results\tools_dev30_calibrated_oracle_context_B_r1.json `
  --run C=results\tools_dev30_calibrated_oracle_context_C_r1.json
```
같은 스크립트를 baseline 결과 하나에만 돌려 기록과 대조했다. 평균 0.393, P1 13, P2 9로 결과 파일의 `metrics`와 일치했다.

## 실행한 검증
| 명령·검증 | 실행 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | 메인 체크아웃, `exp/oracle-context` = c0af9cb, 작업 트리 clean(미추적 spec.md만) | exit 0, `Ran 271 tests ... OK (skipped=3)` | 기존 테스트(baseline 프롬프트·도구 계약 포함) + 신규 24개 |
| 같은 명령 | worktree(c0af9cb 커밋 전 동일 내용), `vendor/` 없음 | 224개 중 error 2 | `test_calibration`·`test_command_code_simulator`가 `vendor` 모듈 import 실패. 환경 차이이고, 위 메인 실행에서 통과 |
| `python -m unittest discover -s tests -p test_oracle_context.py` | worktree | 24 OK | 신규 테스트만 |
| `build_oracle_context.py check-curation-inputs` | 실제 calibrated 입력·vendor 데이터 | `checked 60 curation inputs; problems: 0` | AC-3 |
| `build_oracle_context.py build --generated-at 2026-10-22T00:00:00Z` + `summary` | 동일 | 90개 파일, 지식 일치 True, A 의심 사례 none | AC-2 |
| `agent.oracle_context.load_oracle_context`로 A/B/C 90개 로드 | worktree 생성 파일 | 모두 해시 검증 통과 | 실험 모드 로더와 생성 파일 호환 |
| `analyze_oracle_context.py --run baseline=...task013_baseline_r1.json` | 기존 결과 파일 | 0.393 / 13 / 9 | 결과 파일 metrics와 일치 |
| `git diff --check` | 커밋 전 | 출력 없음 | |
| `git status --short --untracked-files=all` | worktree와 메인, 커밋 후 | worktree: 이 보고서만 미추적. 메인: `docs/agent/tasks/TASK-015/spec.md`만 미추적 | `private/`는 ignore 대상, 저장소 밖 임시 파일은 세션 scratchpad에만 있음 |

검증 코드와 최종 SHA 일치 근거:
- 전체 스위트는 `exp/oracle-context`를 c0af9cb로 전진시킨 직후의 메인 체크아웃에서 실행했다. 추적 파일 변경은 없었다.
- 생성·검사는 같은 코드(커밋 전 작업 트리 = 커밋 내용)로 실행했다.

## 미검증 항목과 제한
- 실제 서비스 대상 smoke와 A/B/C 본 실행(AC-9, AC-10)은 실행하지 않았다. 사용자가 실행한다.
- 주입 텍스트가 실행 중인 공식 `/schema`·`/all_column_meanings`·`/knowledge` 응답과 바이트 단위로 같은지는 서비스를 띄워 확인하지 않았다.
  - 서비스 코드(`_load_db_data`, `json.dumps(indent=2)`, `KNOWLEDGE_VISIBLE_FIELDS`, `_filter_knowledge`)와 같은 규칙으로 구현했다. 합성 fixture로 형식을 테스트했다.
  - C의 phase 1 schema는 파일 원문, column_meanings는 `json.dumps(전체, indent=2)`이므로 공식 응답과 같아야 한다.
- B 큐레이터가 지시대로 묶음 파일 하나만 읽었는지는 지시와 도구 사용 횟수(2~3회)로만 확인했다. 도구 접근을 기술적으로 막지는 않았다.
- C 컨텍스트(최대 5.2만 자)가 출력 한도·세션 시간에 주는 영향은 smoke에서 확인해야 한다.

## 리뷰 지적 처리
최초 검토이므로 해당 없음.

## 필요한 판단
1. **생성 파일 위치:** 생성 파일은 worktree의 `private\oracle_context\`에 있다. 메인으로 복사하는 것을 권한다(위 명령). 복사하지 않고 worktree를 정리하면 B 큐레이션 기록이 사라진다.
2. 이 보고서도 같은 이유로 worktree의 `docs\agent\tasks\TASK-015\implementation.md`에 있다. 메인의 `spec.md` 옆으로 옮겨 조율자에게 전달하는 것은 사용자 판단이다.
