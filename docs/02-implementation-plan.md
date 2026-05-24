# Assistant Agent Kanban Implementation And Maintenance Map

This document is no longer an early implementation plan. It is a **maintenance-oriented map of the current codebase**.

It is meant to answer:

- what lives where
- which module owns which responsibility
- which tests and docs are affected when behavior changes

## Current Scope

The repository currently includes:

- filesystem-backed state machine
- planner / plan-approval / implementer / reviewer / commit workflow
- request drafting
- Antigravity / OpenCode / Codex / Claude / Gemini runtime adapters
- clone-overlay workspaces
- human verification flow
- optional Slack integration
- retrospective support
- FastAPI + SSE dashboard
- request creation, task detail, markdown artifact viewing/editing, and verification-related API/UI

## Package Layout

The Python package lives under `src/assistant_agent_kanban/`. The tree below is a maintenance map of important current modules rather than a promise that every helper file is listed.

```text
src/assistant_agent_kanban/
├─ __init__.py
├─ main.py
├─ config.py
├─ models.py
├─ enums.py
├─ exceptions.py
├─ scanner.py
├─ metadata_store.py
├─ locks.py
├─ transitions.py
├─ events.py
├─ language.py
├─ retry_policy.py
├─ plan_artifacts.py
├─ assistant_adapter.py
├─ assistant_factory.py
├─ antigravity_adapter.py
├─ opencode_adapter.py
├─ codex_adapter.py
├─ claude_adapter.py
├─ gemini_adapter.py
├─ workspace_manager.py
├─ integration_manager.py
├─ commit_manager.py
├─ target_repo_guard.py
├─ runtime.py
├─ recovery.py
├─ markdown_attachments.py
├─ request_creator.py
├─ request_drafting.py
├─ request_draft_store.py
├─ request_parser.py
├─ repo_discovery.py
├─ repo_branches.py
├─ log_parser.py
├─ omo_config.py
├─ agent_materializer.py
├─ slack_api.py
├─ slack_channel_matcher.py
├─ slack_notifications.py
├─ slack_runtime.py
├─ slack_settings_test.py
├─ services/
│  ├─ board_service.py
│  ├─ task_service.py
│  ├─ task_deletion_service.py
│  ├─ human_verification_service.py
│  ├─ plan_approval_learning.py
│  └─ retrospective_service.py
├─ workers/
│  ├─ base.py
│  ├─ planner.py
│  ├─ plan_approval.py
│  ├─ implementer.py
│  ├─ reviewer.py
│  └─ committer.py
└─ api/
   ├─ app.py
   ├─ main.py
   ├─ routes.py
   ├─ sse.py
   ├─ ui.py
   └─ templates/index.html
```

## Runtime Entrypoints

### CLI

- Distribution / CLI name: `assistant-agent-kanban`
- Entrypoint: `assistant_agent_kanban.main:main`

Main commands:

- `assistant-agent-kanban serve`
- `assistant-agent-kanban request`
- `assistant-agent-kanban logs`

### ASGI

- App object: `assistant_agent_kanban.api.main:app`
- App factory: `assistant_agent_kanban.api.main:create_app`

## Responsibility Map

### Config / Bootstrap

Relevant files:

- `src/assistant_agent_kanban/config.py`
- `init.sh`
- `run.sh`
- `examples/config.yaml`

Responsibilities:

- load base configuration
- bootstrap `kanban_root` and runtime directories
- define runtime backend settings for Antigravity / OpenCode / Codex / Claude / Gemini
- define per-role backend/model settings and optional Slack settings
- maintain workspace, lock, and repo discovery settings

Maintenance note:

- If config keys change, update `README.md`, `examples/config.yaml`, and the settings UI/API together.

### Scanner And Metadata

Relevant files:

- `src/assistant_agent_kanban/scanner.py`
- `src/assistant_agent_kanban/metadata_store.py`
- `src/assistant_agent_kanban/models.py`

Responsibilities:

- scan state directories
- bootstrap tasks when needed
- read/write metadata
- materialize board and task snapshots

Key contracts:

- scans must be deterministic
- `metadata.json` writes must be atomic
- directory state and `metadata.state` must match

### Locks And Transitions

Relevant files:

- `src/assistant_agent_kanban/locks.py`
- `src/assistant_agent_kanban/transitions.py`
- `src/assistant_agent_kanban/enums.py`

Responsibilities:

- per-task locking
- allowed transition validation
- state moves and history tracking

Key contracts:

- never mutate state without a lock
- invalid transitions must fail loudly

### Assistant Adapter Layer

Relevant files:

- `src/assistant_agent_kanban/assistant_adapter.py`
- `src/assistant_agent_kanban/antigravity_adapter.py`
- `src/assistant_agent_kanban/opencode_adapter.py`
- `src/assistant_agent_kanban/codex_adapter.py`
- `src/assistant_agent_kanban/claude_adapter.py`
- `src/assistant_agent_kanban/gemini_adapter.py`
- `src/assistant_agent_kanban/assistant_factory.py`
- `src/assistant_agent_kanban/agent_materializer.py`

Responsibilities:

- invoke Antigravity / OpenCode / Codex / Claude / Gemini
- capture raw results
- extract final assistant text
- wire role-specific adapters

Key contracts:

- runtime adapter state is not the workflow source of truth
- adapters normalize execution results for the rest of the system

### Planning Worker

Relevant files:

- `src/assistant_agent_kanban/workers/planner.py`

Responsibilities:

- read `requests` tasks
- produce `PLAN.md`
- optionally persist `SPLIT-PROPOSAL.md` / `SPLIT-PROPOSAL.json` when the request should be split into independent child requests
- move tasks to `plan-approving`

