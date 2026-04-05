from __future__ import annotations

from datetime import timedelta

from .models import TaskMetadata, utc_now


RETRY_COOLDOWN = timedelta(minutes=10)
REVIEW_REWORK_BACKSTOP_COOLDOWN = timedelta(hours=1)
IMMEDIATE_GATE_REASONS = {
    "planner-empty-artifact",
    "planner-invalid-artifact",
    "implementation-no-changes",
    "implementation-base-sync-conflict",
    "implementation-local-commits",
    "implementation-failed",
    "review-no-workspace",
    "review-no-changes",
    "review-local-commits",
    "review-empty-artifact",
}


def can_auto_dispatch(metadata: TaskMetadata) -> bool:
    if metadata.review.human_rework_required:
        return False
    not_before = metadata.retry_gate.not_before
    if not_before is None:
        return True
    return utc_now() >= not_before


def clear_retry_gate(metadata: TaskMetadata) -> None:
    metadata.retry_gate.reason = None
    metadata.retry_gate.consecutive_count = 0
    metadata.retry_gate.not_before = None


def apply_retry_gate(metadata: TaskMetadata, *, reason: str) -> None:
    gate = metadata.retry_gate
    if gate.reason == reason:
        gate.consecutive_count += 1
    else:
        gate.reason = reason
        gate.consecutive_count = 1

    if reason in IMMEDIATE_GATE_REASONS:
        gate.not_before = utc_now() + RETRY_COOLDOWN
        return
    if reason == "review-rework-backstop":
        gate.not_before = utc_now() + REVIEW_REWORK_BACKSTOP_COOLDOWN
        return
    if reason == "review-needs-changes" and gate.consecutive_count >= 2:
        gate.not_before = utc_now() + RETRY_COOLDOWN
        return
    gate.not_before = None
