from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import json
import logging
from pathlib import Path
from typing import Any, Protocol, cast

from .config import AppConfig, AssistantBackend
from .events import EventBus
from .locks import TaskLockManager
from .metadata_store import MetadataStore
from .models import TaskContext
from .recovery import RecoveryService
from .repo_branches import describe_target_repo_branches
from .scanner import KanbanScanner
from .services.board_service import BoardService
from .services.human_verification_service import HumanVerificationService
from .services.retrospective_service import RetrospectiveService
from .services.task_deletion_service import TaskDeletionService
from .services.task_service import TaskService
from .transitions import TransitionManager
from .assistant_adapter import AssistantAdapter, AssistantBackendManager, build_backend_manager
from .exceptions import AdapterRunError, CommitError, IntegrationError, TaskNotFoundError, TransitionError
from .language import runtime_language_code_to_request_language
from .request_creator import RequestTemplateData, build_default_scope_sections_for_language, create_request, split_lines
from .request_draft_store import RequestDraftStore, StoredRequestDraft, serialize_request_draft_transcript_markdown
from .request_drafting import draft_request
from .slack_api import slack_api_call, slack_error_message, slack_upload_file_to_thread
from .slack_channel_matcher import slack_channel_matches_config
from .slack_notifications import SlackMilestoneNotifier
from .slack_runtime import SlackRuntime
from .workers.committer import CommitWorker
from .workers.implementer import ImplementerWorker
from .workers.plan_approval import PlanApprovalWorker
from .workers.planner import PlanningWorker
from .workers.reviewer import ReviewerWorker
from watchfiles import awatch


logger = logging.getLogger(__name__)

SLACK_SECTION_TEXT_LIMIT = 3000
SLACK_FIELD_TEXT_LIMIT = 2000


class DispatchWorker(Protocol):
    def candidate_tasks(self) -> list[TaskContext]: ...

    async def run_task(self, task: TaskContext) -> bool: ...


class BoardProvider(Protocol):
    def get_board(self) -> Any: ...


class RecoveryProvider(Protocol):
    def recover(self) -> Iterable[Any]: ...


class ModelRegistryProvider(Protocol):
    def warm_availability(self) -> dict[AssistantBackend, Any]: ...
    def get(self, backend: AssistantBackend, *, refresh: bool = False) -> Any: ...
    def all_availability(self, *, refresh: bool = False) -> dict[AssistantBackend, Any]: ...


