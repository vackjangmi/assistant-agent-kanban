from __future__ import annotations

import pytest

from fs_kanban_agent.exceptions import LockError
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.scanner import KanbanScanner

from .conftest import create_request_task


def test_lock_manager_uses_stable_runtime_lock_path(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "lock-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    first = TaskLockManager(config)
    second = TaskLockManager(config)

    assert first.path_for(task.metadata.task_id).parent == config.locks_dir
    with first.acquire(task.task_dir, task.metadata, owner="a", run_id="1"):
        with pytest.raises(LockError):
            with second.acquire(task.task_dir, task.metadata, owner="b", run_id="2"):
                pass
