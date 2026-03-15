# Backup Notes

This file captures the work that was implemented and validated before the code was rolled back.

## Goal

Two related changes were implemented:

1. Add verified artifact-file output for planner / implementer / reviewer so final markdown no longer depends only on stdout parsing.
2. Add an experimental OpenCode worker mode that uses `--format default --thinking`, with live output visible in a dedicated task `[로그]` tab.

## Final Shape That Was Implemented

### 1) Verified worker artifacts

- Added `RunArtifactSpec` and `RunArtifact` models.
- Extended `RunResult` with `artifact` metadata.
- Worker runs allocated an expected artifact path under task log/runtime paths.
- Planner / implementer / reviewer prompts were changed so the agent wrote one JSON artifact file to the orchestrator-provided path.
- Adapter verified:
  - file exists
  - JSON parses
  - `version` matches
  - `kind` matches (`plan`, `work`, `review`)
  - `markdown` exists and is non-empty
  - `review` artifacts also contain `verdict` of `PASS` or `NEEDS_CHANGES`
- On successful verification, markdown came from the artifact payload rather than stdout.
- On verification failure, the run was marked failed.

### 2) Dedicated `[로그]` tab

- Added a new task modal tab: `[로그]`.
- Reused existing `/api/tasks/{task_id}/logs` API.
- Lazy-loaded logs when the tab opened.
- Kept `activeLogName` in client state.
- On `worker_log` SSE events, if the logs tab was open, the UI refetched logs instead of refreshing the entire task detail.
- Selection rule:
  - preserve current selected log if still present
  - otherwise fall back to the newest log
- Viewer behavior:
  - prefer `rendered_content`
  - fall back to `debug_rendered_content`
  - otherwise show an empty/unavailable message

### 3) Experimental default-thinking mode

- Added config flag:
  - `opencode.experimental_default_thinking_mode: bool = False`
- Exposed the flag in settings API and dashboard settings UI.
- Adapter logic:
  - if `experimental_default_thinking_mode` is `True`
  - and `expected_artifact` is present
  - then run OpenCode with `--format default --thinking`
  - otherwise keep existing `--format json`
- This intentionally limited the experiment to worker runs that already had artifact verification.
- Non-worker calls such as branch-summary / retrospective generation were kept on JSON mode.

## Important Runtime Behavior Discovered

Observed / researched behavior for `opencode run --format default --thinking`:

- final answer goes to `stdout`
- thinking and some meta lines go to `stderr`
- ANSI color codes are present
- if only `stdout` is captured, thinking is lost

Because of that, the adapter was changed so that in experimental default-thinking mode:

- `stderr` was also streamed into the runtime log file
- ANSI was stripped for stored `stdout` / `stderr` values in `RunResult`
- raw runtime logs could still contain richer live text for the `[로그]` tab

## Files That Were Changed

These were the main files changed during the implementation:

- `src/fs_kanban_agent/models.py`
- `src/fs_kanban_agent/opencode_adapter.py`
- `src/fs_kanban_agent/workers/base.py`
- `src/fs_kanban_agent/workers/planner.py`
- `src/fs_kanban_agent/workers/implementer.py`
- `src/fs_kanban_agent/workers/reviewer.py`
- `src/fs_kanban_agent/config.py`
- `src/fs_kanban_agent/api/routes.py`
- `src/fs_kanban_agent/api/templates/index.html`
- `.opencode/agents/fs-kanban-planner.md`
- `.opencode/agents/fs-kanban-implementer.md`
- `.opencode/agents/fs-kanban-reviewer.md`
- `tests/conftest.py`
- `tests/test_config.py`
- `tests/test_opencode_adapter.py`
- `tests/test_planner_worker.py`
- `tests/test_reviewer_worker.py`
- `tests/test_api.py`

## Test Status Before Rollback

The final implementation state passed the full test suite.

- `pytest`
- Result: `189 passed`

## Key Design Decisions

### Why artifact files were kept

The safest part of the redesign was moving durable worker output to verified artifact files. That reduced dependency on parsing assistant stdout for final markdown.

### Why default-thinking mode was bounded

Default mode is good for human-visible live logs, but weak for structured metadata. To limit risk, only verified worker runs used `--format default --thinking`.

### Why non-worker runs stayed on JSON

Branch summary and retrospective generation still rely on concise machine-readable output expectations. Letting those paths ingest unstructured thinking text would have increased risk with little upside.

## Known Risks / Watch Points

These remained true even after tests passed:

1. `session_id` and `total_tokens` may become unreliable or blank for worker runs in default-thinking mode because they were previously extracted from JSON events.
2. Live log volume can get noisy because each streamed line can trigger `worker_log` handling and log refetches.
3. Raw runtime logs may still contain ANSI/control-sequence noise depending on exactly how OpenCode emits data.
4. Manual validation with a real OpenCode run is still necessary because tests used fake subprocess output.

## Recommended Manual Verification

If this work is re-applied, these were the recommended checks:

1. Enable `Experimental thinking mode` in Settings.
2. Run a full planner -> implementer -> reviewer cycle.
3. Keep the task `[로그]` tab open and confirm thinking appears live.
4. Inspect `PLAN.json`, `WORK-*.json`, `REVIEW-*.json` and confirm:
   - markdown is correct
   - artifact metadata exists
   - `stdout` / `stderr` are readable
   - `session_id` / `total_tokens` are acceptable even if blank or zero
5. Force a malformed or missing artifact and confirm the task fails clearly.
6. Smoke-test non-worker flows like branch summary or retrospective to confirm they still behave in JSON mode.

## Suggested Re-Implementation Order

If you want to rebuild this from scratch after rollback, the safest order is:

1. Restore artifact verification support first.
2. Restore `[로그]` tab UI next.
3. Add the settings/config flag for experimental thinking mode.
4. Switch adapter branching for worker runs only.
5. Re-run full tests.
6. Do one real OpenCode manual run with the logs tab open.

## Short Summary

The implemented version was a bounded hybrid design:

- verified artifact files were the durable source of truth
- worker logs got a dedicated `[로그]` tab
- worker runs could optionally use `--format default --thinking`
- non-worker OpenCode flows stayed on JSON mode

That version was mechanically sound and fully green in tests before rollback.
