# TASK-012 리뷰 보고서

작업 ID: TASK-012
명세 버전: 1
대상 코드 버전: feafe1852dad0cfdf53dc688efc2592f99e08d6f
상태: pass
요약: 재검토 결과 R-1이 해결됐다. 하네스 로더는 링크·junction인 항목과 하네스 루트 밖으로 해석되는 항목을 거부한다. 리뷰어가 독립적으로 만든 우회 사례 5개가 모두 거부되거나 의도대로 처리됐다. 누적 diff의 AC-1~AC-9 판정도 새 SHA에서 다시 확인했다(202 tests OK, skipped=3). 새 수정 요청은 없다.
근거:
- R-1 수정 diff(`b901a55..feafe18`의 `agent/harness.py`, `tests/test_harness.py`)를 읽었다.
- 누적 diff(`15977f8..feafe18`)를 다시 확인했다.
- 리뷰어 probe 스크립트로 7가지 사례를 실행했다.
- 대상 SHA에서 전체 스위트를 실행했다.
미확정 사항: 없음.

## 검토 범위
기준 커밋: 15977f804d99b8bd914c94c5aa863d8d0f0c8fe1
검토 코드 커밋: feafe1852dad0cfdf53dc688efc2592f99e08d6f
검토 명령: git diff 15977f804d99b8bd914c94c5aa863d8d0f0c8fe1 feafe1852dad0cfdf53dc688efc2592f99e08d6f --
R-1 수정분: git diff b901a55453dd3cef06856fa125a0e31032b3911f feafe1852dad0cfdf53dc688efc2592f99e08d6f --
검증한 작업 공간 코드 버전: HEAD = feafe1852dad0cfdf53dc688efc2592f99e08d6f. `git status --short`에는 미추적 `docs/agent/tasks/TASK-012/`만 있다. 검증 스크립트와 임시 하네스는 세션 scratchpad에 두었고, 저장소에는 아무것도 만들지 않았다.
독립성 제한: 이 재검토는 R-1 수정을 구현한 것과 같은 세션이 수행했다. 독립성을 보완하려고 구현자 테스트와 다른 우회 사례(아래 4·5·6번)를 따로 만들어 확인했다. 사람의 확인이나 별도 세션의 리뷰가 필요하면 조율자가 판단한다.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | `b901a55` 이후 `agent/tool_loop.py`·`agent/tools.py`에 변경이 없다. 이전 판정 근거(중복 분기 제거, `test_same_sql_is_submitted_and_charged_every_time`, `test_the_same_sql_is_submitted_again_in_phase_two`)가 그대로 유효하고 새 SHA에서 통과한다. |
| AC-2 | 충족 | 위와 같다. `test_repeated_information_lookup_is_sent_and_charged_again`이 통과한다. |
| AC-3 | 충족 | 계약 관련 코드는 R-1 수정에서 바뀌지 않았다. 관련 테스트가 통과한다. |
| AC-4 | 충족 | `harness/baseline/system.md`와 `system_prompt()`는 바뀌지 않았다. `test_the_baseline_builds_the_frozen_contract`가 통과하고, probe 7번에서 기준 하네스가 정상 로드된다. 이전 리뷰의 AST 비교(`exact match: True`) 대상 코드가 같다. |
| AC-5 | 충족 | 조립 코드는 바뀌지 않았다. `AssemblyTests`가 통과한다. |
| AC-6 | 충족 | 기존 `ValidationTests`에 더해 링크 거부를 `LinkTests`로 확인한다(아래 R-1). |
| AC-7 | 충족 | 해시 계산은 바뀌지 않았다. `HashTests`가 통과한다. `test_the_harness_root_itself_may_be_a_link`는 루트 별칭과 실제 폴더의 해시가 같음을 확인한다. |
| AC-8 | 충족(정적) | 변경이 없어 이전 판정과 같다. |
| AC-9 | 충족 | `.venv/Scripts/python.exe -m unittest discover -s tests` → 202 tests OK(skipped=3), 13.6s. |
| AC-10(선택) | 미검증 | 실제 평가는 실행하지 않았다. |
| 제약: 지정 폴더만 읽기 | 충족 | R-1 해결. 하드링크는 아래 "별도 제안" 참고. |

## 수정 요청
없음.

