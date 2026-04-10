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

MILESTONE_EMOJIS: dict[str, str] = {
    "Plan ready for review": "📝",
    "Plan approved": "✅",
    "Implementation ready for review": "🛠️",
    "Review requested changes": "🔁",
    "AI review passed": "🔍",
    "Human verification started": "👀",
    "Human requested changes": "✏️",
    "Task completed": "🎉",
}


class SlackMilestoneNotifier:
    def __init__(self, config: AppConfig, metadata_store: MetadataStore | None = None) -> None:
        self.config = config
        self.metadata_store = metadata_store or MetadataStore()

    def notify_transition(self, context: TaskContext, *, previous_state: TaskState, by: str, note: str | None = None) -> None:
        milestone = MILESTONE_TRANSITIONS.get((previous_state, context.state))
        token = self.config.slack.bot_token
        if not self.config.slack.enabled or not token:
            return
        if context.state == TaskState.IMPLEMENTING:
            self._clear_action_message(context, action_key="resume_review_loop", token=token)
        if milestone is None:
            return
        channel = context.metadata.slack.channel or self.config.slack.default_channel
        if not channel:
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
        payload["blocks"] = self._build_blocks(
            context,
            milestone=milestone,
            previous_state=previous_state,
            by=by,
            note=note,
            is_parent_message=context.metadata.slack.thread_ts is None,
        )
        if context.metadata.slack.thread_ts:
            payload["thread_ts"] = context.metadata.slack.thread_ts
        response = slack_api_call("chat.postMessage", token=token, body=payload)
        if response.get("ok"):
            self._record_thread_identity(context, fallback_channel=channel, response=response)
            self._record_action_message(context, milestone=milestone, response=response, text=str(payload["text"]))
            self._clear_obsolete_action_buttons(context, milestone=milestone, token=token)
            self._upload_markdown_artifact(context, milestone=milestone, token=token)
            return
        logger.warning(
            "slack milestone notification failed: %s",
            slack_error_message(response, fallback="Slack chat.postMessage failed."),
            extra={
                "task_id": context.metadata.task_id,
                "from_state": self._state_value(previous_state),
                "to_state": self._state_value(context.state),
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
                f"🧩 [{context.metadata.task_id}] {context.metadata.title}",
            ]
            if context.metadata.target.repo_root:
                lines.append(f"• Repo: {context.metadata.target.repo_root}")
            if context.metadata.target.base_branch:
                lines.append(f"• Base branch: {context.metadata.target.base_branch}")
            return "\n".join(lines)
        emoji = MILESTONE_EMOJIS.get(milestone, "🔔")
        lines = [
            f"{emoji} {milestone}",
            f"• State: {self._state_value(previous_state)} → {self._state_value(context.state)}",
        ]
        if by:
            lines.append(f"• Actor: {by}")
        if note:
            lines.append(f"• Note: {note}")
        return "\n".join(lines)

    def _build_blocks(
        self,
        context: TaskContext,
        *,
        milestone: str,
        previous_state: TaskState,
        by: str,
        note: str | None,
        is_parent_message: bool,
    ) -> list[dict[str, object]]:
        if is_parent_message:
            return self._build_parent_blocks(context, milestone=milestone)
        return self._build_thread_blocks(
            context,
            milestone=milestone,
            previous_state=previous_state,
            by=by,
            note=note,
        )

    def _build_parent_blocks(self, context: TaskContext, *, milestone: str) -> list[dict[str, object]]:
        title = context.metadata.title
        task_id = context.metadata.task_id
        repo_root = context.metadata.target.repo_root
        base_branch = context.metadata.target.base_branch
        fields: list[dict[str, str]] = [
            {"type": "mrkdwn", "text": f"*Task ID*\n`{task_id}`"},
            {"type": "mrkdwn", "text": f"*Milestone*\n{MILESTONE_EMOJIS.get(milestone, '🔔')} {milestone}"},
        ]
        if repo_root:
            fields.append({"type": "mrkdwn", "text": f"*Repo*\n`{repo_root}`"})
        if base_branch:
            fields.append({"type": "mrkdwn", "text": f"*Base branch*\n`{base_branch}`"})
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🧩 {title}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Task opened in Slack thread*",
                },
            },
            {"type": "section", "fields": fields},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "Replies in this thread will track status changes and review actions."}
                ],
            },
        ]

    def _build_thread_blocks(
        self,
        context: TaskContext,
        *,
        milestone: str,
        previous_state: TaskState,
        by: str,
        note: str | None,
    ) -> list[dict[str, object]]:
        emoji = MILESTONE_EMOJIS.get(milestone, "🔔")
        blocks: list[dict[str, object]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{emoji} *{milestone}*"},
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*State change*\n`{self._state_value(previous_state)}` → `{self._state_value(context.state)}`",
                    },
                ],
            },
        ]
        detail_fields: list[dict[str, str]] = []
        if by:
            detail_fields.append({"type": "mrkdwn", "text": f"*Actor*\n{by}"})
        if detail_fields:
            blocks.append({"type": "section", "fields": detail_fields})
        if note:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"*Note:* {note}"},
                    ],
                }
            )
        action_block = self._build_action_block(context, milestone=milestone)
        if action_block is not None:
            blocks.append(action_block)
        return blocks

    def _build_action_block(self, context: TaskContext, *, milestone: str) -> dict[str, object] | None:
        task_id = context.metadata.task_id
        if milestone == "AI review passed":
            return {
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
        if milestone == "Review requested changes":
            return {
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
        if milestone != "Human verification started":
            return None
        return {
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

    def _record_action_message(self, context: TaskContext, *, milestone: str, response: dict[str, object], text: str) -> None:
        action_key = self._action_key_for_milestone(milestone)
        if action_key is None:
            return
        response_ts = response.get("ts")
        if not isinstance(response_ts, str) or not response_ts:
            return
        context.metadata.slack.action_message_ts[action_key] = response_ts
        context.metadata.slack.action_message_text[action_key] = text
        self.metadata_store.save(context.task_dir, context.metadata)

    def _clear_obsolete_action_buttons(self, context: TaskContext, *, milestone: str, token: str) -> None:
        if milestone == "Human verification started":
            self._clear_action_message(context, action_key="start_verification", token=token)

    def _clear_action_message(self, context: TaskContext, *, action_key: str, token: str) -> None:
        channel = context.metadata.slack.channel
        message_ts = context.metadata.slack.action_message_ts.get(action_key)
        text = context.metadata.slack.action_message_text.get(action_key)
        if not channel or not message_ts:
            return
        slack_api_call(
            "chat.update",
            token=token,
            body={
                "channel": channel,
                "ts": message_ts,
                "text": text or "",
                "blocks": [],
            },
        )
        context.metadata.slack.action_message_ts.pop(action_key, None)
        context.metadata.slack.action_message_text.pop(action_key, None)
        self.metadata_store.save(context.task_dir, context.metadata)

    def _action_key_for_milestone(self, milestone: str) -> str | None:
        if milestone == "AI review passed":
            return "start_verification"
        if milestone == "Review requested changes":
            return "resume_review_loop"
        if milestone == "Human verification started":
            return "verification_decision"
        return None

    def _state_value(self, state: TaskState | str) -> str:
        return state.value if isinstance(state, TaskState) else str(state)

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
                "slack markdown upload failed: %s",
                slack_error_message(upload, fallback="Slack file upload failed."),
                extra={
                    "task_id": context.metadata.task_id,
                    "milestone": milestone,
                    "artifact": artifact_path.name,
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
