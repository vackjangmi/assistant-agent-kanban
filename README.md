# Assistant Agent Kanban

`Assistant Agent Kanban`은 파일시스템 상태를 기반으로 동작하는 AI 작업 오케스트레이션 서비스입니다. OpenCode 또는 Codex CLI 기반의 Planner, Implementer, Reviewer, Committer 역할을 자동으로 실행하고, 사람 승인 단계가 필요한 구간은 명시적으로 남겨 둡니다.

이 프로젝트는 다음과 같은 상황을 위해 만들어졌습니다.

- 요청서(`REQUEST.md`)에서 시작해 계획, 구현, 리뷰, 인간 검증까지 이어지는 작업 흐름을 추적하고 싶을 때
- AI가 실제 코드를 수정하더라도 작업 디렉토리와 실제 코드 workspace를 분리해 안전하게 운영하고 싶을 때
- 브라우저에서 칸반 보드와 작업 상세, 로그, 문서 산출물을 실시간으로 보고 싶을 때

현재 버전은 공개 가능한 MVP에 가깝습니다. 핵심 워크플로와 대시보드, 테스트는 갖추고 있지만, 운영 환경 하드닝이나 인증 같은 production 기능까지 모두 포함한 상태는 아닙니다.

## 왜 시작했는가

이 프로젝트는 단순히 "AI로 코드를 고쳐보자"에서 출발한 도구가 아닙니다. 개인 사이드 프로젝트를 진행하고 실제로 앱을 만들고 출시하는 과정에서, AI Agent 기반 개발을 여러 방식으로 실험해 본 경험이 출발점이었습니다.

그 과정에서 터미널 중심의 자율 주행 흐름이나 랄프 스타일의 루프는 분명 강력했지만, 실제 업무처럼 "요구사항을 정리하고, 계획을 검토하고, 승인한 뒤 다음 단계로 넘기고, 마지막에는 사람이 최종 책임을 갖고 확인하는 흐름"을 담기에는 아쉬움이 있었습니다. 지금 어떤 작업이 어떤 상태에 있고, AI가 무엇을 했고, 사람이 어디서 개입해야 하는지를 한눈에 보기 어려운 점도 컸습니다.

그래서 개인 프로젝트에서 부딪혀 본 경험을 바탕으로, 실제 업무에서 익숙하게 사용하던 스프린트/칸반 방식을 OpenCode 기반 Agent 개발 흐름과 결합해 보고 싶었습니다. 그 결과물이 바로 `Assistant Agent Kanban`입니다.

이 프로젝트는 작업의 모든 단계를 Markdown 파일로 남기고, 그 파일을 기준으로 Agent와 사람이 함께 일하도록 설계되어 있습니다. 사람은 요구사항을 작성하고 계획을 검토/수정하며, 구현 에이전트와 리뷰 에이전트는 반복적으로 작업을 주고받고, 마지막에는 사람이 브랜치, diff, 코멘트 기반으로 최종 판단을 내립니다. 그리고 완료된 작업은 브랜치 단위 회고까지 이어질 수 있도록 구성했습니다.

결국 이 프로젝트가 지향하는 것은 "사람이 하던 업무 프로세스를 AI 협업 환경에서도 유효한 형태로 다시 세우는 것"입니다. 익숙한 스크럼/칸반 흐름을 버리는 대신, 계획 승인, 반복 구현/리뷰, 인간 검증, 회고까지 포함한 구조를 AI와 함께 사용할 수 있는 도구로 만들고 싶었습니다.

아직은 실험 단계에 가깝지만, 몇 차례 실제 테스트를 통해 충분히 가능성이 있다고 느꼈고, 더 다듬으면 다른 사람도 사용할 수 있는 도구로 발전시킬 수 있다고 보고 있습니다.

## 무엇을 목표로 하는가

- 작업의 모든 단계가 파일과 상태로 명확히 남는 개발 흐름
- AI와 사람이 서로 역할을 나눠 협업할 수 있는 스크럼/칸반 기반 프로세스
- "플랜 승인 -> 구현/리뷰 반복 -> 인간 최종 검증"이 자연스럽게 이어지는 작업 시스템
- 단발성 코드 생성이 아니라, 작업 히스토리와 회고까지 포함한 지속 가능한 개발 도구
- 개인 실험을 넘어 팀이나 다른 사용자도 적용할 수 있는 공개형 오픈소스 툴

## 핵심 특징

