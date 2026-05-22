from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from .models import TaskMetadata


SPLIT_PROPOSAL_MARKDOWN = "SPLIT-PROPOSAL.md"
SPLIT_PROPOSAL_JSON = "SPLIT-PROPOSAL.json"


class SplitChildRequest(BaseModel):
    title: str
    goal: str
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    independence_notes: str = ""

    @field_validator("title", "goal", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("split child title and goal are required")
        return text

    @field_validator("scope", "out_of_scope", "constraints", "references", "acceptance_criteria", mode="before")
    @classmethod
    def normalize_text_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip().lstrip("- ").strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class SplitProposal(BaseModel):
    recommended: bool = True
    reason: str = ""
    children: list[SplitChildRequest]

    @field_validator("children")
    @classmethod
    def require_multiple_children(cls, value: list[SplitChildRequest]) -> list[SplitChildRequest]:
        if len(value) < 2:
            raise ValueError("split proposal requires at least two child requests")
        return value


def has_split_proposal(task_dir: Path) -> bool:
    return (task_dir / SPLIT_PROPOSAL_JSON).exists()


def load_split_proposal(task_dir: Path) -> SplitProposal:
    return SplitProposal.model_validate_json((task_dir / SPLIT_PROPOSAL_JSON).read_text())


def sync_split_proposal_artifacts(task_dir: Path, metadata: TaskMetadata, plan_text: str) -> SplitProposal | None:
    proposal = extract_split_proposal(plan_text)
    if proposal is None or not proposal.recommended:
        clear_split_proposal_artifacts(task_dir, metadata)
        return None
    markdown_path = task_dir / SPLIT_PROPOSAL_MARKDOWN
    json_path = task_dir / SPLIT_PROPOSAL_JSON
    markdown_path.write_text(render_split_proposal_markdown(proposal))
    json_path.write_text(json.dumps(proposal.model_dump(mode="json"), indent=2) + "\n")
    metadata.split_proposal.recommended = True
    metadata.split_proposal.path = markdown_path.name
    metadata.split_proposal.json_path = json_path.name
    metadata.split_proposal.child_count = len(proposal.children)
    metadata.split_proposal.source_plan_revision = metadata.plan.revision
    return proposal


def clear_split_proposal_artifacts(task_dir: Path, metadata: TaskMetadata) -> None:
    for filename in (SPLIT_PROPOSAL_MARKDOWN, SPLIT_PROPOSAL_JSON):
        (task_dir / filename).unlink(missing_ok=True)
    metadata.split_proposal.recommended = False
    metadata.split_proposal.path = None
    metadata.split_proposal.json_path = None
    metadata.split_proposal.child_count = 0
    metadata.split_proposal.source_plan_revision = 0


def extract_split_proposal(markdown: str) -> SplitProposal | None:
    for block in _json_fence_blocks(markdown):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        candidate = payload.get("split_proposal") if isinstance(payload, dict) and "split_proposal" in payload else payload
        if not isinstance(candidate, dict):
            continue
        if "children" not in candidate:
            continue
        try:
            return SplitProposal.model_validate(candidate)
        except ValidationError:
            continue
    return None


def render_split_proposal_markdown(proposal: SplitProposal) -> str:
    lines = ["# Split Proposal", ""]
    if proposal.reason.strip():
        lines.extend(["## Reason", proposal.reason.strip(), ""])
    lines.append("## Child Requests")
    for index, child in enumerate(proposal.children, start=1):
        lines.extend(["", f"### {index}. {child.title}", "", child.goal])
        _extend_list_section(lines, "Scope", child.scope)
        _extend_list_section(lines, "Out of Scope", child.out_of_scope)
        _extend_list_section(lines, "Constraints", child.constraints)
        _extend_list_section(lines, "References", child.references)
        _extend_list_section(lines, "Acceptance Criteria", child.acceptance_criteria)
        if child.independence_notes.strip():
            lines.extend(["", "#### Independence Notes", child.independence_notes.strip()])
    lines.append("")
    return "\n".join(lines)


def _extend_list_section(lines: list[str], heading: str, values: list[str]) -> None:
    if not values:
        return
    lines.extend(["", f"#### {heading}"])
    lines.extend(f"- {value}" for value in values)


def _json_fence_blocks(markdown: str) -> list[str]:
    return [
        match.group("body").strip()
        for match in re.finditer(
            r"```(?:json)?\s*\n(?P<body>.*?)\n```",
            markdown,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]
