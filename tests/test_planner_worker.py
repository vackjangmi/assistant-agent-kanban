from __future__ import annotations

import asyncio
import pytest

from fs_kanban_agent.exceptions import AdapterRunError
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workers.planner import PlanningWorker

from .conftest import FakeAdapter, create_request_task


def test_planner_worker_generates_plan(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter(["## Summary\nplan"]))

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    assert task.state == TaskState.WAITING_CHECK_PLANS
    assert (task.task_dir / "PLAN.md").exists()


def test_planner_worker_does_not_advance_on_failed_adapter(configured_paths):
    config, _, _ = configured_paths
    task_dir = create_request_task(config, "planner-failure-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["opencode run [message..]"], ok=False, returncode=1, stderr="planner failed"),
    )

    with pytest.raises(AdapterRunError):
        asyncio.run(worker.run_once())

    planning_task = scanner.scan()[0]
    assert planning_task.state == TaskState.PLANNING
    assert not (planning_task.task_dir / "PLAN.md").exists()
    assert planning_task.metadata.errors[-1].code == "planner-run-failed"
