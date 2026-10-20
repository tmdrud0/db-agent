# TASK-007 작업 명세

작업 ID: TASK-007
명세 버전: 2
대상 코드 버전: 157a57a148bff68612f5b242e06c43a076250db3
상태: planned
요약: 고정 컨텍스트(fixed) 에이전트 모드를 코드에서 제거하고, TASK-006의 9개 도구 선택 루프(tools)를 유일하고 기본인 에이전트 동작으로 만든다.
근거: 사용자의 "코드에서 기존 내용 지우고 기본을 현재 코드로 하는 수정" 요청. 이 채팅에서 사용자가 fixed 모드가 남아 있는지 확인한 직후의 요청이며, 조사 결과 `agent/server.py`의 `DEFAULT_MODE = "fixed"`, `scripts/run-evaluation.ps1`의 `-AgentMode` 기본값 `fixed`, 래퍼 스크립트가 모드를 넘기지 않아 fixed로 실행되는 상태를 확인했다.
미확정 사항: 없음. "기존 내용"은 fixed 모드와 그 모드 선택 경로로 해석했다(아래 범위). 보정 DB·oracle·데이터 준비 스크립트와 과거 results는 제거 대상이 아니다.

## 목표

에이전트 서비스와 평가 실행기가 별도 옵션 없이 9개 도구 선택 루프로 동작한다. fixed 루프와 이를 고르는 모드 분기·기본값이 코드에서 사라지고, 기존 tools 모드의 동작·계약·채점 결과 형식은 그대로 유지된다.

사용자가 확인할 동작:
- `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated`처럼 모드 옵션 없이 실행해도 9개 도구 에이전트가 실행된다.
- 에이전트 `/health`가 9개 도구 목록·가격을 보고하며 fixed 관련 이름·분기가 남지 않는다.
- 전체 테스트가 통과한다.

## 조사한 기준 코드

- `agent/loop.py`: fixed 루프 본체(정보 3종 1회 조회 → phase별 모델 1회·제출 1회). `agent/server.py`와 `tests/test_connection.py`만 import한다. `InputError`를 server가 이 모듈에서 import한다(정의는 `agent/session.py` 경로와 함께 확인 필요).
- `agent/server.py`: `AGENT_NAMES`(fixed/tools), `DEFAULT_MODE = "fixed"`, `AGENT_NAME`, `RUNNERS`, `AGENT_MODE` 환경변수 또는 `create_app(mode=...)`로 모드 선택, `/health.tools`는 tools일 때만 채운다.
- `agent/session.py`: 두 루프가 공유하는 예산·종료·기록·실패 복원 도우미. tools 루프가 쓰는 부분은 유지한다.
- `scripts/run-evaluation.ps1`: `-AgentMode fixed|tools`(기본 fixed), `$agentNames` 매핑, `$env:AGENT_MODE` 설정, `Assert-Simulator`·`Assert-BaselineAgent`의 tools 전용 검사 분기.
- `scripts/run-smoke.ps1`, `scripts/run-dev30.ps1`: `-AgentMode`를 넘기지 않아 현재 fixed로 실행된다. 이 작업 후에는 tools로 실행된다.
- `tests/test_connection.py`: fixed 루프 동작 테스트와 함께 서버 세션 계약(`/init_session`·reset·재사용·캐시된 결과·init 전 호출 404·질문 누락 입력 오류·동시 요청 무중복·실패 후 무재시도)을 fixed 루프로 검증한다. `tests/test_tool_loop.py`는 `AGENT_NAMES`를 import한다.
- README·`docs/setup.md`에서 `fixed`/`AgentMode`/`AGENT_MODE` 문자열은 검색되지 않았다.

## 범위

### 1. fixed 모드 제거
- `agent/loop.py`의 fixed 루프와 이를 위한 전용 상수·프롬프트를 삭제한다. 다른 모듈이 쓰는 공용 요소가 있으면 사용처에 맞는 모듈로 옮기고 동작을 바꾸지 않는다.
- `agent/server.py`에서 모드 분기(`AGENT_NAMES`의 fixed, `DEFAULT_MODE`, `RUNNERS`, `mode` 인자·`AGENT_MODE` 선택)를 정리해 tools 루프만 실행한다. 모드 선택 인터페이스를 남길지(단일 값만 허용) 완전히 없앨지는 구현자가 정하되, `AGENT_MODE=fixed`처럼 제거된 모드를 명시적으로 요청하면 조용히 tools로 실행하지 말고 명확한 오류로 거부한다.
- 에이전트 이름 `deepseek-v4.1-flash-tool-selecting-two-phase`와 `/health`의 `tools`·timeout 보고는 유지한다. 실행기가 오래된 서비스(7개 도구, fixed)를 재사용하지 않는 검증은 유지한다.

