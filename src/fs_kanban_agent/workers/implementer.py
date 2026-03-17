from __future__ import annotations

import asyncio
from pathlib import Path

from ..enums import TaskState
from ..exceptions import WorkspaceSyncError
from ..models import RunResult, TaskErrorInfo
from ..opencode_adapter import OpenCodeAdapter
from ..retry_policy import apply_retry_gate, can_auto_dispatch, clear_retry_gate
from ..workspace_manager import WorkspaceManager
from .base import WorkerBase


class ImplementerWorker(WorkerBase):
    worker_name = "implementer"

    def __init__(self, *args, adapter: OpenCodeAdapter, workspace_manager: WorkspaceManager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.workspace_manager = workspace_manager

    def candidate_tasks(self):
        return [
            task
            for task in self.scanner.scan()
            if task.state == TaskState.TODOS and can_auto_dispatch(task.metadata)
        ]

    async def run_once(self) -> bool:
        tasks = self.candidate_tasks()
        if not tasks:
            return False
        return await self.run_task(tasks[0])

    async def run_task(self, task) -> bool:
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            try:
                workspace_repo = await self._prepare_workspace(task)
            except WorkspaceSyncError as exc:
                apply_retry_gate(task.metadata, reason="implementation-base-sync-conflict")
                task.metadata.implementation.last_result = "failure"
                task.metadata.errors.append(
                    TaskErrorInfo(code="implementation-base-sync-conflict", message=str(exc))
                )
                self.metadata_store.save(task.task_dir, task.metadata)
                return True
            implementing = self.transitions.move(task, TaskState.IMPLEMENTING, by=self.worker_name)
            run_log_path = self.task_log_dir(task.metadata.task_id) / f"implementer-{implementing.metadata.cycle + 1:03d}.jsonl"
            prompt = self.build_prompt(self._build_implementer_source(implementing.task_dir), implementing.metadata, phase="implementer")
            await self.emit("task_moved", implementing.metadata.task_id, state=implementing.state.value)
            loop = asyncio.get_running_loop()
            session_id = self.reuse_session_id(
                session_id=implementing.metadata.implementation.session_id,
                session_tokens=implementing.metadata.implementation.session_tokens,
                budget=self.config.opencode.implementer_session_token_budget,
            )
            prior_session_tokens = implementing.metadata.implementation.session_tokens if session_id else 0
            run_config = self.config.model_copy(deep=True)
            result = await self._run_adapter_with_retry(
                implementing=implementing,
                prompt=prompt,
                workspace_repo=workspace_repo,
                run_log_path=run_log_path,
                run_config=run_config,
                session_id=session_id,
                loop=loop,
            )
            implementing.metadata.implementation.resolved_model = result.resolved_model
            implementing.metadata.implementation.session_id = result.session_id
            implementing.metadata.implementation.last_run_tokens = result.total_tokens
            implementing.metadata.implementation.session_tokens = self.next_session_token_total(
                reused_session_id=session_id,
                returned_session_id=result.session_id,
                prior_session_tokens=prior_session_tokens,
                run_tokens=result.total_tokens,
            )
            implementing.metadata.cycle += 1
            has_changes = self.workspace_has_changes(workspace_repo)
            has_local_commits = self.workspace_has_local_commits(workspace_repo, implementing.metadata.target.base_branch)
            success = result.ok and has_changes and not has_local_commits
            implementing.metadata.implementation.iteration = implementing.metadata.cycle
            implementing.metadata.implementation.last_result = "success" if success else "failure"
            work_name = f"WORK-{implementing.metadata.cycle:03d}"
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
                clear_retry_gate(implementing.metadata)
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.WAITING_REVIEWS, by=self.worker_name)
            else:
                if not has_changes:
                    apply_retry_gate(implementing.metadata, reason="implementation-no-changes")
                    note = "implementation produced no workspace changes"
                elif has_local_commits:
                    apply_retry_gate(implementing.metadata, reason="implementation-local-commits")
                    note = "implementation created local commits"
                else:
                    apply_retry_gate(implementing.metadata, reason="implementation-failed")
                    note = "implementation failed"
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=note)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    async def _prepare_workspace(self, task):
        try:
            return await asyncio.to_thread(self.workspace_manager.prepare, task.metadata)
        except WorkspaceSyncError as exc:
            existing_workspace = Path(task.metadata.implementation.workspace or "")
            if not existing_workspace.exists():
                raise
            await asyncio.to_thread(self.workspace_manager.discard, task.metadata)
            self._reset_implementation_context(task.metadata)
            task.metadata.errors.append(
                TaskErrorInfo(code="implementation-base-sync-conflict", message=str(exc))
            )
            self.metadata_store.save(task.task_dir, task.metadata)
            return await asyncio.to_thread(self.workspace_manager.prepare, task.metadata)

    def _reset_implementation_context(self, metadata) -> None:
        metadata.implementation.last_result = None
        metadata.implementation.resolved_model = None
        metadata.implementation.session_id = None
        metadata.implementation.last_run_tokens = 0
        metadata.implementation.session_tokens = 0

    async def _run_adapter_with_retry(
        self,
        *,
        implementing,
        prompt: str,
        workspace_repo: Path,
        run_log_path: Path,
        run_config,
        session_id: str | None,
        loop,
    ) -> RunResult:
        result = await asyncio.to_thread(
            self.adapter.run,
            agent=run_config.opencode.implementer_agent,
            prompt=prompt,
            cwd=workspace_repo,
            run_log_path=run_log_path,
            config=run_config,
            session_id=session_id,
            cancel_key=implementing.metadata.task_id,
            on_log_line=self.make_log_callback(loop, implementing.metadata.task_id, run_log_path.name),
        )
        if not self._is_interrupted_run(result):
            return result
        return await asyncio.to_thread(
            self.adapter.run,
            agent=run_config.opencode.implementer_agent,
            prompt=prompt,
            cwd=workspace_repo,
            run_log_path=run_log_path,
            config=run_config,
            session_id=session_id,
            cancel_key=implementing.metadata.task_id,
            on_log_line=self.make_log_callback(loop, implementing.metadata.task_id, run_log_path.name),
        )

    def _is_interrupted_run(self, result) -> bool:
        return (
            not result.ok
            and result.returncode < 0
            and not result.stdout.strip()
            and not result.stderr.strip()
            and not result.assistant_text.strip()
        )

    def _build_implementer_source(self, task_dir):
        sections = ["# Plan", "", (task_dir / "PLAN.md").read_text().rstrip()]
        latest_review = sorted(task_dir.glob("REVIEW-*.md"))
        if latest_review:
            sections.extend(["", "# Latest AI Review", "", latest_review[-1].read_text().rstrip()])
        latest_human_verify = sorted(task_dir.glob("HUMAN-VERIFY-*.md"))
        if latest_human_verify:
            sections.extend(["", "# Latest Human Verification", "", latest_human_verify[-1].read_text().rstrip()])
        return "\n".join(section for section in sections if section is not None)
