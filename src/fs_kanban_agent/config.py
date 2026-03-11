from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .enums import STATE_ORDER, TaskState


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_CONFIG_PATH = PROJECT_ROOT / "config.local.yaml"
DEFAULT_REPO_DISCOVERY_ROOT = "../"


class OpenCodeConfig(BaseModel):
    binary: str = "opencode"
    attach_url: str | None = None
    planner_agent: str = "fs-kanban-planner"
    planner_model: str | None = None
    implementer_agent: str = "fs-kanban-implementer"
    implementer_model: str | None = None
    reviewer_agent: str = "fs-kanban-reviewer"
    reviewer_model: str | None = None
    commit_agent: str = "fs-kanban-committer"
    commit_model: str | None = None
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


class RepoDiscoveryConfig(BaseModel):
    root: str | Path | None = DEFAULT_REPO_DISCOVERY_ROOT
    max_depth: int = 2


class AppConfig(BaseModel):
    kanban_root: Path = Path("./ai-kanban")
    repo_root: Path = Path(".")
    base_branch: str = "main"
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    locks: LocksConfig = Field(default_factory=LocksConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    repo_discovery: RepoDiscoveryConfig = Field(default_factory=RepoDiscoveryConfig)
    loaded_from: Path | None = Field(default=None, exclude=True)

    def bootstrap(self) -> None:
        self.kanban_root.mkdir(parents=True, exist_ok=True)
        for state in STATE_ORDER:
            self.state_dir(state).mkdir(parents=True, exist_ok=True)
        for relative in [
            "_runtime/locks",
            "_runtime/workspaces",
            "_runtime/runs",
            "_runtime/events",
            "_runtime/board-cache",
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

    def config_path_for_persistence(self) -> Path:
        if self.loaded_from is not None:
            return self.loaded_from
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
    def events_dir(self) -> Path:
        return self.kanban_root / "_runtime/events"


def load_config(path: str | Path | None = None) -> AppConfig:
    loaded_from: Path | None = None
    if path is None and DEFAULT_LOCAL_CONFIG_PATH.exists():
        loaded_from = DEFAULT_LOCAL_CONFIG_PATH
    elif path is not None:
        loaded_from = Path(path)
    if loaded_from is None:
        config = AppConfig()
    else:
        resolved_path = loaded_from.expanduser().resolve()
        raw = yaml.safe_load(resolved_path.read_text()) or {}
        config = AppConfig.model_validate(raw)
        config.loaded_from = resolved_path
    config.bootstrap()
    return config
