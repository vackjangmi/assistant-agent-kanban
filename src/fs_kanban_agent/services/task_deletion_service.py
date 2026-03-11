from __future__ import annotations

import shutil
from pathlib import Path

from ..config import AppConfig
from ..enums import ACTIVE_STATES
from ..exceptions import TaskNotFoundError, TransitionError
from ..locks import TaskLockManager
from ..models import TaskContext, TaskMetadata
from ..scanner import KanbanScanner


class TaskDeletionService:
    def __init__(self, config: AppConfig, scanner: KanbanScanner, locks: TaskLockManager) -> None:
        self.config = config
        self.scanner = scanner
        self.locks = locks

    def delete(self, task_id: str, *, by: str) -> None:
        lock_path = self.locks.path_for(task_id)
        with self.locks.acquire_by_task_id(task_id, owner=by, run_id="manual-delete"):
            context = self._find_task(task_id)
            self._ensure_deletable(context)
            self._delete_tree(self.config.runs_dir / task_id)
            self._delete_tree(self._workspace_root(task_id, context.metadata))
            self._delete_tree(context.task_dir)
        if lock_path.exists():
            lock_path.unlink()

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _ensure_deletable(self, context: TaskContext) -> None:
        if context.state in ACTIVE_STATES:
            raise TransitionError(f"task deletion is blocked while state is {context.state.value}")
        if context.metadata.integration.applied:
            raise TransitionError("task deletion is blocked while integration changes are applied")
        self._validate_managed_workspace(context.metadata)
        self._validate_managed_patch(context.metadata)

    def _workspace_root(self, task_id: str, metadata: TaskMetadata) -> Path:
        workspace_root = metadata.implementation.workspace
        expected_root = (self.config.workspace.root or (self.config.kanban_root / "_runtime/workspaces")) / task_id
        if workspace_root is None:
            return expected_root
        resolved = Path(workspace_root).expanduser().resolve()
        try:
            resolved.relative_to(expected_root.resolve())
        except ValueError as exc:
            raise TransitionError("task deletion is blocked because workspace path is outside the managed workspace root") from exc
        return expected_root

    def _validate_managed_workspace(self, metadata: TaskMetadata) -> None:
        self._workspace_root(metadata.task_id, metadata)

    def _validate_managed_patch(self, metadata: TaskMetadata) -> None:
        if not metadata.integration.patch_path:
            return
        patch_path = Path(metadata.integration.patch_path).expanduser().resolve()
        managed_root = (self.config.runs_dir / metadata.task_id).resolve()
        try:
            patch_path.relative_to(managed_root)
        except ValueError as exc:
            raise TransitionError("task deletion is blocked because patch path is outside the managed runs root") from exc

    def _delete_tree(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)
