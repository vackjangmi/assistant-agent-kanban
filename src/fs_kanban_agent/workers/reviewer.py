from __future__ import annotations

import asyncio
from pathlib import Path

from ..enums import TaskState
from ..integration_manager import IntegrationManager
from ..models import TaskErrorInfo
from ..opencode_adapter import OpenCodeAdapter
from ..retry_policy import apply_retry_gate, clear_retry_gate
from .base import WorkerBase


class ReviewerWorker(WorkerBase):
    worker_name = "reviewer"

    def __init__(self, *args, adapter: OpenCodeAdapter, integration_manager: IntegrationManager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.integration_manager = integration_manager

    async def run_once(self) -> bool:
        tasks = [task for task in self.scanner.scan() if task.state == TaskState.WAITING_REVIEWS]
        if not tasks:
            return False
        task = tasks[0]
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            reviewing = self.transitions.move(task, TaskState.REVIEWING, by=self.worker_name)
            workspace_repo = reviewing.metadata.implementation.workspace
            if workspace_repo is None:
                apply_retry_gate(reviewing.metadata, reason="review-no-workspace")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-no-workspace", message="review skipped because implementation workspace is missing")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: missing workspace")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            workspace_path = Path(workspace_repo)
            if self.workspace_has_local_commits(workspace_path, task.metadata.target.base_branch):
                apply_retry_gate(reviewing.metadata, reason="review-local-commits")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-local-commits", message="review skipped because workspace contains local commits")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: workspace has local commits")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            if not self.workspace_has_changes(workspace_path):
                apply_retry_gate(reviewing.metadata, reason="review-no-changes")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-no-changes", message="review skipped because workspace has no file changes")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: no workspace changes")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            run_log_path = self.task_log_dir(task.metadata.task_id) / f"reviewer-{reviewing.metadata.cycle:03d}.jsonl"
            prompt = self.build_prompt(
                self._build_reviewer_source(reviewing.task_dir, reviewing.metadata.cycle),
                reviewing.metadata,
                phase="reviewer",
            )
            await self.emit("task_moved", reviewing.metadata.task_id, state=reviewing.state.value)
            loop = asyncio.get_running_loop()
            result = await asyncio.to_thread(
                self.adapter.run,
                agent=self.config.opencode.reviewer_agent,
                prompt=prompt,
                cwd=workspace_path,
                run_log_path=run_log_path,
                config=self.config,
                session_id=reviewing.metadata.review.session_id,
                on_log_line=self.make_log_callback(loop, reviewing.metadata.task_id, run_log_path.name),
            )
            reviewing.metadata.review.resolved_model = result.resolved_model
            reviewing.metadata.review.session_id = result.session_id
            if not result.assistant_text.strip():
                apply_retry_gate(reviewing.metadata, reason="review-empty-artifact")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-empty-artifact", message="reviewer did not return a markdown artifact")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review failed: empty artifact")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            reviewing.metadata.implementation.iteration = reviewing.metadata.cycle
            reviewing.metadata.review.iteration = reviewing.metadata.cycle
            verdict = "PASS" if "Verdict: PASS" in result.assistant_text or "VERDICT: PASS" in result.assistant_text else "NEEDS_CHANGES"
            reviewing.metadata.review.last_verdict = verdict
            review_name = f"REVIEW-{reviewing.metadata.cycle:03d}"
            self.write_result_artifacts(reviewing.task_dir, review_name, result)
            self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
            if verdict != "PASS":
                apply_retry_gate(reviewing.metadata, reason="review-needs-changes")
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review needs changes")
            else:
                clear_retry_gate(reviewing.metadata)
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by=self.worker_name, note="review passed")
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    def _build_reviewer_source(self, task_dir: Path, implementation_iteration: int) -> str:
        sections = ["# Plan", "", (task_dir / "PLAN.md").read_text().rstrip()]

        work_files = sorted(task_dir.glob("WORK-*.md"))
        if work_files:
            sections.extend(["", "# Work History"])
            for work_file in work_files:
                sections.extend(["", f"## {work_file.name}", "", work_file.read_text().rstrip()])

        review_files = sorted(task_dir.glob("REVIEW-*.md"))
        if review_files:
            sections.extend(["", "# Previous AI Reviews"])
            for review_file in review_files:
                sections.extend(["", f"## {review_file.name}", "", review_file.read_text().rstrip()])

        human_verify_files = sorted(task_dir.glob("HUMAN-VERIFY-*.md"))
        if human_verify_files:
            sections.extend(["", "# Human Verification History"])
            for verify_file in human_verify_files:
                sections.extend(["", f"## {verify_file.name}", "", verify_file.read_text().rstrip()])

        current_work = task_dir / f"WORK-{implementation_iteration:03d}.md"
        if current_work.exists():
            sections.extend(["", "# Current Work Artifact", "", current_work.read_text().rstrip()])

        sections.extend(
            [
                "",
                "# Review Instructions",
                "",
                "- Check the full work history, previous AI reviews, and human verification history before deciding.",
                "- Do not repeat earlier findings unless they still apply; explain why they remain unresolved.",
                "- Use `Verdict: NEEDS_CHANGES` only when implementation changes are still required.",
                "- If the work is acceptable with only minor notes, prefer `Verdict: PASS` and list the notes under follow-ups.",
            ]
        )
        return "\n".join(section for section in sections if section is not None)
