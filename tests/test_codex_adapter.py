from __future__ import annotations

from pathlib import Path
from typing import cast

import subprocess

from assistant_agent_kanban.codex_adapter import CODEX_KNOWN_MODELS, SubprocessCodexAdapter
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
    config.codex.planner_model = "gpt-5.4"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-planner",
        prompt="plan this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert command[:6] == ["codex", "exec", "-c", 'approval_policy="never"', "-s", "danger-full-access"]
    assert "--json" in command
    assert "--model" in command
    assert command[command.index("--model") + 1] == "gpt-5.4"
    assert command[-1] == "plan this task"
    assert result.assistant_text == "ok"
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
    assert command[:8] == ["codex", "exec", "-c", 'approval_policy="never"', "-s", "danger-full-access", "resume", "thread-existing"]
    assert result.session_id == "thread-existing"


def test_codex_adapter_exposes_known_models(tmp_path):
    adapter = SubprocessCodexAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "codex"
    config.bootstrap()

    assert adapter.discover_models(config=config) == CODEX_KNOWN_MODELS
