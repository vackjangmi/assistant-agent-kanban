from __future__ import annotations

from pathlib import Path
from typing import cast

import subprocess

import pytest

from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.opencode_adapter import SubprocessOpenCodeAdapter, _extract_assistant_text


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
    adapter = SubprocessOpenCodeAdapter()
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "repo")
    config.opencode.planner_model = "openai/gpt-5.4"
    config.bootstrap()

    adapter.run(
        agent="fs-kanban-planner",
        prompt="---\ntitle: sample\n---\n",
        cwd=tmp_path,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert "--" in command
    assert command[-1] == "---\ntitle: sample\n---\n"
    env = cast(dict[str, str], recorded["env"])
    xdg_config_home = env["XDG_CONFIG_HOME"]
    assert xdg_config_home.endswith("ai-kanban/_runtime/opencode-config")
    agent_file = tmp_path / "ai-kanban" / "_runtime" / "opencode-config" / "opencode" / "agents" / "fs-kanban-planner.md"
    assert agent_file.exists()
    assert agent_file.read_text().startswith("---\nmodel: openai/gpt-5.4\n---\n")
    assert "FS Kanban Planner" in agent_file.read_text()
