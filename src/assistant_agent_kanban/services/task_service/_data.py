from __future__ import annotations

from dataclasses import dataclass
import re

from ...enums import TaskState
from ...models import ChangedFileHunk


HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<header>.*)$")
ARTIFACT_CYCLE_RE = re.compile(r"^(WORK|REVIEW|HUMAN-QA|REVIEWER-QA|HUMAN-VERIFY)-(?P<cycle>\d{3})\.md$")
REQUEST_DRAFT_ARTIFACT_RE = re.compile(r"^REQUEST-DRAFT(?:-(?P<cycle>\d{3}))?\.md$")
ASSISTANT_RESULT_ARTIFACT_RE = re.compile(r"^(PLAN|PLAN-APPROVAL|PLAN-REJECTED-\d{3}|WORK-\d{3}|REVIEW-\d{3}|REVIEW-FAILED-\d{3})\.json$")
PLAN_APPROVAL_ARTIFACT_ORDER = {
    "PLAN-APPROVAL.md": 0,
    "PLAN-HUMAN-APPROVAL.md": 1,
}
PLANNER_RESTART_ARTIFACT = "PLANNER-RESTART.md"
AI_ACTIVE_STATES = {
    TaskState.PLANNING,
    TaskState.PLAN_APPROVING,
    TaskState.IMPLEMENTING,
    TaskState.REVIEWING,
}
WAITING_STATES = {
    TaskState.REQUESTS,
    TaskState.WAITING_CHECK_PLANS,
    TaskState.TODOS,
    TaskState.WAITING_REVIEWS,
    TaskState.COMPLETED_REVIEWS,
}

RUNTIME_ASSISTANT_LABELS = {
    "planner": "Planner",
    "plan_approval": "Plan Approval",
    "implementer": "Implementer",
    "reviewer": "Reviewer",
    "reviewer_qa": "Reviewer Q&A",
    "branch_summary": "Branch Summary",
    "commit": "Committer",
}
RUNTIME_ASSISTANT_METADATA_FIELDS = {
    "planner": "plan",
    "plan_approval": "plan_approval",
    "implementer": "implementation",
    "reviewer": "review",
    "reviewer_qa": "review",
    "branch_summary": "plan",
    "commit": "commit",
}


@dataclass(frozen=True)
class AssistantTokenUsageRow:
    runtime_assistant: str
    used_assistant: str
    model: str
    sessions: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    total_tokens: int
    input_unavailable_runs: int = 0
    cached_unavailable_runs: int = 0
    output_unavailable_runs: int = 0
    unavailable_runs: int = 0


@dataclass(frozen=True)
class TokenUsageBreakdown:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    has_input: bool = False
    has_cached: bool = False
    has_output: bool = False
    has_total: bool = False
    input_unavailable_units: int = 0
    cached_unavailable_units: int = 0
    output_unavailable_units: int = 0
    total_unavailable_units: int = 0

    def merge(self, other: "TokenUsageBreakdown") -> "TokenUsageBreakdown":
        return TokenUsageBreakdown(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            has_input=self.has_input or other.has_input,
            has_cached=self.has_cached or other.has_cached,
            has_output=self.has_output or other.has_output,
            has_total=self.has_total or other.has_total,
            input_unavailable_units=self.input_unavailable_units + other.input_unavailable_units,
            cached_unavailable_units=self.cached_unavailable_units + other.cached_unavailable_units,
            output_unavailable_units=self.output_unavailable_units + other.output_unavailable_units,
            total_unavailable_units=self.total_unavailable_units + other.total_unavailable_units,
        )


PatchFileState = dict[str, str | int | bool | list[ChangedFileHunk] | None]
