from __future__ import annotations

import shutil
from pathlib import Path

from ..config import AppConfig
from ..exceptions import IntegrationError, TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..locks import TaskLockManager
from ..models import TaskContext, TaskMetadata
from ..scanner import KanbanScanner
from ..target_repo_guard import resolve_safe_target_repo_root


class TaskDeletionService:
    def __init__(self, config: AppConfig, scanner: KanbanScanner, locks: TaskLockManager, integration_manager: IntegrationManager) -> None:
        self.config = config
        self.scanner = scanner
        self.locks = locks
        self.integration_manager = integration_manager

    def delete(self, task_id: str, *, by: str) -> None:
        lock_path = self.locks.path_for(task_id)
        with self.locks.acquire_by_task_id(task_id, owner=by, run_id="manual-delete"):
            context = self._find_task(task_id)
            self._prepare_for_deletion(context)
            self._delete_target_repo_docs(context.metadata)
            self._delete_tree(self.config.runs_dir / task_id)
            self._delete_tree(self.config.archive_runs_dir / task_id)
            self._delete_tree(self._workspace_root(task_id, context.metadata))
            self._delete_tree(context.task_dir)
        if lock_path.exists():
            lock_path.unlink()

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _prepare_for_deletion(self, context: TaskContext) -> None:
        self._validate_managed_workspace(context.metadata)
        self._validate_managed_patch(context.metadata)
        if context.metadata.integration.applied:
            self.integration_manager.rollback_workspace(context.metadata)

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
        managed_roots = [
            (self.config.runs_dir / metadata.task_id).resolve(),
            (self.config.archive_runs_dir / metadata.task_id).resolve(),
        ]
        for managed_root in managed_roots:
            try:
                patch_path.relative_to(managed_root)
                return
            except ValueError:
                continue
        raise TransitionError("task deletion is blocked because patch path is outside the managed runs roots")

    def _delete_tree(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def _delete_target_repo_docs(self, metadata: TaskMetadata) -> None:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        try:
            docs_root = self.config.resolve_target_repo_docs_root(target_repo_root)
        except ValueError as exc:
            raise TransitionError(str(exc)) from exc
        if not docs_root.exists():
            return
        for candidate in docs_root.glob(f"*/*/*/{metadata.task_id}"):
            resolved = candidate.resolve()
            try:
                resolved.relative_to(docs_root.resolve())
            except ValueError as exc:
                raise TransitionError("task deletion is blocked because task docs path is outside the managed docs root") from exc
            self._delete_tree(resolved)
