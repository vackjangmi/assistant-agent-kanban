from __future__ import annotations

from datetime import datetime, timezone
import subprocess
from pathlib import Path

import pytest

from fs_kanban_agent.commit_manager import CommitManager
from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.exceptions import IntegrationError, TransitionError
from fs_kanban_agent.integration_manager import IntegrationManager
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.services.task_service import TaskService
from fs_kanban_agent.services.human_verification_service import HumanVerificationService
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workspace_manager import WorkspaceManager
from fs_kanban_agent.workers.implementer import ImplementerWorker

from .conftest import FakeAdapter, create_request_task, init_git_repo


def _task_ready_for_human_verification(config: AppConfig, *, workspace_side_effect=None, branch_summary_adapter=None):
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
    service = HumanVerificationService(scanner, config, metadata_store, locks, transitions, IntegrationManager(config), CommitManager(), branch_summary_adapter=branch_summary_adapter)
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
    assert updated.metadata.integration.final_branch_summary == "verify-start-task"
    assert (updated.task_dir / "COMMIT.md").exists()
    assert updated.metadata.commit.status == "review-committed"
    assert updated.metadata.commit.sha
    committed_message = subprocess.run(["git", "-C", str(repo_root), "log", "-1", "--pretty=%B"], check=True, capture_output=True, text=True).stdout.strip()
    assert committed_message == (updated.task_dir / "COMMIT.md").read_text().strip()
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert status == ""


def test_human_verification_start_includes_untracked_files(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-untracked-task")

    def create_untracked_file(cwd: Path):
        (cwd / "new-file.txt").write_text("brand new\n")

    scanner, service, completed = _task_ready_for_human_verification(config, workspace_side_effect=create_untracked_file)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    assert (repo_root / "new-file.txt").read_text() == "brand new\n"
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root)
    detail = task_service.get_task(completed.metadata.task_id)
    added_file = next(file for file in detail.changed_files if file.path == "new-file.txt")
    assert added_file.change_type == "added"
    assert added_file.additions == 1
    diff = task_service.get_changed_file(completed.metadata.task_id, added_file.id)
    assert diff.summary.path == "new-file.txt"
    assert diff.hunks[0].unified_lines[0].kind == "add"
    assert diff.hunks[0].unified_lines[0].content == "brand new"


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
        (tmp_path / "ai-kanban" / "_runtime" / "runs" / updated.metadata.task_id / "review-001.patch").resolve()
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
    rejected = scanner.find_task(completed.metadata.task_id)
    assert rejected.metadata.commit.status == "pending"
    assert rejected.metadata.commit.sha is None
    artifact = rejected.task_dir / "HUMAN-VERIFY-001.md"
    assert artifact.exists()
    assert "Please keep the old behavior." in artifact.read_text()


