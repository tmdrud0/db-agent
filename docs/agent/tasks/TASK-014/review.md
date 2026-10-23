# TASK-014 리뷰 보고서

작업 ID: TASK-014
명세 버전: 1
대상 코드 버전: 715393884553b71f2251258aae97b77a3fdf0d9b
상태: pass
요약: `fetch` 머리말, `consult` 때의 공식 조회 1회 실행·공식 비용 차감·기록, 원문의 서브에이전트 전용 전달, 오류 계약, 프롬프트 표시, 예시 하네스가 명세 v1의 AC-1~AC-10을 충족한다. 수정을 요구할 결함은 없다. AC-11(선택, 실제 평가)은 미실행으로 미검증이다.
근거: 누적 diff 전체와 `agent/tool_loop.py`(`_information`, `_consult_action`, `_session_loop`, `_observation`, `_bounded`, `_model_actions`), `agent/harness.py`, `agent/subagent.py`, `agent/tools.py`, `agent/session.py`, `agent/server.py`, `scripts/run-evaluation.ps1` `Assert-BaselineAgent`를 읽었다. 전체 스위트 실행, 기준 SHA 코드로 만든 프롬프트와의 문자 단위 비교, 표식 문자열·코인 정합성 독립 프로브를 실행했다.
미확정 사항: 없음.

## 검토 범위
기준 커밋: 438f50951b19fb453efc0645a4cefab3fe791a6b
검토 코드 커밋: 715393884553b71f2251258aae97b77a3fdf0d9b
검토 명령: git diff 438f50951b19fb453efc0645a4cefab3fe791a6b 715393884553b71f2251258aae97b77a3fdf0d9b --
검증한 작업 공간 코드 버전: HEAD = 715393884553b71f2251258aae97b77a3fdf0d9b. `git diff HEAD --stat -- agent tests harness` 출력 없음. `git status --short --untracked-files=all`은 `?? docs/agent/tasks/TASK-014/spec.md`만 표시(implementation.md는 .gitignore된 로컬 기록). 따라서 실행한 검증은 대상 SHA의 코드·테스트 검증이다.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 기준 SHA를 `git archive`로 저장소 밖에 풀어 만든 `harness/baseline`(2,384자)·`harness/sql_review`(3,370자) 프롬프트가 대상 SHA의 프롬프트와 문자 단위로 같다. 대상 SHA의 baseline은 `EXPECTED_BASELINE_PROMPT`와 같고, 테스트 상수 `EXPECTED_SQL_REVIEW_PROMPT`도 기준 SHA 생성 문자열과 같다. `fetch` 없는 consult는 항목 1개, cost 0, `fetch` 키 없음, 관찰 머리말 cost 0(`test_a_consult_without_fetch_keeps_the_task_013_record`, 기존 `ConsultTests` 통과). |
| AC-2 | 충족 | `_parse_sub_agent`가 기존 키 규칙(중복·알 수 없는 키 거부) 뒤 빈 값, 쉼표 다중 값, `SUB_AGENT_FETCH_TOOLS` 밖의 이름을 `HarnessError`로 거부한다. 공백 구분 다중 값·대소문자 변형도 허용 목록에 없어 거부된다. `test_invalid_fetch_values_fail`·`test_the_four_argument_free_lookups_load_as_fetch`가 명세의 각 경우를 확인한다. |
| AC-3 | 충족 | `_consult_action`이 `_information()`을 재사용해 조회 1회·공식 비용 차감·span·기록을 기존 정보 조회와 같은 코드로 처리한다. 테스트가 서비스 요청 1회, 18→17, 서브에이전트 호출 1회, `Fetched get_schema:`가 `Request:` 앞, 2회 consult 시 조회·차감 2회를 확인한다. 캐시 없음. |
| AC-4 | 충족 | 원문은 `fetched` 인자로 `build_user_message`에만 들어가고, 주 에이전트 `messages`에는 답 + 안내 줄만 추가된다. 테스트의 `RAW-SCHEMA-MARKER`와 독립 프로브(아래)로 주 호출 요청·`model_call.prompt`·다른 서브에이전트의 `Observations:` 맥락 어디에도 원문이 없음을 확인했다. |
| AC-5 | 충족 | 조회 항목(`requested_by`, `args: {}`, 원문 `result`, 비용, 예산 전후, phase, elapsed) → `consult` 항목(cost 0, `fetch`, 전후 동일) 순서. `subagent_call.fetch`, 가짜 Langfuse의 `get_schema` tool span·`consult` span·`subagent:<이름>` generation, 관찰 머리말 `cost 1`을 테스트가 확인한다. |
| AC-6 | 충족 | 조회 `GradingError`: `_information`이 `state["failure"]={"stage":"information",...}` 기록 후 전파(세션 실패, `consult` 항목 없음). 조회 중 시간 종료: `timed_out` 항목, `session_time_limit`. 조회 후 서브에이전트 `ModelError`: 세션 유지, 비용 유지, 실패 관찰에 "was not refunded" 문구. 예산 < fetch 비용: 서비스·모델·기록 없이 거부(내부 함수 직접 호출 테스트). |
| AC-7 | 충족 | 카탈로그 줄 `Fetches <tool> (cost <n>).`, `fetch`가 하나 이상일 때만 붙는 4줄 계약 문구, 공식 9개 줄 보존을 테스트가 고정한다. 0.5코인 도구 표시(`cost 0.5`)도 프로브로 확인했다. |
| AC-8 | 충족 | `harness/context_finder`가 로드되고 세 서브에이전트·각 `fetch`·`context: task, dialogue`·프롬프트 표시를 테스트가 확인한다. `sql_reviewer` 없음. |
| AC-9 | 충족(코드·테스트 근거) | 세 하네스의 `/health.tools`가 공식 9개 이름·가격 그대로(테스트, 프로브에서 context_finder `/health.tools` 9개 확인). `Assert-BaselineAgent`는 `service`·`mode`·`agent`·`model`과 9개 도구 이름·가격만 검사하므로(`scripts/run-evaluation.ps1:129-145`) 통과 조건이 바뀌지 않는다. 실제 스크립트 실행은 하지 않았다. 실행 명령은 implementation.md에 있다. |
| AC-10 | 충족 | `.venv\Scripts\python.exe -m unittest discover -s tests` → `Ran 247 tests`, `OK (skipped=3)`(리뷰어 재실행, 구현자 결과와 일치). |
| AC-11 | 미검증 | 선택 조건. 실제 dev30 평가는 구현자·리뷰어 모두 실행하지 않았다. |

