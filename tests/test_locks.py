from __future__ import annotations

import pytest

from assistant_agent_kanban.exceptions import LockError
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner

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


def test_lock_manager_allows_raw_task_id_locking(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "raw-lock-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    first = TaskLockManager(config)
    second = TaskLockManager(config)

    with first.acquire_by_task_id(task.metadata.task_id, owner="delete", run_id="raw-1"):
        with pytest.raises(LockError):
            with second.acquire_by_task_id(task.metadata.task_id, owner="other", run_id="raw-2"):
                pass


def test_lock_manager_resolves_missing_task_dir_without_runtime_metadata(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "resolve-task")
    scanner = KanbanScanner(config, MetadataStore())
    task = scanner.scan()[0]
    runtime_metadata = config.kanban_root / "_runtime" / "workspaces" / task.metadata.task_id / "metadata.json"
    runtime_metadata.parent.mkdir(parents=True, exist_ok=True)
    runtime_metadata.write_text((task.task_dir / "metadata.json").read_text())
    missing_task_dir = task.task_dir.parent / "missing-task-dir"

    resolved = TaskLockManager(config, MetadataStore())._resolve_task_dir(missing_task_dir, task.metadata.task_id)

    assert resolved == task.task_dir
