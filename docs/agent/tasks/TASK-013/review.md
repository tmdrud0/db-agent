# TASK-013 리뷰 보고서

작업 ID: TASK-013
명세 버전: 1
대상 코드 버전: 8cd1c6bae0e387611b56222b4b7a2786df4b1944
상태: pass
요약: 서브에이전트 정의(`sub_agents/<이름>.md`) 로더, 하네스 의존 `consult` 행동, 서브에이전트 입력 조립·오류·기록, 예시 하네스가 명세 v1의 AC-1~AC-10을 충족한다. 수정을 요구할 결함은 찾지 못했다. AC-11(선택, 실제 평가)은 미실행이다.
근거: 기준~코드 SHA 누적 diff 전체, 주변 코드(`_session_loop`, `_model_call`, `_bounded`, `_ask_action`, `_read_text`, `resolve_query`, `scripts/run-evaluation.ps1`의 `Assert-BaselineAgent`, vendor `orchestrator/report.py`), 전체 테스트 스위트 재실행, 머리말 파서 경계 입력 프로브.
미확정 사항: 없음.

## 검토 범위
기준 커밋: 00cb7c3e7ea23357ecd2071a482df6a249577166
검토 코드 커밋: 8cd1c6bae0e387611b56222b4b7a2786df4b1944
검토 명령: git diff 00cb7c3e7ea23357ecd2071a482df6a249577166 8cd1c6bae0e387611b56222b4b7a2786df4b1944 --
검증한 작업 공간 코드 버전: 메인 체크아웃 `C:\Users\SSAFY\Desktop\db_agent`의 HEAD가 `8cd1c6b…`이고, `git status`에서 미추적 파일은 `docs/agent/tasks/` 아래 로컬 기록뿐이며 코드·테스트 변경은 없다. 리뷰 worktree(`claude/dev-review-13-2787d2`)도 같은 SHA지만 `vendor/`가 없어 vendor 의존 테스트 2개가 import 단계에서 실패하므로(`db_environment`, `user_simulator` 모듈 없음, 코드 결함 아님) 전체 스위트는 메인 체크아웃에서 실행했다.
입력 문서 위치: 명세·구현 보고서는 메인 체크아웃의 `docs/agent/tasks/TASK-013/`(미추적 로컬 파일)에서 읽었다. 이 보고서는 리뷰 세션 worktree의 같은 상대 경로에 작성했다(메인 체크아웃 쓰기 차단).

