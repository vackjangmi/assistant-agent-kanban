from __future__ import annotations

import asyncio
from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, Request

from ...agent_materializer import ensure_runtime_agents
from ...config import ASSISTANT_ROLES
from ...slack_settings_test import run_slack_settings_test
from ._helpers import (
    _apply_config_update,
    _normalize_agent_count,
    _normalize_model_override,
    _normalize_optional_text,
    _normalize_repo_discovery_root,
    _normalize_runtime_coding_assistant,
    _normalize_runtime_language,
    _normalize_session_token_budget,
    _reconfigure_runtime_adapters,
    _resolve_availability_map,
    _resolve_settings_snapshots,
    _resolve_settings_validation_snapshots,
    _settings_response,
    _uses_builtin_runtime_adapter,
    _validate_backend_available,
    _validate_model_selection,
)
from ._payloads import (
    ModelSettingsPayload,
    SlackReceiveTestStartPayload,
    SlackSettingsTestPayload,
)


def register(router: APIRouter) -> None:
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
