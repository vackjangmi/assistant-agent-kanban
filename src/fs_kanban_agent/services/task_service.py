from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..exceptions import TaskNotFoundError, TransitionError
from ..log_parser import render_opencode_log
from ..models import TaskDetail, TaskLogEntry, TaskLogs
from ..scanner import KanbanScanner


class TaskService:
    def __init__(self, scanner: KanbanScanner, runs_root: Path) -> None:
        self.scanner = scanner
        self.runs_root = runs_root

    def get_task(self, task_id: str) -> TaskDetail:
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc
        markdown_files = sorted(path.name for path in task.task_dir.glob("*.md"))
        json_files = sorted(path.name for path in task.task_dir.glob("*.json") if path.name != "metadata.json")
        log_dir = self.runs_root / task.metadata.task_id
        log_files = sorted(path.name for path in log_dir.glob("*")) if log_dir.exists() else []
        return TaskDetail(metadata=task.metadata, task_path=str(task.task_dir), markdown_files=markdown_files, json_files=json_files, log_files=log_files)

    def get_logs(self, task_id: str) -> TaskLogs:
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc
        log_dir = self.runs_root / task.metadata.task_id
        entries: list[TaskLogEntry] = []
        if log_dir.exists():
            paths = sorted(
                [path for path in log_dir.glob("*.jsonl") if path.is_file()],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for path in paths:
                raw_content = path.read_text()
                rendered_content = render_opencode_log(raw_content)
                entries.append(
                    TaskLogEntry(
                        name=path.name,
                        path=str(path),
                        content=raw_content,
                        rendered_content=rendered_content or None,
                        updated_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                    )
                )
        return TaskLogs(task_id=task.metadata.task_id, entries=entries)

    def get_markdown_artifact(self, task_id: str, filename: str) -> str:
        task = self._find_task(task_id)
        path = self._validate_readable_markdown_artifact(task.task_dir, filename)
        return path.read_text()

    def update_markdown_artifact(self, task_id: str, filename: str, content: str) -> None:
        task = self._find_task(task_id)
        if task.state != "waiting-check-plans":
            raise TransitionError("markdown editing is only allowed in waiting-check-plans")
        path = self._validate_writable_markdown_artifact(task.task_dir, filename)
        path.write_text(content.rstrip() + "\n")

    def _find_task(self, task_id: str):
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _validate_readable_markdown_artifact(self, task_dir: Path, filename: str) -> Path:
        if not filename.endswith(".md"):
            raise TransitionError("only markdown artifacts can be viewed")
        path = (task_dir / filename).resolve()
        if path.parent != task_dir.resolve() or not path.exists():
            raise TaskNotFoundError(filename)
        return path

    def _validate_writable_markdown_artifact(self, task_dir: Path, filename: str) -> Path:
        if filename != "PLAN.md":
            raise TransitionError("only PLAN.md is editable")
        return self._validate_readable_markdown_artifact(task_dir, filename)
