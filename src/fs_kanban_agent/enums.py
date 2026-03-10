from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    REQUESTS = "requests"
    PLANNING = "planning"
    WAITING_CHECK_PLANS = "waiting-check-plans"
    TODOS = "todos"
    IMPLEMENTING = "implementing"
    WAITING_REVIEWS = "waiting-reviews"
    REVIEWING = "reviewing"
    COMPLETED_REVIEWS = "completed-reviews"
    INTEGRATION_TEST_COMPLETED = "integration-test-completed"
    DONE = "done"


STATE_ORDER = [
    TaskState.REQUESTS,
    TaskState.PLANNING,
    TaskState.WAITING_CHECK_PLANS,
    TaskState.TODOS,
    TaskState.IMPLEMENTING,
    TaskState.WAITING_REVIEWS,
    TaskState.REVIEWING,
    TaskState.COMPLETED_REVIEWS,
    TaskState.INTEGRATION_TEST_COMPLETED,
    TaskState.DONE,
]

ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.REQUESTS: {TaskState.PLANNING},
    TaskState.PLANNING: {TaskState.WAITING_CHECK_PLANS},
    TaskState.WAITING_CHECK_PLANS: {TaskState.TODOS},
    TaskState.TODOS: {TaskState.IMPLEMENTING},
    TaskState.IMPLEMENTING: {TaskState.WAITING_REVIEWS},
    TaskState.WAITING_REVIEWS: {TaskState.REVIEWING},
    TaskState.REVIEWING: {TaskState.TODOS, TaskState.COMPLETED_REVIEWS},
    TaskState.COMPLETED_REVIEWS: {TaskState.INTEGRATION_TEST_COMPLETED},
    TaskState.INTEGRATION_TEST_COMPLETED: {TaskState.DONE},
    TaskState.DONE: set(),
}

MANUAL_TRANSITIONS = {
    (TaskState.WAITING_CHECK_PLANS, TaskState.TODOS),
    (TaskState.COMPLETED_REVIEWS, TaskState.INTEGRATION_TEST_COMPLETED),
}

ACTIVE_STATES = {
    TaskState.PLANNING,
    TaskState.IMPLEMENTING,
    TaskState.REVIEWING,
}
