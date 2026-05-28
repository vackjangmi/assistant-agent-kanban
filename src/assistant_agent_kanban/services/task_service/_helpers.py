from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import TYPE_CHECKING

from ...enums import STATE_ORDER, TaskState
from ...exceptions import TaskNotFoundError, TransitionError
from ...markdown_attachments import attachments_dir_for_task
from ...models import (
    ChangedFileDetail,
    HumanReviewState,
    StageTimingSegment,
    StageTimingSummary,
    TaskMetadata,
    TaskStageTiming,
)
from ...request_parser import extract_goal_text, parse_request_markdown
from ...scanner import TERMINAL_STATES

if TYPE_CHECKING:
    from ._protocol import _TaskServiceLike
else:
    _TaskServiceLike = object

from ._data import (
    AI_ACTIVE_STATES,
    ARTIFACT_CYCLE_RE,
    PLAN_APPROVAL_ARTIFACT_ORDER,
    PLANNER_RESTART_ARTIFACT,
    REQUEST_DRAFT_ARTIFACT_RE,
    WAITING_STATES,
)


class _HelpersMixin(_TaskServiceLike):
    def _visible_log_files(self, log_dir: Path) -> list[str]:
        if not log_dir.exists():
            return []
        return sorted(path.name for path in log_dir.glob("*") if self._should_show_log_file(path.name))


    def _should_show_log_file(self, filename: str) -> bool:
        return not filename.endswith(("-handshake.jsonl", "-finalize.jsonl"))


    def _summary_keywords(self, metadata: TaskMetadata) -> list[str]:
        raw_parts = [metadata.title, metadata.integration.final_branch_summary or ""]
        seen: OrderedDict[str, None] = OrderedDict()
        for part in raw_parts:
            for token in re.findall(r"[A-Za-z0-9]+", part.lower()):
                if len(token) < 3:
                    continue
                if token in {"task", "summary", "feature"}:
                    continue
                seen.setdefault(token, None)
        return list(seen.keys())[:8]


    def _target_repo_changed_files(self, metadata: TaskMetadata) -> list[ChangedFileDetail]:
        diff_text = self._target_repo_diff_against_base(metadata, ref=None)
        if diff_text is not None:
            return self._parse_patch(diff_text)
        patch_path = self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
        if patch_path is not None and patch_path.exists():
            return self._parse_patch(patch_path.read_text())
        return []


    def _request_goal(self, task_dir: Path) -> str | None:
        request_path = task_dir / "REQUEST.md"
        if not request_path.exists():
            return None
        parsed = parse_request_markdown(request_path.read_text())
        goal = extract_goal_text(parsed.body)
        return self._single_line(goal)


    def _latest_artifact_summary(self, task_dir: Path, pattern: str) -> str | None:
        files = sorted(task_dir.glob(pattern))
        if not files:
            return None
        return self._artifact_summary(files[-1])


    def _artifact_summary(self, path: Path) -> str | None:
        if not path.exists():
            return None
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("Verdict:"):
                continue
            if line in {"No notes yet.", "No unresolved comments."}:
                continue
            return self._single_line(line)
        return None


    def _single_line(self, value: str | None) -> str | None:
        if value is None:
            return None
        collapsed = " ".join(value.split())
        return collapsed or None


    def _format_duration_ms(self, duration_ms: int) -> str:
        total_seconds = max(0, duration_ms // 1000)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts: list[str] = []
        if hours:
            parts.append(f"{hours}h")
        if minutes or hours:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)


    def _find_task(self, task_id: str):
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc


    def _validate_readable_markdown_artifact(self, task_dir: Path, filename: str) -> Path:
        if not filename.endswith(".md"):
            raise TransitionError("only markdown artifacts can be viewed")
        path = (task_dir / filename).resolve()
        if path.parent != task_dir.resolve() or not path.exists():
            raise TaskNotFoundError(filename)
        return path


    def _validate_writable_markdown_artifact(self, task_dir: Path, filename: str) -> Path:
        if filename != "PLAN.md":
            raise TransitionError("only PLAN.md is editable")
        return self._validate_readable_markdown_artifact(task_dir, filename)


    def _validate_attachment_artifact(self, task, filename: str) -> Path:
        if task.state == TaskState.WAITING_CHECK_PLANS:
            return self._validate_writable_markdown_artifact(task.task_dir, filename)
        if task.state == TaskState.HUMAN_VERIFYING:
            note_path = task.metadata.human_verification.note_path or f"HUMAN-VERIFY-{task.metadata.cycle:03d}.md"
            if filename != note_path:
                raise TransitionError("attachments are only allowed for the active HUMAN-VERIFY note during human-verifying")
            return self._validate_readable_markdown_artifact(task.task_dir, filename)
        raise TransitionError("attachments are only allowed in waiting-check-plans or human-verifying")


    def _attachments_dir(self, task_dir: Path, *, create: bool = False) -> Path:
        return attachments_dir_for_task(task_dir, create=create)


    def _validate_readable_attachment(self, task_dir: Path, filename: str) -> Path:
        attachments_dir = self._attachments_dir(task_dir)
        path = (attachments_dir / filename).resolve()
        if path.parent != attachments_dir or not path.exists():
            raise TaskNotFoundError(filename)
        return path


    def _sorted_markdown_files(self, task_dir: Path) -> list[str]:
        files = [path.name for path in task_dir.glob("*.md")]
        return sorted(files, key=self._artifact_sort_key)


    def _build_stage_timing(self, metadata: TaskMetadata) -> TaskStageTiming:
        summaries_by_state: dict[TaskState, StageTimingSummary] = {
            state: StageTimingSummary(state=state)
            for state in STATE_ORDER
        }
        if not metadata.history:
            return TaskStageTiming(summaries=list(summaries_by_state.values()))

        history = sorted(metadata.history, key=lambda entry: entry.entered_at)
        now = datetime.now(timezone.utc)
        segments: list[StageTimingSegment] = []
        visit_counts: dict[TaskState, int] = {state: 0 for state in STATE_ORDER}
        total_duration_ms = 0
        ai_work_duration_ms = 0
        human_work_duration_ms = 0
        waiting_duration_ms = 0

        for index, entry in enumerate(history):
            next_entry = history[index + 1] if index + 1 < len(history) else None
            exited_at = next_entry.entered_at if next_entry else None
            end_time = exited_at or now
            raw_duration_ms = max(0, int((end_time - entry.entered_at).total_seconds() * 1000))
            duration_ms = 0 if entry.state in TERMINAL_STATES else raw_duration_ms
            is_current = next_entry is None and entry.state == metadata.state and entry.state not in TERMINAL_STATES
            visit_counts[entry.state] = visit_counts.get(entry.state, 0) + 1
            segment = StageTimingSegment(
                state=entry.state,
                entered_at=entry.entered_at,
                exited_at=exited_at,
                duration_ms=duration_ms,
                visit_index=visit_counts[entry.state],
                is_current=is_current,
            )
            segments.append(segment)
            summary = summaries_by_state.setdefault(entry.state, StageTimingSummary(state=entry.state))
            summary.total_duration_ms += duration_ms
            summary.latest_duration_ms = duration_ms
            summary.latest_entered_at = entry.entered_at
            summary.attempt_count += 1
            summary.is_current = is_current
            if entry.state in TERMINAL_STATES:
                continue
            total_duration_ms += duration_ms
            if entry.state in AI_ACTIVE_STATES:
                ai_work_duration_ms += duration_ms
            elif entry.state == TaskState.HUMAN_VERIFYING:
                human_work_duration_ms += duration_ms
            elif entry.state in WAITING_STATES:
                waiting_duration_ms += duration_ms

        ordered_summaries = [summaries_by_state[state] for state in STATE_ORDER]
        return TaskStageTiming(
            total_duration_ms=total_duration_ms,
            ai_work_duration_ms=ai_work_duration_ms,
            human_work_duration_ms=human_work_duration_ms,
            waiting_duration_ms=waiting_duration_ms,
            summaries=ordered_summaries,
            segments=segments,
        )


    def _build_human_review_state(self, task) -> HumanReviewState:
        metadata = task.metadata
        comments = self._load_current_cycle_line_comments(task, metadata)
        all_comments = self._load_all_line_comments(task, metadata)
        reviewer_qa_files = sorted(task.task_dir.glob("REVIEWER-QA-*.md"))
        reviewer_qa_path = reviewer_qa_files[-1].name if reviewer_qa_files else metadata.review.qa_path
        reviewer_qa_markdown = ""
        if reviewer_qa_path:
            reviewer_qa_file = task.task_dir / reviewer_qa_path
            if reviewer_qa_file.exists():
                reviewer_qa_markdown = reviewer_qa_file.read_text().rstrip()
        return HumanReviewState(
            note_path=metadata.human_verification.note_path,
            comments_path=metadata.human_verification.comments_path,
            note_markdown=metadata.human_verification.note_markdown,
            reviewer_qa_path=reviewer_qa_path,
            reviewer_qa_markdown=reviewer_qa_markdown,
            qa_path=metadata.human_verification.qa_path,
            qa_items=metadata.human_verification.qa_items if metadata.human_verification.qa_cycle == metadata.cycle else [],
            qa_total_count=len(metadata.human_verification.qa_items) if metadata.human_verification.qa_cycle == metadata.cycle else 0,
            qa_required_count=sum(1 for item in metadata.human_verification.qa_items if item.required) if metadata.human_verification.qa_cycle == metadata.cycle else 0,
            qa_completed_required_count=sum(1 for item in metadata.human_verification.qa_items if item.required and (item.checked or item.skipped)) if metadata.human_verification.qa_cycle == metadata.cycle else 0,
            total_comment_count=len(comments),
            unresolved_comment_count=sum(1 for comment in comments if not comment.resolved),
            historical_comment_count=max(0, len(all_comments) - len(comments)),
        )


    def _artifact_sort_key(self, filename: str) -> tuple[int, int, int, str]:
        request_draft_match = REQUEST_DRAFT_ARTIFACT_RE.match(filename)
        if request_draft_match:
            cycle = request_draft_match.group("cycle")
            return (0, int(cycle) if cycle else 0, 0, filename)
        if filename == "REQUEST.md":
            return (1, 0, 0, filename)
        if filename == PLANNER_RESTART_ARTIFACT:
            return (2, 0, 0, filename)
        if filename == "PLAN.md":
            return (3, 0, 0, filename)
        if filename == "SPLIT-PROPOSAL.md":
            return (3, 1, 0, filename)
        plan_approval_order = PLAN_APPROVAL_ARTIFACT_ORDER.get(filename)
        if plan_approval_order is not None:
            return (4, plan_approval_order, 0, filename)
        match = ARTIFACT_CYCLE_RE.match(filename)
        if match:
            kind = match.group(1)
            cycle = int(match.group("cycle"))
            kind_order = {"WORK": 0, "REVIEW": 1, "HUMAN-QA": 2, "REVIEWER-QA": 3, "HUMAN-VERIFY": 4}[kind]
            return (5, cycle, kind_order, filename)
        if filename == "COMMIT.md":
            return (7, 0, 0, filename)
        if filename.startswith("RETRO-") and filename.endswith(".md"):
            return (8, 0, 0, filename)
        return (6, 0, 0, filename)
