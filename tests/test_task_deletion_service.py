from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.exceptions import TransitionError
from fs_kanban_agent.integration_manager import IntegrationManager
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
    archive_run_dir = config.archive_runs_dir / task.metadata.task_id
    archive_run_dir.mkdir(parents=True)
    (archive_run_dir / "review-001.patch").write_text("patch\n")
    task.metadata.integration.patch_path = str(archive_run_dir / "review-001.patch")
    scanner.metadata_store.save(task.task_dir, task.metadata)
    lock_path = config.locks_dir / f"{task.metadata.task_id}.lock"
    lock_path.write_text("locked\n")

    service = TaskDeletionService(config, scanner, TaskLockManager(config), IntegrationManager(config))
    service.delete(task.metadata.task_id, by="human")

    assert not task_dir.exists()
    assert not workspace_root.exists()
    assert not run_dir.exists()
    assert not archive_run_dir.exists()
    assert not lock_path.exists()
    with pytest.raises(FileNotFoundError):
        scanner.find_task(task.metadata.task_id)


def test_task_deletion_service_allows_missing_runtime_artifacts(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "delete-missing-artifacts")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]

    service = TaskDeletionService(config, scanner, TaskLockManager(config), IntegrationManager(config))
    service.delete(task.metadata.task_id, by="human")

    with pytest.raises(FileNotFoundError):
        scanner.find_task(task.metadata.task_id)


def test_task_deletion_service_deletes_active_states(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "active-delete-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")

    service = TaskDeletionService(config, scanner, locks, IntegrationManager(config))
    service.delete(planning.metadata.task_id, by="human")

    with pytest.raises(FileNotFoundError):
        scanner.find_task(planning.metadata.task_id)


def test_task_deletion_service_deletes_human_verifying_state(configured_paths):
    config, _, _ = configured_paths
    task_dir = config.state_dir(TaskState.HUMAN_VERIFYING) / "manual-human-verifying-task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# task\n")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]

    service = TaskDeletionService(config, scanner, TaskLockManager(config), IntegrationManager(config))
    service.delete(task.metadata.task_id, by="human")

    with pytest.raises(FileNotFoundError):
        scanner.find_task(task.metadata.task_id)


def test_task_deletion_service_blocks_workspace_outside_managed_root(configured_paths, tmp_path: Path):
    config, _, _ = configured_paths
    create_request_task(config, "bad-workspace-delete-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.implementation.workspace = str((tmp_path / "outside-workspace").resolve())
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store), IntegrationManager(config))

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

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store), IntegrationManager(config))

    with pytest.raises(TransitionError, match="patch path is outside"):
        service.delete(task.metadata.task_id, by="human")


def test_task_deletion_service_rolls_back_applied_integration_and_docs(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "delete-applied-integration-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    review_branch = f"review/{task.metadata.task_id.lower()}"
    docs_root = repo_root / "docs" / "kanban-agent" / "2026" / "03" / "14" / task.metadata.task_id
    docs_root.mkdir(parents=True)
    (docs_root / "HUMAN-VERIFY-001.md").write_text("review note\n")
    subprocess.run(["git", "-C", str(repo_root), "branch", review_branch, "main"], check=True, capture_output=True, text=True)
    task.metadata.integration.applied = True
    task.metadata.integration.original_branch = "main"
    task.metadata.integration.review_branch = review_branch
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store), IntegrationManager(config))
    service.delete(task.metadata.task_id, by="human")

    assert not docs_root.exists()
    branch_check = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", review_branch], capture_output=True, text=True, check=False)
    assert branch_check.stdout.strip() == ""


def test_task_deletion_service_removes_docs_from_configured_target_docs_root(configured_paths):
    config, repo_root, _ = configured_paths
    config.target_repo_docs_root = "records/kanban-docs"
    create_request_task(config, "delete-configured-doc-root-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    review_branch = f"review/{task.metadata.task_id.lower()}"
    docs_root = repo_root / "records" / "kanban-docs" / "2026" / "03" / "14" / task.metadata.task_id
    docs_root.mkdir(parents=True)
    (docs_root / "HUMAN-VERIFY-001.md").write_text("review note\n")
    subprocess.run(["git", "-C", str(repo_root), "branch", review_branch, "main"], check=True, capture_output=True, text=True)
    task.metadata.integration.applied = True
    task.metadata.integration.original_branch = "main"
    task.metadata.integration.review_branch = review_branch
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store), IntegrationManager(config))
    service.delete(task.metadata.task_id, by="human")

    assert not docs_root.exists()
