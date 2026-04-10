from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest

from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.exceptions import TransitionError
from assistant_agent_kanban.integration_manager import IntegrationManager
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import WorkerEvent
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workspace_manager import WorkspaceManager
from assistant_agent_kanban.workers.implementer import ImplementerWorker
from assistant_agent_kanban.workers.reviewer import ReviewerWorker

from .conftest import FakeAdapter, create_request_task, init_git_repo


def reviewer_cycle_responses(
    greeting: str = "hello",
    live: str = "live review",
    *,
    verdict: str = "PASS",
    primary_blocker: str | None = None,
    markdown: str | None = None,
) -> list[str]:
    final_markdown = markdown or f"Verdict: {verdict}\n\n## Acceptance Criteria Check\nReady"
    finalize_json = json.dumps(
        {
            "schema_version": 1,
            "artifact_type": "review",
            "task_id": "TASK-TEST",
            "cycle": 1,
            "verdict": verdict,
            "primary_blocker": primary_blocker,
            "markdown": final_markdown,
        }
    )
    return [greeting, live, finalize_json]


def _task_ready_for_review(config, *, worker_live_logs_enabled: bool = True):
    config.opencode.worker_live_logs_enabled = worker_live_logs_enabled
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

    implementer_responses = ["hello", "implemented live", "## Summary\nimplemented"] if worker_live_logs_enabled else ["## Summary\nimplemented"]
    side_effect_formats = {"default"} if worker_live_logs_enabled else {"json"}
    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(implementer_responses, side_effect=modify_workspace, side_effect_output_formats=side_effect_formats),
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
        adapter=FakeAdapter(reviewer_cycle_responses(verdict="NEEDS_CHANGES", markdown="Verdict: NEEDS_CHANGES\n\n- fix it")),
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
        adapter=FakeAdapter(reviewer_cycle_responses(), resolved_models=["github-copilot/gpt-5", "github-copilot/gpt-5"]),
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    assert scanner.scan()[0].state == TaskState.COMPLETED_REVIEWS
    assert scanner.scan()[0].metadata.cycle == 1
    assert scanner.scan()[0].metadata.review.iteration == 1
    assert (repo_root / "app.txt").read_text() == "hello\n"
    review_json = json.loads((scanner.scan()[0].task_dir / "REVIEW-001.json").read_text())
    assert review_json["verdict"] == "PASS"
    assert "Verdict: PASS" in review_json["assistant_text"]
    assert review_json["resolved_model"] == "github-copilot/gpt-5"
    assert scanner.scan()[0].metadata.review.resolved_model == "github-copilot/gpt-5"
    assert scanner.scan()[0].metadata.retry_gate.reason is None


def test_reviewer_worker_allows_same_blocker_loop_to_continue_when_patch_changes(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-loop-cap-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)

    reviewer = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="same-issue", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue 1")
            + reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="same-issue", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue 2")
            + reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="same-issue", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue 3")
        ),
        integration_manager=IntegrationManager(config),
    )

    rerun_counter = {"value": 1}

    def modify_workspace(cwd):
        rerun_counter["value"] += 1
        (cwd / "app.txt").write_text(f"review me {rerun_counter['value']}\n")

    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            ["hello", "implemented live", "## Summary\nimplemented again"]
            + ["hello", "implemented live", "## Summary\nimplemented once more"],
            side_effect=modify_workspace,
            side_effect_output_formats={"default"},
        ),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(reviewer.run_once()) is True
    after_first = scanner.scan()[0]
    assert after_first.state == TaskState.TODOS
    assert after_first.metadata.review.consecutive_rework_loops == 1
    assert after_first.metadata.review.total_rework_loops == 1
    assert after_first.metadata.review.human_rework_required is False

    assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True
    after_second = scanner.scan()[0]
    assert after_second.state == TaskState.TODOS
    assert after_second.metadata.review.consecutive_rework_loops == 1
    assert after_second.metadata.review.total_rework_loops == 2
    assert after_second.metadata.review.human_rework_required is False

    assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True
    after_third = scanner.scan()[0]
    assert after_third.state == TaskState.TODOS
    assert after_third.metadata.review.consecutive_rework_loops == 1
    assert after_third.metadata.review.total_rework_loops == 3
    assert after_third.metadata.review.human_rework_required is False
    assert after_third.metadata.review.human_rework_reason is None