변경 파일: `agent/harness.py`, `agent/server.py`, `agent/subagent.py`(신규), `agent/tool_loop.py`, `agent/tools.py`, `agent/tracing.py`, `harness/sql_review/system.md`, `harness/sql_review/sub_agents/sql_reviewer.md`, `tests/test_harness.py`, `tests/test_tool_loop.py`, `tests/test_tracing.py`. `scripts/`, `vendor/`, 결과·GT 파일은 변경 없음.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 기준 하네스 불변 | 충족 | `system_prompt()`는 `harness.sub_agents`가 비면 기존과 같은 `actions`/`rules` 문자열로 계약을 조립한다. `EXPECTED_BASELINE_PROMPT`는 diff에서 변경되지 않았고 `BaselinePromptTests`가 통과한다. `_allowed_actions()`가 서브에이전트 없을 때 `ALLOWED_TOOLS` 그대로를 넘겨 `consult`는 `Unknown action` 거부(코인 0)이며 `test_consult_is_unknown_in_a_harness_without_sub_agents`가 확인한다. `/health.harness`에 `sub_agents: []`가 추가된 것은 명세 제약에서 허용하며, 저장소의 `scripts/`·`orchestrator/`·`benchmark/` 중 `/health.harness`를 읽는 곳은 없다. |
| AC-2 정의 파일 검증 | 충족 | `_sub_agent_files()`/`_parse_sub_agent()`가 이름 규칙, 머리말 없음·미닫힘, `description` 없음/빈 값, 본문 없음, 미지원 키·중복 키, 미허용·빈 `context`, 하위 디렉터리·비 `.md`, `sub_agents`가 파일인 경우를 `HarnessError`로 거부한다(`SubAgentTests`). 디렉터리·내부 항목 링크는 `_check_inside()`로 거부되고 junction 기반 `LinkTests` 2건이 Windows에서 skip 없이 통과했다. 해시는 `sub_agents/<이름>.md` 경로와 정규화 내용으로 계산되며 내용·이름 변경 테스트가 통과한다. |
| AC-3 카탈로그·계약 | 충족 | `consult_catalog()`가 `consult` 줄(인자, `Cost: 0`)과 이름·설명 목록을, `system_prompt()`가 명세의 5가지 취지(공식 도구 아님, 코인 없음, 행동 1회, DB·사용자 접근 불가·받은 맥락만, 점수 무관)를 담은 규칙 2줄을 추가한다. `test_sub_agents_add_the_consult_line_rules_and_agent_list`가 문구를 고정한다. `CONSULT_SPEC`은 `ALLOWED_TOOLS` 밖이라 `tool_metadata()`·`TOOL_COSTS`는 공식 9개 그대로다. |
| AC-4 호출과 입력 | 충족 | `_consult_action()`이 `[system=정의 본문, user=build_user_message(...)]`로 `model.chat` 한 번을 부른다. `build_user_message()`는 `context` 선언 순서의 섹션과 `Request:`만 넣고, `state`에서 `db_name`·`current_phase`·`follow_up_query`·`dialogue_history`와 `resolve_query` 결과만 읽는다. `ConsultTests` 4건이 system 본문, 항목별 포함·제외, phase 2 후속 질문, 이전 consult 답의 observations 포함, 숨은 `state` 키 미포함, 다음 주 호출 관찰 반영, 공식 경로 `["/execute", "/submit"]` 불변을 확인한다. `messages[2:-1]`은 `_session_loop`에서 `messages[-1]`이 방금 `_action_history()`로 바꾼 consult 행동이므로 올바르게 제외된다. |
| AC-5 비용·한도 | 충족 | `CONSULT_COST = 0.0`, `budget_before == budget_after`. 주 응답은 `_model_call()`의 `model_call` event로 1회 세고 서브 호출은 `subagent_call` event라 `_model_actions()`에 들어가지 않는다. `test_consult_counts_one_model_action_and_the_sub_agent_call_none`이 24회 consult 후 `action_limit_reached`, 모델 요청 48개를 확인한다. |
| AC-6 오류 거부 | 충족 | 없는 agent는 파싱 직후 `ActionError`로 기존 거부 경로(코인 0, 모델 호출 없음). 인자 누락·추가는 `validate_args`. `ModelError`는 `state["failure"]`를 설정하지 않고 실패 관찰·`model_error` 기록 후 세션 지속. 잘림 시 안내 문구 추가. `_bounded` 시간 초과는 `SessionExpired`로 기존 `session_time_limit` 종료. 각각 테스트가 있다. 주 모델 호출 실패 경로(`_model_call`)는 변경 없음. |
| AC-7 기록 | 충족 | `tool_trajectory` consult 항목(args, cost 0, 예산 전후, phase, elapsed, result), `adk_events`의 `subagent_call`(harness, agent, prompt, content, truncated, usage, elapsed, 실패 시 error), Langfuse 세션 trace 직하 `subagent:<이름>` generation과 실패 시 ERROR를 테스트가 확인한다. `model_call` event 생성 코드는 변경 없음. vendor `report.py`의 `_build_timeline()`은 `if/elif`로 알려진 type만 처리하고 나머지를 건너뛰는 것을 코드로 확인했다. |
| AC-8 예시 하네스 | 충족 | `harness/sql_review/system.md`는 기준 `system.md`와 동일한 3줄 뒤에 검토 지침 문단만 추가됐다(`diff`로 확인). `sql_reviewer.md`는 `context: task, dialogue, observations`, 출력 형식(번호 목록 / `No problems found.`)을 정하고 채점기·정답을 언급하지 않는다. `ExampleHarnessTests`가 로드와 프롬프트 내 `consult`·`sql_reviewer`를 확인한다. |
| AC-9 평가 실행 경로 | 충족(코드·테스트 근거) | `Assert-BaselineAgent`는 `service`·`mode`·`agent`·`model`과 `/health.tools`의 개수·이름·가격만 검사한다(`scripts/run-evaluation.ps1:128-145`). 두 하네스 모두 `/health.tools == tool_metadata()`(공식 9개)를 테스트가 확인한다. 실행 명령은 implementation.md에 있다. 실제 서비스 기동 후 스크립트 실행은 구현자·리뷰어 모두 하지 않았다. |
| AC-10 전체 테스트 | 충족 | 아래 독립 검증에서 `Ran 230 tests … OK (skipped=3)`로 재현했다. |
| AC-11 (선택) | 미검증 | 실행하지 않음. 명세상 선택 항목이다. |

