from __future__ import annotations

from pathlib import Path

from fs_kanban_agent.config import AppConfig, PROJECT_ROOT, load_config
from fs_kanban_agent.enums import STATE_ORDER


def test_app_config_bootstrap_creates_state_and_runtime_dirs(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.bootstrap()

    for state in STATE_ORDER:
        assert config.state_dir(state).is_dir()

    assert config.locks_dir.is_dir()
    assert config.runs_dir.is_dir()
    assert config.events_dir.is_dir()
    assert config.workspace.root is not None
    assert config.workspace.root.is_dir()
    assert config.repo_discovery.root == "../"
    assert config.runtime.language == "EN"
    assert config.runtime.coding_assistant == "opencode"
    assert config.runtime.planner_agent_count == 1
    assert config.runtime.implementer_agent_count == 1
    assert config.runtime.reviewer_agent_count == 1


def test_resolve_repo_discovery_root_uses_loaded_config_directory(tmp_path):
    config_path = tmp_path / "nested" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("repo_discovery:\n  root: ../\n")

    config = load_config(config_path)

    assert config.resolve_repo_discovery_root() == config_path.parent.parent.resolve()


def test_load_config_merges_base_and_local_override(tmp_path, monkeypatch):
    base_path = tmp_path / "config.yaml"
    local_path = tmp_path / "config.local.yaml"
    monkeypatch.setattr("fs_kanban_agent.config.DEFAULT_CONFIG_PATH", base_path)
    monkeypatch.setattr("fs_kanban_agent.config.DEFAULT_LOCAL_CONFIG_PATH", local_path)
    base_path.write_text(
        "\n".join(
            [
                "kanban_root: ./base-kanban",
                "opencode:",
                "  planner_model: planner-base",
                "repo_discovery:",
                "  max_depth: 2",
            ]
        )
    )
    local_path.write_text(
        "\n".join(
            [
                "opencode:",
                "  planner_model: planner-local",
                "runtime:",
                "  auto_dispatch: false",
                "  language: ko",
                "  coding_assistant: opencode",
                "  planner_agent_count: 2",
            ]
        )
    )

    config = load_config()

    assert config.kanban_root == Path("./base-kanban")
    assert config.opencode.planner_model == "planner-local"
    assert config.repo_discovery.max_depth == 2
    assert config.runtime.auto_dispatch is False
    assert config.runtime.language == "KO"
    assert config.runtime.coding_assistant == "opencode"
    assert config.runtime.planner_agent_count == 2
    assert config.runtime.implementer_agent_count == 1
    assert config.runtime.reviewer_agent_count == 1
    assert config.loaded_from == base_path.resolve()
    assert config.loaded_local_from == local_path.resolve()


def test_resolve_repo_discovery_root_defaults_from_project_root_when_unloaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.bootstrap()

    assert config.resolve_repo_discovery_root() == (PROJECT_ROOT / "../").resolve()


def test_load_config_accepts_codex_runtime_backend(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "runtime:",
                "  coding_assistant: codex",
                "codex:",
                "  planner_model: gpt-5.4",
            ]
        )
    )

    config = load_config(config_path)

    assert config.runtime.coding_assistant == "codex"
    assert config.codex.planner_model == "gpt-5.4"