- 파일/디렉토리 기반 상태 머신 + `metadata.json`을 source of truth로 사용
- Planner / Implementer / Reviewer / Committer 역할을 개별 worker로 분리
- `clone-overlay` 전략 기반의 격리 workspace 생성
- 리뷰 통과 후에만 human verification 시작 가능
- human verification 시작 시점에만 target repository에 patch 적용
- 최종 commit은 `human-verifying -> done`에서만 생성
- FastAPI + SSE 기반 단일 페이지 대시보드 제공
- Markdown 산출물(`PLAN.md`, `WORK-001.md`, `REVIEW-001.md`)과 JSON 원본 결과를 함께 보관
- CLI와 웹 UI 모두 지원

## 어떤 문제를 푸는가

이 프로젝트는 “AI가 알아서 코드를 고친다”보다 “AI 작업 흐름을 사람이 추적·검토·승인할 수 있게 만든다”에 더 가깝습니다.

핵심 설계 원칙은 다음과 같습니다.

- 워크플로 상태는 외부 오케스트레이터가 관리한다.
- task 디렉토리와 실제 코드 작업 workspace는 분리한다.
- 허용된 상태 전이만 통과시킨다.
- AI review를 통과한 뒤에만 사람이 실제 target repo에서 검증한다.
- 런타임 엔진(OpenCode/Codex)과 워크플로 엔진(Python/FastAPI)을 분리한다.

## 빠른 시작

### 1. 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

또는 부트스트랩 스크립트를 사용할 수 있습니다.

```bash
./init.sh
```

`./init.sh`는 다음 작업을 수행합니다.

- `.venv`가 없으면 생성
- `pip install -e .[dev]` 실행
- 설정 파일이 없으면 `examples/config.yaml`을 기준으로 생성
- 기본 칸반 루트와 런타임 디렉토리를 초기화

### 2. 앱 실행

가장 간단한 실행 방법은 다음과 같습니다.

```bash
./run.sh
```

직접 CLI를 사용하려면(`assistant-agent-kanban`는 현재 패키지의 실제 실행 명령입니다):

```bash
assistant-agent-kanban serve --config ./examples/config.yaml --host 127.0.0.1 --port 8000
```

또는 Uvicorn으로 바로 띄울 수 있습니다.

```bash
uvicorn assistant_agent_kanban.api.main:app
```

자동 리로드가 필요하면:

```bash
assistant-agent-kanban serve --reload --config ./examples/config.yaml
```

앱을 실행한 뒤 브라우저에서 `http://127.0.0.1:8000/`으로 접속하면 대시보드를 볼 수 있습니다.

### 3. 테스트 실행

```bash
pytest -q
```

## 가장 짧은 사용 흐름

1. `REQUEST.md`를 만든다.
2. Planner가 자동으로 `PLAN.md`를 생성한다.
3. 사람이 계획을 확인하고 task를 `todos`로 이동한다.
4. Implementer가 격리된 workspace에서 구현하고 `WORK-{n}.md`를 남긴다.
5. Reviewer가 검토하고 `REVIEW-{n}.md`를 남긴다.
6. 리뷰가 통과되면 사람이 verification을 시작한다.
7. target repository에서 검증 후 승인하면 최종 commit이 생성된다.

## 아키텍처 개요

```text
repo-root/
├─ AGENTS.md
├─ .opencode/
│  └─ agents/
├─ .kanban-agent/
│  ├─ requests/
│  ├─ planning/
│  ├─ waiting-check-plans/
│  ├─ todos/
│  ├─ implementing/
│  ├─ waiting-reviews/
│  ├─ reviewing/
│  ├─ completed-reviews/
│  ├─ human-verifying/
│  ├─ done/
│  └─ _runtime/
│     ├─ locks/
│     ├─ workspaces/
│     ├─ runs/
│     └─ events/
└─ src/assistant_agent_kanban/
```

구성 요소는 크게 네 층으로 나뉩니다.

- `task directory`: 요청서, 계획서, 구현/리뷰 결과, `metadata.json` 같은 상태 문서 저장
- `workspace`: 실제 코드 수정이 일어나는 격리 작업공간
- `runtime supervisor`: 파일시스템 스캔, 상태 전이, worker 실행, recovery 담당
- `FastAPI + SSE`: 보드, 작업 상세, 로그, 실시간 업데이트 제공

## 상태 머신

이 프로젝트는 아래 상태를 사용합니다.

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

주요 전이는 다음과 같습니다.

```text
requests -> planning
planning -> waiting-check-plans
waiting-check-plans -> todos        (manual)
todos -> implementing
implementing -> waiting-reviews
implementing -> todos
waiting-reviews -> reviewing
reviewing -> completed-reviews
reviewing -> todos
completed-reviews -> human-verifying (manual)
human-verifying -> todos
human-verifying -> done
```

중요한 규칙:

