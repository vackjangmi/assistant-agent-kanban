from __future__ import annotations

import asyncio

from ..enums import TaskState
from ..exceptions import AdapterRunError
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
            run_log_path = self.task_log_dir(task.metadata.task_id) / f"planner-{planning.metadata.plan.revision + 1:03d}.jsonl"
            prompt = self.build_prompt((planning.task_dir / "REQUEST.md").read_text(), planning.metadata, phase="planner")
            await self.emit("task_moved", planning.metadata.task_id, state=planning.state.value)
            loop = asyncio.get_running_loop()
            result = await asyncio.to_thread(
                self.adapter.run,
                agent=self.config.opencode.planner_agent,
                prompt=prompt,
                cwd=planning.task_dir,
                run_log_path=run_log_path,
                config=self.config,
                on_log_line=self.make_log_callback(loop, planning.metadata.task_id, run_log_path.name),
            )
            planning.metadata.plan.resolved_model = result.resolved_model
            if not result.ok:
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-run-failed",
                    message=result.stderr.strip() or result.assistant_text.strip() or "planner run failed",
                )
                raise AdapterRunError(result.stderr.strip() or "planner run failed")
            planning.metadata.plan.revision += 1
            plan_path, _ = self.write_result_artifacts(planning.task_dir, "PLAN", result)
            planning.metadata.plan.path = plan_path
            self.metadata_store.save(planning.task_dir, planning.metadata)
            done = self.transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by=self.worker_name)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True
