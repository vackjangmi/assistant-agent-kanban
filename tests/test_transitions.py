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


def test_invalid_transition_is_blocked(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "bad-task")
    scanner = KanbanScanner(config)
    transitions = TransitionManager(config, MetadataStore(), scanner, TaskLockManager(config))

    with pytest.raises(TransitionError):
        transitions.move(scanner.scan()[0], TaskState.DONE, by="tester")
