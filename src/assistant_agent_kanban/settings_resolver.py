from __future__ import annotations

from pathlib import Path

from .config import AppConfig, ASSISTANT_ROLES, AssistantRole, normalize_runtime_assistant
from .language import normalize_runtime_language
from .user_settings_store import ProjectSettings, RuntimePreferenceSettings, UserSettingsStore


def effective_config_for_user_and_project(
    config: AppConfig,
    store: UserSettingsStore | None,
    *,
    target_repo: str | Path | None = None,
    user_id: str | None = None,
) -> AppConfig:
    effective = config.model_copy(deep=True)
    if store is None:
        return effective
    if target_repo is not None:
        project_settings = store.get_project_settings(str(target_repo))
        apply_project_settings(effective, project_settings)
        project_slack_bot_token, project_slack_app_token = store.slack_credentials_for_project(str(target_repo))
        if project_slack_bot_token:
            effective.slack.bot_token = project_slack_bot_token
        if project_slack_app_token:
            effective.slack.app_token = project_slack_app_token
    if user_id is not None:
        user_settings = store.get_user_settings(user_id)
        apply_user_runtime_preferences(effective, user_settings.runtime)
        token, username = store.git_credentials_for_user(user_id)
        if token:
            # User Git tokens are never copied into task metadata. The caller passes
            # them explicitly to the git operation that needs the secret.
            _ = username
    return effective


def apply_project_settings(config: AppConfig, settings: ProjectSettings) -> None:
    apply_runtime_preferences(config, settings.runtime)
    if settings.git_remote_name is not None:
        config.review_branch_remote.remote_name = settings.git_remote_name
    if settings.review_branch_push_enabled is not None:
        config.review_branch_remote.enabled = settings.review_branch_push_enabled
    if settings.review_branch_require_push_success is not None:
        config.review_branch_remote.require_push_success = settings.review_branch_require_push_success
    if settings.review_branch_delete_on_cleanup is not None:
        config.review_branch_remote.delete_on_cleanup = settings.review_branch_delete_on_cleanup


def apply_user_runtime_preferences(config: AppConfig, settings: RuntimePreferenceSettings) -> None:
    fields_set = settings.model_fields_set
    data: dict[str, object] = {}
    for field_name in [
        "language",
        "theme",
        "slack_default_channel",
        "slack_default_channel_display",
    ]:
        if field_name in fields_set:
            data[field_name] = getattr(settings, field_name)
    apply_runtime_preferences(config, RuntimePreferenceSettings.model_validate(data))


def apply_runtime_preferences(config: AppConfig, settings: RuntimePreferenceSettings) -> None:
    fields_set = settings.model_fields_set
    if "language" in fields_set and settings.language is not None:
        language = normalize_runtime_language(settings.language)
        if language is not None:
            config.runtime.language = language
    if "theme" in fields_set and settings.theme in {"light", "dark"}:
        config.runtime.theme = settings.theme
    if "coding_assistant" in fields_set and settings.coding_assistant is not None:
        assistant = normalize_runtime_assistant(settings.coding_assistant)
        if assistant is not None:
            config.runtime.coding_assistant = assistant
    role_fields_set = settings.role_backends.model_fields_set
    for role in ASSISTANT_ROLES:
        if role not in role_fields_set:
            continue
        value = getattr(settings.role_backends, role)
        normalized = normalize_runtime_assistant(value) if value else None
        if value is None or normalized is not None:
            config.set_role_backend(role, normalized)
    for role in ASSISTANT_ROLES:
        if f"{role}_model" not in fields_set:
            continue
        model = getattr(settings, f"{role}_model", None)
        if model is not None:
            config.set_role_model(role, model.strip() or None)
    _apply_budget(config, "planner", settings.planner_session_token_budget)
    _apply_budget(config, "plan_approval", settings.plan_approval_session_token_budget)
    _apply_budget(config, "implementer", settings.implementer_session_token_budget)
    _apply_budget(config, "reviewer", settings.reviewer_session_token_budget)
    _apply_budget(config, "commit", settings.commit_session_token_budget)
    if "slack_enabled" in fields_set and settings.slack_enabled is not None:
        config.slack.enabled = settings.slack_enabled
    if "slack_socket_mode_enabled" in fields_set and settings.slack_socket_mode_enabled is not None:
        config.slack.socket_mode_enabled = settings.slack_socket_mode_enabled
    if "slack_default_channel" in fields_set:
        config.slack.default_channel = (settings.slack_default_channel or "").strip() or None
        if "slack_default_channel_display" not in fields_set:
            config.slack.default_channel_display = None
    if "slack_default_channel_display" in fields_set:
        config.slack.default_channel_display = (settings.slack_default_channel_display or "").strip() or None
    if "slack_app_mention_enabled" in fields_set and settings.slack_app_mention_enabled is not None:
        config.slack.app_mention_enabled = settings.slack_app_mention_enabled


def _apply_budget(config: AppConfig, role: AssistantRole, value: int | None) -> None:
    if value is None:
        return
    config.set_role_session_token_budget(role, max(1, int(value)) * 1000)
