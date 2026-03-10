from __future__ import annotations

from datetime import timedelta

from .config import AppConfig
from .enums import TaskState
from .locks import TaskLockManager
from .models import WorkerEvent, utc_now
from .scanner import KanbanScanner
from .transitions import TransitionManager


RECOVERY_TARGETS = {
    TaskState.PLANNING: TaskState.REQUESTS,
    TaskState.IMPLEMENTING: TaskState.TODOS,
    TaskState.REVIEWING: TaskState.WAITING_REVIEWS,
}


class RecoveryService:
    def __init__(self, config: AppConfig, scanner: KanbanScanner, transitions: TransitionManager, locks: TaskLockManager) -> None:
        self.config = config
        self.scanner = scanner
        self.transitions = transitions
        self.locks = locks

    def recover(self) -> list[WorkerEvent]:
        events: list[WorkerEvent] = []
        cutoff = utc_now() - timedelta(seconds=self.config.locks.stale_after_seconds)
        for task in self.scanner.scan():
            if task.state not in RECOVERY_TARGETS:
                continue
            if task.metadata.lease.heartbeat_at and task.metadata.lease.heartbeat_at > cutoff:
                continue
            with self.locks.acquire(task.task_dir, task.metadata, owner="recovery", run_id="recovery"):
                moved = self.transitions.recover_move(task, RECOVERY_TARGETS[task.state], by="recovery", note="stale lease recovery")
            events.append(WorkerEvent(event="recovery_event", task_id=moved.metadata.task_id, payload={"state": moved.state.value}))
        return events
