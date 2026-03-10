from __future__ import annotations

import asyncio

from ..enums import TaskState
from ..models import TaskErrorInfo
from ..opencode_adapter import OpenCodeAdapter
from ..workspace_manager import WorkspaceManager
from .base import WorkerBase


class ImplementerWorker(WorkerBase):
    worker_name = "implementer"

    def __init__(self, *args, adapter: OpenCodeAdapter, workspace_manager: WorkspaceManager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.workspace_manager = workspace_manager

    async def run_once(self) -> bool:
        tasks = [task for task in self.scanner.scan() if task.state == TaskState.TODOS]
        if not tasks:
            return False
        task = tasks[0]
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            workspace_repo = await asyncio.to_thread(self.workspace_manager.prepare, task.metadata)
            implementing = self.transitions.move(task, TaskState.IMPLEMENTING, by=self.worker_name)
            run_log_path = self.task_log_dir(task.metadata.task_id) / f"implementer-{implementing.metadata.implementation.iteration + 1:03d}.jsonl"
            prompt = self.build_prompt(self._build_implementer_source(implementing.task_dir), implementing.metadata, phase="implementer")
            await self.emit("task_moved", implementing.metadata.task_id, state=implementing.state.value)
            loop = asyncio.get_running_loop()
            result = await asyncio.to_thread(
                self.adapter.run,
                agent=self.config.opencode.implementer_agent,
                prompt=prompt,
                cwd=workspace_repo,
                run_log_path=run_log_path,
                config=self.config,
                on_log_line=self.make_log_callback(loop, implementing.metadata.task_id, run_log_path.name),
            )
            implementing.metadata.implementation.iteration += 1
            has_changes = self.workspace_has_changes(workspace_repo)
            has_local_commits = self.workspace_has_local_commits(workspace_repo, implementing.metadata.target.base_branch)
            success = result.ok and has_changes and not has_local_commits
            implementing.metadata.implementation.last_result = "success" if success else "failure"
            work_name = f"WORK-{implementing.metadata.implementation.iteration:03d}"
            self.write_result_artifacts(implementing.task_dir, work_name, result)
            if not has_changes:
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-no-changes", message="implementer produced no workspace changes")
                )
            if has_local_commits:
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-local-commits", message="implementer must not create local git commits")
                )
            if not result.ok:
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-failed", message=result.stderr.strip() or "implementer run failed")
                )
            self.metadata_store.save(implementing.task_dir, implementing.metadata)
            if success:
                done = self.transitions.move(implementing, TaskState.WAITING_REVIEWS, by=self.worker_name)
            else:
                if not has_changes:
                    note = "implementation produced no workspace changes"
                elif has_local_commits:
                    note = "implementation created local commits"
                else:
                    note = "implementation failed"
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=note)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    def _build_implementer_source(self, task_dir):
        sections = ["# Plan", "", (task_dir / "PLAN.md").read_text().rstrip()]
        latest_review = sorted(task_dir.glob("REVIEW-*.md"))
        if latest_review:
            sections.extend(["", "# Latest AI Review", "", latest_review[-1].read_text().rstrip()])
        latest_human_verify = sorted(task_dir.glob("HUMAN-VERIFY-*.md"))
        if latest_human_verify:
            sections.extend(["", "# Latest Human Verification", "", latest_human_verify[-1].read_text().rstrip()])
        return "\n".join(section for section in sections if section is not None)
