from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import subprocess
from pathlib import Path
from threading import Event

import pytest

from assistant_agent_kanban.commit_manager import CommitManager
from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.exceptions import IntegrationError, TransitionError
from assistant_agent_kanban.integration_manager import IntegrationManager
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import HumanLineComment, HumanLineCommentAnchor, HumanLineCommentsArtifact
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.task_service import TaskService
from assistant_agent_kanban.services.human_verification_service import HumanVerificationService
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workspace_manager import WorkspaceManager
from assistant_agent_kanban.workers.implementer import ImplementerWorker

from .conftest import FakeAdapter, create_request_task, init_git_repo


def _task_ready_for_human_verification(config: AppConfig, *, workspace_side_effect=None, branch_summary_adapter=None):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = next(item for item in scanner.scan() if item.state == TaskState.REQUESTS)
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
        adapter=FakeAdapter(
            ["## Summary\nimplemented"],
            side_effect=modify_workspace,
            side_effect_output_formats={"json", "default"},
        ),
        workspace_manager=WorkspaceManager(config),
    )
    import asyncio

    asyncio.run(implementer.run_task(scanner.find_task(waiting.metadata.task_id)))
    reviewing = transitions.move(scanner.find_task(waiting.metadata.task_id), TaskState.REVIEWING, by="reviewer")
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
    assert updated.metadata.commit.review_sha is not None
    assert updated.metadata.commit.sha == updated.metadata.commit.review_sha
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


