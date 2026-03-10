from __future__ import annotations

from fs_kanban_agent.opencode_adapter import _extract_assistant_text


def test_extract_assistant_text_prefers_final_event():
    stdout = "\n".join(
        [
            '{"type":"message","role":"assistant","content":"draft"}',
            '{"type":"final","content":"final answer"}',
        ]
    )

    assert _extract_assistant_text(stdout) == "final answer"
