from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from pathlib import Path
from typing import Any, Protocol, cast

from ..config import AppConfig, AssistantBackend, SUPPORTED_RUNTIME_ASSISTANTS
from ..events import EventBus
from ..exceptions import NoSupportedAssistantError
from ..locks import TaskLockManager
from ..metadata_store import MetadataStore
from ..models import TaskContext
from ..recovery import RecoveryService
from ..scanner import KanbanScanner
from ..services.board_service import BoardService
from ..services.human_verification_service import HumanVerificationService
from ..services.retrospective_service import RetrospectiveService
from ..services.task_cancellation_service import TaskCancellationService
from ..services.task_deletion_service import TaskDeletionService
from ..services.task_inspection_service import TaskInspectionService
from ..services.task_rerequest_service import TaskRerequestService
from ..services.task_service import TaskService
from ..transitions import TransitionManager
from ..assistant_adapter import AssistantAdapter, build_backend_manager
from ..slack_notifications import SlackMilestoneNotifier
from ..slack_runtime import SlackRuntime
from ..workers.committer import CommitWorker
from ..workers.implementer import ImplementerWorker
from ..workers.plan_approval import PlanApprovalWorker
from ..workers.planner import PlanningWorker
from ..workers.reviewer import ReviewerWorker
from watchfiles import awatch

from ._slack import _SlackHandlersMixin


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
    def peek(self, backend: AssistantBackend) -> Any: ...
    def peek_availability(self, backend: AssistantBackend) -> Any: ...
    def all_availability(self, *, refresh: bool = False) -> dict[AssistantBackend, Any]: ...


class RuntimeSupervisor(_SlackHandlersMixin):
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
        inspection_service: Any,
        retrospective_service: Any,
        recovery: RecoveryProvider,
        events: EventBus,
        model_registry: ModelRegistryProvider,
        rerequest_service: Any | None = None,
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
        self.cancellation_service: Any = None
        self.deletion_service = deletion_service
        self.task_service = task_service
        self.inspection_service = inspection_service
        self.retrospective_service = retrospective_service
        self.rerequest_service: Any = rerequest_service
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
        self._raise_if_no_supported_assistant()
        warm_models = getattr(self.model_registry, "warm", None)
        if callable(warm_models):
            await asyncio.to_thread(warm_models)
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


    async def force_delete(self, task_id: str, *, by: str) -> None:
        await self.cancel_task(task_id)
        await asyncio.to_thread(self.deletion_service.delete, task_id, by=by)

    async def cancel_workflow(self, task_id: str, *, by: str, note: str | None = None) -> TaskContext:
        await self.cancel_task(task_id)
        return await asyncio.to_thread(self.cancellation_service.cancel, task_id, by=by, note=note)

    async def rerequest_task(self, task_id: str, *, by: str) -> TaskContext:
        return await asyncio.to_thread(self.rerequest_service.rerequest, task_id, by=by)

    def _raise_if_no_supported_assistant(self) -> None:
        if not self.backend_availability:
            return
        if any(getattr(status, "available", False) for status in self.backend_availability.values()):
            return
        details = "; ".join(
            f"{backend}: {getattr(status, 'error', None) or 'unavailable'}"
            for backend in SUPPORTED_RUNTIME_ASSISTANTS
            if (status := self.backend_availability.get(cast(AssistantBackend, backend))) is not None
        )
        raise NoSupportedAssistantError(
            "No supported assistant CLI is available. Install and authenticate at least one of: "
            "claude, codex, agy, gemini, opencode."
            f" Availability errors: {details}"
        )

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
    from ..models import WorkerEvent

    return WorkerEvent(event="board_snapshot", payload=board.model_dump(mode="json"))


def build_runtime(
    config: AppConfig,
    planner_adapter: AssistantAdapter,
    implementer_adapter: AssistantAdapter,
    reviewer_adapter: AssistantAdapter,
    commit_adapter: AssistantAdapter | None = None,
    branch_summary_adapter: AssistantAdapter | None = None,
    adapter_registry: dict[AssistantBackend, AssistantAdapter] | None = None,
    user_settings_store: Any | None = None,
):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    slack_notifier = SlackMilestoneNotifier(config, metadata_store, settings_store=user_settings_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks, slack_notifier=slack_notifier)
    events = EventBus()
    from ..workspace_manager import WorkspaceManager
    from ..commit_manager import CommitManager
    from ..integration_manager import IntegrationManager

    workspace_manager = WorkspaceManager(config)
    integration_manager = IntegrationManager(config)
    commit_manager = CommitManager()
    from ..assistant_factory import build_adapter_registry

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
    cancellation_service = TaskCancellationService(config, scanner, locks, transitions, integration_manager)
    deletion_service = TaskDeletionService(config, scanner, locks, integration_manager)
    rerequest_service = TaskRerequestService(config, scanner, metadata_store, locks, transitions)
    task_service = TaskService(
        scanner,
        config.runs_dir,
        config.kanban_root,
        config.archive_runs_dir,
        metadata_store=metadata_store,
        transitions=transitions,
        locks=locks,
    )
    inspection_service = TaskInspectionService(config=config, scanner=scanner, adapter_registry=registry)
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
        inspection_service,
        retrospective_service,
        recovery,
        events,
        model_registry,
        rerequest_service=rerequest_service,
    )
    runtime.adapter_registry = registry
    runtime.cancellation_service = cancellation_service
    runtime.slack_runtime = SlackRuntime(
        config,
        events,
        action_handler=runtime.handle_slack_interactive_action,
        mention_handler=runtime.handle_slack_app_mention,
    )
    return runtime