### 코인·계약 점검 결과
- 차감은 `_information()` 한 곳에서만 일어나고 환불 경로가 없다. 조회 성공·`GradingError`·시간 종료 모두 차감 후 항목을 남긴다. 조회 없이 공식 서비스를 부르는 경로는 없다.
- `consult` 관찰 머리말 cost는 `action_budget - budget(state)`로 계산돼 실제 차감액과 항상 같다. 예산은 0.5 단위라 부동소수 오차가 없다.
- 행동 한도는 `model_call` event 수로 세므로 조회 항목이 추가돼도 24회 한도 계산이 바뀌지 않는다.
- 서브에이전트 모델 호출의 대기 한도를 `_remaining(deadline)`으로 다시 계산해, 조회에 쓴 시간만큼 세션 시한이 반영된다.
- 에이전트 코드 변경에 로컬 파일·GT·`private/`·`results/`·`vendor/` 읽기가 없다. 조회는 `spec.run(environment, ...)`의 공식 서비스 경로다.

## 수정 요청
없음

## 독립 검증
- 전체 스위트: `.venv\Scripts\python.exe -m unittest discover -s tests`(저장소 루트, HEAD 7153938) → `Ran 247 tests in 15.560s`, `OK (skipped=3)`. skip 3건은 Windows 파일 심볼릭 링크 권한 관련 기존 skip.
- AC-1 프롬프트 비교: `git archive 438f509 agent harness`를 저장소 밖 scratchpad(`review014/base`)에 풀고 `system_prompt(load_harness(...))`를 기준·대상 코드로 각각 생성(`python -I`, sys.path 명시). baseline 2,384자·sql_review 3,370자 모두 일치, `EXPECTED_BASELINE_PROMPT`·`EXPECTED_SQL_REVIEW_PROMPT`와도 일치.
- 독립 프로브(`review014/probe.py`, 기존 `ToolLoopTestCase`의 가짜 모델·서비스 사용):
  - `harness/context_finder`로 세 finder를 차례로 consult 후 제출. 서비스 경로 `/schema`, `/all_column_meanings`, `/knowledge`, `/submit` 각 1회. 궤적 `get_schema(1, 18→17, requested_by)`, `consult(0, 17→17, fetch)`, … , `submit_sql(3, 15→12)`. 비용 합계 = 초기 − 최종 예산. 도구별 표식 문자열이 모든 주 에이전트 요청과 `model_call.prompt`에 없고, 각 서브에이전트에는 자기 조회 표식만 있다. `/health.tools` 9개.
  - 0.5코인 `get_all_external_knowledge_names` fetch + 직접 `get_schema` + `observations` 맥락 서브에이전트 + 같은 fetch 재호출: 관찰 머리말이 `consult 0.5/17.5`, `get_schema 1/16.5`, `consult 0/16.5`, `consult 0.5/16`으로 예산 변화와 정확히 맞는다. `observations` 서브에이전트 입력에 표식 없음.
- 실행하지 않은 것: 실제 모델·DB·Langfuse·Docker, `run-evaluation.ps1`, AC-11 실제 평가.

## 이전 지적 처리
최초 검토. 해당 없음.

## 별도 제안·판단 요청
아래는 명세 밖 제안이며 판정에 영향을 주지 않는다.
- 예산 부족 거부 경로(`budget_before < fetch_cost`)는 `tool_trajectory`·`adk_events`에 아무 기록도 남기지 않는다. 명세 요구는 아니고 정상 루프에서 도달하지 않지만, 도달 시 결과 JSON만으로 원인을 찾기 어렵다. 필요하면 후속 작업에서 거부 사실을 event로 남기는 것을 검토할 수 있다.
- README·`docs/setup.md`에 `fetch` 머리말과 `harness/context_finder` 사용법을 추가하는 일은 implementation.md에 후속 사항으로 남아 있다(이번 범위 밖).