def test_reviewer_worker_requires_human_review_after_third_rework_loop_without_patch_progress(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-loop-no-progress-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)

    reviewer = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="same-issue", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue 1")
            + reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="same-issue", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue 2")
            + reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="same-issue", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue 3")
        ),
        integration_manager=IntegrationManager(config),
    )

    def keep_workspace_patch_identical(cwd):
        (cwd / "app.txt").write_text("review me 2\n")

    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            ["hello", "implemented live", "## Summary\nimplemented again"]
            + ["hello", "implemented live", "## Summary\nimplemented once more"],
            side_effect=keep_workspace_patch_identical,
            side_effect_output_formats={"default"},
        ),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(reviewer.run_once()) is True
    first = scanner.scan()[0]
    assert first.metadata.review.consecutive_rework_loops == 1

    assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True
    second = scanner.scan()[0]
    assert second.metadata.review.consecutive_rework_loops == 1

    assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True
    third = scanner.scan()[0]
    assert third.metadata.review.consecutive_rework_loops == 2
    assert third.metadata.review.human_rework_required is False

    assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True
    fourth = scanner.scan()[0]
    assert fourth.state == TaskState.TODOS
    assert fourth.metadata.review.consecutive_rework_loops == 3
    assert fourth.metadata.review.total_rework_loops == 4
    assert fourth.metadata.review.human_rework_required is True
    assert fourth.metadata.review.human_rework_reason == "human review required after 3 repeated rework loops for blocker 'same-issue'"
    assert implementer.candidate_tasks() == []


def test_reviewer_needs_changes_gates_on_second_consecutive_loop(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-gated-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(reviewer_cycle_responses(verdict="NEEDS_CHANGES", markdown="Verdict: NEEDS_CHANGES\n\n- still broken")),
        integration_manager=IntegrationManager(config),
    )

    task = scanner.scan()[0]
    task.metadata.retry_gate.reason = "review-needs-changes"
    task.metadata.retry_gate.consecutive_count = 1
    note = worker._handle_needs_changes(
        task.metadata,
        primary_blocker="same-issue",
        blocker_patch_fingerprint="fingerprint-1",
    )

    assert note == "review needs changes"
    assert task.metadata.retry_gate.reason == "review-needs-changes"
    assert task.metadata.retry_gate.consecutive_count == 2
    assert task.metadata.retry_gate.not_before is not None


def test_reviewer_worker_resets_same_blocker_streak_when_blocker_changes(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-progress-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)

    reviewer = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="issue-a", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue a")
            + reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="issue-b", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue b")
            + reviewer_cycle_responses(verdict="NEEDS_CHANGES", primary_blocker="issue-c", markdown="Verdict: NEEDS_CHANGES\n\n- fix issue c")
        ),
        integration_manager=IntegrationManager(config),
    )

    rerun_counter = {"value": 1}

    def modify_workspace(cwd):
        rerun_counter["value"] += 1
        (cwd / "app.txt").write_text(f"review me {rerun_counter['value']}\n")

    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            ["hello", "implemented live", "## Summary\nimplemented again"]
            + ["hello", "implemented live", "## Summary\nimplemented once more"],
            side_effect=modify_workspace,
            side_effect_output_formats={"default"},
        ),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(reviewer.run_once()) is True
    assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True
    assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True

    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.review.total_rework_loops == 3
    assert updated.metadata.review.consecutive_rework_loops == 1
    assert updated.metadata.review.primary_blocker == "issue-c"
    assert updated.metadata.review.human_rework_required is False


