from __future__ import annotations

from ..enums import TaskState
from ..opencode_adapter import OpenCodeAdapter
from .base import WorkerBase


class PlanningWorker(WorkerBase):
    worker_name = "planner"

    def __init__(self, *args, adapter: OpenCodeAdapter, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter

    async def run_once(self) -> bool:
        tasks = [task for task in self.scanner.scan() if task.state == TaskState.REQUESTS]
        if not tasks:
            return False
        task = tasks[0]
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            planning = self.transitions.move(task, TaskState.PLANNING, by=self.worker_name)
            prompt = (planning.task_dir / "REQUEST.md").read_text()
            result = self.adapter.run(
                agent=self.config.opencode.planner_agent,
                prompt=prompt,
                cwd=planning.task_dir,
                run_log_path=self.task_log_dir(task.metadata.task_id) / f"planner-{planning.metadata.plan.revision + 1:03d}.jsonl",
                config=self.config,
            )
            planning.metadata.plan.revision += 1
            planning.metadata.plan.path = "PLAN.md"
            (planning.task_dir / "PLAN.md").write_text(result.assistant_text.strip() + "\n")
            self.metadata_store.save(planning.task_dir, planning.metadata)
            done = self.transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by=self.worker_name)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True
