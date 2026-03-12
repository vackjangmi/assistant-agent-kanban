from __future__ import annotations

from fs_kanban_agent.log_parser import render_opencode_event_line, render_opencode_log


def test_render_opencode_log_includes_tool_errors_as_readable_text():
    raw = "\n".join(
        [
            '{"type":"step_start"}',
            '{"type":"tool_use","part":{"tool":"read","state":{"status":"error","error":"Error: File not found"}}}',
        ]
    )

    rendered = render_opencode_log(raw)

    assert "Started agent step" in rendered
    assert "Tool `read` failed: Error: File not found" in rendered


def test_render_opencode_event_line_reports_tool_running_state():
    raw_line = '{"type":"tool_use","part":{"tool":"task","state":{"status":"running"}}}'

    assert render_opencode_event_line(raw_line) == "Tool `task` running"


def test_render_opencode_event_line_keeps_final_content():
    raw_line = '{"type":"final","content":"## Summary\\nplan"}'

    assert render_opencode_event_line(raw_line) == "## Summary\nplan"


def test_render_opencode_log_debug_includes_reasoning_token_metadata():
    raw = '{"type":"step_finish","tokens":{"total":42,"input":30,"output":12,"reasoning":7,"cache":{"read":5}},"durationMs":1800}'

    rendered = render_opencode_log(raw, debug=True)

    assert "Debug tokens" in rendered
    assert "reasoning=7" in rendered
    assert "cache_read=5" in rendered
    assert "duration_ms=1800" in rendered
