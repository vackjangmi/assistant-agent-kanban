from __future__ import annotations

import asyncio
from collections.abc import Iterable
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
from .scanner import KanbanScanner
from .services.board_service import BoardService
from .services.human_verification_service import HumanVerificationService
from .services.retrospective_service import RetrospectiveService
from .services.task_deletion_service import TaskDeletionService
from .services.task_service import TaskService
from .transitions import TransitionManager
from .assistant_adapter import AssistantAdapter, AssistantBackendManager, build_backend_manager
from .exceptions import CommitError, IntegrationError, TaskNotFoundError, TransitionError
from .slack_api import slack_api_call, slack_error_message
from .slack_notifications import SlackMilestoneNotifier
from .slack_runtime import SlackRuntime
from .workers.committer import CommitWorker
from .workers.implementer import ImplementerWorker
from .workers.plan_approval import PlanApprovalWorker
from .workers.planner import PlanningWorker
from .workers.reviewer import ReviewerWorker
from watchfiles import awatch


logger = logging.getLogger(__name__)


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
        self.backend_availability = await asyncio.to_thread(self.model_registry.warm_availability)
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
        tasks = list(self._background_tasks)
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
        try:
            if action_id == "start_verification":
                await asyncio.to_thread(self.verification_service.start, task_id, by=by)
            elif action_id == "approve_verification":
                await asyncio.to_thread(self.verification_service.approve, task_id, by=by, completion_mode="new-branch")
            else:
                await asyncio.to_thread(
                    self.verification_service.reject,
                    task_id,
                    by=by,
                    note=f"requested via Slack by {by}",
                )
        except (TransitionError, TaskNotFoundError, CommitError, IntegrationError) as exc:
            return {"status": "error", "message": str(exc)}
        await self.rescan_and_publish()
        return {"status": "success", "clear_buttons": True}

    async def _handle_slack_view_submission(self, payload: dict[str, Any]) -> dict[str, object]:
        view = payload.get("view")
        if not isinstance(view, dict):
            return {"status": "error", "message": "Slack modal payload is invalid."}
        callback_id = view.get("callback_id")
        if callback_id != "resume_review_loop_modal":
            return {"status": "noop"}
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

    def _open_slack_resume_review_loop_modal(self, payload: dict[str, Any], task_id: str, by: str) -> dict[str, object]:
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
                    "callback_id": "resume_review_loop_modal",
                    "private_metadata": json.dumps(
                        {
                            "task_id": task_id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "message_ts": message_ts,
                            "message_text": message_text,
                        }
                    ),
                    "title": {"type": "plain_text", "text": "Resume review"},
                    "submit": {"type": "plain_text", "text": "Resume"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "resume_review_loop_input",
                            "label": {"type": "plain_text", "text": "Message"},
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "message_input",
                                "multiline": True,
                                "initial_value": f"requested via Slack by {by}",
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
    runtime.slack_runtime = SlackRuntime(config, events, action_handler=runtime.handle_slack_interactive_action)
    return runtime
