from __future__ import annotations

from ..enums import TaskState
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
            workspace_repo = self.workspace_manager.prepare(task.metadata)
            implementing = self.transitions.move(task, TaskState.IMPLEMENTING, by=self.worker_name)
            result = self.adapter.run(
                agent=self.config.opencode.implementer_agent,
                prompt=(implementing.task_dir / "PLAN.md").read_text(),
                cwd=workspace_repo,
                run_log_path=self.task_log_dir(task.metadata.task_id) / f"implementer-{implementing.metadata.implementation.iteration + 1:03d}.jsonl",
                config=self.config,
            )
            implementing.metadata.implementation.iteration += 1
            implementing.metadata.implementation.last_result = "success" if result.ok else "failure"
            work_name = f"WORK-{implementing.metadata.implementation.iteration:03d}.md"
            (implementing.task_dir / work_name).write_text(result.assistant_text.strip() + "\n")
            self.metadata_store.save(implementing.task_dir, implementing.metadata)
            done = self.transitions.move(implementing, TaskState.WAITING_REVIEWS, by=self.worker_name)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True
