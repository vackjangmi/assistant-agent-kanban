from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
import pytest

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.api.routes import _resolve_settings_snapshots
from assistant_agent_kanban import config as config_module
from assistant_agent_kanban.config import PROJECT_ROOT, load_config
from assistant_agent_kanban.opencode_adapter import _parse_discovered_models

from ..conftest import FakeAdapter


from ._helpers import _settings_adapter_registry

def test_api_reads_and_updates_model_settings(configured_paths, tmp_path, monkeypatch):
    config, _, _ = configured_paths
    config_path = tmp_path / "dashboard-config.yaml"
    local_config_path = tmp_path / "config.local.yaml"
    config.persist(config_path)
    omo_root = tmp_path / "xdg-config"
    omo_config_dir = omo_root / "opencode"
    omo_config_dir.mkdir(parents=True)
    (omo_config_dir / "oh-my-opencode.json").write_text(
        json.dumps(
            {
                "agents": {
                    "explore": {"model": "openai/gpt-5-mini", "variant": "low"},
                    "librarian": {"model": "openai/gpt-5-mini", "variant": "low"},
                },
                "categories": {
                    "quick": {"model": "openai/gpt-5-nano", "variant": "low"},
                },
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(omo_root))
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        get_response = client.get("/api/settings/models")
        assert get_response.status_code == 200
        assert get_response.json()["language"] == "EN"
        assert get_response.json()["theme"] == "light"
        assert get_response.json()["coding_assistant"] == "opencode"
        assert get_response.json()["role_backends"] == {
            "planner": None,
            "request_draft": None,
            "plan_approval": None,
            "implementer": None,
            "reviewer": None,
            "commit": None,
        }
        assert get_response.json()["effective_role_backends"] == {
            "planner": "opencode",
            "request_draft": "opencode",
            "plan_approval": "opencode",
            "implementer": "opencode",
            "reviewer": "opencode",
            "commit": "opencode",
        }
        assert get_response.json()["worker_live_logs_enabled"] is True
        assert get_response.json()["available_assistants"] == [
            {"value": "codex", "label": "Codex CLI"},
            {"value": "claude", "label": "Claude Code"},
            {"value": "gemini", "label": "Gemini CLI"},
            {"value": "opencode", "label": "OpenCode"},
        ]
        assert get_response.json()["planner_model"] is None
        assert get_response.json()["request_draft_model"] is None
        assert get_response.json()["planner_session_token_budget"] == 250
        assert get_response.json()["planner_agent_count"] == 1
        assert get_response.json()["implementer_session_token_budget"] == 250
        assert get_response.json()["implementer_agent_count"] == 1
        assert get_response.json()["reviewer_session_token_budget"] == 250
        assert get_response.json()["reviewer_agent_count"] == 1
        assert get_response.json()["commit_session_token_budget"] == 250
        assert get_response.json()["repo_discovery_root"] == str(config.repo_discovery.root)
        assert get_response.json()["repo_discovery_max_depth"] == config.repo_discovery.max_depth
        assert get_response.json()["slack_enabled"] is False
        assert get_response.json()["slack_socket_mode_enabled"] is True
        assert get_response.json()["slack_default_channel"] is None
        assert get_response.json()["slack_app_mention_enabled"] is False
        assert get_response.json()["slack_bot_token_configured"] is False
        assert get_response.json()["slack_bot_token_masked"] is None
        assert get_response.json()["slack_app_token_configured"] is False
        assert get_response.json()["slack_app_token_masked"] is None
        assert get_response.json()["config_path"] == str(local_config_path.resolve())
        assert get_response.json()["available_models"] == ["gpt-5", "o3-mini"]
        assert get_response.json()["available_models_by_backend"]["opencode"] == ["gpt-5", "o3-mini"]
        assert get_response.json()["available_models_by_backend"]["codex"] == ["gpt-5.4", "gpt-5"]
        assert get_response.json()["available_models_by_backend"]["claude"] == [
            "default",
            "best",
            "sonnet",
            "opus",
            "haiku",
            "opus[1m]",
            "opusplan",
        ]
        assert get_response.json()["discovery_status"] == "ready"
        assert get_response.json()["discovery_error"] is None
        assert get_response.json()["delegated_model_status"] == "ready"
        assert get_response.json()["delegated_model_source_path"] == str((omo_config_dir / "oh-my-opencode.json").resolve())
        assert get_response.json()["delegated_models"] == [
            {"key": "quick", "source_type": "category", "model": "openai/gpt-5-nano", "variant": "low"},
            {"key": "explore", "source_type": "agent", "model": "openai/gpt-5-mini", "variant": "low"},
            {"key": "librarian", "source_type": "agent", "model": "openai/gpt-5-mini", "variant": "low"},
        ]
        assert planner_adapter.discovery_calls == [False]

        put_response = client.put(
            "/api/settings/models",
            json={
                "language": "KO",
                "coding_assistant": "opencode",
                "role_backends": {
                    "request_draft": "gemini",
                    "implementer": "codex",
                    "commit": "codex",
                },
                "worker_live_logs_enabled": False,
                "planner_model": "gpt-5",
                "request_draft_model": "gemini-2.5-flash",
                "planner_session_token_budget": 210,
                "planner_agent_count": 2,
                "implementer_model": " gpt-5.4 (high) ",
                "implementer_session_token_budget": 230,
                "implementer_agent_count": 3,
                "reviewer_model": "",
                "reviewer_session_token_budget": 190,
                "reviewer_agent_count": 4,
                "commit_model": "gpt-5 (xhigh)",
                "commit_session_token_budget": 250,
                "repo_discovery_root": "../",
                "repo_discovery_max_depth": 4,
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_bot_token": "xoxb-test-12345678",
                "slack_app_token": "xapp-test-87654321",
                "slack_default_channel": "#agent-alerts",
                "slack_app_mention_enabled": True,
            },
        )

    assert put_response.status_code == 200
    payload = put_response.json()
    assert payload["saved"] is True
    assert payload["language"] == "KO"
    assert payload["coding_assistant"] == "opencode"
    assert payload["role_backends"] == {
        "planner": None,
        "request_draft": "gemini",
        "plan_approval": None,
        "implementer": "codex",
        "reviewer": None,
        "commit": "codex",
    }
    assert payload["effective_role_backends"] == {
        "planner": "opencode",
        "request_draft": "gemini",
        "plan_approval": "opencode",
        "implementer": "codex",
        "reviewer": "opencode",
        "commit": "codex",
    }
    assert payload["worker_live_logs_enabled"] is False
    assert payload["planner_model"] == "gpt-5"
    assert payload["request_draft_model"] == "gemini-2.5-flash"
    assert payload["planner_session_token_budget"] == 210
    assert payload["planner_agent_count"] == 2
    assert payload["implementer_model"] == "gpt-5.4 (high)"
    assert payload["implementer_session_token_budget"] == 230
    assert payload["implementer_agent_count"] == 3
    assert payload["reviewer_model"] is None
    assert payload["reviewer_session_token_budget"] == 190
    assert payload["reviewer_agent_count"] == 4
    assert payload["commit_model"] == "gpt-5 (xhigh)"
    assert payload["commit_session_token_budget"] == 250
    assert payload["repo_discovery_root"] == "../"
    assert payload["repo_discovery_max_depth"] == 4
    assert payload["slack_enabled"] is True
    assert payload["slack_socket_mode_enabled"] is True
    assert payload["slack_default_channel"] is None
    assert payload["slack_default_channel_display"] is None
    assert payload["slack_app_mention_enabled"] is True
    assert payload["slack_bot_token_configured"] is True
    assert payload["slack_bot_token_masked"] == "••••••••••••••5678"
    assert payload["slack_app_token_configured"] is True
    assert payload["slack_app_token_masked"] == "••••••••••••••4321"
    assert app.state.runtime.config.opencode.planner_model == "gpt-5"
    assert app.state.runtime.config.runtime.language == "KO"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.runtime.role_backends.request_draft == "gemini"
    assert app.state.runtime.config.runtime.role_backends.implementer == "codex"
    assert app.state.runtime.config.runtime.role_backends.commit == "codex"
    assert app.state.runtime.config.opencode.worker_live_logs_enabled is False
    assert app.state.runtime.config.opencode.planner_session_token_budget == 210000
    assert app.state.runtime.config.runtime.planner_agent_count == 2
    assert app.state.runtime.config.gemini.request_draft_model == "gemini-2.5-flash"
    assert app.state.runtime.config.codex.implementer_model == "gpt-5.4 (high)"
    assert app.state.runtime.config.codex.implementer_session_token_budget == 230000
    assert app.state.runtime.config.runtime.implementer_agent_count == 3
    assert app.state.runtime.config.opencode.reviewer_model is None
    assert app.state.runtime.config.opencode.reviewer_session_token_budget == 190000
    assert app.state.runtime.config.runtime.reviewer_agent_count == 4
    assert app.state.runtime.config.repo_discovery.root == "../"
    assert app.state.runtime.config.repo_discovery.max_depth == 4
    assert app.state.runtime.config.slack.enabled is True
    assert app.state.runtime.config.slack.socket_mode_enabled is True
    assert app.state.runtime.config.slack.bot_token == "xoxb-test-12345678"
    assert app.state.runtime.config.slack.app_token == "xapp-test-87654321"
    assert app.state.runtime.config.slack.default_channel is None
    assert app.state.runtime.config.slack.default_channel_display is None
    assert app.state.runtime.config.slack.app_mention_enabled is True
    assert load_config(config_path).codex.commit_model == "gpt-5 (xhigh)"
    assert load_config(config_path).codex.commit_session_token_budget == 250000
    assert load_config(config_path).runtime.role_backends.implementer == "codex"
    assert load_config(config_path).runtime.role_backends.commit == "codex"
    assert load_config(config_path).repo_discovery.root == "../"
    assert load_config(config_path).repo_discovery.max_depth == 4
    assert load_config(config_path).slack.bot_token == "xoxb-test-12345678"
    assert load_config(config_path).slack.app_token == "xapp-test-87654321"
    assert load_config(config_path).slack.default_channel is None
    assert load_config(config_path).slack.default_channel_display is None



def test_api_settings_can_clear_slack_tokens(configured_paths):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-existing-1234"
    config.slack.app_token = "xapp-existing-5678"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "slack_bot_token": "",
                "slack_app_token": "",
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_app_mention_enabled": False,
            },
        )

    assert response.status_code == 200
    assert response.json()["slack_bot_token_configured"] is False
    assert response.json()["slack_bot_token_masked"] is None
    assert response.json()["slack_app_token_configured"] is False
    assert response.json()["slack_app_token_masked"] is None
    assert app.state.runtime.config.slack.bot_token is None
    assert app.state.runtime.config.slack.app_token is None



