from __future__ import annotations

import asyncio
from typing import Any, cast

from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.slack_runtime import SlackRuntime


def test_slack_runtime_start_receive_test_requires_mentions(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    config.slack.enabled = True
    config.slack.socket_mode_enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.app_token = "xapp-test"
    runtime = SlackRuntime(config, EventBus())

    try:
        asyncio.run(runtime.start_receive_test())
    except RuntimeError as exc:
        assert "Enable app mentions" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_slack_runtime_matches_receive_test_token(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    config.slack.enabled = True
    config.slack.socket_mode_enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.app_token = "xapp-test"
    config.slack.app_mention_enabled = True
    runtime = SlackRuntime(config, EventBus())

    async def fake_start_listener():
        runtime._listener_enabled = True
        runtime._listener_connected = True

    runtime.start_listener = fake_start_listener  # type: ignore[method-assign]
    async def scenario():
        snapshot = await runtime.start_receive_test()
        receive_test = cast(dict[str, Any], snapshot["receive_test"])
        token = cast(str, receive_test["token"])
        await runtime._maybe_match_receive_test(
            {"team_id": "T123"},
            {
                "text": f"<@U123> verify {token}",
                "channel": "C123",
                "user": "U234",
            },
        )
        return runtime.snapshot()

    updated = asyncio.run(scenario())
    updated_receive_test = cast(dict[str, Any], updated["receive_test"])
    assert updated_receive_test["status"] == "received"
    assert updated_receive_test["channel"] == "C123"
    assert updated_receive_test["user"] == "U234"


def test_slack_runtime_restart_if_running_captures_invalid_config(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    config.slack.enabled = True
    config.slack.socket_mode_enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.app_token = "xapp-test"
    runtime = SlackRuntime(config, EventBus())

    async def sleeper():
        await asyncio.sleep(3600)

    async def scenario():
        runtime._listener_task = asyncio.create_task(sleeper())
        config.slack.enabled = False
        await runtime.restart_if_running()
        return runtime.snapshot()

    snapshot = asyncio.run(scenario())
    assert snapshot["listener_connected"] is False
    assert snapshot["listener_last_error"] == "Slack is disabled."


def test_slack_runtime_handles_block_actions(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    seen: list[dict[str, Any]] = []

    async def fake_handler(payload: dict[str, Any]) -> str | None:
        seen.append(payload)
        return None

    runtime = SlackRuntime(config, EventBus(), action_handler=fake_handler)

    async def scenario():
        await runtime._handle_socket_payload(
            {
                "type": "interactive",
                "payload": {
                    "type": "block_actions",
                    "actions": [{"action_id": "approve_verification", "value": '{"task_id":"task-1"}'}],
                },
            }
        )

    asyncio.run(scenario())
    assert seen
    assert seen[0]["type"] == "block_actions"


def test_slack_runtime_posts_error_for_failed_block_action(tmp_path, monkeypatch):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    config.slack.bot_token = "xoxb-test"
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    async def fake_handler(payload: dict[str, Any]) -> dict[str, object] | None:
        return {"status": "error", "message": "approval is blocked"}

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_runtime.slack_api_call", fake_call)
    runtime = SlackRuntime(config, EventBus(), action_handler=fake_handler)

    async def scenario():
        await runtime._handle_socket_payload(
            {
                "type": "interactive",
                "payload": {
                    "type": "block_actions",
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "173.456", "ts": "173.789"},
                    "actions": [{"action_id": "approve_verification", "value": '{"task_id":"task-1"}'}],
                },
            }
        )

    asyncio.run(scenario())
    assert calls
    method, _, payload = calls[0]
    assert method == "chat.postMessage"
    assert len(calls) == 1
    assert payload is not None
    assert payload["channel"] == "C123"
    assert payload["thread_ts"] == "173.456"
    assert "approval is blocked" in str(payload["text"])


def test_slack_runtime_clears_buttons_after_successful_block_action(tmp_path, monkeypatch):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    config.slack.bot_token = "xoxb-test"
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    async def fake_handler(payload: dict[str, Any]) -> dict[str, object] | None:
        return {"status": "success", "clear_buttons": True}

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_runtime.slack_api_call", fake_call)
    runtime = SlackRuntime(config, EventBus(), action_handler=fake_handler)

    async def scenario():
        await runtime._handle_socket_payload(
            {
                "type": "interactive",
                "payload": {
                    "type": "block_actions",
                    "channel": {"id": "C123"},
                    "message": {"ts": "173.789", "text": "Original message", "blocks": [{"type": "actions"}]},
                    "actions": [{"action_id": "approve_verification", "value": '{"task_id":"task-1"}'}],
                },
            }
        )

    asyncio.run(scenario())
    assert calls
    method, _, payload = calls[0]
    assert method == "chat.update"
    assert len(calls) == 1
    assert payload is not None
    assert payload["channel"] == "C123"
    assert payload["ts"] == "173.789"
    assert payload["text"] == "Original message"
    assert payload["blocks"] == []
