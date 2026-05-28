# Assistant Agent Kanban Architecture Reference

This document is the **architecture reference** for the current repository.

- Purpose: explain how the system is structured and which invariants must remain true
- Role: serve as the baseline for maintenance and design decisions
- Non-goal: preserve long historical design notes or early exploration text

## One-Line Summary

Assistant Agent Kanban is a filesystem-backed AI workflow orchestrator that uses **state directories + `metadata.json`** as the source of truth and provides a planning → plan approval → implementation/review loop → human verification → done flow through a FastAPI + SSE dashboard.

## System Overview

The high-level flow is:

1. A human creates `REQUEST.md`.
2. The planner generates `PLAN.md`.
3. The plan-approval worker either auto-approves the plan or routes it to a human.
4. The implementer and reviewer iterate on the task.
5. After review passes, a human starts verification in the target repo.
6. Reject returns the task to `todos`; approve creates the final commit and finishes the task.
7. Completed work can later be grouped into retrospective summaries.

## Architectural Invariants

These rules are the core architectural constraints of the system.

1. The source of truth for workflow state is **directory state + `metadata.json`**.
2. Antigravity, OpenCode, Codex, Claude, Gemini, and oh-my-opencode internal state files are never used as the source of truth.
3. Task directories and real code workspaces are separate.
4. State transitions always happen under a lock.
5. The target repo must not be patched before review passes.
6. Target repo patch apply happens only during `completed-reviews -> human-verifying`.
7. The final commit is created only during `human-verifying -> done`.

## Runtime Layers

The system is easiest to understand as four layers.

### 1. Task Directory Layer

This is the kanban root and its per-task directories.

- Stores request, plan, implementation, and review documents
- Stores `metadata.json`
- Represents workflow state by directory location

### 2. Workspace Layer

This is the isolated code-editing area.

- Workspace root: `_runtime/workspaces/{task_id}`
- Editable repository checkout: `_runtime/workspaces/{task_id}/repo`
- Strategy: `clone-overlay`
- Implementer edits code only here

### 3. Runtime Supervisor Layer

This is the workflow engine.

- scanner
- metadata store
- locks
- transitions
- workers
- recovery
- optional Slack runtime
- read-only task inspector runtime

### 4. FastAPI + SSE Layer

This is the user-facing control and visibility layer.

- board snapshot API
- task detail and log APIs
- settings and request-draft APIs
- SSE live updates
- single HTML + vanilla JS UI

## Module Layout

```
src/assistant_agent_kanban/
├── adapters (top-level *_adapter.py)
│   ├── assistant_adapter.py       # base AssistantAdapter contract + backend manager
│   ├── antigravity_adapter.py
│   ├── opencode_adapter.py
│   ├── codex_adapter.py
│   ├── claude_adapter.py
│   └── gemini_adapter.py
├── assistant_factory.py           # build_adapter_registry + per-role wiring
├── workers/                       # one worker per workflow stage
│   ├── base.py                    # shared lifecycle + dispatch protocol
│   ├── planner.py
│   ├── plan_approval.py
│   ├── implementer.py
│   ├── reviewer.py
│   └── committer.py
├── services/                      # stateful domain operations callable from many places
│   ├── board_service.py
│   ├── task_service/              # split into mixins (service, token_usage,
│   │   ├── _service.py            # artifacts, changed_files, resume, helpers)
│   │   ├── _token_usage.py
│   │   ├── _artifacts.py
│   │   ├── _changed_files.py
│   │   ├── _resume.py
│   │   ├── _helpers.py
│   │   ├── _data.py
│   │   └── _protocol.py
│   ├── human_verification_service.py
│   ├── task_deletion_service.py
│   ├── retrospective_service.py
│   └── plan_approval_learning.py
├── runtime/                       # workflow supervisor (split from monolithic runtime.py)
│   ├── _supervisor.py             # core: __init__, lifecycle, dispatch, recovery
│   ├── _slack.py                  # Slack interaction handlers (mixin)
│   ├── _protocol.py               # type-only stub for the Slack mixin
│   └── __init__.py
├── api/                           # FastAPI surface
│   ├── app.py                     # create_app + server lock
│   ├── main.py                    # uvicorn entry point
│   ├── routes/                    # split by domain
│   │   ├── settings_routes.py
│   │   ├── task_routes.py
│   │   ├── request_routes.py
│   │   ├── workflow_routes.py
│   │   ├── _payloads.py
│   │   ├── _helpers.py
│   │   └── _build.py
│   ├── sse.py                     # SSE event stream
│   ├── ui.py                      # /  → renders single-page HTML
│   └── templates/                 # static assets concatenated into one <script>
│       ├── index.html
│       ├── index.css
│       └── js/                    # 00_globals → 99_sse (load order matters;
│                                   # prefixes spaced in tens for extensibility)
├── core utilities (top-level)
│   ├── config.py                  # AppConfig + load_config
│   ├── models.py                  # Pydantic state / artifact models
│   ├── enums.py                   # TaskState + STATE_ORDER
│   ├── metadata_store.py          # atomic metadata.json writes
│   ├── locks.py                   # per-task FileLocks
│   ├── transitions.py             # TransitionManager (state machine enforcement)
│   ├── scanner.py                 # KanbanScanner: filesystem → TaskContext
│   ├── recovery.py                # startup orphan recovery
│   └── workspace_manager.py       # clone-overlay workspaces
└── Slack / integrations
    ├── slack_runtime.py           # socket-mode listener
    ├── slack_api.py               # raw Web API calls
    ├── slack_notifications.py     # milestone publishing
    └── integration_manager.py     # patch apply + final commit
```

