from __future__ import annotations

from datetime import datetime, timezone
import re
import secrets
from pathlib import Path
from typing import Literal

from .config import AppConfig
from .enums import STATE_ORDER, TaskState
from .metadata_store import MetadataStore, slugify
from .models import BoardColumn, BoardSnapshot, HistoryEntry, TaskContext, TaskMetadata, TaskSnapshot, utc_now
from .request_parser import parse_request_markdown, resolve_repo_root


AGENT_ACTIVE_STATES = {
    TaskState.PLANNING,
    TaskState.IMPLEMENTING,
    TaskState.REVIEWING,
}

TERMINAL_STATES = {
    TaskState.DONE,
}


def derive_agent_status(metadata: TaskMetadata, state: TaskState) -> Literal["active", "waiting", "idle"]:
    if state not in AGENT_ACTIVE_STATES:
        return "idle"
    if metadata.lease.owner:
        return "active"
    return "waiting"


def target_repo_label(repo_root: str) -> str:
    path = Path(repo_root)
    return path.name or path.anchor or repo_root


def total_task_duration_ms(metadata: TaskMetadata, current_state: TaskState) -> int:
    if not metadata.history:
        return 0
    history = sorted(metadata.history, key=lambda entry: entry.entered_at)
    now = datetime.now(timezone.utc)
    total_duration_ms = 0
    for index, entry in enumerate(history):
        is_current = index == len(history) - 1 and entry.state == current_state
        if entry.state in TERMINAL_STATES:
            continue
        if entry.state not in AGENT_ACTIVE_STATES and not is_current:
            continue
        next_entry = history[index + 1] if index + 1 < len(history) else None
        end_time = next_entry.entered_at if next_entry else now
        total_duration_ms += max(0, int((end_time - entry.entered_at).total_seconds() * 1000))
    return total_duration_ms


def current_state_duration_ms(metadata: TaskMetadata, state: TaskState) -> int:
    if state in TERMINAL_STATES:
        return 0
    entered_at = None
    for entry in reversed(metadata.history):
        if entry.state == state:
            entered_at = entry.entered_at
            break
    if entered_at is None:
        return 0
    return max(0, int((datetime.now(timezone.utc) - entered_at).total_seconds() * 1000))


class TaskIdSequence:
    def next_id(self, existing_ids: set[str]) -> str:
        while True:
            candidate = secrets.token_hex(4)[:7]
            if candidate not in existing_ids:
                return candidate


TASK_KEY_PATTERN = re.compile(r"^[0-9a-f]{7}$")


