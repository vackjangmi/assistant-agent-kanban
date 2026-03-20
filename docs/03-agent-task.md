# Assistant Agent Kanban Agent Task Brief

This document is the **execution brief** for AI coding agents working in this repository.

If `AGENTS.md` is the short rules document, this file explains the boundaries and expected outcomes when an agent makes real changes.

## Repo Assumptions

- Product name: `Assistant Agent Kanban`
- Python package: `assistant_agent_kanban`
- CLI: `assistant-agent-kanban`
- The repository already contains a working MVP/experimental implementation of a filesystem-backed workflow + FastAPI SSE dashboard.

## What This Repository Does

This repository covers:

- filesystem-backed workflow state management
- planner / implementer / reviewer / committer orchestration
- clone-overlay workspace isolation
- human verification flow
- web dashboard and task detail modal
- retrospective support

## Hard Constraints

Never break the following rules while working here.

1. Do not put the full workspace inside the task directory.
2. Perform state transitions only under a lock.
3. Do not remove human approval stages.
4. The source of truth is the state directory + `metadata.json`.
5. Do not use OpenCode / oh-my-opencode internal state files as workflow truth.
6. Do not touch the target repo before review passes.
7. Apply target repo patches only when human verification starts.
8. Create the final commit only during `human-verifying -> done`.
9. Keep `clone-overlay` as the default workspace strategy.

## State-Aware Workflow Summary

### `requests`
- starting state where the planner picks up a new request

### `planning`
- planner is running

### `waiting-check-plans`
- human reviews or edits the plan

### `todos`
- implementer-ready backlog state

### `implementing`
- implementation is running in a workspace

### `waiting-reviews`
- reviewer is waiting to start

### `reviewing`
- review is in progress

### `completed-reviews`
- AI review passed, but the target repo has not been updated yet

### `human-verifying`
- a human is validating the reviewed result in the target repo

### `done`
- final approval and commit are complete

## Code And Workspace Boundaries

### Task Directory

This stores task state documents and metadata.

- `REQUEST.md`
- `PLAN.md`
- `WORK-{n}.md`
- `REVIEW-{n}.md`
- `COMMIT.md`
- `metadata.json`

### Workspace

This is where real code edits happen.

- `_runtime/workspaces/{task_id}`

### Target Repo

This is the human verification repo.

- It is not the active implementation workspace.
- Patches are applied only when verification begins.

## Expected Outputs

When possible, a change in this repository should end with:

- code changes under `src/assistant_agent_kanban/...`
- updated or added tests under `tests/...`
- documentation updates when contracts change
- artifact viewer/editor checks when worker document formats change

## Validation Expectations

Before considering work complete, verify:

- relevant tests exist
- source-of-truth rules still hold
- workflow transitions and UI behavior still match
- human gates were not weakened
- workspace / target repo boundaries remain intact

## Agent Work Style In This Repo

- planner/reviewer-related changes should also consider document artifact formats
- implementer/reviewer/human verification changes should also consider state machine and target repo timing
- task modal / dashboard changes should also inspect `tests/test_api.py` and the user flow
- package/CLI changes should also inspect `pyproject.toml`, `main.py`, `run.sh`, `init.sh`, and `README.md`

## Definition Of Done

A change is done in this repository only when all of the following are true.

- the requested behavior is implemented
- workflow invariants still hold
- relevant tests were updated and verified
- documentation is not left stale
- human approval and verification gates were not weakened

## Recommended Reading Order

Before starting a meaningful change, read in this order:

1. `AGENTS.md`
2. `docs/01-architecture-review.md`
3. `docs/02-implementation-plan.md`
4. the code you are about to modify
5. the related test files

This order helps avoid the most common failures in this repo: breaking workflow rules, blurring workspace vs target repo boundaries, and creating UI changes that no longer match workflow contracts.
