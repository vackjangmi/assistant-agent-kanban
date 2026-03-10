from __future__ import annotations

from pathlib import Path

from .config import AppConfig
from .enums import STATE_ORDER, TaskState
from .metadata_store import MetadataStore, slugify
from .models import BoardColumn, BoardSnapshot, TaskContext, TaskMetadata, TaskSnapshot


class TaskIdSequence:
    def next_id(self, existing_ids: set[str]) -> str:
        counter = 1
        while True:
            candidate = f"TASK-{counter:04d}"
            if candidate not in existing_ids:
                return candidate
            counter += 1


class KanbanScanner:
    def __init__(self, config: AppConfig, metadata_store: MetadataStore | None = None) -> None:
        self.config = config
        self.metadata_store = metadata_store or MetadataStore()
        self.sequence = TaskIdSequence()

    def scan(self) -> list[TaskContext]:
        tasks: list[TaskContext] = []
        existing_ids = self._existing_task_ids()
        for state in STATE_ORDER:
            state_dir = self.config.state_dir(state)
            for task_dir in sorted([path for path in state_dir.iterdir() if path.is_dir()]):
                metadata = self._ensure_metadata(task_dir, state, existing_ids)
                existing_ids.add(metadata.task_id)
                if metadata.state != state:
                    metadata.state = state
                    self.metadata_store.save(task_dir, metadata)
                tasks.append(TaskContext(metadata=metadata, task_dir=task_dir, state=state))
        return tasks

    def board_snapshot(self) -> BoardSnapshot:
        tasks = self.scan()
        columns: list[BoardColumn] = []
        for state in STATE_ORDER:
            items = [
                TaskSnapshot(
                    task_id=item.metadata.task_id,
                    title=item.metadata.title,
                    state=state,
                    path=str(item.task_dir),
                    updated_at=item.metadata.updated_at,
                    iteration=max(item.metadata.implementation.iteration, item.metadata.review.iteration),
                    has_error=bool(item.metadata.errors),
                )
                for item in tasks
                if item.state == state
            ]
            columns.append(BoardColumn(state=state, items=items))
        return BoardSnapshot(columns=columns)

    def find_task(self, task_id: str) -> TaskContext:
        for task in self.scan():
            if task.metadata.task_id == task_id:
                return task
        raise FileNotFoundError(task_id)

    def _existing_task_ids(self) -> set[str]:
        existing: set[str] = set()
        for metadata_path in self.config.kanban_root.glob("*/**/metadata.json"):
            if metadata_path.parent.parent.name == "_runtime":
                continue
            existing.add(TaskMetadata.model_validate_json(metadata_path.read_text()).task_id)
        return existing

    def _ensure_metadata(self, task_dir: Path, state: TaskState, existing_ids: set[str]) -> TaskMetadata:
        path = self.metadata_store.metadata_path(task_dir)
        if path.exists():
            return self.metadata_store.load(task_dir)
        title = task_dir.name.split("__", 1)[-1].replace("-", " ").strip() or task_dir.name
        request_path = task_dir / "REQUEST.md"
        if request_path.exists():
            first_line = request_path.read_text().splitlines()
            if first_line:
                title = first_line[0].lstrip("# ").strip() or title
        task_id = self.sequence.next_id(existing_ids)
        slug = slugify(title)
        return self.metadata_store.bootstrap(task_dir, state, task_id, title, slug)
