from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from ...exceptions import TransitionError
from ...markdown_attachments import normalize_markdown_attachments, store_attachment
from ...models import (
    PlanEditEvent,
    TaskContext,
    TaskMetadata,
    reset_plan_approval_tracking,
    utc_now,
)
from ...target_repo_guard import resolve_safe_target_repo_root
from ...commit_manager import CommitManager
from ..plan_approval_learning import classify_plan_change, load_plan_baseline_text, plan_text_hash

if TYPE_CHECKING:
    from ._protocol import _TaskServiceLike
else:
    _TaskServiceLike = object

class _ArtifactsMixin(_TaskServiceLike):
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
            "## Assistant Token Usage",
            "| Runtime Assistant | Used Assistant | Model | Sessions | Input Tokens | Cached Tokens | Output Tokens | Total Tokens |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ])
        token_usage_rows = self._assistant_token_usage_rows(task)
        if token_usage_rows:
            for row in token_usage_rows:
                lines.append(
                    f"| {self._markdown_table_cell(row.runtime_assistant)} "
                    f"| {self._markdown_table_cell(row.used_assistant)} "
                    f"| {self._markdown_table_cell(row.model)} "
                    f"| {row.sessions} "
                    f"| {self._format_token_total(row.input_tokens, row.input_unavailable_runs)} "
                    f"| {self._format_token_total(row.cached_tokens, row.cached_unavailable_runs)} "
                    f"| {self._format_token_total(row.output_tokens, row.output_unavailable_runs)} "
                    f"| {self._format_token_total(row.total_tokens, row.unavailable_runs)} |"
                )
        else:
            lines.append("| unavailable | unavailable | unavailable | 0 | unavailable | unavailable | unavailable | unavailable |")

        lines.extend([
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

        return self.target_repo_summary_filename(metadata), "\n".join(lines).encode("utf-8")


    def target_repo_summary_filename(self, metadata: TaskMetadata) -> str:
        branch_summary = CommitManager().sanitize_branch_summary(
            metadata.integration.final_branch_summary,
            fallback_title=metadata.title,
        )
        return f"{metadata.task_id}-{branch_summary}-summary.md"


    def legacy_target_repo_summary_filename(self, metadata: TaskMetadata) -> str:
        return f"{metadata.task_id}-summary.md"


    def target_repo_summary_dir(self, metadata: TaskMetadata, *, created_at: datetime | None = None) -> Path:
        target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        timestamp = created_at or datetime.now(timezone.utc)
        docs_root = self.scanner.config.resolve_target_repo_docs_root(target_repo_root)
        return docs_root / f"{timestamp.year:04d}" / f"{timestamp.month:02d}" / f"{timestamp.day:02d}"


    def target_repo_summary_path(self, metadata: TaskMetadata, *, created_at: datetime | None = None) -> Path:
        return self.target_repo_summary_dir(metadata, created_at=created_at) / self.target_repo_summary_filename(metadata)


    def legacy_target_repo_summary_path(self, metadata: TaskMetadata, *, created_at: datetime | None = None) -> Path:
        return self.target_repo_summary_dir(metadata, created_at=created_at) / self.legacy_target_repo_summary_filename(metadata)


    def find_target_repo_summary_path(self, metadata: TaskMetadata, *, created_at: datetime | None = None) -> Path:
        summary_path = self.target_repo_summary_path(metadata, created_at=created_at)
        if summary_path.exists() and summary_path.is_file():
            return summary_path
        legacy_path = self.legacy_target_repo_summary_path(metadata, created_at=created_at)
        if legacy_path.exists() and legacy_path.is_file():
            return legacy_path
        return summary_path


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


    def save_attachment(self, task_id: str, artifact_filename: str, upload_name: str, content_type: str | None, data: bytes) -> dict[str, str]:
        task = self._find_task(task_id)
        self._validate_attachment_artifact(task, artifact_filename)
        return store_attachment(task.task_dir, upload_name, content_type, data)


    def get_attachment(self, task_id: str, filename: str) -> tuple[Path, str]:
        task = self._find_task(task_id)
        path = self._validate_readable_attachment(task.task_dir, filename)
        return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"
