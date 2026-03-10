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


def parse_request_markdown(content: str) -> ParsedRequest:
    metadata: dict[str, object] = {}
    body = content
    if content.startswith("---\n"):
        parts = content.split("\n---\n", 1)
        if len(parts) == 2:
            metadata = yaml.safe_load(parts[0][4:]) or {}
            body = parts[1]
    target = metadata.get("target") if isinstance(metadata, dict) else None
    raw_title = metadata.get("title") if isinstance(metadata, dict) else None
    title = raw_title if isinstance(raw_title, str) else _extract_title(body)
    target_repo_root = None
    base_branch = None
    raw_language = metadata.get("language") if isinstance(metadata, dict) else None
    language = normalize_language(raw_language if isinstance(raw_language, str) else None)
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
    )


def resolve_repo_root(raw_path: str | None, fallback: Path) -> Path:
    if not raw_path:
        return fallback.expanduser().resolve()
    return Path(raw_path).expanduser().resolve()


def _extract_title(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip() or None
        return stripped
    return None
