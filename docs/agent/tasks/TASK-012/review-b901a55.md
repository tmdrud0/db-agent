# TASK-012 리뷰 보고서

작업 ID: TASK-012
명세 버전: 1
대상 코드 버전: b901a55453dd3cef06856fa125a0e31032b3911f
상태: pass
요약: 중복 제출 차단·정보 조회 캐시 제거, 하네스 로딩·검증·해시, 프롬프트 조립, `/health`·세션 상태·Langfuse 기록이 명세대로 구현됐다. 기준 하네스 프롬프트는 기존 `SYSTEM_PROMPT`에서 두 항목만 뺀 텍스트와 문자 단위로 같음을 독립 확인했다. 수정 요청 없음. `ask_user` 문장의 위치와 결과 JSON의 하네스 기록 위치는 조율자 확인 사항으로 남긴다.
근거: `git diff 15977f8 b901a55` 전체, `agent/session.py`·`scripts/run-evaluation.ps1`·벤더 러너 `ainteract.py` 주변 코드, 전체 테스트 196개 통과, 기준 커밋의 `SYSTEM_PROMPT` AST 재구성 비교, 임시 하네스 프로브.
미확정 사항: 없음(판정을 막는 사항 없음). AC-10(선택)은 미실행.

## 검토 범위
기준 커밋: 15977f804d99b8bd914c94c5aa863d8d0f0c8fe1
검토 코드 커밋: b901a55453dd3cef06856fa125a0e31032b3911f
검토 명령: git diff 15977f804d99b8bd914c94c5aa863d8d0f0c8fe1 b901a55453dd3cef06856fa125a0e31032b3911f --
검증한 작업 공간 코드 버전: 메인 체크아웃 `C:\Users\SSAFY\Desktop\db_agent`, HEAD = b901a55453dd3cef06856fa125a0e31032b3911f, `git status --untracked-files=no` 비어 있음(추적 변경 없음). 미추적은 `docs/agent/tasks/TASK-012/`뿐이며 테스트에 쓰이지 않는다. 리뷰 worktree(`claude/dev-review-task12-d84e28`, 같은 SHA)에는 gitignore된 `vendor/`가 없어 `test_calibration`·`test_command_code_simulator` import가 실패하므로(149개 실행, errors=2, 코드 결함 아님) 전체 스위트는 메인 체크아웃에서 실행했다.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `_is_duplicate_submission`과 차단 분기 삭제(`agent/tool_loop.py` `_submit_action`). `test_same_sql_is_submitted_and_charged_every_time`이 `/submit` 페이로드 `[first, first, second]`, 비용 3×3, 잔액 15→12→9, `blocked`/`duplicate_sql` 없음을 단언. `test_the_same_sql_is_submitted_again_in_phase_two`가 phase 1·2 재제출 채점·차감 확인. 기존 차단 테스트 2개는 대체됐고 이유가 implementation.md에 있음. |
| AC-2 | 충족 | `_cached_result`와 캐시 분기 삭제. `test_repeated_information_lookup_is_sent_and_charged_again`이 경로 `["/schema","/schema","/submit"]`, 비용 1·1, `cached` 없음, 두 번째 관찰에 캐시 문구 없음 확인. tracing의 `cached`·`blocked` 메타데이터도 제거. `agent/`·`scripts/`에 `cache_hit`·`duplicate_sql`을 만드는 코드 없음(grep). |
| AC-3 | 충족 | `statement_count`·`validate_args` 유지(`submission_key`·`_starts_string_constant`만 삭제, 남은 헬퍼는 `statement_count`가 사용). 행동 한도·시간·코인 종료, 첫 행동만 실행, 표준 행동 JSON, phase 전환·보상 테스트 통과. `test_action_limit_stops_the_session`의 `["/schema"] * 24` 변경은 캐시 제거의 직접 결과로 타당. |
| AC-4 | 충족 | `tests/test_harness.py`의 고정 기대 텍스트 테스트 통과. 독립 확인: 기준 커밋의 `SYSTEM_PROMPT` 식을 AST로 평가하고 두 항목(캐시 4줄, 중복 2줄)을 제거한 텍스트와 `system_prompt(load_harness("harness/baseline"))`가 일치(`True`, 2784→2384자). 명세의 "캐시 항목 세 줄"은 실제 4줄이며, 항목 전체 제거가 명세 의도에 맞다. |
| AC-5 | 충족 | `AssemblyTests` 통과. 프로브: 계약 덮어쓰기 시도 문구를 둔 `system.md`가 선두에 붙고 응답 형식·카탈로그는 그대로, 공백뿐인 `memory.md`는 섹션 없음, `tools/execute_sql.md`는 해당 카탈로그 줄 아래 `Usage note:`로 들여 붙음. |
| AC-6 | 충족 | `ValidationTests` 통과(폴더 없음, `system.md` 없음, `README.md`·`notes.txt`, 공식 외 도구, 하위 디렉터리, `tools`가 파일, 잘못된 UTF-8). `README.md` 불허를 테스트로 고정. 프로브: `.gitkeep`도 `Unrecognized harness entry`로 실패(명세의 "조용히 무시하지 않음"에 부합). |
| AC-7 | 충족 | `/health.harness`, 세션 상태 `harness`, 각 `model_call` event의 `harness` 테스트 통과. `HashTests`로 같은 내용/내용 변경/경로 변경/줄바꿈 정규화 확인. Langfuse 메타데이터는 가짜 클라이언트 테스트로 확인. 기준 하네스 해시 `22e2b82d…b3cba` 재현. |
| AC-8 | 충족(정적) | `run-evaluation.ps1:119-136`은 `status`와 `service`·`mode`·`agent`·`model`·`tools`만 읽어 필드 추가에 영향 없음을 직접 확인. 하네스 지정 명령 예시가 implementation.md에 있음. 실제 Docker 평가는 미실행. |
| AC-9 | 충족 | `.venv\Scripts\python.exe -m unittest discover -s tests` → 196 tests OK(메인 체크아웃, b901a55). |
| AC-10 | 미검증(선택) | 실행하지 않음. |

