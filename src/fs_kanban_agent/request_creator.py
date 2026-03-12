from __future__ import annotations

import secrets
from pathlib import Path

from pydantic import BaseModel, Field

from .config import AppConfig
from .enums import TaskState
from .language import runtime_language_code_to_request_language
from .target_repo_guard import resolve_safe_target_repo_root


class RequestTemplateData(BaseModel):
    title: str
    goal: str | None = None
    background: str | None = None
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


def build_default_scope_sections(target_repo_root: str | Path) -> tuple[list[str], list[str]]:
    return build_default_scope_sections_for_language(target_repo_root, language_code="en")


def build_default_scope_sections_for_language(target_repo_root: str | Path, *, language_code: str) -> tuple[list[str], list[str]]:
    target_path = str(Path(target_repo_root).expanduser())
    if language_code == "ko":
        scope = [
            f"코드 변경 범위는 `{target_path}` 내부로 제한한다.",
            f"이 요청에 필요한 파일만 `{target_path}` 아래에서 수정한다.",
            f"테스트와 로컬 설정 변경도 `{target_path}` 범위 안에서만 수행한다.",
        ]
        out_of_scope = [
            f"`{target_path}` 밖의 파일은 수정하지 않는다.",
            "관련 없는 앱, 패키지, 워크스페이스 전체 설정은 변경하지 않는다.",
            "요청에서 명시하지 않으면 배포나 인프라 변경은 추가하지 않는다.",
        ]
        return scope, out_of_scope
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
    goal = (template.goal or "").strip()
    if not title:
        raise ValueError("title is required")
    requests_dir = config.state_dir(TaskState.REQUESTS)
    task_dir = requests_dir / _generate_task_key(config.kanban_root)
    task_dir.mkdir(parents=True, exist_ok=False)
    request_path = task_dir / "REQUEST.md"
    resolved_repo = resolve_safe_target_repo_root(target_repo_root)
    request_language = runtime_language_code_to_request_language(config.runtime.language)
    lines = [
        "---",
        f"title: {title}",
        f"language: {request_language}",
        "target:",
        f"  repo_root: {resolved_repo}",
        f"  base_branch: {base_branch or config.base_branch}",
        "---",
        "",
        f"# {title}",
    ]
    lines.extend(_render_request_sections(template.model_copy(update={"title": title, "goal": goal or None}), language_code=request_language))
    request_path.write_text("\n".join(lines))
    return task_dir


def split_lines(value: str | None) -> list[str]:
    if value is None:
        return []
    return [line.strip().lstrip("- ").strip() for line in value.splitlines() if line.strip()]


def _render_request_sections(template: RequestTemplateData, *, language_code: str) -> list[str]:
    lines = [""]
    if template.goal and template.goal.strip():
        lines.extend([f"## {_section_title('goal', language_code)}", template.goal.strip(), ""])
    if template.background and template.background.strip():
        lines.extend([f"## {_section_title('background', language_code)}", template.background.strip(), ""])
    lines.extend(_render_list_section(_section_title('scope', language_code), template.scope))
    lines.extend(_render_list_section(_section_title('out_of_scope', language_code), template.out_of_scope))
    lines.extend(_render_list_section(_section_title('constraints', language_code), template.constraints))
    lines.extend(_render_list_section(_section_title('references', language_code), template.references))
    lines.extend(_render_list_section(_section_title('acceptance_criteria', language_code), template.acceptance_criteria))
    return lines


def _render_list_section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    lines = [f"## {title}"]
    lines.extend(f"- {item}" for item in items)
    lines.append("")
    return lines


def _section_title(key: str, language_code: str) -> str:
    localized = {
        "en": {
            "goal": "Goal",
            "background": "Background",
            "scope": "Scope",
            "out_of_scope": "Out of Scope",
            "constraints": "Constraints",
            "references": "References",
            "acceptance_criteria": "Acceptance Criteria",
        },
        "ko": {
            "goal": "목표",
            "background": "배경",
            "scope": "범위",
            "out_of_scope": "범위 외",
            "constraints": "제약사항",
            "references": "참고자료",
            "acceptance_criteria": "승인 기준",
        },
    }
    return localized.get(language_code, localized["en"])[key]


def _generate_task_key(kanban_root: Path) -> str:
    while True:
        candidate = secrets.token_hex(4)[:7]
        if not any(path.name == candidate for path in kanban_root.glob("*/**")):
            return candidate
