from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from typing import Callable, cast

from pathlib import Path

from .config import AppConfig, AssistantBackend
from .exceptions import AdapterRunError
from .models import RunResult


class AssistantAdapter:
    supports_model_discovery = True

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
        raise NotImplementedError

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        return []

    def availability_error(self, *, config: AppConfig, backend: AssistantBackend) -> str | None:
        return None

    def cancel_task(self, task_id: str) -> None:
        return None


@dataclass(slots=True)
class AssistantModelSnapshot:
    models: list[str]
    discovered_at: str | None
    error: str | None
    attempted: bool
    backend: str
    supports_model_discovery: bool

    @property
    def status(self) -> str:
        if not self.supports_model_discovery:
            return "unsupported"
        if self.error and self.models:
            return "fallback"
        if self.error:
            return "error"
        if self.models:
            return "ready"
        if self.attempted:
            return "empty"
        return "idle"


@dataclass(slots=True)
class AssistantBackendStatusSnapshot:
    backend: AssistantBackend
    available: bool
    error: str | None
    checked_at: str | None


class AssistantBackendState:
    def __init__(self, *, backend: AssistantBackend, adapter: AssistantAdapter, config: AppConfig) -> None:
        self.backend = backend
        self.adapter = adapter
        self.config = config
        self._lock = threading.Lock()
        self._models: list[str] = []
        self._discovered_at: str | None = None
        self._discovery_error: str | None = None
        self._attempted = False
        self._available = False
        self._availability_error: str | None = None
        self._checked_at: str | None = None

    def availability(self, *, refresh: bool = False) -> AssistantBackendStatusSnapshot:
        if refresh:
            return self.refresh_availability()
        with self._lock:
            if self._checked_at is not None:
                return AssistantBackendStatusSnapshot(
                    backend=cast(AssistantBackend, self.backend),
                    available=self._available,
                    error=self._availability_error,
                    checked_at=self._checked_at,
                )
        return self.refresh_availability()

    def refresh_availability(self) -> AssistantBackendStatusSnapshot:
        error = self.adapter.availability_error(config=self.config, backend=cast(AssistantBackend, self.backend))
        checked_at = _utc_timestamp()
        with self._lock:
            self._available = error is None
            self._availability_error = error
            self._checked_at = checked_at
        return AssistantBackendStatusSnapshot(
            backend=cast(AssistantBackend, self.backend),
            available=error is None,
            error=error,
            checked_at=checked_at,
        )

    def snapshot(self) -> AssistantModelSnapshot:
        with self._lock:
            return AssistantModelSnapshot(
                models=list(self._models),
                discovered_at=self._discovered_at,
                error=self._discovery_error,
                attempted=self._attempted,
                backend=self.backend,
                supports_model_discovery=self.adapter.supports_model_discovery,
            )

    def availability_snapshot(self) -> AssistantBackendStatusSnapshot:
        with self._lock:
            return AssistantBackendStatusSnapshot(
                backend=cast(AssistantBackend, self.backend),
                available=self._available,
                error=self._availability_error,
                checked_at=self._checked_at,
            )

    def get(self, *, refresh: bool = False) -> AssistantModelSnapshot:
        if refresh:
            return self.refresh(refresh_cli=True)
        snapshot = self.snapshot()
        if snapshot.attempted:
            return snapshot
        return self.refresh(refresh_cli=False)

    def refresh(self, *, refresh_cli: bool) -> AssistantModelSnapshot:
        availability = self.availability(refresh=False)
        if not availability.available:
            with self._lock:
                self._attempted = True
                self._discovery_error = availability.error
            return self.snapshot()
        if not self.adapter.supports_model_discovery:
            with self._lock:
                self._attempted = True
                self._models = []
                self._discovery_error = None
            return self.snapshot()
        try:
            models = self.adapter.discover_models(config=self.config, refresh=refresh_cli)
        except AdapterRunError as exc:
            with self._lock:
                self._attempted = True
                self._discovery_error = str(exc)
            return self.snapshot()
        with self._lock:
            self._attempted = True
            self._models = models
            self._discovered_at = _utc_timestamp()
            self._discovery_error = None
        return self.snapshot()


