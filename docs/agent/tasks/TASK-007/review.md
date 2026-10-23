# TASK-007 리뷰 보고서

작업 ID: TASK-007
명세 버전: 1 + 사용자 확정 범위 추가(모델 호출 일시 실패 시 최대 3회 재시도, 2026-10-20 리뷰 채팅에서 사용자가 "내가 요청해서 그것도 포함하기로 했다"로 확정)
대상 코드 버전: 87f7b9f487dc9032105d102fd1fd6840a2b2a253
상태: pass
요약: R-1은 6840ff6에서 해결됐다. 87f7b9f의 모델 호출 재시도는 사용자가 TASK-007 범위에 포함하기로 확정해 D-1이 선택지 (a)로 결정됐다. 이 범위 기준으로 AC-4를 "재시도 외 tools 루프 동작 무변경"으로 판정하면 모든 완료 조건을 충족하고, 재시도 코드에서 수정이 필요한 결함은 찾지 못했다. D-1의 관찰 1~4는 명세에 없는 요구라 통과를 막지 않는 별도 제안으로 넘긴다.
근거: `git diff 157a57a 87f7b9f` 누적 diff, 후속 diff `1f66d46..6840ff6`, `6840ff6..87f7b9f`, `agent/tool_loop.py`의 `_model_call`·`_bounded`, 전체 테스트 131개, 실행기 가드 실제 재현.
미확정 사항: 없음. 단 `spec.md`는 아직 버전 1이므로 조율자가 사용자 확정 범위(재시도)를 명세 변경 이력·AC-4에 v2로 기록해야 한다. 그 기록이 이 리뷰의 판정 기준(아래 AC-4 조정 문구)과 같으면 코드 재검토는 필요 없고, 다르면 다시 검토한다.

## 검토 범위
기준 커밋: 157a57a148bff68612f5b242e06c43a076250db3
검토 코드 커밋: 87f7b9f487dc9032105d102fd1fd6840a2b2a253 (작업 커밋 1f66d46 → 6840ff6 → 87f7b9f)
검토 명령: git diff 157a57a148bff68612f5b242e06c43a076250db3 87f7b9f487dc9032105d102fd1fd6840a2b2a253 --
검증한 작업 공간 코드 버전: HEAD = 87f7b9f487dc9032105d102fd1fd6840a2b2a253, `git diff --quiet HEAD` 통과. 미추적은 `docs/agent/tasks/TASK-007/`와 무시된 산출물뿐.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 1차 리뷰 판정 유지. 후속 커밋은 fixed 경로를 되살리지 않음. 실행기는 잔존 `AGENT_MODE`를 거부만 함. |
| AC-2 | 충족 | `agent/server.py` 1f66d46 이후 무변경. 제거 모드 거부 테스트 통과. |
| AC-3 | 충족 | R-1 해결. `Assert-AgentModeEnvironment`가 함수 정의 직후·`Import-AgentApiKey`·compose 이전에 실행됨. 자식 PowerShell에서 `$env:AGENT_MODE='fixed'`로 `run-evaluation.ps1 -Limit 1 -Calibrated` 실행 → `AGENT_MODE='fixed' is set, but only the tool-selecting agent remains. Run 'Remove-Item Env:AGENT_MODE' and retry.`, 실행 후 값 `fixed` 유지, 기존 컨테이너 가동 시간 변화 없음. 옵션 없는 정상 경로 smoke는 1f66d46 기준 구현자 결과(해시 일치)로 확인, 이후 실행기 변경은 앞단 가드 추가뿐. |
| AC-4 | 충족(사용자 확정 범위 기준: 모델 호출 재시도 외 tools 동작 무변경) | `tool_loop.py`·`tools.py`·`environment.py`·`server.py`(1f66d46 이후)·시뮬레이터 어댑터 무변경. 변경은 `agent/model.py`의 재시도뿐: 5xx/408/429·transport/timeout·깨진 JSON/payload만 최대 4회 요청, 401 등 4xx·키 누락은 1회, 소진 시 `ModelError("... (after 4 attempts)")`로 기존 실패 경로(무과금·`failure.stage=model`·HTTP 502·재실행 없음) 유지, 세션 기한 `_bounded`가 전체 재시도를 감쌈. 시뮬레이터는 `ModelClient`를 쓰지 않음. 명세 v1 원문 기준으로는 미충족이었으며 사용자 확정으로 범위가 조정됨. |
| AC-5 | 충족 | 131 tests OK, `git diff --check 157a57a 87f7b9f` 통과. 세션 계약 테스트는 재시도 소진 기준으로 갱신됐고 무과금·서비스 무호출·동시 요청 재실행 없음 유지. |
| AC-6 | 충족(형식) | 세 커밋 모두 `[TASK-007]` 제목, 코드·테스트·스크립트만 포함, push 없음. 보고서에 최종 SHA 기록. 단 명세 버전이 "1 (사용자 추가 요청 포함)"으로 표기돼 정식 명세 버전과 다름 → 아래 판단 요청. |

## 수정 요청
없음(코드 결함 기준). 판정 보류 사유는 아래 "판단 요청".

## 판단 요청 (조율자)

