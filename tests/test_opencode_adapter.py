from __future__ import annotations

from pathlib import Path
from typing import cast

import subprocess

from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.opencode_adapter import SubprocessOpenCodeAdapter, _extract_assistant_text, _extract_session_id


def test_extract_assistant_text_prefers_final_event():
    stdout = "\n".join(
        [
            '{"type":"message","role":"assistant","content":"draft"}',
            '{"type":"final","content":"final answer"}',
        ]
    )

    assert _extract_assistant_text(stdout) == "final answer"


def test_extract_assistant_text_reads_text_part_event():
    stdout = "\n".join(
        [
            '{"type":"step_start"}',
            '{"type":"text","part":{"type":"text","text":"## Summary\\n\\nreal markdown"}}',
            '{"type":"step_finish"}',
        ]
    )

    assert _extract_assistant_text(stdout) == "## Summary\n\nreal markdown"


def test_extract_assistant_text_ignores_tool_only_json_stream():
    stdout = "\n".join(
        [
            '{"type":"step_start"}',
            '{"type":"tool_use","part":{"tool":"glob","state":{"status":"error","error":"rejected"}}}',
            '{"type":"step_finish","part":{"reason":"tool-calls"}}',
        ]
    )

    assert _extract_assistant_text(stdout) == ""


def test_extract_session_id_reads_first_event_session():
    stdout = "\n".join(
        [
            '{"type":"step_start","sessionID":"ses_123"}',
            '{"type":"final","content":"ok"}',
        ]
    )

    assert _extract_session_id(stdout) == "ses_123"


def test_subprocess_adapter_uses_double_dash_before_prompt(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ['{"type":"final","content":"ok"}\n']
            self.stderr = []

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        recorded["env"] = kwargs.get("env")
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.chdir(tmp_path)
    adapter = SubprocessOpenCodeAdapter()
    config = AppConfig(kanban_root=Path("ai-kanban"), repo_root=tmp_path / "repo")
    config.opencode.planner_model = "openai/gpt-5.4"
    config.bootstrap()
    nested_cwd = tmp_path / "ai-kanban" / "planning" / "abc1234"
    nested_cwd.mkdir(parents=True)

    adapter.run(
        agent="fs-kanban-planner",
        prompt="---\ntitle: sample\n---\n",
        cwd=nested_cwd,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert "--model" in command
    assert command[command.index("--model") + 1] == "openai/gpt-5.4"
    assert "--agent" in command
    assert command[command.index("--agent") + 1] == "fs-kanban-planner"
    assert "--" in command
    assert command[-1] == "---\ntitle: sample\n---\n"
    env = cast(dict[str, str], recorded["env"])
    xdg_config_home = env["XDG_CONFIG_HOME"]
    assert xdg_config_home == str((tmp_path / "ai-kanban" / "_runtime" / "opencode-config").resolve())
    agent_file = tmp_path / "ai-kanban" / "_runtime" / "opencode-config" / "opencode" / "agents" / "fs-kanban-planner.md"
    assert agent_file.exists()
    assert agent_file.read_text().startswith("---\nmodel: openai/gpt-5.4\n---\n")
    assert "FS Kanban Planner" in agent_file.read_text()
    assert not (nested_cwd / "ai-kanban").exists()


def test_discover_models_uses_absolute_runtime_config_home_from_relative_kanban_root(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["env"] = kwargs.get("env")

        class Result:
            returncode = 0
            stdout = '["openai/gpt-5.4"]\n'
            stderr = ""

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    adapter = SubprocessOpenCodeAdapter()
    config = AppConfig(kanban_root=Path("ai-kanban"), repo_root=tmp_path / "repo")
    config.bootstrap()

    models = adapter.discover_models(config=config)

    assert models == ["openai/gpt-5.4"]
    env = cast(dict[str, str], recorded["env"])
    assert env["XDG_CONFIG_HOME"] == str((tmp_path / "ai-kanban" / "_runtime" / "opencode-config").resolve())


def test_subprocess_adapter_reports_resolved_model_from_materialized_agent(monkeypatch, tmp_path):
    class FakeProcess:
        def __init__(self):
            self.stdout = ['{"type":"final","content":"ok"}\n']
            self.stderr = []

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessOpenCodeAdapter()
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "repo")
    config.opencode.planner_model = "openai/gpt-5.4"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-planner",
        prompt="sample",
        cwd=tmp_path,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
    )

    assert result.resolved_model == "openai/gpt-5.4"
    assert result.session_id is None


def test_subprocess_adapter_skips_model_flag_when_no_override(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.stdout = ['{"type":"final","content":"ok"}\n']
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
    adapter = SubprocessOpenCodeAdapter()
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "repo")
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-planner",
        prompt="sample",
        cwd=tmp_path,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert "--model" not in command
    assert result.resolved_model is None


def test_subprocess_adapter_reuses_explicit_session_id(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command):
            self.stdout = ['{"type":"final","content":"ok"}\n']
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
    adapter = SubprocessOpenCodeAdapter()
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "repo")
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-implementer",
        prompt="sample",
        cwd=tmp_path,
        run_log_path=tmp_path / "implementer.jsonl",
        config=config,
        session_id="ses_existing",
    )

    command = cast(list[str], recorded["command"])
    assert "--session" in command
    assert command[command.index("--session") + 1] == "ses_existing"
    assert result.session_id == "ses_existing"
