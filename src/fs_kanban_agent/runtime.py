from __future__ import annotations

import asyncio

from .config import AppConfig
from .events import EventBus
from .locks import TaskLockManager
from .metadata_store import MetadataStore
from .recovery import RecoveryService
from .scanner import KanbanScanner
from .services.board_service import BoardService
from .services.human_verification_service import HumanVerificationService
from .services.task_deletion_service import TaskDeletionService
from .services.task_service import TaskService
from .transitions import TransitionManager
from .opencode_adapter import OpenCodeModelRegistry, SubprocessOpenCodeAdapter
from .workers.committer import CommitWorker
from .workers.implementer import ImplementerWorker
from .workers.planner import PlanningWorker
from .workers.reviewer import ReviewerWorker
from watchfiles import awatch


class RuntimeSupervisor:
    def __init__(
        self,
        config: AppConfig,
        planner: PlanningWorker,
        implementer: ImplementerWorker,
        reviewer: ReviewerWorker,
        committer: CommitWorker,
        scanner: KanbanScanner,
        board_service: BoardService,
        verification_service: HumanVerificationService,
        deletion_service: TaskDeletionService,
        task_service: TaskService,
        recovery: RecoveryService,
        events: EventBus,
        model_registry: OpenCodeModelRegistry,
    ) -> None:
        self.config = config
        self.planner = planner
        self.implementer = implementer
        self.reviewer = reviewer
        self.committer = committer
        self.scanner = scanner
        self.board_service = board_service
        self.verification_service = verification_service
        self.deletion_service = deletion_service
        self.task_service = task_service
        self.recovery = recovery
        self.events = events
        self.model_registry = model_registry
        self._stop_event = asyncio.Event()
        self._background_tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self._stop_event.clear()
        await self.startup_recovery()
        if isinstance(self.model_registry.adapter, SubprocessOpenCodeAdapter):
            self._background_tasks.append(
                asyncio.create_task(self.warm_model_registry(), name="fs-kanban-model-discovery")
            )
        if self.config.runtime.auto_dispatch:
            self._background_tasks = [
                *self._background_tasks,
                asyncio.create_task(self.dispatch_forever(), name="fs-kanban-dispatch"),
                asyncio.create_task(self.watch_forever(), name="fs-kanban-watch"),
            ]

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = list(self._background_tasks)
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def rescan_and_publish(self) -> None:
        board = self.board_service.get_board()
        await self.events.publish(board_to_event(board))

    async def dispatch_once(self) -> bool:
        for worker in [self.planner, self.implementer, self.reviewer]:
            if await worker.run_once():
                await self.rescan_and_publish()
                return True
        return False

    async def startup_recovery(self) -> None:
        for event in self.recovery.recover():
            await self.events.publish(event)
        await self.rescan_and_publish()

    async def warm_model_registry(self) -> None:
        await asyncio.to_thread(self.model_registry.get)

    async def dispatch_forever(self) -> None:
        while not self._stop_event.is_set():
            changed = await self.dispatch_once()
            if not changed:
                await asyncio.sleep(self.config.runtime.poll_interval_seconds)

    async def watch_forever(self) -> None:
        async for _changes in awatch(self.config.kanban_root, stop_event=self._stop_event):
            await asyncio.sleep(self.config.runtime.poll_interval_seconds)
            await self.rescan_and_publish()


def board_to_event(board):
    from .models import WorkerEvent

    return WorkerEvent(event="board_snapshot", payload=board.model_dump(mode="json"))


def build_runtime(config: AppConfig, planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter=None):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    events = EventBus()
    from .workspace_manager import WorkspaceManager
    from .commit_manager import CommitManager
    from .integration_manager import IntegrationManager

    workspace_manager = WorkspaceManager(config)
    integration_manager = IntegrationManager(config)
    commit_manager = CommitManager()
    planner = PlanningWorker(config, scanner, metadata_store, locks, transitions, events, adapter=planner_adapter)
    implementer = ImplementerWorker(config, scanner, metadata_store, locks, transitions, events, adapter=implementer_adapter, workspace_manager=workspace_manager)
    reviewer = ReviewerWorker(config, scanner, metadata_store, locks, transitions, events, adapter=reviewer_adapter, integration_manager=integration_manager)
    committer = CommitWorker(config, scanner, metadata_store, locks, transitions, events, adapter=commit_adapter)
    board_service = BoardService(scanner)
    verification_service = HumanVerificationService(scanner, metadata_store, locks, transitions, integration_manager, commit_manager)
    deletion_service = TaskDeletionService(config, scanner, locks)
    task_service = TaskService(scanner, config.runs_dir)
    recovery = RecoveryService(config, scanner, transitions, locks)
    model_registry = OpenCodeModelRegistry(adapter=planner_adapter, config=config)
    return RuntimeSupervisor(
        config,
        planner,
        implementer,
        reviewer,
        committer,
        scanner,
        board_service,
        verification_service,
        deletion_service,
        task_service,
        recovery,
        events,
        model_registry,
    )