class KanbanScanner:
    def __init__(self, config: AppConfig, metadata_store: MetadataStore | None = None) -> None:
        self.config = config
        self.metadata_store = metadata_store or MetadataStore()
        self.sequence = TaskIdSequence()

    def scan(self) -> list[TaskContext]:
        tasks: list[TaskContext] = []
        existing_ids = self._existing_task_ids()
        for state in STATE_ORDER:
            for task_dir in self._task_dirs_for_state(state):
                metadata, task_dir = self._ensure_metadata(task_dir, state, existing_ids)
                existing_ids.add(metadata.task_id)
                should_save = False
                if metadata.state != state:
                    metadata.state = state
                    if not metadata.history or metadata.history[-1].state != state:
                        metadata.history.append(HistoryEntry(state=state, entered_at=utc_now(), by="scanner", note="state-sync"))
                    should_save = True
                request_path = task_dir / metadata.request.path
                if request_path.exists():
                    parsed = parse_request_markdown(request_path.read_text())
                    if state == TaskState.REQUESTS:
                        if parsed.title and metadata.title != parsed.title:
                            metadata.title = parsed.title
                            metadata.slug = slugify(parsed.title)
                            should_save = True
                        parsed_repo_root = str(resolve_repo_root(parsed.target_repo_root, self.config.repo_root))
                        if metadata.target.repo_root != parsed_repo_root:
                            metadata.target.repo_root = parsed_repo_root
                            should_save = True
                        parsed_base_branch = parsed.base_branch or self.config.base_branch
                        if metadata.target.base_branch != parsed_base_branch:
                            metadata.target.base_branch = parsed_base_branch
                            should_save = True
                    if metadata.request.language != parsed.language:
                        metadata.request.language = parsed.language
                        should_save = True
                normalized_repo_root = str(resolve_repo_root(metadata.target.repo_root, self.config.repo_root))
                if metadata.target.repo_root != normalized_repo_root:
                    metadata.target.repo_root = normalized_repo_root
                    should_save = True
                synced_cycle = max(metadata.cycle, metadata.implementation.iteration, metadata.review.iteration)
                if metadata.cycle != synced_cycle:
                    metadata.cycle = synced_cycle
                    should_save = True
                if not metadata.target.base_branch:
                    metadata.target.base_branch = self.config.base_branch
                    should_save = True
                if metadata.integration.base_branch != metadata.target.base_branch:
                    metadata.integration.base_branch = metadata.target.base_branch
                    should_save = True
                if should_save:
                    self.metadata_store.save(task_dir, metadata)
                tasks.append(TaskContext(metadata=metadata, task_dir=task_dir, state=state))
        return tasks

    def _task_dirs_for_state(self, state: TaskState) -> list[Path]:
        state_dir = self.config.state_dir(state)
        if state is not TaskState.DONE:
            return sorted([path for path in state_dir.iterdir() if path.is_dir()])
        task_dirs = {metadata_path.parent for metadata_path in state_dir.glob("**/metadata.json")}
        return sorted(task_dirs)

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
                    state_entered_at=self._state_entered_at(item.metadata, state),
                    iteration=item.metadata.cycle,
                    has_error=bool(item.metadata.errors),
                    active_model=self._active_model(item.metadata, state),
                    agent_status=derive_agent_status(item.metadata, state),
                    agent_owner=item.metadata.lease.owner,
                    agent_heartbeat_at=item.metadata.lease.heartbeat_at,
                    target_repo_root=item.metadata.target.repo_root,
                    target_repo_label=target_repo_label(item.metadata.target.repo_root),
                    base_branch=item.metadata.target.base_branch,
                    total_duration_ms=total_task_duration_ms(item.metadata, state),
                    current_state_duration_ms=current_state_duration_ms(item.metadata, state),
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

    def _ensure_metadata(self, task_dir: Path, state: TaskState, existing_ids: set[str]) -> tuple[TaskMetadata, Path]:
        path = self.metadata_store.metadata_path(task_dir)
        if path.exists():
            return self.metadata_store.load(task_dir), task_dir
        title = task_dir.name.split("__", 1)[-1].replace("-", " ").strip() or task_dir.name
        request_path = task_dir / "REQUEST.md"
        target_repo_root = str(self.config.repo_root.expanduser().resolve())
        base_branch = self.config.base_branch
        request_language = None
        if request_path.exists():
            parsed = parse_request_markdown(request_path.read_text())
            if parsed.title:
                title = parsed.title
            target_repo_root = str(resolve_repo_root(parsed.target_repo_root, self.config.repo_root))
            if parsed.base_branch:
                base_branch = parsed.base_branch
            request_language = parsed.language
        task_id = task_dir.name if TASK_KEY_PATTERN.fullmatch(task_dir.name) else self.sequence.next_id(existing_ids)
        slug = slugify(title)
        final_task_dir = self._ensure_task_dir_name(task_dir, task_id)
        metadata = self.metadata_store.bootstrap(
            final_task_dir,
            state,
            task_id,
            title,
            slug,
            target_repo_root=target_repo_root,
            base_branch=base_branch,
            request_language=request_language,
        )
        return metadata, final_task_dir

    def _state_entered_at(self, metadata: TaskMetadata, state: TaskState):
        for entry in reversed(metadata.history):
            if entry.state == state:
                return entry.entered_at
        return None

    def _active_model(self, metadata: TaskMetadata, state: TaskState) -> str | None:
        if state == TaskState.PLANNING:
            return metadata.plan.resolved_model
        if state == TaskState.IMPLEMENTING:
            return metadata.implementation.resolved_model
        if state == TaskState.REVIEWING:
            return metadata.review.resolved_model
        return None

    def _ensure_task_dir_name(self, task_dir: Path, task_id: str) -> Path:
        if task_dir.name == task_id:
            return task_dir
        target = task_dir.parent / task_id
        if target.exists():
            return task_dir
        task_dir.rename(target)
        return target