def test_reviewer_worker_pauses_after_six_total_rework_loops(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-backstop-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)

    review_responses: list[str] = []
    for index in range(1, 7):
        review_responses.extend(
            reviewer_cycle_responses(
                verdict="NEEDS_CHANGES",
                primary_blocker=f"issue-{index}",
                markdown=f"Verdict: NEEDS_CHANGES\n\n- fix issue {index}",
            )
        )
    reviewer = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(review_responses),
        integration_manager=IntegrationManager(config),
    )

    rerun_counter = {"value": 1}

    def modify_workspace(cwd):
        rerun_counter["value"] += 1
        (cwd / "app.txt").write_text(f"review me {rerun_counter['value']}\n")

    implement_responses: list[str] = []
    for index in range(2, 7):
        implement_responses.extend(["hello", "implemented live", f"## Summary\nimplemented pass {index}"])
    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            implement_responses,
            side_effect=modify_workspace,
            side_effect_output_formats={"default"},
        ),
        workspace_manager=WorkspaceManager(config),
    )

    for _ in range(5):
        assert asyncio.run(reviewer.run_once()) is True
        assert asyncio.run(implementer.run_once()) is True
    assert asyncio.run(reviewer.run_once()) is True

    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.review.total_rework_loops == 6
    assert updated.metadata.review.consecutive_rework_loops == 1
    assert updated.metadata.review.primary_blocker == "issue-6"
    assert updated.metadata.review.human_rework_required is False
    assert updated.metadata.retry_gate.reason == "review-rework-backstop"
    assert updated.metadata.retry_gate.not_before is not None
    assert implementer.candidate_tasks() == []


