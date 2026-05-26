from __future__ import annotations

from pydantic import SecretStr

from assistant_agent_kanban.settings_resolver import effective_config_for_user_and_project
from assistant_agent_kanban.user_settings_store import ProjectSettings, RuntimePreferenceSettings, UserSecretUpdate, UserSettingsStore


def test_user_settings_store_hashes_password_and_encrypts_tokens(configured_paths):
    config, repo_root, _ = configured_paths
    store = UserSettingsStore(config)

    user = store.create_user("Alice", "correct horse battery staple", is_admin=True)
    updated = store.update_user_settings(
        user.user_id,
        RuntimePreferenceSettings(language="KO", theme="dark"),
        secrets_update=UserSecretUpdate(
            git_token="ghp_secret_token",
            git_token_username="x-access-token",
        ),
    )
    project = store.update_project_settings(
        ProjectSettings(
            repo_root=str(repo_root),
            runtime=RuntimePreferenceSettings(slack_enabled=True, slack_default_channel="C123"),
            slack_bot_token=SecretStr("xoxb-project-secret"),
        )
    )

    assert store.authenticate("alice", "correct horse battery staple") is not None
    assert updated.git_token_configured is True
    assert updated.git_token_masked is not None
    assert updated.git_token_masked.endswith("oken")
    assert store.git_credentials_for_user(user.user_id) == ("ghp_secret_token", "x-access-token")
    assert project.slack_bot_token_configured is True
    assert project.slack_bot_token_masked is not None
    assert "xoxb-project-secret" not in str(project.model_dump(mode="json"))
    assert store.slack_credentials_for_project(str(repo_root)) == ("xoxb-project-secret", None)

    effective = effective_config_for_user_and_project(config, store, target_repo=repo_root, user_id=user.user_id)
    assert effective.slack.enabled is True
    assert effective.slack.default_channel == "C123"
    assert effective.slack.bot_token == "xoxb-project-secret"

    raw_database = config.app_database_path.read_bytes()
    assert b"correct horse battery staple" not in raw_database
    assert b"ghp_secret_token" not in raw_database
    assert b"xoxb-project-secret" not in raw_database


def test_user_slack_channel_override_does_not_reuse_common_display_label(configured_paths):
    config, _, _ = configured_paths
    config.slack.default_channel = "C-common"
    config.slack.default_channel_display = "#common"
    store = UserSettingsStore(config)
    user = store.create_user("member", "member-password", is_admin=False)

    store.update_user_settings(
        user.user_id,
        RuntimePreferenceSettings(slack_default_channel="#personal"),
    )

    effective = effective_config_for_user_and_project(config, store, user_id=user.user_id)

    assert effective.slack.default_channel == "#personal"
    assert effective.slack.default_channel_display is None


def test_user_slack_channel_override_keeps_matching_resolved_display(configured_paths):
    config, _, _ = configured_paths
    config.slack.default_channel = "C-common"
    config.slack.default_channel_display = "#common"
    store = UserSettingsStore(config)
    user = store.create_user("member", "member-password", is_admin=False)

    store.update_user_settings(
        user.user_id,
        RuntimePreferenceSettings(slack_default_channel="C-user", slack_default_channel_display="#personal"),
    )

    effective = effective_config_for_user_and_project(config, store, user_id=user.user_id)

    assert effective.slack.default_channel == "C-user"
    assert effective.slack.default_channel_display == "#personal"


def test_user_settings_store_deletes_user_sessions_and_settings(configured_paths):
    config, _, _ = configured_paths
    store = UserSettingsStore(config)
    admin = store.create_user("admin", "admin-password", is_admin=True)
    member = store.create_user("member", "member-password", is_admin=False)
    token, _ = store.create_session(member)
    store.update_user_settings(
        member.user_id,
        RuntimePreferenceSettings(language="KO"),
        secrets_update=UserSecretUpdate(git_token="ghp_member_secret"),
    )

    deleted = store.delete_user(member.user_id)

    assert deleted == member
    assert store.authenticate("member", "member-password") is None
    assert store.user_for_session(token) is None
    assert store.git_credentials_for_user(member.user_id) == (None, "x-access-token")
    assert store.authenticate("admin", "admin-password") == admin


def test_user_settings_store_can_delete_all_users(configured_paths):
    config, _, _ = configured_paths
    store = UserSettingsStore(config)
    admin = store.create_user("admin", "admin-password", is_admin=True)
    member = store.create_user("member", "member-password", is_admin=False)
    admin_token, _ = store.create_session(admin)
    member_token, _ = store.create_session(member)
    store.update_user_settings(
        member.user_id,
        RuntimePreferenceSettings(language="KO"),
        secrets_update=UserSecretUpdate(git_token="ghp_member_secret"),
    )

    deleted_count = store.delete_all_users()

    assert deleted_count == 2
    assert store.user_count() == 0
    assert store.authenticate("admin", "admin-password") is None
    assert store.authenticate("member", "member-password") is None
    assert store.user_for_session(admin_token) is None
    assert store.user_for_session(member_token) is None
    assert store.git_credentials_for_user(member.user_id) == (None, "x-access-token")


def test_user_settings_store_tracks_onboarding_per_user_and_version(configured_paths):
    config, _, _ = configured_paths
    store = UserSettingsStore(config)
    alice = store.create_user("alice", "alice-password", is_admin=False)
    bob = store.create_user("bob", "bob-password", is_admin=False)

    assert store.get_user_onboarding_state(alice.user_id, current_version=1).completed is False
    assert store.get_user_onboarding_state(bob.user_id, current_version=1).completed is False

    completed = store.update_user_onboarding_state(alice.user_id, completed=True, version=1)

    assert completed.completed is True
    assert completed.completed_at is not None
    assert completed.version == 1
    assert store.get_user_onboarding_state(alice.user_id, current_version=1).completed is True
    assert store.get_user_onboarding_state(alice.user_id, current_version=2).completed is False
    assert store.get_user_onboarding_state(bob.user_id, current_version=1).completed is False


def test_user_settings_store_rejects_deleting_last_admin(configured_paths):
    config, _, _ = configured_paths
    store = UserSettingsStore(config)
    admin = store.create_user("admin", "admin-password", is_admin=True)

    try:
        store.delete_user(admin.user_id)
    except ValueError as exc:
        assert str(exc) == "cannot delete the last admin"
    else:
        raise AssertionError("expected deleting the last admin to fail")

    assert store.authenticate("admin", "admin-password") == admin
