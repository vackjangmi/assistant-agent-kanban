# Assistant Agent Kanban Architecture Reference

This document is the **architecture reference** for the current repository.

- Purpose: explain how the system is structured and which invariants must remain true
- Role: serve as the baseline for maintenance and design decisions
- Non-goal: preserve long historical design notes or early exploration text

## One-Line Summary

Assistant Agent Kanban is a filesystem-backed AI workflow orchestrator that uses **state directories + `metadata.json`** as the source of truth and provides a planning → implementation/review loop → human verification → done flow through a FastAPI + SSE dashboard.

## System Overview

The high-level flow is:

1. A human creates `REQUEST.md`.
2. The planner generates `PLAN.md`.
3. A human reviews the plan and moves it into implementation.
4. The implementer and reviewer iterate on the task.
5. After review passes, a human starts verification in the target repo.
6. Reject returns the task to `todos`; approve creates the final commit and finishes the task.
7. Completed work can later be grouped into retrospective summaries.

## Architectural Invariants

These rules are the core architectural constraints of the system.

1. The source of truth for workflow state is **directory state + `metadata.json`**.
2. OpenCode / oh-my-opencode internal state files are never used as the source of truth.
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

- Location: `_runtime/workspaces/{task_id}`
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

### 4. FastAPI + SSE Layer

This is the user-facing control and visibility layer.

- board snapshot API
- task detail and log APIs
- SSE live updates
- single HTML + vanilla JS UI

## State Machine

### States

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

### Allowed Transitions

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

### Human-Gated Transitions

- `waiting-check-plans -> todos`
- `completed-reviews -> human-verifying`
- `human-verifying -> done`

### State Meaning

- `completed-reviews` means **AI review passed**, not that the target repo has already been updated.
- `human-verifying` means a human is validating the reviewed result in the target repo.
- `done` means human approval and final commit are both complete.

## Task Artifact Model

Each task combines documents and metadata.

- `REQUEST.md` — human-authored initial request
- `PLAN.md` — planner output
- `WORK-{n}.md` — implementation iteration summary
- `REVIEW-{n}.md` — review iteration summary
- `COMMIT.md` — final commit information
- `*.json` — raw machine-readable results
- `metadata.json` — state, history, lease, integration, errors

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
- `plan`
- `implementation`
- `review`
- `integration`
- `commit`
- `lease`
- `history`
- `errors`

Design rules:

- `metadata.state` must match the actual directory location.
- `history` is the state transition audit trail.
- `lease` tracks ownership and heartbeat.
- Metadata writes must use atomic write semantics.

## Workspace Model

The default workspace strategy is `clone-overlay`.

- Start from a local clone
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
