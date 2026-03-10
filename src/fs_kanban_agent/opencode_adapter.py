from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import AppConfig
from .exceptions import AdapterRunError
from .models import RunResult


class OpenCodeAdapter:
    def run(self, *, agent: str, prompt: str, cwd: Path, run_log_path: Path, config: AppConfig) -> RunResult:
        raise NotImplementedError


class SubprocessOpenCodeAdapter(OpenCodeAdapter):
    def run(self, *, agent: str, prompt: str, cwd: Path, run_log_path: Path, config: AppConfig) -> RunResult:
        command = [config.opencode.binary, "run"]
        if config.opencode.attach_url:
            command.extend(["--attach", config.opencode.attach_url])
        command.extend(["--agent", agent, "--format", "json", "--", prompt])
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=config.opencode.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterRunError(f"opencode timed out for agent {agent}") from exc
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        run_log_path.write_text(completed.stdout)
        assistant_text = _extract_assistant_text(completed.stdout)
        return RunResult(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            assistant_text=assistant_text,
            stdout=completed.stdout,
            stderr=completed.stderr,
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
