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
    HUMAN_VERIFYING = "human-verifying"
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
    TaskState.HUMAN_VERIFYING,
    TaskState.DONE,
]

ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.REQUESTS: {TaskState.PLANNING},
    TaskState.PLANNING: {TaskState.WAITING_CHECK_PLANS},
    TaskState.WAITING_CHECK_PLANS: {TaskState.TODOS},
    TaskState.TODOS: {TaskState.IMPLEMENTING},
    TaskState.IMPLEMENTING: {TaskState.TODOS, TaskState.WAITING_REVIEWS},
    TaskState.WAITING_REVIEWS: {TaskState.REVIEWING},
    TaskState.REVIEWING: {TaskState.TODOS, TaskState.COMPLETED_REVIEWS},
    TaskState.COMPLETED_REVIEWS: {TaskState.HUMAN_VERIFYING},
    TaskState.HUMAN_VERIFYING: {TaskState.TODOS, TaskState.DONE},
    TaskState.DONE: set(),
}

MANUAL_TRANSITIONS = {
    (TaskState.WAITING_CHECK_PLANS, TaskState.TODOS),
    (TaskState.COMPLETED_REVIEWS, TaskState.HUMAN_VERIFYING),
    (TaskState.HUMAN_VERIFYING, TaskState.TODOS),
    (TaskState.HUMAN_VERIFYING, TaskState.DONE),
}

ACTIVE_STATES = {
    TaskState.PLANNING,
    TaskState.IMPLEMENTING,
    TaskState.REVIEWING,
    TaskState.HUMAN_VERIFYING,
}
