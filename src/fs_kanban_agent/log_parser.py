from __future__ import annotations

import json


def render_opencode_log(raw_content: str) -> str:
    rendered: list[str] = []
    for raw_line in raw_content.splitlines():
        chunk = render_opencode_event_line(raw_line)
        if chunk:
            rendered.append(chunk)
    return "\n\n".join(part.strip() for part in rendered if part.strip())


def render_opencode_event_line(raw_line: str) -> str | None:
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
        return _render_tool_use(payload)
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


def _render_tool_use(payload: dict[str, object]) -> str | None:
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
