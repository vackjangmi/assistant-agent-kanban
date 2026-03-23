from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import utc_now
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.task_service import TaskService
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workers.plan_approval import PlanApprovalWorker

from .conftest import FakeAdapter, create_request_task


def test_plan_approval_worker_auto_approves_low_risk_plan(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-approval-auto")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\nsmall plan\n")
    planning.metadata.plan.revision = 1
    metadata_store.save(planning.task_dir, planning.metadata)
    approving = transitions.move(planning, TaskState.PLAN_APPROVING, by="planner")
    adapter = FakeAdapter(
        [json.dumps({"disposition": "auto_approve", "confidence": "high", "risk_signals": [], "rationale": "Small file-scoped change."})],
        resolved_models=["openai/gpt-5.4"],
        session_ids=["ses_plan_gate"],
        total_tokens=[33],
    )
    worker = PlanApprovalWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    updated = scanner.find_task(approving.metadata.task_id)
    assert updated.state == TaskState.TODOS
    assert updated.metadata.plan.approved is True
    assert updated.metadata.plan_approval.disposition == "auto_approve"
    assert updated.metadata.plan_approval.confidence == "high"
    assert updated.metadata.plan_approval.resolved_model == "openai/gpt-5.4"
    assert updated.metadata.plan_approval.attempt_count == 1
    assert (updated.task_dir / "PLAN-APPROVAL.md").exists()


def test_plan_approval_worker_retries_invalid_output_once_before_approval(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-approval-fallback")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\nunclear plan\n")
    planning.metadata.plan.revision = 1
    metadata_store.save(planning.task_dir, planning.metadata)
    approving = transitions.move(planning, TaskState.PLAN_APPROVING, by="planner")
    worker = PlanApprovalWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            [
                "not json",
                json.dumps({"disposition": "auto_approve", "confidence": "high", "risk_signals": [], "rationale": "Recovered on retry."}),
            ]
        ),
    )

    assert asyncio.run(worker.run_once()) is True

    updated = scanner.find_task(approving.metadata.task_id)
    assert updated.state == TaskState.TODOS
    assert updated.metadata.plan.approved is True
    assert updated.metadata.plan_approval.disposition == "auto_approve"
    assert updated.metadata.plan_approval.attempt_count == 2
    assert updated.metadata.plan_approval.attempts[0]["risk_signals"] == ["approval_output_invalid"]


def test_plan_approval_worker_escalates_after_retry_cap_is_exhausted(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-approval-cap")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\nunclear plan\n")
    planning.metadata.plan.revision = 1
    metadata_store.save(planning.task_dir, planning.metadata)
    approving = transitions.move(planning, TaskState.PLAN_APPROVING, by="planner")
    worker = PlanApprovalWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter(["not json", "not json"]))

    assert asyncio.run(worker.run_once()) is True

    updated = scanner.find_task(approving.metadata.task_id)
    assert updated.state == TaskState.WAITING_CHECK_PLANS
    assert updated.metadata.plan.approved is False
    assert updated.metadata.plan_approval.disposition == "review_required"
    assert updated.metadata.plan_approval.attempt_count == 2
    assert updated.metadata.plan_approval.escalation_reason == "approval_retry_exhausted"


def test_plan_approval_worker_does_not_retry_substantive_review_required(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-approval-substantive")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\napi touching plan\n")
    planning.metadata.plan.revision = 1
    metadata_store.save(planning.task_dir, planning.metadata)
    approving = transitions.move(planning, TaskState.PLAN_APPROVING, by="planner")
    adapter = FakeAdapter([json.dumps({"disposition": "review_required", "confidence": "medium", "risk_signals": ["api_contract_change"], "rationale": "API changes need human review."})])
    worker = PlanApprovalWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    updated = scanner.find_task(approving.metadata.task_id)
    assert updated.state == TaskState.WAITING_CHECK_PLANS
    assert updated.metadata.plan_approval.attempt_count == 1
    assert updated.metadata.plan_approval.escalation_reason == "review_required"
    assert len(adapter.run_calls) == 1


