from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from .enums import STATE_ORDER, TaskState
from .language import normalize_runtime_language
from .models import TaskRuntimePin, TaskRuntimeRoleBackends


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DEFAULT_LOCAL_CONFIG_PATH = PROJECT_ROOT / "config.local.yaml"
DEFAULT_REPO_DISCOVERY_ROOT = "../"
DEFAULT_SESSION_TOKEN_BUDGET = 250_000
DEFAULT_TARGET_REPO_DOCS_ROOT = "docs/kanban-agent"
AssistantBackend = Literal["opencode", "codex", "gemini", "claude", "antigravity"]
AssistantRole = Literal["planner", "request_draft", "plan_approval", "implementer", "reviewer", "commit"]
ASSISTANT_ROLES: tuple[AssistantRole, ...] = ("planner", "request_draft", "plan_approval", "implementer", "reviewer", "commit")
TASK_RUNTIME_ASSISTANT_ROLES: tuple[AssistantRole, ...] = ("planner", "plan_approval", "implementer", "reviewer", "commit")
SUPPORTED_RUNTIME_ASSISTANTS = {
    "antigravity": "Antigravity CLI",
    "codex": "Codex CLI",
    "claude": "Claude Code",
    "gemini": "Gemini CLI",
    "opencode": "OpenCode",
}


def normalize_runtime_assistant(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in SUPPORTED_RUNTIME_ASSISTANTS:
        return normalized
    if normalized in {"opencode", "gemini", "claude", "agy"}:
        if normalized == "agy":
            return "antigravity"
        return normalized
    return None


class OpenCodeConfig(BaseModel):
    binary: str = "opencode"
    worker_live_logs_enabled: bool = False
    planner_agent: str = "fs-kanban-planner"
    planner_model: str | None = None
    planner_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    request_draft_agent: str = "fs-kanban-request-draft"
    request_draft_model: str | None = None
    plan_approval_agent: str = "fs-kanban-plan-approval"
    plan_approval_model: str | None = None
    plan_approval_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
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
    request_draft_model: str | None = None
    plan_approval_model: str | None = None
    plan_approval_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    implementer_model: str | None = None
    implementer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    reviewer_model: str | None = None
    reviewer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    commit_model: str | None = None
    commit_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    timeout_seconds: int = 1800


class GeminiConfig(BaseModel):
    binary: str = "gemini"
    planner_model: str | None = None
    planner_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    request_draft_model: str | None = None
    plan_approval_model: str | None = None
    plan_approval_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    implementer_model: str | None = None
    implementer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    reviewer_model: str | None = None
    reviewer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    commit_model: str | None = None
    commit_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    timeout_seconds: int = 1800


class ClaudeConfig(BaseModel):
    binary: str = "claude"
    planner_model: str | None = None
    planner_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    request_draft_model: str | None = None
    plan_approval_model: str | None = None
    plan_approval_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    implementer_model: str | None = None
    implementer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    reviewer_model: str | None = None
    reviewer_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    commit_model: str | None = None
    commit_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    timeout_seconds: int = 1800


class AntigravityConfig(BaseModel):
    binary: str = "agy"
    settings_path: Path | None = None
    dangerously_skip_permissions: bool = True
    sandbox: bool = False
    planner_model: str | None = None
    planner_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
    request_draft_model: str | None = None
    plan_approval_model: str | None = None
    plan_approval_session_token_budget: int = Field(default=DEFAULT_SESSION_TOKEN_BUDGET, ge=1)
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
    class RoleBackends(BaseModel):
        planner: AssistantBackend | None = None
        request_draft: AssistantBackend | None = None
        plan_approval: AssistantBackend | None = None
        implementer: AssistantBackend | None = None
        reviewer: AssistantBackend | None = None
        commit: AssistantBackend | None = None

    poll_interval_seconds: float = 0.2
    auto_dispatch: bool = True
    language: Literal["EN", "KO"] = "EN"
    theme: Literal["light", "dark"] = "light"
    coding_assistant: AssistantBackend = "opencode"
    role_backends: RoleBackends = Field(default_factory=RoleBackends)
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
            raise ValueError("runtime coding assistant must be OpenCode, Codex CLI, Gemini CLI, Claude Code, or Antigravity CLI")
        return normalized


class RepoDiscoveryConfig(BaseModel):
    root: str | Path | None = DEFAULT_REPO_DISCOVERY_ROOT
    max_depth: int = 2


class SlackConfig(BaseModel):
    enabled: bool = False
    socket_mode_enabled: bool = True
    bot_token: str | None = None
    app_token: str | None = None
    bot_name: str | None = None
    default_channel: str | None = None
    default_channel_display: str | None = None
    app_mention_enabled: bool = False

    @field_validator("bot_token", "app_token", "bot_name", "default_channel", "default_channel_display", mode="before")
    @classmethod
    def normalize_optional_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AuthConfig(BaseModel):
    enabled: bool = False
    database_path: Path | None = None
    encryption_key_path: Path | None = None
    session_cookie_name: str = "assistant_agent_kanban_session"
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=60)
    require_admin_for_common_settings: bool = True


