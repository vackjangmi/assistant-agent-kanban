from __future__ import annotations

from fs_kanban_agent.config import AppConfig, PROJECT_ROOT, load_config
from fs_kanban_agent.enums import STATE_ORDER


def test_app_config_bootstrap_creates_state_and_runtime_dirs(tmp_path):
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "repo")
    config.bootstrap()

    for state in STATE_ORDER:
        assert config.state_dir(state).is_dir()

    assert config.locks_dir.is_dir()
    assert config.runs_dir.is_dir()
    assert config.events_dir.is_dir()
    assert config.workspace.root is not None
    assert config.workspace.root.is_dir()
    assert config.repo_discovery.root == "../"


def test_resolve_repo_discovery_root_uses_loaded_config_directory(tmp_path):
    config_path = tmp_path / "nested" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("repo_discovery:\n  root: ../\n")

    config = load_config(config_path)

    assert config.resolve_repo_discovery_root() == config_path.parent.parent.resolve()


def test_resolve_repo_discovery_root_defaults_from_project_root_when_unloaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "repo")
    config.bootstrap()

    assert config.resolve_repo_discovery_root() == (PROJECT_ROOT / "../").resolve()
