from __future__ import annotations

from pathlib import Path

import pytest

from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.exceptions import TransitionError
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.services.task_deletion_service import TaskDeletionService
from fs_kanban_agent.transitions import TransitionManager

from .conftest import create_request_task


def test_task_deletion_service_removes_task_runtime_artifacts_and_lock(configured_paths):
    config, _, _ = configured_paths
    task_dir = create_request_task(config, "delete-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    workspace_root = config.workspace.root / task.metadata.task_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "repo").mkdir()
    run_dir = config.runs_dir / task.metadata.task_id
    run_dir.mkdir(parents=True)
    (run_dir / "planner-001.jsonl").write_text("log\n")
    lock_path = config.locks_dir / f"{task.metadata.task_id}.lock"
    lock_path.write_text("locked\n")

    service = TaskDeletionService(config, scanner, TaskLockManager(config))
    service.delete(task.metadata.task_id, by="human")

    assert not task_dir.exists()
    assert not workspace_root.exists()
    assert not run_dir.exists()
    assert not lock_path.exists()
    with pytest.raises(FileNotFoundError):
        scanner.find_task(task.metadata.task_id)


def test_task_deletion_service_allows_missing_runtime_artifacts(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "delete-missing-artifacts")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]

    service = TaskDeletionService(config, scanner, TaskLockManager(config))
    service.delete(task.metadata.task_id, by="human")

    with pytest.raises(FileNotFoundError):
        scanner.find_task(task.metadata.task_id)


def test_task_deletion_service_blocks_active_states(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "active-delete-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")

    service = TaskDeletionService(config, scanner, locks)

    with pytest.raises(TransitionError, match="blocked while state is planning"):
        service.delete(planning.metadata.task_id, by="human")


def test_task_deletion_service_blocks_human_verifying_state(configured_paths):
    config, _, _ = configured_paths
    task_dir = config.state_dir(TaskState.HUMAN_VERIFYING) / "manual-human-verifying-task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# task\n")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]

    service = TaskDeletionService(config, scanner, TaskLockManager(config))

    with pytest.raises(TransitionError, match="blocked while state is human-verifying"):
        service.delete(task.metadata.task_id, by="human")


def test_task_deletion_service_blocks_workspace_outside_managed_root(configured_paths, tmp_path: Path):
    config, _, _ = configured_paths
    create_request_task(config, "bad-workspace-delete-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.implementation.workspace = str((tmp_path / "outside-workspace").resolve())
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store))

    with pytest.raises(TransitionError, match="workspace path is outside"):
        service.delete(task.metadata.task_id, by="human")


def test_task_deletion_service_blocks_patch_outside_managed_runs_root(configured_paths, tmp_path: Path):
    config, _, _ = configured_paths
    create_request_task(config, "bad-patch-delete-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.integration.patch_path = str((tmp_path / "outside.patch").resolve())
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store))

    with pytest.raises(TransitionError, match="patch path is outside"):
        service.delete(task.metadata.task_id, by="human")