def test_reviewer_worker_uses_pinned_backend_after_global_change(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "opencode"
    config.opencode.reviewer_model = "openai/gpt-5.4"
    create_request_task(config, "review-pinned-backend-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(task.task_dir, task.metadata)
    config.runtime.coding_assistant = "codex"
    config.codex.reviewer_model = "gpt-5.3-codex"
    opencode_adapter = FakeAdapter(reviewer_cycle_responses(), resolved_models=["openai/gpt-5.4", "openai/gpt-5.4"])
    codex_adapter = FakeAdapter(["Verdict: PASS\nWrong backend"], resolved_models=["gpt-5.3-codex"])
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=codex_adapter,
        integration_manager=IntegrationManager(config),
        adapter_registry={"opencode": opencode_adapter, "codex": codex_adapter},
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert len(opencode_adapter.run_calls) == 3
    assert len(codex_adapter.run_calls) == 0
    assert updated.metadata.review.resolved_model == "openai/gpt-5.4"


def test_reviewer_worker_leaves_target_repo_clean_until_human_verification(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
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
        adapter=FakeAdapter(reviewer_cycle_responses()),
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
        adapter=FakeAdapter(reviewer_cycle_responses()),
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
            super().__init__(
                reviewer_cycle_responses(verdict="NEEDS_CHANGES", markdown="Verdict: NEEDS_CHANGES\n\n- revise")
                + reviewer_cycle_responses(),
                session_ids=cast(list[str | None], ["ses_rev_1"] * 6),
            )
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
    (first_task.task_dir / "REVIEWER-QA-001.md").write_text("# Reviewer Q&A\n\n## Question 1\nWhy was this left?\n\n## Answer 1\nBecause the diff is intentional.\n")
    (first_task.task_dir / "WORK-000.md").write_text("older work\n")
    first_task.metadata.review.session_id = "ses_rev_1"
    first_task.metadata.review.qa_path = "REVIEWER-QA-001.md"
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
    prompt = adapter.prompts[1]
    assert "# Original Request" in prompt
    assert "# Work History" in prompt
    assert "WORK-000.md" in prompt
    assert "WORK-001.md" in prompt
    assert "# Primary Human Rework Goal" in prompt
    assert "highest-priority outcome for this review cycle" in prompt
    assert "current-cycle refinement inside the bounds of the original request, the approved plan, and the repository invariants" in prompt
    assert "# Human Verification History" in prompt
    assert "HUMAN-VERIFY-001.md" in prompt
    assert "# Reviewer Q&A History" in prompt
    assert "REVIEWER-QA-001.md" in prompt
    assert "# Previous AI Reviews" in prompt
    assert "REVIEW-001.md" in prompt
    assert "Judge against the original request and approved plan first" in prompt
    assert prompt.index("# Primary Human Rework Goal") < prompt.index("# Reviewer Q&A History")
    assert prompt.index("# Reviewer Q&A History") < prompt.index("# Previous AI Reviews")
    assert "Treat the latest human verification request as the authoritative goal for this cycle, but not in ways that break the original request" in prompt
    assert "Do not repeat earlier findings unless they still apply" in prompt


def test_reviewer_human_qa_writes_artifact_and_uses_thinking_mode(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "reviewer-qa-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    waiting_reviews = scanner.scan()[0]
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    metadata_store.save(completed.task_dir, completed.metadata)

    adapter = FakeAdapter(
        ["The current implementation is acceptable, but the naming can still be refined."],
        session_ids=["ses_review_qa"],
        total_tokens=[21],
    )
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

    result = worker.answer_human_question(completed.metadata.task_id, by="human", question="Should we rename the public label as well?")
    updated = scanner.find_task(completed.metadata.task_id)
    artifact_path = updated.task_dir / "REVIEWER-QA-001.md"

    assert result["qa_path"] == "REVIEWER-QA-001.md"
    assert result["session_id"] == "ses_review_qa"
    assert adapter.run_calls[0]["show_thinking"] is True
    assert updated.metadata.review.qa_path == "REVIEWER-QA-001.md"
    assert updated.metadata.review.qa_session_id == "ses_review_qa"
    assert updated.metadata.review.qa_last_run_tokens == 21
    assert artifact_path.exists()
    assert "## Question 1" in artifact_path.read_text()
    assert "Should we rename the public label as well?" in artifact_path.read_text()
    assert "The current implementation is acceptable" in artifact_path.read_text()


def test_reviewer_human_qa_async_emits_worker_log_events(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "reviewer-qa-live-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    waiting_reviews = scanner.scan()[0]
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    metadata_store.save(completed.task_dir, completed.metadata)

    event_bus = EventBus()
    adapter = FakeAdapter(["Live reviewer answer"], session_ids=["ses_review_qa_live"], total_tokens=[9])
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        event_bus,
        adapter=adapter,
        integration_manager=IntegrationManager(config),
    )

    async def scenario() -> None:
        events: list[WorkerEvent] = []

        async def collect_one_event() -> None:
            async for event in event_bus.subscribe():
                events.append(event)
                break

        collector = asyncio.create_task(collect_one_event())
        await worker.answer_human_question_async(completed.metadata.task_id, by="human", question="Is the copy okay?")
        await asyncio.wait_for(collector, timeout=1)

        assert any(getattr(event, "event", None) == "worker_log" for event in events)
        worker_log_event = next(event for event in events if getattr(event, "event", None) == "worker_log")
        assert worker_log_event.payload["log_name"] == "reviewer-qa.jsonl"
        assert "Live reviewer answer" in (worker_log_event.payload["rendered_content"] or "")

    asyncio.run(scenario())


def test_reviewer_human_qa_requires_workspace(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "reviewer-qa-no-workspace-task")
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
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    metadata_store.save(completed.task_dir, completed.metadata)

    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["unused"]),
        integration_manager=IntegrationManager(config),
    )

    with pytest.raises(TransitionError, match="requires an active implementation workspace"):
        worker.answer_human_question(completed.metadata.task_id, by="human", question="Can you explain the issue?")


def test_reviewer_worker_rolls_over_session_after_budget_is_exceeded(configured_paths):
    config, _, _ = configured_paths
    config.opencode.reviewer_session_token_budget = 100
    create_request_task(config, "review-session-budget-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config, worker_live_logs_enabled=False)
    task = scanner.scan()[0]
    task.metadata.review.session_id = "ses_rev_1"
    task.metadata.review.session_tokens = 120
    metadata_store.save(task.task_dir, task.metadata)

    adapter = FakeAdapter(
        [json.dumps({
            "schema_version": 1,
            "artifact_type": "review",
            "task_id": "TASK-TEST",
            "cycle": 1,
            "verdict": "PASS",
            "markdown": "Verdict: PASS\n\n## Acceptance Criteria Check\nReady",
        })],
        session_ids=["ses_rev_2"],
        total_tokens=[35],
    )
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
    assert adapter.run_calls[0]["session_id"] is None
    assert updated.metadata.review.session_id == "ses_rev_2"
    assert updated.metadata.review.last_run_tokens == 35
    assert updated.metadata.review.session_tokens == 35


def test_reviewer_worker_uses_single_json_run_when_live_logs_disabled(configured_paths):
    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "reviewer-single-run-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config, worker_live_logs_enabled=False)
    adapter = FakeAdapter(
        [json.dumps({
            "schema_version": 1,
            "artifact_type": "review",
            "task_id": "TASK-TEST",
            "cycle": 1,
            "verdict": "PASS",
            "markdown": "Verdict: PASS\n\n## Acceptance Criteria Check\nReady",
        })],
        session_ids=["ses_review_single"],
        total_tokens=[33],
    )
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
    assert len(adapter.run_calls) == 1
    assert adapter.run_calls[0]["output_format"] == "json"
    assert updated.state == TaskState.COMPLETED_REVIEWS
    review_json = json.loads((updated.task_dir / "REVIEW-001.json").read_text())
    assert review_json["session_id"] == "ses_review_single"
    assert review_json["total_tokens"] == 33


def test_reviewer_worker_localizes_review_source_for_korean_requests(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(reviewer_cycle_responses(markdown="Verdict: PASS\n\n## Acceptance Criteria Check\n준비 완료"))
            self.prompts: list[str] = []

        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return super().run(**kwargs)

    config, _, _ = configured_paths
    create_request_task(config, "review-korean-task", language="ko", body="한국어로 작업합니다.")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    task = scanner.scan()[0]
    task.metadata.request.language = "ko"
    metadata_store.save(task.task_dir, task.metadata)

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
    prompt = adapter.prompts[1]
    assert "Return the markdown artifact in Korean." in prompt
    assert "# 원래 요청" in prompt
    assert "# 계획" in prompt
    assert "# 리뷰 지침" in prompt
    assert "판단하기 전에 먼저 원래 요청과 승인된 계획을 기준으로 보고" in prompt


def test_reviewer_worker_falls_back_to_english_for_unsupported_request_language(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(reviewer_cycle_responses())
            self.prompts: list[str] = []

        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return super().run(**kwargs)

    config, _, _ = configured_paths
    create_request_task(config, "review-japanese-task", language="ja", body="日本語の依頼です。")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    task = scanner.scan()[0]
    task.metadata.request.language = "ja"
    metadata_store.save(task.task_dir, task.metadata)

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
    prompt = adapter.prompts[1]
    assert "Return the markdown artifact in English." in prompt
    assert "# Review Instructions" in prompt


def test_reviewer_finalize_failure_returns_to_waiting_reviews(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "review-finalize-failure-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    adapter = FakeAdapter(["hello", "live review", "not-json"])
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
    assert updated.state == TaskState.WAITING_REVIEWS
    assert any(error.code == "review-finalize-failed" for error in updated.metadata.errors)
    assert updated.metadata.review.last_verdict is None
    assert updated.metadata.retry_gate.reason == "review-finalize-failed"
    assert updated.metadata.retry_gate.not_before is not None
    assert worker.candidate_tasks() == []


def test_reviewer_worker_uses_current_settings_override_when_requested(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.coding_assistant = "gemini"
    config.gemini.reviewer_model = "gemini-2.5-pro"
    create_request_task(config, "review-current-settings-override-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(task.task_dir, task.metadata)
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.implementation.workspace = str(repo_root)
    (repo_root / "app.txt").write_text("review me\n")
    metadata_store.save(implementing.task_dir, implementing.metadata)
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")

    config.runtime.coding_assistant = "codex"
    config.runtime.role_backends.reviewer = "codex"
    config.codex.reviewer_model = "gpt-5.4"
    waiting_reviews.metadata.review.resume_mode = "current-settings"
    waiting_reviews.metadata.review.resume_backend_override = "codex"
    waiting_reviews.metadata.review.resume_model_override = "gpt-5.4"
    metadata_store.save(waiting_reviews.task_dir, waiting_reviews.metadata)

    gemini_adapter = FakeAdapter(reviewer_cycle_responses(verdict="PASS"), resolved_models=["gemini-2.5-pro", "gemini-2.5-pro"])
    codex_adapter = FakeAdapter([reviewer_cycle_responses(verdict="PASS")[-1]], resolved_models=["gpt-5.4"])
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=codex_adapter,
        integration_manager=IntegrationManager(config),
        adapter_registry={"gemini": gemini_adapter, "codex": codex_adapter},
    )

    current = scanner.scan()[0]
    assert current.state == TaskState.WAITING_REVIEWS
    assert asyncio.run(worker.run_task(current)) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.COMPLETED_REVIEWS
    assert len(codex_adapter.run_calls) == 1
    assert len(gemini_adapter.run_calls) == 0
    assert updated.metadata.review.resolved_model == "gpt-5.4"
    assert updated.metadata.review.resume_mode is None
    assert updated.metadata.review.resume_backend_override is None
    assert updated.metadata.review.resume_model_override is None


def test_reviewer_worker_consumes_pinned_resume_mode_once(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "review-pinned-resume-once-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    planning.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.implementation.workspace = str(repo_root)
    (repo_root / "app.txt").write_text("review me\n")
    metadata_store.save(implementing.task_dir, implementing.metadata)
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    waiting_reviews.metadata.review.resume_mode = "pinned"
    metadata_store.save(waiting_reviews.task_dir, waiting_reviews.metadata)

    adapter = FakeAdapter([reviewer_cycle_responses(verdict="PASS")[-1]], resolved_models=["gemini-2.5-pro"])
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

    current = scanner.scan()[0]
    assert asyncio.run(worker.run_task(current)) is True
    updated = scanner.scan()[0]
    assert updated.metadata.review.resume_mode is None


def test_reviewer_worker_announces_log_file(configured_paths):
    async def receive_worker_log_file(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log_file":
                return event

    config, _, _ = configured_paths
    create_request_task(config, "review-log-file-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config)
    event_bus = EventBus()
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        event_bus,
        adapter=FakeAdapter(reviewer_cycle_responses()),
        integration_manager=IntegrationManager(config),
    )

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log_file(event_bus))
        await asyncio.sleep(0)
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())
    assert event is not None
    assert event.payload["log_name"] == "reviewer.jsonl"


def test_reviewer_worker_emits_realtime_worker_log_events_when_live_logs_disabled(configured_paths):
    async def receive_worker_log(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log":
                return event

    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "review-log-default-task")
    metadata_store, scanner, locks, transitions = _task_ready_for_review(config, worker_live_logs_enabled=False)
    event_bus = EventBus()
    adapter = FakeAdapter([
        json.dumps({
            "schema_version": 1,
            "artifact_type": "review",
            "task_id": "TASK-TEST",
            "cycle": 1,
            "verdict": "PASS",
            "markdown": "Verdict: PASS\n\n## Acceptance Criteria Check\nReady",
        })
    ])
    worker = ReviewerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        event_bus,
        adapter=adapter,
        integration_manager=IntegrationManager(config),
    )

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await asyncio.sleep(0)
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())
    assert event is not None
    assert event.payload["log_name"] == "reviewer.jsonl"
    assert "Verdict: PASS" in event.payload["rendered_content"]