### 2. 실행기 기본값
- `scripts/run-evaluation.ps1`에서 `-AgentMode` 매개변수와 fixed 분기를 제거하거나 tools만 허용하도록 바꿔, 옵션 없이 tools 에이전트가 기동·검증되게 한다. tools 전용이던 9개 도구·시뮬레이터 연결 검사는 항상 수행한다.
- 래퍼 스크립트(`run-smoke.ps1`, `run-dev30.ps1`)는 수정 없이 또는 최소 수정으로 tools 에이전트를 실행하게 한다. 이 래퍼들이 이제 질문 시뮬레이터 모델 호출과 `CMD_API_KEY`를 필요로 한다는 점을 보고서에 적는다.
- 보정 DB·oracle 실행(`run-dev30-calibrated.ps1` 등)·데이터 준비·보정 검증 스크립트는 범위 밖이며 동작을 바꾸지 않는다.

### 3. 테스트
- fixed 루프 전용 동작 테스트는 삭제한다.
- 서버 세션 계약(init/reset/재사용·캐시된 결과·init 전 404·입력 오류·동시 요청 무중복·실패 후 무재시도·`.env` 키 로딩)은 tools 루프 기준으로 계속 검증되게 한다. `tests/test_tool_loop.py`에 이미 같은 계약이 있으면 중복 추가하지 않고, 없는 계약만 옮긴다. 어떤 테스트를 삭제·이전·유지했는지 보고서에 목록으로 남긴다.
- 제거된 모드 요청이 명확히 거부되는지 테스트한다.

### 4. 모델 호출 재시도 (v2 추가, 사용자 요청)
- 에이전트의 Command Code 모델 호출(`agent/model.py`의 `ModelClient`)이 일시적으로 실패하면 최초 호출 뒤 최대 3회(총 4회) 재시도한다.
- 재시도 대상: HTTP 408·429·5xx, transport 오류·timeout, JSON이 아닌 응답, `choices`가 없거나 깨진 성공 응답. 제외: API 키 부재, 그 밖의 4xx, 빈 content·절단 응답.
- 재시도 후에도 실패하면 기존 모델 실패 경로(무과금·`failure.stage="model"`·재실행 없음)를 유지하고, 전체 재시도는 tools 루프의 세션 기한 안에서 끝난다.
- 사용자 시뮬레이터 내부 모델의 재시도 설정(`USER_SIM_MAX_RETRIES`)은 바꾸지 않는다. backoff·시도별 기록은 요구하지 않는다.

## 제약

- 범위 4의 모델 호출 재시도를 제외하고, tools 루프의 행동·프롬프트·도구 목록·가격·예산·캐시·기한·세션 계약·결과 state 형식을 바꾸지 않는다. 이 작업은 삭제·정리이며 루프 개선이 아니다.
- 공식 채점 응답으로 보상을 기록하고 연결 오류를 오답과 구분하는 계약을 유지한다. GT·테스트 케이스를 에이전트 입력에 넣지 않는다.
- vendor·GT·원본 문제·채점기·보정 데이터·과거 `results/` 파일을 수정하거나 삭제하지 않는다. 과거 fixed 결과(`results/two_phase_manual_30.json` 등)는 비교 근거로 남는다.
- 사용자가 진행 중인 30문제 평가(`results/tools_dev30_calibrated_task006*`)의 컨테이너(`db-agent-smoke`)·포트 6000~6002·결과 파일을 중단·재생성·덮어쓰지 않는다. 실제 실행 확인이 필요하면 평가가 끝난 뒤 수행하거나 사용자 확인을 받는다.
- 보고서 외 문서 수정: 없음. README·`docs/setup.md`·상태 기록·공통/역할 지침·기존 TASK 문서를 수정하지 않는다. 문서 갱신이 필요해 보이면 implementation.md에 후속 사항으로 남긴다.

문서 산출물: docs/agent/tasks/TASK-007/implementation.md (변경·삭제 목록·실행 방법·검증 결과·정확한 SHA).
보존 정책: implementation.md·review.md는 로컬 작업 기록. 사용자 확인 후 spec.md·final.md를 한 번에 커밋.

## 완료 조건