### D-1: 모델 호출 재시도(87f7b9f)의 범위 확정 — 결정됨
- 결정: 사용자가 리뷰 채팅에서 재시도를 TASK-007에 포함하기로 확정(선택지 a). 조율자는 `spec.md`에 v2(변경 이유: 사용자 추가 요청, AC-4 조정)를 기록한다. 아래 관찰 1~4는 요구사항이 아니라 후속 개선 제안으로 남긴다.
- 원래 사실: 구현 보고서에 따르면 사용자가 "모델 호출 실패했을 때 재시도 3번까지"를 TASK-007 추가 수정으로 요청했다. `spec.md`는 여전히 버전 1이며 변경 이력·완료 조건에 재시도가 없다. AGENTS.md상 명세 변경은 조율자가 버전·이유를 기록하고 구현·리뷰에 새 버전을 전달해야 한다.
- 선택지: (a) 명세 v2로 TASK-007 범위에 재시도를 추가하고 AC-4를 "모델 호출 재시도 외 tools 동작 무변경"으로 조정, (b) 87f7b9f를 TASK-007에서 빼고 별도 작업으로 분리(TASK-007은 6840ff6 기준으로 재판정 가능, 이 경우 1f66d46~6840ff6 범위는 pass 수준).
- v2로 포함할 경우 요구사항 여부를 정해야 할 관찰(현재 명세가 없어 결함으로 판정하지 않음):
  1. 기록 누락: 재시도 후 성공하면 `adk_events`의 `model_call` 이벤트에 시도 횟수·실패한 시도의 오류·소요 시간이 남지 않는다(`ChatReply`에 시도 정보 없음, `elapsed_seconds`만 전체 합산). 프로젝트 평가 관점(실제 행동 기록·시간·비용)상 실제 HTTP 요청 수가 과소 기록된다. 200 응답이지만 payload가 깨져 재시도된 경우 과금됐을 수 있는 usage도 버려진다.
  2. 대기 없는 즉시 재시도: 429(rate limit)·503도 지연 없이 연속 4회 요청한다. 동시성 10 평가에서 rate limit이면 재시도가 대부분 같은 이유로 실패할 가능성이 높다.
  3. 시간 상한: 시도당 timeout 300초 × 4 = 최대 1200초가 한 모델 행동에 쓰일 수 있다. `_bounded`로 세션 기한 1500초 안에서 끊기므로 계약 위반은 아니지만, 한 번의 느린 공급자 장애가 세션 대부분을 소모할 수 있다.
  4. 결과 비교: TASK-006·TASK-007(1f66d46) smoke와 이후 결과는 모델 실패 처리 조건이 다르므로 비교 시 코드 SHA로 구분해야 한다.

## 독립 검증
| 명령·검증 | 결과 |
| --- | --- |
| `git rev-parse HEAD`, `git diff --quiet HEAD` | 87f7b9f…, 추적 변경 없음 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests` | Ran 131 tests, OK |
| `git diff --check 157a57a 87f7b9f` | 출력 없음 |
| `git diff --stat 1f66d46 87f7b9f -- agent/` | `agent/model.py`만 변경 |
| `git grep -n "agent.model\|ModelClient" -- benchmark scripts orchestrator` | 시뮬레이터·실행기에서 사용 없음(재시도는 에이전트 모델 호출에만 적용) |
| 자식 PowerShell: `$env:AGENT_MODE='fixed'; & .\scripts\run-evaluation.ps1 -Limit 1 -Calibrated` | 명확한 오류로 즉시 중단, 값 보존, `db-agent-smoke` 컨테이너 가동 시간 변화 없음 |

실행하지 않은 사항: 87f7b9f 기준 실제 스택 smoke(모델 비용·컨테이너 재생성 회피), 실제 공급자 오류에서의 재시도 동작.

## 이전 지적 처리
| ID | 수정 횟수 | 상태 | 근거 |
| --- | --- | --- | --- |
| R-1 (잔존 `AGENT_MODE=fixed`에서 원인 없는 기동 실패) | 1회(6840ff6) | 해결 | 실행기 앞단 가드, 값·`Remove-Item Env:AGENT_MODE` 안내, 값 미삭제. `AgentModeGuardTests` 3개(허용·거부·환경 보존)가 실제 스크립트의 함수 AST를 추출해 검증. 리뷰어 실제 실행 재현 일치. |

1차 리뷰(1f66d46): changes_requested(R-1). 당시 AC-1·2·4·5·6 충족, AC-3 부분 충족.
2차 리뷰(87f7b9f): needs_decision(D-1, 재시도 범위). 사용자 확정 후 같은 SHA로 pass. 코드 변경 없음(HEAD 87f7b9f, 추적 변경 없음 재확인).

## 별도 제안·판단 요청
- S-1(1차와 동일, 통과 조건 아님): `README.md:72`가 삭제된 `agent/loop.py`를 현재 구성으로 설명한다. 래퍼가 `CMD_API_KEY`·시뮬레이터 모델 호출을 필요로 한다는 점, 재시도가 포함되면 그 동작도 문서 갱신 후보로 조율자가 사용자 확인 후 판단.
