from __future__ import annotations

import json
import subprocess
from typing import cast

import assistant_agent_kanban.antigravity_adapter as antigravity_module
from assistant_agent_kanban.antigravity_adapter import (
    ANTIGRAVITY_KNOWN_MODELS,
    SubprocessAntigravityAdapter,
    _bind_prompt_to_cwd,
    _extract_assistant_text,
    _extract_session_id,
    _extract_total_tokens,
)
from assistant_agent_kanban.config import AppConfig


def test_extract_assistant_text_prefers_plain_stdout():
    assert _extract_assistant_text("done\n") == "done"


def test_extract_session_id_reads_resume_command():
    output = "Resume with: agy --conversation conv-123\n"

    assert _extract_session_id(output) == "conv-123"


def test_extract_total_tokens_reads_json_usage():
    output = "\n".join(
        [
            '{"type":"result","usage":{"totalTokens":12}}',
            '{"type":"result","result":{"usage":{"total_tokens":8}}}',
        ]
    )

    assert _extract_total_tokens(output) == 20


def test_antigravity_adapter_builds_print_command_and_temporarily_sets_model(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"enableTelemetry": False, "model": "old-model"}))
    monkeypatch.setattr(antigravity_module, "ANTIGRAVITY_MODEL_RESTORE_DELAY_SECONDS", 0)

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ["implemented\n", "Resume with: agy --conversation conv-456\n"]
            self.stderr = []

        def wait(self, timeout=None):
            recorded["settings_during_run"] = json.loads(settings_path.read_text())
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        recorded["cwd"] = kwargs.get("cwd")
        recorded["settings_at_start"] = json.loads(settings_path.read_text())
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessAntigravityAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "antigravity"
    config.antigravity.settings_path = settings_path
    config.antigravity.implementer_model = "Gemini 3.5 Flash (High)"
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-implementer",
        prompt="implement this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "implementer.log",
        config=config,
        include_directories=[tmp_path / "target-repo"],
        session_id="conv-123",
    )

    command = cast(list[str], recorded["command"])
    assert command[:3] == ["agy", "--print-timeout", "1800s"]
    assert "--dangerously-skip-permissions" in command
    assert "--conversation" in command
    assert command[command.index("--conversation") + 1] == "conv-123"
    assert "--add-dir" in command
    add_dirs = [command[index + 1] for index, value in enumerate(command) if value == "--add-dir"]
    assert add_dirs == [str(tmp_path.resolve()), str((tmp_path / "target-repo").resolve())]
    assert command[-2] == "--print"
    assert "The only editable implementation repository" in command[-1]
    assert "implement this task" in command[-1]
    assert recorded["settings_at_start"] == {"enableTelemetry": False, "model": "Gemini 3.5 Flash (High)"}
    assert recorded["settings_during_run"] == {"enableTelemetry": False, "model": "old-model"}
    assert json.loads(settings_path.read_text()) == {"enableTelemetry": False, "model": "old-model"}
    assert result.assistant_text.startswith("implemented")
    assert result.resolved_model == "Gemini 3.5 Flash (High)"
    assert result.session_id == "conv-456"
    assert recorded["cwd"] == str(tmp_path)


def test_antigravity_adapter_uses_existing_default_model_without_settings_rewrite(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"enableTelemetry": False, "model": "existing-default"}))

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdout = ["drafted\n"]
            self.stderr = []

        def wait(self, timeout=None):
            recorded["settings_during_run"] = json.loads(settings_path.read_text())
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        recorded["settings_at_start"] = json.loads(settings_path.read_text())
        return FakeProcess(command)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = SubprocessAntigravityAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "antigravity"
    config.antigravity.settings_path = settings_path
    config.bootstrap()

    result = adapter.run(
        agent="fs-kanban-request-draft",
        prompt="draft this task",
        cwd=tmp_path,
        run_log_path=tmp_path / "request-draft.log",
        config=config,
    )

    command = cast(list[str], recorded["command"])
    assert command[-2] == "--print"
    assert "draft this task" in command[-1]
    assert recorded["settings_at_start"] == {"enableTelemetry": False, "model": "existing-default"}
    assert recorded["settings_during_run"] == {"enableTelemetry": False, "model": "existing-default"}
    assert result.resolved_model is None


def test_bind_prompt_to_cwd_forbids_antigravity_scratch(tmp_path):
    prompt = _bind_prompt_to_cwd("do the work", cwd=tmp_path)

    assert str(tmp_path.resolve()) in prompt
    assert "Do not copy the repository to Antigravity scratch space" in prompt
    assert prompt.endswith("do the work")


def test_antigravity_adapter_discovers_configured_and_settings_models(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"model": "Gemini 3.5 Flash (High)"}))
    adapter = SubprocessAntigravityAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "antigravity"
    config.antigravity.settings_path = settings_path
    config.antigravity.planner_model = "Claude Sonnet 4.6 (Thinking)"
    config.bootstrap()

    assert adapter.discover_models(config=config) == [
        *ANTIGRAVITY_KNOWN_MODELS,
    ]


def test_antigravity_adapter_appends_custom_models(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"model": "Custom Default Model"}))
    adapter = SubprocessAntigravityAdapter()
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.runtime.coding_assistant = "antigravity"
    config.antigravity.settings_path = settings_path
    config.antigravity.planner_model = "Custom Planner Model"
    config.bootstrap()

    assert adapter.discover_models(config=config) == [
        *ANTIGRAVITY_KNOWN_MODELS,
        "Custom Default Model",
        "Custom Planner Model",
    ]
