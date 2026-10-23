# TASK-008 리뷰 보고서

작업 ID: TASK-008
명세 버전: 1
대상 코드 버전: c8db8f22e38a03616c1de21d54e014db087e9041
상태: pass
요약: R-3이 해결돼 수정 요청이 남지 않았다. 후속 커밋 `c8db8f2`는 parity 테스트에서 모델 응답 시계도
고정하는 테스트 변경뿐이다. `tests.test_tracing` 30회 반복 0 실패, 전체 158개 테스트 반복 통과,
셸 키 상속 상태에서도 실제 SDK 클라이언트 0개다. 누적 diff는 AC-1~AC-9를 충족한다.
근거: `git diff 0418483..c8db8f2`, 누적 diff `9255f21..c8db8f2`, 반복 테스트, 키 상속 재현 스크립트,
이전 검토의 Langfuse API 조회(제품 코드 무변경이라 유효).
미확정 사항: AC-6에서 public key를 어떻게 볼지는 조율자 판단 사항이다(아래). 리뷰어는 허용으로 판단했고 판정을 막지 않는다.

## 검토 범위
기준 커밋: 9255f21b96891c5ed58df04ade15c5e37cecefc5
검토 코드 커밋: c8db8f22e38a03616c1de21d54e014db087e9041
검토 명령: git diff 9255f21b96891c5ed58df04ade15c5e37cecefc5 c8db8f22e38a03616c1de21d54e014db087e9041 --
(후속 수정분: git diff 04184831f2d758acdebd224e5e3e72ae728f7c10 c8db8f22e38a03616c1de21d54e014db087e9041 --,
`tests/test_tracing.py` 7줄 추가·1줄 삭제만 변경)
검증한 작업 공간 코드 버전: HEAD = c8db8f22e38a03616c1de21d54e014db087e9041, `git status --short`는 untracked
`docs/agent/tasks/TASK-008/`만 보인다. `agent/`·`scripts/`·requirements는 `0418483`과 동일하다.

## 완료 조건별 판정
| 조건 | 판정 | 근거 |
| --- | --- | --- |
| AC-1 | 충족 | 키 미설정 시 SDK 미로드 no-op(`DisabledTracingTests`). 실제 tracer는 lifespan startup에서만 만든다. 리뷰어 재현: 가짜 키가 있는 환경에서 전체 158개 3회 실행 → SDK `Langfuse` 생성 0, `server.create_tracer` 호출 0, 모두 OK(약 13초). |
| AC-2 | 충족 | `SuccessfulTraceTests`가 phase 2 전환 세션 포함 trace 1·generation·tool span·child transition·submit 결과·점수 4개를 state와 대조한다. 모든 `record_action` 경로에 span이 짝지어 있다. |
| AC-3 | 충족 | early-return 뒤에만 trace를 시작하고, 실패 시 `fail()`만 호출해 점수를 만들지 않는다. `FailureTraceTests`(모델 500·grader 500·캐시 재호출·실패 재현) 통과. |
| AC-4 | 충족 | `FlushParityTests`가 NoopTracer 대비 성공 200·모델 500·grader 500 응답 본문과 status 동일성을 검증한다. 실제 시계 테스트로 flush 상한과 elapsed 불포함을 확인한다. R-3 수정 후 30회 반복 0 실패. |
| AC-5 | 충족 | `tools.py`·`environment.py`·시뮬레이터 어댑터 무변경. 제품 변경은 추적 호출, `attempts` 내부 전달, `record_action` 반환값, tracer 주입·lifespan·flush뿐이다. 기존 테스트 기대값 변경 없음. |
| AC-6 | 충족(판단 요청 유지) | 결과·로그에 세 키 0회(구현자 근거). trace observation에 secret key 없음(리뷰어 API 조회 2회). public key는 SDK scope 속성으로 포함된다. |
| AC-7 | 충족 | `0418483`(제품 코드가 `c8db8f2`와 동일) smoke: exit 0, oracle 1.0. 리뷰어 API 재조회에서 trace `c7749f5781952fb8e3f45fbcf21f3d79`의 GENERATION 19 / tool SPAN 18 + root 1, 점수 total_reward 0.0·phase1/2 False·stop_reason `budget_exhausted_before_submit`이 결과 JSON(`adk_events` 19, `tool_trajectory` 18, total_reward 0.0, final_response)과 일치했다. |
| AC-8 | 충족 | `langfuse==5.0.2`가 requirements·두 lock에 반영됐다. 전체 테스트 158 OK(정상 환경 3회 + 키 상속 환경 3회), `git diff --check 9255f21 c8db8f2` exit 0. 새 venv 설치는 구현자 근거. |
| AC-9 | 충족 | 코드·테스트·스크립트·requirements만 담은 `[TASK-008]` 커밋 3개(`dbac6a5`, `0418483`, `c8db8f2`). 문서 커밋·push 없음. |

## 수정 요청
없음.

## 독립 검증
- `git diff --check 9255f21b96891c5ed58df04ade15c5e37cecefc5 c8db8f22e38a03616c1de21d54e014db087e9041`: exit 0.
- `.venv/Scripts/python.exe -m unittest tests.test_tracing` 30회 반복: 0 실패(수정 전 20회 중 4회 실패).
- 전체 테스트(Langfuse 환경변수 제거, PowerShell) 3회: 모두 158 tests OK(약 13초).
- 키 상속 재현(가짜 키, `http://127.0.0.1:9`) 3회: 모두 158 OK, SDK 클라이언트 0, `server.create_tracer` 호출 0.
- `agent/model.py`는 `time.monotonic`만 쓴다. 따라서 테스트의 `SimpleNamespace(monotonic=...)` 교체가 다른 시간 함수를 깨지 않는다.
- 미실행: `c8db8f2`에서 smoke 재실행(제품 코드가 `0418483`과 같아 이전 smoke로 갈음), 새 venv 설치, 동시성 10 평가의 flush 지연 측정.

## 이전 지적 처리
| ID | 수정 횟수 | 상태 | 근거 |
| --- | --- | --- | --- |
| R-1 | 1 | 해결 | `0418483`. tracer 생성을 lifespan으로 이동. 키 상속 환경에서 실제 클라이언트 0(이번에도 재확인). |
| R-2 | 1 | 해결 | `0418483`. NoopTracer 대비 성공·502 본문 비교와 실제 시계 elapsed 테스트 추가. |
| R-3 | 1 | 해결 | `c8db8f2`. `agent.model.time`도 결정적 시계로 고정. 30회 반복 0 실패. |

## 별도 제안·판단 요청
- 판단 요청(조율자): AC-6의 "키 값"에 Langfuse public key를 포함할지. 리뷰어 의견은 허용이다. secret key가 없고,
  같은 프로젝트의 공개 식별자이며, 제거하려면 SDK exporter를 대체해야 한다. 엄격히 금지한다면 후속 작업으로 분리한다.
- 제안(비차단): `run()`이 `ModelError`·`GradingError` 외 예외에서는 trace를 오류로 표시하지 않는다.
- 제안(비차단): `client.flush()`는 프로세스 전체 버퍼를 비우므로 동시성 10에서 응답당 최대 5초가 더해질 수 있다. 실제 평가에서 측정을 권한다.
- 후속 문서(구현자 보고): `docs/setup.md`에 Langfuse 설정 안내를 추가하는 일은 이번 범위 밖이다.
