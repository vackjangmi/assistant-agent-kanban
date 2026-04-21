from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .assistant_adapter import AssistantAdapter
from .config import AppConfig
from .exceptions import AdapterRunError
from .language import runtime_language_code_to_request_language
from .request_creator import build_default_scope_sections_for_language
from .target_repo_guard import resolve_safe_target_repo_root


ALLOWED_DRAFT_UPDATE_FIELDS = (
    "title",
    "goal",
    "background",
    "scope",
    "out_of_scope",
    "constraints",
    "references",
    "acceptance_criteria",
    "target_repo",
    "base_branch",
)

BASELINE_REFERENCE_FILENAMES = (
    "AGENTS.md",
    "CLAUDE.md",
)


class RequestDraftTranscriptEntry(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""


class RequestDraftPayload(BaseModel):
    request_draft_id: str | None = None
    title: str | None = None
    goal: str | None = None
    background: str | None = None
    plan_auto_approve: bool = True
    scope: str | None = None
    out_of_scope: str | None = None
    constraints: str | None = None
    references: str | None = None
    acceptance_criteria: str | None = None
    target_repo: str | None = None
    base_branch: str | None = None
    request_upload_token: str | None = None
    active_tab: Literal["assistant", "fields"] | None = None
    request_draft_input: str | None = None
    transcript: list[RequestDraftTranscriptEntry] = Field(default_factory=list)
    message: str


class RequestDraftResult(BaseModel):
    reply: str
    field_updates: dict[str, str]
    backend: str
    agent: str
    resolved_model: str | None = None
    session_id: str | None = None
    total_tokens: int = 0


def draft_request(
    *,
    config: AppConfig,
    adapter_registry: dict[str, AssistantAdapter],
    payload: RequestDraftPayload,
) -> RequestDraftResult:
    backend = config.backend_for_role("request_draft")
    adapter = adapter_registry[backend]
    agent = config.role_agent("request_draft")
    discovered_reference_paths = _discover_baseline_reference_paths((payload.target_repo or "").strip())
    prompt = build_request_drafting_prompt(config=config, payload=payload)
    with tempfile.TemporaryDirectory(prefix="assistant-agent-kanban-draft-") as temp_dir:
        cwd = _resolve_drafting_cwd(config=config, payload=payload, temp_dir=Path(temp_dir))
        run_log_path = Path(temp_dir) / "request-draft.jsonl"
        result = adapter.run(
            agent=agent,
            prompt=prompt,
            cwd=cwd,
            run_log_path=run_log_path,
            config=config,
            output_format="json",
        )
    if not result.ok:
        raise AdapterRunError(result.stderr.strip() or result.assistant_text.strip() or "request drafting failed")
    reply, field_updates = parse_request_drafting_response(result.assistant_text)
    field_updates = _apply_baseline_reference_invariant(
        field_updates=field_updates,
        existing_references=payload.references or "",
        discovered_reference_paths=discovered_reference_paths,
    )
    return RequestDraftResult(
        reply=reply,
        field_updates=field_updates,
        backend=backend,
        agent=agent,
        resolved_model=result.resolved_model,
        session_id=result.session_id,
        total_tokens=result.total_tokens,
    )


def build_request_drafting_prompt(*, config: AppConfig, payload: RequestDraftPayload) -> str:
    request_language = runtime_language_code_to_request_language(config.runtime.language)
    normalized_target_repo = (payload.target_repo or "").strip()
    normalized_base_branch = (payload.base_branch or "").strip() or config.base_branch
    discovered_reference_paths = _discover_baseline_reference_paths(normalized_target_repo)
    effective_references = _merge_reference_lines(payload.references or "", discovered_reference_paths)
    default_scope: list[str] = []
    default_out_of_scope: list[str] = []
    if normalized_target_repo:
        default_scope, default_out_of_scope = build_default_scope_sections_for_language(
            normalized_target_repo,
            language_code=request_language,
            managed_docs_root=config.target_repo_docs_root_value(),
        )
    transcript = payload.transcript + [RequestDraftTranscriptEntry(role="user", content=payload.message)]
    return "\n".join(
        [
            "You are helping a user draft a pre-submit request for Assistant Agent Kanban.",
            "This is only for the request composer before task creation.",
            "Do not create or imply any task directory, state transition, or workflow artifact.",
            "The final REQUEST.md is created only later by the existing request creation flow, and REQUEST.md remains the authoritative planner input.",
            "Use the conversation to improve the request fields non-destructively.",
            "If you suggest a field update, return the full replacement value for that field so the user can optionally apply it.",
            "Preserve any existing attachment or image URLs exactly as written, including /api/request-uploads/... and data:image/... URLs.",
            f"Reply in {request_language.upper()}.",
            "Return strict JSON only with this shape:",
            "{",
            '  "reply": "short assistant reply for the user",',
            '  "field_updates": {',
            '    "title": null,',
            '    "goal": null,',
            '    "background": null,',
            '    "scope": null,',
            '    "out_of_scope": null,',
            '    "constraints": null,',
            '    "references": null,',
            '    "acceptance_criteria": null,',
            '    "target_repo": null,',
            '    "base_branch": null',
            "  }",
            "}",
            "For line-based sections such as scope, out_of_scope, constraints, references, and acceptance_criteria, return plain newline-separated items without markdown headings.",
            "Always keep any auto-discovered baseline reference files in references when they are present in the target repository.",
            "Use null for fields that should stay unchanged.",
            "Current composer payload:",
            json.dumps(
                {
                    "title": (payload.title or "").strip(),
                    "goal": payload.goal or "",
                    "background": payload.background or "",
                    "plan_auto_approve": payload.plan_auto_approve,
                    "scope": payload.scope or "",
                    "out_of_scope": payload.out_of_scope or "",
                    "constraints": payload.constraints or "",
                    "references": effective_references,
                    "acceptance_criteria": payload.acceptance_criteria or "",
                    "target_repo": normalized_target_repo,
                    "base_branch": normalized_base_branch,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "Auto-discovered baseline references:",
            json.dumps(discovered_reference_paths, ensure_ascii=False, indent=2),
            "Repo-aware defaults for empty scope fields:",
            json.dumps(
                {
                    "scope": default_scope,
                    "out_of_scope": default_out_of_scope,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "Conversation transcript:",
            json.dumps([entry.model_dump(mode="json") for entry in transcript], ensure_ascii=False, indent=2),
        ]
    )


def parse_request_drafting_response(raw_text: str) -> tuple[str, dict[str, str]]:
    normalized = (raw_text or "").strip()
    if not normalized:
        return "", {}
    parsed = _parse_json_object(normalized)
    if not isinstance(parsed, dict):
        return normalized, {}
    reply = str(parsed.get("reply") or "").strip() or normalized
    raw_updates = parsed.get("field_updates")
    if not isinstance(raw_updates, dict):
        return reply, {}
    updates: dict[str, str] = {}
    for field_name in ALLOWED_DRAFT_UPDATE_FIELDS:
        normalized_value = _normalize_field_update_value(raw_updates.get(field_name))
        if normalized_value is None:
            continue
        updates[field_name] = normalized_value
    return reply, updates


def _parse_json_object(raw_text: str) -> object | None:
    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.DOTALL)
    candidate = fenced_match.group(1) if fenced_match else raw_text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or start >= end:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None


def _normalize_field_update_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(items) if items else None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    stripped = str(value).strip()
    return stripped or None


def _resolve_drafting_cwd(*, config: AppConfig, payload: RequestDraftPayload, temp_dir: Path) -> Path:
    target_repo = (payload.target_repo or "").strip()
    if not target_repo:
        return temp_dir.resolve()
    resolved = resolve_safe_target_repo_root(Path(target_repo))
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("target repo must be an existing directory")
    return resolved


def _discover_baseline_reference_paths(target_repo: str) -> list[str]:
    normalized_target_repo = target_repo.strip()
    if not normalized_target_repo:
        return []
    repo_root = resolve_safe_target_repo_root(Path(normalized_target_repo))
    if not repo_root.exists() or not repo_root.is_dir():
        return []
    discovered: list[str] = []
    for filename in BASELINE_REFERENCE_FILENAMES:
        candidate = repo_root / filename
        if candidate.is_file():
            discovered.append(filename)
    return discovered


def _merge_reference_lines(existing_references: str, discovered_reference_paths: list[str]) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for raw_line in existing_references.splitlines():
        line = raw_line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        merged.append(line)
    for path in discovered_reference_paths:
        if path in seen:
            continue
        seen.add(path)
        merged.append(path)
    return "\n".join(merged)


def _apply_baseline_reference_invariant(
    *,
    field_updates: dict[str, str],
    existing_references: str,
    discovered_reference_paths: list[str],
) -> dict[str, str]:
    if not discovered_reference_paths:
        return field_updates
    merged = dict(field_updates)
    reference_source = merged.get("references", existing_references)
    merged["references"] = _merge_reference_lines(reference_source, discovered_reference_paths)
    return merged
