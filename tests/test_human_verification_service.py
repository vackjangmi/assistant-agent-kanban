from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fs_kanban_agent.commit_manager import CommitManager
from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.exceptions import IntegrationError
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
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    updated = scanner.find_task(completed.metadata.task_id)
    assert current_branch == f"review/{updated.metadata.task_id.lower()}"
    assert updated.metadata.integration.original_branch == "main"
    assert updated.metadata.integration.review_branch == current_branch
    assert updated.metadata.commit.message_path == "COMMIT.md"
    assert (updated.task_dir / "COMMIT.md").exists()


def test_human_verification_start_includes_untracked_files(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-untracked-task")

    def create_untracked_file(cwd: Path):
        (cwd / "new-file.txt").write_text("brand new\n")

    scanner, service, completed = _task_ready_for_human_verification(config, workspace_side_effect=create_untracked_file)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    assert (repo_root / "new-file.txt").read_text() == "brand new\n"


def test_human_verification_start_uses_absolute_patch_path_from_relative_config(monkeypatch, tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    monkeypatch.chdir(tmp_path)
    config = AppConfig(kanban_root=Path("ai-kanban"), repo_root=target_repo)
    config.bootstrap()
    create_request_task(config, "verify-relative-patch-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    updated = scanner.find_task(completed.metadata.task_id)
    assert updated.metadata.integration.patch_path == str(
        (tmp_path / "ai-kanban" / "_runtime" / "runs" / updated.metadata.task_id / "review-000.patch").resolve()
    )
    assert (target_repo / "app.txt").read_text() == "review me\n"


def test_human_verification_reject_rolls_back_and_records_note(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-reject-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    moved = service.reject(completed.metadata.task_id, by="human", note="Please keep the old behavior.")

    assert moved.state == TaskState.TODOS
    assert scanner.find_task(completed.metadata.task_id).state == TaskState.TODOS
    assert (repo_root / "app.txt").read_text() == "hello\n"
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    assert branches == ""
    artifact = scanner.find_task(completed.metadata.task_id).task_dir / "HUMAN-VERIFY-000.md"
    assert artifact.exists()
    assert "Please keep the old behavior." in artifact.read_text()


def test_human_verification_reject_fails_when_patch_file_is_missing_and_repo_is_dirty(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-reject-missing-patch-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    task = scanner.find_task(completed.metadata.task_id)
    patch_path = Path(task.metadata.integration.patch_path or "")
    patch_path.unlink()

    with pytest.raises(IntegrationError, match="patch artifact is missing"):
        service.reject(completed.metadata.task_id, by="human", note="Patch artifact disappeared.")

    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.HUMAN_VERIFYING
    assert refreshed.metadata.integration.original_branch == "main"
    assert refreshed.metadata.integration.review_branch == f"review/{refreshed.metadata.task_id.lower()}"
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == f"review/{refreshed.metadata.task_id.lower()}"
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True).stdout
    assert status.strip() != ""
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    assert branches != ""


def test_human_verification_start_cleans_up_review_branch_on_apply_failure(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-start-failure-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    patch_path = config.runs_dir / completed.metadata.task_id / "review-000.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text("this is not a valid patch\n")
    completed.metadata.integration.patch_path = str(patch_path)
    scanner.metadata_store.save(completed.task_dir, completed.metadata)

    original_apply = service.integration_manager.apply_workspace

    def fail_after_branch(metadata, workspace_repo):
        integration = service.integration_manager
        original_branch = integration._current_branch(repo_root) or metadata.target.base_branch
        review_branch = integration._review_branch_name(metadata)
        integration._switch_to_review_branch(repo_root, original_branch, review_branch)
        metadata.integration.original_branch = original_branch
        metadata.integration.review_branch = review_branch
        integration._restore_original_branch(repo_root, original_branch)
        integration._delete_branch(repo_root, review_branch)
        metadata.integration.original_branch = None
        metadata.integration.review_branch = None
        raise IntegrationError("failed to apply patch")

    service.integration_manager.apply_workspace = fail_after_branch

    with pytest.raises(IntegrationError, match="failed to apply patch"):
        service.start(completed.metadata.task_id, by="human")

    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert current_branch == "main"
    assert branches == ""
    assert refreshed.state == TaskState.COMPLETED_REVIEWS
    assert refreshed.metadata.integration.original_branch is None
    assert refreshed.metadata.integration.review_branch is None
    service.integration_manager.apply_workspace = original_apply


def test_human_verification_start_rolls_back_when_post_apply_step_fails(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-start-post-apply-failure-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    original_prepare = service.commit_manager.prepare_commit_message

    def fail_prepare(*args, **kwargs):
        raise RuntimeError("commit message preparation failed")

    service.commit_manager.prepare_commit_message = fail_prepare

    with pytest.raises(RuntimeError, match="commit message preparation failed"):
        service.start(completed.metadata.task_id, by="human")

    refreshed = scanner.find_task(completed.metadata.task_id)
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert refreshed.state == TaskState.COMPLETED_REVIEWS
    assert current_branch == "main"
    assert branches == ""
    assert status == ""
    assert refreshed.metadata.integration.applied is False
    assert refreshed.metadata.integration.original_branch is None
    assert refreshed.metadata.integration.review_branch is None
    service.commit_manager.prepare_commit_message = original_prepare


def test_human_verification_start_rolls_back_when_transition_save_fails(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-start-transition-failure-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    original_save = service.transitions.metadata_store.save

    def fail_human_verifying_save(task_dir, metadata):
        if task_dir.parent.name == TaskState.HUMAN_VERIFYING.value:
            raise RuntimeError("transition save failed")
        return original_save(task_dir, metadata)

    service.transitions.metadata_store.save = fail_human_verifying_save

    with pytest.raises(RuntimeError, match="transition save failed"):
        service.start(completed.metadata.task_id, by="human")

    refreshed = scanner.find_task(completed.metadata.task_id)
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert refreshed.state == TaskState.COMPLETED_REVIEWS
    assert refreshed.task_dir.parent.name == TaskState.COMPLETED_REVIEWS.value
    assert current_branch == "main"
    assert branches == ""
    assert status == ""
    assert refreshed.metadata.integration.applied is False
    assert refreshed.metadata.integration.original_branch is None
    assert refreshed.metadata.integration.review_branch is None
    service.transitions.metadata_store.save = original_save


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
    assert done.metadata.commit.prepared_message == f"feat: {done.metadata.title}"
    assert (done.task_dir / "COMMIT.md").read_text().strip() == f"feat: {done.metadata.title}"
    current_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == f"review/{done.metadata.task_id.lower()}"


def test_human_verification_approve_switches_back_to_review_branch_before_commit(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-branch-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    subprocess.run(["git", "-C", str(target_repo), "switch", "main"], check=True, capture_output=True, text=True)

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    current_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == f"review/{moved.metadata.task_id.lower()}"
