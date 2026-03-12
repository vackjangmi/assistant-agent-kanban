from __future__ import annotations

import asyncio
import json

from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.integration_manager import IntegrationManager
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workspace_manager import WorkspaceManager
from fs_kanban_agent.workers.implementer import ImplementerWorker
from fs_kanban_agent.workers.reviewer import ReviewerWorker

from .conftest import FakeAdapter, create_request_task, init_git_repo


def _task_ready_for_review(config):
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

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("review me\n")

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
    asyncio.run(implementer.run_once())
    return metadata_store, scanner, locks, transitions


def test_reviewer_worker_returns_to_todos_on_needs_changes(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-fail-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["Verdict: NEEDS_CHANGES\n- fix it"]),
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.cycle == 1
    assert updated.metadata.review.iteration == 1
    assert updated.metadata.retry_gate.reason == "review-needs-changes"
    assert updated.metadata.retry_gate.consecutive_count == 1
    assert updated.metadata.retry_gate.not_before is None


def test_reviewer_worker_waits_for_human_verification_on_pass(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "review-pass-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["Verdict: PASS\nReady"], resolved_models=["github-copilot/gpt-5"]),
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    assert scanner.scan()[0].state == TaskState.COMPLETED_REVIEWS
    assert scanner.scan()[0].metadata.cycle == 1
    assert scanner.scan()[0].metadata.review.iteration == 1
    assert (repo_root / "app.txt").read_text() == "hello\n"
    review_json = json.loads((scanner.scan()[0].task_dir / "REVIEW-001.json").read_text())
    assert "Verdict: PASS" in review_json["assistant_text"]
    assert review_json["resolved_model"] == "github-copilot/gpt-5"
    assert scanner.scan()[0].metadata.review.resolved_model == "github-copilot/gpt-5"
    assert scanner.scan()[0].metadata.retry_gate.reason is None


def test_reviewer_worker_leaves_target_repo_clean_until_human_verification(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "review-target-task", target_repo_root=target_repo)
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["Verdict: PASS\nReady"]),
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    assert scanner.scan()[0].state == TaskState.COMPLETED_REVIEWS
    assert (target_repo / "app.txt").read_text() == "hello\n"


def test_reviewer_worker_rejects_tasks_with_no_workspace_changes(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-noop-task")
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
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    metadata_store.save(implementing.task_dir, implementing.metadata)
    WorkspaceManager(config).prepare(implementing.metadata)
    metadata_store.save(implementing.task_dir, implementing.metadata)
    transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")

    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["Verdict: PASS\nlooks good"]),
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert any(error.code == "review-no-changes" for error in updated.metadata.errors)
    assert updated.metadata.retry_gate.reason == "review-no-changes"
    assert updated.metadata.retry_gate.not_before is not None


def test_reviewer_worker_reuses_session_and_builds_full_context(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(["Verdict: NEEDS_CHANGES\n- revise", "Verdict: PASS\nReady"], session_ids=["ses_rev_1", "ses_rev_1"])
            self.prompts: list[str] = []

        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return super().run(**kwargs)

    config, _, _ = configured_paths
    create_request_task(config, "review-context-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    first_task = scanner.scan()[0]
    (first_task.task_dir / "REVIEW-001.md").write_text("Verdict: NEEDS_CHANGES\n- prior issue\n")
    (first_task.task_dir / "HUMAN-VERIFY-001.md").write_text("Please re-check replay flow.\n")
    (first_task.task_dir / "WORK-000.md").write_text("older work\n")
    first_task.metadata.review.session_id = "ses_rev_1"
    first_task.metadata.cycle = 1
    first_task.metadata.implementation.iteration = 1
    first_task.metadata.review.iteration = 1
    metadata_store.save(first_task.task_dir, first_task.metadata)

    adapter = PromptCapturingAdapter()
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=adapter,
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.metadata.review.session_id == "ses_rev_1"
    assert adapter.run_calls[0]["session_id"] == "ses_rev_1"
    prompt = adapter.prompts[0]
    assert "# Work History" in prompt
    assert "WORK-000.md" in prompt
    assert "WORK-001.md" in prompt
    assert "# Previous AI Reviews" in prompt
    assert "REVIEW-001.md" in prompt
    assert "# Human Verification History" in prompt
    assert "HUMAN-VERIFY-001.md" in prompt
    assert "Do not repeat earlier findings unless they still apply" in prompt


def test_reviewer_needs_changes_gates_on_second_consecutive_loop(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-gated-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    task = scanner.scan()[0]
    task.metadata.retry_gate.reason = "review-needs-changes"
    task.metadata.retry_gate.consecutive_count = 1
    metadata_store.save(task.task_dir, task.metadata)
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["Verdict: NEEDS_CHANGES\n- still broken"]),
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.metadata.retry_gate.reason == "review-needs-changes"
    assert updated.metadata.retry_gate.consecutive_count == 2
    assert updated.metadata.retry_gate.not_before is not None
