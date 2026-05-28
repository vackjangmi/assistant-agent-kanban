from __future__ import annotations

import json
import subprocess
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from .assistant_adapter import AssistantAdapter, _resolve_binary_error
from .config import AppConfig, AssistantRole
from .exceptions import AdapterRunError
from .models import RunResult


CLAUDE_MODEL_ALIASES = [
    "default",
    "best",
    "sonnet",
    "opus",
    "haiku",
    "opus[1m]",
    "opusplan",
]

class SubprocessClaudeAdapter(AssistantAdapter):
    supports_model_discovery = True

    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._task_processes: dict[str, set[subprocess.Popen[str]]] = defaultdict(set)

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        del config, refresh
        return list(CLAUDE_MODEL_ALIASES)

    def availability_error(self, *, config: AppConfig, backend) -> str | None:
        del backend
        return _resolve_binary_error(config.claude.binary)

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
        role = _role_from_agent(agent)
        command = [
            config.claude.binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Bash,Read,Edit,Write,Glob,Grep,MultiEdit",
        ]
        resolved_model = config.role_model(role)
        if session_id:
            command.extend(["--resume", session_id])
        if resolved_model:
            command.extend(["--model", resolved_model])
        normalized_include_directories = _normalize_include_directories(include_directories, cwd=cwd)
        for directory in normalized_include_directories:
            command.extend(["--add-dir", directory])
        command.extend(["--", prompt])
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
            raise AdapterRunError(f"failed to start claude for agent {agent}") from exc
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
                        on_log_line(line.rstrip("\n"), _render_claude_event_line(line))

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
                returncode = process.wait(timeout=config.claude.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                raise AdapterRunError(f"claude timed out for agent {agent}") from exc

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
    if suffix in {"request-draft", "request_draft"}:
        return "request_draft"
    if suffix in {"plan-approval", "plan_approval"}:
        return "plan_approval"
    if suffix == "implementer":
        return "implementer"
    if suffix == "reviewer":
        return "reviewer"
    if suffix == "commit":
        return "commit"
    return "planner"


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
    result_texts: list[str] = []
    assistant_messages: list[str] = []
    deltas: list[str] = []
    for payload in _iter_json_lines(stdout):
        event_type = payload.get("type")
        if event_type == "result":
            text = _extract_text_from_payload(payload.get("result") or payload)
            if text:
                result_texts.append(text)
            continue
        if event_type in {"assistant", "message"}:
            role = payload.get("role")
            if role not in {None, "assistant"}:
                continue
            text = _extract_text_from_payload(payload.get("message") or payload)
            if text:
                assistant_messages.append(text)
            continue
        if event_type == "stream_event":
            text = _extract_stream_delta_text(payload.get("event"))
            if text:
                deltas.append(text)
    if result_texts:
        return result_texts[-1]
    if assistant_messages:
        return assistant_messages[-1]
    if deltas:
        return "".join(deltas).strip()
    return ""


def _extract_session_id(stdout: str) -> str | None:
    for payload in _iter_json_lines(stdout):
        for candidate in (
            payload.get("session_id"),
            payload.get("sessionId"),
            (payload.get("result") or {}).get("session_id") if isinstance(payload.get("result"), dict) else None,
            (payload.get("result") or {}).get("sessionId") if isinstance(payload.get("result"), dict) else None,
            (payload.get("event") or {}).get("session_id") if isinstance(payload.get("event"), dict) else None,
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return None


def _extract_total_tokens(stdout: str) -> int:
    total = 0
    for payload in _iter_json_lines(stdout):
        total += _extract_tokens_from_payload(payload)
    return total


def _extract_tokens_from_payload(payload: object) -> int:
    if not isinstance(payload, dict):
        return 0
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key in ("total_tokens", "totalTokens"):
            value = usage.get(key)
            if isinstance(value, int):
                return value
        total = 0
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                total += value
        if total:
            return total
    nested_total = 0
    for key in ("result", "event", "message"):
        nested = payload.get(key)
        nested_total += _extract_tokens_from_payload(nested)
    return nested_total


def _extract_text_from_payload(payload: object) -> str | None:
    if isinstance(payload, str) and payload.strip():
        return payload
    if isinstance(payload, list):
        collected: list[str] = []
        for item in payload:
            text = _extract_text_from_payload(item)
            if text:
                collected.append(text)
        if collected:
            return "\n".join(collected)
        return None
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return text
    content = payload.get("content")
    extracted_content = _extract_text_from_payload(content)
    if extracted_content:
        return extracted_content
    message = payload.get("message")
    extracted_message = _extract_text_from_payload(message)
    if extracted_message:
        return extracted_message
    result = payload.get("result")
    extracted_result = _extract_text_from_payload(result)
    if extracted_result:
        return extracted_result
    delta = payload.get("delta")
    extracted_delta = _extract_stream_delta_text(delta)
    if extracted_delta:
        return extracted_delta
    return None


def _extract_stream_delta_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    nested_delta = payload.get("delta")
    if nested_delta is not None and nested_delta is not payload:
        nested_text = _extract_stream_delta_text(nested_delta)
        if nested_text:
            return nested_text
    delta_type = payload.get("type")
    if delta_type == "text_delta":
        text = payload.get("text")
        if isinstance(text, str) and text:
            return text
    return _extract_text_from_payload(payload.get("content"))


def _render_claude_event_line(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line
    event_type = payload.get("type")
    if event_type == "system":
        subtype = payload.get("subtype")
        if subtype == "api_retry":
            attempt = payload.get("attempt")
            max_retries = payload.get("max_retries")
            if isinstance(attempt, int) and isinstance(max_retries, int):
                return f"Retrying Claude API call ({attempt}/{max_retries})"
        return None
    text = _extract_stream_delta_text((payload.get("event") or {}) if event_type == "stream_event" else payload)
    if text:
        return text
    if event_type == "result":
        return "Completed Claude run"
    return None


def _iter_json_lines(stdout: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique
