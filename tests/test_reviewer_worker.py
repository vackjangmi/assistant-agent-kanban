from __future__ import annotations

import asyncio
import json
from typing import cast

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


def reviewer_cycle_responses(
    greeting: str = "hello",
    live: str = "live review",
    *,
    verdict: str = "PASS",
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
    prompt = adapter.prompts[1]
    assert "# Work History" in prompt
    assert "WORK-000.md" in prompt
    assert "WORK-001.md" in prompt
    assert "# Previous AI Reviews" in prompt
    assert "REVIEW-001.md" in prompt
    assert "# Human Verification History" in prompt
    assert "HUMAN-VERIFY-001.md" in prompt
    assert "Do not repeat earlier findings unless they still apply" in prompt


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
        adapter=FakeAdapter(reviewer_cycle_responses(verdict="NEEDS_CHANGES", markdown="Verdict: NEEDS_CHANGES\n\n- still broken")),
        integration_manager=IntegrationManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.metadata.retry_gate.reason == "review-needs-changes"
    assert updated.metadata.retry_gate.consecutive_count == 2
    assert updated.metadata.retry_gate.not_before is not None


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
    assert "# 계획" in prompt
    assert "# 리뷰 지침" in prompt
    assert "판단하기 전에 전체 작업 이력" in prompt


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
    assert updated.state == TaskState.TODOS
    assert any(error.code == "review-finalize-failed" for error in updated.metadata.errors)
    assert updated.metadata.review.last_verdict is None


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
