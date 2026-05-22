# Assistant Agent Kanban

## English

### Overview

`Assistant Agent Kanban` is a filesystem-backed AI workflow orchestration service. It connects Planner, Implementer, Reviewer, and Committer roles built on top of OpenCode, Codex, Claude, or Gemini CLIs, while explicitly preserving human approval gates where they matter.

The project started from hands-on experiments with AI agent-based development in personal side projects. Terminal-first autonomous loops and Ralph-style iteration were powerful, but they did not map cleanly onto the kind of workflow used in real work: writing requirements, reviewing plans, approving stages, iterating on implementation, and performing final human validation.

That led to the idea behind `Assistant Agent Kanban`: combine an agent-based workflow with a familiar sprint/kanban process, backed by files as durable artifacts and a web UI for visibility.

The current version is best described as a public MVP. The core workflow, dashboard, multi-runtime support, Slack integration, and tests are in place, but it is not yet a fully hardened production system.

### Demo

Full video: [Watch on YouTube](https://youtu.be/gpdcVGiLxaQ)

**1. Plan**  
![Plan Demo](./docs/gifs/assistant-agent-kanban-plan-demo.gif)

**2. Implement & Review**  
![Implement and Review Demo](./docs/gifs/assistant-agent-kanban-implement&review-demo.gif)

**3. Human Verify**  
![Human Verify Demo](./docs/gifs/assistant-agent-kanban-human-verify-demo.gif)

**4. Retry Implement & Review**  
![Retry Implement and Review Demo](./docs/gifs/assistant-agent-kanban-retry-implement&review-demo.gif)

**5. Human Verify & Complete**  
![Complete Demo](./docs/gifs/assistant-agent-kanban-complete-demo.gif)

### Core Goals

- Preserve every stage of work as files and workflow state
- Support AI/human collaboration through a scrum/kanban-style process
- Keep `plan approval -> implement/review loop -> final human verification` explicit
- Go beyond one-off code generation toward a durable development workflow with history and retrospectives
- Evolve from a personal experiment into a reusable open-source tool

### Key Features

- Filesystem-backed state machine with `metadata.json` as the source of truth
- Separate Planner / PlanApproval / Implementer / Reviewer / Committer workers
- Multi-runtime support: OpenCode, Codex, Claude, Gemini — selectable per role
- Per-role backend routing (e.g., `planner: claude`, `implementer: codex`, `reviewer: claude`)
- Per-role model and session token budget configuration
- Isolated `clone-overlay` workspaces
- Automatic plan approval stage with fallback to manual review
- Assistant-first request drafting flow (in-app or from Slack)
- Human verification starts only after review passes
- Target repo patch apply happens only during `completed-reviews -> human-verifying`
- Final commit is created only during `human-verifying -> done`
- Human QA checklist, no-verification-note, and inline-comment gating before final approval
- Reusable target repo summary artifacts and strict summary-driven final commits
- Optional Slack integration: notifications, action buttons, modal flows, file uploads, thread-based request drafting and review loops
- Single-page FastAPI + SSE dashboard with light/dark themes and KO/EN localization
- Retrospective view per task
- Markdown artifacts stored alongside raw JSON outputs
- Both CLI and web UI are supported

### What Problem It Solves

This project is less about “letting AI fix code by itself” and more about making AI work visible, reviewable, and governable by humans.

Its core design principles are:

- workflow state is owned by an external orchestrator
- task directories and real code workspaces remain separate
- only allowed transitions are permitted
- humans verify the result in the real target repo only after AI review passes
- runtime engines (OpenCode/Codex/Claude/Gemini) stay separate from the workflow engine (Python/FastAPI)

### Quick Start

#### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Or use:

```bash
./init.sh
```

`./init.sh` will:

- create `.venv`
- run `pip install -e .[dev]`
- initialize a config file when missing
- bootstrap the kanban root and runtime directories

You will additionally need at least one supported CLI installed and authenticated on your machine: `opencode`, `codex`, `claude`, or `gemini`.

#### 2. Run the App

Simplest path:

```bash
./run.sh
```

Direct CLI usage:

```bash
assistant-agent-kanban serve --config ./config.yaml --host 127.0.0.1 --port 8000
```

Direct Uvicorn usage:

```bash
uvicorn assistant_agent_kanban.api.main:app
```

Then open `http://127.0.0.1:8000/` in your browser.

#### 3. Run Tests

```bash
pytest -q
```

### Shortest Usage Flow

1. Create a request (via web UI, CLI, or Slack draft) — produces `REQUEST.md`.
2. The Planner generates `PLAN.md`.
3. The PlanApproval worker (or a human) reviews the plan and moves the task to `todos`.
4. The Implementer works in an isolated workspace and produces `WORK-{n}.md`.
5. The Reviewer produces `REVIEW-{n}.md` and a QA checklist for the human.
6. Once review passes, a human starts verification.
7. After validation in the target repo, approval creates the final commit and summary artifacts, either on a new final branch or on the target branch.

### Architecture Overview

```text
repo-root/
├─ AGENTS.md
├─ .opencode/
│  └─ agents/
│     ├─ fs-kanban-planner.md
│     ├─ fs-kanban-plan-approval.md
│     ├─ fs-kanban-request-draft.md
│     ├─ fs-kanban-implementer.md
│     ├─ fs-kanban-reviewer.md
│     └─ fs-kanban-committer.md
├─ .kanban-agent/
│  ├─ requests/
│  ├─ planning/
│  ├─ plan-approving/
│  ├─ waiting-check-plans/
│  ├─ todos/
│  ├─ implementing/
│  ├─ waiting-reviews/
│  ├─ reviewing/
│  ├─ completed-reviews/
│  ├─ human-verifying/
│  ├─ done/
│  ├─ retrospectives/
│  └─ _runtime/
│     ├─ locks/
│     ├─ workspaces/
│     ├─ runs/
│     ├─ archive-runs/
│     ├─ events/
│     ├─ request-drafts/
│     ├─ request-uploads/
│     └─ board-cache/
└─ src/assistant_agent_kanban/
```

The system has four main layers.

- `task directory`: request/plan/work/review/human-verification docs and `metadata.json`
- `workspace`: isolated code-editing area
- `runtime supervisor`: scanning, transitions, workers, recovery, and optional Slack runtime
- `FastAPI + SSE`: board, task detail, logs, settings, retrospectives, and live updates

### State Machine

States:

- `requests`
- `planning`
- `plan-approving`
- `waiting-check-plans`
- `todos`
- `implementing`
- `waiting-reviews`
- `reviewing`
- `completed-reviews`
- `human-verifying`
- `done`

Main transitions:

```text
requests -> planning
planning -> plan-approving
planning -> waiting-check-plans
planning -> requests
plan-approving -> waiting-check-plans
plan-approving -> todos
waiting-check-plans -> todos
todos -> implementing
implementing -> waiting-reviews
implementing -> todos
waiting-reviews -> reviewing
reviewing -> completed-reviews
reviewing -> waiting-reviews
reviewing -> todos
completed-reviews -> human-verifying
completed-reviews -> todos
human-verifying -> todos
human-verifying -> done
```

Rules:

- invalid transitions must be blocked in code
- `plan-approving` may auto-promote directly to `todos` when the plan-approval agent approves; otherwise it falls through to `waiting-check-plans` for a human
- `completed-reviews` does not mean the target repo is already updated
- patch apply happens only during `completed-reviews -> human-verifying`
- final commit happens only during `human-verifying -> done`

### Worker Roles

- `PlanningWorker` — reads `REQUEST.md` and creates `PLAN.md`
- `PlanApprovalWorker` — evaluates `PLAN.md` against the request and either auto-approves to `todos` or routes to `waiting-check-plans` for a human
- `RequestDraftAgent` — helps a user iterate on a request before submission, from the UI or from a Slack thread
- `ImplementerWorker` — edits code in a workspace and records `WORK-{n}.md`
- `ReviewerWorker` — records review results in `REVIEW-{n}.md`, surfaces endpoint locations, and emits a QA checklist for the human
- `CommitWorker / Human Verification` — handles verification, target repo patch apply, summary artifacts, and the final commit

### Workspace Strategy

The default strategy is `clone-overlay`.

- workspace roots live under `_runtime/workspaces/{task_id}`
- the editable repository checkout lives under `_runtime/workspaces/{task_id}/repo`
- they start from a local clone
- needed ignored/untracked files can be added through overlay copy or symlink
- the target repo is separated from the implementation workspace to reduce contamination
- Codex runs in workspace-write mode; OpenCode/Claude/Gemini treat the target repo as read-only during implementation

### Task Artifacts

- `REQUEST.md`
- `PLAN.md`
- `WORK-{n}.md`
- `REVIEW-{n}.md`
- `HUMAN-QA-{n}.md` — reviewer-provided human QA checklist
- `REVIEWER-QA-{n}.md` — optional human/reviewer Q&A thread
- `HUMAN-VERIFY-{n}.md` — human verification notes and verdict
- `HUMAN-VERIFY-{n}.comments.json` — inline human verification comments
- `COMMIT.md`
- `*.json` raw outputs (per worker run)
- `metadata.json`

Markdown is the human-readable working artifact, while JSON is the raw worker output.
The semantic target repo summary is written during final approval under `target_repo_docs_root/YYYY/MM/DD/{task_id}-{branch-summary}-summary.md`.

### CLI Examples

#### Create a Request

```bash
assistant-agent-kanban request "Refactor login flow" \
  --target-repo /path/to/target-project \
  --kanban-root ./.kanban-agent \
  --base-branch main
```

#### Show Logs

```bash
assistant-agent-kanban logs TASK-0001 --kanban-root ./.kanban-agent
```

#### Run the App

```bash
assistant-agent-kanban serve --config ./config.local.yaml --host 0.0.0.0 --port 8000
```

### Web UI Capabilities

- view the kanban board (with phase tabs and a final/done board view)
- inspect tasks by state, including request drafts
- open task detail modal for metadata, logs, artifacts, and token usage summaries
- read `REQUEST.md`, `PLAN.md`, work/review/human-QA/human-verification documents
- edit and approve `PLAN.md` in supported states
- start / reject / approve human verification (approval is gated on verification apply success, completed/skipped required QA items, no human verification note, and no unresolved inline comments)
- resume planner / implementer / reviewer with explicit choice modals
- delete tasks, including tasks whose target repo is no longer reachable
- create new requests, including assistant-drafted requests
- open the in-app settings modal: assistant selection, per-role backend routing, per-role model and token budget, theme, language
- open the retrospective view per task

### Slack Integration (Optional)

When enabled, the Slack runtime provides:

- thread notifications for state transitions and verification milestones
- action buttons to start verification, approve, request rework, or resume the review loop
- modal flows for review-loop requests and assistant-first request drafting
- markdown artifact uploads to threads (review, plan, summary, completion)
- channel matching by display metadata and pending channel activation tested before adoption

Slack is configured under the `slack:` section of the config file and requires a bot token and (for socket mode) an app token.

### Configuration

By default the app loads `./config.yaml` and overlays `./config.local.yaml` when present. `examples/config.yaml` is a copyable template, not the recommended path to run directly.

Important keys:

- `kanban_root`
- `repo_root`
- `base_branch`
- `target_repo_docs_root`
- `opencode.*` — per-role agent name, model, and session token budget
- `codex.*` — per-role model and session token budget
- `claude.*` — per-role model and session token budget
- `gemini.*` — per-role model and session token budget
- `workspace.*`
- `locks.*`
- `runtime.*` — `coding_assistant`, `role_backends`, `language`, `theme`, agent counts, auto-dispatch
- `repo_discovery.*`
- `slack.*` (optional)

Per-role backend routing example:

```yaml
runtime:
  coding_assistant: opencode
  role_backends:
    planner: claude
    request_draft: opencode
    plan_approval: opencode
    implementer: codex
    reviewer: claude
    commit: opencode
```

### Repository Structure

- `src/assistant_agent_kanban/` — domain, runtime, workers, services, adapters, and API
  - `workers/` — planner, plan-approval, implementer, reviewer, committer
  - `services/` — task, board, human verification, retrospective, plan-approval learning, task deletion
  - `api/` — FastAPI app, routes, SSE, templates (HTML, CSS, modular JS)
  - `*_adapter.py` — OpenCode, Codex, Claude, Gemini adapters
  - `slack_*.py` — Slack runtime, notifications, channel matching, settings tests
- `tests/` — workflow, service, adapter, and API tests
- `.opencode/agents/` — role prompt contracts
- `examples/` — config and bootstrap examples
- `docs/` — architecture, implementation map, and agent brief

Public users can start with `README.md`. Contributors or maintainers should read `AGENTS.md` and `docs/*` as well.

### Python Usage Example

```python
from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.assistant_factory import build_role_adapters
from assistant_agent_kanban.config import load_config

config = load_config("examples/config.yaml")
planner, implementer, reviewer, committer, branch_summary = build_role_adapters(config)
app = create_app(config, planner, implementer, reviewer, committer, branch_summary)
```

### Testing And Open-Source Notes

- Run the full test suite with `pytest -q`
- This project emphasizes a reviewable workflow more than raw AI automation
- Human approval stages are intentional and should not be removed
- The target repo should be clean when verification begins
- The full workspace must not live inside the task directory
- Internal CLI state files (OpenCode/Codex/Claude/Gemini) are not the source of truth

### Contributing

This repository follows a `fork -> branch -> PR` contribution model.

- do not push directly to the main repository
- do not assume contributor branches are created in the upstream repository
- make changes in your fork and submit a Pull Request

See `CONTRIBUTING.md` for details.

### Related Documents

- `AGENTS.md`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `LICENSE`
- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`

---

## 한국어

### 소개

`Assistant Agent Kanban`은 파일시스템 상태를 기반으로 동작하는 AI 작업 오케스트레이션 서비스입니다. OpenCode, Codex, Claude, Gemini CLI 위에서 Planner, Implementer, Reviewer, Committer 역할을 연결하고, 사람 승인 단계가 필요한 구간은 명시적으로 유지합니다.

이 프로젝트는 개인 프로젝트에서 AI Agent 기반 개발을 여러 방식으로 실험한 경험에서 출발했습니다. 터미널 중심의 자율 주행 흐름이나 랄프 스타일의 루프는 강력했지만, 실제 업무처럼 요구사항 작성, 계획 검토, 승인, 구현 반복, 인간 최종 검증까지 이어지는 흐름을 한눈에 추적하기는 어려웠습니다.

그래서 실제 업무에서 익숙하게 사용하던 스프린트/칸반 프로세스를 Agent 개발 흐름과 결합해, 파일 기반 기록과 웹 기반 가시성을 갖춘 도구를 만들어 보자는 목표로 `Assistant Agent Kanban`을 만들게 되었습니다.

현재 버전은 공개 가능한 MVP에 가깝습니다. 핵심 워크플로, 대시보드, 멀티 런타임 지원, Slack 연동, 테스트는 갖추고 있지만 production hardening이나 인증까지 모두 포함한 상태는 아닙니다.

### 데모

전체 영상: [Watch on YouTube](https://youtu.be/gpdcVGiLxaQ)

**1. 계획**  
![Plan Demo](./docs/gifs/assistant-agent-kanban-plan-demo.gif)

**2. 구현 및 리뷰**  
![Implement and Review Demo](./docs/gifs/assistant-agent-kanban-implement&review-demo.gif)

**3. 사람 검증**  
![Human Verify Demo](./docs/gifs/assistant-agent-kanban-human-verify-demo.gif)

**4. 재요청 구현 및 리뷰**  
![Retry Implement and Review Demo](./docs/gifs/assistant-agent-kanban-retry-implement&review-demo.gif)

**5. 사람 검증 및 완료**  
![Complete Demo](./docs/gifs/assistant-agent-kanban-complete-demo.gif)

### 핵심 목표

- 작업의 모든 단계를 파일과 상태로 남기는 개발 흐름
- AI와 사람이 역할을 나눠 협업할 수 있는 스크럼/칸반 기반 프로세스
- `플랜 승인 -> 구현/리뷰 반복 -> 인간 최종 검증` 흐름의 명확한 분리
- 단발성 코드 생성이 아니라, 히스토리와 회고까지 포함한 지속 가능한 개발 도구
- 개인 실험을 넘어 다른 사람도 사용할 수 있는 공개형 오픈소스 도구

### 핵심 특징

- 파일/디렉토리 기반 상태 머신 + `metadata.json`을 source of truth로 사용
- Planner / PlanApproval / Implementer / Reviewer / Committer를 개별 worker로 분리
- 멀티 런타임 지원: OpenCode, Codex, Claude, Gemini — 역할별로 선택 가능
- 역할별 백엔드 라우팅 (예: `planner: claude`, `implementer: codex`, `reviewer: claude`)
- 역할별 모델·세션 토큰 budget 설정 지원
- `clone-overlay` 전략 기반의 격리 workspace 생성
- 자동 plan approval 단계 + 실패 시 사람 검토로 fallback
- Assistant 기반 request drafting 흐름 (웹 UI 또는 Slack에서)
- 리뷰 통과 후에만 human verification 시작 가능
- `completed-reviews -> human-verifying` 시점에만 target repo patch 적용
- 최종 commit은 `human-verifying -> done`에서만 생성
- 사람용 QA 체크리스트, 사람 검증 note 없음, inline comment 해소 기반 최종 승인 게이팅
- 재사용 가능한 target repo summary 산출물과 summary 기반 final commit 정책
- 선택형 Slack 연동: 알림, 액션 버튼, modal flow, 파일 업로드, 스레드 기반 request drafting 및 review loop
- FastAPI + SSE 기반 단일 페이지 대시보드 (라이트/다크 테마, KO/EN 지원)
- task별 회고(retrospective) 화면
- Markdown 산출물과 JSON 원본 결과를 함께 보관
- CLI와 웹 UI 모두 지원

### 어떤 문제를 푸는가

이 프로젝트는 “AI가 알아서 코드를 고친다”보다는 “AI 작업 흐름을 사람이 추적·검토·승인할 수 있게 만든다”에 가깝습니다.

핵심 설계 원칙은 다음과 같습니다.

- 워크플로 상태는 외부 오케스트레이터가 관리한다.
- task 디렉토리와 실제 코드 작업 workspace는 분리한다.
- 허용된 상태 전이만 통과시킨다.
- AI review를 통과한 뒤에만 사람이 실제 target repo에서 검증한다.
- 런타임 엔진(OpenCode/Codex/Claude/Gemini)과 워크플로 엔진(Python/FastAPI)을 분리한다.

### 빠른 시작

#### 1. 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

또는:

```bash
./init.sh
```

`./init.sh`는 다음을 수행합니다.

- `.venv` 생성
- `pip install -e .[dev]`
- 설정 파일 초기화
- 기본 칸반 루트와 런타임 디렉토리 bootstrap

추가로 지원되는 CLI 중 최소 하나는 설치·인증되어 있어야 합니다: `opencode`, `codex`, `claude`, `gemini`.

#### 2. 앱 실행

가장 간단한 실행:

```bash
./run.sh
```

CLI로 직접 실행:

```bash
assistant-agent-kanban serve --config ./config.yaml --host 127.0.0.1 --port 8000
```

Uvicorn 실행:

```bash
uvicorn assistant_agent_kanban.api.main:app
```

브라우저에서 `http://127.0.0.1:8000/` 접속.

#### 3. 테스트

```bash
pytest -q
```

### 가장 짧은 사용 흐름

1. 요청을 생성한다 (웹 UI, CLI, 또는 Slack draft) — `REQUEST.md` 생성.
2. Planner가 `PLAN.md`를 생성한다.
3. PlanApproval worker(또는 사람)이 plan을 검토하고 task를 `todos`로 이동한다.
4. Implementer가 격리된 workspace에서 작업하고 `WORK-{n}.md`를 남긴다.
5. Reviewer가 `REVIEW-{n}.md`와 사람용 QA 체크리스트를 남긴다.
6. 리뷰가 통과되면 사람이 verification을 시작한다.
7. target repo에서 검증 후 approve하면 새 final branch 또는 target branch에 최종 commit과 summary 산출물이 생성된다.

### 아키텍처 개요

```text
repo-root/
├─ AGENTS.md
├─ .opencode/
│  └─ agents/
│     ├─ fs-kanban-planner.md
│     ├─ fs-kanban-plan-approval.md
│     ├─ fs-kanban-request-draft.md
│     ├─ fs-kanban-implementer.md
│     ├─ fs-kanban-reviewer.md
│     └─ fs-kanban-committer.md
├─ .kanban-agent/
│  ├─ requests/
│  ├─ planning/
│  ├─ plan-approving/
│  ├─ waiting-check-plans/
│  ├─ todos/
│  ├─ implementing/
│  ├─ waiting-reviews/
│  ├─ reviewing/
│  ├─ completed-reviews/
│  ├─ human-verifying/
│  ├─ done/
│  ├─ retrospectives/
│  └─ _runtime/
│     ├─ locks/
│     ├─ workspaces/
│     ├─ runs/
│     ├─ archive-runs/
│     ├─ events/
│     ├─ request-drafts/
│     ├─ request-uploads/
│     └─ board-cache/
└─ src/assistant_agent_kanban/
```

구성 요소는 크게 네 층입니다.

- `task directory`: 요청서, 계획서, 구현/리뷰/사람 검증 문서, `metadata.json` 저장
- `workspace`: 실제 코드 수정이 일어나는 격리 작업공간
- `runtime supervisor`: 스캔, 전이, worker 실행, recovery, (선택) Slack runtime
- `FastAPI + SSE`: 보드, 작업 상세, 로그, 설정, 회고, 실시간 업데이트

### 상태 머신

상태 목록:

- `requests`
- `planning`
- `plan-approving`
- `waiting-check-plans`
- `todos`
- `implementing`
- `waiting-reviews`
- `reviewing`
- `completed-reviews`
- `human-verifying`
- `done`

주요 전이:

```text
requests -> planning
planning -> plan-approving
planning -> waiting-check-plans
planning -> requests
plan-approving -> waiting-check-plans
plan-approving -> todos
waiting-check-plans -> todos
todos -> implementing
implementing -> waiting-reviews
implementing -> todos
waiting-reviews -> reviewing
reviewing -> completed-reviews
reviewing -> waiting-reviews
reviewing -> todos
completed-reviews -> human-verifying
completed-reviews -> todos
human-verifying -> todos
human-verifying -> done
```

규칙:

- 허용되지 않은 전이는 코드에서 차단
- `plan-approving`은 자동 승인 시 `todos`로 직접 promote 가능하고, 그렇지 않으면 `waiting-check-plans`로 fallback
- `completed-reviews`는 target repo 반영 완료 상태가 아님
- patch apply는 `completed-reviews -> human-verifying`에서만 수행
- 최종 commit은 `human-verifying -> done`에서만 수행

### Worker 구성

- `PlanningWorker` — `REQUEST.md`를 읽고 `PLAN.md` 생성
- `PlanApprovalWorker` — 생성된 `PLAN.md`를 요청과 대조해 자동 승인하거나 사람 검토로 라우팅
- `RequestDraftAgent` — UI/Slack 스레드에서 사람과 함께 요청서를 다듬는 역할
- `ImplementerWorker` — workspace에서 코드를 수정하고 `WORK-{n}.md` 기록
- `ReviewerWorker` — `REVIEW-{n}.md` 작성, 엔드포인트 위치 노출, 사람용 QA 체크리스트 생성
- `CommitWorker / Human Verification` — verification, target repo patch apply, summary 산출, final commit 흐름 담당

### Workspace 전략

기본 전략은 `clone-overlay`입니다.

- workspace root는 `_runtime/workspaces/{task_id}` 아래 생성
- 실제 수정 대상 repository checkout은 `_runtime/workspaces/{task_id}/repo` 아래 생성
- local clone 기반으로 준비
- 필요한 ignored/untracked 파일은 overlay copy 또는 symlink로 보강
- target repo와 구현 workspace를 분리해 오염 방지
- Codex는 workspace-write 모드로 실행, OpenCode/Claude/Gemini는 구현 시 target repo를 read-only로 다룸

### Task 산출물

- `REQUEST.md`
- `PLAN.md`
- `WORK-{n}.md`
- `REVIEW-{n}.md`
- `HUMAN-QA-{n}.md` — reviewer가 남기는 사람용 QA 체크리스트
- `REVIEWER-QA-{n}.md` — 선택적 human/reviewer Q&A 스레드
- `HUMAN-VERIFY-{n}.md` — 사람 검증 note와 verdict
- `HUMAN-VERIFY-{n}.comments.json` — 사람 검증 inline comment 상태
- `COMMIT.md`
- `*.json` raw outputs (worker run 단위)
- `metadata.json`

Markdown은 사람이 읽는 working artifact이고, JSON은 worker의 raw output입니다.
semantic target repo summary는 최종 승인 시 `target_repo_docs_root/YYYY/MM/DD/{task_id}-{branch-summary}-summary.md`에 기록됩니다.

### CLI 예시

#### 요청 생성

```bash
assistant-agent-kanban request "로그인 플로우 리팩터링" \
  --target-repo /path/to/target-project \
  --kanban-root ./.kanban-agent \
  --base-branch main
```

#### 로그 확인

```bash
assistant-agent-kanban logs TASK-0001 --kanban-root ./.kanban-agent
```

#### 앱 실행

```bash
assistant-agent-kanban serve --config ./config.local.yaml --host 0.0.0.0 --port 8000
```

### 웹 UI에서 할 수 있는 일

- 칸반 보드 보기 (phase tab, final/done 보드 포함)
- 상태별 task 카드 및 request draft 확인
- task 상세 팝업에서 metadata/로그/문서/토큰 사용량 요약 확인
- `REQUEST.md`, `PLAN.md`, 구현/리뷰/사람 QA/사람 검증 문서 열람
- 특정 상태에서 `PLAN.md` 편집 및 승인
- human verification 시작 / reject / approve (apply 성공, required QA 완료/skip, 사람 검증 note 없음, 미해결 inline comment 없음 기준으로 approve 게이팅)
- planner / implementer / reviewer를 명시적 선택 modal로 resume
- task 삭제 (target repo가 unsafe한 경우도 처리)
- 새 요청 생성 (assistant가 함께 작성)
- 인앱 설정 모달: assistant 선택, 역할별 백엔드 라우팅, 역할별 모델·토큰 budget, 테마, 언어
- task별 회고(retrospective) 화면

### Slack 연동 (선택)

활성화하면 Slack runtime이 다음을 제공합니다.

- 상태 전이와 verification 마일스톤에 대한 thread 알림
- verification 시작/승인/재작업/review loop resume 액션 버튼
- review-loop 요청과 assistant 기반 request drafting을 위한 modal flow
- review/plan/summary/completion markdown 파일 thread 업로드
- channel display metadata 기반 매칭과 테스트 성공 후 채널 활성화

Slack 설정은 config 파일의 `slack:` 섹션에서 관리하며, bot token (필요 시 socket mode용 app token)이 필요합니다.

### 설정

앱은 기본적으로 `./config.yaml`을 읽고 `./config.local.yaml`이 있으면 덮어씁니다. `examples/config.yaml`은 직접 실행용 경로라기보다 복사해서 쓰는 템플릿입니다.

중요한 항목:

- `kanban_root`
- `repo_root`
- `base_branch`
- `target_repo_docs_root`
- `opencode.*` — 역할별 agent 이름, 모델, 세션 토큰 budget
- `codex.*` — 역할별 모델, 세션 토큰 budget
- `claude.*` — 역할별 모델, 세션 토큰 budget
- `gemini.*` — 역할별 모델, 세션 토큰 budget
- `workspace.*`
- `locks.*`
- `runtime.*` — `coding_assistant`, `role_backends`, `language`, `theme`, agent count, auto-dispatch
- `repo_discovery.*`
- `slack.*` (선택)

역할별 백엔드 라우팅 예시:

```yaml
runtime:
  coding_assistant: opencode
  role_backends:
    planner: claude
    request_draft: opencode
    plan_approval: opencode
    implementer: codex
    reviewer: claude
    commit: opencode
```

### 저장소 구조

- `src/assistant_agent_kanban/` — domain, runtime, worker, service, adapter, API
  - `workers/` — planner, plan-approval, implementer, reviewer, committer
  - `services/` — task, board, human verification, retrospective, plan-approval learning, task deletion
  - `api/` — FastAPI app, route, SSE, template (HTML, CSS, 분리된 JS)
  - `*_adapter.py` — OpenCode, Codex, Claude, Gemini adapter
  - `slack_*.py` — Slack runtime, 알림, 채널 매칭, 설정 테스트
- `tests/` — workflow, service, adapter, API 테스트
- `.opencode/agents/` — 역할별 프롬프트 계약
- `examples/` — 설정/bootstrap 예제
- `docs/` — architecture, implementation map, agent brief

공개 사용자는 `README.md`를 먼저 읽으면 되고, 저장소를 수정하거나 agent 동작 규칙을 이해하려면 `AGENTS.md`와 `docs/*`를 함께 보는 것을 권장합니다.

### Python 사용 예시

```python
from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.assistant_factory import build_role_adapters
from assistant_agent_kanban.config import load_config

config = load_config("examples/config.yaml")
planner, implementer, reviewer, committer, branch_summary = build_role_adapters(config)
app = create_app(config, planner, implementer, reviewer, committer, branch_summary)
```

### 테스트와 공개 운영 메모

- 전체 테스트는 `pytest -q`
- 이 프로젝트는 “AI 자동화”보다 “검토 가능한 워크플로”에 무게를 둡니다.
- 사람이 개입하는 승인 단계는 의도적으로 제거하지 않았습니다.
- target repo는 verification 시점에 clean 상태여야 합니다.
- task 디렉토리 안에 전체 workspace를 두지 않습니다.
- CLI(OpenCode/Codex/Claude/Gemini) 내부 상태 파일은 source of truth로 사용하지 않습니다.

### 기여 안내

이 저장소는 `fork -> branch -> PR` 방식의 기여를 전제로 합니다.

- 원본 저장소에 직접 push하지 않습니다.
- contributor 브랜치를 원본 저장소에 직접 만드는 흐름을 기본으로 사용하지 않습니다.
- 외부 기여는 fork에서 작업한 뒤 Pull Request로 제안해 주세요.

자세한 내용은 `CONTRIBUTING.md`를 참고해 주세요.

### 관련 문서

- `AGENTS.md`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `LICENSE`
- `docs/01-architecture-review.md`
- `docs/02-implementation-plan.md`
- `docs/03-agent-task.md`
