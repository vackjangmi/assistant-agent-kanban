from __future__ import annotations

from pathlib import Path

from .config import AppConfig, PROJECT_ROOT


def ensure_runtime_agent(config: AppConfig, agent_name: str) -> Path | None:
    source = PROJECT_ROOT / ".opencode" / "agents" / f"{agent_name}.md"
    if not source.exists():
        return None
    target = runtime_agents_dir(config) / f"{agent_name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = _materialize_agent_content(source.read_text(), _model_for_agent(config, agent_name))
    if not target.exists() or target.read_text() != content:
        target.write_text(content)
    return target


def runtime_agents_dir(config: AppConfig) -> Path:
    return config.kanban_root / "_runtime" / "opencode-config" / "opencode" / "agents"


def runtime_config_home(config: AppConfig) -> Path:
    return config.kanban_root / "_runtime" / "opencode-config"


def _model_for_agent(config: AppConfig, agent_name: str) -> str | None:
    if agent_name == config.opencode.planner_agent:
        return config.opencode.planner_model
    if agent_name == config.opencode.implementer_agent:
        return config.opencode.implementer_model
    if agent_name == config.opencode.reviewer_agent:
        return config.opencode.reviewer_model
    if agent_name == config.opencode.commit_agent:
        return config.opencode.commit_model
    return None


def _materialize_agent_content(content: str, model: str | None) -> str:
    if not model:
        return content
    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            closing = lines.index("---", 1)
        except ValueError:
            closing = -1
        if closing > 0:
            frontmatter = lines[1:closing]
            body = lines[closing + 1 :]
            filtered = [line for line in frontmatter if not line.startswith("model:")]
            filtered.append(f"model: {model}")
            return "\n".join(["---", *filtered, "---", *body]) + "\n"
    return "\n".join(["---", f"model: {model}", "---", content.rstrip()]) + "\n"
