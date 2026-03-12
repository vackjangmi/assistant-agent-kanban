from __future__ import annotations

import pytest

from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.exceptions import TransitionError
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager

from .conftest import create_request_task


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
