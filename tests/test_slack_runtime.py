from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

import assistant_agent_kanban.runtime as runtime_module
from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.request_draft_store import RequestDraftStore
from assistant_agent_kanban.slack_runtime import SlackRuntime

from .conftest import FakeAdapter, create_request_task


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


def test_slack_runtime_receive_test_ignores_other_channel_when_default_channel_name_is_configured(tmp_path, monkeypatch):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    config.slack.enabled = True
    config.slack.socket_mode_enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.app_token = "xapp-test"
    config.slack.app_mention_enabled = True
    config.slack.default_channel = "#agent-alerts"
    runtime = SlackRuntime(config, EventBus())

    async def fake_start_listener():
        runtime._listener_enabled = True
        runtime._listener_connected = True

    def fake_call(method: str, *, token: str, body=None):
        assert method == "conversations.info"
        assert body is not None
        channel = body["channel"]
        return {"ok": True, "channel": {"name": "agent-alerts" if channel == "C123" else "other-channel"}}

    runtime.start_listener = fake_start_listener  # type: ignore[method-assign]
    monkeypatch.setattr("assistant_agent_kanban.slack_channel_matcher.slack_api_call", fake_call)

    async def scenario():
        snapshot = await runtime.start_receive_test()
        receive_test = cast(dict[str, Any], snapshot["receive_test"])
        token = cast(str, receive_test["token"])
        await runtime._maybe_match_receive_test(
            {"team_id": "T123"},
            {"text": f"<@U123> verify {token}", "channel": "C999", "user": "U234"},
        )
        pending = runtime.snapshot()
        await runtime._maybe_match_receive_test(
            {"team_id": "T123"},
            {"text": f"<@U123> verify {token}", "channel": "C123", "user": "U234"},
        )
        return pending, runtime.snapshot()

    pending, updated = asyncio.run(scenario())
    pending_receive_test = cast(dict[str, Any], pending["receive_test"])
    updated_receive_test = cast(dict[str, Any], updated["receive_test"])
    assert pending_receive_test["status"] == "pending"
    assert updated_receive_test["status"] == "received"
    assert updated_receive_test["channel"] == "C123"


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

    async def fake_handler(payload: dict[str, Any]) -> dict[str, object] | None:
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


