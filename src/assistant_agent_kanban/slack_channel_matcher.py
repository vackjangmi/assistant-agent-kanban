from __future__ import annotations

from .slack_api import slack_api_call


def slack_channel_matches_config(*, token: str | None, configured_channel: str | None, actual_channel_id: str | None) -> bool:
    if not actual_channel_id:
        return False
    if not configured_channel:
        return True
    normalized_config = configured_channel.strip()
    if not normalized_config:
        return True
    if actual_channel_id == normalized_config:
        return True
    configured_name = _configured_channel_name(normalized_config)
    if configured_name is None or not token:
        return False
    payload = slack_api_call("conversations.info", token=token, body={"channel": actual_channel_id})
    channel = payload.get("channel")
    if not isinstance(channel, dict):
        return False
    actual_name = channel.get("name")
    return isinstance(actual_name, str) and actual_name.casefold() == configured_name.casefold()


def _configured_channel_name(value: str) -> str | None:
    stripped = value[1:] if value.startswith("#") else value
    if not stripped:
        return None
    if _looks_like_channel_id(value):
        return None
    return stripped


def _looks_like_channel_id(value: str) -> bool:
    return len(value) >= 3 and value[0] in {"C", "G", "D"} and value.upper() == value
