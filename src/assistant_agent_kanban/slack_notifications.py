from __future__ import annotations

import logging
from typing import Protocol

from .config import AppConfig
from .enums import TaskState
from .metadata_store import MetadataStore
from .models import TaskContext
from .slack_api import slack_api_call, slack_error_message


logger = logging.getLogger(__name__)


MILESTONE_TRANSITIONS: dict[tuple[TaskState, TaskState], str] = {
    (TaskState.PLANNING, TaskState.WAITING_CHECK_PLANS): "Plan ready for review",
    (TaskState.PLAN_APPROVING, TaskState.WAITING_CHECK_PLANS): "Plan ready for review",
    (TaskState.PLAN_APPROVING, TaskState.TODOS): "Plan approved",
    (TaskState.WAITING_CHECK_PLANS, TaskState.TODOS): "Plan approved",
    (TaskState.IMPLEMENTING, TaskState.WAITING_REVIEWS): "Implementation ready for review",
    (TaskState.REVIEWING, TaskState.COMPLETED_REVIEWS): "AI review passed",
    (TaskState.COMPLETED_REVIEWS, TaskState.HUMAN_VERIFYING): "Human verification started",
    (TaskState.HUMAN_VERIFYING, TaskState.TODOS): "Human requested changes",
    (TaskState.HUMAN_VERIFYING, TaskState.DONE): "Task completed",
}


class SlackMilestoneNotifier:
    def __init__(self, config: AppConfig, metadata_store: MetadataStore | None = None) -> None:
        self.config = config
        self.metadata_store = metadata_store or MetadataStore()

    def notify_transition(self, context: TaskContext, *, previous_state: TaskState, by: str, note: str | None = None) -> None:
        milestone = MILESTONE_TRANSITIONS.get((previous_state, context.state))
        if milestone is None:
            return
        channel = context.metadata.slack.channel or self.config.slack.default_channel
        token = self.config.slack.bot_token
        if not self.config.slack.enabled or not channel or not token:
            return
        payload: dict[str, object] = {
            "channel": channel,
            "text": self._build_message(context, milestone=milestone, previous_state=previous_state, by=by, note=note),
        }
        if context.metadata.slack.thread_ts:
            payload["thread_ts"] = context.metadata.slack.thread_ts
        response = slack_api_call("chat.postMessage", token=token, body=payload)
        if response.get("ok"):
            self._record_thread_identity(context, fallback_channel=channel, response=response)
            return
        logger.warning(
            "slack milestone notification failed",
            extra={
                "task_id": context.metadata.task_id,
                "from_state": previous_state.value,
                "to_state": context.state.value,
                "error": slack_error_message(response, fallback="Slack chat.postMessage failed."),
            },
        )

    def _build_message(self, context: TaskContext, *, milestone: str, previous_state: TaskState, by: str, note: str | None) -> str:
        lines = [
            f"🔔 {milestone}",
            f"Task: {context.metadata.task_id} — {context.metadata.title}",
            f"State: {previous_state.value} → {context.state.value}",
            f"Actor: {by}",
        ]
        if context.metadata.target.repo_root:
            lines.append(f"Repo: {context.metadata.target.repo_root}")
        if context.metadata.target.base_branch:
            lines.append(f"Base branch: {context.metadata.target.base_branch}")
        if note:
            lines.append(f"Note: {note}")
        return "\n".join(lines)

    def _record_thread_identity(self, context: TaskContext, *, fallback_channel: str, response: dict[str, object]) -> None:
        if context.metadata.slack.thread_ts:
            if context.metadata.slack.channel is None:
                response_channel = response.get("channel")
                context.metadata.slack.channel = response_channel if isinstance(response_channel, str) else fallback_channel
                self.metadata_store.save(context.task_dir, context.metadata)
            return
        response_ts = response.get("ts")
        if not isinstance(response_ts, str) or not response_ts:
            logger.warning(
                "slack parent message did not return a thread ts",
                extra={"task_id": context.metadata.task_id, "channel": fallback_channel},
            )
            return
        response_channel = response.get("channel")
        context.metadata.slack.thread_ts = response_ts
        context.metadata.slack.channel = response_channel if isinstance(response_channel, str) else fallback_channel
        self.metadata_store.save(context.task_dir, context.metadata)


class SlackTransitionNotifier(Protocol):
    def notify_transition(self, context: TaskContext, *, previous_state: TaskState, by: str, note: str | None = None) -> None: ...
