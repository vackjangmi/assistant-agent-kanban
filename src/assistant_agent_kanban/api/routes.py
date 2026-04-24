from __future__ import annotations

from pathlib import Path
import asyncio
from collections.abc import Mapping, Sequence
from typing import cast

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from fastapi.responses import FileResponse

from ..assistant_factory import build_role_adapters
from ..agent_materializer import ensure_runtime_agents
from ..assistant_adapter import AssistantBackendStatusSnapshot, AssistantModelSnapshot
from ..claude_adapter import CLAUDE_MODEL_ALIASES
from ..config import ASSISTANT_ROLES, DEFAULT_REPO_DISCOVERY_ROOT, DEFAULT_SESSION_TOKEN_BUDGET, SUPPORTED_RUNTIME_ASSISTANTS, AssistantBackend, normalize_runtime_assistant
from ..enums import TaskState
from ..exceptions import AdapterRunError, CommitError, IntegrationError, TaskNotFoundError, TransitionError
from ..language import normalize_runtime_language
from ..omo_config import read_omo_delegation_snapshot
from ..repo_branches import describe_target_repo_branches
from ..repo_discovery import discover_target_repos
from ..language import runtime_language_code_to_request_language
from ..request_creator import (
    RequestTemplateData,
    build_default_scope_sections_for_language,
    create_request,
    delete_request_uploads,
    get_request_upload,
    save_request_upload,
    split_lines,
)
from ..request_drafting import RequestDraftPayload as RequestDraftRoutePayload, draft_request
from ..request_draft_store import RequestDraftStore, serialize_request_draft_transcript_markdown
from ..slack_settings_test import run_slack_settings_test


class CompletedGroupOverridePayload(BaseModel):
    group: str | None = None


class CreateRequestPayload(BaseModel):
    title: str
    goal: str
    request_upload_token: str | None = None
    request_draft_id: str | None = None
    request_draft_markdown: str | None = None
    background: str | None = None
    plan_auto_approve: bool = True
    scope: str | None = None
    out_of_scope: str | None = None
    constraints: str | None = None
    references: str | None = None
    acceptance_criteria: str | None = None
    target_repo: str
    base_branch: str | None = None
class UpdateMarkdownPayload(BaseModel):
    content: str


class UpdateRequestDraftPayload(BaseModel):
    title: str | None = None
    goal: str | None = None
    background: str | None = None
    plan_auto_approve: bool | None = None
    scope: str | None = None
    out_of_scope: str | None = None
    constraints: str | None = None
    references: str | None = None
    acceptance_criteria: str | None = None
    target_repo: str | None = None
    base_branch: str | None = None
    request_upload_token: str | None = None
    active_tab: Literal["assistant", "fields"] | None = None
    request_draft_input: str | None = None


class CreateRequestDraftPayload(UpdateRequestDraftPayload):
    pass


def _request_draft_store(request: Request) -> RequestDraftStore:
    return RequestDraftStore(request.app.state.runtime.config)


def _request_draft_state_from_payload(payload: UpdateRequestDraftPayload | RequestDraftRoutePayload) -> dict[str, object]:
    state: dict[str, object] = {}
    for field_name in [
        "title",
        "goal",
        "background",
        "scope",
        "out_of_scope",
        "constraints",
        "references",
        "acceptance_criteria",
        "target_repo",
        "base_branch",
        "request_upload_token",
        "active_tab",
        "request_draft_input",
    ]:
        value = getattr(payload, field_name, None)
        if value is not None:
            state[field_name] = value
    if getattr(payload, "plan_auto_approve", None) is not None:
        state["plan_auto_approve"] = payload.plan_auto_approve
    return state


class HumanVerificationPayload(BaseModel):
    note: str = ""


class HumanVerificationApprovePayload(BaseModel):
    completion_mode: Literal["new-branch", "target-branch"] = "new-branch"


class HumanReviewNotePayload(BaseModel):
    content: str = ""


class ReviewerQuestionPayload(BaseModel):
    question: str = ""


class RetrospectivePayload(BaseModel):
    target_repo_root: str
    base_branch: str
    comparison_branch: str | None = None


class RetrospectiveCreatePayload(RetrospectivePayload):
    completion_mode: Literal["new-branch", "target-branch"]


class CreateLineCommentPayload(BaseModel):
    path: str
    side: Literal["left", "right"]
    line_number: int = Field(ge=1)
    line_kind: Literal["context", "add", "remove"]
    hunk_header: str | None = None
    body: str = ""


class UpdateChangedFileViewedPayload(BaseModel):
    viewed: bool = False


