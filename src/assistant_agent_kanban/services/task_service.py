from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import subprocess
from typing import Literal, cast

from ..enums import STATE_ORDER, TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..locks import TaskLockManager
from ..log_parser import render_assistant_log
from ..markdown_attachments import attachments_dir_for_task, normalize_markdown_attachments, store_attachment
from ..models import (
    ChangedFileDetail,
    ChangedFileHunk,
    ChangedFileLine,
    ChangedFileRow,
    ChangedFileSide,
    ChangedFileSummary,
    HumanLineComment,
    HumanLineCommentsArtifact,
    HumanReviewState,
    PlanEditEvent,
    StageTimingSegment,
    StageTimingSummary,
    TaskDetail,
    TaskContext,
    TaskLogEntry,
    TaskLogs,
    TaskMetadata,
    TaskStageTiming,
    reset_plan_approval_tracking,
    reset_review_loop_tracking,
    utc_now,
)
from ..plan_artifacts import validate_plan_markdown
from ..request_parser import extract_goal_text, parse_request_markdown
from ..scanner import TERMINAL_STATES, KanbanScanner, derive_agent_status
from ..target_repo_guard import resolve_safe_target_repo_root
from ..transitions import TransitionManager
from .plan_approval_learning import PlanApprovalLearningService, classify_plan_change, load_plan_baseline_text, plan_text_hash
from ..retry_policy import clear_retry_gate


HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<header>.*)$")
ARTIFACT_CYCLE_RE = re.compile(r"^(WORK|REVIEW|REVIEWER-QA|HUMAN-VERIFY)-(?P<cycle>\d{3})\.md$")
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


PatchFileState = dict[str, str | int | bool | list[ChangedFileHunk] | None]


