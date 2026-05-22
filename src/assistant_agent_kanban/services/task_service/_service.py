from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ...exceptions import TaskNotFoundError
from ...locks import TaskLockManager
from ...log_parser import render_assistant_log
from ...models import (
    TaskDetail,
    TaskLogEntry,
    TaskLogs,
)
from ...scanner import KanbanScanner, derive_agent_status
from ...transitions import TransitionManager
from ..plan_approval_learning import PlanApprovalLearningService

from ._artifacts import _ArtifactsMixin
from ._changed_files import _ChangedFilesMixin
from ._data import (
    AssistantTokenUsageRow,
    TokenUsageBreakdown,
)
from ._helpers import _HelpersMixin
from ._resume import _ResumeMixin
from ._token_usage import _TokenUsageMixin


__all__ = ["TaskService", "AssistantTokenUsageRow", "TokenUsageBreakdown"]

class TaskService(_TokenUsageMixin, _ArtifactsMixin, _ChangedFilesMixin, _ResumeMixin, _HelpersMixin):
    def __init__(
        self,
        scanner: KanbanScanner,
        runs_root: Path,
        kanban_root: Path,
        archive_runs_root: Path | None = None,
        *,
        metadata_store=None,
        transitions: TransitionManager | None = None,
        locks: TaskLockManager | None = None,
    ) -> None:
        self.scanner = scanner
        self.runs_root = runs_root
        self.kanban_root = kanban_root
        self.archive_runs_root = archive_runs_root or (runs_root.parent / "archive-runs")
        self.metadata_store = metadata_store
        self.transitions = transitions
        self.locks = locks
        self.plan_approval_learning = PlanApprovalLearningService(scanner)


    def get_task(self, task_id: str, *, include_changed_files: bool = True) -> TaskDetail:
        task = self._find_task(task_id)
        request_markdown_path = str((task.task_dir / task.metadata.request.path).resolve())
        markdown_files = self._sorted_markdown_files(task.task_dir)
        json_files = sorted(path.name for path in task.task_dir.glob("*.json") if path.name != "metadata.json")
        log_dir = self._task_runs_dir(task.metadata.task_id)
        log_files = self._visible_log_files(log_dir)
        changed_files = self._load_changed_files_for_task(task, require_available=False) if include_changed_files else []
        return TaskDetail(
            metadata=task.metadata,
            task_path=str(task.task_dir),
            request_markdown_path=request_markdown_path,
            markdown_files=markdown_files,
            json_files=json_files,
            log_files=log_files,
            changed_files_available=self._changed_files_available_for_task(task),
            changed_files=[entry.summary for entry in changed_files],
            stage_timing=self._build_stage_timing(task.metadata),
            human_review=self._build_human_review_state(task),
            agent_status=derive_agent_status(task.metadata, task.state),
        )


    def get_logs(self, task_id: str) -> TaskLogs:
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc
        log_dir = self._task_runs_dir(task.metadata.task_id)
        entries: list[TaskLogEntry] = []
        if log_dir.exists():
            paths = sorted(
                [path for path in log_dir.glob("*.jsonl") if path.is_file() and self._should_show_log_file(path.name)],
                key=lambda path: path.stat().st_mtime,
                reverse=False,
            )
            for path in paths:
                raw_content = path.read_text()
                rendered_content = render_assistant_log(raw_content)
                debug_rendered_content = render_assistant_log(raw_content, debug=True)
                entries.append(
                    TaskLogEntry(
                        name=path.name,
                        path=str(path),
                        rendered_content=rendered_content or None,
                        debug_rendered_content=debug_rendered_content or None,
                        updated_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                    )
                )
        return TaskLogs(task_id=task.metadata.task_id, entries=entries)