## 수정 요청
없음

## 독립 검증
- `.venv/Scripts/python.exe -m unittest discover -s tests` (메인 체크아웃, HEAD b901a55, 추적 변경 없음): `Ran 196 tests ... OK`.
- 같은 명령을 리뷰 worktree에서 실행: 149개 실행, errors=2(`test_calibration`, `test_command_code_simulator`가 미설치 `vendor/` 모듈 import 실패). 환경 차이로 판정에서 제외.
- 비커밋 스크립트(리뷰 세션 scratchpad의 `ac4.py`): 기준 커밋 `SYSTEM_PROMPT` 재구성 후 두 항목 제거 → 기준 하네스 조립 결과와 `match True 2784 2384`; `load_harness` → `baseline 22e2b82d97a4c33a526e147449dc4302d7021e463d41af9b39668482173b3cba`; 임시 하네스 프로브(위 AC-5·AC-6).
- 벤더 러너 `vendor/BIRD-Interact/BIRD-Interact-ADK/orchestrator/ainteract.py:127-145` 확인: 문제별 결과에 `adk_events`는 복사되고 `state["harness"]`는 복사되지 않음. implementation.md 조사 결과와 일치.
- 실행하지 않은 것: 실제 모델·DB·Langfuse 호출, Docker 기반 `run-evaluation.ps1`, dev30 평가.

## 이전 지적 처리
해당 없음(최초 검토).

## 별도 제안·판단 요청
1. **(조율자 판단) `ask_user` 항목의 행동 지침 문장 위치.** 명세 "조사한 기준 코드"는 "decide yourself whether and when to ask"를 행동 지침으로 분류했지만, 구현은 AC-4 문자 단위 일치를 위해 이 문장을 코드 계약에 남겼다. 규칙 목록 중간에 하네스 삽입 지점이 없으면 두 요구가 동시에 성립하지 않으므로 구현 선택은 타당하고 implementation.md에 공개돼 있다. 다만 이 문장은 하네스로 바꿔 실험할 수 없다. 후속 작업에서 옮길지 결정이 필요하다(이번 판정은 막지 않음).
2. **(조율자 확인) 결과 JSON의 하네스 위치.** 명세는 "결과 JSON의 각 문제 상태"에 하네스 기록을 요구했는데, 벤더 러너가 화이트리스트로만 복사해 문제 항목 최상위에는 남지 않는다. 구현은 `adk_events[].harness`(모델 호출 event마다)에 남겼다. 명세가 "남지 않으면 남는 위치를 찾아 기록하고 알린다"고 한 경로를 따랐으므로 충족으로 봤다. 사용자 확인 절차 2번은 `adk_events[0].harness`를 보도록 안내해야 한다.
3. **(제안) 실행 중인 에이전트 재사용.** `run-evaluation.ps1:206`은 포트 6000의 기존 에이전트를 재사용하며 하네스를 비교하지 않는다. `AGENT_HARNESS_DIR`를 바꾸고 재실행해도 이전 하네스로 평가될 수 있다(implementation.md에 주의로 적혀 있음). 하네스 비교 실험을 반복할 계획이면 스크립트가 기대 하네스를 `/health.harness`와 대조하는 후속 작업을 고려할 수 있다.
4. **(제안) 숨김 파일.** `.gitkeep`·`.DS_Store` 같은 파일도 시작 실패를 일으킨다. 명세상 올바른 동작이지만 빈 `tools/` 폴더를 git에 남기려 할 때 걸릴 수 있으므로 후속 문서 갱신 때 안내하면 좋다.
5. **(참고) 쓰이지 않는 기록 필드.** `submit_attempts`는 이제 분기에 쓰이지 않는 기록용 필드다. 명세가 허용한 범위다.
