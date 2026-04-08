from __future__ import annotations

from typing import cast

import subprocess

from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.gemini_adapter import GEMINI_KNOWN_MODELS, SubprocessGeminiAdapter, _extract_assistant_text, _extract_session_id, _extract_total_tokens


def test_extract_assistant_text_reads_message_content():
    stdout = "\n".join(
        [
            '{"type":"tool_use","name":"read"}',
            '{"type":"message","role":"assistant","content":"## Summary\\n\\nready"}',
        ]
    )

    assert _extract_assistant_text(stdout) == "## Summary\n\nready"


def test_extract_session_id_reads_nested_result_value():
    stdout = "\n".join(
        [
            '{"type":"run.started","result":{"session_id":"session-123"}}',
            '{"type":"message","content":"ok"}',
        ]
    )

    assert _extract_session_id(stdout) == "session-123"


def test_extract_total_tokens_reads_usage_total_tokens():
    stdout = "\n".join(
        [
            '{"type":"message","content":"draft"}',
            '{"type":"result","usage":{"totalTokens":42}}',
            '{"type":"result","usage":{"total_tokens":8}}',
        ]
    )

    assert _extract_total_tokens(stdout) == 50


def test_extract_assistant_text_aggregates_assistant_deltas_in_order():
    stdout = "\n".join(
        [
            '{"type":"message","role":"assistant","content":"## Summary\\n","delta":true}',
            '{"type":"message","role":"assistant","content":"full artifact\\n\\n","delta":true}',
            '{"type":"tool_use","name":"read"}',
            '{"type":"message","role":"assistant","content":"## Scope\\n- scoped","delta":true}',
        ]
    )

    assert _extract_assistant_text(stdout) == "## Summary\nfull artifact\n\n## Scope\n- scoped"


def test_gemini_adapter_exposes_known_models(tmp_path):
    adapter = SubprocessGeminiAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "gemini"
    config.bootstrap()

    assert adapter.discover_models(config=config) == GEMINI_KNOWN_MODELS


def test_extract_assistant_text_reads_nested_result_content():
    stdout = "\n".join(
        [
            '{"type":"tool_use","name":"read"}',
            '{"type":"result","result":{"message":{"parts":[{"text":"final markdown"}]}}}',
        ]
    )

    assert _extract_assistant_text(stdout) == "final markdown"


def test_gemini_adapter_builds_noninteractive_command(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = [
                '{"type":"run.started","session_id":"gem-session"}\n',
                '{"type":"message","role":"assistant","content":"ok"}\n',
                '{"type":"result","usage":{"totalTokens":12}}\n',
            ]
            self.stderr = []

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        recorded["cwd"] = kwargs.get("cwd")
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessGeminiAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "gemini"
    config.gemini.implementer_model = "gemini-2.5-pro"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-implementer",
        prompt="implement this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "implementer.jsonl",
        config=config,
        include_directories=[tmp_path / "target-repo"],
        session_id="existing-session",
    )

    command = cast(list[str], recorded["command"])
    assert command[:6] == ["gemini", "--prompt", "implement this task", "--approval-mode", "yolo", "--output-format"]
    assert command[6] == "stream-json"
    assert "--model" in command
    assert command[command.index("--model") + 1] == "gemini-2.5-pro"
    assert "--include-directories" in command
    assert command[command.index("--include-directories") + 1] == str((tmp_path / "target-repo").resolve())
    assert "--resume" in command
    assert command[command.index("--resume") + 1] == "existing-session"
    assert result.assistant_text == "ok"
    assert result.session_id == "gem-session"
    assert result.total_tokens == 12
    assert recorded["cwd"] == str(tmp_path)


def test_gemini_adapter_uses_safer_mode_for_reviewer(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ['{"type":"message","role":"assistant","content":"ok"}\n']
            self.stderr = []

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessGeminiAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "gemini"
    config.bootstrap()

    adapter.run(
        agent="fs-kanban-reviewer",
        prompt="review this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "reviewer.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert command[command.index("--approval-mode") + 1] == "auto_edit"


def test_gemini_adapter_returns_nonzero_result_with_stderr(monkeypatch, tmp_path):
    class FakeProcess:
        def __init__(self):
            self.stdout = ['{"type":"message","role":"assistant","content":"partial"}\n']
            self.stderr = ["permission denied\n"]

        def wait(self, timeout=None):
            return 2

        def poll(self):
            return 2

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessGeminiAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "gemini"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-reviewer",
        prompt="review this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "reviewer.jsonl",
        config=config,
    )

    assert result.ok is False
    assert result.returncode == 2
    assert result.assistant_text == "partial"
    assert result.stderr == "permission denied\n"


def test_gemini_adapter_extracts_nested_usage_totals():
    stdout = "\n".join(
        [
            '{"type":"result","result":{"usage":{"totalTokens":11}}}',
            '{"type":"result","result":{"usage":{"total_tokens":7}}}',
        ]
    )

    assert _extract_total_tokens(stdout) == 18


def test_gemini_adapter_skips_duplicate_include_directory(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ['{"type":"message","role":"assistant","content":"ok"}\n']
            self.stderr = []

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessGeminiAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "gemini"
    config.bootstrap()

    adapter.run(
        agent="fs-kanban-planner",
        prompt="plan this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
        include_directories=[tmp_path, tmp_path],
    )

    command = cast(list[str], recorded["command"])
    assert "--include-directories" not in command


def test_gemini_adapter_uses_request_draft_model_for_request_draft_agent(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ['{"type":"message","role":"assistant","content":"ok"}\n']
            self.stderr = []

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessGeminiAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "gemini"
    config.gemini.request_draft_model = "gemini-2.5-flash"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-request-draft",
        prompt="draft this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "request-draft.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert command[command.index("--model") + 1] == "gemini-2.5-flash"
    assert result.resolved_model == "gemini-2.5-flash"