def test_slack_runtime_routes_app_mentions_to_handler(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def fake_mention_handler(inner_payload: dict[str, Any], event: dict[str, Any]) -> None:
        seen.append((inner_payload, event))

    runtime = SlackRuntime(config, EventBus(), mention_handler=fake_mention_handler)

    async def scenario():
        await runtime._handle_socket_payload(
            {
                "type": "events_api",
                "payload": {
                    "team_id": "T123",
                    "event": {"type": "app_mention", "channel": "C123", "ts": "173.456", "text": "<@U1> hi"},
                },
            }
        )

    asyncio.run(scenario())

    assert seen
    assert seen[0][0]["team_id"] == "T123"
    assert seen[0][1]["channel"] == "C123"


def test_slack_runtime_returns_modal_error_ack_payload(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban", repo_root=tmp_path / "repo")

    async def fake_handler(payload: dict[str, Any]) -> dict[str, object] | None:
        return {"response_action": "errors", "errors": {"request_intake_assistant_prompt": "Generate a draft first."}}

    runtime = SlackRuntime(config, EventBus(), action_handler=fake_handler)

    result = asyncio.run(
        runtime._handle_socket_payload(
            {
                "type": "interactive",
                "payload": {
                    "type": "view_submission",
                    "view": {"callback_id": "request_intake_modal"},
                },
            }
        )
    )

    assert result == {"response_action": "errors", "errors": {"request_intake_assistant_prompt": "Generate a draft first."}}


def test_slack_request_draft_flow_posts_thread_review_without_creating_task_before_submit(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "seed-task")
    runtime, calls = _build_slack_request_runtime(
        config,
        monkeypatch,
        draft_replies=[
            {
                "reply": "Here is a tighter request draft for review.",
                "field_updates": {
                    "title": "Slack drafted request",
                    "goal": "Keep drafting in the Slack thread until final submit.",
                },
            }
        ],
    )

    asyncio.run(runtime.handle_slack_app_mention({"team_id": "T123"}, {"channel": "C123", "ts": "173.456", "text": "<@U1> help"}))
    intro_message = cast(dict[str, Any], _latest_call_body(calls, "chat.postMessage"))
    assert intro_message is not None
    assert intro_message["text"] == "Ask the request-writing assistant to draft a request in this Slack thread."
    assert intro_message["blocks"][1]["elements"][0]["text"]["text"] == "Draft request with assistant"

    draft_id = _open_slack_request_modal(runtime, calls)
    before = sorted(path.name for path in config.state_dir(TaskState.REQUESTS).iterdir())
    generate_result = _generate_slack_request_draft(
        runtime,
        draft_id,
        prompt="Please tighten this request.",
        target_repo=str(config.repo_root),
    )

    assert generate_result == {"status": "success"}
    after = sorted(path.name for path in config.state_dir(TaskState.REQUESTS).iterdir())
    assert after == before

    placeholder_post = cast(
        dict[str, Any],
        next(body for method, _token, body in calls if method == "chat.postMessage" and body is not None and body.get("text") == "Writing request draft…"),
    )
    assert placeholder_post["thread_ts"] == "173.456"
    assert "응답 작성중" in placeholder_post["blocks"][0]["text"]["text"]

    upload_call = cast(
        dict[str, Any],
        next(body for method, _token, body in calls if method == "slack_upload_file_to_thread"),
    )
    assert upload_call["thread_ts"] == "173.456"
    assert upload_call["filename"] == "REQUEST-DRAFT-001.md"
    assert "Slack drafted request" in upload_call["content"]

    review_update = cast(
        dict[str, Any],
        next(
            body
            for method, _token, body in calls
            if method == "chat.update" and body is not None and body.get("text") == "Assistant draft 1 ready for review."
        ),
    )
    assert review_update["channel"] == "C123"
    assert review_update["blocks"][0]["text"]["text"] == "📝 *Assistant draft 1 ready for review*"
    assert review_update["blocks"][-1]["elements"][0]["text"]["text"] == "Submit final request"
    assert review_update["blocks"][-1]["elements"][1]["text"]["text"] == "Request another draft"

    draft = RequestDraftStore(config).load(draft_id)
    assert [entry.role for entry in draft.transcript] == ["user", "assistant"]


def test_handle_slack_app_mention_ignores_non_default_channel(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.default_channel = "C999"
    runtime, calls = _build_slack_request_runtime(config, monkeypatch, draft_replies=[])

    asyncio.run(runtime.handle_slack_app_mention({"team_id": "T123"}, {"channel": "C123", "ts": "173.456", "text": "<@U1> help"}))

    assert not [method for method, _token, _body in calls if method == "chat.postMessage"]


def test_handle_slack_app_mention_accepts_configured_default_channel(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.default_channel = "C123"
    runtime, calls = _build_slack_request_runtime(config, monkeypatch, draft_replies=[])

    asyncio.run(runtime.handle_slack_app_mention({"team_id": "T123"}, {"channel": "C123", "ts": "173.456", "text": "<@U1> help"}))

    intro_message = cast(dict[str, Any], _latest_call_body(calls, "chat.postMessage"))
    assert intro_message is not None
    assert intro_message["channel"] == "C123"


def test_handle_slack_app_mention_accepts_configured_default_channel_name(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.default_channel = "#agent-alerts"
    runtime, calls = _build_slack_request_runtime(config, monkeypatch, draft_replies=[])

    def fake_call(method: str, *, token: str, body=None):
        assert method == "conversations.info"
        return {"ok": True, "channel": {"name": "agent-alerts"}}

    monkeypatch.setattr("assistant_agent_kanban.slack_channel_matcher.slack_api_call", fake_call)

    asyncio.run(runtime.handle_slack_app_mention({"team_id": "T123"}, {"channel": "C123", "ts": "173.456", "text": "<@U1> help"}))

    intro_message = cast(dict[str, Any], _latest_call_body(calls, "chat.postMessage"))
    assert intro_message is not None
    assert intro_message["channel"] == "C123"


def test_slack_request_draft_flow_supports_revise_loop_and_parent_message_update(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "seed-task")
    runtime, calls = _build_slack_request_runtime(
        config,
        monkeypatch,
        draft_replies=[
            {
                "reply": "Draft 1 is ready.",
                "field_updates": {
                    "title": "Slack drafted request",
                    "goal": "First draft goal.",
                },
            },
            {
                "reply": "Draft 2 reflects the revision.",
                "field_updates": {
                    "goal": "Revised goal for the final submission.",
                    "base_branch": "main",
                },
            },
        ],
    )

    draft_id = _open_slack_request_modal(runtime, calls)
    _generate_slack_request_draft(
        runtime,
        draft_id,
        prompt="Please draft this request.",
        target_repo=str(config.repo_root),
    )
    revise_result = _request_another_draft(runtime, draft_id)
    assert revise_result == {"status": "opened_modal", "clear_buttons": False}
    _generate_slack_request_draft(
        runtime,
        draft_id,
        prompt="Revise it to be more specific.",
        target_repo=str(config.repo_root),
    )

    draft = RequestDraftStore(config).load(draft_id)
    assert len(draft.transcript) == 4
    assert sum(1 for entry in draft.transcript if entry.role == "assistant") == 2
    assert len([
        body
        for method, _token, body in calls
        if method == "chat.update"
        and body
        and str(body.get("text", "")).startswith("Assistant draft")
        and isinstance(body.get("blocks"), list)
        and body["blocks"]
        and isinstance(body["blocks"][-1], dict)
        and body["blocks"][-1].get("type") == "actions"
    ]) == 2

    cleared_revise_message = cast(
        dict[str, Any],
        next(body for method, _token, body in calls if method == "chat.update" and body is not None and body.get("ts") == "msg-revise"),
    )
    assert cleared_revise_message["blocks"] == [{"type": "section", "text": {"type": "mrkdwn", "text": "draft"}}]

    submit_result = _submit_slack_request(runtime, draft_id)
    assert submit_result == {"status": "success"}
    assert not RequestDraftStore(config).exists(draft_id)

    tasks = sorted(path.name for path in config.state_dir(TaskState.REQUESTS).iterdir())
    assert len(tasks) == 2
    request_markdowns = [(path / "REQUEST.md").read_text() for path in config.state_dir(TaskState.REQUESTS).iterdir()]
    assert any("Revised goal for the final submission." in markdown for markdown in request_markdowns)

    parent_update = cast(
        dict[str, Any],
        next(
            body
            for method, _token, body in calls
            if method == "chat.update" and body is not None and body.get("ts") == "173.456"
        ),
    )
    assert parent_update["channel"] == "C123"
    assert parent_update["blocks"][0]["text"]["text"] == "🧩 Slack drafted request"
    assert "Task opened in Slack thread" in parent_update["blocks"][1]["text"]["text"]

    cleared_submit_message = cast(
        dict[str, Any],
        next(body for method, _token, body in calls if method == "chat.update" and body is not None and body.get("ts") == "msg-submit"),
    )
    assert cleared_submit_message["blocks"] == [{"type": "section", "text": {"type": "mrkdwn", "text": "draft"}}]


def test_slack_request_draft_flow_posts_summary_in_thread_when_parent_update_fails(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "seed-task")
    runtime, calls = _build_slack_request_runtime(
        config,
        monkeypatch,
        draft_replies=[
            {
                "reply": "Fallback draft is ready.",
                "field_updates": {
                    "title": "Slack fallback request",
                    "goal": "Fallback goal.",
                },
            }
        ],
        update_parent_ok=False,
    )

    draft_id = _open_slack_request_modal(runtime, calls)
    _generate_slack_request_draft(
        runtime,
        draft_id,
        prompt="Please draft this request.",
        target_repo=str(config.repo_root),
    )

    submit_result = _submit_slack_request(runtime, draft_id)
    assert submit_result == {"status": "success"}

    assert any(method == "chat.update" and body is not None and body.get("ts") == "173.456" for method, _token, body in calls)
    fallback_post = cast(
        dict[str, Any],
        next(
            body
            for method, _token, body in calls
            if method == "chat.postMessage"
            and body is not None
            and body.get("thread_ts") == "173.456"
            and _is_parent_summary_post(body)
        ),
    )
    assert fallback_post["blocks"][0]["text"]["text"] == "🧩 Slack fallback request"


def test_slack_request_draft_flow_posts_review_message_when_placeholder_update_fails(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "seed-task")
    runtime, calls = _build_slack_request_runtime(
        config,
        monkeypatch,
        draft_replies=[
            {
                "reply": "Draft fallback is ready.",
                "field_updates": {
                    "title": "Slack fallback draft",
                    "goal": "Fallback review message goal.",
                },
            }
        ],
    )

    original_call = runtime_module.slack_api_call

    def fail_placeholder_update(method: str, *, token: str, body=None):
        if method == "chat.update" and isinstance(body, dict) and body.get("text") == "Assistant draft 1 ready for review.":
            return {"ok": False, "error": "cant_update_message"}
        return original_call(method, token=token, body=body)

    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fail_placeholder_update)

    draft_id = _open_slack_request_modal(runtime, calls)
    result = _generate_slack_request_draft(
        runtime,
        draft_id,
        prompt="Please draft this request.",
        target_repo=str(config.repo_root),
    )

    assert result == {"status": "success"}
    fallback_review_post = cast(
        dict[str, Any],
        next(
            body
            for method, _token, body in calls
            if method == "chat.postMessage" and body is not None and body.get("text") == "Assistant draft 1 ready for review."
        ),
    )
    assert len([body for method, _token, body in calls if method == "slack_upload_file_to_thread"]) == 1
    assert fallback_review_post["thread_ts"] == "173.456"
    assert fallback_review_post["blocks"][-1]["elements"][0]["text"]["text"] == "Submit final request"
    assert fallback_review_post["blocks"][-1]["elements"][1]["text"]["text"] == "Request another draft"


def test_slack_request_draft_flow_logs_when_review_fallback_post_fails(configured_paths, monkeypatch, caplog):
    config, _, _ = configured_paths
    create_request_task(config, "seed-task")
    runtime, calls = _build_slack_request_runtime(
        config,
        monkeypatch,
        draft_replies=[
            {
                "reply": "Draft fallback is ready.",
                "field_updates": {
                    "title": "Slack fallback draft",
                    "goal": "Fallback review message goal.",
                },
            }
        ],
    )

    original_call = runtime_module.slack_api_call

    def fail_update_and_post(method: str, *, token: str, body=None):
        if method == "chat.update" and isinstance(body, dict) and body.get("text") == "Assistant draft 1 ready for review.":
            return {"ok": False, "error": "cant_update_message"}
        if method == "chat.postMessage" and isinstance(body, dict) and body.get("text") == "Assistant draft 1 ready for review.":
            return {"ok": False, "error": "missing_scope"}
        return original_call(method, token=token, body=body)

    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fail_update_and_post)

    draft_id = _open_slack_request_modal(runtime, calls)
    with caplog.at_level(logging.WARNING):
        result = _generate_slack_request_draft(
            runtime,
            draft_id,
            prompt="Please draft this request.",
            target_repo=str(config.repo_root),
        )

    assert result == {"status": "success"}
    assert "slack request draft placeholder update failed" in caplog.text
    assert "slack request draft review post failed" in caplog.text


def test_slack_request_draft_review_blocks_clamp_oversized_text(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    runtime, _calls = _build_slack_request_runtime(
        config,
        monkeypatch,
        draft_replies=[
            {
                "reply": "unused",
                "field_updates": {},
            }
        ],
    )
    draft = RequestDraftStore(config).create(
        {
            "title": "T" * 5000,
            "goal": "G" * 5000,
            "target_repo": "/tmp/" + ("p" * 5000),
            "base_branch": "b" * 5000,
        }
    )
    field_updates = {
        "title": "x" * 5000,
        "goal": "y" * 5000,
        "background": "z" * 5000,
        "scope": ["a" * 2000, "b" * 2000, "c" * 2000],
    }

    blocks = runtime._build_slack_request_draft_review_blocks(
        draft,
        draft_number=1,
        reply="r" * 5000,
        field_updates=field_updates,
        draft_filename="REQUEST-DRAFT-001.md",
        upload_ok=True,
    )

    for block in blocks:
        if block.get("type") == "section" and isinstance(block.get("text"), dict):
            assert len(block["text"]["text"]) <= 3000
        if block.get("type") == "section" and isinstance(block.get("fields"), list):
            for field in block["fields"]:
                assert len(field["text"]) <= 2000


def _build_slack_request_runtime(config, monkeypatch, *, draft_replies, update_parent_ok=True):
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    replies = list(draft_replies)
    post_counter = {"value": 0}

    class FakeDraftResult:
        def __init__(self, reply, field_updates):
            self.reply = reply
            self.field_updates = field_updates

    def fake_draft_request(*, config, adapter_registry, payload):
        response = replies.pop(0)
        return FakeDraftResult(response["reply"], response["field_updates"])

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        if method in {"views.open", "views.update"}:
            return {"ok": True}
        if method == "chat.update":
            return {"ok": update_parent_ok}
        if method == "chat.postMessage":
            post_counter["value"] += 1
            return {"ok": True, "channel": body.get("channel") if isinstance(body, dict) else None, "ts": f"msg-{post_counter['value']}"}
        return {"ok": True}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        calls.append(("slack_upload_file_to_thread", token, {"channel": channel_id, "thread_ts": thread_ts, "filename": filename, "title": title, "content": content.decode("utf-8")}))
        return {"ok": True, "file": {"id": f"F{post_counter['value'] + 1}"}}

    monkeypatch.setattr("assistant_agent_kanban.runtime.draft_request", fake_draft_request)
    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_upload_file_to_thread", fake_upload)
    return runtime, calls


def _open_slack_request_modal(runtime, calls):
    result = asyncio.run(
        runtime.handle_slack_interactive_action(
            {
                "type": "block_actions",
                "trigger_id": "trigger-1",
                "user": {"id": "U123"},
                "team": {"id": "T123"},
                "channel": {"id": "C123"},
                "message": {"ts": "173.456", "thread_ts": "173.456"},
                "actions": [{"action_id": "open_request_intake", "value": '{"action":"open_request_intake"}'}],
            }
        )
    )
    assert result == {"status": "opened_modal", "clear_buttons": False}
    open_call = cast(dict[str, Any], next(body for method, _token, body in calls if method == "views.open"))
    assert open_call is not None
    assert open_call["view"]["title"]["text"] == "Draft request"
    assert open_call["view"]["submit"]["text"] == "Post draft to thread"
    return json.loads(open_call["view"]["private_metadata"])["draft_id"]


def _generate_slack_request_draft(runtime, draft_id, *, prompt, target_repo):
    async def scenario():
        result = await runtime.handle_slack_interactive_action(
            {
                "type": "view_submission",
                "user": {"id": "U123"},
                "view": {
                    "callback_id": "request_intake_modal",
                    "private_metadata": json.dumps({"draft_id": draft_id}),
                    "state": _slack_request_intake_state(prompt=prompt, target_repo=target_repo),
                },
            }
        )
        current_loop = asyncio.get_running_loop()
        detached = [
            task
            for task in runtime._background_tasks
            if task.get_name().startswith("fs-kanban-slack-request-draft-") and not task.done() and task.get_loop() is current_loop
        ]
        if detached:
            await asyncio.gather(*detached)
        return result

    return asyncio.run(scenario())


def _request_another_draft(runtime, draft_id):
    return asyncio.run(
        runtime.handle_slack_interactive_action(
            {
                "type": "block_actions",
                "trigger_id": "trigger-revise",
                "user": {"id": "U123"},
                "team": {"id": "T123"},
                "channel": {"id": "C123"},
                "message": {
                    "ts": "msg-revise",
                    "text": "Assistant draft 1 ready for review.",
                    "blocks": [
                        {"type": "section", "text": {"type": "mrkdwn", "text": "draft"}},
                        {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Request another draft"}}]},
                    ],
                },
                "actions": [{"action_id": "request_intake_revise", "value": json.dumps({"draft_id": draft_id})}],
            }
        )
    )


def _submit_slack_request(runtime, draft_id):
    return asyncio.run(
        runtime.handle_slack_interactive_action(
            {
                "type": "block_actions",
                "user": {"id": "U123"},
                "channel": {"id": "C123"},
                "message": {
                    "ts": "msg-submit",
                    "text": "Assistant draft ready for review.",
                    "blocks": [
                        {"type": "section", "text": {"type": "mrkdwn", "text": "draft"}},
                        {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Submit final request"}}]},
                    ],
                },
                "actions": [{"action_id": "request_intake_submit", "value": json.dumps({"draft_id": draft_id})}],
            }
        )
    )


def _slack_request_intake_state(*, prompt, target_repo):
    return {
        "values": {
            "request_intake_project": {
                "project_select": {"selected_option": {"value": target_repo}},
            },
            "request_intake_base_branch": {
                "base_branch_input": {"value": "main"},
            },
            "request_intake_assistant_prompt": {
                "assistant_prompt_input": {"value": prompt},
            },
        }
    }


def _latest_call_body(calls, method_name):
    for method, _token, body in reversed(calls):
        if method == method_name:
            return body
    return None


def _is_parent_summary_post(body: dict[str, object]) -> bool:
    blocks = body.get("blocks")
    return isinstance(blocks, list) and bool(blocks) and isinstance(blocks[0], dict) and blocks[0].get("type") == "header"
