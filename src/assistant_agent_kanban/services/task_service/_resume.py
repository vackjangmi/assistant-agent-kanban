from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Literal

from ...enums import TaskState
from ...exceptions import TransitionError
from ...models import (
    TaskContext,
    TaskMetadata,
    reset_plan_approval_tracking,
    reset_review_loop_tracking,
    utc_now,
)
from ...plan_artifacts import validate_plan_markdown
from ...request_creator import RequestTemplateData, create_request
from ...retry_policy import clear_retry_gate
from ...split_proposals import SplitChildRequest, load_split_proposal

from ._data import PLANNER_RESTART_ARTIFACT

if TYPE_CHECKING:
    from ._protocol import _TaskServiceLike
else:
    _TaskServiceLike = object


class _ResumeMixin(_TaskServiceLike):
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


    def split_plan(self, task_id: str, *, by: str = "human"):
        task = self._find_task(task_id)
        if task.state != TaskState.WAITING_CHECK_PLANS:
            raise TransitionError("plan splitting is only allowed in waiting-check-plans")
        if self.transitions is None or self.locks is None:
            raise TransitionError("plan splitting requires lock manager")
        try:
            proposal = load_split_proposal(task.task_dir)
        except FileNotFoundError as exc:
            raise TransitionError("split proposal artifact is missing") from exc
        except ValueError as exc:
            raise TransitionError(f"split proposal artifact is invalid: {exc}") from exc
        created_child_dirs: list[Path] = []
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-split-plan"):
            try:
                child_task_ids: list[str] = []
                split_count = len(proposal.children)
                for index, child in enumerate(proposal.children, start=1):
                    child_dir = create_request(
                        self.scanner.config,
                        template=self._split_child_request_template(
                            task,
                            child,
                            split_index=index,
                            split_count=split_count,
                            split_reason=proposal.reason,
                        ),
                        target_repo_root=Path(task.metadata.target.repo_root),
                        base_branch=task.metadata.target.base_branch,
                        request_language=task.metadata.request.language,
                        slack_channel_id=task.metadata.slack.channel,
                        slack_thread_ts=task.metadata.slack.thread_ts,
                        created_by_user_id=task.metadata.created_by_user_id,
                        created_by_username=task.metadata.created_by_username,
                        runtime_pin=task.metadata.runtime_pin,
                    )
                    created_child_dirs.append(child_dir)
                    self._copy_parent_request_attachments(task.task_dir, child_dir)
                    self.scanner.scan()
                    child_task = self.scanner.find_task(child_dir.name)
                    child_task.metadata.parent_task_id = task.metadata.task_id
                    child_task.metadata.split_index = index
                    child_task.metadata.split_count = split_count
                    self.scanner.metadata_store.save(child_task.task_dir, child_task.metadata)
                    child_task_ids.append(child_task.metadata.task_id)

                task.metadata.closure.reason = "split_into_children"
                task.metadata.closure.closed_by = by
                task.metadata.closure.closed_at = utc_now()
                task.metadata.closure.child_task_ids = child_task_ids
                task.metadata.closure.note = proposal.reason.strip() or None
                task.metadata.plan.approved = False
                clear_retry_gate(task.metadata)
                self.scanner.metadata_store.save(task.task_dir, task.metadata)
                return self.transitions.move(task, target=TaskState.CLOSED, by=by, note="split into child requests")
            except Exception:
                for child_dir in created_child_dirs:
                    shutil.rmtree(child_dir, ignore_errors=True)
                raise


    def _split_child_request_template(
        self,
        task: TaskContext,
        child: SplitChildRequest,
        *,
        split_index: int,
        split_count: int,
        split_reason: str,
    ) -> RequestTemplateData:
        original_request = (task.task_dir / task.metadata.request.path).read_text()
        background = "\n".join(
            [
                f"Derived from parent task `{task.metadata.task_id}` ({split_index}/{split_count}).",
                "",
                "This child request must implement only its scoped portion of the original request.",
                "",
                "## Parent Split Reason",
                split_reason.strip() or "(none recorded)",
                "",
                "## Original Request",
                "```markdown",
                original_request.strip(),
                "```",
            ]
        ).strip()
        sibling_guard = [
            "Implement only this child request's scope.",
            "Do not implement sibling child request scopes unless this child request explicitly includes them.",
            "Keep the child request independently implementable; do not create ordering dependencies on sibling tasks.",
        ]
        return RequestTemplateData(
            title=child.title,
            goal=child.goal,
            background=background,
            plan_auto_approve=False,
            scope=child.scope,
            out_of_scope=child.out_of_scope,
            constraints=[*child.constraints, *sibling_guard],
            references=child.references,
            acceptance_criteria=child.acceptance_criteria,
        )


    def _copy_parent_request_attachments(self, parent_task_dir: Path, child_task_dir: Path) -> None:
        parent_attachments = parent_task_dir / "_attachments"
        if not parent_attachments.exists():
            return
        shutil.copytree(parent_attachments, child_task_dir / "_attachments", dirs_exist_ok=True)


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
            task.metadata.implementation.last_run_tokens = 0
            task.metadata.implementation.resume_mode = resume_mode
            if resume_mode == "current-settings":
                current_config = self.scanner.config
                task.metadata.implementation.session_id = None
                task.metadata.implementation.session_tokens = 0
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
