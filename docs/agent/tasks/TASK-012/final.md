# TASK-012 최종 보고서

작업 ID: TASK-012
명세 버전: 1
기준 코드 버전: 15977f804d99b8bd914c94c5aa863d8d0f0c8fe1
대상 코드 버전: feafe1852dad0cfdf53dc688efc2592f99e08d6f
상태: ready_for_user
요약: 루프에서 중복 제출 차단과 정보 조회 캐시를 걷어 내고, 계약과 평가 보호 장치만 남긴 최소 기준 루프로 만들었다. 행동 지침은 하네스 폴더(`harness/baseline/`, `AGENT_HARNESS_DIR`)에서 읽고, 하네스 이름과 SHA-256을 실행 기록에 남긴다. 리뷰 R-1(링크를 따라가 하네스 폴더 밖 파일을 읽음)은 후속 커밋으로 해결했다. 필수 완료 조건 AC-1~AC-9를 충족했고, 같은 명세 버전과 코드 SHA에 대한 리뷰 판정은 `pass`다. 선택 항목 AC-10(실제 평가)은 실행하지 않았다.
근거: 아래에 코드 위치, 테스트, 조율자와 리뷰어의 재확인 결과, 지적 처리를 직접 기록했다. 대상 SHA에서 전체 테스트 202개가 통과했다(3개 skip).
미확정 사항: 없음.

## 변경 내용과 설계 결정
작업 코드 커밋:
- b901a55453dd3cef06856fa125a0e31032b3911f `[TASK-012] feat: load behavioral guidance from a harness folder`
- feafe1852dad0cfdf53dc688efc2592f99e08d6f `[TASK-012] fix: reject links and junctions in the harness folder` (리뷰 R-1)

변경 파일(누적 9개, +783/−391):
- `agent/harness.py`(신규)
- `agent/server.py`, `agent/tool_loop.py`, `agent/tools.py`, `agent/tracing.py`
- `harness/baseline/system.md`(신규)
- `tests/test_harness.py`(신규), `tests/test_tool_loop.py`, `tests/test_tracing.py`

### 1. 루프에서 걷어 낸 것과 남긴 것
**걷어 낸 것**
- **중복 제출 차단:** 같은 phase의 같은 SQL도 항상 공식 `/submit`으로 보내고 3코인을 차감한다.
- **정보 조회 캐시:** 같은 도구·인자도 매번 공식 서비스를 호출하고 같은 비용을 차감한다.
- **두 규칙의 설명과 전용 코드:** 시스템 프롬프트의 설명 두 항목과 `submission_key()`, `_starts_string_constant()`, tracing의 `cached`/`blocked` 메타데이터를 지웠다.
- `state["submit_attempts"]`는 기록으로만 남는다.

**남긴 것(바꾸지 않음)**
- 공식 도구 9개, 인자, 비용
- 예산 차감, 코인 부족 시 종료
- phase 전환, 보상 기록
- 첫 행동만 실행(TASK-010)과 표준 행동 JSON 대화 기록(TASK-011)
- `execute_sql` 여러 문장 거부(공식 SELECT 전용 검사의 우회 방지)
- 행동 한도 24회, 세션 1500초

### 2. 하네스 폴더(`agent/harness.py`)
- **위치:** 기본은 `harness/baseline/`이다. `AGENT_HARNESS_DIR`로 바꿀 수 있고, 상대 경로는 저장소 루트 기준이다. 하네스 이름은 폴더 이름이다.
- **인식 파일:** `system.md`(필수), `memory.md`(선택), `tools/<공식 도구>.md`(선택)만 인식한다.
- **시작 실패 조건:** 다음은 모두 `HarnessError`로 서버 시작을 실패시킨다.
  - 그 밖의 항목(`README.md` 포함), 공식 도구가 아닌 이름, 잘못된 UTF-8
  - 링크·junction인 항목, 실제 경로가 루트 밖인 항목(R-1)
  - 하네스 루트 자체는 링크여도 허용한다.
