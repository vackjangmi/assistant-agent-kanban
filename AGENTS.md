# Assistant Agent Kanban Project Rules

이 프로젝트는 **파일/디렉토리 기반 칸반 + OpenCode orchestration + FastAPI SSE dashboard** 를 구현한다.

## 먼저 읽을 문서

반드시 아래 문서를 먼저 읽고 작업한다.

- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`

## 목표

아래 요구를 만족하는 Python 시스템을 구현한다.

- 상태 디렉토리 기반 workflow
- `opencode run` 으로 각 단계 수행
- planner / implementer / reviewer worker
- isolated workspace (`clone-overlay`)
- metadata / lock / recovery
- FastAPI + SSE 칸반 대시보드

## 핵심 제약

1. **workflow state의 진실 소스는 디렉토리 상태 + `metadata.json`** 이다.
2. `oh-my-opencode` 내부 state 파일에 의존하지 않는다.
3. **task 디렉토리와 코드 workspace는 분리**한다.
4. 구현은 workspace에서만 한다.
5. review 통과 후에만 human verification 을 시작할 수 있다.
6. target repo patch apply 는 `completed-reviews -> human-verifying` 에서만 수행한다.
7. 최종 commit은 `human-verifying -> done` 에서만 수행한다.
8. lock 없이 task state를 바꾸지 않는다.

## 상태 디렉토리

반드시 아래 상태를 사용한다.

- `requests`
- `planning`
- `waiting-check-plans`
- `todos`
- `implementing`
- `waiting-reviews`
- `reviewing`
- `completed-reviews`
- `human-verifying`
- `done`

## 허용 전이

- `requests -> planning`
- `planning -> waiting-check-plans`
- `waiting-check-plans -> todos`
- `todos -> implementing`
- `implementing -> todos`
- `implementing -> waiting-reviews`
- `waiting-reviews -> reviewing`
- `reviewing -> todos`
- `reviewing -> completed-reviews`
- `completed-reviews -> human-verifying`
- `human-verifying -> todos`
- `human-verifying -> done`

허용되지 않은 전이는 코드로 막아라.

## metadata 규칙

모든 task는 `metadata.json` 을 가진다.

최소 포함 필드:
- `task_id`
- `title`
- `slug`
- `state`
- `created_at`
- `updated_at`
- `plan.revision`
- `implementation.iteration`
- `review.iteration`
- `integration`
- `commit`
- `lease`
- `history`
- `errors`

## workspace 규칙

기본 전략은 **`clone-overlay`** 다.

- local clone 사용
- overlay copy / symlink manifest 사용
- 순수 worktree only 전략은 기본값으로 채택하지 않는다
- workspace는 반드시 `_runtime/workspaces/{task_id}` 아래에 둔다

## OpenCode 사용 규칙

- `opencode serve` + `opencode run --attach` 사용 가능하도록 설계
- `--format json` 결과를 저장
- planner/reviewer는 **stdout markdown 반환형**
- implementer는 workspace에서 코드 수정
- commit message는 AI 생성 또는 규칙 기반 생성 가능

## 구현 우선순위

1. filesystem scanner + metadata + lock
2. PlanningWorker
3. ImplementerWorker
4. ReviewerWorker
5. IntegrationManager
6. CommitWorker
7. FastAPI + SSE dashboard
8. recovery + tests

## 코드 품질 규칙

- Python 3.11+ 타입 힌트 사용
- Pydantic v2 모델 사용
- 함수는 작고 테스트 가능하게 유지
- subprocess 래퍼는 반드시 분리
- 파일 쓰기는 atomic write 사용
- 예외는 의미 있는 도메인 예외로 변환
- 로그에 비밀값을 남기지 말 것

## 테스트 규칙

최소 아래 테스트를 작성한다.

- scanner
- transitions
- locks
- planner worker
- implementer worker
- reviewer worker
- recovery
- board API

## UI 규칙

UI는 처음에는 단순해야 한다.

- 단일 HTML 페이지
- vanilla JS
- `/api/board` 초기 fetch
- `/api/events` SSE 실시간 반영

## 작업 방식

- 각 phase마다 작은 단위로 구현
- 먼저 domain layer
- 다음 orchestration layer
- 마지막에 API/UI
- 구현이 끝날 때마다 테스트 추가

## 산출물

최종적으로 아래가 있어야 한다.

- `src/fs_kanban_agent/...`
- `tests/...`
- `README.md`
- 예시 설정 파일
- 예시 state root bootstrap
- FastAPI 앱 엔트리포인트
