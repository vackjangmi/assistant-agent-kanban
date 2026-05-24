from __future__ import annotations

import asyncio
from pathlib import Path


from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workspace_manager import WorkspaceManager
from assistant_agent_kanban.workers.implementer import ImplementerWorker

from ..conftest import FakeAdapter


def valid_plan_markdown(summary: str = "plan") -> str:
    return "\n".join(
        [
            "## Summary",
            summary,
            "",
            "## Scope",
            "- Scope item",
            "",
            "## Out of Scope",
            "- Out of scope item",
            "",
            "## File Map",
            "- `app.txt`: example file",
            "",
            "## Step-by-step Plan",
            "1. Update the task.",
            "",
            "## Validation Plan",
            "- Run focused tests.",
            "",
            "## Acceptance Criteria",
            "- The request is satisfied.",
            "",
            "## Risks",
            "- Low risk.",
            "",
            "## Open Questions",
            "- None.",
        ]
    )



def _settings_adapter_registry(opencode_adapter=None, codex_adapter=None, gemini_adapter=None, claude_adapter=None, antigravity_adapter=None):
    return {
        "antigravity": antigravity_adapter or FakeAdapter(["antigravity"], discovery_responses=[["Gemini 3.5 Flash (High)"]]),
        "opencode": opencode_adapter or FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]]),
        "codex": codex_adapter or FakeAdapter(["codex"], discovery_responses=[["gpt-5.4", "gpt-5"]]),
        "gemini": gemini_adapter or FakeAdapter(["gemini"], discovery_responses=[["gemini-2.5-pro", "gemini-2.5-flash"]]),
        "claude": claude_adapter or FakeAdapter(["claude"], discovery_responses=[["default", "best", "sonnet", "opus", "haiku", "opus[1m]", "opusplan"]]),
    }



def _task_ready_for_completed_reviews(config, task_name: str):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = next(task for task in scanner.scan() if task.metadata.title == task_name)
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd: Path):
        (cwd / "app.txt").write_text("review me\n")

    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    asyncio.run(implementer.run_once())
    waiting_reviews = next(task for task in scanner.scan() if task.metadata.title == task_name and task.state == TaskState.WAITING_REVIEWS)
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    return scanner, completed



def _locate_task_dir(config, key: str) -> Path:
    for state_dir in config.kanban_root.iterdir():
        if not state_dir.is_dir() or state_dir.name == "_runtime":
            continue
        candidate = state_dir / key
        if candidate.exists():
            return candidate
    raise FileNotFoundError(key)
