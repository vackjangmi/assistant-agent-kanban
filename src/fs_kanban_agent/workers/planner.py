from __future__ import annotations

import asyncio
from pathlib import Path

from ..assistant_adapter import AssistantAdapter
from ..config import PROJECT_ROOT
from ..enums import TaskState
from ..exceptions import AdapterRunError
from ..language import generation_language_name
from ..models import RunResult
from ..request_parser import has_required_request_fields
from ..retry_policy import apply_retry_gate, can_auto_dispatch, clear_retry_gate
from .base import WorkerBase


class PlanningWorker(WorkerBase):
    worker_name = "planner"
    planner_context_docs = (
        "docs/01-architecture-review.md",
        "docs/02-implementation-plan.md",
        "docs/03-agent-task.md",
    )

    def __init__(self, *args, adapter: AssistantAdapter, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter

    def candidate_tasks(self):
        return [
            task
            for task in self.scanner.scan()
            if task.state == TaskState.REQUESTS
            and can_auto_dispatch(task.metadata)
            and self._request_ready_for_planning(task.task_dir)
        ]

    async def run_once(self) -> bool:
        tasks = self.candidate_tasks()
        if not tasks:
            return False
        return await self.run_task(tasks[0])

    async def run_task(self, task) -> bool:
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            planning = self.transitions.move(task, TaskState.PLANNING, by=self.worker_name)
            revision = planning.metadata.plan.revision + 1
            log_path = self.task_log_dir(task.metadata.task_id) / "planner.jsonl"
            log_name = log_path.name
            request_text = (planning.task_dir / "REQUEST.md").read_text()
            handshake_prompt = self._handshake_prompt(planning.metadata)
            live_prompt = self.build_prompt(self._planner_source_text(request_text), planning.metadata, phase="planner")
            finalize_prompt = self._finalize_prompt(request_text, planning.metadata)
            planner_cwd = self.config.repo_root.expanduser().resolve()
            await self.emit("task_moved", planning.metadata.task_id, state=planning.state.value)
            loop = asyncio.get_running_loop()
            session_id = self.reuse_session_id(
                session_id=planning.metadata.plan.session_id,
                session_tokens=planning.metadata.plan.session_tokens,
                budget=self.resolve_task_run_config(planning.task_dir, planning.metadata).role_session_token_budget("planner"),
            )
            prior_session_tokens = planning.metadata.plan.session_tokens if session_id else 0
            run_config = self.resolve_task_run_config(planning.task_dir, planning.metadata)
            adapter = self.resolve_task_adapter(planning.task_dir, planning.metadata)

            self.append_log_marker(log_path=log_path, phase="handshake", cycle=revision)
            handshake_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=handshake_prompt,
                cwd=planner_cwd,
                run_log_path=log_path,
                config=run_config,
                session_id=session_id,
                cancel_key=planning.metadata.task_id,
            )
            planning.metadata.plan.resolved_model = handshake_result.resolved_model
            planning.metadata.plan.session_id = handshake_result.session_id
            planning.metadata.plan.last_run_tokens = handshake_result.total_tokens
            planning.metadata.plan.session_tokens = self.next_session_token_total(
                reused_session_id=session_id,
                returned_session_id=handshake_result.session_id,
                prior_session_tokens=prior_session_tokens,
                run_tokens=handshake_result.total_tokens,
            )
            self.metadata_store.save(planning.task_dir, planning.metadata)
            if not handshake_result.ok:
                apply_retry_gate(planning.metadata, reason="planner-run-failed")
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-run-failed",
                    message=handshake_result.stderr.strip() or handshake_result.assistant_text.strip() or "planner run failed",
                )
                raise AdapterRunError(handshake_result.stderr.strip() or "planner run failed")

            active_session_id = handshake_result.session_id or session_id
            self.append_log_marker(log_path=log_path, phase="live", cycle=revision)
            live_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=live_prompt,
                cwd=planner_cwd,
                run_log_path=log_path,
                config=run_config,
                session_id=active_session_id,
                cancel_key=planning.metadata.task_id,
                on_log_line=self.make_log_callback(loop, planning.metadata.task_id, log_name),
                output_format="default",
                stream_stderr_to_log=True,
                show_thinking=True,
            )
            if not live_result.ok:
                apply_retry_gate(planning.metadata, reason="planner-run-failed")
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-run-failed",
                    message=live_result.stderr.strip() or live_result.assistant_text.strip() or "planner run failed",
                )
                raise AdapterRunError(live_result.stderr.strip() or "planner run failed")

            self.append_log_marker(log_path=log_path, phase="finalize", cycle=revision)
            finalize_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=finalize_prompt,
                cwd=planner_cwd,
                run_log_path=log_path,
                config=run_config,
                session_id=live_result.session_id or active_session_id,
                cancel_key=planning.metadata.task_id,
            )
            planning.metadata.plan.resolved_model = finalize_result.resolved_model or live_result.resolved_model or handshake_result.resolved_model
            planning.metadata.plan.session_id = finalize_result.session_id or live_result.session_id or active_session_id
            planning.metadata.plan.last_run_tokens = finalize_result.total_tokens
            planning.metadata.plan.session_tokens = self.next_session_token_total(
                reused_session_id=active_session_id,
                returned_session_id=finalize_result.session_id,
                prior_session_tokens=planning.metadata.plan.session_tokens,
                run_tokens=finalize_result.total_tokens,
            )
            if not finalize_result.ok or not finalize_result.assistant_text.strip():
                apply_retry_gate(planning.metadata, reason="planner-empty-artifact")
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-empty-artifact",
                    message=finalize_result.stderr.strip() or "planner did not return a markdown artifact",
                )
                raise AdapterRunError("planner did not return a markdown artifact")

            clear_retry_gate(planning.metadata)
            planning.metadata.plan.revision += 1
            finalized_result = RunResult(
                ok=finalize_result.ok,
                returncode=finalize_result.returncode,
                assistant_text=finalize_result.assistant_text,
                stdout=finalize_result.stdout,
                stderr=finalize_result.stderr,
                raw_events_path=finalize_result.raw_events_path,
                command=finalize_result.command,
                resolved_model=planning.metadata.plan.resolved_model,
                session_id=planning.metadata.plan.session_id,
                total_tokens=finalize_result.total_tokens,
            )
            plan_path, _ = self.write_result_artifacts(planning.task_dir, "PLAN", finalized_result)
            planning.metadata.plan.path = plan_path
            self.metadata_store.save(planning.task_dir, planning.metadata)
            done = self.transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by=self.worker_name)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    def _request_ready_for_planning(self, task_dir: Path) -> bool:
        request_path = task_dir / "REQUEST.md"
        if not request_path.exists():
            return False
        return has_required_request_fields(request_path.read_text())

    def _planner_source_text(self, request_text: str) -> str:
        context_blocks: list[str] = []
        for relative_path in self.planner_context_docs:
            doc_path = PROJECT_ROOT / relative_path
            context_blocks.extend([f"## {relative_path}", doc_path.read_text().rstrip()])
        return "\n\n".join([request_text.rstrip(), "## Planner Context Docs", *context_blocks])

    def _handshake_prompt(self, metadata) -> str:
        requested_language = generation_language_name(metadata.request.language)
        return "\n".join(
            [
                "You are preparing a reusable fs-kanban planner session.",
                f"Reply with one short greeting in {requested_language}.",
                "Do not analyze the request yet.",
                "Do not produce a plan yet.",
            ]
        )

    def _finalize_prompt(self, request_text: str, metadata) -> str:
        source = self._planner_source_text(request_text)
        instructions = "\n".join(
            [
                source,
                "",
                "# Finalize Plan Artifact",
                "- Summarize the plan that should result from the existing session context.",
                "- Do not use thinking or logs as the final artifact.",
                "- Return only the final markdown artifact with the required sections.",
            ]
        )
        return self.build_prompt(instructions, metadata, phase="planner")
