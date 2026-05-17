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
            metadata = _parse_front_matter(parts[0][4:])
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


def _parse_front_matter(raw: str) -> dict[str, object]:
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        loaded = _parse_known_front_matter_fields(raw)
    if isinstance(loaded, dict):
        return loaded
    return {}


def _parse_known_front_matter_fields(raw: str) -> dict[str, object]:
    metadata: dict[str, object] = {}
    current_section: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            key, value = _split_front_matter_assignment(line)
            current_section = key if key else None
            if key and value is not None:
                metadata[key] = _strip_front_matter_scalar(value)
            elif key == "target":
                metadata[key] = {}
            continue
        if current_section == "target":
            key, value = _split_front_matter_assignment(line.strip())
            if key and value is not None:
                target = metadata.setdefault("target", {})
                if isinstance(target, dict):
                    target[key] = _strip_front_matter_scalar(value)
    return metadata


def _split_front_matter_assignment(line: str) -> tuple[str | None, str | None]:
    if ":" not in line:
        return None, None
    key, value = line.split(":", 1)
    normalized_key = key.strip()
    if not normalized_key:
        return None, None
    stripped_value = value.strip()
    return normalized_key, stripped_value if stripped_value else None


def _strip_front_matter_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


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
