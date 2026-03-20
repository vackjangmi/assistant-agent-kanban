from __future__ import annotations

import json
import subprocess
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable, cast

from .assistant_adapter import AssistantAdapter
from .config import AppConfig, AssistantRole
from .exceptions import AdapterRunError
from .models import RunResult


CODEX_KNOWN_MODELS = [
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2-codex",
    "gpt-5.2",
    "gpt-5.1-codex-max",
    "gpt-5.1",
    "gpt-5.1-codex",
    "gpt-5-codex",
    "gpt-5-codex-mini",
    "gpt-5",
]


class SubprocessCodexAdapter(AssistantAdapter):
    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._task_processes: dict[str, set[subprocess.Popen[str]]] = defaultdict(set)

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        return list(CODEX_KNOWN_MODELS)

    def run(
        self,
        *,
        agent: str,
        prompt: str,
        cwd: Path,
        run_log_path: Path,
        config: AppConfig,
        session_id: str | None = None,
        cancel_key: str | None = None,
        on_log_line: Callable[[str, str | None], None] | None = None,
        output_format: str = "json",
        stream_stderr_to_log: bool = False,
        show_thinking: bool = False,
    ) -> RunResult:
        command = [
            config.codex.binary,
            "exec",
            "-c",
            'approval_policy="never"',
            "-s",
            "danger-full-access",
        ]
        if session_id:
            command.extend(["resume", session_id])
        command.extend(["--json", "--skip-git-repo-check"])
        resolved_model = config.role_model(_role_from_agent(agent))
        if resolved_model:
            command.extend(["--model", resolved_model])
        command.append(prompt)
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise AdapterRunError(f"failed to start codex for agent {agent}") from exc
        if cancel_key:
            with self._process_lock:
                self._task_processes[cancel_key].add(process)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def read_stdout() -> None:
            assert process.stdout is not None
            with run_log_path.open("a") as handle:
                for line in process.stdout:
                    stdout_chunks.append(line)
                    handle.write(line)
                    handle.flush()
                    if on_log_line is not None:
                        on_log_line(line.rstrip("\n"), _render_codex_event_line(line))

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_chunks.append(line)

        stdout_thread = threading.Thread(target=read_stdout, name=f"{agent}-stdout", daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, name=f"{agent}-stderr", daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            try:
                returncode = process.wait(timeout=config.codex.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                raise AdapterRunError(f"codex timed out for agent {agent}") from exc

            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)
            return RunResult(
                ok=returncode == 0,
                returncode=returncode,
                assistant_text=_extract_assistant_text(stdout),
                stdout=stdout,
                stderr=stderr,
                raw_events_path=str(run_log_path),
                command=command,
                resolved_model=resolved_model,
                session_id=_extract_session_id(stdout) or session_id,
                total_tokens=_extract_total_tokens(stdout),
            )
        finally:
            if cancel_key:
                with self._process_lock:
                    processes = self._task_processes.get(cancel_key)
                    if processes is not None:
                        processes.discard(process)
                        if not processes:
                            self._task_processes.pop(cancel_key, None)

    def cancel_task(self, task_id: str) -> None:
        with self._process_lock:
            processes = list(self._task_processes.pop(task_id, set()))
        for process in processes:
            if process.poll() is None:
                process.kill()


def _role_from_agent(agent: str) -> AssistantRole:
    suffix = agent.removeprefix("fs-kanban-")
    if suffix == "planner":
        return "planner"
    if suffix == "implementer":
        return "implementer"
    if suffix == "reviewer":
        return "reviewer"
    if suffix == "commit":
        return "commit"
    return cast(AssistantRole, "planner")


def _extract_assistant_text(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "item.completed":
            item = payload.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        if payload.get("type") == "turn.completed":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


def _extract_session_id(stdout: str) -> str | None:
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "thread.started":
            thread_id = payload.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                return thread_id
        if payload.get("type") == "session_configured":
            session_id = payload.get("session_id")
            if isinstance(session_id, str) and session_id.strip():
                return session_id
    return None


def _extract_total_tokens(stdout: str) -> int:
    total = 0
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "turn.completed":
            usage = payload.get("usage") or {}
            if isinstance(usage, dict):
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    value = usage.get(key)
                    if isinstance(value, int):
                        total += value
    return total


def _render_codex_event_line(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line
    event_type = payload.get("type")
    if event_type == "item.started":
        item = payload.get("item") or {}
        if isinstance(item, dict):
            item_type = item.get("type")
            if isinstance(item_type, str):
                return f"Started {item_type.replace('_', ' ')}"
    if event_type == "item.completed":
        item = payload.get("item") or {}
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    return text
            if isinstance(item_type, str):
                return f"Completed {item_type.replace('_', ' ')}"
    if event_type == "turn.failed":
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return f"ERROR: {message}"
        return "ERROR: turn failed"
    return None
