from __future__ import annotations

from pathlib import Path

from assistant_agent_kanban.config import AppConfig, PROJECT_ROOT, load_config
from assistant_agent_kanban.enums import STATE_ORDER


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
    assert config.opencode.worker_live_logs_enabled is False
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
    monkeypatch.setattr("assistant_agent_kanban.config.DEFAULT_CONFIG_PATH", base_path)
    monkeypatch.setattr("assistant_agent_kanban.config.DEFAULT_LOCAL_CONFIG_PATH", local_path)
    base_path.write_text(
        "\n".join(
            [
                "kanban_root: ./base-kanban",
                "opencode:",
                "  planner_model: planner-base",
                "  plan_approval_model: plan-approval-base",
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
                "  plan_approval_model: plan-approval-local",
                "runtime:",
                "  auto_dispatch: false",
                "  language: ko",
                "  coding_assistant: opencode",
                "  planner_agent_count: 2",
            ]
        )
    )

    config = load_config()

    assert config.kanban_root == (base_path.parent / "base-kanban").resolve()
    assert config.opencode.planner_model == "planner-local"
    assert config.opencode.plan_approval_model == "plan-approval-local"
    assert config.repo_discovery.max_depth == 2
    assert config.runtime.auto_dispatch is False
    assert config.runtime.language == "KO"
    assert config.runtime.coding_assistant == "opencode"
    assert config.opencode.worker_live_logs_enabled is False
    assert config.runtime.planner_agent_count == 2
    assert config.runtime.implementer_agent_count == 1
    assert config.runtime.reviewer_agent_count == 1
    assert config.loaded_from == base_path.resolve()
    assert config.loaded_local_from == local_path.resolve()


def test_load_config_normalizes_root_paths_against_loaded_local_config(tmp_path):
    config_dir = tmp_path / "nested"
    config_dir.mkdir(parents=True)
    base_path = config_dir / "config.yaml"
    local_path = config_dir / "config.local.yaml"
    base_path.write_text(
        "\n".join(
            [
                "kanban_root: .kanban-agent",
                "repo_root: .",
                "workspace:",
                "  root: .kanban-agent/_runtime/workspaces-custom",
            ]
        )
    )
    local_path.write_text("runtime:\n  language: ko\n  coding_assistant: opencode\n")

    config = load_config(base_path)

    assert config.kanban_root == (config_dir / ".kanban-agent").resolve()
    assert config.repo_root == config_dir.resolve()
    assert config.workspace.root == (config_dir / ".kanban-agent" / "_runtime" / "workspaces-custom").resolve()


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


def test_load_config_accepts_role_backend_overrides(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "runtime:",
                "  coding_assistant: opencode",
                "  role_backends:",
                "    request_draft: gemini",
                "    implementer: codex",
                "    reviewer: codex",
                "gemini:",
                "  request_draft_model: gemini-2.5-flash",
                "codex:",
                "  implementer_model: gpt-5.4",
                "  reviewer_model: gpt-5.3-codex",
            ]
        )
    )

    config = load_config(config_path)

    assert config.runtime.coding_assistant == "opencode"
    assert config.backend_for_role("planner") == "opencode"
    assert config.backend_for_role("request_draft") == "gemini"
    assert config.backend_for_role("implementer") == "codex"
    assert config.backend_for_role("reviewer") == "codex"
    assert config.role_model("request_draft") == "gemini-2.5-flash"
    assert config.role_model("implementer") == "gpt-5.4"
    assert config.role_model("reviewer") == "gpt-5.3-codex"


def test_resolve_target_repo_docs_root_uses_configured_relative_path(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo", target_repo_docs_root="docs/task-artifacts")
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()

    assert config.resolve_target_repo_docs_root(target_repo) == (target_repo / "docs/task-artifacts").resolve()
