from __future__ import annotations

import pytest

from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import TransitionError
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.slack_notifications import MILESTONE_TRANSITIONS
from assistant_agent_kanban.transitions import TransitionManager

from .conftest import create_request_task


class _FakeSlackNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def notify_transition(self, context, *, previous_state, by, note=None) -> None:
        previous_state_value = getattr(previous_state, "value", previous_state)
        current_state_value = getattr(context.state, "value", context.state)
        if (TaskState(previous_state_value), TaskState(current_state_value)) not in MILESTONE_TRANSITIONS:
            return
        self.calls.append(
            {
                "task_id": context.metadata.task_id,
                "from_state": previous_state_value,
                "to_state": current_state_value,
                "by": by,
                "note": note,
            }
        )


class _RaisingSlackNotifier:
    def notify_transition(self, context, *, previous_state, by, note=None) -> None:
        raise RuntimeError("slack broke")


def test_manual_transition_respects_allowed_edges(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-task")
    scanner = KanbanScanner(config)
    metadata_store = MetadataStore()
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    moved = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    assert moved.state == TaskState.TODOS
    assert moved.metadata.plan.approved is True


def test_waiting_plan_can_close_without_marking_done(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "split-parent-task")
    scanner = KanbanScanner(config)
    metadata_store = MetadataStore()
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    closed = transitions.manual_move(waiting.metadata.task_id, TaskState.CLOSED, by="human")

    assert closed.state == TaskState.CLOSED
    assert closed.task_dir.parent == config.state_dir(TaskState.CLOSED)
    assert closed.metadata.plan.approved is False
    assert closed.metadata.closure.reason == "other"
    assert closed.metadata.closure.closed_by == "human"


def test_manual_transition_supports_human_verifying_edges(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "verify-transition-task")
    scanner = KanbanScanner(config)
    metadata_store = MetadataStore()
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")

    human_verifying = transitions.manual_move(completed.metadata.task_id, TaskState.HUMAN_VERIFYING, by="human")
    rejected = transitions.manual_move(human_verifying.metadata.task_id, TaskState.TODOS, by="human")

    assert human_verifying.state == TaskState.HUMAN_VERIFYING
    assert rejected.state == TaskState.TODOS


def test_invalid_transition_is_blocked(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "bad-task")
    scanner = KanbanScanner(config)
    transitions = TransitionManager(config, MetadataStore(), scanner, TaskLockManager(config))

    with pytest.raises(TransitionError):
        transitions.move(scanner.scan()[0], TaskState.DONE, by="tester")


def test_manual_move_to_done_uses_date_nested_directory(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "done-nested-task")
    scanner = KanbanScanner(config)
    metadata_store = MetadataStore()
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    human_verifying = transitions.manual_move(completed.metadata.task_id, TaskState.HUMAN_VERIFYING, by="human")

    done = transitions.manual_move(human_verifying.metadata.task_id, TaskState.DONE, by="human")

    entered_at = done.metadata.history[-1].entered_at.astimezone()
    expected_parent = config.state_dir(TaskState.DONE) / entered_at.strftime("%Y") / entered_at.strftime("%m") / entered_at.strftime("%d")
    assert done.task_dir.parent == expected_parent
    assert done.task_dir.name == done.metadata.task_id
    assert done.task_dir.exists()


def test_completed_reviews_can_return_to_todos(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "completed-review-conflict-task")
    scanner = KanbanScanner(config)
    transitions = TransitionManager(config, MetadataStore(), scanner, TaskLockManager(config))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")

    moved = transitions.move(completed, TaskState.TODOS, by="human", note="integration conflict")

    assert moved.state == TaskState.TODOS


def test_transitions_emit_slack_notifications_for_selected_milestones(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "slack-milestone-task")
    scanner = KanbanScanner(config)
    notifier = _FakeSlackNotifier()
    transitions = TransitionManager(config, MetadataStore(), scanner, TaskLockManager(config), slack_notifier=notifier)
    task = scanner.scan()[0]

    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner", note="plan ready")
    todo = transitions.move(waiting, TaskState.TODOS, by="human", note="manual approval")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer", note="implementation complete")

    assert notifier.calls == [
        {
            "task_id": waiting.metadata.task_id,
            "from_state": "planning",
            "to_state": "waiting-check-plans",
            "by": "planner",
            "note": "plan ready",
        },
        {
            "task_id": todo.metadata.task_id,
            "from_state": "waiting-check-plans",
            "to_state": "todos",
            "by": "human",
            "note": "manual approval",
        },
        {
            "task_id": waiting_reviews.metadata.task_id,
            "from_state": "implementing",
            "to_state": "waiting-reviews",
            "by": "implementer",
            "note": "implementation complete",
        },
    ]


def test_transitions_do_not_fail_when_slack_notification_raises(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "slack-notifier-failure-task")
    scanner = KanbanScanner(config)
    transitions = TransitionManager(config, MetadataStore(), scanner, TaskLockManager(config), slack_notifier=_RaisingSlackNotifier())
    task = scanner.scan()[0]

    planning = transitions.move(task, TaskState.PLANNING, by="planner")

    assert planning.state == TaskState.PLANNING
    assert planning.task_dir.exists()
