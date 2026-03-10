from __future__ import annotations

from pathlib import Path

from .config import AppConfig, PROJECT_ROOT


def ensure_runtime_agent(config: AppConfig, agent_name: str) -> Path | None:
    source = PROJECT_ROOT / ".opencode" / "agents" / f"{agent_name}.md"
    if not source.exists():
        return None
    target = runtime_agents_dir(config) / f"{agent_name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_text()
    if not target.exists() or target.read_text() != content:
        target.write_text(content)
    return target


def runtime_agents_dir(config: AppConfig) -> Path:
    return config.kanban_root / "_runtime" / "opencode-config" / "opencode" / "agents"


def runtime_config_home(config: AppConfig) -> Path:
    return config.kanban_root / "_runtime" / "opencode-config"
