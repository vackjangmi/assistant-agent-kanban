from __future__ import annotations

from pathlib import Path

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


def test_subprocess_adapter_uses_double_dash_before_prompt(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout='{"type":"final","content":"ok"}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SubprocessOpenCodeAdapter()
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "repo")
    config.bootstrap()

    adapter.run(
        agent="fs-kanban-planner",
        prompt="---\ntitle: sample\n---\n",
        cwd=tmp_path,
        run_log_path=tmp_path / "planner.jsonl",
        config=config,
    )

    command = recorded["command"]
    assert "--" in command
    assert command[-1] == "---\ntitle: sample\n---\n"
