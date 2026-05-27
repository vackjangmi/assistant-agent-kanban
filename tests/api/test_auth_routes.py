from __future__ import annotations

from fastapi.testclient import TestClient

from assistant_agent_kanban.api.app import create_app

from ..conftest import FakeAdapter


def test_existing_admin_account_requires_login_even_when_auth_config_is_disabled(configured_paths):
    config, repo_root, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)

    with TestClient(app) as client:
        blocked = client.get("/api/settings/models")
        assert blocked.status_code == 401

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["enabled"] is True
        assert me.json()["authenticated"] is False
        assert me.json()["bootstrap_required"] is False

        login = client.post("/api/auth/login", json={"username": "admin", "password": "secret-password"})
        assert login.status_code == 200
        assert login.json()["is_admin"] is True

        settings = client.get("/api/settings/models")
        assert settings.status_code == 200
        assert settings.json()["settings_scope"] == "common"
        assert settings.json()["can_edit_common_settings"] is True
        assert settings.json()["repo_discovery_readonly"] is False

        project = client.put(
            "/api/project-settings",
            json={
                "repo_root": str(repo_root),
                "git_remote_name": "origin",
                "review_branch_push_enabled": True,
            },
        )
        assert project.status_code == 200
        assert project.json()["git_remote_name"] == "origin"