- 허용되지 않은 전이는 코드에서 차단합니다.
- `completed-reviews`는 AI review가 통과한 상태이지, target repo 반영 완료 상태가 아닙니다.
- target repo patch apply는 `completed-reviews -> human-verifying`에서만 수행합니다.
- 최종 commit은 `human-verifying -> done`에서만 수행합니다.

## Worker 구성

### PlanningWorker

- 입력 상태: `requests`
- 역할: `REQUEST.md`를 읽고 `PLAN.md`를 생성
- 결과: `waiting-check-plans`로 이동

### ImplementerWorker

- 입력 상태: `todos`
- 역할: workspace를 만들고 실제 구현 실행
- 결과: 변경이 있으면 `waiting-reviews`, 없거나 실패하면 `todos`

### ReviewerWorker

- 입력 상태: `waiting-reviews`
- 역할: 구현 결과 검토 및 verdict 생성
- 결과: `PASS`면 `completed-reviews`, `NEEDS_CHANGES`면 `todos`

### CommitWorker / Human Verification

- 입력 상태: `completed-reviews`, `human-verifying`
- 역할: patch 적용, 검증, 최종 commit
- 결과: reject면 `todos`, approve면 `done`

## Workspace 전략

기본 전략은 `clone-overlay`입니다.

- workspace는 task 디렉토리 내부가 아니라 `_runtime/workspaces/{task_id}` 아래에 생성됩니다.
- 기본 repository는 local clone으로 준비합니다.
- 필요한 ignored/untracked 파일은 overlay copy 또는 symlink로 보강합니다.
- 사람 검증용 target repo와 AI 구현용 workspace를 분리해 오염을 방지합니다.

이 전략은 순수 `git worktree`보다 운영 예측 가능성이 높고, 로컬 개발 환경 파일을 선택적으로 보강할 수 있다는 장점이 있습니다.

## Task 산출물

작업 디렉토리에는 Markdown과 JSON이 함께 저장됩니다.

- `REQUEST.md`: 최초 요청서
- `PLAN.md`: Planner 결과
- `WORK-001.md`: 구현 iteration 결과
- `REVIEW-001.md`: 리뷰 iteration 결과
- `COMMIT.md`: 최종 commit 정보
- `PLAN.json`, `WORK-001.json`, `REVIEW-001.json`: 원본 machine-readable 결과
- `metadata.json`: 상태, revision, lease, history, errors 등 메타데이터

중요:

- Markdown은 사람이 읽고 편집하는 working copy입니다.
- JSON은 worker가 남긴 원본 결과입니다.
- Markdown 수정이 JSON으로 역동기화되지는 않습니다.

## CLI 사용 예시

### 요청 생성

```bash
assistant-agent-kanban request "로그인 플로우 리팩터링" \
  --target-repo /path/to/target-project \
  --kanban-root ./.kanban-agent \
  --base-branch main
```

이 명령은 `requests/` 아래에 새 요청 task를 만들고, target repo와 base branch 정보를 함께 기록합니다.

### 로그 확인

```bash
assistant-agent-kanban logs TASK-0001 --kanban-root ./.kanban-agent
```

### 앱 실행

```bash
assistant-agent-kanban serve --config ./config.local.yaml --host 0.0.0.0 --port 8000
```

## 웹 UI에서 할 수 있는 일

- 칸반 보드 보기
- 상태별 task 카드 확인
- task 상세 팝업에서 metadata/로그/문서 확인
- `REQUEST.md`, `PLAN.md`, 구현/리뷰 문서 열람
- 특정 상태에서 `PLAN.md` 편집 및 승인
- human verification 시작 / reject / approve
- 새 요청 생성

대시보드는 단일 HTML + vanilla JS + SSE로 구성되어 있습니다.

## API 개요

주요 엔드포인트는 다음과 같습니다.

- `GET /healthz`
- `GET /api/board`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/logs`
- `GET /api/events`
- `POST /api/tasks/{task_id}/approve-plan`
- `POST /api/tasks/{task_id}/start-verification`
- `POST /api/tasks/{task_id}/reject-verification`
- `POST /api/tasks/{task_id}/approve-verification`
- `GET /`

또한 현재 구현에는 요청 생성, human review note, 설정, retrospective 등 UI 지원용 엔드포인트도 포함되어 있습니다. 공개 README에서는 우선 핵심 흐름 중심으로 보면 충분합니다.

## 설정

기본 설정 파일은 `examples/config.yaml`입니다.

중요한 항목:

- `kanban_root`: 칸반 상태 루트 디렉토리
- `repo_root`: task가 별도로 target repo를 지정하지 않을 때의 기본 repository
- `base_branch`: workspace 생성 기준 브랜치
- `opencode.*`: OpenCode 실행 바이너리, agent 이름, 모델, timeout
- `codex.*`: Codex CLI 사용 시 역할별 모델/토큰 예산 설정
- `workspace.*`: clone-overlay workspace 루트와 overlay 정책
- `locks.*`: heartbeat, stale timeout 설정
- `runtime.*`: 자동 dispatch, UI 언어, coding assistant 종류, worker agent count
- `repo_discovery.*`: UI에서 target repo 후보를 탐색할 루트와 깊이

예시:

```yaml
kanban_root: ./.kanban-agent
repo_root: .
base_branch: main

