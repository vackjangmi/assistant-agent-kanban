from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .assistant_adapter import AssistantAdapter, _resolve_binary_error
from .config import AppConfig, AssistantRole
from .exceptions import AdapterRunError
from .models import RunResult


ANTIGRAVITY_MODEL_RESTORE_DELAY_SECONDS = 30.0
ANTIGRAVITY_KNOWN_MODELS = [
    "Gemini 3.1 Pro (Low)",
    "Gemini 3.1 Pro (High)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
]


class SubprocessAntigravityAdapter(AssistantAdapter):
    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._settings_lock = threading.Lock()
        self._task_processes: dict[str, set[subprocess.Popen[str]]] = {}

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        del refresh
        configured = [getattr(config.antigravity, f"{role}_model") for role in _ASSISTANT_ROLES]
        settings_model = _read_settings_model(_resolve_settings_path(config))
        return _deduplicate_models([*ANTIGRAVITY_KNOWN_MODELS, settings_model, *configured])

    def availability_error(self, *, config: AppConfig, backend) -> str | None:
        del backend
        return _resolve_binary_error(config.antigravity.binary)

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
        resolved_model = config.role_model(role)
        bound_prompt = _bind_prompt_to_cwd(prompt, cwd=cwd)
        command = [
            config.antigravity.binary,
            "--print-timeout",
            f"{max(1, config.antigravity.timeout_seconds)}s",
        ]
        if config.antigravity.dangerously_skip_permissions:
            command.append("--dangerously-skip-permissions")
        if config.antigravity.sandbox:
            command.append("--sandbox")
        if session_id:
            command.extend(["--conversation", session_id])
        for directory in _normalize_workspace_directories(include_directories, cwd=cwd):
            command.extend(["--add-dir", directory])
        command.extend(["--print", bound_prompt])

        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        process = self._start_process(command=command, cwd=cwd, config=config, model=resolved_model, agent=agent)

        if cancel_key:
            with self._process_lock:
                self._task_processes.setdefault(cancel_key, set()).add(process)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def read_stdout() -> None:
            assert process.stdout is not None
            with run_log_path.open("a") as handle:
                for line in process.stdout:
                    normalized = _strip_ansi(line)
                    stdout_chunks.append(normalized)
                    handle.write(normalized)
                    handle.flush()
                    if on_log_line is not None:
                        on_log_line(normalized.rstrip("\n"), normalized.rstrip("\n"))

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                normalized = _strip_ansi(line)
                stderr_chunks.append(normalized)
                if stream_stderr_to_log:
                    with run_log_path.open("a") as handle:
                        handle.write(normalized)
                        handle.flush()
                    if on_log_line is not None:
                        on_log_line(normalized.rstrip("\n"), normalized.rstrip("\n"))

        stdout_thread = threading.Thread(target=read_stdout, name=f"{agent}-stdout", daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, name=f"{agent}-stderr", daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            try:
                returncode = process.wait(timeout=config.antigravity.timeout_seconds + 5)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                raise AdapterRunError(f"antigravity timed out for agent {agent}") from exc

            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)
            return RunResult(
                ok=returncode == 0,
                returncode=returncode,
                assistant_text=_extract_assistant_text(stdout, stderr),
                stdout=stdout,
                stderr=stderr,
                raw_events_path=str(run_log_path),
                command=command,
                resolved_model=resolved_model,
                session_id=_extract_session_id(stdout + "\n" + stderr) or session_id,
                total_tokens=_extract_total_tokens(stdout + "\n" + stderr),
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

    def _start_process(self, *, command: list[str], cwd: Path, config: AppConfig, model: str | None, agent: str) -> subprocess.Popen[str]:
        if not model:
            with self._settings_lock:
                return _popen_antigravity(command=command, cwd=cwd, agent=agent)
        self._settings_lock.acquire()
        settings_path = _resolve_settings_path(config)
        original_exists = settings_path.exists()
        original_text = settings_path.read_text() if original_exists else None
        settings = _decode_settings(original_text or "{}")
        settings["model"] = model
        _write_settings(settings_path, settings)
        try:
            process = _popen_antigravity(command=command, cwd=cwd, agent=agent)
        except Exception:
            _restore_settings_model(settings_path, original_exists=original_exists, original_text=original_text, model=model)
            self._settings_lock.release()
            raise
        self._restore_model_later(settings_path=settings_path, original_exists=original_exists, original_text=original_text, model=model)
        return process

    def _restore_model_later(self, *, settings_path: Path, original_exists: bool, original_text: str | None, model: str) -> None:
        delay = max(0.0, ANTIGRAVITY_MODEL_RESTORE_DELAY_SECONDS)
        if delay == 0:
            _restore_settings_model(settings_path, original_exists=original_exists, original_text=original_text, model=model)
            self._settings_lock.release()
            return

        def restore() -> None:
            try:
                time.sleep(delay)
                _restore_settings_model(settings_path, original_exists=original_exists, original_text=original_text, model=model)
            finally:
                self._settings_lock.release()

        threading.Thread(target=restore, name="antigravity-model-restore", daemon=False).start()


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
    if suffix == "inspector":
        return "inspector"
    if suffix == "commit":
        return "commit"
    return "planner"


def _popen_antigravity(*, command: list[str], cwd: Path, agent: str) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise AdapterRunError(f"failed to start antigravity for agent {agent}") from exc


def _bind_prompt_to_cwd(prompt: str, *, cwd: Path) -> str:
    workspace = str(cwd.expanduser().resolve())
    return "\n".join(
        [
            "Antigravity workspace binding:",
            f"- The only editable implementation repository is `{workspace}`.",
            "- Edit files in that directory in place.",
            "- Do not copy the repository to Antigravity scratch space or any other directory.",
            "- Do not satisfy this task with changes outside that editable repository.",
            "",
            prompt,
        ]
    )


def _normalize_workspace_directories(include_directories: list[Path] | None, *, cwd: Path) -> list[str]:
    resolved_cwd = cwd.expanduser().resolve()
    normalized: list[str] = []
    seen: set[str] = set()
    for directory in [resolved_cwd, *(include_directories or [])]:
        resolved_directory = directory.expanduser().resolve()
        normalized_path = str(resolved_directory)
        if normalized_path in seen:
            continue
        seen.add(normalized_path)
        normalized.append(normalized_path)
    return normalized


def _extract_assistant_text(stdout: str, stderr: str = "") -> str:
    for payload in reversed(list(_iter_json_lines(stdout))):
        text = _extract_text_from_payload(payload)
        if text:
            return text
    visible_stdout = stdout.strip()
    if visible_stdout:
        return visible_stdout
    return stderr.strip()


def _extract_text_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("content", "text", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for key in ("message", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            text = _extract_text_from_payload(nested)
            if text:
                return text
    return None


def _extract_session_id(output: str) -> str | None:
    for payload in _iter_json_lines(output):
        for key in ("conversation_id", "conversationId", "session_id", "sessionId"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        result = payload.get("result")
        if isinstance(result, dict):
            for key in ("conversation_id", "conversationId", "session_id", "sessionId"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    patterns = [
        r"--conversation(?:=|\s+)([A-Za-z0-9._:-]+)",
        r"\bconversation(?:\s+ID|\s+id)?\s*[:=]\s*([A-Za-z0-9._:-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_total_tokens(output: str) -> int:
    total = 0
    for payload in _iter_json_lines(output):
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
    result = payload.get("result")
    if isinstance(result, dict):
        return _extract_tokens_from_payload(result)
    return 0


def _iter_json_lines(output: str):
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def _resolve_settings_path(config: AppConfig) -> Path:
    if config.antigravity.settings_path is not None:
        return config.antigravity.settings_path.expanduser().resolve()
    return Path.home().joinpath(".gemini", "antigravity-cli", "settings.json")


def _read_settings_model(settings_path: Path) -> str | None:
    if not settings_path.exists():
        return None
    settings = _decode_settings(settings_path.read_text())
    model = settings.get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


def _decode_settings(content: str) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_settings(settings_path: Path, settings: dict[str, object]) -> None:
    serialized = json.dumps(settings, indent=2, sort_keys=False)
    _atomic_write_text(settings_path, serialized + "\n")


def _restore_settings_model(settings_path: Path, *, original_exists: bool, original_text: str | None, model: str) -> None:
    current = _decode_settings(settings_path.read_text() if settings_path.exists() else "{}")
    if current.get("model") != model:
        return
    if original_exists and original_text is not None:
        original = _decode_settings(original_text)
        if "model" in original:
            current["model"] = original["model"]
        else:
            current.pop("model", None)
        _write_settings(settings_path, current)
        return
    current.pop("model", None)
    if current:
        _write_settings(settings_path, current)
        return
    try:
        settings_path.unlink()
    except FileNotFoundError:
        pass


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content)
    tmp_path.replace(path)


def _deduplicate_models(values: list[str | None]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        models.append(normalized)
    return models


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value)


_ASSISTANT_ROLES: tuple[AssistantRole, ...] = ("planner", "request_draft", "plan_approval", "implementer", "reviewer", "commit")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
