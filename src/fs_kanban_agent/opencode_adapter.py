from __future__ import annotations

import json
import os
import subprocess
import threading
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
        on_log_line: Callable[[str, str | None], None] | None = None,
    ) -> RunResult:
        raise NotImplementedError


class SubprocessOpenCodeAdapter(OpenCodeAdapter):
    def run(
        self,
        *,
        agent: str,
        prompt: str,
        cwd: Path,
        run_log_path: Path,
        config: AppConfig,
        on_log_line: Callable[[str, str | None], None] | None = None,
    ) -> RunResult:
        command = [config.opencode.binary, "run"]
        ensure_runtime_agent(config, agent)
        if config.opencode.attach_url:
            command.extend(["--attach", config.opencode.attach_url])
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
        return RunResult(
            ok=returncode == 0,
            returncode=returncode,
            assistant_text=assistant_text,
            stdout=stdout,
            stderr=stderr,
            raw_events_path=str(run_log_path),
            command=command,
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
    return stdout.strip()
