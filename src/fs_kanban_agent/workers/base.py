from __future__ import annotations

import uuid
from pathlib import Path

from ..config import AppConfig
from ..events import EventBus
from ..locks import TaskLockManager
from ..metadata_store import MetadataStore
from ..models import WorkerEvent
from ..scanner import KanbanScanner
from ..transitions import TransitionManager


class WorkerBase:
    worker_name = "worker"

    def __init__(
        self,
        config: AppConfig,
        scanner: KanbanScanner,
        metadata_store: MetadataStore,
        locks: TaskLockManager,
        transitions: TransitionManager,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.scanner = scanner
        self.metadata_store = metadata_store
        self.locks = locks
        self.transitions = transitions
        self.event_bus = event_bus

    def make_run_id(self) -> str:
        return f"{self.worker_name}-{uuid.uuid4()}"

    async def emit(self, event: str, task_id: str, **payload: object) -> None:
        await self.event_bus.publish(WorkerEvent(event=event, task_id=task_id, payload=dict(payload)))

    def task_log_dir(self, task_id: str) -> Path:
        path = self.config.runs_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path
