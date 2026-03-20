from __future__ import annotations

from datetime import timedelta

from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import LockError
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.recovery import RecoveryService
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.models import utc_now

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


def test_recovery_skips_locked_stale_tasks(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "locked-recover-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    implementing = transitions.move(
        transitions.manual_move(
            transitions.move(
                transitions.move(task, TaskState.PLANNING, by="planner"),
                TaskState.WAITING_CHECK_PLANS,
                by="planner",
            ).metadata.task_id,
            TaskState.TODOS,
            by="human",
        ),
        TaskState.IMPLEMENTING,
        by="implementer",
    )
    implementing.metadata.lease.owner = "implementer"
    implementing.metadata.lease.run_id = "locked"
    implementing.metadata.lease.heartbeat_at = utc_now() - timedelta(seconds=config.locks.stale_after_seconds + 5)
    metadata_store.save(implementing.task_dir, implementing.metadata)

    original_acquire = locks.acquire

    def flaky_acquire(task_dir, metadata, owner, run_id):
        if metadata.task_id == implementing.metadata.task_id:
            raise LockError("locked")
        return original_acquire(task_dir, metadata, owner, run_id)

    monkeypatch.setattr(locks, "acquire", flaky_acquire)

    recovery = RecoveryService(config, scanner, transitions, locks)
    events = recovery.recover()

    assert events == []
    assert scanner.scan()[0].state == TaskState.IMPLEMENTING
