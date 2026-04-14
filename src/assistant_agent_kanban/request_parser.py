from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from .language import detect_primary_language, normalize_language


class ParsedRequest(BaseModel):
    title: str | None = None
    body: str
    target_repo_root: str | None = None
    base_branch: str | None = None
    language: str = "en"
    plan_auto_approve: bool = False


REQUIRED_REQUEST_FIELDS = ("title", "goal", "target.repo_root", "target.base_branch")


def parse_request_markdown(content: str) -> ParsedRequest:
    metadata: dict[str, object] = {}
    body = content
    if content.startswith("---\n"):
        parts = content.split("\n---\n", 1)
        if len(parts) == 2:
            body = parts[1]
            try:
                metadata = yaml.safe_load(parts[0][4:]) or {}
            except yaml.YAMLError:
                metadata = {}
    target = metadata.get("target") if isinstance(metadata, dict) else None
    raw_title = metadata.get("title") if isinstance(metadata, dict) else None
    title = raw_title if isinstance(raw_title, str) else _extract_title(body)
    target_repo_root = None
    base_branch = None
    raw_language = metadata.get("language") if isinstance(metadata, dict) else None
    language = normalize_language(raw_language if isinstance(raw_language, str) else None)
    raw_plan_auto_approve = metadata.get("plan_auto_approve") if isinstance(metadata, dict) else None
    if isinstance(target, dict):
        if isinstance(target.get("repo_root"), str):
            target_repo_root = target["repo_root"]
        if isinstance(target.get("base_branch"), str):
            base_branch = target["base_branch"]
    return ParsedRequest(
        title=title,
        body=body,
        target_repo_root=target_repo_root,
        base_branch=base_branch,
        language=language or detect_primary_language(content),
        plan_auto_approve=_coerce_bool(raw_plan_auto_approve),
    )


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return False


def resolve_repo_root(raw_path: str | None, fallback: Path) -> Path:
    if not raw_path:
        return fallback.expanduser().resolve()
    return Path(raw_path).expanduser().resolve()


def has_required_request_fields(content: str) -> bool:
    parsed = parse_request_markdown(content)
    return bool(
        parsed.title
        and parsed.target_repo_root
        and parsed.base_branch
        and extract_goal_text(parsed.body)
    )


def extract_goal_text(body: str) -> str | None:
    goal_section = _extract_markdown_section(body, "Goal", "목표")
    if goal_section:
        return goal_section

    lines = body.splitlines()
    started = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not started:
            if not stripped:
                continue
            started = True
            if stripped.startswith("#"):
                continue
        if not stripped and not collected:
            continue
        if stripped.startswith("## "):
            break
        collected.append(line.rstrip())
    candidate = "\n".join(collected).strip()
    return candidate or None


def _extract_markdown_section(body: str, *titles: str) -> str | None:
    headings = {f"## {title}".casefold() for title in titles}
    lines = body.splitlines()
    inside = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not inside:
            if stripped.casefold() in headings:
                inside = True
            continue
        if stripped.startswith("## "):
            break
        collected.append(line.rstrip())
    candidate = "\n".join(collected).strip()
    return candidate or None


def _extract_title(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip() or None
        return stripped
    return None