class TaskService:
    def __init__(
        self,
        scanner: KanbanScanner,
        runs_root: Path,
        kanban_root: Path,
        archive_runs_root: Path | None = None,
        *,
        metadata_store=None,
        transitions: TransitionManager | None = None,
        locks: TaskLockManager | None = None,
    ) -> None:
        self.scanner = scanner
        self.runs_root = runs_root
        self.kanban_root = kanban_root
        self.archive_runs_root = archive_runs_root or (runs_root.parent / "archive-runs")
        self.metadata_store = metadata_store
        self.transitions = transitions
        self.locks = locks
        self.plan_approval_learning = PlanApprovalLearningService(scanner)

    def get_task(self, task_id: str, *, include_changed_files: bool = True) -> TaskDetail:
        task = self._find_task(task_id)
        request_markdown_path = str((task.task_dir / task.metadata.request.path).resolve())
        markdown_files = self._sorted_markdown_files(task.task_dir)
        json_files = sorted(path.name for path in task.task_dir.glob("*.json") if path.name != "metadata.json")
        log_dir = self._task_runs_dir(task.metadata.task_id)
        log_files = self._visible_log_files(log_dir)
        changed_files = self._load_changed_files_for_task(task, require_available=False) if include_changed_files else []
        return TaskDetail(
            metadata=task.metadata,
            task_path=str(task.task_dir),
            request_markdown_path=request_markdown_path,
            markdown_files=markdown_files,
            json_files=json_files,
            log_files=log_files,
            changed_files_available=self._changed_files_available_for_task(task),
            changed_files=[entry.summary for entry in changed_files],
            stage_timing=self._build_stage_timing(task.metadata),
            human_review=self._build_human_review_state(task),
            agent_status=derive_agent_status(task.metadata, task.state),
        )

    def build_persisted_changed_files_markdown_artifact(self, task: TaskContext) -> tuple[str, bytes] | None:
        patch_path = self._resolve_patch_path(task.metadata.task_id, task.metadata.integration.patch_path)
        if patch_path is None or not patch_path.exists():
            return None
        patch_text = patch_path.read_text()
        changed_files = self._parse_patch(patch_text)
        lines = [
            f"# Changed Files ({len(changed_files)})",
            "",
            f"Patch source: `{patch_path.name}`",
            "",
        ]
        if not changed_files:
            lines.append("No changed files found.")
        else:
            for entry in changed_files:
                summary = entry.summary
                stats = f"+{summary.additions} / -{summary.deletions}, hunks={summary.hunk_count}"
                if summary.is_binary:
                    stats += ", binary"
                lines.append(f"- `{summary.display_path}` — {summary.change_type} ({stats})")
                if summary.previous_path and summary.previous_path != summary.path:
                    lines.append(f"  - previous: `{summary.previous_path}`")
        lines.append("")
        filename = f"CHANGED-FILES-{task.metadata.cycle:03d}.md"
        return filename, "\n".join(lines).encode("utf-8")

    def build_persisted_patch_artifact(self, task: TaskContext) -> tuple[str, bytes] | None:
        patch_path = self._resolve_patch_path(task.metadata.task_id, task.metadata.integration.patch_path)
        if patch_path is None or not patch_path.exists():
            return None
        return patch_path.name, patch_path.read_bytes()

    def build_target_repo_summary_artifact(self, task: TaskContext) -> tuple[str, bytes]:
        metadata = task.metadata
        stage_timing = self._build_stage_timing(metadata)
        changed_files = self._target_repo_changed_files(metadata)
        keywords = self._summary_keywords(metadata)
        lines = [
            f"# Task Summary: {metadata.title}",
            "",
            "## Overview",
            f"- Task ID: `{metadata.task_id}`",
            f"- Repo: `{metadata.target.repo_root}`",
            f"- Base branch: `{metadata.target.base_branch}`",
        ]
        if metadata.integration.final_branch_summary:
            lines.append(f"- Branch summary: `{metadata.integration.final_branch_summary}`")
        if metadata.integration.applied_at is not None:
            lines.append(f"- Verification applied at: {metadata.integration.applied_at.isoformat()}")

        lines.extend([
            "",
            "## Why / Keywords",
        ])
        goal = self._request_goal(task.task_dir)
        if goal:
            lines.append(f"- Goal: {goal}")
        plan = self._artifact_summary(task.task_dir / "PLAN.md")
        if plan:
            lines.append(f"- Plan summary: {plan}")
        review = self._latest_artifact_summary(task.task_dir, "REVIEW-*.md")
        if review:
            lines.append(f"- Review summary: {review}")
        human_review = self._latest_artifact_summary(task.task_dir, "HUMAN-VERIFY-*.md")
        if human_review:
            lines.append(f"- Human review summary: {human_review}")
        if keywords:
            lines.append(f"- Keywords: {', '.join(keywords)}")

        lines.extend([
            "",
            f"## Changed Files ({len(changed_files)})",
        ])
        if not changed_files:
            lines.append("- No changed files found.")
        else:
            for entry in changed_files:
                summary = entry.summary
                stats = f"+{summary.additions} / -{summary.deletions}, hunks={summary.hunk_count}"
                if summary.is_binary:
                    stats += ", binary"
                lines.append(f"- `{summary.display_path}` — {summary.change_type} ({stats})")
                if summary.previous_path and summary.previous_path != summary.path:
                    lines.append(f"  - previous: `{summary.previous_path}`")

        lines.extend([
            "",
            "## Time Summary",
            f"- Total: {self._format_duration_ms(stage_timing.total_duration_ms)}",
            f"- AI work: {self._format_duration_ms(stage_timing.ai_work_duration_ms)}",
            f"- Human work: {self._format_duration_ms(stage_timing.human_work_duration_ms)}",
            f"- Waiting: {self._format_duration_ms(stage_timing.waiting_duration_ms)}",
            "",
            "## Stage Breakdown",
        ])
        for summary in stage_timing.summaries:
            if summary.attempt_count == 0:
                continue
            lines.append(
                "- "
                f"`{summary.state}` — total {self._format_duration_ms(summary.total_duration_ms)}, "
                f"latest {self._format_duration_ms(summary.latest_duration_ms)}, "
                f"attempts {summary.attempt_count}"
            )
        lines.append("")

        return f"{metadata.task_id}-summary.md", "\n".join(lines).encode("utf-8")

    def target_repo_summary_path(self, metadata: TaskMetadata, *, created_at: datetime | None = None) -> Path:
        target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        timestamp = created_at or datetime.now(timezone.utc)
        docs_root = self.scanner.config.resolve_target_repo_docs_root(target_repo_root)
        return docs_root / f"{timestamp.year:04d}" / f"{timestamp.month:02d}" / f"{timestamp.day:02d}" / f"{metadata.task_id}-summary.md"

    def get_logs(self, task_id: str) -> TaskLogs:
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc
        log_dir = self._task_runs_dir(task.metadata.task_id)
        entries: list[TaskLogEntry] = []
        if log_dir.exists():
            paths = sorted(
                [path for path in log_dir.glob("*.jsonl") if path.is_file() and self._should_show_log_file(path.name)],
                key=lambda path: path.stat().st_mtime,
                reverse=False,
            )
            for path in paths:
                raw_content = path.read_text()
                rendered_content = render_assistant_log(raw_content)
                debug_rendered_content = render_assistant_log(raw_content, debug=True)
                entries.append(
                    TaskLogEntry(
                        name=path.name,
                        path=str(path),
                        rendered_content=rendered_content or None,
                        debug_rendered_content=debug_rendered_content or None,
                        updated_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                    )
                )
        return TaskLogs(task_id=task.metadata.task_id, entries=entries)

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

    def get_changed_file(self, task_id: str, changed_file_id: str) -> ChangedFileDetail:
        task = self._find_task(task_id)
        changed_files = self._load_changed_files_for_task(task, require_available=True)
        for entry in changed_files:
            if entry.summary.id == changed_file_id:
                return entry.model_copy(update={"comments": self._comments_for_file(task, entry.summary.path)})
        raise TaskNotFoundError(changed_file_id)

    def get_changed_file_by_path(self, task_id: str, path: str) -> ChangedFileDetail:
        task = self._find_task(task_id)
        changed_files = self._load_changed_files_for_task(task, require_available=True)
        for entry in changed_files:
            if entry.summary.path == path:
                return entry.model_copy(update={"comments": self._comments_for_file(task, entry.summary.path)})
        raise TaskNotFoundError(path)

    def get_markdown_artifact(self, task_id: str, filename: str) -> str:
        task = self._find_task(task_id)
        path = self._validate_readable_markdown_artifact(task.task_dir, filename)
        return path.read_text()

    def update_markdown_artifact(self, task_id: str, filename: str, content: str, *, by: str = "human") -> None:
        task = self._find_task(task_id)
        if task.state != "waiting-check-plans":
            raise TransitionError("markdown editing is only allowed in waiting-check-plans")
        path = self._validate_writable_markdown_artifact(task.task_dir, filename)
        if not content.strip():
            raise TransitionError("PLAN.md cannot be empty")
        previous_content = path.read_text() if path.exists() else ""
        normalized = normalize_markdown_attachments(task.task_dir, content)
        path.write_text(normalized.rstrip() + "\n")
        current_content = path.read_text()
        baseline_text = load_plan_baseline_text(task.task_dir)
        change_classification = cast(
            Literal["none", "trivial", "substantive", "unknown"],
            classify_plan_change(
                baseline_text=baseline_text or previous_content or current_content,
                current_text=current_content,
            ),
        )
        task.metadata.plan.edit_events.append(
            PlanEditEvent(
                edited_at=utc_now(),
                edited_by=by,
                from_revision=task.metadata.plan.revision,
                to_revision=task.metadata.plan.revision + 1,
                before_hash=plan_text_hash(previous_content) if previous_content.strip() else None,
                after_hash=plan_text_hash(current_content),
                change_classification=change_classification,
            )
        )
        task.metadata.plan.edit_events = task.metadata.plan.edit_events[-20:]
        task.metadata.plan.revision += 1
        task.metadata.plan.approved = False
        reset_plan_approval_tracking(task.metadata.plan_approval)
        task.metadata.plan_approval.auto_progress_at = None
        self.scanner.metadata_store.save(task.task_dir, task.metadata)

    def approve_plan(self, task_id: str, *, by: str = "human"):
        task = self._find_task(task_id)
        if task.state != TaskState.WAITING_CHECK_PLANS:
            raise TransitionError(f"manual transition not allowed: {task.state.value} -> {TaskState.TODOS.value}")
        if self.transitions is None or self.locks is None:
            raise TransitionError("manual plan approval requires lock manager")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-todos"):
            validation = validate_plan_markdown(
                (task.task_dir / "PLAN.md").read_text(),
                request_language=task.metadata.request.language,
            )
            if validation.missing_heading is not None:
                raise TransitionError(f"PLAN.md missing required section: {validation.missing_heading}")
            approval_record = self.plan_approval_learning.build_human_approval_record(task, approved_by=by)
            approval_markdown_path = task.task_dir / "PLAN-HUMAN-APPROVAL.md"
            approval_json_path = task.task_dir / "PLAN-HUMAN-APPROVAL.json"
            approval_record.artifact_path = approval_markdown_path.name
            approval_markdown_path.write_text(self._render_human_plan_approval_markdown(task, approval_record))
            approval_json_path.write_text(json.dumps(approval_record.model_dump(mode="json"), indent=2) + "\n")
            task.metadata.plan_approval.human_approvals.append(approval_record)
            task.metadata.plan_approval.human_approvals = task.metadata.plan_approval.human_approvals[-10:]
            task.metadata.plan.approved = True
            reset_plan_approval_tracking(task.metadata.plan_approval)
            task.metadata.plan_approval.auto_progress_at = None
            task.metadata.plan_approval.resolved_by = by
            task.metadata.plan_approval.resolved_at = utc_now()
            clear_retry_gate(task.metadata)
            self.scanner.metadata_store.save(task.task_dir, task.metadata)
            moved = self.transitions.move(task, target=TaskState.TODOS, by=by, note="manual approval")
            moved.metadata.plan_approval.human_approvals[-1].outcome_state = moved.state
            moved.metadata.plan_approval.human_approvals[-1].strong_positive = self.plan_approval_learning.is_strong_positive(
                moved,
                moved.metadata.plan_approval.human_approvals[-1],
            )
            (moved.task_dir / "PLAN-HUMAN-APPROVAL.md").write_text(
                self._render_human_plan_approval_markdown(moved, moved.metadata.plan_approval.human_approvals[-1])
            )
            (moved.task_dir / "PLAN-HUMAN-APPROVAL.json").write_text(
                json.dumps(moved.metadata.plan_approval.human_approvals[-1].model_dump(mode="json"), indent=2) + "\n"
            )
            self.scanner.metadata_store.save(moved.task_dir, moved.metadata)
            return moved

    def append_human_reviewer_qa_message(self, task_id: str, *, message: str, by: str = "human") -> TaskContext:
        task = self._find_task(task_id)
        return self._append_human_reviewer_qa_message(task, message=message, by=by)

    def _can_resume_implementer_from_todos_retry_gate(self, task: TaskContext) -> bool:
        if task.state != TaskState.TODOS:
            return False
        retry_reason = task.metadata.retry_gate.reason or ""
        if task.metadata.retry_gate.not_before is None:
            return False
        if retry_reason.startswith("implementation-"):
            return True
        return retry_reason == "review-rework-backstop" and not task.metadata.review.human_rework_required

    def resume_review_loop(self, task_id: str, *, by: str = "human", message: str | None = None):
        task = self._find_task(task_id)
        if task.state != TaskState.TODOS:
            raise TransitionError("review loop resume is only allowed in todos")
        if not task.metadata.review.human_rework_required:
            raise TransitionError("review loop resume is only allowed when human review is required")
        if self.locks is None:
            raise TransitionError("review loop resume requires lock manager")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-review-loop-resume"):
            self._append_human_reviewer_qa_message(task, message=message, by=by)
            reset_review_loop_tracking(task.metadata.review)
            clear_retry_gate(task.metadata)
            self.scanner.metadata_store.save(task.task_dir, task.metadata)
            return self.scanner.find_task(task.metadata.task_id)

    def resume_planner(self, task_id: str, *, by: str = "human", message: str | None = None):
        task = self._find_task(task_id)
        if task.state != TaskState.REQUESTS:
            raise TransitionError("planner resume is only allowed in requests")
        retry_reason = task.metadata.retry_gate.reason or ""
        if not retry_reason.startswith("planner-"):
            raise TransitionError("planner resume is only allowed when a planner retry gate is present")
        if self.locks is None:
            raise TransitionError("planner resume requires lock manager")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-planner-resume"):
            if not (message or "").strip():
                task.metadata.plan.restart_message_path = None
            self._append_planner_restart_message(task, message=message, by=by)
            clear_retry_gate(task.metadata)
            task.metadata.plan.resolved_model = None
            task.metadata.plan.session_id = None
            task.metadata.plan.session_tokens = 0
            task.metadata.plan.last_run_tokens = 0
            self.scanner.metadata_store.save(task.task_dir, task.metadata)
            return self.scanner.find_task(task.metadata.task_id)

    def resume_reviewer(
        self,
        task_id: str,
        *,
        by: str = "human",
        resume_mode: Literal["pinned", "current-settings"] = "pinned",
        message: str | None = None,
    ):
        task = self._find_task(task_id)
        if task.state != TaskState.WAITING_REVIEWS:
            raise TransitionError("reviewer resume is only allowed in waiting-reviews")
        retry_reason = task.metadata.retry_gate.reason or ""
        review_retry = retry_reason.startswith("review-")
        if not review_retry or task.metadata.retry_gate.not_before is None:
            raise TransitionError("reviewer resume is only allowed when an active review retry gate is present")
        if self.locks is None:
            raise TransitionError("reviewer resume requires lock manager")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-reviewer-resume"):
            self._append_human_reviewer_qa_message(task, message=message, by=by)
            clear_retry_gate(task.metadata)
            task.metadata.review.last_verdict = None
            task.metadata.review.resolved_model = None
            task.metadata.review.session_id = None
            task.metadata.review.session_tokens = 0
            task.metadata.review.last_run_tokens = 0
            task.metadata.review.resume_mode = resume_mode
            if resume_mode == "current-settings":
                current_config = self.scanner.config
                task.metadata.review.resume_backend_override = current_config.backend_for_role("reviewer")
                task.metadata.review.resume_model_override = current_config.role_model("reviewer")
            else:
                task.metadata.review.resume_backend_override = None
                task.metadata.review.resume_model_override = None
            self.scanner.metadata_store.save(task.task_dir, task.metadata)
            return self.scanner.find_task(task.metadata.task_id)

    def resume_implementer(
        self,
        task_id: str,
        *,
        by: str = "human",
        resume_mode: Literal["pinned", "current-settings"] = "pinned",
        message: str | None = None,
    ):
        task = self._find_task(task_id)
        if task.state != TaskState.TODOS:
            raise TransitionError("implementer resume is only allowed in todos")
        if not self._can_resume_implementer_from_todos_retry_gate(task):
            raise TransitionError(
                "implementer resume is only allowed when an active implementation retry gate or paused review backstop is present"
            )
        if self.locks is None:
            raise TransitionError("implementer resume requires lock manager")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-implementer-resume"):
            self._append_human_reviewer_qa_message(task, message=message, by=by)
            clear_retry_gate(task.metadata)
            task.metadata.implementation.last_result = None
            task.metadata.implementation.resolved_model = None
            task.metadata.implementation.session_id = None
            task.metadata.implementation.session_tokens = 0
            task.metadata.implementation.last_run_tokens = 0
            task.metadata.implementation.resume_mode = resume_mode
            if resume_mode == "current-settings":
                current_config = self.scanner.config
                task.metadata.implementation.resume_backend_override = current_config.backend_for_role("implementer")
                task.metadata.implementation.resume_model_override = current_config.role_model("implementer")
            else:
                task.metadata.implementation.resume_backend_override = None
                task.metadata.implementation.resume_model_override = None
            self.scanner.metadata_store.save(task.task_dir, task.metadata)
            return self.scanner.find_task(task.metadata.task_id)

    def _append_human_reviewer_qa_message(self, task, *, message: str | None, by: str) -> TaskContext:
        normalized_message = (message or "").strip()
        if not normalized_message:
            return task
        expected_qa_path = f"REVIEWER-QA-{task.metadata.cycle:03d}.md"
        if task.metadata.review.qa_path != expected_qa_path:
            task.metadata.review.qa_path = expected_qa_path
            task.metadata.review.qa_session_id = None
            task.metadata.review.qa_last_run_tokens = 0
            task.metadata.review.qa_session_tokens = 0
            task.metadata.review.qa_resolved_model = None
        qa_path = task.task_dir / task.metadata.review.qa_path
        existing = qa_path.read_text().rstrip() if qa_path.exists() else ""
        exchange_count = existing.count("## Question") + 1
        now = datetime.now(timezone.utc).isoformat()
        sections: list[str] = []
        if existing:
            sections.extend([existing, ""])
        else:
            sections.extend(
                [
                    "# Reviewer Q&A",
                    "",
                    f"- Cycle: {task.metadata.cycle:03d}",
                    "",
                ]
            )
        sections.extend(
            [
                f"## Question {exchange_count}",
                f"- Asked by: {by}",
                f"- Asked at: {now}",
                "- Source: human resume note",
                "",
                normalized_message,
                "",
            ]
        )
        qa_path.write_text("\n".join(sections).rstrip() + "\n")
        return task

    def _append_planner_restart_message(self, task, *, message: str | None, by: str) -> TaskContext:
        normalized_message = (message or "").strip()
        if not normalized_message:
            return task
        task.metadata.plan.restart_message_path = PLANNER_RESTART_ARTIFACT
        restart_path = task.task_dir / PLANNER_RESTART_ARTIFACT
        now = datetime.now(timezone.utc).isoformat()
        sections = [
            "# Planner Restart Notes",
            "",
            "Saved manual context for the next planner rerun.",
            "",
            "## Note 1",
            f"- Added by: {by}",
            f"- Added at: {now}",
            "- Source: manual planner restart",
            "",
            normalized_message,
            "",
        ]
        restart_path.write_text("\n".join(sections).rstrip() + "\n")
        return task

    def _ensure_reviewer_qa_path(self, metadata: TaskMetadata) -> str:
        expected_path = f"REVIEWER-QA-{metadata.cycle:03d}.md"
        if metadata.review.qa_path != expected_path:
            metadata.review.qa_path = expected_path
        return expected_path

    def _render_human_plan_approval_markdown(self, task, approval_record) -> str:
        signals = ", ".join(approval_record.ai_risk_signals) if approval_record.ai_risk_signals else "none"
        return "\n".join(
            [
                "# Human Plan Approval",
                "",
                f"- Approved by: {approval_record.approved_by}",
                f"- Approved at: {approval_record.approved_at.isoformat()}",
                f"- Plan revision: {approval_record.plan_revision}",
                f"- Change classification: {approval_record.change_classification}",
                f"- Strong positive: {'yes' if approval_record.strong_positive else 'no'}",
                f"- Prior AI disposition: {approval_record.ai_disposition or 'unknown'}",
                f"- Prior AI confidence: {approval_record.ai_confidence or 'unknown'}",
                f"- Prior AI risk signals: {signals}",
                "",
                approval_record.ai_rationale or "No AI rationale recorded.",
                "",
                "## Request",
                (task.task_dir / task.metadata.request.path).read_text().rstrip(),
                "",
                "## Plan",
                (task.task_dir / "PLAN.md").read_text().rstrip(),
            ]
        ) + "\n"

    def save_attachment(self, task_id: str, artifact_filename: str, upload_name: str, content_type: str | None, data: bytes) -> dict[str, str]:
        task = self._find_task(task_id)
        self._validate_attachment_artifact(task, artifact_filename)
        return store_attachment(task.task_dir, upload_name, content_type, data)

    def get_attachment(self, task_id: str, filename: str) -> tuple[Path, str]:
        task = self._find_task(task_id)
        path = self._validate_readable_attachment(task.task_dir, filename)
        return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def update_completed_group_override(self, task_id: str, *, by: str, group: str | None) -> TaskContext:
        task = self._find_task(task_id)
        if task.state != TaskState.DONE:
            raise TransitionError("completed group override can only be updated for done tasks")
        normalized = (group or "").strip() or None
        if normalized is not None and len(normalized) > 200:
            raise TransitionError("completed group override is too long")
        if self.metadata_store is None or self.locks is None:
            raise TransitionError("completed group override updates require a configured runtime")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-completed-group"):
            task.metadata.completed_group_override = normalized
            self.metadata_store.save(task.task_dir, task.metadata)
            return task

    def set_changed_file_viewed(self, task_id: str, changed_file_id: str, *, by: str, viewed: bool) -> ChangedFileSummary:
        task = self._find_task(task_id)
        if task.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("changed file viewed state is only available during human verification")
        if self.metadata_store is None or self.locks is None:
            raise TransitionError("changed file viewed updates require a configured runtime")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-changed-file-viewed"):
            changed_files = self._load_changed_files_for_task(task, require_available=True)
            for entry in changed_files:
                if entry.summary.id != changed_file_id:
                    continue
                viewed_files = self._current_cycle_viewed_files(task.metadata, create=True)
                if viewed:
                    viewed_files[entry.summary.path] = True
                else:
                    viewed_files.pop(entry.summary.path, None)
                self.metadata_store.save(task.task_dir, task.metadata)
                return entry.summary.model_copy(update={"viewed": viewed})
        raise TaskNotFoundError(changed_file_id)

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
            is_current = next_entry is None and entry.state == metadata.state
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
            total_comment_count=len(comments),
            unresolved_comment_count=sum(1 for comment in comments if not comment.resolved),
            historical_comment_count=max(0, len(all_comments) - len(comments)),
        )

    def _artifact_sort_key(self, filename: str) -> tuple[int, int, int, str]:
        if filename == "REQUEST.md":
            return (0, 0, 0, filename)
        if filename == PLANNER_RESTART_ARTIFACT:
            return (1, 0, 0, filename)
        if filename == "PLAN.md":
            return (2, 0, 0, filename)
        match = ARTIFACT_CYCLE_RE.match(filename)
        if match:
            kind = match.group(1)
            cycle = int(match.group("cycle"))
            kind_order = {"WORK": 0, "REVIEW": 1, "REVIEWER-QA": 2, "HUMAN-VERIFY": 3}[kind]
            return (3, cycle, kind_order, filename)
        if filename == "COMMIT.md":
            return (5, 0, 0, filename)
        if filename.startswith("RETRO-") and filename.endswith(".md"):
            return (6, 0, 0, filename)
        return (4, 0, 0, filename)

    def _load_changed_files(self, task_id: str, *, require_available: bool) -> list[ChangedFileDetail]:
        task = self._find_task(task_id)
        return self._load_changed_files_for_task(task, require_available=require_available)

    def _changed_files_available_for_task(self, task) -> bool:
        metadata = task.metadata
        if metadata.state not in {TaskState.HUMAN_VERIFYING, TaskState.DONE}:
            return False
        if metadata.integration.patch_path:
            try:
                self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
            except TransitionError:
                return False
        if metadata.state == TaskState.HUMAN_VERIFYING:
            return True
        patch_path = self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
        return bool(patch_path and patch_path.exists())

    def _load_changed_files_for_task(self, task, *, require_available: bool) -> list[ChangedFileDetail]:
        if task.metadata.state not in {"human-verifying", "done"}:
            if require_available:
                raise TransitionError("changed files are only available during or after human verification")
            return []
        patch_text = self._resolve_changed_files_patch(task.metadata, require_available=require_available)
        if patch_text is None:
            return []
        return self._apply_changed_file_viewed_state(self._parse_patch(patch_text), task.metadata)

    def _apply_changed_file_viewed_state(self, details: list[ChangedFileDetail], metadata: TaskMetadata) -> list[ChangedFileDetail]:
        viewed_files = self._current_cycle_viewed_files(metadata)
        if not viewed_files:
            return details
        return [
            entry.model_copy(update={"summary": entry.summary.model_copy(update={"viewed": bool(viewed_files.get(entry.summary.path))})})
            for entry in details
        ]

    def _current_cycle_viewed_files(self, metadata: TaskMetadata, *, create: bool = False) -> dict[str, bool]:
        human_verification = metadata.human_verification
        if human_verification.viewed_cycle != metadata.cycle:
            if not create:
                return {}
            human_verification.viewed_cycle = metadata.cycle
            human_verification.viewed_files = {}
        return human_verification.viewed_files

    def _resolve_changed_files_patch(self, metadata: TaskMetadata, *, require_available: bool) -> str | None:
        if metadata.integration.patch_path:
            try:
                self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
            except TransitionError:
                if require_available:
                    raise
                return None
        if metadata.state == TaskState.HUMAN_VERIFYING and metadata.integration.applied:
            patch_text = self._target_repo_diff_against_base(metadata, ref=None)
            if patch_text is not None:
                return patch_text
        try:
            patch_path = self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
        except TransitionError:
            if require_available:
                raise
            return None
        if patch_path is None or not patch_path.exists():
            if require_available:
                raise TaskNotFoundError(f"patch for {metadata.task_id}")
            return None
        return patch_path.read_text()

    def _load_current_cycle_line_comments(self, task, metadata: TaskMetadata) -> list[HumanLineComment]:
        comments_path = self._resolve_comments_path(task.task_dir, metadata)
        if comments_path is None or not comments_path.exists():
            return []
        artifact = HumanLineCommentsArtifact.model_validate_json(comments_path.read_text())
        return [self._annotate_comment(comment, metadata.cycle, editable=True) for comment in artifact.comments]

    def _load_all_line_comments(self, task, metadata: TaskMetadata) -> list[HumanLineComment]:
        comments_by_id: OrderedDict[str, HumanLineComment] = OrderedDict()
        for path in sorted(task.task_dir.glob("HUMAN-VERIFY-*.comments.json")):
            cycle = self._cycle_from_comment_artifact_name(path.name)
            if cycle is None:
                continue
            artifact = HumanLineCommentsArtifact.model_validate_json(path.read_text())
            editable = cycle == metadata.cycle
            for comment in artifact.comments:
                annotated = self._annotate_comment(comment, cycle, editable=editable)
                comments_by_id[annotated.id] = annotated
        return list(comments_by_id.values())

    def _annotate_comment(self, comment: HumanLineComment, cycle: int, *, editable: bool) -> HumanLineComment:
        return comment.model_copy(update={"cycle": comment.cycle or cycle, "editable": editable})

    def _resolve_comments_path(self, task_dir: Path, metadata: TaskMetadata) -> Path | None:
        raw_path = metadata.human_verification.comments_path
        if not raw_path and metadata.human_verification.note_path:
            raw_path = f"{metadata.human_verification.note_path[:-3]}.comments.json"
        if not raw_path:
            return None
        resolved_task_dir = task_dir.resolve()
        resolved = (resolved_task_dir / raw_path).resolve()
        if resolved.parent != resolved_task_dir:
            raise TransitionError("human verification comments are unavailable because comments path is outside the task directory")
        return resolved

    def _comments_for_file(self, task, path: str) -> list[HumanLineComment]:
        return [comment for comment in self._load_all_line_comments(task, task.metadata) if comment.anchor.path == path]

    def _cycle_from_comment_artifact_name(self, filename: str) -> int | None:
        match = re.match(r"^HUMAN-VERIFY-(\d{3})\.comments\.json$", filename)
        if not match:
            return None
        return int(match.group(1))

    def _target_repo_diff_against_base(self, metadata: TaskMetadata, *, ref: str | None) -> str | None:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError:
            return None
        range_spec = metadata.target.base_branch if ref is None else f"{metadata.target.base_branch}..{ref}"
        command = ["git", "-C", str(target_repo_root), "diff", "--binary", range_spec]
        diff = subprocess.run(command, capture_output=True, text=True, check=False)
        if diff.returncode != 0:
            return None
        return diff.stdout


    def _resolve_patch_path(self, task_id: str, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        patch_path = Path(raw_path).expanduser()
        if patch_path.is_absolute():
            resolved = patch_path.resolve()
        else:
            resolved = (self.kanban_root.expanduser().resolve().parent / patch_path).resolve()
        managed_roots = [
            (self.runs_root / task_id).resolve(),
            (self.archive_runs_root / task_id).resolve(),
        ]
        for managed_root in managed_roots:
            try:
                resolved.relative_to(managed_root)
                return resolved
            except ValueError:
                continue
        raise TransitionError("changed files are unavailable because patch path is outside the managed runs roots")
        return resolved

    def _task_runs_dir(self, task_id: str) -> Path:
        live_dir = self.runs_root / task_id
        if live_dir.exists():
            return live_dir
        return self.archive_runs_root / task_id

    def _parse_patch(self, patch_text: str) -> list[ChangedFileDetail]:
        details: list[ChangedFileDetail] = []
        current_file: PatchFileState | None = None
        current_hunk: ChangedFileHunk | None = None
        old_line_number = 0
        new_line_number = 0
        pending_removals: list[ChangedFileLine] = []
        pending_additions: list[ChangedFileLine] = []

        def file_int(key: str) -> int:
            if current_file is None:
                return 0
            value = current_file.get(key)
            return value if isinstance(value, int) else 0

        def file_str(key: str) -> str | None:
            if current_file is None:
                return None
            value = current_file.get(key)
            return value if isinstance(value, str) else None

        def file_bool(key: str) -> bool:
            if current_file is None:
                return False
            value = current_file.get(key)
            return value if isinstance(value, bool) else False

        def file_hunks() -> list[ChangedFileHunk]:
            if current_file is None:
                return []
            value = current_file.get("hunks")
            return value if isinstance(value, list) else []

        def empty_side() -> ChangedFileSide:
            return ChangedFileSide(kind="empty")

        def side_from_line(line: ChangedFileLine) -> ChangedFileSide:
            line_number = line.old_line_number if line.kind != "add" else line.new_line_number
            return ChangedFileSide(kind=line.kind, line_number=line_number, content=line.content)

        def flush_pending() -> None:
            nonlocal pending_removals, pending_additions
            if current_hunk is None:
                pending_removals = []
                pending_additions = []
                return
            pair_count = max(len(pending_removals), len(pending_additions))
            for index in range(pair_count):
                left_line = pending_removals[index] if index < len(pending_removals) else None
                right_line = pending_additions[index] if index < len(pending_additions) else None
                current_hunk.rows.append(
                    ChangedFileRow(
                        left=side_from_line(left_line) if left_line else empty_side(),
                        right=side_from_line(right_line) if right_line else empty_side(),
                    )
                )
            pending_removals = []
            pending_additions = []

        def finish_hunk() -> None:
            nonlocal current_hunk
            if current_hunk is None:
                return
            flush_pending()
            if current_file is not None:
                file_hunks().append(current_hunk)
            current_hunk = None

        def finish_file() -> None:
            nonlocal current_file
            if current_file is None:
                return
            finish_hunk()
            old_path = file_str("old_path") or ""
            new_path = file_str("new_path") or ""
            renamed_from = file_str("rename_from")
            renamed_to = file_str("rename_to")
            if old_path == "/dev/null":
                path = new_path
                display_path = new_path
                previous_path = None
                change_type = "added"
            elif new_path == "/dev/null":
                path = old_path
                display_path = old_path
                previous_path = None
                change_type = "deleted"
            elif isinstance(renamed_from, str) and isinstance(renamed_to, str) and renamed_from != renamed_to:
                path = renamed_to
                display_path = f"{renamed_from} -> {renamed_to}"
                previous_path = renamed_from
                change_type = "renamed"
            else:
                path = new_path or old_path
                display_path = path
                previous_path = None
                change_type = "modified"
            additions = file_int("additions")
            deletions = file_int("deletions")
            hunks = file_hunks()
            summary = ChangedFileSummary(
                id=self._changed_file_id(path, previous_path),
                path=path,
                display_path=display_path,
                previous_path=previous_path,
                change_type=change_type,
                additions=additions,
                deletions=deletions,
                hunk_count=len(hunks),
                is_binary=file_bool("is_binary"),
            )
            details.append(ChangedFileDetail(summary=summary, hunks=hunks.copy()))
            current_file = None

        for raw_line in patch_text.splitlines():
            if raw_line.startswith("diff --git "):
                finish_file()
                old_path, new_path = self._parse_diff_header(raw_line)
                current_file = {
                    "old_path": old_path,
                    "new_path": new_path,
                    "rename_from": None,
                    "rename_to": None,
                    "additions": 0,
                    "deletions": 0,
                    "hunks": [],
                    "is_binary": False,
                }
                continue
            if current_file is None:
                continue
            if raw_line.startswith("rename from "):
                current_file["rename_from"] = raw_line.removeprefix("rename from ")
                continue
            if raw_line.startswith("rename to "):
                current_file["rename_to"] = raw_line.removeprefix("rename to ")
                continue
            if raw_line.startswith("--- "):
                current_file["old_path"] = self._normalize_patch_path(raw_line.removeprefix("--- "))
                continue
            if raw_line.startswith("+++ "):
                current_file["new_path"] = self._normalize_patch_path(raw_line.removeprefix("+++ "))
                continue
            if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
                current_file["is_binary"] = True
                continue
            match = HUNK_HEADER_RE.match(raw_line)
            if match:
                finish_hunk()
                old_line_number = int(match.group("old_start"))
                new_line_number = int(match.group("new_start"))
                current_hunk = ChangedFileHunk(
                    header=raw_line,
                    old_start=old_line_number,
                    new_start=new_line_number,
                )
                continue
            if current_hunk is None:
                continue
            if raw_line.startswith("\\ No newline at end of file"):
                continue
            prefix = raw_line[:1]
            content = raw_line[1:] if raw_line else ""
            if prefix == " ":
                flush_pending()
                line = ChangedFileLine(
                    kind="context",
                    old_line_number=old_line_number,
                    new_line_number=new_line_number,
                    content=content,
                )
                current_hunk.unified_lines.append(line)
                current_hunk.rows.append(
                    ChangedFileRow(
                        left=ChangedFileSide(kind="context", line_number=old_line_number, content=content),
                        right=ChangedFileSide(kind="context", line_number=new_line_number, content=content),
                    )
                )
                old_line_number += 1
                new_line_number += 1
                continue
            if prefix == "-":
                line = ChangedFileLine(kind="remove", old_line_number=old_line_number, content=content)
                current_hunk.unified_lines.append(line)
                pending_removals.append(line)
                current_file["deletions"] = file_int("deletions") + 1
                old_line_number += 1
                continue
            if prefix == "+":
                line = ChangedFileLine(kind="add", new_line_number=new_line_number, content=content)
                current_hunk.unified_lines.append(line)
                pending_additions.append(line)
                current_file["additions"] = file_int("additions") + 1
                new_line_number += 1
        finish_file()
        return details

    def _changed_file_id(self, path: str, previous_path: str | None) -> str:
        stable_key = f"{previous_path or ''}\0{path}"
        return hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:12]

    def _parse_diff_header(self, raw_line: str) -> tuple[str, str]:
        parts = raw_line.split(" ", 3)
        if len(parts) < 4:
            return "", ""
        suffix = parts[3]
        old_token, _, new_token = suffix.partition(" b/")
        old_path = self._normalize_patch_path(old_token)
        new_path = self._normalize_patch_path(f"b/{new_token}" if new_token else "")
        return old_path, new_path

    def _normalize_patch_path(self, raw_path: str) -> str:
        path = raw_path.strip()
        if path in {"/dev/null", ""}:
            return path
        if path.startswith("a/") or path.startswith("b/"):
            return path[2:]
        return path
