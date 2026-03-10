from __future__ import annotations

from ..exceptions import TaskNotFoundError
from ..models import TaskDetail
from ..scanner import KanbanScanner


class TaskService:
    def __init__(self, scanner: KanbanScanner) -> None:
        self.scanner = scanner

    def get_task(self, task_id: str) -> TaskDetail:
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc
        markdown_files = sorted(path.name for path in task.task_dir.glob("*.md"))
        log_dir = task.task_dir / "logs"
        log_files = sorted(path.name for path in log_dir.glob("*")) if log_dir.exists() else []
        return TaskDetail(metadata=task.metadata, task_path=str(task.task_dir), markdown_files=markdown_files, log_files=log_files)