## Layer Responsibilities And Dependency Direction

The codebase enforces a one-way dependency arrow:

```
adapter  →  worker  →  service  →  api
                       ↑
                    runtime  (composes services + workers + Slack)
```

- **adapter** (`*_adapter.py`): each implements the `AssistantAdapter` contract — invoke a CLI tool, parse `run` output, expose `discover_models` and `cancel_task`. Adapters do not know about workflow state.
- **worker** (`workers/`): one worker per workflow stage. A worker selects candidate tasks, acquires a lock via `TransitionManager`, dispatches an adapter, and writes artifacts. Workers do not call services.
- **service** (`services/`): pure domain operations — read/write task artifacts, resolve changed files, approve plans, verify completion. Services accept the supervisor's collaborators by injection.
- **runtime** (`runtime/`): wires workers + services + adapters together, owns the asyncio dispatch loop, and brokers Slack interactions. `runtime.build_runtime()` is the single composition root.
- **api** (`api/`): exposes services and workers through FastAPI routes plus SSE. The API never calls adapters directly.

Module boundaries are checked informally — there is no enforced linter rule — but every PR that adds a cross-layer call (e.g. an adapter importing a service) is a smell worth questioning.

## Extension Points

### Adding A New Assistant Backend

1. Create `src/assistant_agent_kanban/{name}_adapter.py` that subclasses `AssistantAdapter` (see `claude_adapter.py` for a minimal reference implementation).
2. Register the backend's display label in `SUPPORTED_RUNTIME_ASSISTANTS` (`config.py`).
3. Add a `*Config` section to `AppConfig` if the backend has runtime knobs (model defaults, tool flags). Update `assistant_factory.build_adapter_registry` to instantiate the new adapter from that config.
4. Wire role selection: `config.backend_for_role(role)` already understands any string that appears in `SUPPORTED_RUNTIME_ASSISTANTS`; ensure the settings UI option list is unchanged (it pulls from `available_assistants` in `routes/_helpers.py:_settings_response`).
5. Add unit tests under `tests/test_{name}_adapter.py` covering: `run`, `discover_models`, and error paths.

### Adding A New API Route

1. Decide which group the new route belongs to (`settings_routes`, `task_routes`, `request_routes`, `workflow_routes`) and add the handler inside its `register(router)` function.
2. Define request payloads in `api/routes/_payloads.py`; share helpers in `api/routes/_helpers.py`.
3. Add tests under the matching file in `tests/api/`.

### Adding A New Workflow Stage

Avoid this unless you change the state machine in `enums.py` + `transitions.py` together. Any new state needs:

- a new entry in `TaskState` + `STATE_ORDER`
- an allowed-transition pair in `transitions.py`
- a new worker (if AI-driven) or human gate (if human-driven)
- a recovery policy in `recovery.py`
- a board column rendering rule in the frontend

## State Machine

### States

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
- `closed`

### Allowed Transitions

- `requests -> planning`
- `requests -> closed`
- `planning -> requests`
- `planning -> plan-approving`
- `planning -> waiting-check-plans`
- `planning -> closed`
- `plan-approving -> waiting-check-plans`
- `plan-approving -> todos`
- `plan-approving -> closed`
- `waiting-check-plans -> todos`
- `waiting-check-plans -> closed`
- `todos -> implementing`
- `todos -> closed`
- `implementing -> todos`
- `implementing -> waiting-reviews`
- `implementing -> closed`
- `waiting-reviews -> reviewing`
- `waiting-reviews -> closed`
- `reviewing -> waiting-reviews`
- `reviewing -> todos`
- `reviewing -> completed-reviews`
- `reviewing -> closed`
- `completed-reviews -> todos`
- `completed-reviews -> human-verifying`
- `completed-reviews -> closed`
- `human-verifying -> todos`
- `human-verifying -> done`
- `human-verifying -> closed`
- `closed` has no outgoing transitions

### Human-Gated Transitions

- `waiting-check-plans -> todos`
- `waiting-check-plans -> closed`
- any nonterminal state except `done` can be cancelled by a human into `closed`
- `completed-reviews -> human-verifying`
- `human-verifying -> done`

### State Meaning

