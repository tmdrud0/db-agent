# DB Agent 공통 지침

## 프로젝트 맥락

BIRD-Interact Lite로 자체 에이전트 루프를 개발·평가한다. 현재 코드는 모델 없이
빈 SQL을 한 번 제출하는 연결 확인용 구현이다. 프로젝트 작업을 시작할 때
`docs/agent/project.md`를 읽고 현재 코드와 해당 작업의 명세를 확인한다.
일반 Markdown 파일이나 링크 내용이 자동으로 프롬프트에 들어온다고 가정하지 않는다.

## 역할과 작업 자료

- 조율자: `$dev-coordinate`, 지침·템플릿은 `docs/agent/roles/coordinator.md`.
- 구현자: `$dev-implement`, 지침·템플릿은 `docs/agent/roles/implementer.md`.
- 리뷰어: `$dev-review`, 지침·템플릿은 `docs/agent/roles/reviewer.md`.

사용자가 지정한 역할과 작업 파일을 읽는다. 선택하지 않은 역할의 상세 지침을
일괄 로드하지 않는다. 새 채팅이 이전 대화를 안다고 가정하지 말고
`docs/agent/tasks/<작업 ID>/`의 명세·보고서와 정확한 커밋 SHA로 상태를 전달한다.
일반 질문은 해당 역할을 선택하지 않아도 답할 수 있다.

## 구현과 검토

구현자는 코드·테스트를 로컬 커밋한 뒤 전체 SHA를 보고서에 기록한다.
리뷰어는 명세의 기준 SHA와 구현 SHA 사이의 누적 diff를 검토한다.
수정은 후속 커밋으로 남기고 새 코드 SHA를 다시 검토한다.
보고서는 코드 커밋 이후 별도 문서 커밋으로 저장할 수 있다.
로컬 커밋과 사용자 최종 승인, 원격 push를 구분한다.
원격 변경은 해당 작업의 사용자 요청과 기존 권한을 따른다.
사용자의 기존 변경을 임의로 덮어쓰거나 초기화하지 않는다.

## 코드·평가 계약

세션은 `(mode, task_id)`별 메모리에 저장하므로 현재 Uvicorn worker는 1개다.
평가 기본 동시성은 10이다. 이 조건을 변경하는 작업이면 영향과 검증을 명세에 적는다.
공식 채점 응답으로 보상·단계 성공을 기록하고 연결 오류를 오답 0점과 구분한다.
정답·테스트 케이스를 에이전트 입력에 넣거나 숨겨진 정보를 로컬 파일에서 우회해 읽지 않는다.
GT, API 키, `.env`, `private/`, `results/`, `vendor/`, 가상환경과 로컬 설정을 커밋하지 않는다.

## 검증

프로젝트 루트의 PowerShell에서 관련 검증을 선택한다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\scripts\run-smoke.ps1
.\scripts\run-dev30.ps1
```

unittest는 모의 HTTP 계약 검증이고 실제 DB 채점과 다르다.
평가 스크립트는 oracle 후 자체 에이전트를 실행하며 결과 파일을 갱신한다.
검증한 코드 버전·명령·결과와 미검증 사항을 기록한다.
설치·평가 준비는 `docs/setup.md`, 역할 운영은 `docs/agent/README.md`를 참고한다.
