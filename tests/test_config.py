from __future__ import annotations

from fs_kanban_agent.config import AppConfig
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
