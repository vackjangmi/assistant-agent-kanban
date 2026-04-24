from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Mapping, cast

from ..config import AppConfig, AssistantBackend, AssistantRole
from ..events import EventBus
from ..exceptions import AdapterRunError
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
        adapter_registry: Mapping[str | AssistantBackend, AssistantAdapter] | None = None,
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

    def assistant_role(self) -> AssistantRole:
        if self.worker_name == "committer":
            return "commit"
        return cast(AssistantRole, self.worker_name)

    async def emit(self, event: str, task_id: str, **payload: object) -> None:
        await self.event_bus.publish(WorkerEvent(event=event, task_id=task_id, payload=dict(payload)))

    async def announce_log_file(self, task_id: str, log_name: str) -> None:
        await self.emit("worker_log_file", task_id, log_name=log_name)

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
            instructions.append(
                "You must edit files only in the current workspace before returning. "
                "The real target repo at "
                f"`{metadata.target.repo_root}` is off-limits and must never be modified directly. "
                "Treat any path outside the current workspace as read-only context. "
                "Do not return a markdown summary unless you made real workspace file changes."
            )
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

    def workspace_patch_fingerprint(self, workspace_repo: Path, base_branch: str) -> str | None:
        base_ref = self._resolve_workspace_base_ref(workspace_repo, base_branch)
        if base_ref is None:
            return None
        diff = subprocess.run(
            ["git", "-C", str(workspace_repo), "diff", "--binary", base_ref, "--"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode != 0:
            return None
        tracked_patch = diff.stdout
        untracked = subprocess.run(
            ["git", "-C", str(workspace_repo), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
        )
        if untracked.returncode != 0:
            return None
        digest = hashlib.sha256()
        digest.update(tracked_patch.encode("utf-8"))
        for raw_path in [part for part in untracked.stdout.split(b"\x00") if part]:
            relative_path = raw_path.decode("utf-8", errors="replace")
            digest.update(b"\0untracked\0")
            digest.update(relative_path.encode("utf-8", errors="replace"))
            file_path = workspace_repo / relative_path
            if file_path.is_file():
                digest.update(b"\0")
                digest.update(file_path.read_bytes())
        return digest.hexdigest()

    def _resolve_workspace_base_ref(self, workspace_repo: Path, base_branch: str) -> str | None:
        candidates = [base_branch, f"origin/{base_branch}"]
        for candidate in candidates:
            probe = subprocess.run(
                ["git", "-C", str(workspace_repo), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode == 0:
                return candidate
        return None

    def make_log_callback(self, loop: asyncio.AbstractEventLoop, task_id: str, log_name: str):
        content = ""
        rendered_content = ""
        debug_content = ""

        def callback(raw_line: str, rendered_line: str | None) -> None:
            nonlocal content, rendered_content, debug_content
            next_chunk = raw_line if raw_line.endswith("\n") else f"{raw_line}\n"
            content = f"{content}{next_chunk}"
            rendered_delta = rendered_line or render_assistant_log(next_chunk) or None
            debug_delta = rendered_line or render_assistant_log(next_chunk, debug=True) or None
            if rendered_delta:
                rendered_content = f"{rendered_content}{rendered_delta}"
            if debug_delta:
                debug_content = f"{debug_content}{debug_delta}"
            loop.call_soon_threadsafe(
                asyncio.create_task,
                self.emit(
                    "worker_log",
                    task_id,
                    log_name=log_name,
                    rendered_delta=rendered_delta,
                    debug_rendered_delta=debug_delta,
                    rendered_content=rendered_content or render_assistant_log(content) or None,
                    debug_rendered_content=debug_content or render_assistant_log(content, debug=True) or None,
                ),
            )

        return callback

    def append_log_marker(
        self,
        *,
        log_path: Path,
        phase: str,
        cycle: int,
    ) -> None:
        marker = f"\n===== phase={phase} cycle={cycle} =====\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as handle:
            handle.write(marker)

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

    def resolve_task_adapter(self, task_dir: Path, metadata: TaskMetadata, *, role: AssistantRole | None = None) -> AssistantAdapter:
        run_config = self.resolve_task_run_config(task_dir, metadata)
        resolved_role = role or self.assistant_role()
        backend = run_config.backend_for_role(resolved_role)
        adapter = self.adapter_registry.get(backend)
        if adapter is None:
            adapter = getattr(self, "adapter", None)
        if adapter is None:
            raise RuntimeError(f"no adapter registered for backend: {backend}")
        availability_error = adapter.availability_error(config=run_config, backend=backend)
        if availability_error is not None:
            raise AdapterRunError(f"{backend} backend is unavailable for {resolved_role}: {availability_error}")
        return adapter

    def worker_live_logs_enabled(self, run_config: AppConfig, *, role: AssistantRole | None = None) -> bool:
        return run_config.backend_for_role(role or self.assistant_role()) == "opencode" and run_config.opencode.worker_live_logs_enabled
