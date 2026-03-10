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
    if event_type == "text":
        part = payload.get("part") or {}
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and text.strip():
            return text
        return None
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
