from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..enums import TaskState
from ..models import PlanHumanApprovalRecord, TaskContext


PlanChangeClassification = Literal["none", "trivial", "substantive", "unknown"]
SUBSTANTIVE_SECTION_NAMES = {
    "scope",
    "out of scope",
    "file map",
    "step-by-step plan",
    "validation plan",
    "acceptance criteria",
    "risks",
}
TRIVIAL_SECTION_NAMES = {"summary", "open questions"}
HEADING_RE = re.compile(r"^(?P<level>#+)\s+(?P<title>.+?)\s*$")
ATTACHMENT_PATH_RE = re.compile(r"\((?:/api/tasks/[^)]+/attachments\?artifact=PLAN\.md&path=|attachments/)[^)]+\)")


@dataclass(slots=True)
class HistoricalApprovalExample:
    task_id: str
    title: str
    approved_at: str
    change_classification: str
    ai_disposition: str | None
    ai_confidence: str | None
    ai_risk_signals: list[str]
    ai_rationale: str
    request_excerpt: str
    plan_excerpt: str
    score: tuple[int, int, str]


def normalize_plan_markdown(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = ATTACHMENT_PATH_RE.sub("(<attachment>)", normalized)
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def plan_text_hash(text: str) -> str:
    return hashlib.sha256(normalize_plan_markdown(text).encode("utf-8")).hexdigest()


def classify_plan_change(*, baseline_text: str, current_text: str) -> PlanChangeClassification:
    baseline_normalized = normalize_plan_markdown(baseline_text)
    current_normalized = normalize_plan_markdown(current_text)
    if baseline_normalized == current_normalized:
        return "none"
    baseline_sections = _parse_sections(baseline_normalized)
    current_sections = _parse_sections(current_normalized)
    if baseline_sections is None or current_sections is None:
        return "unknown"
    if list(baseline_sections) != list(current_sections):
        return "substantive"
    saw_trivial_diff = False
    for section_name, baseline_section in baseline_sections.items():
        current_section = current_sections[section_name]
        if baseline_section == current_section:
            continue
        if section_name in SUBSTANTIVE_SECTION_NAMES:
            return "substantive"
        if _normalize_lenient(baseline_section) == _normalize_lenient(current_section):
            if section_name in TRIVIAL_SECTION_NAMES:
                saw_trivial_diff = True
                continue
            return "unknown"
        return "unknown"
    return "trivial" if saw_trivial_diff else "unknown"


def load_plan_baseline_text(task_dir: Path) -> str | None:
    plan_json_path = task_dir / "PLAN.json"
    if not plan_json_path.exists():
        return None
    try:
        payload = json.loads(plan_json_path.read_text())
    except json.JSONDecodeError:
        return None
    assistant_text = payload.get("assistant_text")
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return None
    return assistant_text


def count_file_map_entries(plan_text: str) -> int:
    sections = _parse_sections(normalize_plan_markdown(plan_text))
    if sections is None:
        return 0
    file_map = sections.get("file map", "")
    return len([line for line in file_map.splitlines() if line.lstrip().startswith(("- ", "* "))])


class PlanApprovalLearningService:
    def __init__(self, scanner) -> None:
        self.scanner = scanner

    def format_historical_examples(self, task: TaskContext, *, max_examples: int = 3, max_chars: int = 5000) -> str:
        examples = self._collect_examples(task, max_examples=max_examples)
        if not examples:
            return ""
        blocks: list[str] = ["# Historical Human Approvals (Strong Positives)"]
        total_chars = len(blocks[0])
        for example in examples:
            block = "\n".join(
                [
                    f"## Task {example.task_id}: {example.title}",
                    f"- Approved at: {example.approved_at}",
                    f"- Plan changes: {example.change_classification}",
                    f"- Prior AI decision: {example.ai_disposition or 'unknown'} / {example.ai_confidence or 'unknown'}",
                    f"- Risk signals: {', '.join(example.ai_risk_signals) if example.ai_risk_signals else 'none'}",
                    f"- Rationale: {example.ai_rationale or 'No rationale recorded.'}",
                    "- Request excerpt:",
                    example.request_excerpt,
                    "- Plan excerpt:",
                    example.plan_excerpt,
                ]
            )
            if total_chars + len(block) > max_chars:
                break
            blocks.extend(["", block])
            total_chars += len(block)
        return "\n".join(blocks) if len(blocks) > 1 else ""

    def build_human_approval_record(self, task: TaskContext, *, approved_by: str) -> PlanHumanApprovalRecord:
        plan_text = (task.task_dir / "PLAN.md").read_text().rstrip()
        baseline_text = load_plan_baseline_text(task.task_dir)
        classification = classify_plan_change(
            baseline_text=baseline_text or plan_text,
            current_text=plan_text,
        ) if baseline_text is not None else "unknown"
        approval_record = PlanHumanApprovalRecord(
            approved_by=approved_by,
            plan_revision=task.metadata.plan.revision,
            generated_plan_hash=plan_text_hash(baseline_text) if baseline_text is not None else None,
            current_plan_hash=plan_text_hash(plan_text),
            change_classification=classification,
            ai_disposition=task.metadata.plan_approval.disposition,
            ai_confidence=task.metadata.plan_approval.confidence,
            ai_risk_signals=list(task.metadata.plan_approval.risk_signals),
            ai_rationale=task.metadata.plan_approval.rationale,
            ai_resolved_at=task.metadata.plan_approval.resolved_at,
            source_plan_revision=task.metadata.plan_approval.source_plan_revision,
            file_map_entry_count=count_file_map_entries(plan_text),
        )
        approval_record.strong_positive = self.is_strong_positive(task, approval_record)
        return approval_record

    def infer_historical_approval_record(self, task: TaskContext) -> PlanHumanApprovalRecord | None:
        if task.state != TaskState.DONE or self._manual_approval_history_entry(task) is None:
            return None
        baseline_text = load_plan_baseline_text(task.task_dir)
        plan_path = task.task_dir / "PLAN.md"
        if baseline_text is None or not plan_path.exists():
            return None
        current_text = plan_path.read_text().rstrip()
        classification = classify_plan_change(baseline_text=baseline_text, current_text=current_text)
        if classification not in {"none", "trivial"}:
            return None
        history_entry = self._manual_approval_history_entry(task)
        if history_entry is None:
            return None
        record = PlanHumanApprovalRecord(
            approved_at=history_entry.entered_at,
            approved_by=history_entry.by,
            plan_revision=task.metadata.plan.revision,
            generated_plan_hash=plan_text_hash(baseline_text),
            current_plan_hash=plan_text_hash(current_text),
            change_classification=classification,
            file_map_entry_count=count_file_map_entries(current_text),
            outcome_state=task.state,
        )
        record.strong_positive = self.is_strong_positive(task, record)
        return record if record.strong_positive else None

    def is_strong_positive(self, task: TaskContext, record: PlanHumanApprovalRecord) -> bool:
        if task.state != TaskState.DONE:
            return False
        if record.change_classification not in {"none", "trivial"}:
            return False
        todos_visits = sum(1 for entry in task.metadata.history if entry.state == TaskState.TODOS)
        return todos_visits <= 1

    def _collect_examples(self, current_task: TaskContext, *, max_examples: int) -> list[HistoricalApprovalExample]:
        current_title_tokens = _title_tokens(current_task.metadata.title)
        candidates: list[HistoricalApprovalExample] = []
        for task in self.scanner.scan():
            if task.metadata.task_id == current_task.metadata.task_id or task.state != TaskState.DONE:
                continue
            if task.metadata.target.repo_root != current_task.metadata.target.repo_root:
                continue
            if task.metadata.target.base_branch != current_task.metadata.target.base_branch:
                continue
            if task.metadata.request.language != current_task.metadata.request.language:
                continue
            record = self._latest_strong_positive_record(task)
            if record is None:
                continue
            request_excerpt = _truncate_excerpt((task.task_dir / "REQUEST.md").read_text().strip()) if (task.task_dir / "REQUEST.md").exists() else ""
            plan_excerpt = _truncate_excerpt((task.task_dir / "PLAN.md").read_text().strip()) if (task.task_dir / "PLAN.md").exists() else ""
            score = (-_title_overlap(current_title_tokens, _title_tokens(task.metadata.title)), -int(record.file_map_entry_count), task.metadata.task_id)
            candidates.append(
                HistoricalApprovalExample(
                    task_id=task.metadata.task_id,
                    title=task.metadata.title,
                    approved_at=record.approved_at.isoformat(),
                    change_classification=record.change_classification,
                    ai_disposition=record.ai_disposition,
                    ai_confidence=record.ai_confidence,
                    ai_risk_signals=list(record.ai_risk_signals),
                    ai_rationale=record.ai_rationale,
                    request_excerpt=request_excerpt,
                    plan_excerpt=plan_excerpt,
                    score=score,
                )
            )
        candidates.sort(key=lambda example: example.score)
        return candidates[:max_examples]

    def _latest_strong_positive_record(self, task: TaskContext) -> PlanHumanApprovalRecord | None:
        records = [record for record in task.metadata.plan_approval.human_approvals if record.strong_positive]
        if records:
            return records[-1]
        return self.infer_historical_approval_record(task)

    def _manual_approval_history_entry(self, task: TaskContext):
        for entry in reversed(task.metadata.history):
            if entry.state == TaskState.TODOS and entry.note == "manual approval":
                return entry
        return None


def _parse_sections(text: str) -> dict[str, str] | None:
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    saw_heading = False
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match is not None and len(match.group("level")) <= 2:
            current_key = match.group("title").strip().lower()
            sections[current_key] = []
            saw_heading = True
            continue
        if current_key is None:
            continue
        sections[current_key].append(line)
    if not saw_heading:
        return None
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _normalize_lenient(text: str) -> str:
    normalized = normalize_plan_markdown(text).lower()
    normalized = re.sub(r"^[*-]\s+", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"[^a-z0-9\n]+", "", normalized)
    return normalized.strip()


def _title_tokens(title: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", title.lower()) if token}


def _title_overlap(left: set[str], right: set[str]) -> int:
    if not left or not right:
        return 0
    return len(left & right)


def _truncate_excerpt(text: str, *, max_chars: int = 400) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"
