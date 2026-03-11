from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

from ..config import AppConfig
from ..events import EventBus
from ..language import language_name
from ..locks import TaskLockManager
from ..log_parser import render_opencode_log
from ..metadata_store import MetadataStore
from ..models import RunResult, TaskMetadata, WorkerEvent
from ..scanner import KanbanScanner
from ..transitions import TransitionManager


class WorkerBase:
    worker_name = "worker"

    def __init__(
        self,
        config: AppConfig,
        scanner: KanbanScanner,
        metadata_store: MetadataStore,
        locks: TaskLockManager,
        transitions: TransitionManager,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.scanner = scanner
        self.metadata_store = metadata_store
        self.locks = locks
        self.transitions = transitions
        self.event_bus = event_bus

    def make_run_id(self) -> str:
        return f"{self.worker_name}-{uuid.uuid4()}"

    async def emit(self, event: str, task_id: str, **payload: object) -> None:
        await self.event_bus.publish(WorkerEvent(event=event, task_id=task_id, payload=dict(payload)))

    def task_log_dir(self, task_id: str) -> Path:
        path = self.config.runs_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def build_prompt(self, source_text: str, metadata: TaskMetadata, *, phase: str) -> str:
        requested_language = language_name(metadata.request.language)
        instructions = [
            f"You are the fs-kanban {phase} worker.",
            f"Return the markdown artifact in {requested_language}.",
            "Translate headings and narrative content to that language while preserving the required structure and semantics from the agent contract.",
        ]
        if phase == "implementer":
            instructions.append("You must edit files in the current workspace before returning. Do not return a markdown summary unless you made real workspace file changes.")
        if phase == "reviewer":
            instructions.append("Keep one exact machine-readable line: `Verdict: PASS` or `Verdict: NEEDS_CHANGES`.")
        instructions.extend(["", "<task-document>", source_text.rstrip(), "</task-document>"])
        return "\n".join(instructions)

    def workspace_has_changes(self, workspace_repo: Path) -> bool:
        result = subprocess.run(
            ["git", "-C", str(workspace_repo), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False
        return bool(result.stdout.strip())

    def workspace_has_local_commits(self, workspace_repo: Path, base_branch: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(workspace_repo), "rev-list", "--count", f"{base_branch}..HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False
        try:
            return int(result.stdout.strip() or "0") > 0
        except ValueError:
            return False

    def make_log_callback(self, loop: asyncio.AbstractEventLoop, task_id: str, log_name: str):
        content = ""

        def callback(raw_line: str, rendered_line: str | None) -> None:
            nonlocal content
            next_chunk = raw_line if raw_line.endswith("\n") else f"{raw_line}\n"
            content = f"{content}{next_chunk}"
            loop.call_soon_threadsafe(
                asyncio.create_task,
                self.emit(
                    "worker_log",
                    task_id,
                    log_name=log_name,
                    raw_line=raw_line,
                    rendered_line=rendered_line,
                    content=content,
                    rendered_content=render_opencode_log(content) or None,
                ),
            )

        return callback

    def write_result_artifacts(self, task_dir: Path, stem: str, result: RunResult) -> tuple[str, str]:
        markdown_path = task_dir / f"{stem}.md"
        json_path = task_dir / f"{stem}.json"
        markdown_path.write_text(result.assistant_text.strip() + "\n")
        json_path.write_text(
            json.dumps(
                {
                    "ok": result.ok,
                    "returncode": result.returncode,
                    "assistant_text": result.assistant_text,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "raw_events_path": result.raw_events_path,
                    "command": result.command,
                    "resolved_model": result.resolved_model,
                    "session_id": result.session_id,
                    "markdown_path": markdown_path.name,
                    "editable_markdown": True,
                    "sync_policy": "markdown_edits_do_not_modify_json",
                },
                indent=2,
            )
            + "\n"
        )
        return markdown_path.name, json_path.name
