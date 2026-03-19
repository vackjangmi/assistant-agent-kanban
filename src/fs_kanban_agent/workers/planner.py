from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import PROJECT_ROOT
from ..enums import TaskState
from ..exceptions import AdapterRunError
from ..assistant_adapter import AssistantAdapter
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
            bootstrap_log_path = self.task_log_dir(task.metadata.task_id) / f"planner-{revision:03d}-bootstrap.jsonl"
            live_log_path = self.task_log_dir(task.metadata.task_id) / f"planner-{revision:03d}.jsonl"
            draft_plan_path = self.task_log_dir(task.metadata.task_id) / f"planner-{revision:03d}-draft.md"
            request_text = (planning.task_dir / "REQUEST.md").read_text()
            bootstrap_prompt = self._bootstrap_prompt(planning.metadata)
            planner_cwd = self.config.repo_root.expanduser().resolve()
            live_prompt = self._live_prompt(request_text, planning.metadata, draft_plan_path)
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
            result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=bootstrap_prompt,
                cwd=planner_cwd,
                run_log_path=bootstrap_log_path,
                config=run_config,
                session_id=session_id,
                cancel_key=planning.metadata.task_id,
            )
            planning.metadata.plan.resolved_model = result.resolved_model
            planning.metadata.plan.session_id = result.session_id
            planning.metadata.plan.last_run_tokens = result.total_tokens
            planning.metadata.plan.session_tokens = self.next_session_token_total(
                reused_session_id=session_id,
                returned_session_id=result.session_id,
                prior_session_tokens=prior_session_tokens,
                run_tokens=result.total_tokens,
            )
            self.metadata_store.save(planning.task_dir, planning.metadata)
            if not result.ok:
                apply_retry_gate(planning.metadata, reason="planner-run-failed")
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-run-failed",
                    message=result.stderr.strip() or result.assistant_text.strip() or "planner run failed",
                )
                raise AdapterRunError(result.stderr.strip() or "planner run failed")
            draft_plan_path.unlink(missing_ok=True)
            live_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=live_prompt,
                cwd=planner_cwd,
                run_log_path=live_log_path,
                config=run_config,
                session_id=result.session_id or session_id,
                cancel_key=planning.metadata.task_id,
                on_log_line=self.make_log_callback(loop, planning.metadata.task_id, live_log_path.name),
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
            draft_markdown = self._read_verified_plan_draft(draft_plan_path)
            if not draft_markdown:
                apply_retry_gate(planning.metadata, reason="planner-empty-artifact")
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-empty-artifact",
                    message="planner did not write a markdown artifact",
                )
                raise AdapterRunError("planner did not write a markdown artifact")
            clear_retry_gate(planning.metadata)
            planning.metadata.plan.revision += 1
            finalized_result = RunResult(
                ok=live_result.ok,
                returncode=live_result.returncode,
                assistant_text=draft_markdown,
                stdout=live_result.stdout,
                stderr=live_result.stderr,
                raw_events_path=live_result.raw_events_path,
                command=live_result.command,
                resolved_model=result.resolved_model,
                session_id=result.session_id,
                total_tokens=result.total_tokens,
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
            context_blocks.extend(
                [
                    f"## {relative_path}",
                    doc_path.read_text().rstrip(),
                ]
            )
        return "\n\n".join([request_text.rstrip(), "## Planner Context Docs", *context_blocks])

    def _live_prompt(self, request_text: str, metadata, draft_plan_path: Path) -> str:
        base_prompt = self.build_prompt(self._planner_source_text(request_text), metadata, phase="planner")
        artifact_instructions = "\n".join(
            [
                "",
                "<plan-artifact-path>",
                str(draft_plan_path.expanduser().resolve()),
                "</plan-artifact-path>",
                "The artifact path is an absolute path.",
                "Write the final completed plan markdown only to that exact file path.",
                "Do not treat chat output as the final artifact.",
                "Overwrite the artifact file with the completed plan.",
            ]
        )
        return f"{base_prompt}{artifact_instructions}"

    def _bootstrap_prompt(self, metadata) -> str:
        requested_language = generation_language_name(metadata.request.language)
        return "\n".join(
            [
                "You are preparing a reusable fs-kanban planner session.",
                f"Reply with one short greeting in {requested_language}.",
                "Do not analyze the request yet.",
                "Do not produce a plan.",
                "Do not call tools unless greeting is impossible without them.",
            ]
        )

    def _read_verified_plan_draft(self, draft_plan_path: Path) -> str:
        if not draft_plan_path.exists() or not draft_plan_path.is_file():
            return ""
        content = draft_plan_path.read_text().strip()
        if not content:
            return ""
        if any(marker in content for marker in ("----- Debug / Thinking -----", "<plan-artifact-path>", "Do not analyze the request yet.")):
            return ""
        lowered_lines = [line.strip().lower() for line in content.splitlines() if line.strip()]
        if any(line.startswith("thinking:") for line in lowered_lines):
            return ""
        return content
