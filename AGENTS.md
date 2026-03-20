# Assistant Agent Kanban Project Rules

This is the short operating rules document that humans and AI agents should read first in this repository.

For deeper detail, read these documents.

- `docs/01-architecture-review.md` — current architecture and invariants
- `docs/02-implementation-plan.md` — implementation map from a code structure and maintenance perspective
- `docs/03-agent-task.md` — practical brief for agents doing real work

## Mission

This repository implements **filesystem-backed workflow state + AI worker orchestration + FastAPI SSE dashboard**.

- Product name: `Assistant Agent Kanban`
- Python package: `assistant_agent_kanban`
- CLI: `assistant-agent-kanban`

## Core Invariants

Always keep these rules intact.

1. The source of truth for workflow state is **the state directory location + `metadata.json`**.
2. Never use OpenCode or oh-my-opencode internal state files as the source of truth.
3. Keep the task directory separate from the real code workspace.
4. Perform implementation only inside the workspace.
5. Never change task state without a lock.
6. Human verification can start only after review passes.
7. Apply the target repo patch only during `completed-reviews -> human-verifying`.
8. Create the final commit only during `human-verifying -> done`.

## State Machine

### States

- `requests` — initial request state
- `planning` — planner is generating a plan
- `waiting-check-plans` — human reviews or edits the plan
- `todos` — waiting for implementation
- `implementing` — implementer is working in the workspace
- `waiting-reviews` — waiting for reviewer
- `reviewing` — review is in progress
- `completed-reviews` — AI review passed, waiting to start human verification
- `human-verifying` — human is verifying in the target repo
- `done` — final approval and commit are complete

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

The code must block any transition that is not allowed.

## Human-Gated Steps

These are the main decision points that require human judgment.

- `waiting-check-plans -> todos` — approve implementation after reviewing or editing the plan
- `completed-reviews -> human-verifying` — start verification by applying the reviewed result to the real target repo
- `human-verifying -> done` — final approval after human verification

## Role Responsibilities

### PlanningWorker

- Input state: `requests`
- Input document: `REQUEST.md`
- Output document: `PLAN.md`
- Result state: `waiting-check-plans`
- Planner should remain a read-only document producer by default.

### ImplementerWorker

- Input state: `todos`
- Work location: workspace under `_runtime/workspaces/{task_id}`
- Output document: `WORK-{n}.md`
- Result state: `waiting-reviews` if there are changes, otherwise `todos`

### ReviewerWorker

- Input state: `waiting-reviews`
- Output document: `REVIEW-{n}.md`
- Result state: `completed-reviews` on `PASS`, otherwise `todos`

### Commit / Human Verification Flow

- Input states: `completed-reviews`, `human-verifying`
- Apply the patch to the target repo only when a human starts verification.
- If the human rejects it, go back to `todos`.
- If the human approves it, move to `done`.

## Workspace Rules

- Default strategy: `clone-overlay`
- Always place the workspace under `_runtime/workspaces/{task_id}`.
- Never place the full repo workspace inside the task directory.
- The target repo is not the active implementation area before human verification.

## Metadata And Lock Rules

Every task must have `metadata.json`.

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

Additional rules:

- Use atomic writes for metadata updates.
- Place lock files in a stable runtime path, not inside a moving task directory.
- `metadata.state` must always match the actual directory state.

## OpenCode / Codex Runtime Rules

- OpenCode and Codex CLI are the execution engines.
- The Python application is the workflow and state machine engine.
- Planner and reviewer should stay focused on producing markdown results.
- Implementer edits real code inside the workspace.
- Store raw run results, and keep human-facing markdown outputs separately.

## Quality And Testing Rules

- Python 3.11+
- Pydantic v2
- Keep functions small and testable
- Isolate subprocess wrappers
- Convert exceptions into domain exceptions
- Never log sensitive information

Minimum test areas:

- scanner
- transitions
- locks
- planner worker
- implementer worker
- reviewer worker
- recovery
- board/API

## Deliverables

Work in this repository should keep at least these outputs in place.

- `src/assistant_agent_kanban/...`
- `tests/...`
- `README.md`
- example config files
- bootstrap examples
- FastAPI app entrypoint

## Prompt Contract References

The real prompt contract sources for each role are these files.

- `.opencode/agents/fs-kanban-planner.md`
- `.opencode/agents/fs-kanban-implementer.md`
- `.opencode/agents/fs-kanban-reviewer.md`
- `.opencode/agents/fs-kanban-committer.md`

Do not copy long prompts into `AGENTS.md`. Keep only repository rules and invariants here.
