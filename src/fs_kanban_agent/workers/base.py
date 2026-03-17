from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from pathlib import Path
from typing import Mapping

from ..config import AppConfig
from ..events import EventBus
from ..language import generation_language_name
from ..locks import TaskLockManager
from ..log_parser import render_assistant_log
from ..metadata_store import MetadataStore
from ..assistant_adapter import AssistantAdapter
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
        adapter_registry: Mapping[str, AssistantAdapter] | None = None,
    ) -> None:
        self.config = config
        self.scanner = scanner
        self.metadata_store = metadata_store
        self.locks = locks
        self.transitions = transitions
        self.event_bus = event_bus
        self.adapter_registry = dict(adapter_registry or {})

    def make_run_id(self) -> str:
        return f"{self.worker_name}-{uuid.uuid4()}"

    async def emit(self, event: str, task_id: str, **payload: object) -> None:
        await self.event_bus.publish(WorkerEvent(event=event, task_id=task_id, payload=dict(payload)))

    def task_log_dir(self, task_id: str) -> Path:
        path = self.config.runs_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def build_prompt(self, source_text: str, metadata: TaskMetadata, *, phase: str) -> str:
        requested_language = generation_language_name(metadata.request.language)
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
                    rendered_content=render_assistant_log(content) or None,
                    debug_rendered_content=render_assistant_log(content, debug=True) or None,
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
                    "total_tokens": result.total_tokens,
                    "markdown_path": markdown_path.name,
                    "editable_markdown": True,
                    "sync_policy": "markdown_edits_do_not_modify_json",
                },
                indent=2,
            )
            + "\n"
        )
        return markdown_path.name, json_path.name

    def reuse_session_id(self, *, session_id: str | None, session_tokens: int, budget: int) -> str | None:
        if not session_id:
            return None
        if session_tokens >= budget:
            return None
        return session_id

    def next_session_token_total(
        self,
        *,
        reused_session_id: str | None,
        returned_session_id: str | None,
        prior_session_tokens: int,
        run_tokens: int,
    ) -> int:
        if reused_session_id and returned_session_id == reused_session_id:
            return prior_session_tokens + run_tokens
        return run_tokens

    def ensure_task_runtime_pin(self, task_dir: Path, metadata: TaskMetadata) -> None:
        if metadata.runtime_pin is not None:
            return
        metadata.runtime_pin = self.config.capture_runtime_pin(captured_by=self.worker_name)
        self.metadata_store.save(task_dir, metadata)

    def resolve_task_run_config(self, task_dir: Path, metadata: TaskMetadata) -> AppConfig:
        self.ensure_task_runtime_pin(task_dir, metadata)
        return self.config.with_runtime_pin(metadata.runtime_pin)

    def resolve_task_adapter(self, task_dir: Path, metadata: TaskMetadata) -> AssistantAdapter:
        run_config = self.resolve_task_run_config(task_dir, metadata)
        backend = run_config.active_backend()
        adapter = self.adapter_registry.get(backend)
        if adapter is None:
            adapter = getattr(self, "adapter", None)
        if adapter is None:
            raise RuntimeError(f"no adapter registered for backend: {backend}")
        return adapter
