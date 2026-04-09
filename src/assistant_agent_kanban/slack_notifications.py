from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Protocol

from .config import AppConfig
from .enums import TaskState
from .metadata_store import MetadataStore
from .models import TaskContext
from .slack_api import slack_api_call, slack_error_message, slack_upload_file_to_thread


logger = logging.getLogger(__name__)


MILESTONE_TRANSITIONS: dict[tuple[TaskState, TaskState], str] = {
    (TaskState.PLANNING, TaskState.WAITING_CHECK_PLANS): "Plan ready for review",
    (TaskState.PLAN_APPROVING, TaskState.WAITING_CHECK_PLANS): "Plan ready for review",
    (TaskState.PLAN_APPROVING, TaskState.TODOS): "Plan approved",
    (TaskState.WAITING_CHECK_PLANS, TaskState.TODOS): "Plan approved",
    (TaskState.IMPLEMENTING, TaskState.WAITING_REVIEWS): "Implementation ready for review",
    (TaskState.REVIEWING, TaskState.TODOS): "Review requested changes",
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
            "text": self._build_message(
                context,
                milestone=milestone,
                previous_state=previous_state,
                by=by,
                note=note,
                is_parent_message=context.metadata.slack.thread_ts is None,
            ),
        }
        blocks = self._build_blocks(context, milestone=milestone)
        if blocks is not None:
            payload["blocks"] = blocks
        if context.metadata.slack.thread_ts:
            payload["thread_ts"] = context.metadata.slack.thread_ts
        response = slack_api_call("chat.postMessage", token=token, body=payload)
        if response.get("ok"):
            self._record_thread_identity(context, fallback_channel=channel, response=response)
            self._upload_markdown_artifact(context, milestone=milestone, token=token)
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

    def _build_message(
        self,
        context: TaskContext,
        *,
        milestone: str,
        previous_state: TaskState,
        by: str,
        note: str | None,
        is_parent_message: bool,
    ) -> str:
        if is_parent_message:
            lines = [
                f"[{context.metadata.task_id}] {context.metadata.title}",
                f"- repo: {context.metadata.target.repo_root}",
                f"- base branch: {context.metadata.target.base_branch}",
            ]
            return "\n".join(lines)
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

    def _build_blocks(self, context: TaskContext, *, milestone: str) -> list[dict[str, object]] | None:
        task_id = context.metadata.task_id
        if milestone == "AI review passed":
            return [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Start verification"},
                            "style": "primary",
                            "action_id": "start_verification",
                            "value": json.dumps({"task_id": task_id, "action": "start_verification"}),
                        }
                    ],
                }
            ]
        if milestone == "Review requested changes":
            return [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Resume review loop"},
                            "style": "primary",
                            "action_id": "resume_review_loop",
                            "value": json.dumps({"task_id": task_id, "action": "resume_review_loop"}),
                        }
                    ],
                }
            ]
        if milestone != "Human verification started":
            return None
        return [
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "approve_verification",
                        "value": json.dumps({"task_id": task_id, "action": "approve_verification"}),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Request changes"},
                        "style": "danger",
                        "action_id": "reject_verification",
                        "value": json.dumps({"task_id": task_id, "action": "reject_verification"}),
                    },
                ],
            }
        ]

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

    def _upload_markdown_artifact(self, context: TaskContext, *, milestone: str, token: str) -> None:
        channel = context.metadata.slack.channel
        thread_ts = context.metadata.slack.thread_ts
        if not channel or not thread_ts:
            logger.info(
                "slack markdown upload skipped: missing thread identity",
                extra={"task_id": context.metadata.task_id, "milestone": milestone},
            )
            return
        artifact_path = self._artifact_path_for_milestone(context, milestone=milestone)
        if artifact_path is None or not artifact_path.exists() or artifact_path.suffix.lower() != ".md":
            logger.info(
                "slack markdown upload skipped: no eligible artifact",
                extra={
                    "task_id": context.metadata.task_id,
                    "milestone": milestone,
                    "artifact": str(artifact_path) if artifact_path is not None else None,
                },
            )
            return
        try:
            content = artifact_path.read_bytes()
        except OSError as exc:
            logger.warning(
                "slack markdown artifact read failed",
                extra={
                    "task_id": context.metadata.task_id,
                    "milestone": milestone,
                    "artifact": str(artifact_path),
                    "error": str(exc),
                },
            )
            return
        digest = hashlib.sha256(content).hexdigest()
        if context.metadata.slack.uploaded_markdown.get(artifact_path.name) == digest:
            logger.info(
                "slack markdown upload skipped: unchanged artifact",
                extra={
                    "task_id": context.metadata.task_id,
                    "milestone": milestone,
                    "artifact": artifact_path.name,
                },
            )
            return
        upload = slack_upload_file_to_thread(
            token=token,
            channel_id=channel,
            thread_ts=thread_ts,
            filename=artifact_path.name,
            title=artifact_path.name,
            content=content,
        )
        if not upload.get("ok"):
            logger.warning(
                "slack markdown upload failed",
                extra={
                    "task_id": context.metadata.task_id,
                    "milestone": milestone,
                    "artifact": artifact_path.name,
                    "error": slack_error_message(upload, fallback="Slack file upload failed."),
                },
            )
            return
        context.metadata.slack.uploaded_markdown[artifact_path.name] = digest
        self.metadata_store.save(context.task_dir, context.metadata)

    def _artifact_path_for_milestone(self, context: TaskContext, *, milestone: str) -> Path | None:
        task_dir = context.task_dir
        cycle = context.metadata.cycle
        if milestone == "Plan ready for review":
            return task_dir / (context.metadata.plan.path or "PLAN.md")
        if milestone == "Implementation ready for review":
            return task_dir / f"WORK-{cycle:03d}.md"
        if milestone in {"Review requested changes", "AI review passed"}:
            return task_dir / f"REVIEW-{cycle:03d}.md"
        if milestone in {"Human verification started", "Human requested changes", "Task completed"}:
            note_path = context.metadata.human_verification.note_path or f"HUMAN-VERIFY-{cycle:03d}.md"
            candidate = task_dir / note_path
            if candidate.exists():
                return candidate
        return None


class SlackTransitionNotifier(Protocol):
    def notify_transition(self, context: TaskContext, *, previous_state: TaskState, by: str, note: str | None = None) -> None: ...
