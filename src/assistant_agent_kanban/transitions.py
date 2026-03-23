from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from .config import AppConfig
from .enums import ALLOWED_TRANSITIONS, MANUAL_TRANSITIONS, TaskState
from .exceptions import TransitionError
from .locks import TaskLockManager
from .metadata_store import MetadataStore
from .models import HistoryEntry, TaskContext, utc_now
from .retry_policy import clear_retry_gate
from .scanner import KanbanScanner


class TransitionManager:
    def __init__(self, config: AppConfig, metadata_store: MetadataStore | None = None, scanner: KanbanScanner | None = None, locks: TaskLockManager | None = None) -> None:
        self.config = config
        self.metadata_store = metadata_store or MetadataStore()
        self.scanner = scanner or KanbanScanner(config, self.metadata_store)
        self.locks = locks

    def move(self, context: TaskContext, target: TaskState, by: str, note: str | None = None) -> TaskContext:
        if target not in ALLOWED_TRANSITIONS[context.state]:
            raise TransitionError(f"invalid transition {context.state.value} -> {target.value}")
        return self._move(context, target=target, by=by, note=note)

    def recover_move(self, context: TaskContext, target: TaskState, by: str, note: str | None = None) -> TaskContext:
        return self._move(context, target=target, by=by, note=note)

    def _move(self, context: TaskContext, target: TaskState, by: str, note: str | None = None) -> TaskContext:
        source_dir = context.task_dir
        entered_at = utc_now()
        target_dir = self._target_dir_for_state(source_dir.name, target, entered_at)
        metadata = context.metadata
        previous_state = metadata.state
        previous_history_len = len(metadata.history)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target_dir))
        metadata.state = target
        metadata.history.append(HistoryEntry(state=target, entered_at=entered_at, by=by, note=note))
        try:
            self.metadata_store.save(target_dir, metadata)
        except Exception:
            metadata.state = previous_state
            del metadata.history[previous_history_len:]
            shutil.move(str(target_dir), str(source_dir))
            raise
        return TaskContext(metadata=metadata, task_dir=target_dir, state=target)

    def _target_dir_for_state(self, task_dir_name: str, target: TaskState, entered_at: datetime) -> Path:
        state_root = self.config.state_dir(target)
        if target is not TaskState.DONE:
            return state_root / task_dir_name
        local_stamp = entered_at.astimezone()
        return state_root / local_stamp.strftime("%Y") / local_stamp.strftime("%m") / local_stamp.strftime("%d") / task_dir_name

    def manual_move(self, task_id: str, target: TaskState, by: str) -> TaskContext:
        context = self.scanner.find_task(task_id)
        if (context.state, target) not in MANUAL_TRANSITIONS:
            raise TransitionError(f"manual transition not allowed: {context.state.value} -> {target.value}")
        if self.locks is None:
            raise TransitionError("manual transition requires lock manager")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id=f"manual-{target.value}"):
            if target == TaskState.TODOS:
                context.metadata.plan.approved = True
                context.metadata.plan_approval.auto_progress_at = None
                context.metadata.plan_approval.resolved_by = by
                context.metadata.plan_approval.resolved_at = utc_now()
            clear_retry_gate(context.metadata)
            return self.move(context, target=target, by=by, note="manual approval")