- **로딩 시점:** 서버 시작 시 한 번 읽는다. 실행 중 파일을 바꿔도 반영되지 않는다.
- **해시:** 상대 경로를 정렬한 뒤, 파일마다 `경로\0내용\0`을 SHA-256에 넣는다. 내용의 줄바꿈은 LF로 정규화한다.
  - 기준 하네스의 해시는 `22e2b82d97a4c33a526e147449dc4302d7021e463d41af9b39668482173b3cba`다.

### 3. 시스템 프롬프트 조립(`system_prompt()`)
- **순서:** `system.md` → 빈 줄 → 코드가 만드는 계약(응답 형식, 도구 카탈로그, 규칙) → `memory.md`가 있으면 `Harness memory:` 섹션.
- **도구 안내:** 해당 카탈로그 줄 아래에 `  Usage note:`와 함께 붙인다.
- **계약 보호:** 계약 문구는 하네스 내용과 관계없이 항상 코드가 만든 그대로 들어간다.
- **기준 하네스:** 기준 하네스의 `system.md`는 기존 첫 문단 세 줄이다. 조립된 프롬프트는 task011의 `SYSTEM_PROMPT`에서 캐시 항목(4줄)과 중복 제출 항목(2줄)만 뺀 텍스트와 문자 단위로 같다.
- **설계 선택:** "decide yourself whether and when to ask"는 규칙 목록 안에 있어 코드에 남겼다. 하네스로 옮기면 문자 단위 일치를 지킬 수 없기 때문이다.

### 4. 실행 기록
- `/health`, 세션 상태, 각 `model_call` event, Langfuse 세션 메타데이터에 `harness` 이름과 해시를 남긴다.
- 벤더 러너(`orchestrator/ainteract.py:127-155`)는 세션 상태 중 정해진 필드만 결과 JSON에 복사한다. 그래서 결과 파일에서는 문제 항목의 `adk_events[].harness`로 확인한다.
- `scripts/run-evaluation.ps1`은 바꾸지 않았다. `/health`의 기존 필드만 읽기 때문이다.

