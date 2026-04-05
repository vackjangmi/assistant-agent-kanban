from __future__ import annotations

import json
import subprocess
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .assistant_adapter import AssistantAdapter, _resolve_binary_error
from .config import AppConfig, AssistantRole
from .exceptions import AdapterRunError
from .models import RunResult


GEMINI_KNOWN_MODELS = [
    "auto-gemini-3",
    "auto-gemini-2.5",
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


class SubprocessGeminiAdapter(AssistantAdapter):
    supports_model_discovery = True

    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._task_processes: dict[str, set[subprocess.Popen[str]]] = defaultdict(set)

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        del config, refresh
        return list(GEMINI_KNOWN_MODELS)

    def availability_error(self, *, config: AppConfig, backend) -> str | None:
        return _resolve_binary_error(config.gemini.binary)

    def run(
        self,
        *,
        agent: str,
        prompt: str,
        cwd: Path,
        run_log_path: Path,
        config: AppConfig,
        include_directories: list[Path] | None = None,
        session_id: str | None = None,
        cancel_key: str | None = None,
        on_log_line: Callable[[str, str | None], None] | None = None,
        output_format: str = "json",
        stream_stderr_to_log: bool = False,
        show_thinking: bool = False,
    ) -> RunResult:
        del output_format, show_thinking
        command = [
            config.gemini.binary,
            "--prompt",
            prompt,
            "--approval-mode",
            _approval_mode_for_role(_role_from_agent(agent)),
            "--output-format",
            "stream-json",
        ]
        resolved_model = config.role_model(_role_from_agent(agent))
        if resolved_model:
            command.extend(["--model", resolved_model])
        for directory in _normalize_include_directories(include_directories, cwd=cwd):
            command.extend(["--include-directories", directory])
        if session_id:
            command.extend(["--resume", session_id])
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
            raise AdapterRunError(f"failed to start gemini for agent {agent}") from exc
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
                        on_log_line(line.rstrip("\n"), _render_gemini_event_line(line))

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_chunks.append(line)
                if stream_stderr_to_log:
                    with run_log_path.open("a") as handle:
                        handle.write(line)
                        handle.flush()
                    if on_log_line is not None:
                        on_log_line(line.rstrip("\n"), line.rstrip("\n"))

        stdout_thread = threading.Thread(target=read_stdout, name=f"{agent}-stdout", daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, name=f"{agent}-stderr", daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            try:
                returncode = process.wait(timeout=config.gemini.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                raise AdapterRunError(f"gemini timed out for agent {agent}") from exc

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
    if suffix in {"plan-approval", "plan_approval"}:
        return "plan_approval"
    if suffix == "implementer":
        return "implementer"
    if suffix == "reviewer":
        return "reviewer"
    if suffix == "commit":
        return "commit"
    return "planner"


def _approval_mode_for_role(role: AssistantRole) -> str:
    if role in {"implementer", "commit"}:
        return "yolo"
    return "auto_edit"


def _normalize_include_directories(include_directories: list[Path] | None, *, cwd: Path) -> list[str]:
    if not include_directories:
        return []
    resolved_cwd = cwd.expanduser().resolve()
    normalized: list[str] = []
    seen: set[str] = set()
    for directory in include_directories:
        resolved_directory = directory.expanduser().resolve()
        if resolved_directory == resolved_cwd:
            continue
        normalized_path = str(resolved_directory)
        if normalized_path in seen:
            continue
        seen.add(normalized_path)
        normalized.append(normalized_path)
    return normalized


def _extract_assistant_text(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    result_texts: list[str] = []
    assistant_messages: list[str] = []
    assistant_deltas: list[str] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        result_text = _extract_result_text(payload)
        if result_text:
            result_texts.append(result_text)
            continue
        assistant_text = _extract_assistant_message_text(payload)
        if not assistant_text:
            continue
        if payload.get("delta") is True:
            assistant_deltas.append(assistant_text)
        else:
            assistant_messages.append(assistant_text)
    if result_texts:
        return result_texts[-1]
    if assistant_messages:
        return assistant_messages[-1]
    if assistant_deltas:
        return "".join(assistant_deltas).strip()
    return ""


def _extract_text_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("content", "text", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    message = payload.get("message")
    if isinstance(message, dict):
        nested = message.get("content")
        if isinstance(nested, str) and nested.strip():
            return nested
        parts = message.get("parts")
        if isinstance(parts, list):
            text = _text_from_parts(parts)
            if text:
                return text
    result = payload.get("result")
    if isinstance(result, dict):
        nested = _extract_text_from_payload(result)
        if nested:
            return nested
    return None


def _extract_result_text(payload: object) -> str | None:
    if not isinstance(payload, dict) or payload.get("type") != "result":
        return None
    return _extract_text_from_payload(payload)


def _extract_assistant_message_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "message" or payload.get("role") != "assistant":
        return None
    return _extract_text_from_payload(payload)


def _text_from_parts(parts: list[object]) -> str | None:
    collected: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            collected.append(text)
    if not collected:
        return None
    return "\n".join(collected)


def _extract_session_id(stdout: str) -> str | None:
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in ("session_id", "sessionId"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        result = payload.get("result")
        if isinstance(result, dict):
            for key in ("session_id", "sessionId"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value
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
        total += _extract_tokens_from_payload(payload)
    return total


def _extract_tokens_from_payload(payload: object) -> int:
    if not isinstance(payload, dict):
        return 0
    usage = payload.get("usage")
    if isinstance(usage, dict):
        total = usage.get("totalTokens")
        if isinstance(total, int):
            return total
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
    result = payload.get("result")
    if isinstance(result, dict):
        return _extract_tokens_from_payload(result)
    return 0


def _render_gemini_event_line(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line.rstrip("\n") or None
    event_type = payload.get("type")
    if event_type == "tool_use":
        name = payload.get("name") or payload.get("tool")
        if isinstance(name, str) and name:
            return f"Tool `{name}` called"
    text = _extract_text_from_payload(payload)
    if text:
        return text
    if isinstance(event_type, str) and event_type:
        return event_type.replace("_", " ").title()
    return None
