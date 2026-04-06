from __future__ import annotations

import asyncio
from pathlib import Path

from ..assistant_adapter import AssistantAdapter
from ..config import PROJECT_ROOT
from ..enums import TaskState
from ..exceptions import AdapterRunError
from ..language import generation_language_code, generation_language_name
from ..models import RunResult, reset_plan_approval_tracking
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
            finalize_prompt = self._finalize_prompt(request_text, planning.metadata)
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
                    include_directories=self._gemini_include_directories(run_config, planning.metadata, planner_cwd),
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
                    run_tokens=result.total_tokens,
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
                plan_artifact, missing_marker = self._validated_plan_artifact(result.assistant_text, planning.metadata)
                if plan_artifact is None:
                    apply_retry_gate(planning.metadata, reason="planner-invalid-artifact")
                    self.metadata_store.add_error(
                        planning.task_dir,
                        planning.metadata,
                        code="planner-invalid-artifact",
                        message=f"planner artifact missing required section: {missing_marker}",
                    )
                    self.metadata_store.save(planning.task_dir, planning.metadata)
                    self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner artifact invalid")
                    raise AdapterRunError(f"planner artifact missing required section: {missing_marker}")
                clear_retry_gate(planning.metadata)
                planning.metadata.plan.revision += 1
                planning.metadata.plan.restart_message_path = None
                reset_plan_approval_tracking(planning.metadata.plan_approval)
                plan_path, _ = self.write_result_artifacts(
                    planning.task_dir,
                    "PLAN",
                    result.model_copy(update={"assistant_text": plan_artifact}),
                )
                planning.metadata.plan.path = plan_path
                self.metadata_store.save(planning.task_dir, planning.metadata)
                planning.metadata.plan.approved = False
                done = self.transitions.move(planning, TaskState.PLAN_APPROVING, by=self.worker_name)
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            self.append_log_marker(log_path=log_path, phase="handshake", cycle=revision)
            handshake_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=handshake_prompt,
                cwd=planner_cwd,
                run_log_path=log_path,
                config=run_config,
                include_directories=self._gemini_include_directories(run_config, planning.metadata, planner_cwd),
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
                include_directories=self._gemini_include_directories(run_config, planning.metadata, planner_cwd),
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
                run_tokens=live_result.total_tokens,
            )

            self.append_log_marker(log_path=log_path, phase="finalize", cycle=revision)
            finalize_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("planner"),
                prompt=finalize_prompt,
                cwd=planner_cwd,
                run_log_path=log_path,
                config=run_config,
                include_directories=self._gemini_include_directories(run_config, planning.metadata, planner_cwd),
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
            if not finalize_result.ok:
                apply_retry_gate(planning.metadata, reason="planner-run-failed")
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-run-failed",
                    message=finalize_result.stderr.strip() or finalize_result.assistant_text.strip() or "planner run failed",
                )
                self.metadata_store.save(planning.task_dir, planning.metadata)
                self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner run failed")
                raise AdapterRunError(finalize_result.stderr.strip() or "planner run failed")
            if not finalize_result.assistant_text.strip():
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

            plan_artifact, missing_marker = self._validated_plan_artifact(finalize_result.assistant_text, planning.metadata)
            repair_attempt = 0
            artifact_result = finalize_result
            while plan_artifact is None and repair_attempt < self.planner_finalize_repair_attempts:
                repair_attempt += 1
                repair_session_id = planning.metadata.plan.session_id
                prior_repair_session_tokens = planning.metadata.plan.session_tokens
                repair_result = await asyncio.to_thread(
                    adapter.run,
                    agent=run_config.role_agent("planner"),
                    prompt=self._repair_finalize_prompt(request_text, planning.metadata, missing_marker),
                    cwd=planner_cwd,
                    run_log_path=log_path,
                    config=run_config,
                    include_directories=self._gemini_include_directories(run_config, planning.metadata, planner_cwd),
                    session_id=planning.metadata.plan.session_id,
                    cancel_key=planning.metadata.task_id,
                )
                planning.metadata.plan.resolved_model = repair_result.resolved_model or planning.metadata.plan.resolved_model
                planning.metadata.plan.session_id = repair_result.session_id or planning.metadata.plan.session_id
                planning.metadata.plan.last_run_tokens = repair_result.total_tokens
                planning.metadata.plan.session_tokens = self.next_session_token_total(
                    reused_session_id=repair_session_id,
                    returned_session_id=repair_result.session_id,
                    prior_session_tokens=prior_repair_session_tokens,
                    run_tokens=repair_result.total_tokens,
                )
                if not repair_result.ok:
                    apply_retry_gate(planning.metadata, reason="planner-run-failed")
                    self.metadata_store.add_error(
                        planning.task_dir,
                        planning.metadata,
                        code="planner-run-failed",
                        message=repair_result.stderr.strip() or repair_result.assistant_text.strip() or "planner run failed",
                    )
                    self.metadata_store.save(planning.task_dir, planning.metadata)
                    self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner run failed")
                    raise AdapterRunError(repair_result.stderr.strip() or "planner run failed")
                if not repair_result.assistant_text.strip():
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
                plan_artifact, missing_marker = self._validated_plan_artifact(repair_result.assistant_text, planning.metadata)
                if plan_artifact is not None:
                    artifact_result = repair_result
            if plan_artifact is None:
                apply_retry_gate(planning.metadata, reason="planner-invalid-artifact")
                self.metadata_store.add_error(
                    planning.task_dir,
                    planning.metadata,
                    code="planner-invalid-artifact",
                    message=f"planner artifact missing required section: {missing_marker}",
                )
                self.metadata_store.save(planning.task_dir, planning.metadata)
                self.transitions.recover_move(planning, TaskState.REQUESTS, by=self.worker_name, note="planner artifact invalid")
                raise AdapterRunError(f"planner artifact missing required section: {missing_marker}")
            clear_retry_gate(planning.metadata)
            planning.metadata.plan.revision += 1
            planning.metadata.plan.restart_message_path = None
            reset_plan_approval_tracking(planning.metadata.plan_approval)
            finalized_result = RunResult(
                ok=artifact_result.ok,
                returncode=artifact_result.returncode,
                assistant_text=plan_artifact,
                stdout=artifact_result.stdout,
                stderr=artifact_result.stderr,
                raw_events_path=artifact_result.raw_events_path,
                command=artifact_result.command,
                resolved_model=planning.metadata.plan.resolved_model,
                session_id=planning.metadata.plan.session_id,
                total_tokens=artifact_result.total_tokens,
            )
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

    def _gemini_include_directories(self, run_config, metadata, planner_cwd: Path) -> list[Path] | None:
        if run_config.backend_for_role("planner") != "gemini":
            return None
        target_repo_root = Path(metadata.target.repo_root).expanduser().resolve()
        if target_repo_root == planner_cwd:
            return None
        return [target_repo_root]

    def _validated_plan_artifact(self, assistant_text: str, metadata) -> tuple[str | None, str | None]:
        artifact = _strip_outer_markdown_fence(assistant_text.strip())
        heading_lines = _heading_lines_outside_fences(artifact)
        cursor = 0
        for marker in self._required_heading_lines(metadata):
            while cursor < len(heading_lines) and heading_lines[cursor] != marker:
                cursor += 1
            if cursor >= len(heading_lines):
                return None, marker
            cursor += 1
        return artifact, None

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

    def _repair_finalize_prompt(self, request_text: str, metadata, missing_marker: str | None) -> str:
        source = self._planner_source_text(request_text, metadata)
        headings = self._required_heading_lines(metadata)
        missing_heading_instruction = f"- The missing heading was `{missing_marker}`. Include it as its own line." if missing_marker else "- Ensure every required heading is present as its own line."
        instructions = "\n".join(
            [
                source,
                "",
                "# Repair Plan Artifact",
                "- The previous final artifact was invalid because it did not match the required section headings.",
                "- Rewrite the final PLAN.md artifact from scratch.",
                "- Return only markdown. Do not add prefaces, explanations, code fences, JSON, or tool/log output.",
                "- Use the exact required headings below, in this exact order.",
                "- Every heading must start with `## ` exactly.",
                "- Required headings only count when they appear as their own line outside fenced code blocks.",
                missing_heading_instruction,
                *headings,
                "- Return only the corrected final markdown artifact.",
            ]
        )
        return self.build_prompt(instructions, metadata, phase="planner")

    def _required_heading_lines(self, metadata) -> list[str]:
        return [f"## {heading}" for heading in _expected_plan_headings(generation_language_code(metadata.request.language))]

    def _uses_multi_phase_planning(self, run_config) -> bool:
        return run_config.backend_for_role("planner") == "gemini" or self.worker_live_logs_enabled(run_config)


def _expected_plan_headings(language_code: str) -> tuple[str, ...]:
    if language_code == "ko":
        return (
            "요약",
            "범위",
            "범위 외",
            "파일 맵",
            "단계별 계획",
            "검증 계획",
            "승인 기준",
            "리스크",
            "열린 질문",
        )
    return (
        "Summary",
        "Scope",
        "Out of Scope",
        "File Map",
        "Step-by-step Plan",
        "Validation Plan",
        "Acceptance Criteria",
        "Risks",
        "Open Questions",
    )


def _strip_outer_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3:
        return stripped
    opening = lines[0].strip().lower()
    if opening not in {"```", "```markdown", "```md"}:
        return stripped
    if lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _heading_lines_outside_fences(text: str) -> list[str]:
    headings: list[str] = []
    active_fence: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        fence_char = _fence_char(stripped)
        if active_fence is not None:
            if fence_char == active_fence:
                active_fence = None
            continue
        if fence_char is not None:
            active_fence = fence_char
            continue
        if raw_line.startswith("## "):
            headings.append(raw_line.rstrip())
    return headings


def _fence_char(line: str) -> str | None:
    if line.startswith("```"):
        return "`"
    if line.startswith("~~~"):
        return "~"
    return None
