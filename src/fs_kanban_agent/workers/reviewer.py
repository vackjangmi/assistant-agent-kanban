from __future__ import annotations

from pathlib import Path

from ..enums import TaskState
from ..exceptions import IntegrationError
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
            result = self.adapter.run(
                agent=self.config.opencode.reviewer_agent,
                prompt=(reviewing.task_dir / f"WORK-{reviewing.metadata.implementation.iteration:03d}.md").read_text(),
                cwd=Path(reviewing.metadata.target.repo_root),
                run_log_path=self.task_log_dir(task.metadata.task_id) / f"reviewer-{reviewing.metadata.review.iteration + 1:03d}.jsonl",
                config=self.config,
            )
            reviewing.metadata.review.iteration += 1
            verdict = "PASS" if "Verdict: PASS" in result.assistant_text or "VERDICT: PASS" in result.assistant_text else "NEEDS_CHANGES"
            reviewing.metadata.review.last_verdict = verdict
            review_name = f"REVIEW-{reviewing.metadata.review.iteration:03d}.md"
            (reviewing.task_dir / review_name).write_text(result.assistant_text.strip() + "\n")
            self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
            if verdict != "PASS":
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review needs changes")
            else:
                try:
                    if workspace_repo is None:
                        raise IntegrationError("workspace path missing")
                    self.integration_manager.apply_workspace(reviewing.metadata, workspace_repo=Path(workspace_repo))
                    self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                    done = self.transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by=self.worker_name, note="review passed")
                except IntegrationError as exc:
                    reviewing.metadata.errors.append(TaskErrorInfo(code="integration-conflict", message=str(exc)))
                    self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                    done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="integration failed")
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True