## 설정과 실행 방법
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
$env:AGENT_HARNESS_DIR = 'harness/exp1'   # 생략하면 harness/baseline
.\scripts\run-evaluation.ps1 -Limit 1 -Concurrency 1 -ResultName 'tools_harness_exp1'
```
- `AGENT_HARNESS_DIR`는 에이전트 프로세스가 시작되기 전에 설정해야 한다.
- 포트 6000에서 이미 실행 중인 서버를 재사용하면, 그 서버가 시작할 때 읽은 하네스가 그대로 쓰인다.
- 실제로 쓰인 값은 `/health.harness`로 확인한다.
- 모델 설정과 평가 조건은 바뀌지 않았다.

## 완료 조건별 결과
| 조건 | 결과 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `test_same_sql_is_submitted_and_charged_every_time`: `/submit` 3회, 잔액 15→12→9, `blocked`/`duplicate_sql` 없음. `test_the_same_sql_is_submitted_again_in_phase_two`: phase 1·2 각각 채점·3코인. 기존 중복 차단 테스트 2개를 대체 |
| AC-2 | 충족 | `test_repeated_information_lookup_is_sent_and_charged_again`: `/schema` 2회, `cached` 키·캐시 안내 없음, 비용 2회 |
| AC-3 | 충족 | 여러 문장 거부, 한도·시간·예산 종료, 첫 행동만 실행, 표준 행동 JSON, phase 전환·보상 테스트 통과. 행동 한도 테스트의 호출 기대값은 캐시 제거에 따라 `["/schema"] * 24`로 변경 |
| AC-4 | 충족 | `test_the_baseline_builds_the_frozen_contract`. 리뷰어가 기준 커밋의 `SYSTEM_PROMPT`를 AST로 재구성해 두 항목을 뺀 텍스트와 비교: `exact match: True`(38→32줄, 2784→2384자) |
| AC-5 | 충족 | `AssemblyTests`, `ToolCatalogTests`: `system.md`·`memory.md`·`tools/*.md` 반영, 계약 문구 유지 |
| AC-6 | 충족 | `ValidationTests`(폴더·`system.md` 없음, 미인식 항목, 비공식 도구 이름, 잘못된 UTF-8), `LinkTests`(링크·junction 거부, 루트 별칭 허용) |
| AC-7 | 충족 | `/health`·세션·`model_call` event 테스트, `HashTests`(내용·경로 변경 시 다른 해시, 줄바꿈 차이는 같은 해시), tracing 가짜 클라이언트 테스트 |
| AC-8 | 충족(정적 확인) | 스크립트가 읽는 `/health` 필드(`status`, `service`, `mode`, `agent`, `model`, `tools`)가 유지됨을 테스트로 확인. 실제 Docker 평가는 미실행 |
| AC-9 | 충족 | 대상 SHA에서 202 tests OK(skipped=3). 구현자와 리뷰어가 각각 실행 |
| AC-10(선택) | 미검증 | 실제 dev30 평가 미실행 |

## 검증 결과
| 명령·검증 | 코드 버전·실행 조건 | 결과 | 확인한 동작 |
| --- | --- | --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` (구현자) | feafe1852dad0cfdf53dc688efc2592f99e08d6f, 추적 변경 없음 | exit 0, 202 tests OK (skipped=3) | AC-1~AC-9 |
| `.venv/Scripts/python.exe -m unittest discover -s tests` (리뷰어 재검토) | 같은 SHA, `git status`에 미추적 작업 문서만 있음 | 202 tests OK (skipped=3) | 독립 재실행 |
| 기준 `SYSTEM_PROMPT` AST 비교(리뷰어 스크립트, 미커밋) | 기준 15977f8 대비. 프롬프트 코드는 b901a55 이후 바뀌지 않음 | exact match: True | AC-4 |
| 하네스 링크 probe 7개(리뷰어 스크립트, 미커밋) | feafe18, Windows junction(`_winapi.CreateJunction`), `os.link` | 폴더 밖 junction 4가지 모두 거부. 루트 별칭·기준 하네스는 로드됨. 하드링크는 로드됨(아래 제한) | R-1·AC-6 |
| 조율자 SHA·버전 대조 | HEAD = feafe18 | spec v1, implementation·review 모두 v1·feafe18 | 문서 일치 |

검증 코드와 최종 SHA 일치 근거: HEAD가 대상 SHA이고 추적 코드 변경이 없다. 미추적 파일은 `docs/agent/tasks/TASK-012/`의 문서뿐이며 테스트에 쓰이지 않는다. 검증 스크립트와 임시 하네스는 세션 scratchpad에 있다.
검증 범위: 모두 가짜 모델·환경을 쓰는 계약 테스트와 로컬 probe다. 실제 모델, DB 채점, Langfuse 전송, Docker 평가는 실행하지 않았다.

## 리뷰 처리 결과
검토 명세 버전: 1
검토한 코드 SHA: feafe1852dad0cfdf53dc688efc2592f99e08d6f
리뷰 판정: pass
판정 근거·독립 검증:
- 리뷰어가 R-1 수정분(`b901a55..feafe18`)과 누적 diff를 검토했다.
- 이전 재현 사례와 변형 사례를 직접 만든 probe로 확인했다.
  - `tools/` junction, 대상이 사라진 junction, `tools/` 안의 junction, 루트 별칭 아래의 junction은 모두 거부됐다.
  - 허용해야 하는 루트 별칭과 기준 하네스는 정상 로드됐다.
- 전체 스위트를 다시 실행했다.
- 판정 순서도 확인했다. `_check_inside()`가 링크를 따라가기 전에 `is_symlink()`/`is_junction()`으로 먼저 거부하고, `resolve()` 결과가 루트 아래에 있는지 추가로 검사한다.

지적 처리:
- R-1(링크를 따라가 폴더 밖 파일을 읽음): b901a55에서 `changes_requested` → feafe18에서 해결. 수정 1회, 의견 충돌 없음.
- 최초 리뷰의 확인 항목 판단(수정 불필요):
  - A. 계약 문장이 하네스로 옮겨진 점: 명세가 열거한 계약(응답 형식, 도구·인자·비용, 한도, 보상·phase)은 모두 코드에 남아 있어 위반이 아니다. "한 턴 한 행동" 문장을 계약 쪽으로 옮기는 것은 별도 제안으로 남겼다.
  - C. AC-4 줄 수 표기: 명세에는 캐시 항목이 "세 줄"로 적혀 있지만 실제로는 네 줄이다. 대상을 시작·끝 문구로 특정했으므로 구현에는 영향이 없는 표기 오류다. 명세 버전은 올리지 않았다.

## 남은 제한
- **AC-10(실제 평가) 미실행.** 기준 루프는 동작이 달라졌다. 중복 제출과 반복 조회가 이제 공식 서비스로 가고 비용이 든다. 따라서 task011의 평균 보상 0.36과 직접 비교할 수 없고, 새 기준 측정이 필요하다. 실행 간 편차가 크므로 2회 이상 측정을 권한다.
- **하드링크는 막지 않는다.** 하드링크로 넣은 파일은 폴더 안의 일반 파일이라, 복사해 넣은 것과 효과가 같다. 로더로는 막을 수 없다. 정답 자료 유입은 하네스 내용 검토와 해시 기록으로 관리해야 한다. 하네스를 LLM이 자동 편집하는 후속 작업에서 다룰 사항이다.
- **실제 파일 심볼릭 링크 테스트 3건은 skip됐다.** 이 Windows 환경에 링크 생성 권한이 없기 때문이다(WinError 1314). 같은 분기는 대체 테스트와 junction 테스트로 확인했다.
- **리뷰 독립성이 제한된다.** R-1 수정과 재검토를 같은 세션이 수행했다. 리뷰어가 구현자 테스트와 다른 사례를 따로 만들어 보완했다.
- **결과 JSON에서 하네스를 확인하는 위치가 다르다.** 문제 항목 최상위가 아니라 `adk_events[].harness`다.
- **캐시 제거로 반복 조회가 늘어날 수 있다.** 실제 평가의 실행 시간과 서비스 호출이 늘 수 있지만 측정하지 않았다.
- **후속 문서 갱신이 남아 있다.** README와 `docs/setup.md`에 하네스 형식과 `AGENT_HARNESS_DIR` 사용법을 넣는 것이 좋다. 이번 범위에서는 제외했다.

필수 완료 조건과 유지할 계약에는 영향이 없다.

## 필요한 보완·판단
없음. 선택 사항은 다음과 같다.
- 기준 하네스로 dev30 calibrated 평가를 실행한다(AC-10).
- 리뷰 독립성을 보강하려면 별도 세션에서 리뷰한다.

## 직접 확인 방법
1. `.\.venv\Scripts\python.exe -m unittest discover -s tests` → `Ran 202 tests`, `OK (skipped=3)`.
2. 평가 1문제를 실행한 뒤 다음을 확인한다.
   - `/health`와 결과 JSON의 `adk_events[0].harness`가 `{"name": "baseline", "sha256": "22e2b82d…"}`이다.
   - `adk_events[0].prompt[0].content`에 캐시 안내와 중복 제출 안내가 없다.
3. 하네스 폴더에 `README.md`를 두거나 `tools/`를 링크로 바꾸면 서버 시작이 `HarnessError`로 실패한다.

## 사용자 결정
결정: 승인
대상: 명세 버전 1, 최종 코드 feafe1852dad0cfdf53dc688efc2592f99e08d6f
확인 근거: 2026-10-21 조율자 채팅에서 사용자가 ready_for_user 최종 보고서를 확인한 뒤 "승인한다. TASK-012 spec.md와 final.md를 문서 커밋해줘."라고 승인했다. AC-10(선택)은 실행하지 않은 상태로 승인했다.
보존 문서: spec.md, final.md
