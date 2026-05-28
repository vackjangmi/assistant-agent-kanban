from __future__ import annotations

from typing import cast

import subprocess

from assistant_agent_kanban.codex_adapter import (
    CODEX_KNOWN_MODELS,
    SubprocessCodexAdapter,
    _extract_session_budget_tokens,
    _extract_total_tokens,
    _parse_codex_discovered_models,
)
from assistant_agent_kanban.config import AppConfig


def test_codex_adapter_builds_exec_command(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ['{"type":"thread.started","thread_id":"thread-123"}\n', '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n']
            self.stderr = []

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        recorded["cwd"] = kwargs.get("cwd")
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.codex.planner_model = "gpt-5.5 (xhigh)"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-planner",
        prompt="plan this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert command[:6] == ["codex", "exec", "-c", 'approval_policy="never"', "-s", "workspace-write"]
    assert "--json" in command
    assert "--model" in command
    assert command[command.index("--model") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="xhigh"' in command
    assert command[-1] == "plan this task"
    assert result.assistant_text == "ok"
    assert result.resolved_model == "gpt-5.5 (xhigh)"
    assert result.session_id == "thread-123"
    assert recorded["cwd"] == str(tmp_path)


def test_codex_adapter_reuses_session_id(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.stdout = ['{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n']
            self.stderr = []
            self.command = command

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-reviewer",
        prompt="review this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "reviewer.jsonl",
        config=config,
        session_id="thread-existing",
    )

    command = cast(list[str], recorded["command"])
    assert command[:8] == ["codex", "exec", "-c", 'approval_policy="never"', "-s", "workspace-write", "resume", "thread-existing"]
    assert result.session_id == "thread-existing"


def test_codex_adapter_omits_model_flag_for_default_model(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.stdout = ['{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n']
            self.stderr = []
            self.command = command

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
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
    assert command[-1] == "draft this task"
    assert result.resolved_model is None


def test_codex_token_extraction_does_not_double_count_cached_input_tokens():
    stdout = "\n".join(
        [
            '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":20}}',
            '{"type":"turn.completed","usage":{"input_tokens":50,"cached_input_tokens":25,"output_tokens":10}}',
        ]
    )

    assert _extract_total_tokens(stdout) == 180
    assert _extract_session_budget_tokens(stdout) == 180


def test_codex_adapter_discovers_models_from_cli(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"models":[{"slug":"gpt-5.5","visibility":"list","supported_reasoning_levels":[{"effort":"low"},{"effort":"medium"},{"effort":"high"},{"effort":"xhigh"}]},{"slug":"gpt-5.4","visibility":"list"}]}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.bootstrap()

    assert adapter.discover_models(config=config) == ["gpt-5.5", "gpt-5.5 (low)", "gpt-5.5 (medium)", "gpt-5.5 (high)", "gpt-5.5 (xhigh)", "gpt-5.4"]
    assert recorded["command"] == ["codex", "debug", "models", "--bundled"]
    assert recorded["cwd"] == str(config.repo_root)


def test_codex_adapter_refreshes_models_from_cli(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout='{"models":["gpt-5.5"]}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.bootstrap()

    assert adapter.discover_models(config=config, refresh=True) == ["gpt-5.5"]
    assert recorded["command"] == ["codex", "debug", "models"]


def test_codex_adapter_falls_back_to_known_models_when_discovery_fails(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="unknown command")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.bootstrap()

    assert adapter.discover_models(config=config)[:5] == ["gpt-5.5", "gpt-5.5 (low)", "gpt-5.5 (medium)", "gpt-5.5 (high)", "gpt-5.5 (xhigh)"]
    assert CODEX_KNOWN_MODELS[0] == "gpt-5.5"


def test_parse_codex_discovered_models_uses_visible_catalog_slugs():
    output = """
WARNING: proceeding, even though we could not update PATH
{"models":[
  {"slug":"gpt-5.5","display_name":"GPT-5.5","visibility":"list","supported_reasoning_levels":[{"effort":"low"},{"effort":"high"},{"effort":"xhigh"}]},
  {"slug":"hidden-model","visibility":"hidden"},
  {"id":"gpt-5.4","visibility":"list"},
  {"model":"gpt-5.4","visibility":"list"}
]}
"""

    assert _parse_codex_discovered_models(output) == ["gpt-5.5", "gpt-5.5 (low)", "gpt-5.5 (high)", "gpt-5.5 (xhigh)", "gpt-5.4"]


def test_codex_adapter_uses_request_draft_model_for_request_draft_agent(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.stdout = ['{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n']
            self.stderr = []
            self.command = command

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.codex.request_draft_model = "gpt-5.1"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-request-draft",
        prompt="draft this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "request-draft.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert command[command.index("--model") + 1] == "gpt-5.1"
    assert result.resolved_model == "gpt-5.1"


def test_codex_adapter_promotes_stdout_turn_failed_to_stderr(monkeypatch, tmp_path):
    class FakeProcess:
        def __init__(self, command):
            self.stdout = [
                '{"type":"turn.failed","message":"Invalid prompt: policy flagged"}\n',
            ]
            self.stderr = []

        def wait(self, timeout=None):
            return 1

        def kill(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: FakeProcess(command))
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-implementer",
        prompt="implement this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "implementer.jsonl",
        config=config,
    )

    assert not result.ok
    assert result.stderr == "Invalid prompt: policy flagged"


def test_codex_adapter_notes_empty_web_search_before_turn_failed(monkeypatch, tmp_path):
    class FakeProcess:
        def __init__(self, command):
            self.stdout = [
                '{"type":"item.completed","item":{"type":"web_search","query":""}}\n',
                '{"type":"turn.failed","message":"Invalid prompt: policy flagged"}\n',
            ]
            self.stderr = []

        def wait(self, timeout=None):
            return 1

        def kill(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: FakeProcess(command))
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-implementer",
        prompt="implement this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "implementer.jsonl",
        config=config,
    )

    assert not result.ok
    assert "Invalid prompt: policy flagged" in result.stderr
    assert "web_search with an empty query" in result.stderr