def test_human_verification_start_returns_to_todos_when_target_base_branch_advances(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-drift-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    (repo_root / "app.txt").write_text("changed outside workspace\n")
    subprocess.run(["git", "-C", str(repo_root), "commit", "-am", "advance main"], check=True, capture_output=True, text=True)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert refreshed.metadata.retry_gate.reason == "verification-target-repo-drift"
    assert any(error.code == "verification-target-repo-drift" for error in refreshed.metadata.errors)


def test_human_verification_start_returns_to_todos_when_target_repo_becomes_dirty_on_main(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-dirty-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    (repo_root / "app.txt").write_text("dirty outside workspace\n")

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert refreshed.metadata.retry_gate.reason == "verification-target-repo-drift"
    assert any(error.code == "verification-target-repo-drift" for error in refreshed.metadata.errors)


def test_human_verification_start_does_not_auto_reject_other_review_branch_dirt(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-other-review-branch-dirty-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    subprocess.run(["git", "-C", str(repo_root), "switch", "-c", "review/other-task"], check=True, capture_output=True, text=True)
    (repo_root / "app.txt").write_text("human review edits\n")

    with pytest.raises(IntegrationError, match="target repo must be clean before apply"):
        service.start(completed.metadata.task_id, by="human")

    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.COMPLETED_REVIEWS
    assert not any(error.code == "verification-target-repo-drift" for error in refreshed.metadata.errors)


def test_human_verification_start_uses_absolute_patch_path_from_relative_config(monkeypatch, tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    monkeypatch.chdir(tmp_path)
    config = AppConfig(kanban_root=Path(".kanban-agent"), repo_root=target_repo)
    config.bootstrap()
    create_request_task(config, "verify-relative-patch-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    updated = scanner.find_task(completed.metadata.task_id)
    assert updated.metadata.integration.patch_path == str(
        (tmp_path / ".kanban-agent" / "_runtime" / "runs" / updated.metadata.task_id / "review-001.patch").resolve()
    )
    assert (target_repo / "app.txt").read_text() == "review me\n"


def test_human_verification_start_rechecks_state_after_lock_for_duplicate_requests(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-duplicate-start-task")
    scanner, service, completed = _task_ready_for_human_verification(config)

    entered_apply = Event()
    allow_apply = Event()
    original_run_verification_apply = service._run_verification_apply

    def slow_run_verification_apply(context):
        entered_apply.set()
        assert allow_apply.wait(timeout=5)
        return original_run_verification_apply(context)

    service._run_verification_apply = slow_run_verification_apply
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_start = executor.submit(service.start, completed.metadata.task_id, by="human-a")
            assert entered_apply.wait(timeout=5)
            second_start = executor.submit(service.start, completed.metadata.task_id, by="human-b")
            allow_apply.set()

            moved = first_start.result(timeout=10)
            assert moved.state == TaskState.HUMAN_VERIFYING
            with pytest.raises(TransitionError, match="human verification can only start from completed-reviews"):
                second_start.result(timeout=10)
    finally:
        service._run_verification_apply = original_run_verification_apply

    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.HUMAN_VERIFYING
    assert refreshed.metadata.lease.owner is None
    assert refreshed.metadata.lease.run_id is None


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
    assert rejected.metadata.review.human_rework_required is False
    assert rejected.metadata.review.human_rework_reason is None


def test_human_verification_rerequest_from_reviewer_qa_moves_completed_reviews_back_to_todos(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-rerequest-from-qa-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    qa_path = completed.task_dir / "REVIEWER-QA-001.md"
    qa_path.write_text(
        "\n".join(
            [
                "# Reviewer Q&A",
                "",
                "## Question 1",
                "- Asked by: human",
                "- Asked at: 2026-01-01T00:00:00+00:00",
                "",
                "Can we keep the existing label?",
                "",
                "## Answer 1",
                "- Answered by: reviewer",
                "- Answered at: 2026-01-01T00:00:05+00:00",
                "",
                "The label can stay, but the helper copy still needs to change.",
                "",
            ]
        )
    )
    completed.metadata.review.qa_path = qa_path.name
    scanner.metadata_store.save(completed.task_dir, completed.metadata)

    moved = service.rerequest_from_reviewer_qa(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert refreshed.metadata.human_verification.note_markdown.startswith("## Re-request Note")
    assert "Can we keep the existing label?" in refreshed.metadata.human_verification.note_markdown
    assert "The label can stay, but the helper copy still needs to change." in refreshed.metadata.human_verification.note_markdown
    assert (repo_root / "app.txt").read_text() == "hello\n"


def test_human_verification_rerequest_from_reviewer_qa_requires_completed_answer(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-rerequest-no-answer-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    qa_path = completed.task_dir / "REVIEWER-QA-001.md"
    qa_path.write_text(
        "\n".join(
            [
                "# Reviewer Q&A",
                "",
                "## Question 1",
                "- Asked by: human",
                "- Asked at: 2026-01-01T00:00:00+00:00",
                "",
                "Can we keep the existing label?",
                "",
            ]
        )
    )
    completed.metadata.review.qa_path = qa_path.name
    scanner.metadata_store.save(completed.task_dir, completed.metadata)

    with pytest.raises(TransitionError, match="reviewer Q&A re-request requires at least one completed reviewer answer"):
        service.rerequest_from_reviewer_qa(completed.metadata.task_id, by="human")


def test_human_verification_rerequest_from_reviewer_qa_works_during_human_verifying(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-rerequest-human-verifying-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    qa_path = completed.task_dir / "REVIEWER-QA-001.md"
    qa_path.write_text(
        "\n".join(
            [
                "# Reviewer Q&A",
                "",
                "## Question 1",
                "- Asked by: human",
                "- Asked at: 2026-01-01T00:00:00+00:00",
                "",
                "What should change next?",
                "",
                "## Answer 1",
                "- Answered by: reviewer",
                "- Answered at: 2026-01-01T00:00:05+00:00",
                "",
                "Please revise the helper copy before approval.",
                "",
            ]
        )
    )
    completed.metadata.review.qa_path = qa_path.name
    scanner.metadata_store.save(completed.task_dir, completed.metadata)
    service.start(completed.metadata.task_id, by="human")

    moved = service.rerequest_from_reviewer_qa(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.human_verification.note_markdown.startswith("## Re-request Note")
    assert "Please revise the helper copy before approval." in refreshed.metadata.human_verification.note_markdown
    assert (repo_root / "app.txt").read_text() == "hello\n"


def test_human_verification_reject_resets_implementation_resume_context(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-reject-reset-implementation-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    in_progress = scanner.find_task(completed.metadata.task_id)
    in_progress.metadata.implementation.last_result = "previous result"
    in_progress.metadata.implementation.resolved_model = "gpt-test"
    in_progress.metadata.implementation.session_id = "ses_previous"
    in_progress.metadata.implementation.last_run_tokens = 123
    in_progress.metadata.implementation.session_tokens = 456
    in_progress.metadata.retry_gate.reason = "review-needs-changes"
    in_progress.metadata.retry_gate.consecutive_count = 2
    in_progress.metadata.retry_gate.not_before = datetime(2025, 1, 1, tzinfo=timezone.utc)
    service.metadata_store.save(in_progress.task_dir, in_progress.metadata)

    moved = service.reject(completed.metadata.task_id, by="human", note="Please revise this.")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.implementation.last_result is None
    assert refreshed.metadata.implementation.resolved_model is None
    assert refreshed.metadata.implementation.session_id is None
    assert refreshed.metadata.implementation.last_run_tokens == 0
    assert refreshed.metadata.implementation.session_tokens == 0
    assert refreshed.metadata.retry_gate.reason is None
    assert refreshed.metadata.retry_gate.consecutive_count == 0
    assert refreshed.metadata.retry_gate.not_before is None
    assert refreshed.metadata.review.human_rework_required is False


def test_human_verification_reject_clears_stale_review_loop_pause_fields(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-reject-clears-review-pause-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    in_progress = scanner.find_task(completed.metadata.task_id)
    in_progress.metadata.review.consecutive_rework_loops = 3
    in_progress.metadata.review.total_rework_loops = 5
    in_progress.metadata.review.rework_loop_plan_revision = in_progress.metadata.plan.revision
    in_progress.metadata.review.primary_blocker = "changed-scope-coverage"
    in_progress.metadata.review.last_blocker_patch_fingerprint = "abc123"
    in_progress.metadata.review.last_backstop_pause_total_rework_loops = 5
    in_progress.metadata.review.human_rework_required = True
    in_progress.metadata.review.human_rework_reason = "stale pause"
    service.metadata_store.save(in_progress.task_dir, in_progress.metadata)

    moved = service.reject(completed.metadata.task_id, by="human", note="Please revise this.")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.review.consecutive_rework_loops == 0
    assert refreshed.metadata.review.total_rework_loops == 0
    assert refreshed.metadata.review.rework_loop_plan_revision == 0
    assert refreshed.metadata.review.primary_blocker is None
    assert refreshed.metadata.review.last_blocker_patch_fingerprint is None
    assert refreshed.metadata.review.last_backstop_pause_total_rework_loops == 0
    assert refreshed.metadata.review.human_rework_required is False
    assert refreshed.metadata.review.human_rework_reason is None


def test_human_verification_reject_makes_task_auto_dispatchable_again(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-reject-auto-dispatch-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    moved = service.reject(completed.metadata.task_id, by="human", note="Please revise this.")

    worker = ImplementerWorker(
        config,
        scanner,
        service.metadata_store,
        service.locks,
        service.transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented again"]),
        workspace_manager=WorkspaceManager(config),
    )

    assert moved.state == TaskState.TODOS
    assert [task.metadata.task_id for task in worker.candidate_tasks()] == [moved.metadata.task_id]


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
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
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


def test_human_verification_approval_allows_resolved_current_cycle_comments(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-resolved-line-comment-approval-task")
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
        body_markdown="Resolved before approval.",
    )

    task = scanner.find_task(completed.metadata.task_id)
    artifact = service._load_comments_artifact(task.task_dir, task.metadata)
    artifact.comments[0].resolved = True
    service._save_comments_artifact(task.task_dir, task.metadata, artifact)

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE


def test_human_verification_reject_requires_note_or_line_comment(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-reject-requires-feedback-task")
    _, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    with pytest.raises(TransitionError, match="request changes is only available after adding a review note or line comment"):
        service.reject(completed.metadata.task_id, by="human", note="")


def test_human_verification_reject_allows_conflict_without_extra_feedback(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-reject-conflict-task")
    scanner, service, completed = _task_ready_for_human_verification(config)

    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert any(error.code == "verification-target-repo-drift" for error in refreshed.metadata.errors)


def test_human_verification_reject_falls_back_when_review_recaputure_fails(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-reject-recapture-failure-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    original_capture = service._capture_review_branch_to_workspace

    def fail_capture(metadata):
        raise IntegrationError("failed to apply reviewed code back into workspace")

    service._capture_review_branch_to_workspace = fail_capture
    try:
        moved = service.reject(completed.metadata.task_id, by="human", note="Need another pass")
    finally:
        service._capture_review_branch_to_workspace = original_capture

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert any(error.code == "human-verification-recapture-failed" for error in refreshed.metadata.errors)
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"


def test_human_verification_approval_is_blocked_when_review_note_exists(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-approval-blocked-by-note-task")
    _, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    service.save_note(completed.metadata.task_id, by="human", content="Please revisit the edge case handling.")

    with pytest.raises(TransitionError, match="approval is blocked until the review note is cleared"):
        service.approve(completed.metadata.task_id, by="human")


def test_human_verification_save_note_extracts_embedded_images_to_attachments(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-note-embedded-image-task")
    _, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    embedded = base64.b64encode(b"pngdata").decode()
    refreshed = service.save_note(
        completed.metadata.task_id,
        by="human",
        content=f"![diagram](data:image/png;base64,{embedded})",
    )

    assert refreshed.metadata.human_verification.note_markdown.startswith("![diagram](_attachments/")
    attachments = list((refreshed.task_dir / "_attachments").glob("*.png"))
    assert len(attachments) == 1
    assert attachments[0].read_bytes() == b"pngdata"


def test_human_verification_reject_normalizes_embedded_note_images(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-note-reject-embedded-image-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    embedded = base64.b64encode(b"pngdata").decode()
    moved = service.reject(
        completed.metadata.task_id,
        by="human",
        note=f"![diagram](data:image/png;base64,{embedded})",
    )

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.human_verification.note_markdown.startswith("![diagram](_attachments/")
    attachments = list((refreshed.task_dir / "_attachments").glob("*.png"))
    assert len(attachments) == 1
    assert attachments[0].read_bytes() == b"pngdata"


def test_human_verification_reject_supports_relative_workspace_path(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-relative-workspace-reject-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    started = service.start(completed.metadata.task_id, by="human")

    workspace = started.metadata.implementation.workspace
    assert workspace is not None
    relative_workspace = os.path.relpath(Path(workspace).resolve(), Path.cwd())
    started.metadata.implementation.workspace = relative_workspace
    scanner.metadata_store.save(started.task_dir, started.metadata)

    moved = service.reject(started.metadata.task_id, by="human", note="Please revise the copy.")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(started.metadata.task_id)
    assert refreshed.metadata.human_verification.note_markdown == "Please revise the copy."


def test_human_verification_start_rolls_note_artifacts_to_current_cycle(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-human-note-cycle-rollover-task")
    scanner, service, completed = _task_ready_for_human_verification(config)

    completed.metadata.cycle = 2
    completed.metadata.human_verification.note_path = "HUMAN-VERIFY-001.md"
    completed.metadata.human_verification.comments_path = "HUMAN-VERIFY-001.comments.json"
    completed.metadata.human_verification.note_markdown = "Old review note"
    scanner.metadata_store.save(completed.task_dir, completed.metadata)

    started = service.start(completed.metadata.task_id, by="human")

    assert started.metadata.human_verification.note_path == "HUMAN-VERIFY-002.md"
    assert started.metadata.human_verification.comments_path == "HUMAN-VERIFY-002.comments.json"
    assert started.metadata.human_verification.note_markdown == ""
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert (refreshed.task_dir / "HUMAN-VERIFY-002.md").exists()


def test_human_verification_start_resets_viewed_files_for_new_cycle(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-viewed-files-cycle-rollover-task")
    scanner, service, completed = _task_ready_for_human_verification(config)

    completed.metadata.cycle = 2
    completed.metadata.human_verification.viewed_cycle = 1
    completed.metadata.human_verification.viewed_files = {"app.txt": True}
    scanner.metadata_store.save(completed.task_dir, completed.metadata)

    started = service.start(completed.metadata.task_id, by="human")

    assert started.metadata.human_verification.viewed_cycle == 2
    assert started.metadata.human_verification.viewed_files == {}
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root)
    detail = task_service.get_task(started.metadata.task_id)
    assert len(detail.changed_files) == 1
    assert detail.changed_files[0].viewed is False


def test_human_verification_shows_previous_cycle_comments_as_read_only_context(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-historical-line-comments-task")
    scanner, service, completed = _task_ready_for_human_verification(config)

    completed.metadata.cycle = 2
    completed.metadata.human_verification.note_path = "HUMAN-VERIFY-002.md"
    completed.metadata.human_verification.comments_path = "HUMAN-VERIFY-002.comments.json"
    scanner.metadata_store.save(completed.task_dir, completed.metadata)
    old_comments = HumanLineCommentsArtifact(
        comments=[
            HumanLineComment(
                id="comment-old-001",
                anchor=HumanLineCommentAnchor(
                    path="app.txt",
                    side="right",
                    line_number=1,
                    line_kind="add",
                    hunk_header="@@ -1 +1 @@",
                ),
                body_markdown="Old round comment",
                cycle=1,
            )
        ]
    )
    (completed.task_dir / "HUMAN-VERIFY-001.comments.json").write_text(old_comments.model_dump_json(indent=2) + "\n")

    started = service.start(completed.metadata.task_id, by="human")

    task_service = TaskService(scanner, config.runs_dir, config.kanban_root)
    detail = task_service.get_task(started.metadata.task_id)
    assert detail.human_review.total_comment_count == 0
    assert detail.human_review.unresolved_comment_count == 0
    assert detail.human_review.historical_comment_count == 1
    changed_file = next(file for file in detail.changed_files if file.path == "app.txt")
    diff = task_service.get_changed_file(started.metadata.task_id, changed_file.id)
    assert len(diff.comments) == 1
    assert diff.comments[0].body_markdown == "Old round comment"
    assert diff.comments[0].cycle == 1
    assert diff.comments[0].editable is False

    with pytest.raises(TransitionError, match="historical line comments are read-only"):
        service.delete_line_comment(started.metadata.task_id, by="human", comment_id="comment-old-001")

    moved = service.approve(started.metadata.task_id, by="human")
    assert moved.state == TaskState.DONE


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


def test_human_verification_start_returns_to_todos_on_target_base_branch_drift(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-start-conflict-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    original_workspace = Path(completed.metadata.implementation.workspace or "")
    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert any(error.code == "verification-target-repo-drift" for error in refreshed.metadata.errors)
    assert refreshed.metadata.commit.status == "pending"
    assert refreshed.metadata.commit.sha is None
    assert refreshed.metadata.commit.review_sha is None
    assert refreshed.metadata.integration.applied is False
    assert refreshed.metadata.implementation.workspace == str(original_workspace)
    assert refreshed.metadata.implementation.branch == completed.metadata.implementation.branch
    assert refreshed.metadata.retry_gate.reason == "verification-target-repo-drift"
    assert original_workspace.exists()
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    branches = subprocess.run(["git", "-C", str(repo_root), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    assert branches == ""
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True).stdout.strip()
    assert status == ""
    artifact = refreshed.task_dir / "HUMAN-VERIFY-001.md"
    assert artifact.exists() is False


def test_human_verification_drift_requires_returning_through_review_flow(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-retry-apply-task")
    scanner, service, completed = _task_ready_for_human_verification(config)
    workspace_path = Path(completed.metadata.implementation.workspace or "")

    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    conflicted = service.start(completed.metadata.task_id, by="human")
    assert conflicted.state == TaskState.TODOS

    subprocess.run(["git", "-C", str(repo_root), "reset", "--hard", "HEAD~1"], check=True, capture_output=True, text=True)

    with pytest.raises(TransitionError, match="human verification can only start from completed-reviews"):
        service.start(completed.metadata.task_id, by="human")

    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert refreshed.metadata.implementation.workspace == str(workspace_path)


def test_human_verification_retry_apply_requires_pending_conflict(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-retry-guard-task")
    _, service, completed = _task_ready_for_human_verification(config)
    started = service.start(completed.metadata.task_id, by="human")

    with pytest.raises(TransitionError, match="verification apply has already succeeded"):
        service.retry_apply(started.metadata.task_id, by="human")


def test_human_verification_approve_is_blocked_until_apply_retry_succeeds(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "verify-approve-blocked-task")
    _, service, completed = _task_ready_for_human_verification(config)

    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    with pytest.raises(TransitionError, match="human verification approval is only allowed from human-verifying"):
        service.approve(completed.metadata.task_id, by="human")


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
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    done = scanner.find_task(completed.metadata.task_id)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir)
    expected_final_branch = f"feature/{done.metadata.task_id.lower()}-{done.metadata.slug}"
    assert config.workspace.root is not None
    workspace_root = config.workspace.root / done.metadata.task_id
    live_runs_dir = config.runs_dir / done.metadata.task_id
    archive_runs_dir = config.archive_runs_dir / done.metadata.task_id
    detail = task_service.get_task(done.metadata.task_id)
    assert done.state == TaskState.DONE
    assert done.metadata.commit.sha
    assert done.metadata.commit.review_sha is not None
    assert done.metadata.commit.review_sha != done.metadata.commit.sha
    assert done.metadata.integration.final_branch == expected_final_branch
    assert done.metadata.integration.review_branch is None
    assert done.metadata.integration.original_branch is None
    assert done.metadata.implementation.workspace is None
    assert not workspace_root.exists()
    assert not live_runs_dir.exists()
    assert archive_runs_dir.exists()
    assert done.metadata.integration.patch_path == str(archive_runs_dir / "review-001.patch")
    assert (archive_runs_dir / "implementer.jsonl").exists()
    assert (archive_runs_dir / "review-001.patch").exists()
    expected_message = "\n".join(
        [
            f"feat: {done.metadata.title}",
            "",
            f"Goal: Implement {done.metadata.title}.",
            "Plan: plan",
            f"Task: {done.metadata.task_id}",
            f"Branch: {done.metadata.integration.final_branch_summary}",
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
    docs_root = config.resolve_target_repo_docs_root(target_repo) / f"{review_date.year:04d}" / f"{review_date.month:02d}" / f"{review_date.day:02d}"
    summary_path = docs_root / f"{done.metadata.task_id}-summary.md"
    assert summary_path.exists()
    summary_text = summary_path.read_text()
    assert f"# Task Summary: {done.metadata.title}" in summary_text
    assert "## Why / Keywords" in summary_text
    assert "- Keywords:" in summary_text
    assert "## Changed Files (1)" in summary_text
    assert "`app.txt` — modified (+1 / -1, hunks=1)" in summary_text
    assert "## Time Summary" in summary_text
    assert detail.log_files == [
        "implementer.jsonl",
        "review-001.patch",
    ]
    changed_file = next(file for file in detail.changed_files if file.path == "app.txt")
    assert task_service.get_changed_file(done.metadata.task_id, changed_file.id).summary.path == "app.txt"


def test_human_verification_approve_switches_back_to_review_branch_before_commit(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-branch-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    subprocess.run(["git", "-C", str(target_repo), "switch", "main"], check=True, capture_output=True, text=True)

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    current_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == f"feature/{moved.metadata.task_id.lower()}-{moved.metadata.slug}"


def test_human_verification_approve_can_commit_to_target_branch(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-target-branch-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")
    moved = service.approve(completed.metadata.task_id, by="human", completion_mode="target-branch")

    assert moved.state == TaskState.DONE
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.commit.review_sha is not None
    assert refreshed.metadata.commit.sha is not None
    assert refreshed.metadata.commit.review_sha != refreshed.metadata.commit.sha
    assert refreshed.metadata.integration.final_branch == "main"
    current_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    review_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--list", f"review/{completed.metadata.task_id.lower()}"], check=True, capture_output=True, text=True).stdout.strip()
    final_branch = subprocess.run(["git", "-C", str(target_repo), "branch", "--list", f"feature/{completed.metadata.task_id.lower()}-{completed.metadata.slug}"], check=True, capture_output=True, text=True).stdout.strip()
    assert review_branch == ""
    assert final_branch == ""


def test_human_verification_approve_uses_configured_target_docs_root(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(
        kanban_root=tmp_path / ".kanban-agent",
        repo_root=tmp_path / "unused-default",
        target_repo_docs_root="records/kanban-docs",
    )
    config.bootstrap()
    create_request_task(config, "verify-configured-doc-root-task", target_repo_root=target_repo)
    _, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    review_date = datetime.now(timezone.utc)
    docs_root = target_repo / "records" / "kanban-docs" / f"{review_date.year:04d}" / f"{review_date.month:02d}" / f"{review_date.day:02d}"
    assert (docs_root / f"{moved.metadata.task_id}-summary.md").exists()


def test_human_verification_approve_writes_summary_without_copying_attachments_bundle(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-attachments-docs-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    embedded = base64.b64encode(b"pngdata").decode()
    refreshed = service.save_note(
        completed.metadata.task_id,
        by="human",
        content=f"![diagram](data:image/png;base64,{embedded})",
    )
    attachment_name = next((refreshed.task_dir / "_attachments").glob("*.png")).name
    refreshed.metadata.human_verification.note_markdown = ""
    scanner.metadata_store.save(refreshed.task_dir, refreshed.metadata)

    moved = service.approve(completed.metadata.task_id, by="human")

    review_date = datetime.now(timezone.utc)
    docs_root = config.resolve_target_repo_docs_root(target_repo) / f"{review_date.year:04d}" / f"{review_date.month:02d}" / f"{review_date.day:02d}"
    summary_path = docs_root / f"{moved.metadata.task_id}-summary.md"
    assert summary_path.exists()
    assert attachment_name not in summary_path.read_text()
    assert not (docs_root / "_attachments").exists()


def test_human_verification_approve_stages_manual_review_changes_before_commit(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
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
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
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
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
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
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
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
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "면적 게임모드 추가", target_repo_root=target_repo)
    branch_summary_adapter = FakeAdapter(["add-area-game-mode"])
    scanner, service, completed = _task_ready_for_human_verification(config, branch_summary_adapter=branch_summary_adapter)

    moved = service.start(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.HUMAN_VERIFYING
    updated = scanner.find_task(completed.metadata.task_id)
    assert updated.metadata.integration.final_branch_summary == "add-area-game-mode"
    assert branch_summary_adapter.run_calls[0]["agent"] == "fs-kanban-planner"


def test_human_verification_approve_uses_stored_english_branch_summary(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "면적 게임모드 추가", target_repo_root=target_repo)
    branch_summary_adapter = FakeAdapter(["add-area-game-mode"])
    scanner, service, completed = _task_ready_for_human_verification(config, branch_summary_adapter=branch_summary_adapter)
    service.start(completed.metadata.task_id, by="human")

    moved = service.approve(completed.metadata.task_id, by="human")

    assert moved.state == TaskState.DONE
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.final_branch == f"feature/{completed.metadata.task_id.lower()}-add-area-game-mode"


def test_human_verification_approve_releases_lock_when_done_cleanup_save_fails(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "verify-approve-lock-release-task", target_repo_root=target_repo)
    scanner, service, completed = _task_ready_for_human_verification(config)
    service.start(completed.metadata.task_id, by="human")

    original_save = service.metadata_store.save

    def fail_done_cleanup_save(task_dir, metadata):
        if TaskState.DONE.value in task_dir.parts and task_dir.name == metadata.task_id and metadata.lease.owner is None:
            raise RuntimeError("done cleanup save failed")
        return original_save(task_dir, metadata)

    service.metadata_store.save = fail_done_cleanup_save
    service.locks.metadata_store.save = fail_done_cleanup_save

    with pytest.raises(RuntimeError, match="done cleanup save failed"):
        service.approve(completed.metadata.task_id, by="human")

    with service.locks.acquire_by_task_id(completed.metadata.task_id, owner="tester", run_id="retry"):
        pass