def test_human_verification_reject_discards_review_branch_even_when_patch_file_is_missing(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-reject-missing-patch-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    task = scanner.find_task(completed.metadata.task_id)
    patch_path = Path(task.metadata.integration.patch_path or "")
    patch_path.unlink()
    (repo_root / "app.txt").write_text("review me\nmanual tweak\n")
    (repo_root / "notes.txt").write_text("temporary review note\n")

    moved = service.reject(completed.metadata.task_id, by="human", note="Patch artifact disappeared.")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert refreshed.metadata.integration.original_branch is None
    assert refreshed.metadata.integration.review_branch is None
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert status == ""
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    assert branches == ""


def test_human_verification_reject_preserves_human_reviewed_code_in_workspace(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-reject-preserve-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    (target_repo / "app.txt").write_text("review me\nhuman tweak\n")
    (target_repo / "extra.txt").write_text("keep this for next iteration\n")

    moved = service.reject(completed.metadata.task_id, by="human", note="carry these edits forward")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    workspace_repo = Path(refreshed.metadata.implementation.workspace or "")
    assert (workspace_repo / "app.txt").read_text() == "review me\nhuman tweak\n"
    assert (workspace_repo / "extra.txt").read_text() == "keep this for next iteration\n"


def test_human_verification_adds_line_comments_and_rewrites_artifacts(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-line-comment-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    moved = service.add_line_comment(
        completed.metadata.task_id,
        by="human",
        path="app.txt",
        side="right",
        line_number=1,
        line_kind="add",
        hunk_header="@@ -1 +1 @@",
        body_markdown="Please keep this rename but adjust the copy.",
    )

    assert moved.state == TaskState.HUMAN_VERIFYING
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.human_verification.comments_path == "HUMAN-VERIFY-001.comments.json"
    comments_path = refreshed.task_dir / "HUMAN-VERIFY-001.comments.json"
    assert comments_path.exists()
    artifact = (refreshed.task_dir / "HUMAN-VERIFY-001.md").read_text()
    assert "## Line Comments" in artifact
    assert "Please keep this rename but adjust the copy." in artifact

    task_service = TaskService(scanner, config.runs_dir, config.kanban_root)
    detail = task_service.get_task(completed.metadata.task_id)
    assert detail.human_review.total_comment_count == 1
    assert detail.human_review.unresolved_comment_count == 1
    changed_file = next(file for file in detail.changed_files if file.path == "app.txt")
    diff = task_service.get_changed_file(completed.metadata.task_id, changed_file.id)
    assert len(diff.comments) == 1
    assert diff.comments[0].anchor.path == "app.txt"
    assert diff.comments[0].anchor.side == "right"
    assert diff.comments[0].anchor.line_number == 1
    assert diff.comments[0].body_markdown == "Please keep this rename but adjust the copy."


def test_human_verification_deletes_line_comments_and_rewrites_artifacts(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-line-comment-delete-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    service.add_line_comment(
        completed.metadata.task_id,
        by="human",
        path="app.txt",
        side="right",
        line_number=1,
        line_kind="add",
        hunk_header="@@ -1 +1 @@",
        body_markdown="Please keep this rename but adjust the copy.",
    )
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root)
    detail_before = task_service.get_task(completed.metadata.task_id)
    changed_file = next(file for file in detail_before.changed_files if file.path == "app.txt")
    diff_before = task_service.get_changed_file(completed.metadata.task_id, changed_file.id)

    moved = service.delete_line_comment(
        completed.metadata.task_id,
        by="human",
        comment_id=diff_before.comments[0].id,
    )

    assert moved.state == TaskState.HUMAN_VERIFYING
    refreshed = scanner.find_task(completed.metadata.task_id)
    artifact = (refreshed.task_dir / "HUMAN-VERIFY-001.md").read_text()
    assert "No unresolved comments." in artifact
    diff_after = task_service.get_changed_file(completed.metadata.task_id, changed_file.id)
    assert diff_after.comments == []
    detail_after = task_service.get_task(completed.metadata.task_id)
    assert detail_after.human_review.total_comment_count == 0
    assert detail_after.human_review.unresolved_comment_count == 0


def test_human_verification_approval_is_blocked_when_line_comments_remain(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-line-comment-approval-block-task")
    _, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    service.add_line_comment(
        completed.metadata.task_id,
        by="human",
        path="app.txt",
        side="right",
        line_number=1,
        line_kind="add",
        hunk_header="@@ -1 +1 @@",
        body_markdown="Please fix this before approval.",
    )

    with pytest.raises(TransitionError, match="approval is blocked until all inline comments are removed"):
        service.approve(completed.metadata.task_id, by="human")


def test_human_verification_start_cleans_up_review_branch_on_apply_failure(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-start-failure-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    patch_path = config.runs_dir / completed.metadata.task_id / "review-001.patch"
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


def test_human_verification_start_returns_to_todos_on_integration_conflict(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-start-conflict-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert any(error.code == "integration-conflict" for error in refreshed.metadata.errors)
    assert refreshed.metadata.commit.status == "pending"
    assert refreshed.metadata.commit.sha is None
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    assert branches == ""
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert status == ""
    artifact = refreshed.task_dir / "HUMAN-VERIFY-001.md"
    assert artifact.exists()
    assert "Verdict: CONFLICT" in artifact.read_text()


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
    assert refreshed.metadata.commit.status == "pending"
    assert refreshed.metadata.commit.sha is None
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
    assert refreshed.metadata.commit.status == "pending"
    assert refreshed.metadata.commit.sha is None
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
    review_sha = scanner.find_task(completed.metadata.task_id).metadata.commit.sha

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    done = scanner.find_task(completed.metadata.task_id)
    expected_final_branch = f"feature/{done.metadata.task_id.lower()}-{done.metadata.slug}"
    assert done.state == TaskState.DONE
    assert done.metadata.commit.sha
    assert done.metadata.commit.review_sha == review_sha
    assert done.metadata.integration.final_branch == expected_final_branch
    assert done.metadata.integration.review_branch is None
    assert done.metadata.integration.original_branch is None
    expected_message = "\n".join(
        [
            f"feat: {done.metadata.title}",
            "",
            f"Goal: Implement {done.metadata.title}.",
            "Plan: plan",
            f"Task: {done.metadata.task_id}",
        ]
    )
    assert done.metadata.commit.prepared_message == expected_message
    assert (done.task_dir / "COMMIT.md").read_text().strip() == expected_message
    git_message = subprocess.run(["git", "-C", str(target_repo), "log", "-1", "--pretty=%B"], check=True, capture_output=True, text=True).stdout.strip()
    assert git_message == expected_message
    current_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == expected_final_branch
    review_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--list", f"review/{done.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    assert review_branch == ""
    review_date = datetime.now(timezone.utc)
    docs_root = target_repo / "docs" / "ai-kanban" / f"{review_date.year:04d}" / f"{review_date.month:02d}" / f"{review_date.day:02d}" / done.metadata.task_id
    assert (docs_root / "REQUEST.md").exists()
    assert (docs_root / "PLAN.md").exists()
    assert (docs_root / "HUMAN-VERIFY-001.md").exists()
    assert (docs_root / "COMMIT.md").exists()


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
    assert current_branch == f"feature/{moved.metadata.task_id.lower()}-{moved.metadata.slug}"


def test_human_verification_approve_stages_manual_review_changes_before_commit(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-manual-edit-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    tracked_file = target_repo / "app.txt"
    tracked_file.write_text("review me\nmanual tweak\n")
    new_file = target_repo / "notes.txt"
    new_file.write_text("human review note\n")

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    show = subprocess.run(["git", "-C", str(target_repo), "show", "--stat", "--format=%B", "HEAD"], check=True, capture_output=True, text=True).stdout
    assert "manual tweak" in (target_repo / "app.txt").read_text()
    assert "notes.txt" in show
    status = subprocess.run(["git", "-C", str(target_repo), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert status == ""


def test_human_verification_approve_returns_to_todos_on_rebase_conflict(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-conflict-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    subprocess.run(["git", "-C", str(target_repo), "switch", "main"], check=True, capture_output=True, text=True)
    (target_repo / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert refreshed.metadata.commit.sha is None
    assert refreshed.metadata.commit.review_sha is None
    assert refreshed.metadata.integration.final_branch is None
    assert any(error.code == "human-verification-finalize-failed" for error in refreshed.metadata.errors)
    current_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    status = subprocess.run(["git", "-C", str(target_repo), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert status == ""
    review_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    final_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--list", f"feature/{completed.metadata.task_id.lower()}-{completed.metadata.slug}"], check=True, capture_output=True, text=True).stdout.strip()
    assert review_branch == ""
    assert final_branch == ""


def test_human_verification_approve_uses_task_id_suffix_when_final_branch_exists(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-collision-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    subprocess.run(["git", "-C", str(target_repo), "switch", "-c", f"feature/{completed.metadata.task_id.lower()}-{completed.metadata.slug}"], check=True, capture_output=True, text=True)
    (target_repo / "collision.txt").write_text("existing branch\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "collision.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "existing branch"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "switch", "main"], check=True, capture_output=True, text=True)
    service.start(completed.metadata.task_id, by="human")

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.final_branch == f"feature/{completed.metadata.task_id.lower()}-{completed.metadata.slug}-{completed.metadata.task_id.lower()}"


def test_human_verification_approve_romanizes_korean_title_for_final_branch(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "면적 게임모드 추가", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.final_branch is not None
    assert refreshed.metadata.integration.final_branch == f"feature/{completed.metadata.task_id.lower()}-myeonjeok-geimmodeu-chuga"


def test_human_verification_start_generates_english_branch_summary_with_adapter(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "면적 게임모드 추가", target_repo_root=target_repo)
    branch_summary_adapter = FakeAdapter(["add-area-game-mode"])
    scanner, service, completed = _task_ready_for_human_verification(config, branch_summary_adapter=branch_summary_adapter)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    updated = scanner.find_task(completed.metadata.task_id)
    assert updated.metadata.integration.final_branch_summary == "add-area-game-mode"
    assert branch_summary_adapter.run_calls[0]["agent"] == "planner"


def test_human_verification_approve_uses_stored_english_branch_summary(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "면적 게임모드 추가", target_repo_root=target_repo)
    branch_summary_adapter = FakeAdapter(["add-area-game-mode"])
    scanner, service, completed = _task_ready_for_human_verification(config, branch_summary_adapter=branch_summary_adapter)
    service.start(completed.metadata.task_id, by="human")

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.final_branch == f"feature/{completed.metadata.task_id.lower()}-add-area-game-mode"
