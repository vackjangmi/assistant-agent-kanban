from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from .enums import STATE_ORDER, TaskState
from .language import normalize_runtime_language


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DEFAULT_LOCAL_CONFIG_PATH = PROJECT_ROOT / "config.local.yaml"
DEFAULT_REPO_DISCOVERY_ROOT = "../"
DEFAULT_SESSION_TOKEN_BUDGET = 250_000
AssistantBackend = Literal["opencode", "codex"]
AssistantRole = Literal["planner", "implementer", "reviewer", "commit"]
SUPPORTED_RUNTIME_ASSISTANTS = {"opencode": "OpenCode", "codex": "Codex CLI"}


def normalize_runtime_assistant(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in SUPPORTED_RUNTIME_ASSISTANTS:
        return normalized
    if normalized == "opencode":
        return "opencode"
    return None


class OpenCodeConfig(BaseModel):
    binary: str = "opencode"
    planner_agent: str = "fs-kanban-planner"
    planner_model: str | None = None
    planner_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    implementer_agent: str = "fs-kanban-implementer"
    implementer_model: str | None = None
    implementer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    reviewer_agent: str = "fs-kanban-reviewer"
    reviewer_model: str | None = None
    reviewer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    commit_agent: str = "fs-kanban-committer"
    commit_model: str | None = None
    commit_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    timeout_seconds: int = 1800


class CodexConfig(BaseModel):
    binary: str = "codex"
    planner_model: str | None = None
    planner_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    implementer_model: str | None = None
    implementer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    reviewer_model: str | None = None
    reviewer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    commit_model: str | None = None
    commit_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    timeout_seconds: int = 1800


class WorkspaceConfig(BaseModel):
    strategy: str = "clone-overlay"
    root: Path | None = None
    overlay_copy: list[str] = Field(default_factory=list)
    overlay_symlink: list[str] = Field(default_factory=list)


class LocksConfig(BaseModel):
    heartbeat_seconds: int = 10
    stale_after_seconds: int = 60
    timeout_seconds: int = 5


class RuntimeConfig(BaseModel):
    poll_interval_seconds: float = 0.2
    auto_dispatch: bool = True
    language: Literal["EN", "KO"] = "EN"
    theme: Literal["light", "dark"] = "light"
    coding_assistant: AssistantBackend = "opencode"
    planner_agent_count: int = Field(default=1, ge=1)
    implementer_agent_count: int = Field(default=1, ge=1)
    reviewer_agent_count: int = Field(default=1, ge=1)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language_setting(cls, value: str) -> str:
        normalized = normalize_runtime_language(value)
        if normalized is None:
            raise ValueError("runtime language must be EN or KO")
        return normalized

    @field_validator("coding_assistant", mode="before")
    @classmethod
    def normalize_coding_assistant_setting(cls, value: str) -> str:
        normalized = normalize_runtime_assistant(value)
        if normalized is None:
            raise ValueError("runtime coding assistant must be OpenCode or Codex CLI")
        return normalized


class RepoDiscoveryConfig(BaseModel):
    root: str | Path | None = DEFAULT_REPO_DISCOVERY_ROOT
    max_depth: int = 2


class AppConfig(BaseModel):
    kanban_root: Path = Path("./.kanban-agent")
    repo_root: Path = Path(".")
    base_branch: str = "main"
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    locks: LocksConfig = Field(default_factory=LocksConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    repo_discovery: RepoDiscoveryConfig = Field(default_factory=RepoDiscoveryConfig)
    loaded_from: Path | None = Field(default=None, exclude=True)
    loaded_local_from: Path | None = Field(default=None, exclude=True)

    def bootstrap(self) -> None:
        self.kanban_root.mkdir(parents=True, exist_ok=True)
        for state in STATE_ORDER:
            self.state_dir(state).mkdir(parents=True, exist_ok=True)
        for relative in [
            "_runtime/locks",
            "_runtime/workspaces",
            "_runtime/runs",
            "_runtime/archive-runs",
            "_runtime/events",
            "_runtime/board-cache",
            "retrospectives",
        ]:
            (self.kanban_root / relative).mkdir(parents=True, exist_ok=True)
        if self.workspace.root is None:
            self.workspace.root = self.kanban_root / "_runtime/workspaces"
        if self.repo_discovery.root is None:
            self.repo_discovery.root = DEFAULT_REPO_DISCOVERY_ROOT

    def repo_discovery_root_value(self) -> str:
        return str(self.repo_discovery.root or DEFAULT_REPO_DISCOVERY_ROOT)

    def resolve_repo_discovery_root(self) -> Path:
        configured_root = Path(self.repo_discovery_root_value()).expanduser()
        if configured_root.is_absolute():
            return configured_root.resolve()
        anchor = self.loaded_from.parent if self.loaded_from is not None else PROJECT_ROOT
        return (anchor / configured_root).resolve()

    def state_dir(self, state: TaskState) -> Path:
        return self.kanban_root / state.value

    def active_backend(self) -> AssistantBackend:
        return self.runtime.coding_assistant

    def backend_config(self):
        return self.opencode if self.active_backend() == "opencode" else self.codex

    def role_agent(self, role: AssistantRole) -> str:
        if self.active_backend() == "opencode":
            return getattr(self.opencode, f"{role}_agent")
        return f"fs-kanban-{role}"

    def role_model(self, role: AssistantRole) -> str | None:
        return getattr(self.backend_config(), f"{role}_model")

    def set_role_model(self, role: AssistantRole, value: str | None) -> None:
        setattr(self.backend_config(), f"{role}_model", value)

    def role_session_token_budget(self, role: AssistantRole) -> int:
        return getattr(self.backend_config(), f"{role}_session_token_budget")

    def set_role_session_token_budget(self, role: AssistantRole, value: int) -> None:
        setattr(self.backend_config(), f"{role}_session_token_budget", value)

    def backend_timeout_seconds(self) -> int:
        return int(self.backend_config().timeout_seconds)

    def config_path_for_persistence(self) -> Path:
        if self.loaded_local_from is not None:
            return self.loaded_local_from
        if self.loaded_from is not None:
            return self.loaded_from.with_name("config.local.yaml")
        return DEFAULT_LOCAL_CONFIG_PATH

    def persist(self, path: Path | None = None) -> Path:
        target_path = (path or self.config_path_for_persistence()).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump(mode="json", exclude={"loaded_from"})
        serialized = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
        tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        tmp_path.write_text(serialized)
        tmp_path.replace(target_path)
        self.loaded_from = target_path
        return target_path

    @property
    def locks_dir(self) -> Path:
        return self.kanban_root / "_runtime/locks"

    @property
    def runs_dir(self) -> Path:
        return self.kanban_root / "_runtime/runs"

    @property
    def archive_runs_dir(self) -> Path:
        return self.kanban_root / "_runtime/archive-runs"

    @property
    def events_dir(self) -> Path:
        return self.kanban_root / "_runtime/events"

    @property
    def retrospectives_dir(self) -> Path:
        return self.kanban_root / "retrospectives"


def load_config(path: str | Path | None = None) -> AppConfig:
    loaded_from: Path | None = None
    loaded_local_from: Path | None = None
    raw: dict[str, Any] = {}
    if path is None:
        if DEFAULT_CONFIG_PATH.exists():
            loaded_from = DEFAULT_CONFIG_PATH.expanduser().resolve()
            raw = _merge_dicts(raw, _read_yaml_dict(loaded_from))
        if DEFAULT_LOCAL_CONFIG_PATH.exists():
            loaded_local_from = DEFAULT_LOCAL_CONFIG_PATH.expanduser().resolve()
            raw = _merge_dicts(raw, _read_yaml_dict(loaded_local_from))
    else:
        resolved_path = Path(path).expanduser().resolve()
        loaded_from = resolved_path
        raw = _merge_dicts(raw, _read_yaml_dict(resolved_path))
        sibling_local = resolved_path.with_name("config.local.yaml")
        if resolved_path.name != "config.local.yaml" and sibling_local.exists():
            loaded_local_from = sibling_local.resolve()
            raw = _merge_dicts(raw, _read_yaml_dict(loaded_local_from))
    if not raw and loaded_from is None and loaded_local_from is None:
        config = AppConfig()
    else:
        config = AppConfig.model_validate(raw)
        config.loaded_from = loaded_from
        config.loaded_local_from = loaded_local_from
    config.bootstrap()
    return config


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file must contain a mapping: {path}")
    return data


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
