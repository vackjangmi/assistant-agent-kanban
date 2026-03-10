# FS Kanban Agent

`fs-kanban-agent` is a filesystem-backed orchestration service for OpenCode-based
planner, implementer, reviewer, and committer workers. It keeps workflow state in
task directories plus `metadata.json`, runs implementation in isolated
workspaces, applies reviewed patches back to an integration repository, and
exposes a small FastAPI dashboard with SSE updates.

## What is included

- `src/fs_kanban_agent/` - domain models, workers, runtime supervisor, FastAPI app
- `tests/` - scanner, locks, transitions, workers, recovery, and API coverage
- `.opencode/agents/` - prompt contracts for planner, implementer, reviewer, committer
- `examples/config.yaml` - sample configuration
- `examples/bootstrap/README.md` - bootstrap guidance for a kanban root
- `docs/` - source design and planning documents used for the implementation

## Core behavior

- Filesystem state plus `metadata.json` is the source of truth
- Allowed states are `requests`, `planning`, `waiting-check-plans`, `todos`,
  `implementing`, `waiting-reviews`, `reviewing`, `completed-reviews`,
  `integration-test-completed`, and `done`
- Only documented transitions are allowed
- Human approvals remain explicit through lock-safe manual transitions
- Workspaces are created outside task directories under `_runtime/workspaces`
- Review pass is required before integration patch apply
- Final commit happens only from `integration-test-completed -> done`

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Run tests

```bash
pytest -q
```

## Start the app

```bash
uvicorn fs_kanban_agent.api.main:app
```

This uses `create_default_app()`, which loads default config and injects real
`SubprocessOpenCodeAdapter` instances.

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
- `GET /api/events`
- `POST /api/tasks/{task_id}/approve-plan`
- `POST /api/tasks/{task_id}/approve-integration`
- `GET /`

## Configuration

Use `examples/config.yaml` as a starting point. Important settings:

- `kanban_root` - filesystem kanban state root
- `repo_root` - clean integration repository
- `base_branch` - base branch for isolated workspaces
- `opencode.*` - adapter binary, agent names, attach URL, timeout
- `workspace.*` - clone-overlay root plus overlay copy/symlink entries
- `locks.*` - heartbeat, stale timeout, lock timeout

## Testing strategy

The suite uses:

- temporary kanban roots
- temporary git repositories for workspace/integration behavior
- fake OpenCode adapters for deterministic planner/implementer/reviewer runs
- FastAPI `TestClient` for API coverage

## Notes

- The runtime supervisor performs startup recovery, rescans on filesystem changes,
  and dispatches workers in the same process as the FastAPI app.
- The dashboard is intentionally simple: a single HTML page with vanilla JS and SSE.
- The current implementation favors a testable MVP surface over production hardening.