class ReviewBranchRemoteConfig(BaseModel):
    enabled: bool = False
    remote_name: str = "origin"
    require_push_success: bool = True
    delete_on_cleanup: bool = True

    @field_validator("remote_name", mode="before")
    @classmethod
    def normalize_remote_name(cls, value: str | None) -> str:
        normalized = (value or "origin").strip()
        return normalized or "origin"


class AppConfig(BaseModel):
    kanban_root: Path = Path("./.kanban-agent")
    repo_root: Path = Path(".")
    base_branch: str = "main"
    target_repo_docs_root: str = DEFAULT_TARGET_REPO_DOCS_ROOT
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    antigravity: AntigravityConfig = Field(default_factory=AntigravityConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    locks: LocksConfig = Field(default_factory=LocksConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    repo_discovery: RepoDiscoveryConfig = Field(default_factory=RepoDiscoveryConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    review_branch_remote: ReviewBranchRemoteConfig = Field(default_factory=ReviewBranchRemoteConfig)
    loaded_from: Path | None = Field(default=None, exclude=True)
    loaded_local_from: Path | None = Field(default=None, exclude=True)

    def bootstrap(self) -> None:
        self.kanban_root.mkdir(parents=True, exist_ok=True)
        for state in STATE_ORDER:
            self.state_dir(state).mkdir(parents=True, exist_ok=True)
        for relative in [
            "_runtime/locks",
            "_runtime/workspaces",
            "_runtime/human-verifications",
            "_runtime/runs",
            "_runtime/archive-runs",
            "_runtime/request-drafts",
            "_runtime/request-uploads",
            "_runtime/events",
            "_runtime/board-cache",
            "_runtime/secrets",
            "retrospectives",
        ]:
            (self.kanban_root / relative).mkdir(parents=True, exist_ok=True)
        if self.workspace.root is None:
            self.workspace.root = self.kanban_root / "_runtime/workspaces"
        if self.repo_discovery.root is None:
            self.repo_discovery.root = DEFAULT_REPO_DISCOVERY_ROOT
        if self.auth.database_path is None:
            self.auth.database_path = self.kanban_root / "_runtime/app.db"
        if self.auth.encryption_key_path is None:
            self.auth.encryption_key_path = self.kanban_root / "_runtime/secrets/settings.key"

    def repo_discovery_root_value(self) -> str:
        return str(self.repo_discovery.root or DEFAULT_REPO_DISCOVERY_ROOT)

    def resolve_repo_discovery_root(self) -> Path:
        configured_root = Path(self.repo_discovery_root_value()).expanduser()
        if configured_root.is_absolute():
            return configured_root.resolve()
        anchor = self.loaded_from.parent if self.loaded_from is not None else PROJECT_ROOT
        return (anchor / configured_root).resolve()

    def target_repo_docs_root_value(self) -> str:
        value = self.target_repo_docs_root.strip()
        return value or DEFAULT_TARGET_REPO_DOCS_ROOT

    def resolve_target_repo_docs_root(self, target_repo_root: Path) -> Path:
        configured_root = Path(self.target_repo_docs_root_value())
        if configured_root.is_absolute():
            raise ValueError("target repo docs root must be a relative path")
        resolved_repo_root = target_repo_root.expanduser().resolve()
        resolved_docs_root = (resolved_repo_root / configured_root).resolve()
        try:
            resolved_docs_root.relative_to(resolved_repo_root)
        except ValueError as exc:
            raise ValueError("target repo docs root must stay inside the target repository") from exc
        return resolved_docs_root

    def state_dir(self, state: TaskState) -> Path:
        return self.kanban_root / state.value

    def active_backend(self) -> AssistantBackend:
        return self.runtime.coding_assistant

    def backend_for_role(self, role: AssistantRole) -> AssistantBackend:
        override = getattr(self.runtime.role_backends, role)
        return override or self.active_backend()

    def role_backend_overrides(self) -> dict[AssistantRole, AssistantBackend | None]:
        return {role: getattr(self.runtime.role_backends, role) for role in ASSISTANT_ROLES}

    def set_role_backend(self, role: AssistantRole, value: AssistantBackend | None) -> None:
        setattr(self.runtime.role_backends, role, value)

    def backend_config(self, *, backend: AssistantBackend | None = None, role: AssistantRole | None = None):
        resolved_backend = backend or (self.backend_for_role(role) if role is not None else self.active_backend())
        if resolved_backend == "opencode":
            return self.opencode
        if resolved_backend == "codex":
            return self.codex
        if resolved_backend == "gemini":
            return self.gemini
        if resolved_backend == "claude":
            return self.claude
        if resolved_backend == "antigravity":
            return self.antigravity
        return self.gemini

    def role_agent(self, role: AssistantRole) -> str:
        if self.backend_for_role(role) == "opencode":
            return getattr(self.opencode, f"{role}_agent")
        return f"fs-kanban-{role.replace('_', '-')}"

    def role_model(self, role: AssistantRole) -> str | None:
        return getattr(self.backend_config(role=role), f"{role}_model")

    def set_role_model(self, role: AssistantRole, value: str | None) -> None:
        setattr(self.backend_config(role=role), f"{role}_model", value)

    def role_session_token_budget(self, role: AssistantRole) -> int:
        return getattr(self.backend_config(role=role), f"{role}_session_token_budget")

    def set_role_session_token_budget(self, role: AssistantRole, value: int) -> None:
        setattr(self.backend_config(role=role), f"{role}_session_token_budget", value)

    def backend_timeout_seconds(self, *, role: AssistantRole | None = None) -> int:
        return int(self.backend_config(role=role).timeout_seconds)

    def capture_runtime_pin(self, *, captured_by: str) -> TaskRuntimePin:
        return TaskRuntimePin(
            backend=self.active_backend(),
            captured_by=captured_by,
            role_backends=TaskRuntimeRoleBackends(
                planner=self.runtime.role_backends.planner,
                plan_approval=self.runtime.role_backends.plan_approval,
                implementer=self.runtime.role_backends.implementer,
                reviewer=self.runtime.role_backends.reviewer,
                commit=self.runtime.role_backends.commit,
            ),
            planner_model=self.role_model("planner"),
            plan_approval_model=self.role_model("plan_approval"),
            implementer_model=self.role_model("implementer"),
            reviewer_model=self.role_model("reviewer"),
            commit_model=self.role_model("commit"),
        )

    def with_runtime_pin(self, runtime_pin: TaskRuntimePin | None) -> AppConfig:
        if runtime_pin is None:
            return self.model_copy(deep=True)
        pinned = self.model_copy(deep=True)
        pinned.runtime.coding_assistant = runtime_pin.backend
        for role in TASK_RUNTIME_ASSISTANT_ROLES:
            pinned.set_role_backend(role, getattr(runtime_pin.role_backends, role))
        pinned.set_role_model("planner", runtime_pin.planner_model)
        pinned.set_role_model("plan_approval", runtime_pin.plan_approval_model)
        pinned.set_role_model("implementer", runtime_pin.implementer_model)
        pinned.set_role_model("reviewer", runtime_pin.reviewer_model)
        pinned.set_role_model("commit", runtime_pin.commit_model)
        return pinned

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
    def human_verifications_dir(self) -> Path:
        return self.kanban_root / "_runtime/human-verifications"

    @property
    def request_uploads_dir(self) -> Path:
        return self.kanban_root / "_runtime/request-uploads"

    @property
    def request_drafts_dir(self) -> Path:
        return self.kanban_root / "_runtime/request-drafts"

    @property
    def app_database_path(self) -> Path:
        return self.auth.database_path or (self.kanban_root / "_runtime/app.db")

    @property
    def encryption_key_path(self) -> Path:
        return self.auth.encryption_key_path or (self.kanban_root / "_runtime/secrets/settings.key")

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
    _normalize_loaded_root_paths(config)
    config.bootstrap()
    return config


def _normalize_loaded_root_paths(config: AppConfig) -> None:
    anchor = None
    if config.loaded_local_from is not None:
        anchor = config.loaded_local_from.parent
    elif config.loaded_from is not None:
        anchor = config.loaded_from.parent
    if anchor is None:
        return
    config.kanban_root = _resolve_config_root_path(config.kanban_root, anchor)
    config.repo_root = _resolve_config_root_path(config.repo_root, anchor)
    if config.workspace.root is not None:
        config.workspace.root = _resolve_config_root_path(config.workspace.root, anchor)


def _resolve_config_root_path(path: Path, anchor: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (anchor / expanded).resolve()


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