opencode:
  binary: opencode
  planner_agent: fs-kanban-planner
  implementer_agent: fs-kanban-implementer
  reviewer_agent: fs-kanban-reviewer
  commit_agent: fs-kanban-committer

workspace:
  strategy: clone-overlay
  root: ./.kanban-agent/_runtime/workspaces

runtime:
  auto_dispatch: true
  coding_assistant: opencode
```

## 저장소 구조

- `src/assistant_agent_kanban/`: 도메인 모델, scanner, locks, transitions, runtime, workers, API
- `src/assistant_agent_kanban/workers/`: planner, implementer, reviewer, committer
- `src/assistant_agent_kanban/api/`: FastAPI app, routes, SSE, UI
- `tests/`: scanner, locks, transitions, workers, recovery, API 테스트
- `.opencode/agents/`: 역할별 prompt contract
- `examples/config.yaml`: 예시 설정
- `examples/bootstrap/`: kanban root 부트스트랩 예시
- `docs/`: 설계 검토, 구현 계획, 에이전트 작업 브리프

## 최소 Python 사용 예시

```python
from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.assistant_factory import build_role_adapters
from assistant_agent_kanban.config import load_config

config = load_config("examples/config.yaml")
planner, implementer, reviewer, committer, branch_summary = build_role_adapters(config)
app = create_app(config, planner, implementer, reviewer, committer, branch_summary)
```

이후 Uvicorn으로 서빙할 수 있습니다.

```bash
uvicorn mymodule:app
```

## 테스트 전략

현재 테스트는 다음을 포함합니다.

- 상태 전이 검증
- metadata atomic write
- lock / heartbeat 동작
- scanner bootstrap
- planner / implementer / reviewer / committer worker 흐름
- recovery
- FastAPI API 테스트

테스트는 temporary kanban root, temporary git repository, fake adapter를 사용해 비교적 결정적으로 동작하도록 설계되어 있습니다.

## 공개용으로 알아둘 점

- 이 프로젝트는 “AI 자동화”보다 “검토 가능한 워크플로”에 무게를 둡니다.
- 사람이 개입하는 승인 단계는 의도적으로 제거하지 않았습니다.
- target repo는 verification 시점에 clean 상태여야 합니다.
- task 디렉토리 안에 전체 workspace를 두지 않습니다.
- OpenCode/oh-my-opencode 내부 상태 파일을 source of truth로 사용하지 않습니다.
- 현재 구현은 production hardening보다 테스트 가능한 MVP 범위를 우선합니다.

## 기여 안내

이 저장소는 공개 저장소 기준으로 `fork 후 Pull Request` 방식의 기여를 전제로 합니다.

- 저장소 본체에 직접 코드 수정 권한을 주는 방식으로 운영하지 않습니다.
- contributor용 브랜치를 원본 저장소에 직접 만드는 흐름을 기본으로 사용하지 않습니다.
- 외부 기여는 fork에서 작업한 뒤 Pull Request로 제안해 주세요.

기여 전 아래 문서를 먼저 읽는 것을 권장합니다.

- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`

특히 다음 원칙을 지켜 주세요.

- 상태 전이는 항상 lock 하에서 수행
- 허용되지 않은 상태 전이 금지
- task 디렉토리와 workspace 분리 유지
- review 이전에 target repo를 건드리지 않기
- 테스트 가능한 작은 단위 변경 선호

자세한 기여 방법은 `CONTRIBUTING.md`를 참고해 주세요.

## 관련 파일

- `README.md`
- `examples/config.yaml`
- `examples/bootstrap/README.md`
- `src/assistant_agent_kanban/main.py`
- `src/assistant_agent_kanban/api/app.py`
- `src/assistant_agent_kanban/api/main.py`
- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`

이 저장소에는 공개 협업을 위한 기본 문서로 `CONTRIBUTING.md`, `LICENSE`, `SECURITY.md`, `CODE_OF_CONDUCT.md`가 포함되어 있습니다.
