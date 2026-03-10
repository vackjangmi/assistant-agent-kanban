from __future__ import annotations

import asyncio
from pathlib import Path

from ..enums import TaskState
from ..integration_manager import IntegrationManager
from ..models import TaskErrorInfo
from ..opencode_adapter import OpenCodeAdapter
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
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-no-workspace", message="review skipped because implementation workspace is missing")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: missing workspace")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            workspace_path = Path(workspace_repo)
            if self.workspace_has_local_commits(workspace_path, task.metadata.target.base_branch):
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-local-commits", message="review skipped because workspace contains local commits")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: workspace has local commits")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            if not self.workspace_has_changes(workspace_path):
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-no-changes", message="review skipped because workspace has no file changes")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: no workspace changes")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            run_log_path = self.task_log_dir(task.metadata.task_id) / f"reviewer-{reviewing.metadata.review.iteration + 1:03d}.jsonl"
            prompt = self.build_prompt(
                (reviewing.task_dir / f"WORK-{reviewing.metadata.implementation.iteration:03d}.md").read_text(),
                reviewing.metadata,
                phase="reviewer",
            )
            await self.emit("task_moved", reviewing.metadata.task_id, state=reviewing.state.value)
            loop = asyncio.get_running_loop()
            result = await asyncio.to_thread(
                self.adapter.run,
                agent=self.config.opencode.reviewer_agent,
                prompt=prompt,
                cwd=Path(reviewing.metadata.target.repo_root),
                run_log_path=run_log_path,
                config=self.config,
                on_log_line=self.make_log_callback(loop, reviewing.metadata.task_id, run_log_path.name),
            )
            reviewing.metadata.review.iteration += 1
            verdict = "PASS" if "Verdict: PASS" in result.assistant_text or "VERDICT: PASS" in result.assistant_text else "NEEDS_CHANGES"
            reviewing.metadata.review.last_verdict = verdict
            review_name = f"REVIEW-{reviewing.metadata.review.iteration:03d}"
            self.write_result_artifacts(reviewing.task_dir, review_name, result)
            self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
            if verdict != "PASS":
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review needs changes")
            else:
                done = self.transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by=self.worker_name, note="review passed")
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True