## 독립 검증
| 명령·검증 | 조건 | 결과 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m unittest discover -s tests` | HEAD feafe18, 추적 변경 없음 | 202 tests OK (skipped=3) |
| probe 1: `tools/` → 폴더 밖 디렉터리 junction(이전 R-1 재현과 같은 사례) | `_winapi.CreateJunction`, Python 3.12.10 | rejected: `Harness entry tools is a link` |
| probe 2: 대상이 사라진 `tools/` junction | 같음 | rejected (`tools.exists()`가 거짓이어도 `is_junction()`으로 잡힘) |
| probe 3: `tools/ask_user.md` 이름의 디렉터리 junction | 같음 | rejected: `tools/ask_user.md is a link` |
| probe 4: 하네스 루트 자체가 junction 별칭이고 내용은 실제 파일 | 같음 | 로드됨. 노트는 폴더 안의 `LOCAL`, 폴더 밖 내용 없음. 명세상 허용되는 경우 |
| probe 5: 루트 별칭 + 그 안의 `tools/`가 폴더 밖 junction | 같음 | rejected: `tools is a link` |
| probe 6: `tools/ask_user.md`가 폴더 밖 파일의 하드링크 | `os.link` | 로드됨(폴더 밖 파일과 같은 내용이 프롬프트에 들어감). 아래 "별도 제안" 참고 |
| probe 7: `harness/baseline` | 같음 | 로드됨 |
| 파일 심볼릭 링크 실제 생성 | 이 환경 | 실행 못 함(WinError 1314). 구현자 테스트 3건도 같은 이유로 skip. 파일 링크 분기는 `Path.is_symlink` 대체 테스트(`test_a_file_reported_as_a_link_is_rejected_without_link_privileges`)로만 확인됨 |

`_check_inside()`의 코드도 확인했다.
- 판정 순서: `is_symlink()`/`is_junction()` 판정이 `is_file()`/`is_dir()`보다 먼저다. 그래서 링크를 따라가기 전에 거부된다.
- 경로 검사: `resolve().is_relative_to(real_root)`가 추가로 루트 밖 경로를 막는다. `real_root`는 `root.resolve()`라서 루트 별칭을 써도 비교가 맞다.
- 검사 범위: `system.md`, `memory.md`, `tools/`, `tools/*`의 모든 항목을 읽기 전에 검사한다. 미인식 항목은 원래대로 이름만 보고 거부한다.

## 이전 지적 처리
### R-1: 하네스 로더가 링크를 따라가 폴더 밖 파일을 읽음 — 해결
- 수정 횟수: 1회. 수정 커밋은 feafe1852dad0cfdf53dc688efc2592f99e08d6f다.
- 지적 내용(b901a55): `is_file()`/`is_dir()`이 링크를 따라가므로, `tools/` junction으로 폴더 밖 내용이 프롬프트에 들어갔다.
- 수정: `agent/harness.py`에 `_check_inside()`를 추가했다. 링크·junction이거나 해석 경로가 루트 밖인 항목은 `HarnessError`로 거부한다. 루트 자체의 링크는 허용한다.
- 검증:
  - 이전 재현 사례(probe 1)가 이제 거부된다.
  - 변형 사례(probe 2·3·5)도 거부된다.
  - 허용해야 하는 루트 별칭(probe 4)과 기준 하네스(probe 7)는 정상으로 로드된다.
  - `LinkTests` 6개 중 3개가 실행·통과했다. 실제 파일 심볼릭 링크 3건은 환경 권한 때문에 skip이고, 같은 분기를 대체 테스트로 확인했다.
- 의견 충돌: 없음.

## 별도 제안·판단 요청
- **하드링크(통과를 막지 않음):** 하드링크로 만든 파일은 이 하네스 폴더 안의 일반 파일이다. 폴더 밖 파일을 "따라가서" 읽는 경로가 아니므로, 파일을 복사해 넣은 것과 효과가 같다. 복사는 어떤 로더로도 막을 수 없으므로 R-1의 범위로 보지 않는다. 구현자가 이 제한을 implementation.md에 적어 두었다. 정답 자료 유입을 막는 근본 장치는 하네스 내용에 대한 검토와 해시 기록이며, 하네스를 LLM이 자동 편집하게 되는 후속 작업에서 다룰 사항이다.
- **파일 링크 실제 테스트:** 링크 생성 권한이 있는 환경(Linux 또는 Windows 개발자 모드)에서 skip된 3건을 한 번 실행해 보면 대체 테스트의 근거를 보강할 수 있다. 필수는 아니다.
- **스타일(통과를 막지 않음):** `agent/harness.py`의 모듈 docstring 11행(105자)과 81행(102자)이 100자를 넘는다. 저장소에는 이미 100자를 넘는 줄이 있어(`agent/tools.py:127` 등) 규칙 위반으로 보지 않는다.
- 이전 리뷰의 확인 항목 A(계약 문장의 하네스 이동)와 C(AC-4 줄 수 표기)는 판단이 바뀌지 않았다. 둘 다 수정할 필요가 없다.
