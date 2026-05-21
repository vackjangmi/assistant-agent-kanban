from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..assistant_adapter import AssistantAdapter
from ..config import PROJECT_ROOT
from ..enums import TaskState
from ..exceptions import AdapterRunError
from ..language import generation_language_name
from ..models import RunResult, reset_plan_approval_tracking
from ..plan_artifacts import required_plan_heading_lines, validate_plan_markdown
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
    planner_finalize_repair_attempts = 1

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
            live_prompt = self.build_prompt(self._planner_source_text(request_text, planning.metadata), planning.metadata, phase="planner")
            planner_cwd = self.config.repo_root.expanduser().resolve()
            await self.emit("task_moved", planning.metadata.task_id, state=planning.state.value)
            await self.announce_log_file(planning.metadata.task_id, log_name)
            loop = asyncio.get_running_loop()
            session_id = self.reuse_session_id(
                session_id=planning.metadata.plan.session_id,
                session_tokens=planning.metadata.plan.session_tokens,
                budget=self.resolve_task_run_config(planning.task_dir, planning.metadata).role_session_token_budget("planner"),
            )
            prior_session_tokens = planning.metadata.plan.session_tokens if session_id else 0
            run_config = self.resolve_task_run_config(planning.task_dir, planning.metadata)
            adapter = self.resolve_task_adapter(planning.task_dir, planning.metadata)

            if not self._uses_multi_phase_planning(run_config):
                self.append_log_marker(log_path=log_path, phase="run", cycle=revision)
                result = await asyncio.to_thread(
                    adapter.run,
                    agent=run_config.role_agent("planner"),
                    prompt=live_prompt,
                    cwd=planner_cwd,
                    run_log_path=log_path,
                    config=run_config,
                    include_directories=self._planner_include_directories(run_config, planning.metadata, planner_cwd),
                    session_id=session_id,
                    cancel_key=planning.metadata.task_id,
                    on_log_line=self.make_log_callback(loop, planning.metadata.task_id, log_name),
                )
                planning.metadata.plan.resolved_model = result.resolved_model
                planning.metadata.plan.session_id = result.session_id
                planning.metadata.plan.last_run_tokens = result.total_tokens
                planning.metadata.plan.session_tokens = self.next_session_token_total(
                    reused_session_id=session_id,
                    returned_session_id=result.session_id,
                    prior_session_tokens=prior_session_tokens,
                    run_tokens=self.session_budget_tokens(result),
                )
                if not result.ok:
                    apply_retry_gate(planning.metadata, reason="planner-run-failed")
                    self.metadata_store.add_error(
                        planning.task_dir,
                        planning.metadata,
                        code="planner-run-failed",
                        message=result.stderr.strip() or result.assistant_text.strip() or "planner run failed",
                    )
                    self.metadata_store.save(planning.task_dir, planning.metadata)
                    self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner run failed")
                    raise AdapterRunError(result.stderr.strip() or "planner run failed")
                if not result.assistant_text.strip():
                    apply_retry_gate(planning.metadata, reason="planner-empty-artifact")
                    self.metadata_store.add_error(
                        planning.task_dir,
                        planning.metadata,
                        code="planner-empty-artifact",
                        message="planner did not return a markdown artifact",
                    )
                    self.metadata_store.save(planning.task_dir, planning.metadata)
                    self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner did not return a markdown artifact")
                    raise AdapterRunError("planner did not return a markdown artifact")
                finalized_result = await self._finalize_plan_artifact(
                    planning,
                    adapter=adapter,
                    run_config=run_config,
                    request_text=request_text,
                    planner_cwd=planner_cwd,
                    log_path=log_path,
                    revision=revision,
                    active_session_id=result.session_id or session_id,
                )
            else:
                self.append_log_marker(log_path=log_path, phase="handshake", cycle=revision)
                handshake_result = await asyncio.to_thread(
                    adapter.run,
                    agent=run_config.role_agent("planner"),
                    prompt=handshake_prompt,
                    cwd=planner_cwd,
                    run_log_path=log_path,
                    config=run_config,
                    include_directories=self._planner_include_directories(run_config, planning.metadata, planner_cwd),
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
                    run_tokens=self.session_budget_tokens(handshake_result),
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
                    self.metadata_store.save(planning.task_dir, planning.metadata)
                    self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner run failed")
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
                    include_directories=self._planner_include_directories(run_config, planning.metadata, planner_cwd),
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
                    self.metadata_store.save(planning.task_dir, planning.metadata)
                    self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner run failed")
                    raise AdapterRunError(live_result.stderr.strip() or "planner run failed")
                planning.metadata.plan.resolved_model = live_result.resolved_model or planning.metadata.plan.resolved_model
                planning.metadata.plan.session_id = live_result.session_id or planning.metadata.plan.session_id
                planning.metadata.plan.last_run_tokens = live_result.total_tokens
                planning.metadata.plan.session_tokens = self.next_session_token_total(
                    reused_session_id=active_session_id,
                    returned_session_id=live_result.session_id,
                    prior_session_tokens=planning.metadata.plan.session_tokens,
                    run_tokens=self.session_budget_tokens(live_result),
                )
                finalized_result = await self._finalize_plan_artifact(
                    planning,
                    adapter=adapter,
                    run_config=run_config,
                    request_text=request_text,
                    planner_cwd=planner_cwd,
                    log_path=log_path,
                    revision=revision,
                    active_session_id=live_result.session_id or active_session_id,
                )

            clear_retry_gate(planning.metadata)
            planning.metadata.plan.revision += 1
            planning.metadata.plan.restart_message_path = None
            reset_plan_approval_tracking(planning.metadata.plan_approval)
            plan_path, _ = self.write_result_artifacts(planning.task_dir, "PLAN", finalized_result)
            planning.metadata.plan.path = plan_path
            self.metadata_store.save(planning.task_dir, planning.metadata)
            planning.metadata.plan.approved = False
            done = self.transitions.move(planning, TaskState.PLAN_APPROVING, by=self.worker_name)
            await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
            return True

    def _request_ready_for_planning(self, task_dir: Path) -> bool:
        request_path = task_dir / "REQUEST.md"
        if not request_path.exists():
            return False
        return has_required_request_fields(request_path.read_text())

    def _planner_include_directories(self, run_config, metadata, planner_cwd: Path) -> list[Path] | None:
        if run_config.backend_for_role("planner") not in {"gemini", "opencode"}:
            return None
        target_repo_root = Path(metadata.target.repo_root).expanduser().resolve()
        if target_repo_root == planner_cwd:
            return None
        return [target_repo_root]

    async def _finalize_plan_artifact(
        self,
        planning,
        *,
        adapter,
        run_config,
        request_text: str,
        planner_cwd: Path,
        log_path: Path,
        revision: int,
        active_session_id: str | None,
    ) -> RunResult:
        self.append_log_marker(log_path=log_path, phase="finalize", cycle=revision)
        finalize_result = await asyncio.to_thread(
            adapter.run,
            agent=run_config.role_agent("planner"),
            prompt=self._finalize_prompt(request_text, planning.metadata),
            cwd=planner_cwd,
            run_log_path=log_path,
            config=run_config,
            include_directories=self._planner_include_directories(run_config, planning.metadata, planner_cwd),
            session_id=active_session_id,
            cancel_key=planning.metadata.task_id,
        )
        self._record_plan_result(planning, finalize_result, reused_session_id=active_session_id)
        self._raise_on_failed_plan_result(planning, finalize_result)

        validation = self._validated_plan_artifact(finalize_result.assistant_text, planning.metadata)
        repair_attempt = 0
        artifact_result = finalize_result
        while validation.missing_heading is not None and repair_attempt < self.planner_finalize_repair_attempts:
            failure_reason = self._plan_artifact_failure_reason(validation.missing_heading)
            self._write_rejected_plan_artifact(planning.task_dir, artifact_result, rejection_reason=failure_reason)
            repair_attempt += 1
            repair_session_id = planning.metadata.plan.session_id
            repair_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=self._repair_finalize_prompt(
                    request_text,
                    planning.metadata,
                    rejected_artifact=validation.artifact_text,
                    failure_reason=failure_reason,
                    missing_marker=validation.missing_heading,
                ),
                cwd=planner_cwd,
                run_log_path=log_path,
                config=run_config,
                include_directories=self._planner_include_directories(run_config, planning.metadata, planner_cwd),
                session_id=planning.metadata.plan.session_id,
                cancel_key=planning.metadata.task_id,
            )
            self._record_plan_result(planning, repair_result, reused_session_id=repair_session_id)
            self._raise_on_failed_plan_result(planning, repair_result)
            artifact_result = repair_result
            validation = self._validated_plan_artifact(repair_result.assistant_text, planning.metadata)

        if validation.missing_heading is not None:
            failure_reason = self._plan_artifact_failure_reason(validation.missing_heading)
            self._write_rejected_plan_artifact(planning.task_dir, artifact_result, rejection_reason=failure_reason)
            apply_retry_gate(planning.metadata, reason="planner-invalid-artifact")
            self.metadata_store.add_error(
                planning.task_dir,
                planning.metadata,
                code="planner-invalid-artifact",
                message=failure_reason,
            )
            self.metadata_store.save(planning.task_dir, planning.metadata)
            self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner artifact invalid")
            raise AdapterRunError(failure_reason)

        return RunResult(
            ok=artifact_result.ok,
            returncode=artifact_result.returncode,
            assistant_text=validation.artifact_text,
            stdout=artifact_result.stdout,
            stderr=artifact_result.stderr,
            raw_events_path=artifact_result.raw_events_path,
            command=artifact_result.command,
            resolved_model=planning.metadata.plan.resolved_model,
            session_id=planning.metadata.plan.session_id,
            total_tokens=artifact_result.total_tokens,
            session_budget_tokens=artifact_result.session_budget_tokens,
        )

    def _record_plan_result(self, planning, result: RunResult, *, reused_session_id: str | None) -> None:
        planning.metadata.plan.resolved_model = result.resolved_model or planning.metadata.plan.resolved_model
        planning.metadata.plan.session_id = result.session_id or planning.metadata.plan.session_id
        planning.metadata.plan.last_run_tokens = result.total_tokens
        planning.metadata.plan.session_tokens = self.next_session_token_total(
            reused_session_id=reused_session_id,
            returned_session_id=result.session_id,
            prior_session_tokens=planning.metadata.plan.session_tokens,
            run_tokens=self.session_budget_tokens(result),
        )

    def _raise_on_failed_plan_result(self, planning, result: RunResult) -> None:
        if not result.ok:
            apply_retry_gate(planning.metadata, reason="planner-run-failed")
            self.metadata_store.add_error(
                planning.task_dir,
                planning.metadata,
                code="planner-run-failed",
                message=result.stderr.strip() or result.assistant_text.strip() or "planner run failed",
            )
            self.metadata_store.save(planning.task_dir, planning.metadata)
            self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner run failed")
            raise AdapterRunError(result.stderr.strip() or "planner run failed")
        if result.assistant_text.strip():
            return
        apply_retry_gate(planning.metadata, reason="planner-empty-artifact")
        self.metadata_store.add_error(
            planning.task_dir,
            planning.metadata,
            code="planner-empty-artifact",
            message="planner did not return a markdown artifact",
        )
        self.metadata_store.save(planning.task_dir, planning.metadata)
        self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner did not return a markdown artifact")
        raise AdapterRunError("planner did not return a markdown artifact")

    def _validated_plan_artifact(self, assistant_text: str, metadata):
        return validate_plan_markdown(assistant_text, request_language=metadata.request.language)

    def _plan_artifact_failure_reason(self, missing_marker: str | None) -> str:
        if missing_marker is None:
            return "planner artifact missing required sections"
        return f"planner artifact missing required section: {missing_marker}"

    def _write_rejected_plan_artifact(self, task_dir: Path, result: RunResult, *, rejection_reason: str) -> tuple[str, str]:
        stem = self._next_rejected_plan_artifact_stem(task_dir)
        markdown_path = task_dir / f"{stem}.md"
        json_path = task_dir / f"{stem}.json"
        rejected_text = result.assistant_text.strip()
        markdown_path.write_text(f"{rejected_text}\n" if rejected_text else "")
        json_path.write_text(
            json.dumps(
                {
                    "ok": result.ok,
                    "returncode": result.returncode,
                    "assistant_text": result.assistant_text,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "raw_events_path": result.raw_events_path,
                    "command": result.command,
                    "resolved_model": result.resolved_model,
                    "session_id": result.session_id,
                    "total_tokens": result.total_tokens,
                    "markdown_path": markdown_path.name,
                    "editable_markdown": False,
                    "sync_policy": "non_authoritative_debug_artifact",
                    "rejection_reason": rejection_reason,
                },
                indent=2,
            )
            + "\n"
        )
        return markdown_path.name, json_path.name

    def _next_rejected_plan_artifact_stem(self, task_dir: Path) -> str:
        index = 1
        while (task_dir / f"PLAN-REJECTED-{index:03d}.md").exists() or (task_dir / f"PLAN-REJECTED-{index:03d}.json").exists():
            index += 1
        return f"PLAN-REJECTED-{index:03d}"

    def _planner_source_text(self, request_text: str, metadata) -> str:
        sections = [request_text.rstrip()]
        restart_message_path = metadata.plan.restart_message_path
        if restart_message_path:
            restart_path = self.scanner.find_task(metadata.task_id).task_dir / restart_message_path
            if restart_path.exists():
                sections.extend(["## Planner Restart Note", restart_path.read_text().rstrip()])
        context_blocks: list[str] = []
        for relative_path in self.planner_context_docs:
            doc_path = PROJECT_ROOT / relative_path
            context_blocks.extend([f"## {relative_path}", doc_path.read_text().rstrip()])
        sections.extend(["## Planner Context Docs", *context_blocks])
        return "\n\n".join(sections)

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
        source = self._planner_source_text(request_text, metadata)
        headings = self._required_heading_lines(metadata)
        instructions = "\n".join(
            [
                source,
                "",
                "# Finalize Plan Artifact",
                "- Summarize the plan that should result from the existing session context.",
                "- Do not use thinking or logs as the final artifact.",
                "- Return only markdown. Do not add prefaces, explanations, code fences, JSON, or tool/log output.",
                "- Use the exact required headings below, in this exact order.",
                "- Every heading must start with `## ` exactly.",
                "- Required headings only count when they appear as their own line outside fenced code blocks.",
                *headings,
                "- Return only the final markdown artifact with the required sections.",
            ]
        )
        return self.build_prompt(instructions, metadata, phase="planner")

    def _repair_finalize_prompt(
        self,
        request_text: str,
        metadata,
        *,
        rejected_artifact: str,
        failure_reason: str,
        missing_marker: str | None,
    ) -> str:
        source = self._planner_source_text(request_text, metadata)
        headings = self._required_heading_lines(metadata)
        missing_heading_instruction = f"- The missing heading was `{missing_marker}`. Include it as its own line." if missing_marker else "- Ensure every required heading is present as its own line."
        rejected_artifact_text = rejected_artifact or "(empty artifact)"
        instructions = "\n".join(
            [
                source,
                "",
                "# Repair Plan Artifact",
                "- The previous final artifact was rejected.",
                f"- Failure reason: {failure_reason}",
                "- Rewrite the final PLAN.md artifact from scratch.",
                "- Return only markdown. Do not add prefaces, explanations, code fences, JSON, or tool/log output.",
                "- Use the exact required headings below, in this exact order.",
                "- Every heading must start with `## ` exactly.",
                "- Required headings only count when they appear as their own line outside fenced code blocks.",
                missing_heading_instruction,
                "",
                "## Rejected Artifact",
                "```markdown",
                rejected_artifact_text,
                "```",
                *headings,
                "- Return only the corrected final markdown artifact.",
            ]
        )
        return self.build_prompt(instructions, metadata, phase="planner")

    def _required_heading_lines(self, metadata) -> list[str]:
        return required_plan_heading_lines(metadata.request.language)

    def _uses_multi_phase_planning(self, run_config) -> bool:
        return run_config.backend_for_role("planner") == "gemini" or self.worker_live_logs_enabled(run_config)
