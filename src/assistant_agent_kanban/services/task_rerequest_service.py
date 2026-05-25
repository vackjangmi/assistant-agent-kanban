from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path

from ..config import AppConfig
from ..enums import TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..locks import TaskLockManager
from ..metadata_store import MetadataStore, slugify
from ..models import TaskContext
from ..request_parser import parse_request_markdown, resolve_repo_root
from ..scanner import KanbanScanner
from ..transitions import TransitionManager


class TaskRerequestService:
    def __init__(
        self,
        config: AppConfig,
        scanner: KanbanScanner,
        metadata_store: MetadataStore,
        locks: TaskLockManager,
        transitions: TransitionManager,
    ) -> None:
        self.config = config
        self.scanner = scanner
        self.metadata_store = metadata_store
        self.locks = locks
        self.transitions = transitions

    def rerequest(self, task_id: str, *, by: str) -> TaskContext:
        source = self._find_task(task_id)
        if source.state != TaskState.CLOSED:
            raise TransitionError(f"task re-request is only allowed from closed, not {source.state.value}")
        if source.metadata.closure.reason != "cancelled_by_human":
            raise TransitionError("task re-request is only allowed for human-cancelled tasks")

        source_request_path = source.task_dir / source.metadata.request.path
        if not source_request_path.exists():
            raise TransitionError("task re-request requires REQUEST.md")

        new_task_dir = self._new_request_dir()
        new_task_dir.mkdir(parents=True, exist_ok=False)
        try:
            request_markdown = self._copied_request_markdown(source_request_path.read_text(), source.metadata.task_id)
            (new_task_dir / "REQUEST.md").write_text(request_markdown)
            self._copy_request_attachments(source.task_dir, new_task_dir)
            parsed = parse_request_markdown(request_markdown)
            title = parsed.title or source.metadata.title
            metadata = self.metadata_store.bootstrap(
                new_task_dir,
                TaskState.REQUESTS,
                new_task_dir.name,
                title,
                slugify(title),
                target_repo_root=str(resolve_repo_root(parsed.target_repo_root, Path(source.metadata.target.repo_root))),
                base_branch=parsed.base_branch or source.metadata.target.base_branch or self.config.base_branch,
                request_language=parsed.language or source.metadata.request.language,
                request_plan_auto_approve=parsed.plan_auto_approve,
            )
            with self.locks.acquire(new_task_dir, metadata, owner=by, run_id="manual-rerequest"):
                return self.transitions.move(
                    TaskContext(metadata=metadata, task_dir=new_task_dir, state=TaskState.REQUESTS),
                    target=TaskState.PLANNING,
                    by=by,
                    note=f"re-requested from {source.metadata.task_id}",
                )
        except Exception:
            for state in (TaskState.REQUESTS, TaskState.PLANNING):
                shutil.rmtree(self.config.state_dir(state) / new_task_dir.name, ignore_errors=True)
            raise

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _new_request_dir(self) -> Path:
        requests_dir = self.config.state_dir(TaskState.REQUESTS)
        while True:
            candidate = secrets.token_hex(4)[:7]
            task_dir = requests_dir / candidate
            if not any(path.name == candidate for path in self.config.kanban_root.glob("*/**")):
                return task_dir

    def _copied_request_markdown(self, content: str, source_task_id: str) -> str:
        pattern = re.compile(rf"(/api/tasks/{re.escape(source_task_id)}/attachments/)(?:_attachments/)?")
        return pattern.sub("_attachments/", content)

    def _copy_request_attachments(self, source_task_dir: Path, target_task_dir: Path) -> None:
        source_attachments = source_task_dir / "_attachments"
        if not source_attachments.exists():
            return
        shutil.copytree(source_attachments, target_task_dir / "_attachments")