class RuntimeSupervisor:
    def __init__(
        self,
        config: AppConfig,
        planner: DispatchWorker,
        plan_approval: DispatchWorker,
        implementer: DispatchWorker,
        reviewer: DispatchWorker,
        committer: Any,
        scanner: KanbanScanner,
        board_service: BoardProvider,
        verification_service: Any,
        deletion_service: Any,
        task_service: Any,
        retrospective_service: Any,
        recovery: RecoveryProvider,
        events: EventBus,
        model_registry: ModelRegistryProvider,
    ) -> None:
        self.config = config
        self.planner = planner
        self.plan_approval = plan_approval
        self.implementer = implementer
        self.reviewer = reviewer
        self.committer = committer
        self.scanner = scanner
        self.board_service = board_service
        self.verification_service = verification_service
        self.deletion_service = deletion_service
        self.task_service = task_service
        self.retrospective_service = retrospective_service
        self.recovery = recovery
        self.events = events
        self.model_registry = model_registry
        self.backend_availability: dict[AssistantBackend, Any] = {}
        self.adapter_registry: dict[AssistantBackend, AssistantAdapter] = {}
        self._stop_event = asyncio.Event()
        self._background_tasks: list[asyncio.Task[None]] = []
        self._role_tasks: dict[str, set[asyncio.Task[None]]] = {
            "planner": set(),
            "plan_approval": set(),
            "implementer": set(),
            "reviewer": set(),
        }
        self._inflight_task_ids: set[str] = set()
        self._task_adapters = [adapter for adapter in self._collect_task_adapters() if adapter is not None]
        self.slack_runtime: SlackRuntime | None = None

    async def start(self) -> None:
        self._stop_event.clear()
        await self.startup_recovery()
        warm_availability = getattr(self.model_registry, "warm_availability", None)
        if callable(warm_availability):
            self.backend_availability = cast(dict[AssistantBackend, Any], await asyncio.to_thread(warm_availability))
        else:
            self.backend_availability = {}
        if self.slack_runtime is not None:
            await self.slack_runtime.start_if_configured()
        if self.config.runtime.auto_dispatch:
            self._background_tasks = [
                *self._background_tasks,
                self._create_supervised_task("fs-kanban-dispatch", self.dispatch_forever),
                self._create_supervised_task("fs-kanban-watch", self.watch_forever),
            ]

    async def stop(self) -> None:
        self._stop_event.set()
        if self.slack_runtime is not None:
            await self.slack_runtime.stop()
        current_loop = asyncio.get_running_loop()
        tasks = [
            task
            for task in self._background_tasks
            if not task.done() and getattr(task, "get_loop", lambda: current_loop)() is current_loop
        ]
        self._background_tasks.clear()
        for role_tasks in self._role_tasks.values():
            tasks.extend(role_tasks)
            role_tasks.clear()
        self._inflight_task_ids.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def rescan_and_publish(self) -> None:
        refresh = getattr(self.board_service, "refresh_board", None)
        board = refresh() if callable(refresh) else self.board_service.get_board()
        await self.events.publish(board_to_event(board))

    async def handle_slack_interactive_action(self, payload: dict[str, Any]) -> dict[str, object] | None:
        payload_type = payload.get("type")
        if payload_type == "view_submission":
            return await self._handle_slack_view_submission(payload)
        actions = payload.get("actions")
        if not isinstance(actions, list) or not actions:
            return {"status": "noop"}
        action = actions[0]
        if not isinstance(action, dict):
            return {"status": "noop"}
        action_id = action.get("action_id")
        if action_id in {"open_request_intake", "request_intake_project_select", "request_intake_generate_draft", "request_intake_revise", "request_intake_submit"}:
            return await self._handle_slack_request_intake_action(payload, action)
        if action_id not in {"start_verification", "approve_verification", "reject_verification", "resume_review_loop"}:
            return {"status": "noop"}
        raw_value = action.get("value")
        if not isinstance(raw_value, str) or not raw_value:
            return {"status": "error", "message": "Slack action payload is missing task context."}
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            return {"status": "error", "message": "Slack action payload is invalid."}
        if not isinstance(value, dict):
            return {"status": "error", "message": "Slack action payload is invalid."}
        task_id = value.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return {"status": "error", "message": "Slack action payload is missing task id."}
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError:
            return {"status": "error", "message": f"Task {task_id} no longer exists."}
        expected_thread_ts = task.metadata.slack.thread_ts
        expected_channel = task.metadata.slack.channel
        message = payload.get("message")
        current_thread_ts = None
        if isinstance(message, dict):
            raw_thread_ts = message.get("thread_ts") or message.get("ts")
            if isinstance(raw_thread_ts, str) and raw_thread_ts:
                current_thread_ts = raw_thread_ts
        channel = payload.get("channel")
        current_channel = channel.get("id") if isinstance(channel, dict) else None
        if expected_thread_ts and current_thread_ts and expected_thread_ts != current_thread_ts:
            return {"status": "error", "message": "This Slack action no longer matches the current task thread."}
        if expected_channel and isinstance(current_channel, str) and expected_channel != current_channel:
            return {"status": "error", "message": "This Slack action was clicked from the wrong Slack channel."}
        user = payload.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        by = f"slack:{user_id}" if isinstance(user_id, str) and user_id else "slack"
        if action_id == "resume_review_loop":
            return await asyncio.to_thread(self._open_slack_resume_review_loop_modal, payload, task_id, by)
        if action_id == "reject_verification":
            return await asyncio.to_thread(self._open_slack_reject_verification_modal, payload, task_id, by)
        try:
            if action_id == "start_verification":
                await asyncio.to_thread(self.verification_service.start, task_id, by=by)
            elif action_id == "approve_verification":
                await asyncio.to_thread(self.verification_service.approve, task_id, by=by, completion_mode="new-branch")
        except (TransitionError, TaskNotFoundError, CommitError, IntegrationError) as exc:
            return {"status": "error", "message": str(exc)}
        await self.rescan_and_publish()
        return {"status": "success", "clear_buttons": True}

    async def _handle_slack_view_submission(self, payload: dict[str, Any]) -> dict[str, object]:
        view = payload.get("view")
        if not isinstance(view, dict):
            return {"status": "error", "message": "Slack modal payload is invalid."}
        callback_id = view.get("callback_id")
        if callback_id == "request_intake_modal":
            return await self._handle_slack_request_intake_submission(payload, view)
        if callback_id == "resume_review_loop_modal":
            return await self._handle_slack_resume_review_loop_submission(payload, view)
        if callback_id == "reject_verification_modal":
            return await self._handle_slack_reject_verification_submission(payload, view)
        return {"status": "noop"}

    async def _handle_slack_resume_review_loop_submission(
        self, payload: dict[str, Any], view: dict[str, Any]
    ) -> dict[str, object]:
        raw_metadata = view.get("private_metadata")
        if not isinstance(raw_metadata, str) or not raw_metadata:
            return {"status": "error", "message": "Slack modal is missing task context."}
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {"status": "error", "message": "Slack modal context is invalid."}
        if not isinstance(metadata, dict):
            return {"status": "error", "message": "Slack modal context is invalid."}
        task_id = metadata.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return {"status": "error", "message": "Slack modal is missing task id."}
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError:
            return {"status": "error", "message": f"Task {task_id} no longer exists."}
        expected_thread_ts = task.metadata.slack.thread_ts
        expected_channel = task.metadata.slack.channel
        modal_thread_ts = metadata.get("thread_ts")
        modal_channel = metadata.get("channel_id")
        if isinstance(expected_thread_ts, str) and isinstance(modal_thread_ts, str) and expected_thread_ts != modal_thread_ts:
            return {"status": "error", "message": "This Slack action no longer matches the current task thread."}
        if isinstance(expected_channel, str) and isinstance(modal_channel, str) and expected_channel != modal_channel:
            return {"status": "error", "message": "This Slack action was submitted from the wrong Slack channel."}
        state = view.get("state")
        message_text = self._extract_slack_modal_input(state, block_id="resume_review_loop_input", action_id="message_input")
        if not message_text:
            return {"status": "error", "message": "Resume message is required."}
        user = payload.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        by = f"slack:{user_id}" if isinstance(user_id, str) and user_id else "slack"
        try:
            await asyncio.to_thread(self.task_service.resume_review_loop, task_id, by=by, message=message_text)
        except (TransitionError, TaskNotFoundError, CommitError, IntegrationError) as exc:
            return {"status": "error", "message": str(exc)}
        await self.rescan_and_publish()
        await asyncio.to_thread(
            self._clear_slack_action_message,
            metadata.get("channel_id"),
            metadata.get("message_ts"),
            metadata.get("message_text") or "",
        )
        return {"status": "success"}

    async def _handle_slack_reject_verification_submission(
        self, payload: dict[str, Any], view: dict[str, Any]
    ) -> dict[str, object]:
        raw_metadata = view.get("private_metadata")
        if not isinstance(raw_metadata, str) or not raw_metadata:
            return {"status": "error", "message": "Slack modal is missing task context."}
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {"status": "error", "message": "Slack modal context is invalid."}
        if not isinstance(metadata, dict):
            return {"status": "error", "message": "Slack modal context is invalid."}
        task_id = metadata.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return {"status": "error", "message": "Slack modal is missing task id."}
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError:
            return {"status": "error", "message": f"Task {task_id} no longer exists."}
        expected_thread_ts = task.metadata.slack.thread_ts
        expected_channel = task.metadata.slack.channel
        modal_thread_ts = metadata.get("thread_ts")
        modal_channel = metadata.get("channel_id")
        if isinstance(expected_thread_ts, str) and isinstance(modal_thread_ts, str) and expected_thread_ts != modal_thread_ts:
            return {"status": "error", "message": "This Slack action no longer matches the current task thread."}
        if isinstance(expected_channel, str) and isinstance(modal_channel, str) and expected_channel != modal_channel:
            return {"status": "error", "message": "This Slack action was submitted from the wrong Slack channel."}
        state = view.get("state")
        message_text = self._extract_slack_modal_input(state, block_id="reject_verification_input", action_id="message_input")
        if not message_text:
            return {"status": "error", "message": "Request changes message is required."}
        user = payload.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        by = f"slack:{user_id}" if isinstance(user_id, str) and user_id else "slack"
        try:
            await asyncio.to_thread(self.verification_service.reject, task_id, by=by, note=message_text)
        except (TransitionError, TaskNotFoundError, CommitError, IntegrationError) as exc:
            return {"status": "error", "message": str(exc)}
        await self.rescan_and_publish()
        await asyncio.to_thread(
            self._clear_slack_action_message,
            metadata.get("channel_id"),
            metadata.get("message_ts"),
            metadata.get("message_text") or "",
        )
        return {"status": "success"}

    async def handle_slack_app_mention(self, inner_payload: dict[str, Any], event: dict[str, Any]) -> None:
        token = self.config.slack.bot_token
        channel_id = event.get("channel") if isinstance(event.get("channel"), str) else None
        allowed_channel = self.config.slack.default_channel
        raw_thread_ts = event.get("thread_ts") or event.get("ts")
        thread_ts = raw_thread_ts if isinstance(raw_thread_ts, str) and raw_thread_ts else None
        if not token or not channel_id or not thread_ts:
            return
        if not slack_channel_matches_config(token=token, configured_channel=allowed_channel, actual_channel_id=channel_id):
            return
        slack_api_call(
            "chat.postMessage",
            token=token,
            body={
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": "Ask the request-writing assistant to draft a request in this Slack thread.",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Ask the request-writing assistant to draft the request in this thread first. Review the draft replies here, revise as many times as needed, and only submit when the final request is ready.",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Draft request with assistant"},
                                "style": "primary",
                                "action_id": "open_request_intake",
                                "value": json.dumps({"action": "open_request_intake"}),
                            }
                        ],
                    },
                ],
            },
        )

    async def _handle_slack_request_intake_action(self, payload: dict[str, Any], action: dict[str, Any]) -> dict[str, object]:
        action_id = action.get("action_id")
        if not isinstance(action_id, str):
            return {"status": "noop"}
        if action_id == "open_request_intake":
            return await asyncio.to_thread(self._open_slack_request_intake_modal, payload)
        action_value = action.get("value") if isinstance(action, dict) else None
        parsed_value = None
        if isinstance(action_value, str) and action_value:
            try:
                parsed_value = json.loads(action_value)
            except json.JSONDecodeError:
                parsed_value = None
        draft_id = parsed_value.get("draft_id") if isinstance(parsed_value, dict) else None
        if action_id in {"request_intake_revise", "request_intake_submit"}:
            if not isinstance(draft_id, str) or not draft_id:
                return {"status": "error", "message": "Slack request draft action is missing draft context."}
            store = RequestDraftStore(self.config)
            try:
                draft = store.load(draft_id)
            except FileNotFoundError:
                return {"status": "error", "message": "Request draft no longer exists."}
            except ValueError as exc:
                return {"status": "error", "message": str(exc)}
            if action_id == "request_intake_revise":
                result = await asyncio.to_thread(self._open_existing_slack_request_intake_modal, payload, draft)
                if result.get("status") == "opened_modal":
                    await asyncio.to_thread(self._clear_slack_message_actions_from_payload, payload)
                return result
            result = await self._submit_slack_request_from_thread_action(payload, draft)
            if result.get("status") == "success":
                await asyncio.to_thread(self._clear_slack_message_actions_from_payload, payload)
            return result
        view = payload.get("view")
        if not isinstance(view, dict):
            return {"status": "error", "message": "Slack modal payload is invalid."}
        metadata = self._load_slack_modal_metadata(view)
        draft_id = metadata.get("draft_id") if isinstance(metadata, dict) else None
        if not isinstance(draft_id, str) or not draft_id:
            return {"status": "error", "message": "Slack modal is missing request draft context."}
        store = RequestDraftStore(self.config)
        try:
            draft = store.load(draft_id)
        except FileNotFoundError:
            return {"status": "error", "message": "Request draft no longer exists."}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        draft = self._update_slack_request_intake_draft_from_view(store, draft, view)
        if action_id == "request_intake_project_select":
            selected_project = self._extract_slack_static_select_value(view.get("state"), block_id="request_intake_project", action_id="project_select")
            if not selected_project:
                selected_option = action.get("selected_option") if isinstance(action, dict) else None
                selected_project = selected_option.get("value") if isinstance(selected_option, dict) and isinstance(selected_option.get("value"), str) else None
            if selected_project == "__empty__":
                selected_project = None
            if selected_project:
                draft = store.update(
                    draft.draft_id,
                    {
                        "target_repo": selected_project,
                        "base_branch": self._suggested_base_branch(selected_project),
                    },
                )
            return await asyncio.to_thread(self._update_slack_request_intake_view, payload, draft)
        if action_id == "request_intake_generate_draft":
            return await asyncio.to_thread(self._generate_slack_request_draft, payload, draft)
        return {"status": "noop"}

    async def _handle_slack_request_intake_submission(self, payload: dict[str, Any], view: dict[str, Any]) -> dict[str, object]:
        metadata = self._load_slack_modal_metadata(view)
        draft_id = metadata.get("draft_id") if isinstance(metadata, dict) else None
        if not isinstance(draft_id, str) or not draft_id:
            return {"status": "error", "message": "Slack modal is missing request draft context."}
        store = RequestDraftStore(self.config)
        try:
            draft = store.load(draft_id)
        except FileNotFoundError:
            return {"status": "error", "message": "Request draft no longer exists."}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        draft = self._update_slack_request_intake_draft_from_view(store, draft, view)
        message = (draft.request_draft_input or "").strip()
        if not message:
            return {
                "response_action": "errors",
                "errors": {
                    "request_intake_assistant_prompt": "Assistant request is required before posting a draft to the thread."
                },
            }
        self._background_tasks.append(
            asyncio.create_task(self._run_slack_request_draft_submission(draft.draft_id), name=f"fs-kanban-slack-request-draft-{draft.draft_id}")
        )
        return {"status": "success"}

    def _open_slack_request_intake_modal(self, payload: dict[str, Any]) -> dict[str, object]:
        trigger_id = payload.get("trigger_id")
        if not isinstance(trigger_id, str) or not trigger_id:
            return {"status": "error", "message": "Slack did not provide a trigger id for opening the modal."}
        token = self.config.slack.bot_token
        if not token:
            return {"status": "error", "message": "Slack bot token is missing."}
        user = payload.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        channel = payload.get("channel")
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        team = payload.get("team")
        team_id = team.get("id") if isinstance(team, dict) else None
        message = payload.get("message")
        thread_ts = None
        if isinstance(message, dict):
            raw_thread_ts = message.get("thread_ts") or message.get("ts")
            if isinstance(raw_thread_ts, str) and raw_thread_ts:
                thread_ts = raw_thread_ts
        store = RequestDraftStore(self.config)
        draft = store.load_or_create_for_slack_context(
            team_id=team_id if isinstance(team_id, str) else None,
            user_id=user_id if isinstance(user_id, str) else None,
            channel_id=channel_id if isinstance(channel_id, str) else None,
            thread_ts=thread_ts,
            data={"plan_auto_approve": True},
        )
        if not draft.target_repo.strip():
            recent_projects = self._slack_recent_projects()
            if recent_projects:
                first_project = recent_projects[0].get("value")
                if isinstance(first_project, str) and first_project and first_project != "__empty__":
                    draft = store.update(
                        draft.draft_id,
                        {
                            "target_repo": first_project,
                            "base_branch": self._suggested_base_branch(first_project),
                        },
                    )
        response = slack_api_call(
            "views.open",
            token=token,
            body={
                "trigger_id": trigger_id,
                "view": self._build_slack_request_intake_view(draft),
            },
        )
        if not response.get("ok"):
            return {"status": "error", "message": slack_error_message(response, fallback="Slack modal open failed.")}
        return {"status": "opened_modal", "clear_buttons": False}

    def _open_existing_slack_request_intake_modal(self, payload: dict[str, Any], draft: StoredRequestDraft) -> dict[str, object]:
        trigger_id = payload.get("trigger_id")
        if not isinstance(trigger_id, str) or not trigger_id:
            return {"status": "error", "message": "Slack did not provide a trigger id for opening the modal."}
        token = self.config.slack.bot_token
        if not token:
            return {"status": "error", "message": "Slack bot token is missing."}
        response = slack_api_call(
            "views.open",
            token=token,
            body={
                "trigger_id": trigger_id,
                "view": self._build_slack_request_intake_view(draft),
            },
        )
        if not response.get("ok"):
            return {"status": "error", "message": slack_error_message(response, fallback="Slack modal open failed.")}
        return {"status": "opened_modal", "clear_buttons": False}

    def _update_slack_request_intake_view(self, payload: dict[str, Any], draft: StoredRequestDraft) -> dict[str, object]:
        token = self.config.slack.bot_token
        if not token:
            return {"status": "error", "message": "Slack bot token is missing."}
        view = payload.get("view")
        if not isinstance(view, dict):
            return {"status": "error", "message": "Slack modal payload is invalid."}
        view_id = view.get("id")
        if not isinstance(view_id, str) or not view_id:
            return {"status": "error", "message": "Slack modal payload is invalid."}
        body: dict[str, object] = {"view_id": view_id, "view": self._build_slack_request_intake_view(draft)}
        view_hash = view.get("hash")
        if isinstance(view_hash, str) and view_hash:
            body["hash"] = view_hash
        response = slack_api_call("views.update", token=token, body=body)
        if not response.get("ok"):
            return {"status": "error", "message": slack_error_message(response, fallback="Slack modal update failed.")}
        return {"status": "success", "clear_buttons": False}

    def _ensure_slack_request_reopen_message(self, draft: StoredRequestDraft) -> None:
        token = self.config.slack.bot_token
        channel_id = draft.slack_channel_id or None
        thread_ts = draft.slack_thread_ts or None
        if not token or not channel_id or not thread_ts:
            return
        store = RequestDraftStore(self.config)
        existing_ts = draft.slack_reopen_message_ts.strip()
        existing_text = draft.slack_reopen_message_text.strip() or "Reopen the request draft modal if you closed it by mistake."
        if existing_ts:
            slack_api_call(
                "chat.update",
                token=token,
                body={
                    "channel": channel_id,
                    "ts": existing_ts,
                    "text": existing_text,
                    "blocks": self._build_slack_request_reopen_blocks(draft.draft_id),
                },
            )
            return
        text = "Reopen the request draft modal if you closed it by mistake."
        response = slack_api_call(
            "chat.postMessage",
            token=token,
            body={
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": text,
                "blocks": self._build_slack_request_reopen_blocks(draft.draft_id),
            },
        )
        message_ts = response.get("ts")
        if isinstance(message_ts, str) and message_ts:
            store.update(
                draft.draft_id,
                {
                    "slack_reopen_message_ts": message_ts,
                    "slack_reopen_message_text": text,
                },
            )

    def _build_slack_request_reopen_blocks(self, draft_id: str) -> list[dict[str, object]]:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Need to continue later? Reopen the draft modal from this thread message.",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Draft request with assistant"},
                        "style": "primary",
                        "action_id": "open_request_intake",
                        "value": json.dumps({"action": "open_request_intake", "draft_id": draft_id}),
                    }
                ],
            },
        ]

    def _generate_slack_request_draft(self, payload: dict[str, Any], draft: StoredRequestDraft) -> dict[str, object]:
        try:
            updated, result = self._generate_slack_request_draft_core(draft)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        self._post_slack_request_draft_review(updated, reply=result.reply, field_updates=cast(Mapping[str, object], result.field_updates))
        view = payload.get("view")
        if not isinstance(view, dict) or not isinstance(view.get("id"), str) or not view.get("id"):
            return {"status": "success"}
        return self._update_slack_request_intake_view(payload, updated)

    def _generate_slack_request_draft_core(self, draft: StoredRequestDraft) -> tuple[StoredRequestDraft, object]:
        message = (draft.request_draft_input or "").strip()
        if not message:
            raise ValueError("Draft prompt is required before generating a request draft.")
        result = draft_request(
            config=self.config,
            adapter_registry=cast(dict[str, AssistantAdapter], self.adapter_registry),
            payload=draft.model_copy(update={"request_draft_input": ""}).to_drafting_payload(message=message),
        )
        store = RequestDraftStore(self.config)
        updated_fields: dict[str, object] = {k: v for k, v in result.field_updates.items() if k in {
            "title", "goal", "background", "scope", "out_of_scope", "constraints", "references", "acceptance_criteria", "target_repo", "base_branch"
        }}
        if "target_repo" in updated_fields and isinstance(updated_fields["target_repo"], str) and updated_fields["target_repo"].strip():
            updated_fields["base_branch"] = self._suggested_base_branch(updated_fields["target_repo"])
        updated = store.update(
            draft.draft_id,
            {
                **updated_fields,
                "request_draft_input": "",
                "transcript": [
                    *draft.transcript,
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": result.reply, "field_updates": result.field_updates},
                ],
            },
        )
        return updated, result

    def _build_slack_request_intake_view(self, draft: StoredRequestDraft) -> dict[str, object]:
        project_options = self._slack_recent_projects()
        selected_project = next((item for item in project_options if item["value"] == draft.target_repo.strip()), None)
        metadata = json.dumps({"draft_id": draft.draft_id})
        blocks: list[dict[str, object]] = [
            {
                "type": "section",
                "block_id": "request_intake_intro",
                "text": {
                    "type": "mrkdwn",
                    "text": "Draft with the request-writing assistant first. Each assistant reply will be posted into the Slack thread for review, and only *Submit final request* creates the real task.",
                },
            },
            {
                "type": "input",
                "block_id": "request_intake_project",
                "label": {"type": "plain_text", "text": "Project"},
                "element": {
                    "type": "static_select",
                    "action_id": "project_select",
                    "placeholder": {"type": "plain_text", "text": "Choose a previously used project"},
                    "options": project_options or [{"text": {"type": "plain_text", "text": "No previous projects found"}, "value": "__empty__"}],
                    **({"initial_option": selected_project} if selected_project else {}),
                },
            },
            {
                "type": "input",
                "block_id": "request_intake_base_branch",
                "label": {"type": "plain_text", "text": "Base branch"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "base_branch_input",
                    "initial_value": (draft.base_branch or self.config.base_branch).strip() or self.config.base_branch,
                },
            },
            {
                "type": "input",
                "block_id": "request_intake_assistant_prompt",
                "label": {"type": "plain_text", "text": "Ask the request-writing assistant"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "assistant_prompt_input",
                    "multiline": True,
                    "initial_value": draft.request_draft_input,
                },
            },
        ]
        if draft.transcript:
            draft_count = sum(1 for entry in draft.transcript if entry.role == "assistant")
            blocks.append(
                {
                    "type": "context",
                    "block_id": "request_intake_thread_review_hint",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Review happens in the Slack thread. {draft_count} assistant draft{'s' if draft_count != 1 else ''} posted so far.",
                        }
                    ],
                }
            )
        summary_fields = self._slack_request_summary_fields(draft)
        if summary_fields:
            blocks.append(
                {
                    "type": "section",
                    "block_id": "request_intake_current_summary",
                    "fields": summary_fields,
                }
            )
        return {
            "type": "modal",
            "callback_id": "request_intake_modal",
            "private_metadata": metadata,
            "title": {"type": "plain_text", "text": "Draft request"},
            "submit": {"type": "plain_text", "text": "Post draft to thread"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": blocks,
        }

    async def _run_slack_request_draft_submission(self, draft_id: str) -> None:
        store = RequestDraftStore(self.config)
        try:
            draft = store.load(draft_id)
        except (FileNotFoundError, ValueError):
            return
        placeholder_ts = await asyncio.to_thread(self._post_slack_request_draft_placeholder, draft)
        try:
            updated, result = await asyncio.to_thread(self._generate_slack_request_draft_core, draft)
        except Exception as exc:
            await asyncio.to_thread(self._update_slack_request_draft_placeholder_error, draft, placeholder_ts, str(exc))
            return
        await asyncio.to_thread(
            self._finalize_slack_request_draft_placeholder,
            updated,
            placeholder_ts,
            result.reply,
            cast(Mapping[str, object], result.field_updates),
        )

    def _post_slack_request_draft_placeholder(self, draft: StoredRequestDraft) -> str | None:
        token = self.config.slack.bot_token
        channel_id = draft.slack_channel_id or None
        thread_ts = draft.slack_thread_ts or None
        if not token or not channel_id or not thread_ts:
            return None
        response = slack_api_call(
            "chat.postMessage",
            token=token,
            body={
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": "Writing request draft…",
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "✍️ *응답 작성중…*\nThe request-writing assistant is preparing a draft for this thread."},
                    }
                ],
            },
        )
        ts = response.get("ts")
        return ts if isinstance(ts, str) and ts else None

    def _update_slack_request_draft_placeholder_error(self, draft: StoredRequestDraft, message_ts: str | None, error: str) -> None:
        token = self.config.slack.bot_token
        channel_id = draft.slack_channel_id or None
        if not token or not channel_id or not isinstance(message_ts, str) or not message_ts:
            return
        slack_api_call(
            "chat.update",
            token=token,
            body={
                "channel": channel_id,
                "ts": message_ts,
                "text": "Request draft failed.",
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"⚠️ *Draft generation failed*\n{error}"[:2900]},
                    }
                ],
            },
        )

    def _finalize_slack_request_draft_placeholder(
        self,
        draft: StoredRequestDraft,
        message_ts: str | None,
        reply: str,
        field_updates: Mapping[str, object],
    ) -> None:
        token = self.config.slack.bot_token
        channel_id = draft.slack_channel_id or None
        if not token or not channel_id or not isinstance(message_ts, str) or not message_ts:
            self._post_slack_request_draft_review(draft, reply=reply, field_updates=field_updates)
            return
        draft_number = sum(1 for entry in draft.transcript if entry.role == "assistant")
        filename = f"REQUEST-DRAFT-{draft_number:03d}.md"
        draft_markdown = self._render_slack_request_preview_markdown(draft)
        upload_result = slack_upload_file_to_thread(
            token=token,
            channel_id=channel_id,
            thread_ts=draft.slack_thread_ts or message_ts,
            filename=filename,
            title=filename,
            content=draft_markdown.encode("utf-8"),
        )
        response = slack_api_call(
            "chat.update",
            token=token,
            body={
                "channel": channel_id,
                "ts": message_ts,
                "text": f"Assistant draft {draft_number} ready for review.",
                "blocks": self._build_slack_request_draft_review_blocks(
                    draft,
                    draft_number=draft_number,
                    reply=reply,
                    field_updates=field_updates,
                    draft_filename=filename,
                    upload_ok=bool(upload_result.get("ok")),
                ),
            },
        )
        if response.get("ok"):
            return
        logger.warning(
            "slack request draft placeholder update failed: %s",
            slack_error_message(response, fallback="Slack chat.update failed."),
            extra={
                "draft_id": draft.draft_id,
                "channel": channel_id,
                "thread_ts": draft.slack_thread_ts or message_ts,
                "message_ts": message_ts,
                "draft_number": draft_number,
            },
        )
        self._post_slack_request_review_message(
            channel_id=channel_id,
            thread_ts=draft.slack_thread_ts or message_ts,
            draft=draft,
            draft_number=draft_number,
            reply=reply,
            field_updates=field_updates,
            draft_filename=filename,
            upload_ok=bool(upload_result.get("ok")),
        )

    def _update_slack_request_intake_draft_from_view(self, store: RequestDraftStore, draft: StoredRequestDraft, view: dict[str, Any]) -> StoredRequestDraft:
        state = view.get("state")
        base_branch = self._extract_slack_modal_input(state, block_id="request_intake_base_branch", action_id="base_branch_input") or self.config.base_branch
        assistant_prompt = self._extract_slack_modal_input(state, block_id="request_intake_assistant_prompt", action_id="assistant_prompt_input") or ""
        selected_project = self._extract_slack_static_select_value(state, block_id="request_intake_project", action_id="project_select")
        return store.update(
            draft.draft_id,
            {
                "target_repo": selected_project or draft.target_repo,
                "base_branch": base_branch,
                "request_draft_input": assistant_prompt,
            },
        )

    def _extract_slack_static_select_value(self, state: object, *, block_id: str, action_id: str) -> str | None:
        if not isinstance(state, dict):
            return None
        values = state.get("values")
        if not isinstance(values, dict):
            return None
        block = values.get(block_id)
        if not isinstance(block, dict):
            return None
        action = block.get(action_id)
        if not isinstance(action, dict):
            return None
        selected = action.get("selected_option")
        if not isinstance(selected, dict):
            return None
        value = selected.get("value")
        if not isinstance(value, str) or not value or value == "__empty__":
            return None
        return value

    def _load_slack_modal_metadata(self, view: dict[str, Any]) -> dict[str, Any] | None:
        raw_metadata = view.get("private_metadata")
        if not isinstance(raw_metadata, str) or not raw_metadata:
            return None
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return None
        return metadata if isinstance(metadata, dict) else None

    def _slack_recent_projects(self) -> list[dict[str, object]]:
        seen: set[str] = set()
        options: list[dict[str, object]] = []
        for task in sorted(self.scanner.scan(), key=lambda item: item.metadata.updated_at, reverse=True):
            repo_root = (task.metadata.target.repo_root or "").strip()
            if not repo_root or repo_root in seen:
                continue
            seen.add(repo_root)
            label = repo_root if len(repo_root) <= 75 else f"…{repo_root[-72:]}"
            options.append({"text": {"type": "plain_text", "text": label}, "value": repo_root})
        return options

    def _suggested_base_branch(self, target_repo: str) -> str:
        try:
            snapshot = describe_target_repo_branches(self.config, Path(target_repo))
        except (ValueError, AdapterRunError):
            return self.config.base_branch
        return (snapshot.suggested_base_branch or snapshot.current_branch or self.config.base_branch).strip() or self.config.base_branch

    def _publish_slack_request_created_summary(self, channel_id: str | None, thread_ts: str | None, task_id: str) -> None:
        token = self.config.slack.bot_token
        if not token or not channel_id or not thread_ts:
            return
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError:
            return
        notifier = SlackMilestoneNotifier(self.config, self.scanner.metadata_store)
        text = notifier._build_message(
            task,
            milestone="Request created",
            previous_state=task.state,
            by="slack",
            note=None,
            is_parent_message=True,
        )
        blocks = notifier._build_parent_blocks(task, milestone="Request created")
        response = slack_api_call(
            "chat.update",
            token=token,
            body={
                "channel": channel_id,
                "ts": thread_ts,
                "text": text,
                "blocks": blocks,
            },
        )
        if response.get("ok"):
            return
        slack_api_call(
            "chat.postMessage",
            token=token,
            body={
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": text,
                "blocks": blocks,
            },
        )

    def _post_slack_request_draft_review(self, draft: StoredRequestDraft, *, reply: str, field_updates: Mapping[str, object]) -> None:
        token = self.config.slack.bot_token
        channel_id = draft.slack_channel_id or None
        thread_ts = draft.slack_thread_ts or None
        if not token or not channel_id or not thread_ts:
            return
        draft_number = sum(1 for entry in draft.transcript if entry.role == "assistant")
        filename = f"REQUEST-DRAFT-{draft_number:03d}.md"
        draft_markdown = self._render_slack_request_preview_markdown(draft)
        upload_result = slack_upload_file_to_thread(
            token=token,
            channel_id=channel_id,
            thread_ts=thread_ts,
            filename=filename,
            title=filename,
            content=draft_markdown.encode("utf-8"),
        )
        self._post_slack_request_review_message(
            channel_id=channel_id,
            thread_ts=thread_ts,
            draft=draft,
            draft_number=draft_number,
            reply=reply,
            field_updates=field_updates,
            draft_filename=filename,
            upload_ok=bool(upload_result.get("ok")),
        )

    def _post_slack_request_review_message(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        draft: StoredRequestDraft,
        draft_number: int,
        reply: str,
        field_updates: Mapping[str, object],
        draft_filename: str,
        upload_ok: bool,
    ) -> None:
        token = self.config.slack.bot_token
        if not token:
            return
        response = slack_api_call(
            "chat.postMessage",
            token=token,
            body={
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": f"Assistant draft {draft_number} ready for review.",
                "blocks": self._build_slack_request_draft_review_blocks(
                    draft,
                    draft_number=draft_number,
                    reply=reply,
                    field_updates=field_updates,
                    draft_filename=draft_filename,
                    upload_ok=upload_ok,
                ),
            },
        )
        if response.get("ok"):
            return
        logger.warning(
            "slack request draft review post failed: %s",
            slack_error_message(response, fallback="Slack chat.postMessage failed."),
            extra={
                "draft_id": draft.draft_id,
                "channel": channel_id,
                "thread_ts": thread_ts,
                "draft_number": draft_number,
            },
        )

    def _build_slack_request_draft_review_blocks(
        self,
        draft: StoredRequestDraft,
        *,
        draft_number: int,
        reply: str,
        field_updates: Mapping[str, object],
        draft_filename: str,
        upload_ok: bool,
    ) -> list[dict[str, object]]:
        blocks: list[dict[str, object]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"📝 *Assistant draft {draft_number} ready for review*"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": reply[:3000] or "(no assistant reply)"},
            },
        ]
        summary_fields = self._slack_request_summary_fields(draft)
        if summary_fields:
            blocks.append({"type": "section", "fields": summary_fields})
        update_lines = self._format_slack_field_updates(field_updates)
        if update_lines:
            update_text = self._clamp_slack_text("*Suggested updates*\n" + "\n".join(update_lines), limit=SLACK_SECTION_TEXT_LIMIT)
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": update_text},
                }
            )
        attachment_line = f"Attached markdown draft: `{draft_filename}`" if upload_ok else f"Draft markdown upload failed for `{draft_filename}`."
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": attachment_line},
            }
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Submit final request"},
                        "style": "primary",
                        "action_id": "request_intake_submit",
                        "value": json.dumps({"draft_id": draft.draft_id}),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Request another draft"},
                        "action_id": "request_intake_revise",
                        "value": json.dumps({"draft_id": draft.draft_id}),
                    },
                ],
            }
        )
        return blocks

    def _slack_request_summary_fields(self, draft: StoredRequestDraft) -> list[dict[str, str]]:
        fields: list[dict[str, str]] = []
        if draft.title.strip():
            fields.append({"type": "mrkdwn", "text": self._clamp_slack_text(f"*Title*\n{draft.title.strip()}", limit=SLACK_FIELD_TEXT_LIMIT)})
        if draft.goal.strip():
            fields.append({"type": "mrkdwn", "text": self._clamp_slack_text(f"*Goal*\n{draft.goal.strip()}", limit=SLACK_FIELD_TEXT_LIMIT)})
        if draft.target_repo.strip():
            fields.append({"type": "mrkdwn", "text": self._clamp_slack_text(f"*Project*\n`{draft.target_repo.strip()}`", limit=SLACK_FIELD_TEXT_LIMIT)})
        if draft.base_branch.strip():
            fields.append({"type": "mrkdwn", "text": self._clamp_slack_text(f"*Base branch*\n`{draft.base_branch.strip()}`", limit=SLACK_FIELD_TEXT_LIMIT)})
        return fields[:10]

    def _format_slack_field_updates(self, field_updates: Mapping[str, object]) -> list[str]:
        labels = {
            "title": "Title",
            "goal": "Goal",
            "background": "Background",
            "scope": "Scope",
            "out_of_scope": "Out of scope",
            "constraints": "Constraints",
            "references": "References",
            "acceptance_criteria": "Acceptance criteria",
            "target_repo": "Project",
            "base_branch": "Base branch",
        }
        lines: list[str] = []
        for field_name, value in field_updates.items():
            label = labels.get(field_name, field_name)
            formatted = self._format_slack_field_update_value(value)
            lines.append(f"• *{label}*: {formatted}")
        return lines[:12]

    def _format_slack_field_update_value(self, value: object) -> str:
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return "; ".join(items)[:1200] if items else "(clear field)"
        if isinstance(value, str):
            normalized = value.strip()
            return normalized[:1200] if normalized else "(clear field)"
        if value is None:
            return "(clear field)"
        normalized = str(value).strip()
        return normalized[:1200] if normalized else "(clear field)"

    def _clamp_slack_text(self, text: str, *, limit: int) -> str:
        normalized = text.strip()
        if len(normalized) <= limit:
            return normalized
        if limit <= 1:
            return normalized[:limit]
        return normalized[: limit - 1].rstrip() + "…"

    async def _submit_slack_request_from_thread_action(self, payload: dict[str, Any], draft: StoredRequestDraft) -> dict[str, object]:
        if not any(entry.role == "assistant" and (entry.content or "").strip() for entry in draft.transcript):
            return {"status": "error", "message": "Ask the assistant for at least one draft before submitting the final request."}
        if not draft.title.strip() or not draft.goal.strip() or not draft.target_repo.strip() or not draft.base_branch.strip():
            return {"status": "error", "message": "The assistant draft is still missing title, goal, project, or base branch."}
        try:
            task_dir = await asyncio.to_thread(
                self.create_request_from_submission,
                title=draft.title,
                goal=draft.goal,
                background=draft.background,
                plan_auto_approve=draft.plan_auto_approve,
                scope=draft.scope,
                out_of_scope=draft.out_of_scope,
                constraints=draft.constraints,
                references=draft.references,
                acceptance_criteria=draft.acceptance_criteria,
                target_repo=draft.target_repo,
                base_branch=draft.base_branch,
                request_upload_token=draft.request_upload_token or None,
                request_draft_id=draft.draft_id,
                request_draft_markdown=None,
                slack_channel_id=draft.slack_channel_id or None,
                slack_thread_ts=draft.slack_thread_ts or None,
            )
        except (ValueError, AdapterRunError) as exc:
            return {"status": "error", "message": str(exc)}
        await self.rescan_and_publish()
        await asyncio.to_thread(
            self._publish_slack_request_created_summary,
            draft.slack_channel_id,
            draft.slack_thread_ts,
            task_dir.name,
        )
        await asyncio.to_thread(
            self._clear_slack_action_message,
            draft.slack_channel_id,
            draft.slack_reopen_message_ts or None,
            draft.slack_reopen_message_text or None,
        )
        return {"status": "success"}

    def _clear_slack_message_actions_from_payload(self, payload: dict[str, Any]) -> None:
        token = self.config.slack.bot_token
        if not token:
            return
        channel = payload.get("channel")
        message = payload.get("message")
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        message_ts = message.get("ts") if isinstance(message, dict) else None
        text = message.get("text") if isinstance(message, dict) else ""
        blocks = message.get("blocks") if isinstance(message, dict) else None
        if not isinstance(channel_id, str) or not isinstance(message_ts, str):
            return
        next_blocks = [block for block in blocks if not (isinstance(block, dict) and block.get("type") == "actions")] if isinstance(blocks, list) else []
        slack_api_call(
            "chat.update",
            token=token,
            body={
                "channel": channel_id,
                "ts": message_ts,
                "text": text if isinstance(text, str) else "",
                "blocks": next_blocks,
            },
        )

    def _render_slack_request_preview_markdown(self, draft: StoredRequestDraft) -> str:
        lines = [
            "---",
            f"title: {draft.title.strip() or 'Untitled request draft'}",
            f"language: {runtime_language_code_to_request_language(self.config.runtime.language)}",
            f"plan_auto_approve: {'true' if draft.plan_auto_approve else 'false'}",
            "target:",
            f"  repo_root: {draft.target_repo.strip() or '(unset)'}",
            f"  base_branch: {draft.base_branch.strip() or self.config.base_branch}",
            "---",
            "",
            f"# {draft.title.strip() or 'Untitled request draft'}",
            "",
        ]
        sections: list[tuple[str, str | list[str] | None]] = [
            ("Goal", draft.goal),
            ("Background", draft.background),
            ("Scope", draft.scope),
            ("Out of Scope", draft.out_of_scope),
            ("Constraints", draft.constraints),
            ("References", draft.references),
            ("Acceptance Criteria", draft.acceptance_criteria),
        ]
        for heading, value in sections:
            rendered = self._render_slack_request_section_value(value)
            if not rendered:
                continue
            lines.append(f"## {heading}")
            lines.append(rendered)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _render_slack_request_section_value(self, value: str | list[str] | None) -> str:
        if isinstance(value, list):
            normalized = [item.strip() for item in value if item.strip()]
            return "\n".join(f"- {item}" for item in normalized)
        if isinstance(value, str):
            return value.strip()
        return ""

    def create_request_from_submission(
        self,
        *,
        title: str,
        goal: str,
        background: str | None,
        plan_auto_approve: bool,
        scope: str | None,
        out_of_scope: str | None,
        constraints: str | None,
        references: str | None,
        acceptance_criteria: str | None,
        target_repo: str,
        base_branch: str | None,
        request_upload_token: str | None,
        request_draft_id: str | None,
        request_draft_markdown: str | None,
        slack_channel_id: str | None = None,
        slack_thread_ts: str | None = None,
    ) -> Path:
        normalized_base_branch = base_branch.strip() if base_branch else self.config.base_branch
        request_language = runtime_language_code_to_request_language(self.config.runtime.language)
        request_draft_store = RequestDraftStore(self.config)
        stored_draft = None
        if request_draft_id:
            stored_draft = request_draft_store.load(request_draft_id)
        draft_markdown = request_draft_markdown
        if stored_draft is not None:
            draft_markdown = serialize_request_draft_transcript_markdown(stored_draft, language_code=request_language)
        default_scope, default_out_of_scope = build_default_scope_sections_for_language(
            target_repo,
            language_code=request_language,
            managed_docs_root=self.config.target_repo_docs_root_value(),
        )
        task_dir = create_request(
            self.config,
            template=RequestTemplateData(
                title=title.strip(),
                goal=goal.strip(),
                background=background.strip() if background else None,
                plan_auto_approve=plan_auto_approve,
                scope=split_lines(scope) or default_scope,
                out_of_scope=split_lines(out_of_scope) or default_out_of_scope,
                constraints=split_lines(constraints),
                references=split_lines(references),
                acceptance_criteria=split_lines(acceptance_criteria),
            ),
            target_repo_root=Path(target_repo),
            base_branch=normalized_base_branch,
            request_upload_token=request_upload_token,
            request_draft_markdown=draft_markdown,
        )
        self.scanner.scan()
        if slack_channel_id or slack_thread_ts:
            task = self.scanner.find_task(task_dir.name)
            task.metadata.slack.channel = slack_channel_id
            task.metadata.slack.thread_ts = slack_thread_ts
            self.scanner.metadata_store.save(task.task_dir, task.metadata)
        if stored_draft is not None:
            request_draft_store.delete(stored_draft.draft_id)
        return task_dir

    def _open_slack_resume_review_loop_modal(self, payload: dict[str, Any], task_id: str, by: str) -> dict[str, object]:
        return self._open_slack_message_modal(
            payload,
            task_id,
            callback_id="resume_review_loop_modal",
            title="Resume review",
            submit="Resume",
            block_id="resume_review_loop_input",
            initial_value=f"requested via Slack by {by}",
        )

    def _open_slack_reject_verification_modal(self, payload: dict[str, Any], task_id: str, by: str) -> dict[str, object]:
        return self._open_slack_message_modal(
            payload,
            task_id,
            callback_id="reject_verification_modal",
            title="Request changes",
            submit="Request changes",
            block_id="reject_verification_input",
            initial_value=f"requested via Slack by {by}",
        )

    def _open_slack_message_modal(
        self,
        payload: dict[str, Any],
        task_id: str,
        *,
        callback_id: str,
        title: str,
        submit: str,
        block_id: str,
        initial_value: str,
    ) -> dict[str, object]:
        trigger_id = payload.get("trigger_id")
        if not isinstance(trigger_id, str) or not trigger_id:
            return {"status": "error", "message": "Slack did not provide a trigger id for opening the modal."}
        token = self.config.slack.bot_token
        if not token:
            return {"status": "error", "message": "Slack bot token is missing."}
        channel = payload.get("channel")
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        message = payload.get("message")
        message_ts = message.get("ts") if isinstance(message, dict) else None
        message_text = message.get("text") if isinstance(message, dict) else ""
        thread_ts = None
        if isinstance(message, dict):
            raw_thread_ts = message.get("thread_ts") or message.get("ts")
            if isinstance(raw_thread_ts, str) and raw_thread_ts:
                thread_ts = raw_thread_ts
        response = slack_api_call(
            "views.open",
            token=token,
            body={
                "trigger_id": trigger_id,
                "view": {
                    "type": "modal",
                    "callback_id": callback_id,
                    "private_metadata": json.dumps(
                        {
                            "task_id": task_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "message_ts": message_ts,
                            "message_text": message_text,
                        }
                    ),
                    "title": {"type": "plain_text", "text": title},
                    "submit": {"type": "plain_text", "text": submit},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": block_id,
                            "label": {"type": "plain_text", "text": "Message"},
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "message_input",
                                "multiline": True,
                                "initial_value": initial_value,
                            },
                        }
                    ],
                },
            },
        )
        if not response.get("ok"):
            return {"status": "error", "message": slack_error_message(response, fallback="Slack modal open failed.")}
        return {"status": "opened_modal", "clear_buttons": False}

    def _clear_slack_action_message(self, channel_id: object, message_ts: object, text: object) -> None:
        token = self.config.slack.bot_token
        if not token or not isinstance(channel_id, str) or not isinstance(message_ts, str):
            return
        slack_api_call(
            "chat.update",
            token=token,
            body={
                "channel": channel_id,
                "ts": message_ts,
                "text": text if isinstance(text, str) else "",
                "blocks": [],
            },
        )

    def _extract_slack_modal_input(self, state: object, *, block_id: str, action_id: str) -> str | None:
        if not isinstance(state, dict):
            return None
        values = state.get("values")
        if not isinstance(values, dict):
            return None
        block = values.get(block_id)
        if not isinstance(block, dict):
            return None
        action = block.get(action_id)
        if not isinstance(action, dict):
            return None
        value = action.get("value")
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    async def force_delete(self, task_id: str, *, by: str) -> None:
        await self.cancel_task(task_id)
        await asyncio.to_thread(self.deletion_service.delete, task_id, by=by)

    async def cancel_task(self, task_id: str) -> None:
        tasks_to_cancel: list[asyncio.Task[None]] = []
        for role_tasks in self._role_tasks.values():
            for task in list(role_tasks):
                if task.get_name().endswith(f"-{task_id}"):
                    tasks_to_cancel.append(task)
        for adapter in self._task_adapters:
            adapter.cancel_task(task_id)
        for task in tasks_to_cancel:
            task.cancel()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self._inflight_task_ids.discard(task_id)

    async def dispatch_once(self) -> bool:
        scheduled = False
        self._prune_role_tasks()
        inflight = set(self._inflight_task_ids)
        for role_name, worker, configured_count in self._worker_specs():
            available_slots = max(0, configured_count - len(self._role_tasks[role_name]))
            if available_slots == 0:
                continue
            candidates = [
                task
                for task in worker.candidate_tasks()
                if task.metadata.task_id not in inflight
            ]
            for task in candidates[:available_slots]:
                task_id = task.metadata.task_id
                inflight.add(task_id)
                self._inflight_task_ids.add(task_id)
                scheduled = True
                background_task = asyncio.create_task(
                    self._run_worker_task(role_name, task_id, worker, task),
                    name=f"fs-kanban-{role_name}-{task_id}",
                )
                self._role_tasks[role_name].add(background_task)
                background_task.add_done_callback(
                    lambda done_task, *, role=role_name, finished_task_id=task_id: self._finalize_role_task(role, finished_task_id, done_task)
                )
        return scheduled

    async def startup_recovery(self) -> None:
        for event in self.recovery.recover():
            await self.events.publish(event)
        await self.rescan_and_publish()

    async def dispatch_forever(self) -> None:
        while not self._stop_event.is_set():
            changed = await self.dispatch_once()
            if not changed:
                await asyncio.sleep(self.config.runtime.poll_interval_seconds)

    async def watch_forever(self) -> None:
        async for _changes in awatch(self.config.kanban_root, stop_event=self._stop_event):
            if not self._should_rescan_for_changes(_changes):
                continue
            await asyncio.sleep(self.config.runtime.poll_interval_seconds)
            await self.rescan_and_publish()

    def _create_supervised_task(self, name: str, runner: Any) -> asyncio.Task[None]:
        return asyncio.create_task(self._run_supervised(name, runner), name=name)

    async def _run_supervised(self, name: str, runner: Any) -> None:
        restart_delay = max(self.config.runtime.poll_interval_seconds, 0.1)
        while not self._stop_event.is_set():
            try:
                await runner()
                if not self._stop_event.is_set():
                    logger.warning("background task exited unexpectedly; restarting", extra={"task_name": name})
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("background task crashed; restarting", extra={"task_name": name})
            if self._stop_event.is_set():
                return
            await asyncio.sleep(restart_delay)

    def _worker_specs(self) -> Iterable[tuple[str, DispatchWorker, int]]:
        return (
            ("planner", self.planner, self.config.runtime.planner_agent_count),
            ("plan_approval", self.plan_approval, 1),
            ("implementer", self.implementer, self.config.runtime.implementer_agent_count),
            ("reviewer", self.reviewer, self.config.runtime.reviewer_agent_count),
        )

    def _should_rescan_for_changes(self, changes: Iterable[tuple[object, str]]) -> bool:
        runtime_root = (self.config.kanban_root / "_runtime").expanduser().resolve()
        for _change, raw_path in changes:
            path = Path(raw_path).expanduser().resolve()
            try:
                path.relative_to(runtime_root)
            except ValueError:
                return True
        return False

    def _collect_task_adapters(self) -> Iterable[AssistantAdapter | None]:
        seen: set[int] = set()
        registry = getattr(self, "adapter_registry", {})
        for adapter in (
            getattr(self.planner, "adapter", None),
            *getattr(self.planner, "adapter_registry", {}).values(),
            getattr(self.plan_approval, "adapter", None),
            *getattr(self.plan_approval, "adapter_registry", {}).values(),
            getattr(self.implementer, "adapter", None),
            *getattr(self.implementer, "adapter_registry", {}).values(),
            getattr(self.reviewer, "adapter", None),
            *getattr(self.reviewer, "adapter_registry", {}).values(),
            getattr(self.committer, "adapter", None),
            *registry.values(),
        ):
            if adapter is None:
                continue
            identifier = id(adapter)
            if identifier in seen:
                continue
            seen.add(identifier)
            yield adapter

    async def _run_worker_task(self, role_name: str, task_id: str, worker: DispatchWorker, task: TaskContext) -> None:
        try:
            changed = await worker.run_task(task)
            if changed:
                await self.rescan_and_publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.rescan_and_publish()
            raise

    def _prune_role_tasks(self) -> None:
        for role_name, tasks in self._role_tasks.items():
            finished = {task for task in tasks if task.done()}
            for task in finished:
                tasks.discard(task)
                try:
                    task.result()
                except Exception:
                    pass

    def _finalize_role_task(self, role_name: str, task_id: str, task: asyncio.Task[None]) -> None:
        self._role_tasks[role_name].discard(task)
        self._inflight_task_ids.discard(task_id)
        try:
            task.result()
        except Exception:
            pass