When changing this area, also check:

- `PLAN.md` document format
- plan approval worker/UI
- task detail artifact viewer/editor behavior

### Plan Approval Worker

Relevant files:

- `src/assistant_agent_kanban/workers/plan_approval.py`
- `src/assistant_agent_kanban/services/plan_approval_learning.py`

Responsibilities:

- evaluate generated plans
- auto-approve low-risk plans into `todos`
- route uncertain, risky, or split-recommended plans to `waiting-check-plans`
- write `PLAN-APPROVAL.md` / `PLAN-APPROVAL.json`

When changing this area, also check:

- plan edit tracking
- manual approval artifacts
- Slack plan approval notifications

### Workspace And Implementer Worker

Relevant files:

- `src/assistant_agent_kanban/workspace_manager.py`
- `src/assistant_agent_kanban/workers/implementer.py`

Responsibilities:

- prepare workspaces
- apply clone-overlay strategy
- keep the workspace root under `_runtime/workspaces/{task_id}` and the editable repository checkout under `_runtime/workspaces/{task_id}/repo`
- run implementation iterations
- record `WORK-{n}.md`

When changing this area, also check:

- workspace root location
- editable repository subdirectory (`repo`)
- no-op implementation handling
- transition rules for review readiness

### Reviewer Worker And Integration

Relevant files:

- `src/assistant_agent_kanban/workers/reviewer.py`
- `src/assistant_agent_kanban/integration_manager.py`
- `src/assistant_agent_kanban/commit_manager.py`

Responsibilities:

- generate review verdicts
- decide entry into `completed-reviews`
- write `HUMAN-QA-{n}.md` for human verification
- support reviewer Q&A artifacts

Key contracts:

- `completed-reviews` does not apply the target repo yet
- target repo patch apply happens only when human verification starts

### Human Verification And Retrospective

Relevant files:

- `src/assistant_agent_kanban/services/human_verification_service.py`
- `src/assistant_agent_kanban/services/retrospective_service.py`
- `src/assistant_agent_kanban/api/routes.py`

Responsibilities:

- verification start / reject / approve flow
- branch summary generation
- retrospective generation and retrieval

When changing this area, also check:

- target repo clean rule
- reject rollback behavior
- completion modes (`new-branch`, `target-branch`)

### API And Dashboard

Relevant files:

- `src/assistant_agent_kanban/api/app.py`
- `src/assistant_agent_kanban/api/main.py`
- `src/assistant_agent_kanban/api/routes.py`
- `src/assistant_agent_kanban/api/sse.py`
- `src/assistant_agent_kanban/api/templates/index.html`

Responsibilities:

- serve board snapshots
- expose task detail, logs, and markdown artifact APIs
- provide request creation and assistant-drafted request UI
- provide settings, plan editor, review note, reviewer Q&A, QA checklist, and verification UI
- stream SSE updates

When changing this area, also check:

- task modal layout
- markdown artifact viewer/editor behavior
- localStorage keys and settings UI
- HTML string assertions in tests

## Test Map

Representative major tests include:

- `tests/test_scanner.py`
- `tests/test_metadata_store.py`
- `tests/test_config.py`
- `tests/test_transitions.py`
- `tests/test_locks.py`
- `tests/test_runtime.py`
- `tests/test_planner_worker.py`
- `tests/test_plan_approval_worker.py`
- `tests/test_plan_approval_learning.py`
- `tests/test_implementer_worker.py`
- `tests/test_reviewer_worker.py`
- `tests/test_human_verification_service.py`
- `tests/test_api_approve_verification.py`
- `tests/test_retrospective_service.py`
- `tests/test_api.py`
- `tests/test_task_service.py`
- `tests/test_task_deletion_service.py`
- `tests/test_request_drafting.py`
- `tests/test_request_draft_store.py`
- `tests/test_request_cli.py`
- `tests/test_target_repo_guard.py`
- `tests/test_antigravity_adapter.py`
- `tests/test_opencode_adapter.py`
- `tests/test_codex_adapter.py`
- `tests/test_claude_adapter.py`
- `tests/test_gemini_adapter.py`
- `tests/test_slack_api.py`
- `tests/test_slack_runtime.py`
- `tests/test_slack_notifications.py`
- `tests/test_slack_settings_test.py`
- `tests/test_board_service.py`
- `tests/test_main.py`

Maintenance rules:

- If you change workflow semantics, check transitions/worker/recovery tests together.
- If you change UI text or DOM structure, check `tests/test_api.py` together.
- If you change package or CLI identity, check `tests/test_main.py`, install flow, and entrypoints together.

## Frequently Coupled Contracts

The following kinds of changes tend to affect code, tests, and docs together.

- package / CLI naming
- state names or allowed transitions
- markdown artifact names or formats
- human verification completion modes
- settings payload/response shape
- task detail modal structure

## Maintenance Checklist

Before finalizing a change, check:

1. Did you preserve the source-of-truth rule?
2. Did you preserve task directory vs workspace separation?
3. Did you avoid weakening human gates?
4. Do the transition rules and UI still agree?
5. Did you update the relevant tests?
6. Did you keep `README.md`, `AGENTS.md`, and `docs/*` in sync?

## Deferred Work

Based on the current implementation, the following remain secondary.

- production auth / access control
- operational hardening
- richer metrics / observability
- better multi-repo UX
- issue / PR template cleanup

## Conclusion

This document is not about “what should we build?” but about “what exists now, where it lives, and what else must be checked when one part changes.” Read it together with `docs/01-architecture-review.md` and `AGENTS.md` before making workflow-impacting changes.
