from __future__ import annotations

from importlib.resources import files

from assistant_agent_kanban import agent_materializer
from assistant_agent_kanban.agent_materializer import ensure_runtime_agent
from assistant_agent_kanban.config import AppConfig, PROJECT_ROOT


def test_bundled_opencode_agents_match_repo_prompt_contracts():
    repo_agents_dir = PROJECT_ROOT / ".opencode" / "agents"
    bundled_agents_dir = files("assistant_agent_kanban").joinpath("agent_prompts", "opencode")

    for repo_agent in sorted(repo_agents_dir.glob("*.md")):
        bundled_agent = bundled_agents_dir.joinpath(repo_agent.name)
        assert bundled_agent.is_file()
        assert bundled_agent.read_text() == repo_agent.read_text()


def test_opencode_inspector_agent_is_primary_runtime():
    repo_agent = PROJECT_ROOT / ".opencode" / "agents" / "fs-kanban-inspector.md"
    bundled_agent = files("assistant_agent_kanban").joinpath("agent_prompts", "opencode", "fs-kanban-inspector.md")

    for content in (repo_agent.read_text(), bundled_agent.read_text()):
        assert "mode: primary" in content
        assert "mode: subagent" not in content


def test_materializer_falls_back_to_bundled_opencode_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_materializer, "PROJECT_ROOT", tmp_path / "missing-project-root")
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.bootstrap()

    materialized = ensure_runtime_agent(config, "fs-kanban-planner")

    assert materialized is not None
    assert materialized.exists()
    assert "# FS Kanban Planner" in materialized.read_text()
