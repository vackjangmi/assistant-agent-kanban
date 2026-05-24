from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import TransitionError
from assistant_agent_kanban.integration_manager import IntegrationManager
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.task_cancellation_service import TaskCancellationService
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workspace_manager import WorkspaceManager

from .conftest import create_request_task


def _move_to_implementing(config, task_name: str):
    create_request_task(config, task_name)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    return scanner, locks, transitions, transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")


def test_task_cancellation_archives_workspace_changes_and_removes_workspace(configured_paths):
    config, _, _ = configured_paths
    scanner, locks, transitions, implementing = _move_to_implementing(config, "cancel-workspace-task")
    repo_dir = WorkspaceManager(config).prepare(implementing.metadata)
    (repo_dir / "app.txt").write_text("changed by cancelled work\n")
    (repo_dir / "notes.txt").write_text("untracked note\n")
    scanner.metadata_store.save(implementing.task_dir, implementing.metadata)
    workspace_root = config.workspace.root / implementing.metadata.task_id

    service = TaskCancellationService(config, scanner, locks, transitions, IntegrationManager(config))
    cancelled = service.cancel(implementing.metadata.task_id, by="human")

    assert cancelled.state == TaskState.CLOSED
    assert cancelled.task_dir.parent == config.state_dir(TaskState.CLOSED)
    assert cancelled.metadata.closure.reason == "cancelled_by_human"
    assert cancelled.metadata.closure.closed_by == "human"
    assert cancelled.metadata.implementation.workspace is None
    assert cancelled.metadata.implementation.branch is None
    assert not workspace_root.exists()
    archive_dir = cancelled.task_dir / "CANCELLED-WORKSPACE"
    assert (archive_dir / "README.md").exists()
    assert "Cancelled workspace changes" in cancelled.metadata.closure.note
    assert "changed by cancelled work" in (archive_dir / "changes.patch").read_text()
    assert (archive_dir / "files" / "notes.txt").read_text() == "untracked note\n"


def test_task_cancellation_removes_workspace_without_archive_when_clean(configured_paths):
    config, _, _ = configured_paths
    scanner, locks, transitions, implementing = _move_to_implementing(config, "cancel-clean-task")
    repo_dir = WorkspaceManager(config).prepare(implementing.metadata)
    scanner.metadata_store.save(implementing.task_dir, implementing.metadata)
    workspace_root = repo_dir.parent

    service = TaskCancellationService(config, scanner, locks, transitions, IntegrationManager(config))
    cancelled = service.cancel(implementing.metadata.task_id, by="human")

    assert cancelled.state == TaskState.CLOSED
    assert not workspace_root.exists()
    assert not (cancelled.task_dir / "CANCELLED-WORKSPACE").exists()


def test_task_cancellation_blocks_done_tasks(configured_paths):
    config, _, _ = configured_paths
    metadata_store = MetadataStore()
    task_dir = config.state_dir(TaskState.DONE) / "2026" / "03" / "14" / "done-cancel-task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# done\n")
    metadata_store.bootstrap(
        task_dir,
        TaskState.DONE,
        "abc1234",
        "done-cancel-task",
        "done-cancel-task",
        target_repo_root=str(config.repo_root),
        base_branch=config.base_branch,
    )
    scanner = KanbanScanner(config, metadata_store)
    service = TaskCancellationService(
        config,
        scanner,
        TaskLockManager(config, metadata_store),
        TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store)),
        IntegrationManager(config),
    )

    task = scanner.scan()[0]
    with pytest.raises(TransitionError, match="not allowed"):
        service.cancel(task.metadata.task_id, by="human")


def test_task_cancellation_rolls_back_applied_integration(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "cancel-applied-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    completed_dir = config.state_dir(TaskState.HUMAN_VERIFYING) / task.task_dir.name
    task.task_dir.rename(completed_dir)
    task.task_dir = completed_dir
    task.state = TaskState.HUMAN_VERIFYING
    task.metadata.state = TaskState.HUMAN_VERIFYING
    review_branch = f"review/{task.metadata.task_id.lower()}"
    subprocess.run(["git", "-C", str(repo_root), "branch", review_branch, "main"], check=True, capture_output=True, text=True)
    docs_root = repo_root / "docs" / "kanban-agent" / "2026" / "03" / "14"
    docs_root.mkdir(parents=True)
    (docs_root / f"{task.metadata.task_id}-summary.md").write_text("summary\n")
    task.metadata.integration.applied = True
    task.metadata.integration.original_branch = "main"
    task.metadata.integration.review_branch = review_branch
    metadata_store.save(task.task_dir, task.metadata)

    service = TaskCancellationService(config, scanner, locks, transitions, IntegrationManager(config))
    cancelled = service.cancel(task.metadata.task_id, by="human")

    assert cancelled.state == TaskState.CLOSED
    assert not docs_root.exists()
    branch_check = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", review_branch], capture_output=True, text=True, check=False)
    assert branch_check.stdout.strip() == ""
