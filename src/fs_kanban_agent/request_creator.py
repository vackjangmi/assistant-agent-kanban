from __future__ import annotations

import secrets
from pathlib import Path

from pydantic import BaseModel, Field

from .config import AppConfig
from .enums import TaskState
from .language import detect_primary_language
from .target_repo_guard import resolve_safe_target_repo_root


class RequestTemplateData(BaseModel):
    title: str
    goal: str
    background: str | None = None
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


def build_default_scope_sections(target_repo_root: str | Path) -> tuple[list[str], list[str]]:
    target_path = str(Path(target_repo_root).expanduser())
    scope = [
        f"Limit code changes to `{target_path}`.",
        f"Modify only the files under `{target_path}` that are needed for this request.",
        f"Keep tests and local configuration changes scoped to `{target_path}`.",
    ]
    out_of_scope = [
        f"Do not modify files outside `{target_path}`.",
        "Do not change unrelated apps, packages, or workspace-wide configuration.",
        "Do not add deployment or infrastructure changes unless the request explicitly asks for them.",
    ]
    return scope, out_of_scope


def create_request(
    config: AppConfig,
    *,
    template: RequestTemplateData,
    target_repo_root: Path,
    base_branch: str | None = None,
) -> Path:
    title = template.title.strip()
    goal = template.goal.strip()
    if not title:
        raise ValueError("title is required")
    if not goal:
        raise ValueError("goal is required")
    requests_dir = config.state_dir(TaskState.REQUESTS)
    task_dir = requests_dir / _generate_task_key(config.kanban_root)
    task_dir.mkdir(parents=True, exist_ok=False)
    request_path = task_dir / "REQUEST.md"
    resolved_repo = resolve_safe_target_repo_root(target_repo_root)
    lines = [
        "---",
        f"title: {title}",
        f"language: {detect_primary_language(_build_language_sample(template, title, goal))}",
        "target:",
        f"  repo_root: {resolved_repo}",
        f"  base_branch: {base_branch or config.base_branch}",
        "---",
        "",
        f"# {title}",
    ]
    lines.extend(_render_request_sections(template.model_copy(update={"title": title, "goal": goal})))
    request_path.write_text("\n".join(lines))
    return task_dir


def split_lines(value: str | None) -> list[str]:
    if value is None:
        return []
    return [line.strip().lstrip("- ").strip() for line in value.splitlines() if line.strip()]


def _render_request_sections(template: RequestTemplateData) -> list[str]:
    lines = ["", "## Goal", template.goal.strip(), ""]
    if template.background and template.background.strip():
        lines.extend(["## Background", template.background.strip(), ""])
    lines.extend(_render_list_section("Scope", template.scope))
    lines.extend(_render_list_section("Out of Scope", template.out_of_scope))
    lines.extend(_render_list_section("Constraints", template.constraints))
    lines.extend(_render_list_section("References", template.references))
    lines.extend(_render_list_section("Acceptance Criteria", template.acceptance_criteria))
    return lines


def _render_list_section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    lines = [f"## {title}"]
    lines.extend(f"- {item}" for item in items)
    lines.append("")
    return lines


def _generate_task_key(kanban_root: Path) -> str:
    while True:
        candidate = secrets.token_hex(4)[:7]
        if not any(path.name == candidate for path in kanban_root.glob("*/**")):
            return candidate


def _build_language_sample(template: RequestTemplateData, title: str, goal: str) -> str:
    parts = [title, goal]
    if template.background:
        parts.append(template.background)
    parts.extend(template.scope)
    parts.extend(template.out_of_scope)
    parts.extend(template.constraints)
    parts.extend(template.references)
    parts.extend(template.acceptance_criteria)
    return "\n".join(parts)
