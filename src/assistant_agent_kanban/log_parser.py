from __future__ import annotations

import json
import re


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def render_assistant_log(raw_content: str, *, debug: bool = False) -> str:
    rendered: list[str] = []
    for raw_line in raw_content.splitlines():
        chunk = render_assistant_event_line(raw_line, debug=debug)
        if chunk:
            rendered.append(chunk)
    return "\n\n".join(part.strip() for part in rendered if part.strip())


def render_assistant_event_line(raw_line: str, *, debug: bool = False) -> str | None:
    line = strip_ansi(raw_line).strip()
    if not line:
        return None
    try:
        payload = json.loads(raw_line.strip())
    except json.JSONDecodeError:
        return line
    event_type = payload.get("type")
    if isinstance(event_type, str) and (event_type.startswith("item.") or event_type.startswith("turn.") or event_type.startswith("thread.")):
        return render_codex_event_line(raw_line, debug=debug)
    return render_opencode_event_line(raw_line, debug=debug)


def render_opencode_log(raw_content: str, *, debug: bool = False) -> str:
    return render_assistant_log(raw_content, debug=debug)


def render_opencode_event_line(raw_line: str, *, debug: bool = False) -> str | None:
    line = strip_ansi(raw_line).strip()
    if not line:
        return None
    try:
        payload = json.loads(raw_line.strip())
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


def render_codex_event_line(raw_line: str, *, debug: bool = False) -> str | None:
    line = strip_ansi(raw_line).strip()
    if not line:
        return None
    try:
        payload = json.loads(raw_line.strip())
    except json.JSONDecodeError:
        return line
    event_type = payload.get("type")
    if event_type == "item.started":
        item = payload.get("item") or {}
        if isinstance(item, dict):
            item_type = item.get("type")
            if isinstance(item_type, str):
                return f"Started {item_type.replace('_', ' ')}"
    if event_type == "item.completed":
        item = payload.get("item") or {}
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    return text
            if item_type == "command_execution":
                command = item.get("command")
                if isinstance(command, str) and command.strip():
                    return f"Command completed: {command}"
            if item_type == "web_search":
                return "Web search completed"
            if isinstance(item_type, str) and debug:
                return f"Completed {item_type.replace('_', ' ')}"
    if event_type == "turn.completed" and debug:
        usage = payload.get("usage") or {}
        if isinstance(usage, dict):
            parts = ["Debug tokens"]
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    parts.append(f"{key}={value}")
            return " | ".join(parts) if len(parts) > 1 else None
    if event_type == "turn.failed":
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return f"ERROR: {message}"
        return "ERROR: turn failed"
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


def strip_ansi(value: str) -> str:
    return ANSI_ESCAPE_RE.sub("", value)