def test_bootstrapping_first_admin_turns_on_effective_login_requirement(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        initial = client.get("/api/auth/me")
        assert initial.status_code == 200
        assert initial.json()["enabled"] is False
        assert initial.json()["bootstrap_required"] is True

        bootstrap = client.post("/api/auth/bootstrap", json={"username": "admin", "password": "secret-password"})
        assert bootstrap.status_code == 200
        assert bootstrap.json()["username"] == "admin"

        authenticated = client.get("/api/auth/me")
        assert authenticated.status_code == 200
        assert authenticated.json()["enabled"] is True
        assert authenticated.json()["authenticated"] is True

    with TestClient(app) as anonymous_client:
        blocked = anonymous_client.get("/api/settings/models")
        assert blocked.status_code == 401


def test_local_admin_can_create_first_user_from_user_settings(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        users = client.get("/api/auth/users")
        assert users.status_code == 200
        assert users.json() == {"users": []}

        created = client.post("/api/auth/users", json={"username": "admin", "password": "secret-password", "is_admin": False})
        assert created.status_code == 200
        assert created.json()["username"] == "admin"
        assert created.json()["is_admin"] is True

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["enabled"] is True
        assert me.json()["authenticated"] is True
        assert me.json()["user"]["username"] == "admin"

        listed = client.get("/api/auth/users")
        assert listed.status_code == 200
        assert [(item["username"], item["is_admin"]) for item in listed.json()["users"]] == [("admin", True)]

    with TestClient(app) as anonymous_client:
        blocked = anonymous_client.get("/api/auth/users")
        assert blocked.status_code == 401


def test_local_admin_mode_is_loopback_only(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app, base_url="http://127.0.0.1") as local_client:
        settings = local_client.get("/api/settings/models")
        assert settings.status_code == 200

    with TestClient(app, base_url="http://192.168.1.50") as remote_client:
        blocked = remote_client.get("/api/settings/models")
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "local admin mode is only available from localhost"


def test_local_admin_mode_remote_browser_gets_help_page(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app, base_url="http://192.168.1.50:8765") as remote_client:
        blocked = remote_client.get("/", headers={"accept": "text/html"})
        assert blocked.status_code == 403
        assert blocked.headers["content-type"].startswith("text/html")
        assert "Assistant Agent Kanban is running in local admin mode." in blocked.text
        assert "http://localhost:8765/" in blocked.text


def test_login_mode_allows_ip_host_after_user_exists(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)

    with TestClient(app, base_url="http://192.168.1.50") as client:
        blocked = client.get("/api/settings/models")
        assert blocked.status_code == 401

        login = client.post("/api/auth/login", json={"username": "admin", "password": "secret-password"})
        assert login.status_code == 200

        settings = client.get("/api/settings/models")
        assert settings.status_code == 200


def test_onboarding_state_is_stored_per_authenticated_user(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)
    app.state.user_settings_store.create_user("member", "member-password", is_admin=False)

    with TestClient(app) as admin_client:
        login = admin_client.post("/api/auth/login", json={"username": "admin", "password": "secret-password"})
        assert login.status_code == 200

        initial = admin_client.get("/api/auth/me")
        assert initial.status_code == 200
        assert initial.json()["onboarding"]["completed"] is False

        updated = admin_client.patch("/api/auth/onboarding", json={"completed": True, "version": 1})
        assert updated.status_code == 200
        assert updated.json()["completed"] is True
        assert updated.json()["version"] == 1

        refreshed = admin_client.get("/api/auth/me")
        assert refreshed.status_code == 200
        assert refreshed.json()["onboarding"]["completed"] is True

    with TestClient(app) as member_client:
        login = member_client.post("/api/auth/login", json={"username": "member", "password": "member-password"})
        assert login.status_code == 200

        member_state = member_client.get("/api/auth/me")
        assert member_state.status_code == 200
        assert member_state.json()["onboarding"]["completed"] is False

    with TestClient(app) as anonymous_client:
        blocked = anonymous_client.patch("/api/auth/onboarding", json={"completed": True, "version": 1})
        assert blocked.status_code == 401


def test_authenticated_user_can_change_own_password(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    member = app.state.user_settings_store.create_user("member", "member-password", is_admin=False)
    other_token, _ = app.state.user_settings_store.create_session(member)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "member", "password": "member-password"})
        assert login.status_code == 200

        rejected = client.patch(
            "/api/auth/password",
            json={"current_password": "wrong-password", "new_password": "new-password"},
        )
        assert rejected.status_code == 401
        assert app.state.user_settings_store.authenticate("member", "member-password") == member

        empty_password = client.patch(
            "/api/auth/password",
            json={"current_password": "member-password", "new_password": ""},
        )
        assert empty_password.status_code == 400

        changed = client.patch(
            "/api/auth/password",
            json={"current_password": "member-password", "new_password": "new-password"},
        )
        assert changed.status_code == 200
        assert changed.json() == {"changed": True}
        assert app.state.user_settings_store.authenticate("member", "member-password") is None
        assert app.state.user_settings_store.authenticate("member", "new-password") == member
        assert app.state.user_settings_store.user_for_session(other_token) is None

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["authenticated"] is True
        assert me.json()["user"]["username"] == "member"

    with TestClient(app) as old_password_client:
        old_login = old_password_client.post("/api/auth/login", json={"username": "member", "password": "member-password"})
        assert old_login.status_code == 401

        new_login = old_password_client.post("/api/auth/login", json={"username": "member", "password": "new-password"})
        assert new_login.status_code == 200


def test_password_change_requires_login_mode(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        changed = client.patch(
            "/api/auth/password",
            json={"current_password": "local", "new_password": "new-password"},
        )
        assert changed.status_code == 409


def test_admin_can_create_users_and_logout(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    admin = app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "secret-password"})
        assert login.status_code == 200

        created = client.post("/api/auth/users", json={"username": "Member", "password": "member-password", "is_admin": False})
        assert created.status_code == 200
        assert created.json()["username"] == "member"
        assert created.json()["is_admin"] is False

        users = client.get("/api/auth/users")
        assert users.status_code == 200
        assert [(item["username"], item["is_admin"]) for item in users.json()["users"]] == [
            ("admin", True),
            ("member", False),
        ]
        member_id = next(item["user_id"] for item in users.json()["users"] if item["username"] == "member")
        member_user = app.state.user_settings_store.authenticate("member", "member-password")
        assert member_user is not None
        member_token, _ = app.state.user_settings_store.create_session(member_user)

        duplicate = client.post("/api/auth/users", json={"username": "member", "password": "member-password", "is_admin": False})
        assert duplicate.status_code == 409

        deleted = client.delete(f"/api/auth/users/{member_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert deleted.json()["user"]["username"] == "member"
        assert app.state.user_settings_store.authenticate("member", "member-password") is None
        assert app.state.user_settings_store.user_for_session(member_token) is None

        missing = client.delete(f"/api/auth/users/{member_id}")
        assert missing.status_code == 404

        self_delete = client.delete(f"/api/auth/users/{admin.user_id}")
        assert self_delete.status_code == 409

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200

        blocked = client.get("/api/settings/models")
        assert blocked.status_code == 401


def test_admin_can_delete_all_users_and_return_to_local_mode(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)
    member = app.state.user_settings_store.create_user("member", "member-password", is_admin=False)
    member_token, _ = app.state.user_settings_store.create_session(member)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "secret-password"})
        assert login.status_code == 200

        deleted = client.delete("/api/auth/users")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "deleted_count": 2}
        assert app.state.user_settings_store.user_count() == 0
        assert app.state.user_settings_store.authenticate("admin", "secret-password") is None
        assert app.state.user_settings_store.user_for_session(member_token) is None

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["enabled"] is False
        assert me.json()["authenticated"] is False

        settings = client.get("/api/settings/models")
        assert settings.status_code == 200