def test_api_runs_slack_settings_test_with_posted_values(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    monkeypatch.setattr(
        "assistant_agent_kanban.api.routes.settings_routes.run_slack_settings_test",
        lambda slack_config, *, uses_posted_values: type(
            "SlackResult",
            (),
            {
                "to_payload": lambda self: {
                    "ok": True,
                    "summary": f"tested {slack_config.default_channel}",
                    "checks": [
                        {"name": "enabled", "ok": slack_config.enabled, "message": "enabled"},
                        {"name": "bot_token", "ok": slack_config.bot_token == "xoxb-posted", "message": "bot"},
                    ],
                    "uses_posted_values": uses_posted_values,
                    "receive_verification_mode": "readiness",
                    "resolved_channel_id": "C123",
                    "resolved_channel_display": "#agent-alerts",
                }
            },
        )(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/settings/slack-test",
            json={
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_bot_token": "xoxb-posted",
                "slack_app_token": "xapp-posted",
                "slack_default_channel": "#agent-alerts",
                "slack_app_mention_enabled": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Effective channel updated" in response.json()["summary"]
    assert response.json()["uses_posted_values"] is True
    assert config.slack.default_channel == "C123"
    assert config.slack.default_channel_display == "#agent-alerts"
    assert config.slack.enabled is False
    reloaded = load_config(config.config_path_for_persistence())
    assert reloaded.slack.default_channel == "C123"
    assert reloaded.slack.default_channel_display == "#agent-alerts"



def test_api_preserves_saved_slack_tokens_when_put_payload_omits_them(configured_paths):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-existing-1234"
    config.slack.app_token = "xapp-existing-5678"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_default_channel": "#agent-alerts",
                "slack_app_mention_enabled": True,
            },
        )

    assert response.status_code == 200
    assert app.state.runtime.config.slack.bot_token == "xoxb-existing-1234"
    assert app.state.runtime.config.slack.app_token == "xapp-existing-5678"



def test_api_slack_settings_test_reports_failure(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    monkeypatch.setattr(
        "assistant_agent_kanban.api.routes.settings_routes.run_slack_settings_test",
        lambda slack_config, *, uses_posted_values: type(
            "SlackResult",
            (),
            {
                "to_payload": lambda self: {
                    "ok": False,
                    "summary": "missing channel",
                    "checks": [{"name": "send_test", "ok": False, "message": "channel required"}],
                    "uses_posted_values": uses_posted_values,
                    "receive_verification_mode": "readiness",
                }
            },
        )(),
    )

    with TestClient(app) as client:
        response = client.post("/api/settings/slack-test", json={"slack_enabled": True, "slack_bot_token": "xoxb-posted"})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["checks"][0]["message"] == "channel required"



def test_api_starts_and_reads_slack_receive_test(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    async def fake_start_receive_test():
        return {"listener_enabled": True, "listener_connected": False, "listener_last_error": None, "receive_test": {"status": "pending", "token": "abc123"}}

    app.state.runtime.slack_runtime.start_receive_test = fake_start_receive_test  # type: ignore[method-assign]
    app.state.runtime.slack_runtime.snapshot = lambda: {"listener_enabled": True, "listener_connected": True, "listener_last_error": None, "receive_test": {"status": "received", "token": "abc123"}}  # type: ignore[method-assign]

    with TestClient(app) as client:
        start_response = client.post("/api/settings/slack-receive-test/start", json={})
        status_response = client.get("/api/settings/slack-receive-test")

    assert start_response.status_code == 200
    assert start_response.json()["receive_test"]["token"] == "abc123"
    assert status_response.status_code == 200
    assert status_response.json()["receive_test"]["status"] == "received"



def test_settings_snapshot_refreshes_only_selected_backend(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    calls: list[tuple[str, bool]] = []

    class Snapshot:
        def __init__(self, backend: str):
            self.backend = backend

    def fake_get(backend, *, refresh=False):
        calls.append((backend, refresh))
        return Snapshot(backend)

    app.state.runtime.model_registry.get = fake_get  # type: ignore[method-assign]

    asyncio.run(_resolve_settings_snapshots(app.state.runtime, refresh=True, assistant="opencode"))

    assert ("opencode", True) in calls
    assert all(refresh is False for backend, refresh in calls if backend != "opencode")



def test_api_persists_model_settings_to_default_local_config_when_unloaded(configured_paths, tmp_path):
    config, _, _ = configured_paths
    default_base_path = tmp_path / "config.yaml"
    default_local_path = tmp_path / "config.local.yaml"
    default_base_path.write_text("opencode:\n  planner_model: base-planner\n")
    original_default_config_path = config_module.DEFAULT_CONFIG_PATH
    original_default_local_path = config_module.DEFAULT_LOCAL_CONFIG_PATH
    config_module.DEFAULT_CONFIG_PATH = default_base_path
    config_module.DEFAULT_LOCAL_CONFIG_PATH = default_local_path
    try:
        app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

        with TestClient(app) as client:
            response = client.put(
                "/api/settings/models",
                json={
                "planner_model": "planner-x",
                "language": "KO",
                "theme": "dark",
                "coding_assistant": "opencode",
                "worker_live_logs_enabled": False,
                "planner_session_token_budget": 180,
                "planner_agent_count": 2,
                "implementer_model": None,
                "implementer_session_token_budget": 250,
                "implementer_agent_count": 1,
                "reviewer_model": "reviewer-y",
                "reviewer_session_token_budget": 220,
                "reviewer_agent_count": 3,
                "commit_model": None,
                "commit_session_token_budget": 250,
                "repo_discovery_root": "/tmp/scan-root",
                "repo_discovery_max_depth": 3,
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_bot_token": "xoxb-local-persist",
                "slack_app_token": "xapp-local-persist",
                "slack_default_channel": "C123",
                "slack_app_mention_enabled": True,
            },
        )

        assert response.status_code == 200
        assert default_local_path.exists()
        persisted = load_config(default_base_path)
        assert persisted.opencode.planner_model == "planner-x"
        assert persisted.runtime.language == "KO"
        assert persisted.runtime.theme == "dark"
        assert persisted.runtime.coding_assistant == "opencode"
        assert persisted.opencode.worker_live_logs_enabled is False
        assert persisted.opencode.planner_session_token_budget == 180000
        assert persisted.runtime.planner_agent_count == 2
        assert persisted.opencode.reviewer_model == "reviewer-y"
        assert persisted.opencode.reviewer_session_token_budget == 220000
        assert persisted.runtime.reviewer_agent_count == 3
        assert persisted.repo_discovery.root == "/tmp/scan-root"
        assert persisted.repo_discovery.max_depth == 3
        assert persisted.slack.enabled is True
        assert persisted.slack.socket_mode_enabled is True
        assert persisted.slack.bot_token == "xoxb-local-persist"
        assert persisted.slack.app_token == "xapp-local-persist"
        assert persisted.slack.default_channel is None
        assert persisted.slack.default_channel_display is None
        assert persisted.slack.app_mention_enabled is True
        assert response.json()["config_path"] == str(default_local_path.resolve())
    finally:
        config_module.DEFAULT_CONFIG_PATH = original_default_config_path
        config_module.DEFAULT_LOCAL_CONFIG_PATH = original_default_local_path



def test_api_preserves_repo_discovery_root_when_put_payload_omits_it(configured_paths):
    config, _, _ = configured_paths
    config.repo_discovery.root = "../custom-root"
    config.runtime.planner_agent_count = 5
    config.runtime.language = "KO"
    config.runtime.theme = "dark"
    config.runtime.coding_assistant = "opencode"
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "planner_model": "planner-x",
                "theme": "dark",
                "coding_assistant": "opencode",
                "worker_live_logs_enabled": False,
                "planner_session_token_budget": 260,
                "implementer_agent_count": 2,
                "implementer_model": None,
                "implementer_session_token_budget": 250,
                "reviewer_model": None,
                "reviewer_session_token_budget": 250,
                "commit_model": None,
                "commit_session_token_budget": 250,
                "repo_discovery_max_depth": 5,
            },
        )

    assert response.status_code == 200
    assert response.json()["language"] == "KO"
    assert response.json()["theme"] == "dark"
    assert response.json()["coding_assistant"] == "opencode"
    assert response.json()["worker_live_logs_enabled"] is False
    assert response.json()["repo_discovery_root"] == "../custom-root"
    assert response.json()["repo_discovery_max_depth"] == 5
    assert response.json()["planner_agent_count"] == 5
    assert response.json()["implementer_agent_count"] == 2
    assert app.state.runtime.config.repo_discovery.root == "../custom-root"
    assert app.state.runtime.config.runtime.language == "KO"
    assert app.state.runtime.config.runtime.theme == "dark"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.opencode.worker_live_logs_enabled is False
    assert app.state.runtime.config.runtime.planner_agent_count == 5
    assert app.state.runtime.config.runtime.implementer_agent_count == 2



def test_api_does_not_mutate_live_runtime_settings_when_persist_fails(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.opencode.planner_model = "stable-planner"
    config.runtime.planner_agent_count = 3
    config.runtime.language = "EN"
    config.runtime.theme = "light"
    config.runtime.coding_assistant = "opencode"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    def fail_persist(self, path=None):
        raise OSError("disk full")

    monkeypatch.setattr(config_module.AppConfig, "persist", fail_persist)

    with TestClient(app) as client:
        with pytest.raises(OSError, match="disk full"):
            client.put(
                "/api/settings/models",
                json={
                    "planner_model": "new-planner",
                    "language": "KO",
                    "theme": "dark",
                    "coding_assistant": "opencode",
                    "planner_session_token_budget": 250,
                    "planner_agent_count": 7,
                    "implementer_model": None,
                    "implementer_session_token_budget": 250,
                    "reviewer_model": None,
                    "reviewer_session_token_budget": 250,
                    "commit_model": None,
                    "commit_session_token_budget": 250,
                },
            )

    assert app.state.runtime.config.opencode.planner_model == "stable-planner"
    assert app.state.runtime.config.runtime.language == "EN"
    assert app.state.runtime.config.runtime.theme == "light"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.runtime.planner_agent_count == 3



def test_api_rejects_invalid_runtime_language(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "language": "JP",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 422
    assert config.runtime.language == "EN"



def test_api_accepts_codex_runtime_coding_assistant(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "codex",
                "planner_model": "gpt-5.4",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 200
    assert config.runtime.coding_assistant == "codex"
    assert config.codex.planner_model == "gpt-5.4"



def test_api_accepts_claude_runtime_coding_assistant(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry(claude_adapter=FakeAdapter(["claude"], discovery_responses=[["claude-sonnet-4-6"]]))
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "claude",
                "planner_model": "claude-sonnet-4-6",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 200
    assert config.runtime.coding_assistant == "claude"
    assert config.claude.planner_model == "claude-sonnet-4-6"



def test_api_includes_persisted_claude_custom_models_in_candidates(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "claude"
    config.claude.binary = "/bin/echo"
    config.claude.planner_model = "claude-sonnet-4-6"
    config.claude.implementer_model = "my-bedrock-profile"
    adapter_registry = {
        "opencode": FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]]),
        "codex": FakeAdapter(["codex"], discovery_responses=[["gpt-5.4", "gpt-5"]]),
    }
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "claude"
    assert payload["available_models"] == [
        "default",
        "best",
        "sonnet",
        "opus",
        "haiku",
        "opus[1m]",
        "opusplan",
        "claude-sonnet-4-6",
        "my-bedrock-profile",
    ]



def test_api_accepts_unknown_claude_model_on_save(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "claude",
                "planner_model": "custom-gateway-model",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 200
    assert response.json()["planner_model"] == "custom-gateway-model"
    assert config.claude.planner_model == "custom-gateway-model"



def test_api_accepts_unknown_opencode_model_on_save(configured_paths):
    config, _, _ = configured_paths
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["openai/gpt-5.4"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "opencode",
                "planner_model": "not-a-real-model",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 200
    assert response.json()["planner_model"] == "not-a-real-model"
    assert config.opencode.planner_model == "not-a-real-model"



def test_api_accepts_unknown_codex_model_on_save(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "codex",
                "planner_model": "not-a-real-model",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 200
    assert response.json()["planner_model"] == "not-a-real-model"
    assert config.codex.planner_model == "not-a-real-model"



def test_api_accepts_unknown_inactive_role_backend_model_on_save(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "opencode",
                "role_backends": {"reviewer": "codex"},
                "reviewer_model": "not-a-real-model",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 200
    assert response.json()["reviewer_model"] == "not-a-real-model"
    assert config.codex.reviewer_model == "not-a-real-model"



def test_api_refresh_can_preview_codex_models_without_switching_runtime(configured_paths):
    config, _, _ = configured_paths
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models?refresh=true&assistant=codex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "codex"
    assert "gpt-5.4" in payload["available_models"]
    assert payload["planner_model"] == config.codex.planner_model
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"



def test_api_rejects_unavailable_role_backend_on_save(configured_paths):
    config, _, _ = configured_paths

    class UnavailableCodexAdapter(FakeAdapter):
        def availability_error(self, *, config, backend):
            return "binary not found on PATH: codex"

    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter, codex_adapter=UnavailableCodexAdapter(["codex"]))
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "opencode",
                "role_backends": {"implementer": "codex"},
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "settings.backend_unavailable",
        "field": "role_backends.implementer",
        "message": "binary not found on PATH: codex",
    }



def test_api_settings_only_lists_startup_available_assistants(configured_paths):
    config, _, _ = configured_paths

    class UnavailableCodexAdapter(FakeAdapter):
        def availability_error(self, *, config, backend):
            return "binary not found on PATH: codex"

    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter, codex_adapter=UnavailableCodexAdapter(["codex"]))
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models")

    assert response.status_code == 200
    assert response.json()["available_assistants"] == [
        {"value": "claude", "label": "Claude Code"},
        {"value": "gemini", "label": "Gemini CLI"},
        {"value": "opencode", "label": "OpenCode"},
    ]



def test_api_settings_without_assistant_query_returns_persisted_backend(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "codex"
    config.codex.planner_model = "gpt-5.4"
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "codex"
    assert payload["planner_model"] == "gpt-5.4"



def test_api_save_materializes_runtime_agents_immediately(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)
    runtime_agents_dir = config.kanban_root / "_runtime" / "opencode-config" / "opencode" / "agents"
    planner_agent_path = runtime_agents_dir / f"{config.opencode.planner_agent}.md"
    implementer_agent_path = runtime_agents_dir / f"{config.opencode.implementer_agent}.md"
    reviewer_agent_path = runtime_agents_dir / f"{config.opencode.reviewer_agent}.md"

    with TestClient(app) as client:
        first_save = client.put(
            "/api/settings/models",
            json={
                "planner_model": "openai/gpt-5.4",
                "planner_session_token_budget": 250,
                "implementer_model": "openai/gpt-5.4-mini",
                "implementer_session_token_budget": 250,
                "reviewer_model": "github-copilot/gpt-5",
                "reviewer_session_token_budget": 250,
                "commit_model": None,
                "commit_session_token_budget": 250,
            },
        )
        assert first_save.status_code == 200
        assert planner_agent_path.exists()
        assert implementer_agent_path.exists()
        assert reviewer_agent_path.exists()
        assert "model: openai/gpt-5.4" in planner_agent_path.read_text()
        assert "model: openai/gpt-5.4-mini" in implementer_agent_path.read_text()
        assert "model: github-copilot/gpt-5" in reviewer_agent_path.read_text()
        assert "Do not call `task()` or delegate helper subtasks." in planner_agent_path.read_text()
        assert "If the prompt says this is a handshake/session-prep step, return only a short greeting." in planner_agent_path.read_text()
        assert "Do not delegate the final file edits" in implementer_agent_path.read_text()
        assert "If the prompt says this is a final review-artifact step, return only the requested strict JSON object." in reviewer_agent_path.read_text()
        assert "If the prompt says this is human review Q&A, answer the human's question directly in markdown with a natural response." in reviewer_agent_path.read_text()
        assert "For normal review runs, prefer `Verdict: PASS` when only minor follow-up notes remain" in reviewer_agent_path.read_text()
        assert "include the endpoint location in the review markdown" in reviewer_agent_path.read_text()

        second_save = client.put(
            "/api/settings/models",
            json={
                "planner_model": None,
                "planner_session_token_budget": 250,
                "implementer_model": None,
                "implementer_session_token_budget": 250,
                "reviewer_model": None,
                "reviewer_session_token_budget": 250,
                "commit_model": None,
                "commit_session_token_budget": 250,
            },
        )
        assert second_save.status_code == 200

    assert planner_agent_path.read_text() == (PROJECT_ROOT / ".opencode" / "agents" / f"{config.opencode.planner_agent}.md").read_text()
    assert implementer_agent_path.read_text() == (PROJECT_ROOT / ".opencode" / "agents" / f"{config.opencode.implementer_agent}.md").read_text()
    assert reviewer_agent_path.read_text() == (PROJECT_ROOT / ".opencode" / "agents" / f"{config.opencode.reviewer_agent}.md").read_text()



def test_api_refreshes_model_discovery_and_keeps_cached_options_on_failure(configured_paths):
    config, _, _ = configured_paths
    planner_adapter = FakeAdapter(
        ["plan"],
        discovery_responses=[["gpt-5", "claude-3.7-sonnet"], RuntimeError("opencode models failed")],
    )
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        initial = client.get("/api/settings/models")
        assert initial.status_code == 200
        assert initial.json()["available_models"] == ["gpt-5", "claude-3.7-sonnet"]
        refreshed = client.get("/api/settings/models?refresh=true")

    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["available_models"] == ["gpt-5", "claude-3.7-sonnet"]
    assert payload["discovery_status"] == "fallback"
    assert payload["discovery_error"] == "opencode models failed"
    assert planner_adapter.discovery_calls == [False, True]



def test_api_refreshes_models_without_refreshing_cached_availability(configured_paths):
    config, _, _ = configured_paths

    class AvailabilityTrackingAdapter(FakeAdapter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.availability_calls = 0

        def availability_error(self, *, config, backend):
            self.availability_calls += 1
            return None

    planner_adapter = AvailabilityTrackingAdapter(
        ["plan"],
        discovery_responses=[["gpt-5", "claude-3.7-sonnet"], ["gpt-5", "o3-mini"]],
    )
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        initial = client.get("/api/settings/models")
        assert initial.status_code == 200
        refreshed = client.get("/api/settings/models?refresh=true")

    assert refreshed.status_code == 200
    assert planner_adapter.discovery_calls == [False, True]
    assert planner_adapter.availability_calls == 1



def test_api_settings_initial_load_discovers_only_active_backend(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "opencode"
    opencode_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    codex_adapter = FakeAdapter(["codex"], discovery_responses=[["gpt-5.4", "gpt-5"]])
    claude_adapter = FakeAdapter(["claude"], discovery_responses=[["default", "sonnet"]])
    adapter_registry = _settings_adapter_registry(
        opencode_adapter=opencode_adapter,
        codex_adapter=codex_adapter,
        claude_adapter=claude_adapter,
    )
    app = create_app(config, opencode_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available_models_by_backend"]["opencode"] == ["gpt-5", "o3-mini"]
    assert payload["available_models_by_backend"]["codex"] == ["gpt-5.4", "gpt-5"]
    assert payload["available_models_by_backend"]["claude"] == ["default", "best", "sonnet", "opus", "haiku", "opus[1m]", "opusplan"]
    assert opencode_adapter.discovery_calls == [False]
    assert codex_adapter.discovery_calls == [False]
    assert claude_adapter.discovery_calls == [False]



def test_api_settings_refresh_discovers_only_requested_backend(configured_paths):
    config, _, _ = configured_paths
    opencode_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    codex_adapter = FakeAdapter(["codex"], discovery_responses=[["gpt-5.4", "gpt-5"]])
    claude_adapter = FakeAdapter(["claude"], discovery_responses=[["default", "sonnet"]])
    adapter_registry = _settings_adapter_registry(
        opencode_adapter=opencode_adapter,
        codex_adapter=codex_adapter,
        claude_adapter=claude_adapter,
    )
    app = create_app(config, opencode_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models?refresh=true&assistant=codex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "codex"
    assert payload["available_models_by_backend"]["codex"] == ["gpt-5.4", "gpt-5"]
    assert payload["available_models_by_backend"]["opencode"] == ["gpt-5", "o3-mini"]
    assert payload["available_models_by_backend"]["claude"] == ["default", "best", "sonnet", "opus", "haiku", "opus[1m]", "opusplan"]
    assert opencode_adapter.discovery_calls == [False]
    assert codex_adapter.discovery_calls == [False, True]
    assert claude_adapter.discovery_calls == [False]



def test_parse_discovered_models_ignores_verbose_json_metadata():
    verbose_output = """openai/gpt-5.4
{
  \"id\": \"gpt-5.4\",
  \"providerID\": \"openai\",
  \"name\": \"GPT-5.4\"
}
github-copilot/gpt-5
{
  \"id\": \"gpt-5\",
  \"providerID\": \"github-copilot\",
  \"name\": \"GPT-5\"
}
"""

    assert _parse_discovered_models(verbose_output) == ["openai/gpt-5.4", "github-copilot/gpt-5"]


def test_api_browse_directories(configured_paths, tmp_path):
    config, _, _ = configured_paths

    # Create test directories
    test_dir = tmp_path / "test_discovery_root"
    test_dir.mkdir()

    sub1 = test_dir / "subdir1"
    sub1.mkdir()

    sub2 = test_dir / "subdir2"
    sub2.mkdir()

    hidden_sub = test_dir / ".hidden_subdir"
    hidden_sub.mkdir()

    pycache_sub = test_dir / "__pycache__"
    pycache_sub.mkdir()

    file_item = test_dir / "some_file.txt"
    file_item.write_text("not a directory")

    # Update config discovery root to test_dir
    config.repo_discovery.root = str(test_dir)

    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        # Test default/no path
        response = client.get("/api/browse-directories")
        assert response.status_code == 200
        payload = response.json()
        assert payload["current_path"] == str(test_dir.resolve())
        assert payload["parent_path"] == str(test_dir.resolve().parent)

        # Hidden and pycache and files should be excluded, subdirectories included
        dirs = payload["directories"]
        assert len(dirs) == 2
        assert dirs[0]["name"] == "subdir1"
        assert dirs[0]["path"] == str(sub1.resolve())
        assert dirs[1]["name"] == "subdir2"
        assert dirs[1]["path"] == str(sub2.resolve())

        # Test specific path
        response2 = client.get(f"/api/browse-directories?path={sub1}")
        assert response2.status_code == 200
        payload2 = response2.json()
        assert payload2["current_path"] == str(sub1.resolve())
        assert payload2["parent_path"] == str(test_dir.resolve())
        assert len(payload2["directories"]) == 0

        # Test non-existent path fallback
        non_existent = test_dir / "does_not_exist"
        response3 = client.get(f"/api/browse-directories?path={non_existent}")
        assert response3.status_code == 200
        payload3 = response3.json()
        # Should fallback to discovery root
        assert payload3["current_path"] == str(test_dir.resolve())
