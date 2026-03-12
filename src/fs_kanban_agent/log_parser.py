from __future__ import annotations

import json


def render_opencode_log(raw_content: str, *, debug: bool = False) -> str:
    rendered: list[str] = []
    for raw_line in raw_content.splitlines():
        chunk = render_opencode_event_line(raw_line, debug=debug)
        if chunk:
            rendered.append(chunk)
    return "\n\n".join(part.strip() for part in rendered if part.strip())


def render_opencode_event_line(raw_line: str, *, debug: bool = False) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line
    event_type = payload.get("type")
    if event_type == "step_start":
        return "Started agent step"
    if event_type == "text":
        part = payload.get("part") or {}
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and text.strip():
            return text
        return None
    if event_type == "tool_use":
        return _render_tool_use(payload, debug=debug)
    if event_type == "step_finish" and debug:
        return _render_step_finish(payload)
    if event_type == "final":
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content
        return None
    if event_type == "message" and payload.get("role") == "assistant":
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content
        return None
    if event_type == "error":
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return f"ERROR: {message}"
    return None


def _render_tool_use(payload: dict[str, object], *, debug: bool = False) -> str | None:
    part = payload.get("part")
    if not isinstance(part, dict):
        return None
    tool_name = part.get("tool")
    state = part.get("state")
    if not isinstance(tool_name, str) or not isinstance(state, dict):
        return None
    status = state.get("status")
    if status == "error":
        message = state.get("error")
        if isinstance(message, str) and message.strip():
            return f"Tool `{tool_name}` failed: {message}"
        return f"Tool `{tool_name}` failed"
    if status == "completed":
        return f"Tool `{tool_name}` completed"
    if status == "running":
        return f"Tool `{tool_name}` running"
    if isinstance(status, str) and status.strip():
        return f"Tool `{tool_name}` {status}"
    return f"Tool `{tool_name}` invoked"


def _render_step_finish(payload: dict[str, object]) -> str | None:
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    total = _int_field(tokens, "total")
    input_tokens = _int_field(tokens, "input")
    output_tokens = _int_field(tokens, "output")
    reasoning_tokens = _int_field(tokens, "reasoning")
    cached_tokens = _int_field(tokens, "cache", "read")
    parts = ["Debug tokens"]
    if total is not None:
        parts.append(f"total={total}")
    if input_tokens is not None:
        parts.append(f"input={input_tokens}")
    if output_tokens is not None:
        parts.append(f"output={output_tokens}")
    if reasoning_tokens is not None:
        parts.append(f"reasoning={reasoning_tokens}")
    if cached_tokens is not None:
        parts.append(f"cache_read={cached_tokens}")
    duration_ms = _int_field(payload, "durationMs")
    if duration_ms is not None:
        parts.append(f"duration_ms={duration_ms}")
    return " | ".join(parts) if len(parts) > 1 else None


def _int_field(payload: dict[str, object], *keys: str) -> int | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, int) else None
