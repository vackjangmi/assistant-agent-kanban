from __future__ import annotations

import json

import pytest

from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import TransitionError
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.plan_approval_learning import (
    PlanApprovalLearningService,
    classify_plan_change,
)
from assistant_agent_kanban.services.task_service import TaskService
from assistant_agent_kanban.transitions import TransitionManager

from .conftest import create_request_task


def valid_plan_markdown(summary: str = "Original summary.") -> str:
    return "\n".join(
        [
            "## Summary",
            summary,
            "",
            "## Scope",
            "- Keep this.",
            "",
            "## Out of Scope",
            "- Do not widen scope.",
            "",
            "## File Map",
            "- `src/example.py`: Example entry point.",
            "",
            "## Step-by-step Plan",
            "1. Update the plan artifact.",
            "",
            "## Validation Plan",
            "- Run focused tests.",
            "",
            "## Acceptance Criteria",
            "- The request remains satisfied.",
            "",
            "## Risks",
            "- Minor regression risk.",
            "",
            "## Open Questions",
            "- None.",
        ]
    )


def test_classify_plan_change_detects_none_and_trivial_and_substantive():
    baseline = "# Plan\n\n## Summary\nSame summary.\n\n## Scope\n- Keep this.\n"
    assert classify_plan_change(baseline_text=baseline, current_text=baseline) == "none"
    assert classify_plan_change(
        baseline_text=baseline,
        current_text="# Plan\n\n## Summary\nSame summary!\n\n## Scope\n- Keep this.\n",
    ) == "trivial"
    assert classify_plan_change(
        baseline_text=baseline,
        current_text="# Plan\n\n## Summary\nSame summary.\n\n## Scope\n- Change this.\n",
    ) == "substantive"


def test_task_service_records_plan_edit_event(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-learning-edit")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root, transitions=transitions, locks=locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    plan_text = valid_plan_markdown()
    (planning.task_dir / "PLAN.md").write_text(plan_text)
    (planning.task_dir / "PLAN.json").write_text(json.dumps({"assistant_text": plan_text}) + "\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    task_service.update_markdown_artifact(
        waiting.metadata.task_id,
        "PLAN.md",
        valid_plan_markdown("Original summary!"),
        by="human",
    )

    updated = scanner.find_task(waiting.metadata.task_id)
    assert updated.metadata.plan.revision == 1
    assert len(updated.metadata.plan.edit_events) == 1
    assert updated.metadata.plan.edit_events[0].edited_by == "human"
    assert updated.metadata.plan.edit_events[0].change_classification == "trivial"


def test_task_service_approve_plan_records_human_approval_artifacts(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-learning-approve")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root, transitions=transitions, locks=locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    plan_text = valid_plan_markdown()
    (planning.task_dir / "PLAN.md").write_text(plan_text)
    (planning.task_dir / "PLAN.json").write_text(json.dumps({"assistant_text": plan_text}) + "\n")
    planning.metadata.plan_approval.disposition = "review_recommended"
    planning.metadata.plan_approval.confidence = "medium"
    planning.metadata.plan_approval.risk_signals = ["multi_file_scope"]
    planning.metadata.plan_approval.rationale = "Looks okay but a human should glance at it."
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    moved = task_service.approve_plan(waiting.metadata.task_id, by="human")

    assert moved.state == TaskState.TODOS
    record = moved.metadata.plan_approval.human_approvals[-1]
    assert record.change_classification == "none"
    assert record.ai_disposition == "review_recommended"
    assert (moved.task_dir / "PLAN-HUMAN-APPROVAL.md").exists()
    assert (moved.task_dir / "PLAN-HUMAN-APPROVAL.json").exists()


def test_task_service_approve_plan_rejects_malformed_plan_markdown(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-learning-reject-malformed")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root, transitions=transitions, locks=locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\nOnly the summary survived.\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    with pytest.raises(TransitionError, match="PLAN.md missing required section: ## Scope"):
        task_service.approve_plan(waiting.metadata.task_id, by="human")

    updated = scanner.find_task(waiting.metadata.task_id)
    assert updated.state == TaskState.WAITING_CHECK_PLANS
    assert not (updated.task_dir / "PLAN-HUMAN-APPROVAL.md").exists()
    assert not (updated.task_dir / "PLAN-HUMAN-APPROVAL.json").exists()


def test_learning_service_formats_strong_positive_examples(configured_paths):
    config, _, _ = configured_paths
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task_service = TaskService(scanner, config.runs_dir, config.kanban_root, transitions=transitions, locks=locks)

    create_request_task(config, "current-plan", body="Current task body")
    current_task = scanner.scan()[0]

    create_request_task(config, "historical-plan", body="Historical body")
    tasks = scanner.scan()
    historical_task = next(task for task in tasks if task.metadata.title == "historical-plan")
    planning = transitions.move(historical_task, TaskState.PLANNING, by="planner")
    plan_text = valid_plan_markdown("Historical summary.")
    (planning.task_dir / "PLAN.md").write_text(plan_text)
    (planning.task_dir / "PLAN.json").write_text(json.dumps({"assistant_text": plan_text}) + "\n")
    planning.metadata.plan_approval.disposition = "review_recommended"
    planning.metadata.plan_approval.confidence = "medium"
    planning.metadata.plan_approval.risk_signals = ["multi_file_scope"]
    planning.metadata.plan_approval.rationale = "Safe but worth a glance."
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = task_service.approve_plan(waiting.metadata.task_id, by="human")
    done = transitions.recover_move(todos, TaskState.DONE, by="human")
    learning = PlanApprovalLearningService(scanner)

    prompt_context = learning.format_historical_examples(current_task)

    assert "Historical Human Approvals" in prompt_context
    assert done.metadata.task_id in prompt_context
    assert "Historical summary" in prompt_context
