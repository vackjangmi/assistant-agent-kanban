from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    REQUESTS = "requests"
    PLANNING = "planning"
    PLAN_APPROVING = "plan-approving"
    WAITING_CHECK_PLANS = "waiting-check-plans"
    TODOS = "todos"
    IMPLEMENTING = "implementing"
    WAITING_REVIEWS = "waiting-reviews"
    REVIEWING = "reviewing"
    COMPLETED_REVIEWS = "completed-reviews"
    HUMAN_VERIFYING = "human-verifying"
    DONE = "done"
    CLOSED = "closed"


STATE_ORDER = [
    TaskState.REQUESTS,
    TaskState.PLANNING,
    TaskState.PLAN_APPROVING,
    TaskState.WAITING_CHECK_PLANS,
    TaskState.TODOS,
    TaskState.IMPLEMENTING,
    TaskState.WAITING_REVIEWS,
    TaskState.REVIEWING,
    TaskState.COMPLETED_REVIEWS,
    TaskState.HUMAN_VERIFYING,
    TaskState.DONE,
    TaskState.CLOSED,
]

ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.REQUESTS: {TaskState.PLANNING, TaskState.CLOSED},
    TaskState.PLANNING: {TaskState.REQUESTS, TaskState.PLAN_APPROVING, TaskState.WAITING_CHECK_PLANS, TaskState.CLOSED},
    TaskState.PLAN_APPROVING: {TaskState.WAITING_CHECK_PLANS, TaskState.TODOS, TaskState.CLOSED},
    TaskState.WAITING_CHECK_PLANS: {TaskState.TODOS, TaskState.CLOSED},
    TaskState.TODOS: {TaskState.IMPLEMENTING, TaskState.CLOSED},
    TaskState.IMPLEMENTING: {TaskState.TODOS, TaskState.WAITING_REVIEWS, TaskState.CLOSED},
    TaskState.WAITING_REVIEWS: {TaskState.REVIEWING, TaskState.CLOSED},
    TaskState.REVIEWING: {TaskState.TODOS, TaskState.WAITING_REVIEWS, TaskState.COMPLETED_REVIEWS, TaskState.CLOSED},
    TaskState.COMPLETED_REVIEWS: {TaskState.TODOS, TaskState.HUMAN_VERIFYING, TaskState.CLOSED},
    TaskState.HUMAN_VERIFYING: {TaskState.TODOS, TaskState.DONE, TaskState.CLOSED},
    TaskState.DONE: set(),
    TaskState.CLOSED: set(),
}

CANCELABLE_STATES = set(STATE_ORDER) - {TaskState.DONE, TaskState.CLOSED}

MANUAL_TRANSITIONS = {
    (TaskState.WAITING_CHECK_PLANS, TaskState.TODOS),
    (TaskState.WAITING_CHECK_PLANS, TaskState.CLOSED),
    (TaskState.COMPLETED_REVIEWS, TaskState.HUMAN_VERIFYING),
    (TaskState.HUMAN_VERIFYING, TaskState.TODOS),
    (TaskState.HUMAN_VERIFYING, TaskState.DONE),
}

ACTIVE_STATES = {
    TaskState.PLANNING,
    TaskState.PLAN_APPROVING,
    TaskState.IMPLEMENTING,
    TaskState.REVIEWING,
    TaskState.HUMAN_VERIFYING,
}
