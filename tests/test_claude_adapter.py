from __future__ import annotations

from typing import cast

import subprocess

from assistant_agent_kanban.claude_adapter import CLAUDE_MODEL_ALIASES, SubprocessClaudeAdapter, _extract_assistant_text, _extract_session_id, _extract_total_tokens
from assistant_agent_kanban.config import AppConfig


def test_extract_assistant_text_prefers_result_payload():
    stdout = "\n".join(
        [
            '{"type":"assistant","message":{"content":[{"text":"draft"}]}}',
            '{"type":"result","result":"final answer"}',
        ]
    )

    assert _extract_assistant_text(stdout) == "final answer"


def test_extract_assistant_text_joins_stream_deltas():
    stdout = "\n".join(
        [
            '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"## Summary\\n"}}}',
            '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"ready"}}}',
        ]
    )

    assert _extract_assistant_text(stdout) == "## Summary\nready"


def test_extract_session_id_reads_top_level_field():
    stdout = "\n".join(
        [
            '{"type":"system","subtype":"init","session_id":"claude-session"}',
            '{"type":"result","result":"done"}',
        ]
    )

    assert _extract_session_id(stdout) == "claude-session"


def test_extract_total_tokens_reads_usage_totals():
    stdout = "\n".join(
        [
            '{"type":"assistant","message":{"content":[{"text":"draft"}]}}',
            '{"type":"result","usage":{"input_tokens":10,"output_tokens":4,"cache_read_input_tokens":3}}',
            '{"type":"result","usage":{"total_tokens":8}}',
        ]
    )

    assert _extract_total_tokens(stdout) == 25


def test_claude_adapter_builds_noninteractive_command(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = [
                '{"type":"system","subtype":"init","session_id":"claude-session"}\n',
                '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"ok"}}}\n',
                '{"type":"result","usage":{"total_tokens":12},"result":"ok"}\n',
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
    adapter = SubprocessClaudeAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "claude"
    config.claude.implementer_model = "claude-sonnet-4-6"
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
    assert command[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert "--bare" not in command
    assert "--include-partial-messages" in command
    assert "--permission-mode" in command
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allowedTools" in command
    assert command[command.index("--allowedTools") + 1] == "Bash,Read,Edit,Write,Glob,Grep,MultiEdit"
    assert "--resume" in command
    assert command[command.index("--resume") + 1] == "existing-session"
    assert "--model" in command
    assert command[command.index("--model") + 1] == "claude-sonnet-4-6"
    assert "--add-dir" in command
    assert command[command.index("--add-dir") + 1] == str((tmp_path / "target-repo").resolve())
    assert command[-2] == "--"
    assert command[-1] == "implement this task"
    assert result.assistant_text == "ok"
    assert result.session_id == "claude-session"
    assert result.total_tokens == 12
    assert recorded["cwd"] == str(tmp_path)


def test_claude_adapter_availability_only_checks_binary(tmp_path):
    adapter = SubprocessClaudeAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "claude"
    config.claude.binary = "/definitely/missing/claude"

    assert adapter.availability_error(config=config, backend="claude") is not None


def test_claude_adapter_separates_default_model_prompt_from_variadic_options(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ['{"type":"result","result":"ok"}\n']
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
    adapter = SubprocessClaudeAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "claude"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-request-draft",
        prompt="draft this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "request-draft.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert "--model" not in command
    assert command[-2:] == ["--", "draft this task"]
    assert result.resolved_model is None


def test_claude_adapter_returns_curated_aliases_for_model_candidates(tmp_path):
    adapter = SubprocessClaudeAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "claude"
    config.bootstrap()

    models = adapter.discover_models(config=config)

    assert models == CLAUDE_MODEL_ALIASES


def test_claude_adapter_model_candidates_do_not_require_anthropic_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    adapter = SubprocessClaudeAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "claude"
    config.bootstrap()

    assert adapter.discover_models(config=config) == CLAUDE_MODEL_ALIASES


def test_claude_adapter_uses_request_draft_model_for_request_draft_agent(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ['{"type":"result","result":"ok"}\n']
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
    adapter = SubprocessClaudeAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "claude"
    config.claude.request_draft_model = "claude-haiku-4-5"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-request-draft",
        prompt="draft this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "request-draft.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert command[command.index("--model") + 1] == "claude-haiku-4-5"
    assert result.resolved_model == "claude-haiku-4-5"