class AssistantModelRegistry:
    def __init__(self, *, adapter: AssistantAdapter, config: AppConfig) -> None:
        self.adapter = adapter
        self.config = config
        self._lock = threading.Lock()
        self._models: list[str] = []
        self._discovered_at: str | None = None
        self._error: str | None = None
        self._attempted = False

    def snapshot(self) -> AssistantModelSnapshot:
        with self._lock:
            return AssistantModelSnapshot(
                models=list(self._models),
                discovered_at=self._discovered_at,
                error=self._error,
                attempted=self._attempted,
                backend=self.config.active_backend(),
                supports_model_discovery=self.adapter.supports_model_discovery,
            )

    def get(self, *, refresh: bool = False) -> AssistantModelSnapshot:
        if refresh:
            return self.refresh(refresh_cli=True)
        snapshot = self.snapshot()
        if snapshot.attempted:
            return snapshot
        return self.refresh(refresh_cli=False)

    def refresh(self, *, refresh_cli: bool) -> AssistantModelSnapshot:
        if not self.adapter.supports_model_discovery:
            with self._lock:
                self._attempted = True
                self._models = []
                self._error = None
            return self.snapshot()
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


class AssistantBackendManager:
    def __init__(self, *, states: dict[AssistantBackend, AssistantBackendState], config: AppConfig) -> None:
        self.states = states
        self.config = config

    def get(self, backend: AssistantBackend, *, refresh: bool = False) -> AssistantModelSnapshot:
        return self.states[backend].get(refresh=refresh)

    def peek(self, backend: AssistantBackend) -> AssistantModelSnapshot:
        return self.states[backend].snapshot()

    def availability(self, backend: AssistantBackend, *, refresh: bool = False) -> AssistantBackendStatusSnapshot:
        return self.states[backend].availability(refresh=refresh)

    def peek_availability(self, backend: AssistantBackend) -> AssistantBackendStatusSnapshot:
        return self.states[backend].availability_snapshot()

    def all(self, *, refresh: bool = False) -> dict[AssistantBackend, AssistantModelSnapshot]:
        return {backend: state.get(refresh=refresh) for backend, state in self.states.items()}

    def all_availability(self, *, refresh: bool = False) -> dict[AssistantBackend, AssistantBackendStatusSnapshot]:
        return {backend: state.availability(refresh=refresh) for backend, state in self.states.items()}

    def warm_availability(self) -> dict[AssistantBackend, AssistantBackendStatusSnapshot]:
        return self.all_availability(refresh=False)

    def warm(self) -> None:
        for backend in self.states:
            resolved_backend = cast(AssistantBackend, backend)
            self.availability(resolved_backend)
            self.get(resolved_backend)


def build_backend_manager(*, config: AppConfig, adapter_registry: dict[AssistantBackend, AssistantAdapter]) -> AssistantBackendManager:
    return AssistantBackendManager(
        states={
            cast(AssistantBackend, backend): AssistantBackendState(backend=cast(AssistantBackend, backend), adapter=adapter, config=config)
            for backend, adapter in adapter_registry.items()
        },
        config=config,
    )


def _backend_binary(config: AppConfig, backend: AssistantBackend) -> str:
    if backend == "antigravity":
        return config.antigravity.binary
    if backend == "opencode":
        return config.opencode.binary
    if backend == "codex":
        return config.codex.binary
    if backend == "gemini":
        return config.gemini.binary
    return config.claude.binary


def _resolve_binary_error(binary: str) -> str | None:
    normalized = (binary or "").strip()
    if not normalized:
        return "binary is not configured"
    binary_path = Path(normalized).expanduser()
    if binary_path.is_absolute() or "/" in normalized:
        if not binary_path.exists():
            return f"binary not found: {normalized}"
        if not binary_path.is_file():
            return f"binary is not a file: {normalized}"
        if not _is_executable(binary_path):
            return f"binary is not executable: {normalized}"
        return None
    resolved = shutil.which(normalized)
    if resolved is None:
        return f"binary not found on PATH: {normalized}"
    return None


def _is_executable(path: Path) -> bool:
    try:
        return path.stat().st_mode & 0o111 != 0
    except OSError:
        return False


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
