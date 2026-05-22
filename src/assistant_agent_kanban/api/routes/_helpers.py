from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import cast

from fastapi import HTTPException, Request

from ...agent_materializer import ensure_runtime_agents  # noqa: F401  (re-exported for symmetry)
from ...assistant_adapter import AssistantBackendStatusSnapshot, AssistantModelSnapshot
from ...assistant_factory import build_role_adapters
from ...claude_adapter import CLAUDE_MODEL_ALIASES
from ...config import (
    ASSISTANT_ROLES,
    DEFAULT_REPO_DISCOVERY_ROOT,
    DEFAULT_SESSION_TOKEN_BUDGET,
    SUPPORTED_RUNTIME_ASSISTANTS,
    AssistantBackend,
    normalize_runtime_assistant,
)
from ...language import normalize_runtime_language
from ...omo_config import read_omo_delegation_snapshot
from ...request_draft_store import RequestDraftStore
from ...request_drafting import RequestDraftPayload as RequestDraftRoutePayload
from ._payloads import UpdateRequestDraftPayload


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
