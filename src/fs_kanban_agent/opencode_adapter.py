from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .agent_materializer import ensure_runtime_agent, runtime_config_home
from .config import AppConfig
from .exceptions import AdapterRunError
from .log_parser import render_opencode_event_line
from .models import RunResult


class OpenCodeAdapter:
    def run(
        self,
        *,
        agent: str,
        prompt: str,
        cwd: Path,
        run_log_path: Path,
        config: AppConfig,
        session_id: str | None = None,
        on_log_line: Callable[[str, str | None], None] | None = None,
    ) -> RunResult:
        raise NotImplementedError

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        return []


@dataclass(slots=True)
class OpenCodeModelSnapshot:
    models: list[str]
    discovered_at: str | None
    error: str | None
    attempted: bool

    @property
    def status(self) -> str:
        if self.error and self.models:
            return "fallback"
        if self.error:
            return "error"
        if self.models:
            return "ready"
        if self.attempted:
            return "empty"
        return "idle"


class OpenCodeModelRegistry:
    def __init__(self, *, adapter: OpenCodeAdapter, config: AppConfig) -> None:
        self.adapter = adapter
        self.config = config
        self._lock = threading.Lock()
        self._models: list[str] = []
        self._discovered_at: str | None = None
        self._error: str | None = None
        self._attempted = False

    def snapshot(self) -> OpenCodeModelSnapshot:
        with self._lock:
            return OpenCodeModelSnapshot(
                models=list(self._models),
                discovered_at=self._discovered_at,
                error=self._error,
                attempted=self._attempted,
            )

    def get(self, *, refresh: bool = False) -> OpenCodeModelSnapshot:
        if refresh:
            return self.refresh(refresh_cli=True)
        snapshot = self.snapshot()
        if snapshot.attempted:
            return snapshot
        return self.refresh(refresh_cli=False)

    def refresh(self, *, refresh_cli: bool) -> OpenCodeModelSnapshot:
        try:
            models = self.adapter.discover_models(config=self.config, refresh=refresh_cli)
        except AdapterRunError as exc:
            with self._lock:
                self._attempted = True
                self._error = str(exc)
            return self.snapshot()
        with self._lock:
            self._attempted = True
            self._models = models
            self._discovered_at = _utc_timestamp()
            self._error = None
        return self.snapshot()


class SubprocessOpenCodeAdapter(OpenCodeAdapter):
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

    def run(
        self,
        *,
        agent: str,
        prompt: str,
        cwd: Path,
        run_log_path: Path,
        config: AppConfig,
        session_id: str | None = None,
        on_log_line: Callable[[str, str | None], None] | None = None,
    ) -> RunResult:
        command = [config.opencode.binary, "run"]
        agent_path = ensure_runtime_agent(config, agent)
        resolved_model = _read_agent_model(agent_path)
        if config.opencode.attach_url:
            command.extend(["--attach", config.opencode.attach_url])
        if session_id:
            command.extend(["--session", session_id])
        if resolved_model:
            command.extend(["--model", resolved_model])
        command.extend(["--agent", agent, "--format", "json", "--", prompt])
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

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def read_stdout() -> None:
            assert process.stdout is not None
            with run_log_path.open("w") as handle:
                for line in process.stdout:
                    stdout_chunks.append(line)
                    handle.write(line)
                    handle.flush()
                    if on_log_line is not None:
                        on_log_line(line.rstrip("\n"), render_opencode_event_line(line))

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_chunks.append(line)

        stdout_thread = threading.Thread(target=read_stdout, name=f"{agent}-stdout", daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, name=f"{agent}-stderr", daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            returncode = process.wait(timeout=config.opencode.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            raise AdapterRunError(f"opencode timed out for agent {agent}") from exc

        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        assistant_text = _extract_assistant_text(stdout)
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
            total_tokens=_extract_total_tokens(stdout),
        )


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


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


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
