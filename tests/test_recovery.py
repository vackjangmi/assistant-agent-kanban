from __future__ import annotations

from datetime import timedelta

from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.recovery import RecoveryService
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.models import utc_now

from .conftest import create_request_task


def test_recovery_moves_stale_implementing_tasks_back_to_todos(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "recover-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.lease.owner = "implementer"
    implementing.metadata.lease.run_id = "old"
    implementing.metadata.lease.heartbeat_at = utc_now() - timedelta(seconds=config.locks.stale_after_seconds + 5)
    metadata_store.save(implementing.task_dir, implementing.metadata)

    recovery = RecoveryService(config, scanner, transitions, locks)
    events = recovery.recover()

    assert events
    assert scanner.scan()[0].state == TaskState.TODOS
