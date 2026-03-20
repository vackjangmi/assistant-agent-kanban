# Contributing Guide

Assistant Agent Kanban에 관심을 가져 주셔서 감사합니다. 이 문서는 이 저장소에 기여하려는 분들을 위한 실무 가이드입니다.

이 프로젝트는 단순한 웹 UI 프로젝트가 아니라, 파일시스템 상태 머신, AI worker orchestration, 격리 workspace, human verification 흐름이 함께 엮인 시스템입니다. 그래서 작은 변경이라도 상태 전이, 문서 산출물, target repo 반영 규칙에 영향을 줄 수 있습니다.

## 먼저 읽어 주세요

작업을 시작하기 전에 아래 문서를 먼저 읽는 것을 권장합니다.

- `README.md`
- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`

이 문서들은 단순 참고 자료가 아니라, 현재 구현의 설계 배경과 제약을 설명하는 기준 문서입니다.

## 개발 환경 준비

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

또는:

```bash
./init.sh
```

앱 실행:

```bash
./run.sh
```

테스트 실행:

```bash
pytest -q
```

## 기여 방식

이 저장소는 기본적으로 직접 코드 수정 권한을 열어두지 않는 운영 방식을 전제로 합니다.

- 저장소 본체에 직접 push하지 않습니다.
- 저장소 내부에서 contributor용 브랜치를 직접 생성하는 흐름을 기본 정책으로 두지 않습니다.
- 외부 기여는 fork 후 Pull Request 방식으로 받습니다.

권장 흐름은 다음과 같습니다.

1. 이 저장소를 fork합니다.
2. 본인 fork에서 작업용 브랜치를 생성합니다.
3. 수정 후 테스트를 실행합니다.
4. 본인 fork에 push합니다.
5. 원본 저장소로 Pull Request를 생성합니다.

즉, 이 프로젝트의 공개 기여 모델은 `fork -> branch -> PR` 입니다.

## 저장소 구조 요약

- `src/assistant_agent_kanban/`: 도메인, worker, runtime, API
- `src/assistant_agent_kanban/workers/`: planner, implementer, reviewer, committer
- `src/assistant_agent_kanban/api/`: FastAPI app, routes, SSE, UI
- `tests/`: 단위/통합 테스트
- `.opencode/agents/`: 역할별 에이전트 프롬프트 계약
- `examples/`: 설정 예시와 bootstrap 예시
- `docs/`: 설계 및 구현 배경 문서

## 기여 원칙

### 1. 상태 머신을 먼저 존중해 주세요

이 프로젝트의 핵심은 “AI를 호출하는 코드”보다 “상태를 안전하게 추적하는 워크플로 엔진”입니다.

특히 아래 원칙은 유지되어야 합니다.

- 상태의 source of truth는 파일시스템 상태 + `metadata.json`
- 허용된 상태 전이만 가능해야 함
- 상태 전이는 lock 하에서만 수행
- task 디렉토리와 workspace는 분리
- review 통과 전에는 target repo에 patch를 적용하지 않음
- 최종 commit은 `human-verifying -> done`에서만 수행

### 2. 작은 변경을 선호합니다

가능하면 다음 순서를 따르는 것이 좋습니다.

- 먼저 문제를 재현
- 영향을 받는 상태/worker/API를 확인
- 최소 수정 적용
- 관련 테스트 추가 또는 보강
- UI 변경이라면 실제 브라우저에서 한 번 확인

### 3. 구현보다 검증이 중요합니다

이 프로젝트는 자동화 시스템이기 때문에, “동작할 것 같다”보다 “검증했다”가 중요합니다.

기여 시 가능한 한 아래를 확인해 주세요.

- 관련 테스트 통과
- 상태 전이 규칙 유지
- metadata/lock/history가 깨지지 않음
- workspace / integration / verification 흐름이 기존 규칙과 충돌하지 않음

## 권장 작업 흐름

1. 이슈나 개선 아이디어의 범위를 분명히 합니다.
2. 관련 문서와 코드를 읽습니다.
3. 작은 단위로 수정합니다.
4. 테스트를 실행합니다.
5. README 또는 관련 문서가 바뀌어야 한다면 함께 반영합니다.

## 코드 스타일 가이드

이 저장소는 다음 방향을 선호합니다.

- Python 3.11+
- Pydantic v2 기반 모델
- 작은 함수, 테스트 가능한 구조
- subprocess 래퍼 분리
- atomic write 사용
- 의미 있는 도메인 예외 사용
- 로그에 민감 정보 남기지 않기

UI 쪽은 다음 원칙을 따릅니다.

- 단일 HTML 페이지 + vanilla JS
- SSE 기반 실시간 반영
- 과한 프레임워크 의존보다 구조적 단순성 우선

## 테스트 범위

현재 테스트는 대략 아래 범주를 다룹니다.

- scanner
- transitions
- metadata store
- locks
- planner worker
- implementer worker
- reviewer worker
- committer worker
- recovery
- API

변경한 영역이 있다면 최소한 관련 테스트는 직접 돌려 주세요.

```bash
pytest -q
```

## 문서 기여도 환영합니다

이 프로젝트는 아키텍처와 운영 규칙이 중요한 편이라, 문서 기여도 매우 가치가 있습니다.

예를 들면:

- README 개선
- 설정 가이드 보강
- 예시 request / plan / review 문서 추가
- 상태 머신 설명 보강
- 에러 상황 / recovery 시나리오 문서화

## Pull Request 작성 팁

PR에는 아래 내용이 들어가면 좋습니다.

- 무엇을 바꿨는지
- 왜 필요한지
- 어떤 경로로 검증했는지
- 상태 머신/verification 규칙에 어떤 영향이 있는지
- 관련 이슈 또는 논의 링크

가능하면 큰 PR보다 작은 PR을 선호합니다.

추가로 아래 기준을 권장합니다.

- 하나의 PR은 하나의 주제에 집중해 주세요.
- UI 변경이면 스크린샷이나 짧은 설명을 함께 남겨 주세요.
- worker / transition / verification 관련 변경이면 재현 시나리오를 같이 적어 주세요.
- 문서만 바꾼 PR이라면 어떤 사용자를 돕기 위한 문서인지 적어 주세요.

## 변경 시 특히 조심할 부분

- `transitions.py`
- `locks.py`
- `metadata_store.py`
- `workspace_manager.py`
- `integration_manager.py`
- `workers/*.py`
- `api/templates/index.html`

이 영역은 UI, worker, filesystem state가 서로 연결되어 있어서 작은 변경도 파급이 큽니다.

## 질문 또는 논의

버그, 설계 질문, 개선 제안은 이슈로 남겨 주세요. 가능하면 다음 정보를 함께 적어 주세요.

- 기대 동작
- 실제 동작
- 재현 방법
- 관련 상태(`planning`, `reviewing` 등)
- 관련 파일 또는 스크린샷

기여해 주셔서 감사합니다.