- `completed-reviews` means **AI review passed**, not that the target repo has already been updated.
- `human-verifying` means a human is validating the reviewed result in the target repo.
- `done` means human approval and final commit are both complete.
- `closed` means the task is no longer being implemented; for split requests, child task ids are recorded in `metadata.closure`, and for cancellations the reason is `cancelled_by_human`.

## Task Artifact Model

Each task combines documents and metadata.

- `REQUEST.md` — human-authored initial request
- `PLAN.md` — planner output
- `SPLIT-PROPOSAL.md` / `SPLIT-PROPOSAL.json` — optional planner recommendation for splitting a large request into independent child requests
- `PLAN-APPROVAL.md` / `PLAN-HUMAN-APPROVAL.md` — AI or human plan approval record
- `WORK-{n}.md` — implementation iteration summary
- `REVIEW-{n}.md` — review iteration summary
- `HUMAN-QA-{n}.md` — reviewer-provided human QA checklist
- `REVIEWER-QA-{n}.md` — optional reviewer Q&A thread
- `HUMAN-VERIFY-{n}.md` — human verification notes and verdict
- `HUMAN-VERIFY-{n}.comments.json` — inline human verification comments
- `COMMIT.md` — final commit information
- `*.json` — raw machine-readable results
- `metadata.json` — state, history, lease, integration, errors

The target repo summary is not `SUMMARY.md` in the task directory. It is written during final approval under `target_repo_docs_root/YYYY/MM/DD/{task_id}-{branch-summary}-summary.md`.

Interpretation rules:

- Markdown is the human-readable working artifact.
- JSON is the raw worker output.
- Markdown changes do not automatically sync back into JSON.

## Metadata Contract

Every task has `metadata.json`.

Minimum required fields:

- `task_id`
- `title`
- `slug`
- `state`
- `created_at`
- `updated_at`
- `request`
- `human_verification`
- `target`
- `runtime_pin`
- `plan`
- `plan_approval`
- `cycle`
- `implementation`
- `review`
- `integration`
- `commit`
- `slack`
- `retry_gate`
- `lease`
- `history`
- `errors`

The current schema also carries `version` and optional grouping fields such as `completed_group_override`.

Design rules:

- `metadata.state` must match the actual directory location.
- `history` is the state transition audit trail.
- `lease` tracks ownership and heartbeat.
- Metadata writes must use atomic write semantics.

## Workspace Model

The default workspace strategy is `clone-overlay`.

- Start from a local clone
- Keep the workspace root under `_runtime/workspaces/{task_id}` by default
- Keep the editable repository checkout under `_runtime/workspaces/{task_id}/repo`
- Add overlay copy or symlink support for needed ignored/untracked files
- Do not copy full build outputs by default

Why this strategy fits the system:

- It cleanly separates task state from code execution
- It reduces contamination during implementation
- It makes review and human verification easier to manage independently

## Integration And Human Verification

The target repo is not touched immediately after review passes. The flow is:

1. Reviewer moves the task to `completed-reviews`.
2. A human explicitly starts verification.
3. Only then is the reviewed result applied to the target repo.
4. A human runs and validates the result.
5. Reject returns to `todos`; approve creates the final commit and moves to `done`.

Operating assumptions:

- The target repo must be clean before verification begins.
- The target repo is not the active implementation workspace before verification.
- Approval requires a successful verification apply, no human verification note, completed or skipped required QA items, and no unresolved inline comments.
- Approval can finalize onto a new final branch or directly onto the target branch, depending on the selected completion mode.

## Locking And Recovery

### Locking

- Use per-task locks.
- Store lock files in a stable runtime directory.
- Never place lock files inside moving task directories.

### Lease / Heartbeat

- `lease.owner`
- `lease.run_id`
- `lease.heartbeat_at`

### Recovery

On startup, the server inspects in-progress tasks.

Default recovery policy:

- orphaned `planning` → `requests`
- orphaned `plan-approving` → `waiting-check-plans`
- orphaned `implementing` → `todos`
- orphaned `reviewing` → `waiting-reviews`

## API And Dashboard Model

Core endpoints:

- `GET /healthz`
- `GET /api/board`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/logs`
- `GET /api/events`
- `GET /`

Extended endpoints cover:

- plan approval
- human verification start / reject / approve
- human review notes
- retrospective generation
- settings and repo discovery

UI principles:

- single HTML page
- vanilla JS
- board snapshot + SSE updates
- task detail modal for metadata, logs, markdown artifacts, review notes, and verification flow

## Observability

The system should preserve:

- raw JSON event logs
- task state transition history
- worker heartbeat
- read-only task inspection reports
- integration apply and commit results
- branch summary and retrospective artifacts

## Deferred Concerns

The current implementation is still MVP-oriented, so these remain lower priority.

- production-grade authentication
- operational hardening
- deeper metrics / observability
- richer multi-repo UX

## Conclusion

The real value of this system is not merely “running AI,” but preserving a workflow that humans and AI can operate together. The state directories, metadata, workspace separation, human gates, and verification timing rules are the core of the architecture.