class ModelSettingsPayload(BaseModel):
    class RoleBackendsPayload(BaseModel):
        planner: str | None = None
        request_draft: str | None = None
        plan_approval: str | None = None
        implementer: str | None = None
        reviewer: str | None = None
        commit: str | None = None

        @field_validator("planner", "request_draft", "plan_approval", "implementer", "reviewer", "commit", mode="before")
        @classmethod
        def normalize_role_backend(cls, value: str | None) -> str | None:
            if value is None:
                return None
            normalized = normalize_runtime_assistant(value)
            if normalized is None:
                raise ValueError("role assistant must be OpenCode, Codex CLI, Gemini CLI, or Claude Code")
            return normalized

    language: str | None = None
    theme: Literal["light", "dark"] | None = None
    coding_assistant: str | None = None
    role_backends: RoleBackendsPayload | None = None
    worker_live_logs_enabled: bool | None = None
    planner_model: str | None = None
    planner_session_token_budget: int | None = Field(default=None, ge=1)
    planner_agent_count: int | None = Field(default=None, ge=1)
    request_draft_model: str | None = None
    plan_approval_model: str | None = None
    plan_approval_session_token_budget: int | None = Field(default=None, ge=1)
    implementer_model: str | None = None
    implementer_session_token_budget: int | None = Field(default=None, ge=1)
    implementer_agent_count: int | None = Field(default=None, ge=1)
    reviewer_model: str | None = None
    reviewer_session_token_budget: int | None = Field(default=None, ge=1)
    reviewer_agent_count: int | None = Field(default=None, ge=1)
    commit_model: str | None = None
    commit_session_token_budget: int | None = Field(default=None, ge=1)
    repo_discovery_root: str | None = None
    repo_discovery_max_depth: int | None = Field(default=None, ge=1)
    slack_enabled: bool | None = None
    slack_socket_mode_enabled: bool | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_default_channel: str | None = None
    slack_app_mention_enabled: bool | None = None

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_runtime_language(value)
        if normalized is None:
            raise ValueError("language must be EN or KO")
        return normalized

    @field_validator("coding_assistant", mode="before")
    @classmethod
    def normalize_coding_assistant(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_runtime_assistant(value)
        if normalized is None:
            raise ValueError("coding assistant must be OpenCode, Codex CLI, Gemini CLI, or Claude Code")
        return normalized


class SlackSettingsTestPayload(BaseModel):
    slack_enabled: bool | None = None
    slack_socket_mode_enabled: bool | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_default_channel: str | None = None
    slack_app_mention_enabled: bool | None = None


class SlackReceiveTestStartPayload(BaseModel):
    pass


class ResumeImplementerPayload(BaseModel):
    resume_mode: Literal["pinned", "current-settings"] | None = None
    message: str | None = None


class ResumeReviewerPayload(BaseModel):
    resume_mode: Literal["pinned", "current-settings"] | None = None
    message: str | None = None


class ResumeReviewLoopPayload(BaseModel):
    message: str | None = None


class ResumePlannerPayload(BaseModel):
    message: str | None = None


def _normalize_model_override(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_repo_discovery_root(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or DEFAULT_REPO_DISCOVERY_ROOT


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_session_token_budget(value: int | None) -> int:
    if value is None:
        return DEFAULT_SESSION_TOKEN_BUDGET
    return max(1, value) * 1000


def _display_session_token_budget(value: int) -> int:
    return max(1, value // 1000)


def _normalize_agent_count(value: int | None) -> int:
    if value is None:
        return 1
    return max(1, value)


def _normalize_runtime_language(value: str | None) -> str:
    normalized = normalize_runtime_language(value)
    if normalized is None:
        raise ValueError("language must be EN or KO")
    return normalized


def _normalize_runtime_coding_assistant(value: str | None) -> str:
    normalized = normalize_runtime_assistant(value)
    if normalized is None:
            raise ValueError("coding assistant must be OpenCode, Codex CLI, Gemini CLI, or Claude Code")
    return normalized


def _apply_config_update(target, updated) -> None:
    target.kanban_root = updated.kanban_root
    target.repo_root = updated.repo_root
    target.base_branch = updated.base_branch
    target.opencode = updated.opencode
    target.codex = updated.codex
    target.gemini = updated.gemini
    target.claude = updated.claude
    target.workspace = updated.workspace
    target.locks = updated.locks
    target.runtime = updated.runtime
    target.repo_discovery = updated.repo_discovery
    target.slack = updated.slack
    target.loaded_from = updated.loaded_from
    target.loaded_local_from = updated.loaded_local_from


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "•" * len(value)
    return f"{'•' * (len(value) - 4)}{value[-4:]}"


def _settings_response(runtime, snapshots_by_backend, *, view_config=None, config_path: str | None = None, saved: bool = False) -> Mapping[str, object]:
    active_config = view_config or runtime.config
    slack_runtime_snapshot = runtime.slack_runtime.snapshot() if getattr(runtime, "slack_runtime", None) is not None else {
        "listener_enabled": False,
        "listener_connected": False,
        "listener_last_error": None,
        "last_event_at": None,
        "last_event_type": None,
        "last_event_channel": None,
        "receive_test": None,
    }
    active_backend = active_config.active_backend()
    snapshot = snapshots_by_backend[active_backend]
    available_models_by_backend = {
        backend: _settings_model_candidates(backend=backend, snapshot_models=backend_snapshot.models, config=active_config)
        for backend, backend_snapshot in snapshots_by_backend.items()
    }
    omo_snapshot = read_omo_delegation_snapshot() if active_backend == "opencode" else None
    availability_map = _resolve_availability_map(runtime, snapshots_by_backend)
    response = {
        "language": runtime.config.runtime.language,
        "theme": runtime.config.runtime.theme,
        "coding_assistant": active_backend,
        "role_backends": {role: getattr(runtime.config.runtime.role_backends, role) for role in ASSISTANT_ROLES},
        "effective_role_backends": {role: active_config.backend_for_role(role) for role in ASSISTANT_ROLES},
        "worker_live_logs_enabled": runtime.config.opencode.worker_live_logs_enabled,
        "planner_model": active_config.role_model("planner"),
        "planner_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("planner")),
        "planner_agent_count": runtime.config.runtime.planner_agent_count,
        "request_draft_model": active_config.role_model("request_draft"),
        "plan_approval_model": active_config.role_model("plan_approval"),
        "plan_approval_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("plan_approval")),
        "implementer_model": active_config.role_model("implementer"),
        "implementer_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("implementer")),
        "implementer_agent_count": runtime.config.runtime.implementer_agent_count,
        "reviewer_model": active_config.role_model("reviewer"),
        "reviewer_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("reviewer")),
        "reviewer_agent_count": runtime.config.runtime.reviewer_agent_count,
        "commit_model": active_config.role_model("commit"),
        "commit_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("commit")),
        "repo_discovery_root": runtime.config.repo_discovery_root_value(),
        "repo_discovery_max_depth": runtime.config.repo_discovery.max_depth,
        "slack_enabled": active_config.slack.enabled,
        "slack_socket_mode_enabled": active_config.slack.socket_mode_enabled,
        "slack_default_channel": active_config.slack.default_channel,
        "slack_default_channel_display": active_config.slack.default_channel_display or active_config.slack.default_channel,
        "slack_app_mention_enabled": active_config.slack.app_mention_enabled,
        "slack_bot_token_configured": active_config.slack.bot_token is not None,
        "slack_bot_token_masked": _mask_secret(active_config.slack.bot_token),
        "slack_app_token_configured": active_config.slack.app_token is not None,
        "slack_app_token_masked": _mask_secret(active_config.slack.app_token),
        "slack_runtime": slack_runtime_snapshot,
        "config_path": config_path or str(runtime.config.config_path_for_persistence()),
        "available_assistants": [
            {"value": value, "label": label}
            for value, label in SUPPORTED_RUNTIME_ASSISTANTS.items()
            if availability_map.get(value) and availability_map[value].available
        ],
        "available_models_by_backend": available_models_by_backend,
        "backend_availability_by_backend": {
            backend: {
                "available": backend_status.available,
                "error": backend_status.error,
                "checked_at": backend_status.checked_at,
            }
            for backend, backend_status in availability_map.items()
        },
        "available_models": available_models_by_backend[active_backend],
        "discovery_status": snapshot.status,
        "discovered_at": snapshot.discovered_at,
        "discovery_error": snapshot.error,
        "discovery_attempted": snapshot.attempted,
        "supports_model_discovery": snapshot.supports_model_discovery,
        "model_backend": snapshot.backend,
        "saved": saved,
    }
    if omo_snapshot is None:
        response.update(
            {
                "delegated_model_source_path": None,
                "delegated_model_status": "unsupported",
                "delegated_model_error": None,
                "delegated_models": [],
            }
        )
        return response
    response.update(
        {
            "delegated_model_source_path": str(omo_snapshot.source_path) if omo_snapshot.source_path else None,
            "delegated_model_status": omo_snapshot.status,
            "delegated_model_error": omo_snapshot.error,
            "delegated_models": [
                {
                    "key": target.key,
                    "source_type": target.source_type,
                    "model": target.model,
                    "variant": target.variant,
                }
                for target in omo_snapshot.targets
            ],
        }
    )
    return response


def _settings_model_candidates(*, backend: str, snapshot_models: list[str], config) -> list[str]:
    candidates = list(CLAUDE_MODEL_ALIASES) if backend == "claude" else list(snapshot_models)
    if backend == "claude":
        candidates.extend(_configured_claude_models(config))
    return _deduplicate_models(candidates)


def _configured_claude_models(config) -> list[str]:
    return _deduplicate_models([
        getattr(config.claude, f"{role}_model")
        for role in ASSISTANT_ROLES
    ])


def _deduplicate_models(values: Sequence[str | None]) -> list[str]:
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


def _validate_model_selection(model_name: str | None, *, backend: str, field_name: str, available_models: set[str]) -> None:
    if model_name is None:
        return
    del backend, field_name, available_models
    return


def _validate_backend_available(status: AssistantBackendStatusSnapshot, *, field_name: str) -> None:
    if status.available:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "settings.backend_unavailable",
            "field": field_name,
            "message": status.error or f"{status.backend} is unavailable",
        },
    )


def _resolve_availability_map(runtime, snapshots_by_backend) -> dict[str, AssistantBackendStatusSnapshot]:
    availability = getattr(runtime, "backend_availability", None)
    if isinstance(availability, dict):
        return availability
    return {
        backend: runtime.model_registry.peek_availability(backend)
        for backend in snapshots_by_backend
    }


def _reconfigure_runtime_adapters(runtime) -> None:
    planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter = build_role_adapters(runtime.config, adapter_registry=runtime.adapter_registry)
    plan_approval_adapter = runtime.adapter_registry.get(runtime.config.backend_for_role("plan_approval"), planner_adapter)
    runtime.planner.adapter = planner_adapter
    runtime.plan_approval.adapter = plan_approval_adapter
    runtime.implementer.adapter = implementer_adapter
    runtime.reviewer.adapter = reviewer_adapter
    runtime.committer.adapter = commit_adapter
    runtime.verification_service.branch_summary_adapter = branch_summary_adapter
    runtime.retrospective_service.adapter = commit_adapter
    runtime._task_adapters = [adapter for adapter in runtime._collect_task_adapters() if adapter is not None]


async def _resolve_settings_snapshot(runtime, *, refresh: bool, assistant: str | None):
    requested_assistant = _normalize_runtime_coding_assistant(assistant) if assistant is not None else runtime.config.active_backend()
    snapshot = await asyncio.to_thread(runtime.model_registry.get, cast(AssistantBackend, requested_assistant), refresh=refresh)
    return snapshot


async def _resolve_settings_snapshots(runtime, *, refresh: bool, assistant: str | None):
    view_config = runtime.config.model_copy(deep=True)
    if assistant is not None:
        view_config.runtime.coding_assistant = _normalize_runtime_coding_assistant(assistant)
    active_backend = _normalize_runtime_coding_assistant(assistant) if assistant is not None else runtime.config.active_backend()
    snapshots_by_backend = {}
    for backend in SUPPORTED_RUNTIME_ASSISTANTS:
        should_refresh_backend = refresh and (assistant is None or backend == assistant)
        if should_refresh_backend or backend == active_backend:
            snapshots_by_backend[backend] = await _resolve_settings_snapshot(runtime, refresh=should_refresh_backend, assistant=backend)
            continue
        snapshots_by_backend[backend] = await asyncio.to_thread(runtime.model_registry.peek, cast(AssistantBackend, backend))
    return view_config, snapshots_by_backend


async def _resolve_settings_validation_snapshots(runtime, *, config) -> dict[str, AssistantModelSnapshot]:
    referenced_backends = {config.active_backend(), *(config.backend_for_role(role) for role in ASSISTANT_ROLES)}
    snapshots_by_backend = {}
    for backend in SUPPORTED_RUNTIME_ASSISTANTS:
        if backend in referenced_backends:
            snapshots_by_backend[backend] = await asyncio.to_thread(runtime.model_registry.get, cast(AssistantBackend, backend), refresh=True)
            continue
        snapshots_by_backend[backend] = await asyncio.to_thread(runtime.model_registry.peek, cast(AssistantBackend, backend))
    return snapshots_by_backend


def _uses_builtin_runtime_adapter(runtime) -> bool:
    adapter = getattr(runtime.planner, "adapter", None)
    return adapter.__class__.__module__.startswith("assistant_agent_kanban.") if adapter is not None else False


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/board")
    async def board(request: Request):
        runtime = request.app.state.runtime
        return runtime.board_service.get_board()

    @router.get("/api/settings/models")
    async def get_model_settings(request: Request, refresh: bool = False, assistant: str | None = None) -> Mapping[str, object]:
        runtime = request.app.state.runtime
        view_config, snapshots_by_backend = await _resolve_settings_snapshots(runtime, refresh=refresh, assistant=assistant)
        return _settings_response(runtime, snapshots_by_backend, view_config=view_config)

    @router.put("/api/settings/models")
    async def update_model_settings(payload: ModelSettingsPayload, request: Request) -> Mapping[str, object]:
        runtime = request.app.state.runtime
        next_config = runtime.config.model_copy(deep=True)
        previous_backend = runtime.config.active_backend()
        previous_role_backends = runtime.config.role_backend_overrides()
        fields_set = payload.model_fields_set
        if "language" in fields_set:
            next_config.runtime.language = _normalize_runtime_language(payload.language)
        if "theme" in fields_set and payload.theme is not None:
            next_config.runtime.theme = payload.theme
        if "coding_assistant" in fields_set:
            next_config.runtime.coding_assistant = _normalize_runtime_coding_assistant(payload.coding_assistant)
        if "role_backends" in fields_set and payload.role_backends is not None:
            for role in payload.role_backends.model_fields_set:
                next_config.set_role_backend(role, getattr(payload.role_backends, role))
        if "worker_live_logs_enabled" in fields_set and payload.worker_live_logs_enabled is not None:
            next_config.opencode.worker_live_logs_enabled = payload.worker_live_logs_enabled
        if "planner_model" in fields_set:
            next_config.set_role_model("planner", _normalize_model_override(payload.planner_model))
        if "request_draft_model" in fields_set:
            next_config.set_role_model("request_draft", _normalize_model_override(payload.request_draft_model))
        if "planner_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("planner", _normalize_session_token_budget(payload.planner_session_token_budget))
        if "planner_agent_count" in fields_set:
            next_config.runtime.planner_agent_count = _normalize_agent_count(payload.planner_agent_count)
        if "plan_approval_model" in fields_set:
            next_config.set_role_model("plan_approval", _normalize_model_override(payload.plan_approval_model))
        if "plan_approval_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("plan_approval", _normalize_session_token_budget(payload.plan_approval_session_token_budget))
        if "implementer_model" in fields_set:
            next_config.set_role_model("implementer", _normalize_model_override(payload.implementer_model))
        if "implementer_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("implementer", _normalize_session_token_budget(payload.implementer_session_token_budget))
        if "implementer_agent_count" in fields_set:
            next_config.runtime.implementer_agent_count = _normalize_agent_count(payload.implementer_agent_count)
        if "reviewer_model" in fields_set:
            next_config.set_role_model("reviewer", _normalize_model_override(payload.reviewer_model))
        if "reviewer_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("reviewer", _normalize_session_token_budget(payload.reviewer_session_token_budget))
        if "reviewer_agent_count" in fields_set:
            next_config.runtime.reviewer_agent_count = _normalize_agent_count(payload.reviewer_agent_count)
        if "commit_model" in fields_set:
            next_config.set_role_model("commit", _normalize_model_override(payload.commit_model))
        if "commit_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("commit", _normalize_session_token_budget(payload.commit_session_token_budget))
        if payload.repo_discovery_root is not None:
            next_config.repo_discovery.root = _normalize_repo_discovery_root(payload.repo_discovery_root)
        if payload.repo_discovery_max_depth is not None:
            next_config.repo_discovery.max_depth = payload.repo_discovery_max_depth
        if "slack_enabled" in fields_set and payload.slack_enabled is not None:
            next_config.slack.enabled = payload.slack_enabled
        if "slack_socket_mode_enabled" in fields_set and payload.slack_socket_mode_enabled is not None:
            next_config.slack.socket_mode_enabled = payload.slack_socket_mode_enabled
        if "slack_bot_token" in fields_set:
            next_config.slack.bot_token = _normalize_optional_text(payload.slack_bot_token)
        if "slack_app_token" in fields_set:
            next_config.slack.app_token = _normalize_optional_text(payload.slack_app_token)
        if "slack_default_channel" in fields_set:
            _ = payload.slack_default_channel
        if "slack_app_mention_enabled" in fields_set and payload.slack_app_mention_enabled is not None:
            next_config.slack.app_mention_enabled = payload.slack_app_mention_enabled
        validation_snapshots = await _resolve_settings_validation_snapshots(runtime, config=next_config)
        availability_map = _resolve_availability_map(runtime, validation_snapshots)
        _validate_backend_available(availability_map[next_config.active_backend()], field_name="coding_assistant")
        for role in ASSISTANT_ROLES:
            backend = next_config.backend_for_role(role)
            field_name = f"role_backends.{role}"
            if getattr(next_config.runtime.role_backends, role) is not None:
                _validate_backend_available(availability_map[backend], field_name=field_name)
            available_models = set(validation_snapshots[next_config.backend_for_role(role)].models)
            _validate_model_selection(next_config.role_model(role), backend=backend, field_name=f"{role}_model", available_models=available_models)
        config_path = next_config.persist()
        _apply_config_update(runtime.config, next_config)
        if getattr(runtime, "slack_runtime", None) is not None:
            await runtime.slack_runtime.restart_if_running()
        if previous_backend != runtime.config.active_backend() or previous_role_backends != runtime.config.role_backend_overrides() or _uses_builtin_runtime_adapter(runtime):
            _reconfigure_runtime_adapters(runtime)
        ensure_runtime_agents(runtime.config)
        refreshed_config, snapshots_by_backend = await _resolve_settings_snapshots(runtime, refresh=False, assistant=None)
        return _settings_response(runtime, snapshots_by_backend, view_config=refreshed_config, config_path=str(config_path), saved=True)

    @router.post("/api/settings/slack-test")
    async def test_slack_settings(payload: SlackSettingsTestPayload, request: Request) -> Mapping[str, object]:
        runtime = request.app.state.runtime
        slack_config = runtime.config.slack.model_copy(deep=True)
        fields_set = payload.model_fields_set
        if "slack_enabled" in fields_set and payload.slack_enabled is not None:
            slack_config.enabled = payload.slack_enabled
        if "slack_socket_mode_enabled" in fields_set and payload.slack_socket_mode_enabled is not None:
            slack_config.socket_mode_enabled = payload.slack_socket_mode_enabled
        if "slack_bot_token" in fields_set:
            slack_config.bot_token = _normalize_optional_text(payload.slack_bot_token)
        if "slack_app_token" in fields_set:
            slack_config.app_token = _normalize_optional_text(payload.slack_app_token)
        if "slack_default_channel" in fields_set:
            slack_config.default_channel = _normalize_optional_text(payload.slack_default_channel)
        if "slack_app_mention_enabled" in fields_set and payload.slack_app_mention_enabled is not None:
            slack_config.app_mention_enabled = payload.slack_app_mention_enabled
        result = await asyncio.to_thread(run_slack_settings_test, slack_config, uses_posted_values=bool(fields_set))
        result_payload = result.to_payload()
        resolved_channel_id = getattr(result, "resolved_channel_id", None)
        if not isinstance(resolved_channel_id, str):
            resolved_channel_id = result_payload.get("resolved_channel_id") if isinstance(result_payload.get("resolved_channel_id"), str) else None
        resolved_channel_display = getattr(result, "resolved_channel_display", None)
        if not isinstance(resolved_channel_display, str):
            resolved_channel_display = result_payload.get("resolved_channel_display") if isinstance(result_payload.get("resolved_channel_display"), str) else None
        if result_payload.get("ok") and resolved_channel_id:
            next_config = runtime.config.model_copy(deep=True)
            next_config.slack.default_channel = resolved_channel_id
            next_config.slack.default_channel_display = _normalize_optional_text(payload.slack_default_channel) or resolved_channel_display or resolved_channel_id
            next_config.persist()
            _apply_config_update(runtime.config, next_config)
            if getattr(runtime, "slack_runtime", None) is not None:
                await runtime.slack_runtime.restart_if_running()
            result_payload["summary"] = f"{result_payload.get('summary', '')} Effective channel updated to {next_config.slack.default_channel_display or next_config.slack.default_channel}.".strip()
        return result_payload

    @router.post("/api/settings/slack-receive-test/start")
    async def start_slack_receive_test(_payload: SlackReceiveTestStartPayload, request: Request) -> Mapping[str, object]:
        runtime = request.app.state.runtime
        if getattr(runtime, "slack_runtime", None) is None:
            raise HTTPException(status_code=503, detail="Slack runtime is unavailable.")
        try:
            return await runtime.slack_runtime.start_receive_test()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/settings/slack-receive-test")
    async def get_slack_receive_test_status(request: Request) -> Mapping[str, object]:
        runtime = request.app.state.runtime
        if getattr(runtime, "slack_runtime", None) is None:
            raise HTTPException(status_code=503, detail="Slack runtime is unavailable.")
        return runtime.slack_runtime.snapshot()

    @router.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str, request: Request, include_changed_files: bool = False):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_task(task_id, include_changed_files=include_changed_files)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/tasks/{task_id}/logs")
    async def task_logs(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_logs(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/tasks/{task_id}/changed-files/{changed_file_id}")
    async def task_changed_file(task_id: str, changed_file_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_changed_file(task_id, changed_file_id)
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.post("/api/tasks/{task_id}/changed-files/{changed_file_id}/viewed")
    async def update_changed_file_viewed(task_id: str, changed_file_id: str, payload: UpdateChangedFileViewedPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            summary = await asyncio.to_thread(
                runtime.task_service.set_changed_file_viewed,
                task_id,
                changed_file_id,
                by="human",
                viewed=payload.viewed,
            )
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return summary

    @router.post("/api/tasks/{task_id}/changed-files/{changed_file_id}/comments")
    async def create_line_comment(task_id: str, changed_file_id: str, payload: CreateLineCommentPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            await asyncio.to_thread(
                runtime.verification_service.add_line_comment,
                task_id,
                by="human",
                path=payload.path,
                side=payload.side,
                line_number=payload.line_number,
                line_kind=payload.line_kind,
                hunk_header=payload.hunk_header,
                body_markdown=payload.body,
            )
            detail = await asyncio.to_thread(runtime.task_service.get_changed_file_by_path, task_id, payload.path)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return detail

    @router.delete("/api/tasks/{task_id}/changed-files/{changed_file_id}/comments/{comment_id}")
    async def delete_line_comment(task_id: str, changed_file_id: str, comment_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            await asyncio.to_thread(
                runtime.verification_service.delete_line_comment,
                task_id,
                by="human",
                comment_id=comment_id,
            )
            detail = await asyncio.to_thread(runtime.task_service.get_changed_file, task_id, changed_file_id)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return detail

    @router.get("/api/tasks/{task_id}/artifacts/{filename}")
    async def task_markdown_artifact(task_id: str, filename: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return {"filename": filename, "content": runtime.task_service.get_markdown_artifact(task_id, filename)}
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.put("/api/tasks/{task_id}/artifacts/{filename}")
    async def update_task_markdown_artifact(task_id: str, filename: str, payload: UpdateMarkdownPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            runtime.task_service.update_markdown_artifact(task_id, filename, payload.content, by="human")
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"saved": True, "filename": filename}

    @router.post("/api/tasks/{task_id}/attachments")
    async def upload_task_attachment(task_id: str, request: Request, artifact: str, file: UploadFile = File(...)):
        runtime = request.app.state.runtime
        data = await file.read()
        try:
            saved = runtime.task_service.save_attachment(task_id, artifact, file.filename or "image", file.content_type, data)
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return saved

    @router.get("/api/tasks/{task_id}/attachments/{filename}")
    async def task_attachment(task_id: str, filename: str, request: Request):
        runtime = request.app.state.runtime
        try:
            path, media_type = runtime.task_service.get_attachment(task_id, filename)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=media_type)

    @router.post("/api/request-uploads")
    async def upload_request_attachment(upload_token: str, request: Request, file: UploadFile = File(...)):
        runtime = request.app.state.runtime
        data = await file.read()
        try:
            saved = save_request_upload(runtime.config, upload_token, file.filename or "image", file.content_type, data)
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return saved

    @router.get("/api/request-uploads/{upload_token}/{filename}")
    async def request_attachment(upload_token: str, filename: str, request: Request):
        runtime = request.app.state.runtime
        try:
            path, media_type = get_request_upload(runtime.config, upload_token, filename)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=media_type)

    @router.delete("/api/request-uploads/{upload_token}")
    async def delete_request_attachment_uploads(upload_token: str, request: Request):
        runtime = request.app.state.runtime
        delete_request_uploads(runtime.config, upload_token)
        return {"deleted": True}

    @router.post("/api/request-drafts")
    async def draft_request_response(payload: RequestDraftRoutePayload, request: Request):
        runtime = request.app.state.runtime
        store = _request_draft_store(request)
        try:
            draft_id = (payload.request_draft_id or "").strip() if hasattr(payload, "request_draft_id") else ""
            draft = store.load(draft_id) if draft_id else store.create()
            draft = store.update(draft.draft_id, _request_draft_state_from_payload(payload))
            user_message = payload.message.strip()
            draft_for_run = draft.model_copy(update={
                "request_draft_input": "",
            })
            result = await asyncio.to_thread(
                draft_request,
                config=runtime.config,
                adapter_registry=runtime.adapter_registry,
                payload=draft_for_run.to_drafting_payload(message=user_message),
            )
            draft = store.update(draft.draft_id, {
                "request_draft_input": "",
                "transcript": [
                    *draft.transcript,
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": result.reply, "field_updates": result.field_updates},
                ],
            })
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            detail = str(exc).strip() or "request drafting failed"
            raise HTTPException(status_code=500, detail=detail) from exc
        response = result.model_dump(mode="json")
        response.update(
            {
                "request_draft_id": draft.draft_id,
                "request_upload_token": draft.request_upload_token,
                "transcript": [entry.model_dump(mode="json") for entry in draft.transcript],
            }
        )
        return response

    @router.post("/api/request-drafts/state")
    async def create_request_draft_state(payload: CreateRequestDraftPayload, request: Request):
        store = _request_draft_store(request)
        try:
            draft = store.create(_request_draft_state_from_payload(payload))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return draft.model_dump(mode="json")

    @router.get("/api/request-drafts")
    async def list_request_drafts(request: Request):
        drafts = _request_draft_store(request).list()
        return {
            "items": [
                {
                    "draft_id": draft.draft_id,
                    "title": draft.title,
                    "target_repo": draft.target_repo,
                    "base_branch": draft.base_branch,
                    "updated_at": draft.updated_at,
                    "created_at": draft.created_at,
                    "active_tab": draft.active_tab,
                    "has_transcript": bool(draft.transcript),
                    "has_unsent_input": bool((draft.request_draft_input or "").strip()),
                }
                for draft in drafts
            ]
        }

    @router.get("/api/request-drafts/{draft_id}")
    async def get_request_draft(draft_id: str, request: Request):
        try:
            draft = _request_draft_store(request).load(draft_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return draft.model_dump(mode="json")

    @router.put("/api/request-drafts/{draft_id}")
    async def update_request_draft(draft_id: str, payload: UpdateRequestDraftPayload, request: Request):
        store = _request_draft_store(request)
        try:
            draft = store.update(draft_id, _request_draft_state_from_payload(payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return draft.model_dump(mode="json")

    @router.delete("/api/request-drafts/{draft_id}")
    async def delete_request_draft(draft_id: str, request: Request):
        store = _request_draft_store(request)
        try:
            draft = store.load(draft_id)
            store.delete(draft_id)
        except FileNotFoundError:
            return {"deleted": True}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if draft.request_upload_token:
            delete_request_uploads(request.app.state.runtime.config, draft.request_upload_token)
        return {"deleted": True}

    @router.get("/api/target-repos")
    async def target_repos(request: Request):
        runtime = request.app.state.runtime
        try:
            items = discover_target_repos(runtime.config)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "root": runtime.config.repo_discovery_root_value(),
            "resolved_root": str(runtime.config.resolve_repo_discovery_root()),
            "max_depth": runtime.config.repo_discovery.max_depth,
            "items": items,
        }

    @router.get("/api/target-repo-branches")
    async def target_repo_branches(target_repo: str, request: Request):
        runtime = request.app.state.runtime
        try:
            snapshot = describe_target_repo_branches(runtime.config, Path(target_repo))
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return snapshot.model_dump(mode="json")

    @router.post("/api/requests")
    async def create_request_task(payload: CreateRequestPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            task_dir = await asyncio.to_thread(
                runtime.create_request_from_submission,
                title=payload.title,
                goal=payload.goal,
                background=payload.background,
                plan_auto_approve=payload.plan_auto_approve,
                scope=payload.scope,
                out_of_scope=payload.out_of_scope,
                constraints=payload.constraints,
                references=payload.references,
                acceptance_criteria=payload.acceptance_criteria,
                target_repo=payload.target_repo,
                base_branch=payload.base_branch,
                request_upload_token=payload.request_upload_token,
                request_draft_id=payload.request_draft_id,
                request_draft_markdown=payload.request_draft_markdown,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"task_path": str(task_dir), "created": True}

    @router.post("/api/tasks/{task_id}/approve-plan")
    async def approve_plan(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.approve_plan(task_id, by="human")
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-review-loop")
    async def resume_review_loop(task_id: str, request: Request, payload: ResumeReviewLoopPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_review_loop(
                task_id,
                by="human",
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-planner")
    async def resume_planner(task_id: str, request: Request, payload: ResumePlannerPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_planner(
                task_id,
                by="human",
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-implementer")
    async def resume_implementer(task_id: str, request: Request, payload: ResumeImplementerPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_implementer(
                task_id,
                by="human",
                resume_mode=(payload.resume_mode if payload and payload.resume_mode else "pinned"),
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-reviewer")
    async def resume_reviewer(task_id: str, request: Request, payload: ResumeReviewerPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_reviewer(
                task_id,
                by="human",
                resume_mode=(payload.resume_mode if payload and payload.resume_mode else "pinned"),
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/start-verification")
    async def start_verification(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = await asyncio.to_thread(runtime.verification_service.start, task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/retry-verification-apply")
    async def retry_verification_apply(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            context = await asyncio.to_thread(runtime.verification_service.retry_apply, task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return context.metadata

    @router.put("/api/tasks/{task_id}/human-review-note")
    async def save_human_review_note(task_id: str, payload: HumanReviewNotePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            context = await asyncio.to_thread(runtime.verification_service.save_note, task_id, by="human", content=payload.content)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {
            "saved": True,
            "task_id": context.metadata.task_id,
            "content": context.metadata.human_verification.note_markdown,
        }

    @router.post("/api/tasks/{task_id}/reviewer-qa")
    async def ask_reviewer_question(task_id: str, payload: ReviewerQuestionPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            result = await runtime.reviewer.answer_human_question_async(task_id, by="human", question=payload.question)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return result

    @router.post("/api/tasks/{task_id}/reviewer-qa-rerequest")
    async def rerequest_from_reviewer_qa(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = await asyncio.to_thread(runtime.verification_service.rerequest_from_reviewer_qa, task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/reject-verification")
    async def reject_verification(task_id: str, payload: HumanVerificationPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = await asyncio.to_thread(runtime.verification_service.reject, task_id, by="human", note=payload.note)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/approve-verification")
    async def approve_verification(
        task_id: str,
        request: Request,
        payload: HumanVerificationApprovePayload | None = None,
    ):
        runtime = request.app.state.runtime
        approval_payload = payload or HumanVerificationApprovePayload()
        try:
            moved = await asyncio.to_thread(
                runtime.verification_service.approve,
                task_id,
                by="human",
                completion_mode=approval_payload.completion_mode,
            )
        except (TransitionError, TaskNotFoundError, CommitError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.put("/api/tasks/{task_id}/completed-group")
    async def update_completed_group_override(task_id: str, payload: CompletedGroupOverridePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            updated = await asyncio.to_thread(
                runtime.task_service.update_completed_group_override,
                task_id,
                by="human",
                group=payload.group,
            )
        except (TransitionError, TaskNotFoundError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return updated.metadata

    @router.post("/api/retrospectives/inspect")
    async def inspect_retrospective(payload: RetrospectivePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            record = await asyncio.to_thread(
                runtime.retrospective_service.inspect,
                payload.target_repo_root,
                payload.base_branch,
                payload.comparison_branch,
            )
        except (TransitionError, TaskNotFoundError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @router.post("/api/retrospectives/create")
    async def create_retrospective(payload: RetrospectiveCreatePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            record = await asyncio.to_thread(
                runtime.retrospective_service.create,
                payload.target_repo_root,
                payload.base_branch,
                payload.comparison_branch,
                by="human",
                completion_mode=payload.completion_mode,
            )
        except (TransitionError, TaskNotFoundError, CommitError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return record.model_dump(mode="json")

    @router.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            await runtime.force_delete(task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"deleted": True, "task_id": task_id}

    return router
