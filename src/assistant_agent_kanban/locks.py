from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from .config import AppConfig
from .enums import STATE_ORDER, TaskState
from .exceptions import LockError
from .metadata_store import MetadataStore
from .models import TaskMetadata, utc_now


class TaskLockManager:
    def __init__(self, config: AppConfig, metadata_store: MetadataStore | None = None) -> None:
        self.config = config
        self.metadata_store = metadata_store or MetadataStore()

    def path_for(self, task_id: str) -> Path:
        return self.config.locks_dir / f"{task_id}.lock"

    @contextmanager
    def acquire(self, task_dir: Path, metadata: TaskMetadata, owner: str, run_id: str):
        lock = self._acquire_file_lock(metadata.task_id)
        try:
            metadata.lease.owner = owner
            metadata.lease.run_id = run_id
            metadata.lease.heartbeat_at = utc_now()
            self.metadata_store.save(task_dir, metadata)
            yield
        finally:
            try:
                metadata.lease.owner = None
                metadata.lease.run_id = None
                metadata.lease.heartbeat_at = None
                self.metadata_store.save(self._resolve_task_dir(task_dir, metadata.task_id), metadata)
            finally:
                lock.release()

    @contextmanager
    def acquire_by_task_id(self, task_id: str, owner: str, run_id: str):
        lock = self._acquire_file_lock(task_id)
        try:
            yield
        finally:
            lock.release()

    def heartbeat(self, task_dir: Path, metadata: TaskMetadata, owner: str, run_id: str) -> None:
        metadata.lease.owner = owner
        metadata.lease.run_id = run_id
        metadata.lease.heartbeat_at = utc_now()
        self.metadata_store.save(self._resolve_task_dir(task_dir, metadata.task_id), metadata)

    def _resolve_task_dir(self, original_task_dir: Path, task_id: str) -> Path:
        if original_task_dir.exists():
            return original_task_dir
        for metadata_path in self._metadata_paths_for_states():
            if metadata_path.parent.name == task_id:
                return metadata_path.parent
        return original_task_dir

    def _metadata_paths_for_states(self) -> list[Path]:
        metadata_paths: list[Path] = []
        for state in STATE_ORDER:
            state_dir = self.config.state_dir(state)
            if state is TaskState.DONE:
                metadata_paths.extend(sorted(state_dir.glob("**/metadata.json")))
                continue
            metadata_paths.extend(
                sorted(
                    task_dir / "metadata.json"
                    for task_dir in state_dir.iterdir()
                    if task_dir.is_dir() and (task_dir / "metadata.json").exists()
                )
            )
        return metadata_paths

    def _acquire_file_lock(self, task_id: str):
        lock = FileLock(str(self.path_for(task_id)))
        try:
            lock.acquire(timeout=self.config.locks.timeout_seconds)
        except Timeout as exc:
            raise LockError(f"could not acquire lock for {task_id}") from exc
        return lock
