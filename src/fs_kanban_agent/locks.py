from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from .config import AppConfig
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
        lock = FileLock(str(self.path_for(metadata.task_id)))
        try:
            lock.acquire(timeout=self.config.locks.timeout_seconds)
        except Timeout as exc:
            raise LockError(f"could not acquire lock for {metadata.task_id}") from exc
        try:
            metadata.lease.owner = owner
            metadata.lease.run_id = run_id
            metadata.lease.heartbeat_at = utc_now()
            self.metadata_store.save(task_dir, metadata)
            yield
        finally:
            metadata.lease.owner = None
            metadata.lease.run_id = None
            metadata.lease.heartbeat_at = None
            self.metadata_store.save(self._resolve_task_dir(task_dir, metadata.task_id), metadata)
            lock.release()

    def heartbeat(self, task_dir: Path, metadata: TaskMetadata, owner: str, run_id: str) -> None:
        metadata.lease.owner = owner
        metadata.lease.run_id = run_id
        metadata.lease.heartbeat_at = utc_now()
        self.metadata_store.save(self._resolve_task_dir(task_dir, metadata.task_id), metadata)

    def _resolve_task_dir(self, original_task_dir: Path, task_id: str) -> Path:
        if original_task_dir.exists():
            return original_task_dir
        for state_dir in self.config.kanban_root.iterdir():
            if not state_dir.is_dir() or state_dir.name == "_runtime":
                continue
            candidate = state_dir / original_task_dir.name
            metadata_path = candidate / "metadata.json"
            if metadata_path.exists() and task_id in metadata_path.read_text():
                return candidate
        return original_task_dir
