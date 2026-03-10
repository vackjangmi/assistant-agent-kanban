from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .enums import STATE_ORDER, TaskState


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    root: Path | None = None
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
            self.repo_discovery.root = PROJECT_ROOT.parent

    def state_dir(self, state: TaskState) -> Path:
        return self.kanban_root / state.value

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
    if path is None:
        config = AppConfig()
    else:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        config = AppConfig.model_validate(raw)
    config.bootstrap()
    return config
