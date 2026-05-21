from __future__ import annotations

import json
import re
import subprocess
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable, cast

from .assistant_adapter import AssistantAdapter, _resolve_binary_error
from .config import AppConfig, AssistantRole
from .exceptions import AdapterRunError
from .models import RunResult


CODEX_KNOWN_MODELS = [
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
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
CODEX_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
CODEX_KNOWN_REASONING_MODELS = {
    "gpt-5.5": CODEX_REASONING_EFFORTS,
}
CODEX_REASONING_MODEL_RE = re.compile(r"^(?P<model>.+?)\s+\((?P<effort>low|medium|high|xhigh)\)$")


class SubprocessCodexAdapter(AssistantAdapter):
    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._task_processes: dict[str, set[subprocess.Popen[str]]] = defaultdict(set)

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        command = [config.codex.binary, "debug", "models"]
        if not refresh:
            command.append("--bundled")
        try:
            result = subprocess.run(
                command,
                cwd=str(config.repo_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=config.codex.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return _known_model_candidates()
        if result.returncode != 0:
            return _known_model_candidates()
        models = _parse_codex_discovered_models(result.stdout)
        return models or _known_model_candidates()

    def availability_error(self, *, config: AppConfig, backend) -> str | None:
        return _resolve_binary_error(config.codex.binary)

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
        command = [
            config.codex.binary,
            "exec",
            "-c",
            'approval_policy="never"',
            "-s",
            "workspace-write",
        ]
        if session_id:
            command.extend(["resume", session_id])
        command.extend(["--json", "--skip-git-repo-check"])
        role = _role_from_agent(agent)
        resolved_model = config.role_model(role)
        command_model, reasoning_effort = _split_codex_model_reasoning(resolved_model)
        if reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        if command_model:
            command.extend(["--model", command_model])
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
            failure_message = _extract_failure_message(stdout)
            return RunResult(
                ok=returncode == 0,
                returncode=returncode,
                assistant_text=_extract_assistant_text(stdout),
                stdout=stdout,
                stderr=stderr or failure_message,
                raw_events_path=str(run_log_path),
                command=command,
                resolved_model=resolved_model,
                session_id=_extract_session_id(stdout) or session_id,
                total_tokens=_extract_total_tokens(stdout),
                session_budget_tokens=_extract_session_budget_tokens(stdout),
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
    return cast(AssistantRole, "planner")


def _parse_codex_discovered_models(stdout: str) -> list[str]:
    payload = _decode_codex_models_payload(stdout)
    if isinstance(payload, dict):
        raw_models = payload.get("models")
    else:
        raw_models = payload
    if not isinstance(raw_models, list):
        return []

    models: list[str] = []
    seen: set[str] = set()
    for item in raw_models:
        candidate: str | None = None
        reasoning_efforts: list[str] = []
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            if item.get("visibility") == "hidden":
                continue
            for key in ("slug", "id", "model"):
                value = item.get(key)
                if isinstance(value, str):
                    candidate = value
                    break
            reasoning_efforts = _extract_supported_reasoning_efforts(item)
        if candidate is None:
            continue
        _append_model_candidate(models, seen, candidate)
        for effort in reasoning_efforts:
            _append_model_candidate(models, seen, _format_reasoning_model(candidate, effort))
    return models


def _known_model_candidates() -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for model in CODEX_KNOWN_MODELS:
        _append_model_candidate(models, seen, model)
        for effort in CODEX_KNOWN_REASONING_MODELS.get(model, ()):
            _append_model_candidate(models, seen, _format_reasoning_model(model, effort))
    return models


def _append_model_candidate(models: list[str], seen: set[str], value: str) -> None:
    normalized = value.strip()
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    models.append(normalized)


def _extract_supported_reasoning_efforts(item: dict) -> list[str]:
    raw_levels = item.get("supported_reasoning_levels")
    if not isinstance(raw_levels, list):
        return []
    efforts: list[str] = []
    for level in raw_levels:
        if not isinstance(level, dict):
            continue
        effort = level.get("effort")
        if isinstance(effort, str) and effort in CODEX_REASONING_EFFORTS and effort not in efforts:
            efforts.append(effort)
    return efforts


def _format_reasoning_model(model: str, effort: str) -> str:
    return f"{model.strip()} ({effort})"


def _split_codex_model_reasoning(model: str | None) -> tuple[str | None, str | None]:
    if model is None:
        return None, None
    normalized = model.strip()
    if not normalized:
        return None, None
    match = CODEX_REASONING_MODEL_RE.match(normalized)
    if not match:
        return normalized, None
    return match.group("model").strip(), match.group("effort")


def _decode_codex_models_payload(stdout: str) -> object | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char not in "{[":
            continue
        try:
            payload, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        return payload
    return None


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
    return _extract_token_sum(stdout, keys=("input_tokens", "output_tokens"))


def _extract_session_budget_tokens(stdout: str) -> int:
    return _extract_token_sum(stdout, keys=("input_tokens", "output_tokens"))


def _extract_token_sum(stdout: str, *, keys: tuple[str, ...]) -> int:
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
                for key in keys:
                    value = usage.get(key)
                    if isinstance(value, int):
                        total += value
    return total


def _extract_failure_message(stdout: str) -> str:
    messages: list[str] = []
    saw_empty_web_search = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _contains_empty_web_search(payload):
            saw_empty_web_search = True
        if payload.get("type") != "turn.failed":
            continue
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message.strip())
    if not messages:
        return ""
    details = messages[-1]
    if saw_empty_web_search:
        details = f"{details} Previous Codex event included web_search with an empty query."
    return details


def _contains_empty_web_search(value: object) -> bool:
    if isinstance(value, dict):
        item_type = value.get("type")
        query = value.get("query")
        if item_type == "web_search" and isinstance(query, str) and not query.strip():
            return True
        return any(_contains_empty_web_search(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_empty_web_search(item) for item in value)
    return False


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
