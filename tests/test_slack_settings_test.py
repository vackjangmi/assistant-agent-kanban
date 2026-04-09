from __future__ import annotations

from assistant_agent_kanban.config import SlackConfig
from assistant_agent_kanban.slack_settings_test import run_slack_settings_test


def test_slack_settings_test_requires_enablement():
    result = run_slack_settings_test(SlackConfig(enabled=False))

    assert result.ok is False
    assert result.summary == "Enable Slack before running the test."
    assert result.checks[0].name == "enabled"


def test_slack_settings_test_requires_default_channel_for_send_check(monkeypatch):
    monkeypatch.setattr(
        "assistant_agent_kanban.slack_settings_test.slack_api_call",
        lambda method, *, token, body=None: {"ok": True, "team": "Workspace"},
    )

    result = run_slack_settings_test(
        SlackConfig(
            enabled=True,
            socket_mode_enabled=False,
            bot_token="xoxb-test",
            default_channel=None,
        )
    )

    assert result.ok is False
    assert "could not be verified without a default channel" in result.summary
    assert result.checks[-1].name == "send_test"


def test_slack_settings_test_posts_message_and_checks_socket_mode(monkeypatch):
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        if method == "auth.test":
            return {"ok": True, "team": "Workspace"}
        if method == "chat.postMessage":
            assert body is not None
            return {"ok": True, "channel": body["channel"]}
        if method == "apps.connections.open":
            return {"ok": True, "url": "wss://example.test/socket"}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr("assistant_agent_kanban.slack_settings_test.slack_api_call", fake_call)

    result = run_slack_settings_test(
        SlackConfig(
            enabled=True,
            socket_mode_enabled=True,
            bot_token="xoxb-test",
            app_token="xapp-test",
            default_channel="#agent-alerts",
        )
    )

    assert result.ok is True
    assert "Socket Mode readiness was verified" in result.summary
    assert [call[0] for call in calls] == ["auth.test", "chat.postMessage", "apps.connections.open"]


def test_slack_settings_test_reports_send_failure(monkeypatch):
    def fake_call(method: str, *, token: str, body=None):
        if method == "auth.test":
            return {"ok": True, "team": "Workspace"}
        if method == "chat.postMessage":
            return {"ok": False, "error": "channel_not_found"}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr("assistant_agent_kanban.slack_settings_test.slack_api_call", fake_call)

    result = run_slack_settings_test(
        SlackConfig(
            enabled=True,
            socket_mode_enabled=False,
            bot_token="xoxb-test",
            default_channel="#missing",
        )
    )

    assert result.ok is False
    assert result.checks[-1].name == "send_test"
    assert "channel_not_found" in result.checks[-1].message


def test_slack_settings_test_still_verifies_send_without_app_token(monkeypatch):
    calls: list[str] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append(method)
        if method == "auth.test":
            return {"ok": True, "team": "Workspace"}
        if method == "chat.postMessage":
            return {"ok": True}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr("assistant_agent_kanban.slack_settings_test.slack_api_call", fake_call)

    result = run_slack_settings_test(
        SlackConfig(
            enabled=True,
            socket_mode_enabled=True,
            bot_token="xoxb-test",
            app_token=None,
            default_channel="#agent-alerts",
        )
    )

    assert result.ok is False
    assert "message was sent" in result.summary
    assert calls == ["auth.test", "chat.postMessage"]
    assert result.checks[-1].name == "receive_ready"