def test_non_admin_cannot_create_users(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)
    app.state.user_settings_store.create_user("member", "member-password", is_admin=False)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "member", "password": "member-password"})
        assert login.status_code == 200

        users = client.get("/api/auth/users")
        assert users.status_code == 403

        created = client.post("/api/auth/users", json={"username": "other", "password": "other-password", "is_admin": False})
        assert created.status_code == 403

        deleted = client.delete(f"/api/auth/users/{app.state.user_settings_store.list_users()[0].user_id}")
        assert deleted.status_code == 403

        delete_all = client.delete("/api/auth/users")
        assert delete_all.status_code == 403


def test_admin_user_delete_guards_self_and_last_admin(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    admin = app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "secret-password"})
        assert login.status_code == 200

        self_delete = client.delete(f"/api/auth/users/{admin.user_id}")
        assert self_delete.status_code == 409
        assert app.state.user_settings_store.authenticate("admin", "secret-password") is not None

        other_admin = client.post("/api/auth/users", json={"username": "other-admin", "password": "other-password", "is_admin": True})
        assert other_admin.status_code == 200

        deleted = client.delete(f"/api/auth/users/{other_admin.json()['user_id']}")
        assert deleted.status_code == 200
        assert app.state.user_settings_store.authenticate("other-admin", "other-password") is None


def test_non_admin_cannot_update_repo_discovery_settings(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    config.repo_discovery.root = "../common-root"
    config.repo_discovery.max_depth = 2
    config.slack.default_channel = "C-common"
    config.slack.default_channel_display = "#common"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)
    app.state.user_settings_store.create_user("member", "member-password", is_admin=False)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "member", "password": "member-password"})
        assert login.status_code == 200

        blocked = client.put(
            "/api/settings/models",
            json={
                "language": "KO",
                "repo_discovery_root": "/tmp/not-allowed",
                "repo_discovery_max_depth": 9,
            },
        )
        assert blocked.status_code == 403
        blocked_runtime = client.put(
            "/api/settings/models",
            json={
                "planner_model": "not-allowed",
                "role_backends": {"planner": "codex"},
                "slack_enabled": True,
                "slack_bot_token": "xoxb-not-allowed",
            },
        )
        assert blocked_runtime.status_code == 403
        assert app.state.runtime.config.repo_discovery.root == "../common-root"
        assert app.state.runtime.config.repo_discovery.max_depth == 2

        saved = client.put("/api/settings/models", json={"language": "KO", "theme": "dark", "slack_default_channel": "#personal"})
        assert saved.status_code == 200
        payload = saved.json()
        assert payload["settings_scope"] == "user"
        assert payload["repo_discovery_readonly"] is True
        assert payload["language"] == "KO"
        assert payload["theme"] == "dark"
        assert payload["slack_default_channel"] == "#personal"
        assert payload["slack_default_channel_display"] == "#personal"
        assert payload["repo_discovery_root"] == "../common-root"
        assert payload["repo_discovery_max_depth"] == 2


def test_non_admin_slack_settings_are_channel_only(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.auth.enabled = False
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-common"
    config.slack.app_token = "xapp-common"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)
    member = app.state.user_settings_store.create_user("member", "member-password", is_admin=False)

    class FakeSlackResult:
        resolved_channel_id = "CUSER"
        resolved_channel_display = "#personal"

        def to_payload(self):
            return {"ok": True, "summary": "ok", "checks": [], "resolved_channel_id": "CUSER", "resolved_channel_display": "#personal"}

    monkeypatch.setattr("assistant_agent_kanban.api.routes.settings_routes.run_slack_settings_test", lambda slack_config, *, uses_posted_values: FakeSlackResult())

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "member", "password": "member-password"}).status_code == 200

        blocked = client.post("/api/settings/slack-test", json={"slack_default_channel": "#personal", "slack_bot_token": "xoxb-user"})
        assert blocked.status_code == 403

        tested = client.post("/api/settings/slack-test", json={"slack_default_channel": "#personal"})
        assert tested.status_code == 200

        listener = client.post("/api/settings/slack-receive-test/start", json={})
        assert listener.status_code == 403

    settings = app.state.user_settings_store.get_user_settings(member.user_id)
    assert settings.runtime.slack_default_channel == "CUSER"
    assert config.slack.bot_token == "xoxb-common"
