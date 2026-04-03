from __future__ import annotations

from pathlib import Path
from typing import cast

from .config import ASSISTANT_ROLES, AppConfig, AssistantRole, PROJECT_ROOT


def ensure_runtime_agent(config: AppConfig, agent_name: str) -> Path | None:
    role = _role_for_agent_name(config, agent_name)
    if role is None or config.backend_for_role(role) != "opencode":
        return None
    source = PROJECT_ROOT / ".opencode" / "agents" / f"{agent_name}.md"
    if not source.exists():
        return None
    target = runtime_agents_dir(config) / f"{agent_name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = _materialize_agent_content(source.read_text(), _model_for_agent(config, agent_name))
    if not target.exists() or target.read_text() != content:
        target.write_text(content)
    return target


def ensure_runtime_agents(config: AppConfig) -> list[Path]:
    materialized: list[Path] = []
    seen: set[str] = set()
    for role in ASSISTANT_ROLES:
        if config.backend_for_role(role) != "opencode":
            continue
        agent_name = config.role_agent(role)
        if agent_name in seen:
            continue
        seen.add(agent_name)
        path = ensure_runtime_agent(config, agent_name)
        if path is not None:
            materialized.append(path)
    return materialized


def runtime_agents_dir(config: AppConfig) -> Path:
    return _resolved_kanban_root(config) / "_runtime" / "opencode-config" / "opencode" / "agents"


def runtime_config_home(config: AppConfig) -> Path:
    return _resolved_kanban_root(config) / "_runtime" / "opencode-config"


def _resolved_kanban_root(config: AppConfig) -> Path:
    return config.kanban_root.expanduser().resolve()


def _model_for_agent(config: AppConfig, agent_name: str) -> str | None:
    role = _role_for_agent_name(config, agent_name)
    if role is not None:
        return config.role_model(role)
    return None


def _role_for_agent_name(config: AppConfig, agent_name: str) -> AssistantRole | None:
    for role in ASSISTANT_ROLES:
        if getattr(config.opencode, f"{role}_agent") == agent_name:
            return cast(AssistantRole, role)
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
