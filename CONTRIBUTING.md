# Contributing Guide / 기여 가이드

## English

### Welcome

Thank you for your interest in Assistant Agent Kanban. This document is a practical guide for contributors.

This is not just a web UI project. It combines a filesystem state machine, AI worker orchestration, isolated workspaces, and a human verification flow. Even small changes can affect state transitions, artifact contracts, and target repo integration rules.

### Read These First

- `README.md`
- `AGENTS.md`
- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`

These documents are the baseline references for the current design and constraints.

### Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Or:

```bash
./init.sh
```

Run the app:

```bash
./run.sh
```

Run tests:

```bash
pytest -q
```

### Contribution Model

This repository assumes a no-direct-write contribution model.

- do not push directly to the upstream repository
- do not expect contributor branches to be created in upstream
- use a fork + Pull Request workflow

Recommended flow:

1. Fork the repository.
2. Create a branch in your fork.
3. Make changes and run tests.
4. Push to your fork.
5. Open a Pull Request against upstream.

### Repository Structure Summary

- `src/assistant_agent_kanban/` — domain, workers, runtime, API
- `tests/` — unit and integration tests
- `.opencode/agents/` — role prompt contracts
- `examples/` — config and bootstrap examples
- `docs/` — architecture, implementation, and agent docs

### Contribution Principles

#### 1. Respect the State Machine First

- the source of truth is filesystem state + `metadata.json`
- only allowed transitions are valid
- state transitions happen only under lock
- task directories and workspaces remain separate
- no target repo patch apply before review passes
- final commit happens only during `human-verifying -> done`

#### 2. Prefer Small Changes

- reproduce the problem first
- identify affected states/workers/API surfaces
- apply the smallest useful fix
- add or update relevant tests
- verify UI changes in a real browser when possible

#### 3. Verification Matters More Than Intuition

- relevant tests should pass
- transition rules must remain intact
- metadata/lock/history should stay consistent
- workspace / integration / verification flow should not conflict with existing rules

### Code Style Guide

- Python 3.11+
- Pydantic v2 models
- small functions and testable structure
- isolated subprocess wrappers
- atomic writes
- meaningful domain exceptions
- never log sensitive information

UI principles:

- single HTML page + vanilla JS
- SSE-based live updates
- avoid unnecessary framework complexity

### Test Coverage Expectations

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

Run the relevant tests for the area you changed.

### Pull Request Tips

Good PRs usually explain:

- what changed
- why it is needed
- how it was verified
- how it affects workflow / verification rules
- links to related issues or discussions

Recommended:

- keep one PR focused on one topic
- include screenshots for UI changes
- include reproduction steps for workflow changes
- explain the target audience for docs-only PRs

### Areas That Need Extra Care

- `transitions.py`
- `locks.py`
- `metadata_store.py`
- `workspace_manager.py`
- `integration_manager.py`
- `workers/*.py`
- `api/templates/index.html`

These areas are tightly coupled to UI behavior, worker flow, and filesystem state.

### Questions And Discussion

Please open an issue for bugs, design questions, or improvements. If possible, include:

- expected behavior
- actual behavior
- reproduction steps
- related workflow state
- related files or screenshots

Thank you for contributing.

---

## 한국어

### 안내

Assistant Agent Kanban에 관심을 가져 주셔서 감사합니다. 이 문서는 이 저장소에 기여하려는 분들을 위한 실무 가이드입니다.

이 프로젝트는 단순한 웹 UI 프로젝트가 아니라, 파일시스템 상태 머신, AI worker orchestration, 격리 workspace, human verification 흐름이 함께 엮인 시스템입니다. 그래서 작은 변경이라도 상태 전이, 문서 산출물, target repo 반영 규칙에 영향을 줄 수 있습니다.

### 먼저 읽어 주세요

- `README.md`
- `AGENTS.md`
- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`

이 문서들은 현재 구현과 제약의 기준 문서입니다.

### 개발 환경 준비

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

### 기여 방식

이 저장소는 직접 코드 수정 권한을 열어두지 않는 운영 방식을 전제로 합니다.

- 원본 저장소에 직접 push하지 않습니다.
- 원본 저장소에서 contributor용 브랜치를 직접 만드는 흐름을 기본 정책으로 두지 않습니다.
- 외부 기여는 fork 후 Pull Request 방식으로 받습니다.

권장 흐름:

1. 저장소를 fork합니다.
2. 본인 fork에서 브랜치를 만듭니다.
3. 수정 후 테스트를 실행합니다.
4. 본인 fork에 push합니다.
5. 원본 저장소로 Pull Request를 생성합니다.

### 저장소 구조 요약

- `src/assistant_agent_kanban/` — domain, worker, runtime, API
- `tests/` — 단위/통합 테스트
- `.opencode/agents/` — 역할별 agent prompt contract
- `examples/` — 설정 예시와 bootstrap 예시
- `docs/` — 아키텍처/구현/agent 문서

### 기여 원칙

#### 1. 상태 머신을 먼저 존중해 주세요

- source of truth는 파일시스템 상태 + `metadata.json`
- 허용된 상태 전이만 가능
- 상태 전이는 lock 하에서만 수행
- task 디렉토리와 workspace는 분리
- review 통과 전에는 target repo에 patch를 적용하지 않음
- 최종 commit은 `human-verifying -> done`에서만 수행

#### 2. 작은 변경을 선호합니다

- 먼저 문제를 재현
- 영향을 받는 상태/worker/API를 확인
- 최소 수정 적용
- 관련 테스트 보강
- UI 변경이면 실제 브라우저에서 확인

#### 3. 구현보다 검증이 중요합니다

- 관련 테스트 통과
- 상태 전이 규칙 유지
- metadata/lock/history 보존
- workspace / integration / verification 흐름 충돌 없음

### 코드 스타일 가이드

- Python 3.11+
- Pydantic v2 기반 모델
- 작은 함수와 테스트 가능한 구조
- subprocess wrapper 분리
- atomic write 사용
- 의미 있는 도메인 예외 사용
- 로그에 민감 정보 금지

UI 원칙:

- 단일 HTML 페이지 + vanilla JS
- SSE 기반 실시간 반영
- 불필요한 프레임워크 의존 최소화

### 테스트 범위

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

변경한 영역의 관련 테스트는 직접 돌려 주세요.

### Pull Request 작성 팁

PR에는 아래 내용이 있으면 좋습니다.

- 무엇을 바꿨는지
- 왜 필요한지
- 어떻게 검증했는지
- 상태 머신/verification 규칙에 어떤 영향이 있는지
- 관련 이슈 또는 논의 링크

권장:

- 한 PR은 하나의 주제에 집중
- UI 변경이면 스크린샷 첨부
- workflow 변경이면 재현 시나리오 포함
- 문서 PR이면 어떤 독자를 위한 문서인지 설명

### 특히 조심할 영역

- `transitions.py`
- `locks.py`
- `metadata_store.py`
- `workspace_manager.py`
- `integration_manager.py`
- `workers/*.py`
- `api/templates/index.html`

이 영역은 UI, worker, filesystem state가 강하게 연결되어 있습니다.

### 질문 또는 논의

버그, 설계 질문, 개선 제안은 이슈로 남겨 주세요. 가능하면 아래 정보를 함께 적어 주세요.

- 기대 동작
- 실제 동작
- 재현 방법
- 관련 상태
- 관련 파일 또는 스크린샷

기여해 주셔서 감사합니다.