## 수정 요청
없음.

## 독립 검증
| 명령·검증 | 환경 | 결과 |
| --- | --- | --- |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | 메인 체크아웃, HEAD `8cd1c6b…`, 코드·테스트 미커밋 변경 없음, Python 3.12 | `Ran 230 tests`, `OK (skipped=3)`. skip 3건은 모두 파일 심볼릭 링크 권한 없음(WinError 1314) — `test_a_linked_system_file_is_rejected`, `test_file_links_are_rejected`의 subTest 2건. 서브에이전트 링크 테스트 2건은 junction으로 실행·통과 |
| 같은 명령 | 리뷰 worktree(같은 SHA, `vendor/` 없음) | `Ran 183`, errors 2(`test_calibration`, `test_command_code_simulator` import 실패, vendor 부재). 코드 결함 아님, 판정 근거로 쓰지 않음 |
| `.venv\Scripts\python.exe -m unittest tests.test_harness -v` | 메인 체크아웃 | 서브에이전트 관련 테스트 전부 ok, skip은 위 3건뿐 |
| 머리말 파서 경계 프로브(스크래치패드 `probe.py`, 임시 디렉터리 하네스) | 메인 체크아웃 코드 import | CRLF → 정상 로드, `description: a: b` → 설명 `a: b`, `context: task, task` → `('task',)`, `context:` 빈 값 → `HarnessError`, `Description:` 대문자 키 → `HarnessError`, 첫 줄 앞 빈 줄 → `HarnessError`, UTF-8 BOM으로 시작 → `HarnessError`(머리말 없음으로 보고), 빈 `sub_agents/` → 서브에이전트 없음·해시 안정 |
| `git diff --check 00cb7c3 8cd1c6b` | — | 출력 없음 |
| `/health.harness` 소비처 검색(`scripts/`, `orchestrator/`, `benchmark/`) | — | 없음 |
| vendor `orchestrator/report.py` `_build_timeline()` 읽기 | — | `user_message`/`adk_event`만 처리, 그 외 type은 무시 |

실행하지 않은 것: 실제 모델·Langfuse·DB 서비스 호출, `scripts/run-evaluation.ps1` 실제 실행, AC-11 평가. implementation.md의 `observations` 크기 측정(최대 약 7.9만 자)은 `results/` 로컬 파일 기반이라 재현하지 않았다.

## 이전 지적 처리
해당 없음(최초 검토).

## 별도 제안·판단 요청
통과를 막지 않는 명세 밖 제안이다.

1. **observations 크기와 비용:** 구현자 측정으로 늦은 단계 consult 입력이 약 1.7만~7.9만 자다. `sql_review` 하네스는 제출 전마다 consult를 유도하므로 문제당 주 에이전트 입력과 비슷한 규모의 서브 호출 토큰이 추가될 수 있다. AC-11 비교 시 주·서브 토큰을 분리 집계하고, 필요하면 후속 작업에서 상한·요약을 검토할 것을 조율자에게 제안한다.
2. **거부된 원문 응답의 전달:** observations에는 거부된 주 에이전트 응답 원문(모델이 꾸며 낸 관찰이 섞일 수 있는 텍스트)도 그대로 들어간다. 명세 문구("대화 기록의 행동 JSON과 관찰 메시지를 옮긴다")와 일치하므로 결함은 아니다. 다만 서브에이전트가 이를 실제 관찰로 오인할 수 있어, 실제 평가에서 문제가 보이면 거부 응답을 표준 표기로 바꾸는 것을 후속 후보로 둔다.
3. **BOM 파일 오류 문구:** UTF-8 BOM으로 저장된 정의 파일은 안전하게 거부되지만 "must start with a '---' header block"으로만 보고된다. 다른 하네스 파일과 같은 정책이므로 변경 요구는 아니며, 문서화 시 BOM 없는 UTF-8을 안내하면 된다.
4. **문서 후속:** README·`docs/setup.md`의 `sub_agents` 형식·`consult` 안내는 implementation.md의 후속 사항과 같다(이번 범위 밖).
