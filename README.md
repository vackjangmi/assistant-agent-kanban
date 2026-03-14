# FS Kanban Agent

`fs-kanban-agent` is a filesystem-backed orchestration service for OpenCode-based
planner, implementer, and reviewer workers. It keeps workflow state in task
directories plus `metadata.json`, runs implementation in isolated workspaces,
supports human verification against the target repository, and exposes a small
FastAPI dashboard with SSE updates.

## What is included

- `src/fs_kanban_agent/` - domain models, workers, runtime supervisor, FastAPI app
- `tests/` - scanner, locks, transitions, workers, recovery, and API coverage
- `.opencode/agents/` - prompt contracts for planner, implementer, and reviewer
- `examples/config.yaml` - sample configuration
- `examples/bootstrap/README.md` - bootstrap guidance for a kanban root
- `docs/` - source design and planning documents used for the implementation

## Core behavior

- Filesystem state plus `metadata.json` is the source of truth
- Allowed states are `requests`, `planning`, `waiting-check-plans`, `todos`,
  `implementing`, `waiting-reviews`, `reviewing`, `completed-reviews`,
  `human-verifying`, and `done`
- Only documented transitions are allowed
- Human approvals remain explicit through lock-safe manual transitions
- Workspaces are created outside task directories under `_runtime/workspaces`
- Review pass is required before human verification can start
- Target repo patch apply happens only when human verification starts
- Final commit happens only from `human-verifying -> done`
- Implementer runs without real workspace changes are sent back to `todos`
- Each task can override its target repository and base branch via `REQUEST.md` frontmatter

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Or use the convenience bootstrap script:

```bash
./init.sh
```

That script creates `.venv` when needed, installs `.[dev]`, copies
`examples/config.yaml` to `config.local.yaml` on first run, and bootstraps the
default kanban directories.

## Run tests

```bash
pytest -q
```

## Start the app

```bash
uvicorn fs_kanban_agent.api.main:app
```

Or run the local launcher:

```bash
./run.sh
```

You can override the config or bind address if needed:

```bash
./run.sh --config /path/to/config.yaml --host 0.0.0.0 --port 8000
```

This uses `create_default_app()`, which loads default config and injects real
`SubprocessOpenCodeAdapter` instances.

By default the adapter uses plain `opencode run` without `--attach`. If you want
to reuse an external OpenCode server, set `opencode.attach_url` explicitly in
your config.

The fs-kanban worker prompts are still role-specific custom agents, but they now
use prompt-driven OMO delegation for lightweight helper work. The planner can
delegate bounded repository exploration or docs lookup to smaller specialists,
the implementer can delegate lightweight search and trivial helper subtasks, and
the reviewer can delegate evidence gathering. Final plans, file edits, review
verdicts, and other role-critical judgments stay with the main worker prompt.
This is prompt-driven behavior in `.opencode/agents/`, not adapter-level
orchestration inside `src/fs_kanban_agent/opencode_adapter.py`.

## Create a request task

The easiest way to bind a task to a specific target project is the CLI helper:

```bash
fs-kanban-agent request "Refactor login flow" \
  --target-repo /path/to/target-project \
  --kanban-root ./.kanban-agent \
  --base-branch main
```

This writes a new `REQUEST.md` under `requests/` with frontmatter describing the
task target. The scanner bootstraps that into task metadata and later workers use
the task-level target repo for workspace creation, review, human verification,
and commit.

You can also create requests directly from the dashboard at `/`. The popup form
collects the fields needed to generate the same structured `REQUEST.md` template:
title, goal, background, scope, out of scope, constraints, references,
acceptance criteria, target repo, and base branch.

Board cards are clickable. Clicking a task opens a detail modal with task
metadata first and a lazy-loaded log view for planner/implementer/reviewer output.
The same runtime logs are also available from the CLI:

```bash
fs-kanban-agent logs TASK-0001 --kanban-root ./.kanban-agent
```

Worker outputs are now dual-written in the task directory:

- Markdown files such as `PLAN.md`, `WORK-001.md`, `REVIEW-001.md` for human review and manual edits
- JSON companions such as `PLAN.json`, `WORK-001.json`, `REVIEW-001.json` containing the original machine-readable result

Markdown edits do not sync back into the JSON file. JSON stays as the original
captured worker output, while Markdown acts as the editable working copy.

The target repo field also supports nearby directory suggestions through a
configurable dropdown. By default it scans sibling directories near the current
repo, and you can control that with `repo_discovery.root` and
`repo_discovery.max_depth`.

You can also author the request file manually:

```md
---
title: Refactor login flow
target:
  repo_root: /path/to/target-project
  base_branch: main
---

# Refactor login flow

Implement the requested refactor.
```

## Minimal Python bootstrap

```python
from fs_kanban_agent.api.app import create_app
from fs_kanban_agent.config import load_config
from fs_kanban_agent.opencode_adapter import SubprocessOpenCodeAdapter

config = load_config("examples/config.yaml")
adapter = SubprocessOpenCodeAdapter()
app = create_app(config, adapter, adapter, adapter, adapter)
```

Then serve it with:

```bash
uvicorn mymodule:app
```

## API surface

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

## Configuration

Use `examples/config.yaml` as a starting point. Important settings:

- `kanban_root` - filesystem kanban state root
- `repo_root` - default target repository when a task does not override its target
- `base_branch` - base branch for isolated workspaces
- `opencode.*` - adapter binary, agent names, attach URL, timeout
- `opencode.planner_model` / `implementer_model` / `reviewer_model` - optional per-role model overrides injected into runtime custom agents
- `workspace.*` - clone-overlay root plus overlay copy/symlink entries
- `locks.*` - heartbeat, stale timeout, lock timeout
- `repo_discovery.root` - root directory to scan for target repo suggestions in the dashboard
- `repo_discovery.max_depth` - how many nested directory levels to include in those suggestions

## Testing strategy

The suite uses:

- temporary kanban roots
- temporary git repositories for workspace and human-verification behavior
- fake OpenCode adapters for deterministic planner/implementer/reviewer runs
- FastAPI `TestClient` for API coverage

## Notes

- The runtime supervisor performs startup recovery, rescans on filesystem changes,
  and dispatches workers in the same process as the FastAPI app.
- The dashboard is intentionally simple: a single HTML page with vanilla JS and SSE.
- The current implementation favors a testable MVP surface over production hardening.
