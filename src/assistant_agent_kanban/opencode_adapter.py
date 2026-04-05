from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .agent_materializer import ensure_runtime_agent, runtime_config_home
from .assistant_adapter import AssistantAdapter, _resolve_binary_error
from .config import AppConfig
from .exceptions import AdapterRunError
from .log_parser import render_opencode_event_line
from .models import RunResult


class SubprocessOpenCodeAdapter(AssistantAdapter):
    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._task_processes: dict[str, set[subprocess.Popen[str]]] = defaultdict(set)

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        command = [config.opencode.binary, "models", "--verbose"]
        if refresh:
            command.append("--refresh")
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(runtime_config_home(config))
        try:
            result = subprocess.run(
                command,
                cwd=str(config.repo_root),
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=config.opencode.timeout_seconds,
            )
        except OSError as exc:
            raise AdapterRunError("failed to start opencode model discovery") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdapterRunError("opencode model discovery timed out") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip() or "opencode model discovery failed"
            raise AdapterRunError(message)
        return _parse_discovered_models(result.stdout)

    def availability_error(self, *, config: AppConfig, backend) -> str | None:
        return _resolve_binary_error(config.opencode.binary)

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
        del include_directories
        command = [config.opencode.binary, "run"]
        agent_path = ensure_runtime_agent(config, agent)
        resolved_model = _read_agent_model(agent_path)
        if session_id:
            command.extend(["--session", session_id])
        if resolved_model:
            command.extend(["--model", resolved_model])
        if show_thinking:
            command.append("--thinking")
        command.extend(["--agent", agent, "--format", output_format, "--", prompt])
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(runtime_config_home(config))
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise AdapterRunError(f"failed to start opencode for agent {agent}") from exc
        if cancel_key:
            with self._process_lock:
                self._task_processes[cancel_key].add(process)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        log_handle = run_log_path.open("a")
        log_lock = threading.Lock()

        def append_log_line(line: str) -> None:
            with log_lock:
                log_handle.write(line)
                log_handle.flush()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                normalized = _normalize_stream_line(line, output_format=output_format)
                stdout_chunks.append(normalized)
                append_log_line(normalized)
                if on_log_line is not None:
                    on_log_line(normalized.rstrip("\n"), render_opencode_event_line(normalized))

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                normalized = _normalize_stream_line(line, output_format=output_format)
                stderr_chunks.append(normalized)
                if stream_stderr_to_log:
                    append_log_line(normalized)
                    if on_log_line is not None:
                        on_log_line(normalized.rstrip("\n"), render_opencode_event_line(normalized))

        stdout_thread = threading.Thread(target=read_stdout, name=f"{agent}-stdout", daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, name=f"{agent}-stderr", daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            try:
                returncode = process.wait(timeout=config.opencode.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                raise AdapterRunError(f"opencode timed out for agent {agent}") from exc

            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)
            assistant_text = _extract_assistant_text(stdout) if output_format == "json" else _extract_default_assistant_text(stdout, stderr)
            resolved_session_id = _extract_session_id(stdout) or session_id
            return RunResult(
                ok=returncode == 0,
                returncode=returncode,
                assistant_text=assistant_text,
                stdout=stdout,
                stderr=stderr,
                raw_events_path=str(run_log_path),
                command=command,
                resolved_model=resolved_model,
                session_id=resolved_session_id,
                total_tokens=_extract_total_tokens(stdout) if output_format == "json" else 0,
            )
        finally:
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            log_handle.close()
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


def _extract_assistant_text(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "message" and payload.get("role") == "assistant":
            return payload.get("content", "")
        if payload.get("type") == "final":
            return payload.get("content", "")
        if payload.get("type") == "text":
            part = payload.get("part") or {}
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def _extract_default_assistant_text(stdout: str, stderr: str) -> str:
    visible_stdout = stdout.strip()
    if visible_stdout:
        return visible_stdout
    return stderr.strip()


def _extract_session_id(stdout: str) -> str | None:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = payload.get("sessionID")
        if isinstance(session_id, str) and session_id.strip():
            return session_id
        part = payload.get("part")
        if isinstance(part, dict):
            nested_session_id = part.get("sessionID")
            if isinstance(nested_session_id, str) and nested_session_id.strip():
                return nested_session_id
    return None


def _extract_total_tokens(stdout: str) -> int:
    total_tokens = 0
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "step_finish":
            continue
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            continue
        total = tokens.get("total")
        if isinstance(total, int):
            total_tokens += total
    return total_tokens


def _normalize_stream_line(line: str, *, output_format: str) -> str:
    if output_format == "json":
        return line
    return _strip_ansi(line)


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value)


def _read_agent_model(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return _extract_agent_model(path.read_text())


def _extract_agent_model(content: str) -> str | None:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return None
    for line in lines[1:closing]:
        if line.startswith("model:"):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def _parse_discovered_models(stdout: str) -> list[str]:
    stripped = stdout.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return _parse_models_from_text(stripped)
    return _unique_models(_extract_models_from_payload(payload))


def _extract_models_from_payload(payload: object) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, list):
        models: list[str] = []
        for item in payload:
            models.extend(_extract_models_from_payload(item))
        return models
    if isinstance(payload, dict):
        for key in ("models", "items", "data"):
            value = payload.get(key)
            if value is not None:
                models = _extract_models_from_payload(value)
                if models:
                    return models
        for key in ("id", "name", "model"):
            value = payload.get(key)
            if isinstance(value, str):
                return [value]
    return []


def _parse_models_from_text(stdout: str) -> list[str]:
    models: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or set(line) <= {"-", "=", "|", "+", " "}:
            continue
        if line.lower().startswith(("provider", "available models", "models:")):
            continue
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            if cells and _looks_like_model_identifier(cells[0]):
                models.append(cells[0])
            continue
        line = line.lstrip("-*• ").strip()
        match = re.match(r"([^\s(]+)", line)
        if match:
            candidate = match.group(1).rstrip(":")
            if _looks_like_model_identifier(candidate):
                models.append(candidate)
    return _unique_models(models)


def _looks_like_model_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:-]*", value, flags=re.IGNORECASE))


def _unique_models(models: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for model in models:
        normalized = model.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