def test_plan_approval_worker_auto_progresses_recommended_review_after_deadline(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-approval-recommended")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\nmedium scope plan\n")
    planning.metadata.plan.revision = 2
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    waiting.metadata.plan_approval.disposition = "review_recommended"
    waiting.metadata.plan_approval.source_plan_revision = 2
    waiting.metadata.plan_approval.auto_progress_at = utc_now() - timedelta(minutes=1)
    metadata_store.save(waiting.task_dir, waiting.metadata)
    worker = PlanApprovalWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter())

    assert asyncio.run(worker.run_once()) is True

    updated = scanner.find_task(waiting.metadata.task_id)
    assert updated.state == TaskState.TODOS
    assert updated.metadata.plan.approved is True
    assert updated.metadata.plan_approval.auto_progress_at is None


def test_plan_edit_resets_plan_approval_retry_tracking(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-approval-edit-reset")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\nfirst draft\n")
    planning.metadata.plan.revision = 1
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    waiting.metadata.plan_approval.attempt_count = 2
    waiting.metadata.plan_approval.last_attempt_plan_revision = 1
    waiting.metadata.plan_approval.last_retry_reason = "approval_output_invalid"
    waiting.metadata.plan_approval.escalation_reason = "approval_retry_exhausted"
    waiting.metadata.plan_approval.attempts = [{"attempt": 1}, {"attempt": 2}]
    metadata_store.save(waiting.task_dir, waiting.metadata)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root)

    task_service.update_markdown_artifact(waiting.metadata.task_id, "PLAN.md", "## Summary\nupdated by human\n")

    updated = scanner.find_task(waiting.metadata.task_id)
    assert updated.metadata.plan.revision == 2
    assert updated.metadata.plan_approval.attempt_count == 0
    assert updated.metadata.plan_approval.last_attempt_plan_revision == 0
    assert updated.metadata.plan_approval.last_retry_reason is None
    assert updated.metadata.plan_approval.escalation_reason is None
    assert updated.metadata.plan_approval.attempts == []


def test_plan_approval_prompt_includes_historical_examples_for_strong_positives(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "historical-approval-task", body="Historical request body")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root, transitions=transitions, locks=locks)
    historical = scanner.scan()[0]
    planning = transitions.move(historical, TaskState.PLANNING, by="planner")
    plan_text = "# Plan\n\n## Summary\nHistorical summary.\n\n## Scope\n- Keep this.\n"
    (planning.task_dir / "PLAN.md").write_text(plan_text)
    (planning.task_dir / "PLAN.json").write_text(json.dumps({"assistant_text": plan_text}) + "\n")
    planning.metadata.plan_approval.disposition = "review_recommended"
    planning.metadata.plan_approval.confidence = "medium"
    planning.metadata.plan_approval.risk_signals = ["multi_file_scope"]
    planning.metadata.plan_approval.rationale = "Human approved this without changing the plan."
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = task_service.approve_plan(waiting.metadata.task_id, by="human")
    transitions.recover_move(todos, TaskState.DONE, by="human")

    create_request_task(config, "current-approval-task", body="Current request body")
    current = next(task for task in scanner.scan() if task.metadata.title == "current-approval-task")
    current_planning = transitions.move(current, TaskState.PLANNING, by="planner")
    (current_planning.task_dir / "PLAN.md").write_text(plan_text)
    metadata_store.save(current_planning.task_dir, current_planning.metadata)
    approving = transitions.move(current_planning, TaskState.PLAN_APPROVING, by="planner")
    worker = PlanApprovalWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter())

    prompt = worker._build_prompt(approving)

    assert "Historical Human Approvals (Strong Positives)" in prompt
    assert "historical-approval-task" in prompt