def board_to_event(board):
    from .models import WorkerEvent

    return WorkerEvent(event="board_snapshot", payload=board.model_dump(mode="json"))


def build_runtime(
    config: AppConfig,
    planner_adapter: AssistantAdapter,
    implementer_adapter: AssistantAdapter,
    reviewer_adapter: AssistantAdapter,
    commit_adapter: AssistantAdapter | None = None,
    branch_summary_adapter: AssistantAdapter | None = None,
    adapter_registry: dict[AssistantBackend, AssistantAdapter] | None = None,
):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    slack_notifier = SlackMilestoneNotifier(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks, slack_notifier=slack_notifier)
    events = EventBus()
    from .workspace_manager import WorkspaceManager
    from .commit_manager import CommitManager
    from .integration_manager import IntegrationManager

    workspace_manager = WorkspaceManager(config)
    integration_manager = IntegrationManager(config)
    commit_manager = CommitManager()
    from .assistant_factory import build_adapter_registry

    registry = build_adapter_registry()
    registry.update(dict(adapter_registry or {}))
    registry[config.backend_for_role("planner")] = planner_adapter
    registry.setdefault(config.backend_for_role("plan_approval"), planner_adapter)
    registry.setdefault(config.backend_for_role("implementer"), implementer_adapter)
    registry.setdefault(config.backend_for_role("reviewer"), reviewer_adapter)
    if commit_adapter is not None:
        registry.setdefault(config.backend_for_role("commit"), commit_adapter)
    plan_approval_adapter: AssistantAdapter = registry.get(config.backend_for_role("plan_approval"), planner_adapter)
    planner = PlanningWorker(config, scanner, metadata_store, locks, transitions, events, adapter=planner_adapter, adapter_registry=registry)
    plan_approval = PlanApprovalWorker(config, scanner, metadata_store, locks, transitions, events, adapter=plan_approval_adapter, adapter_registry=registry)
    implementer = ImplementerWorker(config, scanner, metadata_store, locks, transitions, events, adapter=implementer_adapter, workspace_manager=workspace_manager, adapter_registry=registry)
    reviewer = ReviewerWorker(config, scanner, metadata_store, locks, transitions, events, adapter=reviewer_adapter, integration_manager=integration_manager, adapter_registry=registry)
    committer = CommitWorker(config, scanner, metadata_store, locks, transitions, events, adapter=commit_adapter)
    board_service = BoardService(scanner)
    verification_service = HumanVerificationService(scanner, config, metadata_store, locks, transitions, integration_manager, commit_manager, branch_summary_adapter=branch_summary_adapter, adapter_registry=cast(dict[str | AssistantBackend, AssistantAdapter], registry))
    deletion_service = TaskDeletionService(config, scanner, locks, integration_manager)
    task_service = TaskService(
        scanner,
        config.runs_dir,
        config.kanban_root,
        config.archive_runs_dir,
        metadata_store=metadata_store,
        transitions=transitions,
        locks=locks,
    )
    retrospective_service = RetrospectiveService(scanner, config, locks, commit_manager, adapter=commit_adapter)
    recovery = RecoveryService(config, scanner, transitions, locks)
    model_registry = build_backend_manager(config=config, adapter_registry=registry)
    runtime = RuntimeSupervisor(
        config,
        planner,
        plan_approval,
        implementer,
        reviewer,
        committer,
        scanner,
        board_service,
        verification_service,
        deletion_service,
        task_service,
        retrospective_service,
        recovery,
        events,
        model_registry,
    )
    runtime.adapter_registry = registry
    runtime.slack_runtime = SlackRuntime(
        config,
        events,
        action_handler=runtime.handle_slack_interactive_action,
        mention_handler=runtime.handle_slack_app_mention,
    )
    return runtime
