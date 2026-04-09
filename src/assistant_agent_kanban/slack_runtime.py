from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from .config import AppConfig
from .events import EventBus
from .models import WorkerEvent, utc_now
from .slack_api import slack_api_call, slack_error_message


@dataclass(slots=True)
class SlackReceiveTestSession:
    token: str
    status: str
    created_at: str
    expires_at: str
    instruction: str
    received_at: str | None = None
    channel: str | None = None
    user: str | None = None
    text: str | None = None
    team_id: str | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


class SlackRuntime:
    def __init__(self, config: AppConfig, events: EventBus) -> None:
        self.config = config
        self.events = events
        self._listener_task: asyncio.Task[None] | None = None
        self._listener_stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._listener_connected = False
        self._listener_enabled = False
        self._listener_last_error: str | None = None
        self._last_event_at: str | None = None
        self._last_event_type: str | None = None
        self._last_event_channel: str | None = None
        self._pending_receive_test: SlackReceiveTestSession | None = None

    async def stop(self) -> None:
        self._listener_enabled = False
        self._listener_connected = False
        self._listener_stop.set()
        task = self._listener_task
        self._listener_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def restart_if_running(self) -> None:
        should_restart = self._listener_task is not None
        await self.stop()
        if should_restart:
            try:
                await self.start_listener()
            except RuntimeError as exc:
                self._listener_last_error = str(exc)
                await self._publish_status_event()

    async def start_listener(self) -> None:
        self._validate_listener_config()
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._listener_stop = asyncio.Event()
        self._listener_enabled = True
        self._listener_last_error = None
        self._listener_task = asyncio.create_task(self._run_forever(), name="fs-kanban-slack-listener")

    async def start_receive_test(self) -> dict[str, object]:
        self._validate_receive_test_config()
        await self.start_listener()
        now = utc_now()
        token = secrets.token_hex(3)
        session = SlackReceiveTestSession(
            token=token,
            status="pending",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            instruction=f"Mention the Slack app in a channel where it is already present and include this token: {token}",
        )
        async with self._lock:
            self._pending_receive_test = session
        await self._publish_status_event()
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        session = self._current_receive_test()
        return {
            "listener_enabled": self._listener_enabled,
            "listener_connected": self._listener_connected,
            "listener_last_error": self._listener_last_error,
            "last_event_at": self._last_event_at,
            "last_event_type": self._last_event_type,
            "last_event_channel": self._last_event_channel,
            "receive_test": session.to_payload() if session is not None else None,
        }

    async def _run_forever(self) -> None:
        while not self._listener_stop.is_set():
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._listener_connected = False
                self._listener_last_error = str(exc)
                await self._publish_status_event()
                if self._listener_stop.is_set():
                    return
                await asyncio.sleep(1)

    async def _connect_once(self) -> None:
        app_token = self.config.slack.app_token
        if not app_token:
            raise RuntimeError("Slack app token is missing.")
        connection = await asyncio.to_thread(slack_api_call, "apps.connections.open", token=app_token)
        if not connection.get("ok"):
            raise RuntimeError(slack_error_message(connection, fallback="Slack apps.connections.open failed."))
        socket_url = connection.get("url")
        if not isinstance(socket_url, str) or not socket_url:
            raise RuntimeError("Slack did not return a Socket Mode URL.")
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets dependency is required for Slack receive tests.") from exc
        async with websockets.connect(socket_url, ping_interval=20, ping_timeout=20, close_timeout=5) as websocket:
            self._listener_connected = True
            self._listener_last_error = None
            await self._publish_status_event()
            while not self._listener_stop.is_set():
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                except TimeoutError:
                    continue
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                payload = json.loads(raw_message)
                await self._ack_envelope(websocket, payload)
                await self._handle_socket_payload(payload)
        self._listener_connected = False
        await self._publish_status_event()

    async def _ack_envelope(self, websocket: Any, payload: dict[str, Any]) -> None:
        envelope_id = payload.get("envelope_id")
        if not envelope_id:
            return
        await websocket.send(json.dumps({"envelope_id": envelope_id}))

    async def _handle_socket_payload(self, payload: dict[str, Any]) -> None:
        if payload.get("type") != "events_api":
            return
        inner_payload = payload.get("payload") or {}
        event = inner_payload.get("event") or {}
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return
        self._last_event_type = event_type
        self._last_event_at = utc_now().isoformat()
        self._last_event_channel = event.get("channel") if isinstance(event.get("channel"), str) else None
        if event_type == "app_mention":
            await self._maybe_match_receive_test(inner_payload, event)
        await self._publish_status_event()

    async def _maybe_match_receive_test(self, inner_payload: dict[str, Any], event: dict[str, Any]) -> None:
        session = self._current_receive_test()
        if session is None or session.status != "pending":
            return
        text = event.get("text")
        if not isinstance(text, str) or session.token not in text:
            return
        now = utc_now().isoformat()
        async with self._lock:
            current = self._current_receive_test()
            if current is None or current.status != "pending" or current.token not in text:
                return
            current.status = "received"
            current.received_at = now
            current.channel = event.get("channel") if isinstance(event.get("channel"), str) else None
            current.user = event.get("user") if isinstance(event.get("user"), str) else None
            current.text = text
            current.team_id = inner_payload.get("team_id") if isinstance(inner_payload.get("team_id"), str) else None
            current.error = None

    def _current_receive_test(self) -> SlackReceiveTestSession | None:
        session = self._pending_receive_test
        if session is None:
            return None
        expires_at = session.expires_at
        if expires_at and datetime.fromisoformat(expires_at) <= utc_now() and session.status == "pending":
            session.status = "expired"
            session.error = "Timed out waiting for a matching Slack mention."
        return session

    async def _publish_status_event(self) -> None:
        await self.events.publish(WorkerEvent(event="slack_receive_test_updated", payload=self.snapshot()))

    def _validate_listener_config(self) -> None:
        if not self.config.slack.enabled:
            raise RuntimeError("Slack is disabled.")
        if not self.config.slack.socket_mode_enabled:
            raise RuntimeError("Socket Mode must be enabled for receive tests.")
        if not self.config.slack.bot_token:
            raise RuntimeError("Slack bot token is missing.")
        if not self.config.slack.app_token:
            raise RuntimeError("Slack app token is missing.")

    def _validate_receive_test_config(self) -> None:
        self._validate_listener_config()
        if not self.config.slack.app_mention_enabled:
            raise RuntimeError("Enable app mentions before starting a receive test.")
