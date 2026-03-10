from __future__ import annotations

from pathlib import Path

from fs_kanban_agent.commit_manager import CommitManager
from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.integration_manager import IntegrationManager
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.services.human_verification_service import HumanVerificationService
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workspace_manager import WorkspaceManager
from fs_kanban_agent.workers.implementer import ImplementerWorker

from .conftest import FakeAdapter, create_request_task, init_git_repo


def _task_ready_for_human_verification(config: AppConfig, *, workspace_side_effect=None):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd: Path):
        (cwd / "app.txt").write_text("review me\n")
        if workspace_side_effect is not None:
            workspace_side_effect(cwd)

    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )
    import asyncio

    asyncio.run(implementer.run_once())
    reviewing = transitions.move(scanner.scan()[0], TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    service = HumanVerificationService(scanner, metadata_store, locks, transitions, IntegrationManager(config), CommitManager())
    return scanner, service, completed


def test_human_verification_start_applies_patch_and_moves_state(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-start-task")
    scanner, service, completed = _task_ready_for_human_verification(config)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    assert scanner.find_task(completed.metadata.task_id).state == TaskState.HUMAN_VERIFYING
    assert (repo_root / "app.txt").read_text() == "review me\n"


def test_human_verification_start_includes_untracked_files(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-untracked-task")

    def create_untracked_file(cwd: Path):
        (cwd / "new-file.txt").write_text("brand new\n")

    scanner, service, completed = _task_ready_for_human_verification(config, workspace_side_effect=create_untracked_file)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    assert (repo_root / "new-file.txt").read_text() == "brand new\n"


def test_human_verification_reject_rolls_back_and_records_note(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-reject-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    moved = service.reject(completed.metadata.task_id, by="human", note="Please keep the old behavior.")

    assert moved.state == TaskState.TODOS
    assert scanner.find_task(completed.metadata.task_id).state == TaskState.TODOS
    assert (repo_root / "app.txt").read_text() == "hello\n"
    artifact = scanner.find_task(completed.metadata.task_id).task_dir / "HUMAN-VERIFY-000.md"
    assert artifact.exists()
    assert "Please keep the old behavior." in artifact.read_text()


def test_human_verification_approve_commits_and_moves_done(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    done = scanner.find_task(completed.metadata.task_id)
    assert done.state == TaskState.DONE
    assert done.metadata.commit.sha
