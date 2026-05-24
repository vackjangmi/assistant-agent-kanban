from __future__ import annotations

from assistant_agent_kanban.log_parser import render_assistant_log, render_opencode_event_line, render_opencode_log


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


def test_render_assistant_log_includes_claude_assistant_text_content():
    raw = '\n'.join(
        [
            '===== phase=run cycle=5 =====',
            '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"partial"}}}',
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Verdict: PASS\\n\\n## 요약"}]}}',
        ]
    )

    rendered = render_assistant_log(raw)

    assert "===== phase=run cycle=5 =====" in rendered
    assert "Verdict: PASS" in rendered
    assert "## 요약" in rendered
    assert "partial" not in rendered


def test_render_assistant_log_includes_claude_result_text():
    raw = '{"type":"result","subtype":"success","is_error":true,"result":"Not logged in · Please run /login","terminal_reason":"completed"}'

    assert render_assistant_log(raw) == "Not logged in · Please run /login"


def test_render_assistant_log_keeps_json_scalar_lines_as_text():
    raw = '\n'.join(
        [
            '```json',
            '"verdict"',
            '"PASS"',
            '```',
        ]
    )

    rendered = render_assistant_log(raw)

    assert '"verdict"' in rendered
    assert '"PASS"' in rendered


def test_render_assistant_log_debug_includes_claude_tool_and_token_metadata():
    raw = '\n'.join(
        [
            '{"type":"stream_event","event":{"type":"content_block_start","content_block":{"type":"tool_use","name":"Bash"}}}',
            '{"type":"stream_event","event":{"type":"message_delta","usage":{"input_tokens":3,"output_tokens":5,"cache_read_input_tokens":7}}}',
        ]
    )

    rendered = render_assistant_log(raw, debug=True)

    assert "Tool `Bash` invoked" in rendered
    assert "input_tokens=3" in rendered
    assert "output_tokens=5" in rendered
    assert "cache_read_input_tokens=7" in rendered
