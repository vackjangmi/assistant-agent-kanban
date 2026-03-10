from __future__ import annotations

import asyncio

from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workspace_manager import WorkspaceManager
from fs_kanban_agent.workers.implementer import ImplementerWorker

from .conftest import FakeAdapter, create_request_task


def test_implementer_worker_uses_external_workspace(configured_paths):
    config, _, _ = configured_paths
    task_dir = create_request_task(config, "implement-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.WAITING_REVIEWS
    assert updated.metadata.implementation.workspace is not None
    assert str(task_dir) not in updated.metadata.implementation.workspace
    assert (updated.task_dir / "WORK-001.md").exists()
