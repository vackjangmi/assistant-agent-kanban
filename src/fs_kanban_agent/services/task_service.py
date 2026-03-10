from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..exceptions import TaskNotFoundError, TransitionError
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
                content = self._render_opencode_log(path)
                if not content.strip():
                    content = path.read_text()
                entries.append(
                    TaskLogEntry(
                        name=path.name,
                        path=str(path),
                        content=content,
                        updated_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                    )
                )
        return TaskLogs(task_id=task.metadata.task_id, entries=entries)

    def _render_opencode_log(self, path: Path) -> str:
        rendered: list[str] = []
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                rendered.append(line)
                continue
            event_type = payload.get("type")
            if event_type == "text":
                part = payload.get("part") or {}
                text = part.get("text") if isinstance(part, dict) else None
                if isinstance(text, str):
                    rendered.append(text)
            elif event_type == "final":
                content = payload.get("content")
                if isinstance(content, str):
                    rendered.append(content)
            elif event_type == "message" and payload.get("role") == "assistant":
                content = payload.get("content")
                if isinstance(content, str):
                    rendered.append(content)
            elif event_type == "error":
                message = payload.get("message")
                if isinstance(message, str):
                    rendered.append(f"ERROR: {message}")
        return "\n\n".join(part.strip() for part in rendered if part.strip())

    def get_markdown_artifact(self, task_id: str, filename: str) -> str:
        task = self._find_task(task_id)
        path = self._validate_markdown_artifact(task.task_dir, filename)
        return path.read_text()

    def update_markdown_artifact(self, task_id: str, filename: str, content: str) -> None:
        task = self._find_task(task_id)
        if task.state != "waiting-check-plans":
            raise TransitionError("markdown editing is only allowed in waiting-check-plans")
        path = self._validate_markdown_artifact(task.task_dir, filename)
        path.write_text(content.rstrip() + "\n")

    def _find_task(self, task_id: str):
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _validate_markdown_artifact(self, task_dir: Path, filename: str) -> Path:
        if filename != "PLAN.md":
            raise TransitionError("only PLAN.md is editable")
        path = (task_dir / filename).resolve()
        if path.parent != task_dir.resolve() or not path.exists():
            raise TaskNotFoundError(filename)
        return path