- AC-1: `agent/loop.py`의 fixed 루프와 server·실행기의 fixed 분기·기본값이 제거되고, 저장소 코드(`agent/`, `scripts/`, `tests/`, compose 파일)에서 fixed 모드를 실행할 경로가 없다. `git grep`으로 남은 fixed 모드 참조를 확인해 보고한다(고정 30문제 선택처럼 다른 의미의 "fixed"는 제외).
- AC-2: 에이전트 서비스는 별도 설정 없이 9개 도구 루프로 동작하고 `/health`가 기존 tools 모드와 같은 이름·도구 목록·가격·timeout을 보고한다. 제거된 모드를 요청하면 명확한 오류가 나며 테스트로 확인한다.
- AC-3: `run-evaluation.ps1`을 모드 옵션 없이 실행하면 tools 에이전트를 기동·검증하고 9개 도구·시뮬레이터 연결 검사를 수행한다. 스크립트 변경은 PowerShell 구문 검사(예: 파서 오류 없음)와 정적 확인으로 검증하고, 실제 스택 실행은 진행 중 평가와 충돌하지 않을 때만 1문제 smoke로 수행한다(수행하지 못하면 미검증으로 기록).
- AC-4: 모델 호출 재시도(범위 4) 외 tools 루프 동작은 기준 SHA와 같다. `agent/tool_loop.py`·`agent/tools.py`·`agent/environment.py`·시뮬레이터 어댑터의 동작 변경이 없음을 diff로 보이고, 기존 tools 계약 테스트가 통과한다. 재시도는 일시 실패 후 성공·4회 소진·4xx/키 부재 무재시도·소진 후 기존 실패 경로 유지를 테스트로 확인한다.
- AC-5: 서버 세션 계약 테스트가 tools 루프 기준으로 유지되고, 삭제·이전·유지한 테스트 목록이 보고서에 있다. 전체 테스트와 `git diff --check`가 통과한다.
- AC-6: 코드·테스트·스크립트만 명시적으로 stage해 `[TASK-007]` 제목의 로컬 커밋을 만들고 전체 SHA·명세 버전을 implementation.md에 기록한다. 같은 버전·최종 SHA의 독립 리뷰가 pass가 되기 전 조율자는 ready_for_user로 표시하지 않는다. 문서 커밋·원격 push는 하지 않는다.

## 조사할 사항

- `InputError` 등 server가 `agent/loop.py`에서 가져오는 이름의 실제 정의 위치와, `agent/session.py`·`agent/model.py`에 fixed 전용으로만 쓰이던 요소가 있는지.
- `tests/test_connection.py`의 세션 계약 중 `tests/test_tool_loop.py`가 이미 덮는 것과 아닌 것.
- 실행기의 `Assert-BaselineAgent`·`Assert-Simulator` 분기를 단순화할 때 비 tools 경로에서만 쓰이던 검사(예: 시뮬레이터 연결 미확인 허용)가 사라지는 영향.

## 명세 변경 이력

| 버전 | 변경 내용·이유 | 구현·리뷰에 미치는 영향 |
| --- | --- | --- |
| 1 | 최초 명세. 사용자 요청에 따라 fixed 모드 제거·tools 단일 기본화 | agent/server.py·agent/loop.py·run-evaluation.ps1·관련 테스트 |
| 2 | 사용자 추가 요청 "모델 호출 실패했을 때 재시도 3번까지"를 범위 4로 추가하고 AC-4를 "재시도 외 tools 동작 무변경"으로 조정. 사용자가 리뷰 채팅에서 TASK-007 포함을 확정(리뷰 D-1 선택지 a). 기준 커밋은 유지 | `agent/model.py`·`tests/test_model.py`·`tests/test_tool_loop.py`(커밋 87f7b9f). 리뷰는 같은 AC-4 문구로 87f7b9f를 pass 판정해 재검토 불필요 |

## 전달 정보

명세 경로·버전: docs/agent/tasks/TASK-007/spec.md, 2
기준 커밋: 157a57a148bff68612f5b242e06c43a076250db3
작업 폴더: docs/agent/tasks/TASK-007/
커밋 제목 형식: [TASK-007] <type>: <변경 요약>
기존 미커밋 변경: 추적 파일 변경 없음. 미추적·무시된 로컬 기록(TASK-00x implementation.md·review.md)과 진행 중 평가 결과 파일은 건드리지 않는다.
직접 확인 방법: `.\.venv\Scripts\python.exe -m unittest discover -s tests` 통과, 진행 중 평가가 끝난 뒤 `.\scripts\run-evaluation.ps1 -Limit 1 -Calibrated -ResultName tools_smoke_task007 -OracleResultName oracle_smoke_task007`로 모드 옵션 없이 9개 도구 에이전트가 실행되는지 확인.
