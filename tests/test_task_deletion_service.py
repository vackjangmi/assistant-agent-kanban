from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from assistant_agent_kanban.config import PROJECT_ROOT
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import TransitionError
from assistant_agent_kanban.integration_manager import IntegrationManager
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.task_deletion_service import TaskDeletionService
from assistant_agent_kanban.services.task_service import TaskService
from assistant_agent_kanban.transitions import TransitionManager

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
    docs_root = repo_root / "docs" / "kanban-agent" / "2026" / "03" / "14"
    docs_root.mkdir(parents=True)
    (docs_root / f"{task.metadata.task_id}-summary.md").write_text("summary\n")
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
    docs_root = repo_root / "records" / "kanban-docs" / "2026" / "03" / "14"
    docs_root.mkdir(parents=True)
    (docs_root / f"{task.metadata.task_id}-summary.md").write_text("summary\n")
    subprocess.run(["git", "-C", str(repo_root), "branch", review_branch, "main"], check=True, capture_output=True, text=True)
    task.metadata.integration.applied = True
    task.metadata.integration.original_branch = "main"
    task.metadata.integration.review_branch = review_branch
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store), IntegrationManager(config))
    service.delete(task.metadata.task_id, by="human")

    assert not docs_root.exists()


def test_task_deletion_service_removes_legacy_and_semantic_summary_files(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "delete-semantic-summary-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.integration.final_branch_summary = "semantic-summary"
    review_branch = f"review/{task.metadata.task_id.lower()}"
    metadata_store.save(task.task_dir, task.metadata)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    semantic_summary_path = task_service.target_repo_summary_path(task.metadata, created_at=datetime(2026, 3, 14, tzinfo=timezone.utc))
    legacy_summary_path = task_service.legacy_target_repo_summary_path(task.metadata, created_at=datetime(2026, 3, 14, tzinfo=timezone.utc))
    semantic_summary_path.parent.mkdir(parents=True)
    semantic_summary_path.write_text("semantic\n")
    legacy_summary_path.write_text("legacy\n")
    subprocess.run(["git", "-C", str(repo_root), "branch", review_branch, "main"], check=True, capture_output=True, text=True)
    task.metadata.integration.applied = True
    task.metadata.integration.original_branch = "main"
    task.metadata.integration.review_branch = review_branch
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store), IntegrationManager(config))
    service.delete(task.metadata.task_id, by="human")

    assert not semantic_summary_path.exists()
    assert not legacy_summary_path.exists()
    assert not semantic_summary_path.parent.exists()


def test_task_deletion_service_allows_delete_when_target_repo_overlaps_orchestrator(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "delete-overlapping-target-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    sentinel_docs_root = PROJECT_ROOT / "docs" / "kanban-agent" / "2099" / "12" / "31" / task.metadata.task_id
    sentinel_docs_root.mkdir(parents=True, exist_ok=True)
    (sentinel_docs_root / "HUMAN-VERIFY-001.md").write_text("do not delete\n")
    task.metadata.target.repo_root = str(PROJECT_ROOT)
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskDeletionService(config, scanner, TaskLockManager(config, metadata_store), IntegrationManager(config))
    try:
        service.delete(task.metadata.task_id, by="human")

        with pytest.raises(FileNotFoundError):
            scanner.find_task(task.metadata.task_id)
        assert sentinel_docs_root.exists()
    finally:
        if sentinel_docs_root.exists():
            for path in sorted(sentinel_docs_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            sentinel_docs_root.rmdir()
