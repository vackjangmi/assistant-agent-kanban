from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from pathlib import Path

from .config import AppConfig
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
        session_id: str | None = None,
        cancel_key: str | None = None,
        on_log_line: Callable[[str, str | None], None] | None = None,
    ) -> RunResult:
        raise NotImplementedError

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        return []

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


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
