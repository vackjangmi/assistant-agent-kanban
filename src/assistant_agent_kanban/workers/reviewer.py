from __future__ import annotations

import asyncio
import json
import re
from datetime import timezone, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from ..assistant_adapter import AssistantAdapter
from ..enums import TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..language import generation_language_code, generation_language_name
from ..models import RunResult, TaskErrorInfo
from ..models import reset_review_loop_tracking
from ..retry_policy import apply_retry_gate, can_auto_dispatch, clear_retry_gate
from .base import WorkerBase


class ReviewFinalizeArtifact(TypedDict):
    schema_version: int
    artifact_type: Literal["review"]
    task_id: str
    cycle: int
    verdict: Literal["PASS", "NEEDS_CHANGES"]
    primary_blocker: str | None
    markdown: str


class ReviewerWorker(WorkerBase):
    worker_name = "reviewer"
    review_loop_escalation_threshold = 3
    total_rework_pause_threshold = 6

    def __init__(self, *args, adapter: AssistantAdapter, integration_manager: IntegrationManager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.integration_manager = integration_manager

    def candidate_tasks(self):
        return [
            task
            for task in self.scanner.scan()
            if task.state == TaskState.WAITING_REVIEWS
            and can_auto_dispatch(task.metadata)
            and not ((task.metadata.retry_gate.reason or "").startswith("review-"))
        ]

    async def run_once(self) -> bool:
        tasks = self.candidate_tasks()
        if not tasks:
            return False
        return await self.run_task(tasks[0])

    async def run_task(self, task) -> bool:
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            reviewing = self.transitions.move(task, TaskState.REVIEWING, by=self.worker_name)
            workspace_repo = reviewing.metadata.implementation.workspace
            if workspace_repo is None:
                apply_retry_gate(reviewing.metadata, reason="review-no-workspace")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-no-workspace", message="review skipped because implementation workspace is missing")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: missing workspace")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            workspace_path = Path(workspace_repo)
            if self.workspace_has_local_commits(workspace_path, task.metadata.target.base_branch):
                apply_retry_gate(reviewing.metadata, reason="review-local-commits")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-local-commits", message="review skipped because workspace contains local commits")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: workspace has local commits")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            if not self.workspace_has_changes(workspace_path):
                apply_retry_gate(reviewing.metadata, reason="review-no-changes")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-no-changes", message="review skipped because workspace has no file changes")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review skipped: no workspace changes")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            cycle = reviewing.metadata.cycle
            log_path = self.task_log_dir(task.metadata.task_id) / "reviewer.jsonl"
            log_name = log_path.name
            handshake_prompt = self._build_handshake_prompt(reviewing.metadata)
            live_prompt = self.build_prompt(
                self._build_reviewer_source(reviewing.task_dir, reviewing.metadata),
                reviewing.metadata,
                phase="reviewer",
            )
            finalize_prompt = self._build_finalize_prompt(reviewing.task_dir, reviewing.metadata)
            await self.emit("task_moved", reviewing.metadata.task_id, state=reviewing.state.value)
            await self.announce_log_file(reviewing.metadata.task_id, log_name)
            loop = asyncio.get_running_loop()
            run_config = self._resolve_reviewer_run_config(reviewing.task_dir, reviewing.metadata)
            adapter = self._resolve_reviewer_adapter(run_config)
            session_id = self.reuse_session_id(
                session_id=reviewing.metadata.review.session_id,
                session_tokens=reviewing.metadata.review.session_tokens,
                budget=run_config.role_session_token_budget("reviewer"),
            )
            prior_session_tokens = reviewing.metadata.review.session_tokens if session_id else 0

            if not self.worker_live_logs_enabled(run_config):
                self.append_log_marker(log_path=log_path, phase="run", cycle=cycle)
                result = await asyncio.to_thread(
                    adapter.run,
                    agent=run_config.role_agent("reviewer"),
                    prompt=finalize_prompt,
                    cwd=workspace_path,
                    run_log_path=log_path,
                    config=run_config,
                    session_id=session_id,
                    cancel_key=reviewing.metadata.task_id,
                    on_log_line=self.make_log_callback(loop, reviewing.metadata.task_id, log_name),
                    show_thinking=True,
                )
                reviewing.metadata.review.resolved_model = result.resolved_model
                reviewing.metadata.review.session_id = result.session_id
                reviewing.metadata.review.last_run_tokens = result.total_tokens
                reviewing.metadata.review.session_tokens = self.next_session_token_total(
                    reused_session_id=session_id,
                    returned_session_id=result.session_id,
                    prior_session_tokens=prior_session_tokens,
                    run_tokens=result.total_tokens,
                )
                artifact = self._parse_finalize_artifact(result.assistant_text)
                if not result.ok or artifact is None:
                    reviewing.metadata.errors.append(
                        TaskErrorInfo(code="review-finalize-failed", message=result.stderr.strip() or "review finalize artifact invalid")
                    )
                    apply_retry_gate(reviewing.metadata, reason="review-finalize-failed")
                    self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                    done = self.transitions.move(reviewing, TaskState.WAITING_REVIEWS, by=self.worker_name, note="review finalize failed")
                    await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                    return True
                reviewing.metadata.implementation.iteration = reviewing.metadata.cycle
                reviewing.metadata.review.iteration = reviewing.metadata.cycle
                verdict: Literal["PASS", "NEEDS_CHANGES"] = artifact["verdict"]
                reviewing.metadata.review.last_verdict = verdict
                review_name = f"REVIEW-{reviewing.metadata.cycle:03d}"
                finalized_result = RunResult(
                    ok=result.ok,
                    returncode=result.returncode,
                    assistant_text=cast(str, artifact["markdown"]),
                    stdout=result.stdout,
                    stderr=result.stderr,
                    raw_events_path=result.raw_events_path,
                    command=result.command,
                    resolved_model=reviewing.metadata.review.resolved_model,
                    session_id=reviewing.metadata.review.session_id,
                    total_tokens=result.total_tokens,
                )
                markdown_path, json_path = self.write_result_artifacts(reviewing.task_dir, review_name, finalized_result)
                review_payload = json.loads((reviewing.task_dir / json_path).read_text())
                review_payload["schema_version"] = artifact["schema_version"]
                review_payload["artifact_type"] = artifact["artifact_type"]
                review_payload["task_id"] = artifact["task_id"]
                review_payload["cycle"] = artifact["cycle"]
                review_payload["verdict"] = verdict
                review_payload["primary_blocker"] = artifact["primary_blocker"]
                (reviewing.task_dir / json_path).write_text(json.dumps(review_payload, indent=2) + "\n")
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                if verdict != "PASS":
                    note = self._handle_needs_changes(
                        reviewing.metadata,
                        primary_blocker=cast(str, artifact["primary_blocker"]),
                        blocker_patch_fingerprint=self.workspace_patch_fingerprint(
                            workspace_path, reviewing.metadata.target.base_branch
                        ),
                    )
                    self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                    done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note=note)
                else:
                    reset_review_loop_tracking(reviewing.metadata.review)
                    clear_retry_gate(reviewing.metadata)
                    self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                    done = self.transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by=self.worker_name, note="review passed")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            self.append_log_marker(log_path=log_path, phase="handshake", cycle=cycle)
            handshake_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("reviewer"),
                prompt=handshake_prompt,
                cwd=workspace_path,
                run_log_path=log_path,
                config=run_config,
                session_id=session_id,
                cancel_key=reviewing.metadata.task_id,
                show_thinking=True,
            )
            reviewing.metadata.review.resolved_model = handshake_result.resolved_model
            reviewing.metadata.review.session_id = handshake_result.session_id
            reviewing.metadata.review.last_run_tokens = handshake_result.total_tokens
            reviewing.metadata.review.session_tokens = self.next_session_token_total(
                reused_session_id=session_id,
                returned_session_id=handshake_result.session_id,
                prior_session_tokens=prior_session_tokens,
                run_tokens=handshake_result.total_tokens,
            )
            self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
            if not handshake_result.ok:
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-handshake-failed", message=handshake_result.stderr.strip() or "review handshake failed")
                )
                apply_retry_gate(reviewing.metadata, reason="review-handshake-failed")
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.WAITING_REVIEWS, by=self.worker_name, note="review handshake failed")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            active_session_id = handshake_result.session_id or session_id
            self.append_log_marker(log_path=log_path, phase="live", cycle=cycle)
            live_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("reviewer"),
                prompt=live_prompt,
                cwd=workspace_path,
                run_log_path=log_path,
                config=run_config,
                session_id=active_session_id,
                cancel_key=reviewing.metadata.task_id,
                on_log_line=self.make_log_callback(loop, reviewing.metadata.task_id, log_name),
                show_thinking=True,
            )
            if not live_result.ok:
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-live-failed", message=live_result.stderr.strip() or "review live run failed")
                )
                apply_retry_gate(reviewing.metadata, reason="review-live-failed")
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.WAITING_REVIEWS, by=self.worker_name, note="review live run failed")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            self.append_log_marker(log_path=log_path, phase="finalize", cycle=cycle)
            finalize_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("reviewer"),
                prompt=finalize_prompt,
                cwd=workspace_path,
                run_log_path=log_path,
                config=run_config,
                session_id=live_result.session_id or active_session_id,
                cancel_key=reviewing.metadata.task_id,
                show_thinking=True,
            )
            reviewing.metadata.review.resolved_model = finalize_result.resolved_model or live_result.resolved_model or handshake_result.resolved_model
            reviewing.metadata.review.session_id = finalize_result.session_id or live_result.session_id or active_session_id
            reviewing.metadata.review.last_run_tokens = finalize_result.total_tokens
            reviewing.metadata.review.session_tokens = self.next_session_token_total(
                reused_session_id=active_session_id,
                returned_session_id=finalize_result.session_id,
                prior_session_tokens=reviewing.metadata.review.session_tokens,
                run_tokens=finalize_result.total_tokens,
            )

            artifact = self._parse_finalize_artifact(finalize_result.assistant_text)
            if not finalize_result.ok or artifact is None:
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-finalize-failed", message=finalize_result.stderr.strip() or "review finalize artifact invalid")
                )
                apply_retry_gate(reviewing.metadata, reason="review-finalize-failed")
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.WAITING_REVIEWS, by=self.worker_name, note="review finalize failed")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            reviewing.metadata.implementation.iteration = reviewing.metadata.cycle
            reviewing.metadata.review.iteration = reviewing.metadata.cycle
            verdict: Literal["PASS", "NEEDS_CHANGES"] = artifact["verdict"]
            reviewing.metadata.review.last_verdict = verdict
            review_name = f"REVIEW-{reviewing.metadata.cycle:03d}"
            finalized_result = RunResult(
                ok=finalize_result.ok,
                returncode=finalize_result.returncode,
                assistant_text=cast(str, artifact["markdown"]),
                stdout=finalize_result.stdout,
                stderr=finalize_result.stderr,
                raw_events_path=finalize_result.raw_events_path,
                command=finalize_result.command,
                resolved_model=reviewing.metadata.review.resolved_model,
                session_id=reviewing.metadata.review.session_id,
                total_tokens=finalize_result.total_tokens,
            )
            markdown_path, json_path = self.write_result_artifacts(reviewing.task_dir, review_name, finalized_result)
            review_payload = json.loads((reviewing.task_dir / json_path).read_text())
            review_payload["schema_version"] = artifact["schema_version"]
            review_payload["artifact_type"] = artifact["artifact_type"]
            review_payload["task_id"] = artifact["task_id"]
            review_payload["cycle"] = artifact["cycle"]
            review_payload["verdict"] = verdict
            review_payload["primary_blocker"] = artifact["primary_blocker"]
            (reviewing.task_dir / json_path).write_text(json.dumps(review_payload, indent=2) + "\n")
            self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
            if verdict != "PASS":
                note = self._handle_needs_changes(
                    reviewing.metadata,
                    primary_blocker=cast(str, artifact["primary_blocker"]),
                    blocker_patch_fingerprint=self.workspace_patch_fingerprint(
                        workspace_path, reviewing.metadata.target.base_branch
                    ),
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note=note)
            else:
                reset_review_loop_tracking(reviewing.metadata.review)
                clear_retry_gate(reviewing.metadata)
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by=self.worker_name, note="review passed")
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    async def answer_human_question_async(self, task_id: str, *, by: str, question: str) -> dict[str, str | int | None]:
        loop = asyncio.get_running_loop()
        return await asyncio.to_thread(self.answer_human_question, task_id, by=by, question=question, event_loop=loop)

    def answer_human_question(
        self,
        task_id: str,
        *,
        by: str,
        question: str,
        event_loop: asyncio.AbstractEventLoop | None = None,
    ) -> dict[str, str | int | None]:
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc
        if task.state not in {TaskState.COMPLETED_REVIEWS, TaskState.HUMAN_VERIFYING}:
            raise TransitionError("reviewer Q&A is only available from completed-reviews or human-verifying")
        normalized_question = question.strip()
        if not normalized_question:
            raise TransitionError("reviewer question cannot be empty")

        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-reviewer-qa"):
            expected_qa_path = f"REVIEWER-QA-{task.metadata.cycle:03d}.md"
            if task.metadata.review.qa_path != expected_qa_path:
                task.metadata.review.qa_path = expected_qa_path
                task.metadata.review.qa_session_id = None
                task.metadata.review.qa_last_run_tokens = 0
                task.metadata.review.qa_session_tokens = 0
                task.metadata.review.qa_resolved_model = None
            cwd = self._reviewer_qa_cwd(task.metadata)
            log_path = self.task_log_dir(task.metadata.task_id) / "reviewer-qa.jsonl"
            run_config = self.resolve_task_run_config(task.task_dir, task.metadata)
            adapter = self.resolve_task_adapter(task.task_dir, task.metadata)
            session_id = self.reuse_session_id(
                session_id=task.metadata.review.qa_session_id,
                session_tokens=task.metadata.review.qa_session_tokens,
                budget=run_config.role_session_token_budget("reviewer"),
            )
            prior_session_tokens = task.metadata.review.qa_session_tokens if session_id else 0
            prompt = self._build_reviewer_qa_prompt(task.task_dir, task.metadata, normalized_question)
            on_log_line = self.make_log_callback(event_loop, task.metadata.task_id, log_path.name) if event_loop is not None else None
            result = adapter.run(
                agent=run_config.role_agent("reviewer"),
                prompt=prompt,
                cwd=cwd,
                run_log_path=log_path,
                config=run_config,
                session_id=session_id,
                cancel_key=task.metadata.task_id,
                on_log_line=on_log_line,
                show_thinking=True,
            )
            task.metadata.review.qa_resolved_model = result.resolved_model
            task.metadata.review.qa_session_id = result.session_id
            task.metadata.review.qa_last_run_tokens = result.total_tokens
            task.metadata.review.qa_session_tokens = self.next_session_token_total(
                reused_session_id=session_id,
                returned_session_id=result.session_id,
                prior_session_tokens=prior_session_tokens,
                run_tokens=result.total_tokens,
            )
            answer = result.assistant_text.strip()
            if not result.ok or not answer:
                task.metadata.errors.append(
                    TaskErrorInfo(code="reviewer-qa-failed", message=result.stderr.strip() or "reviewer Q&A failed")
                )
                self.metadata_store.save(task.task_dir, task.metadata)
                raise TransitionError(result.stderr.strip() or "reviewer Q&A failed")
            self._append_reviewer_qa_artifact(
                task.task_dir,
                task.metadata,
                question=normalized_question,
                answer=answer,
                asked_by=by,
            )
            qa_path = task.metadata.review.qa_path
            self.metadata_store.save(task.task_dir, task.metadata)
            return {
                "task_id": task.metadata.task_id,
                "question": normalized_question,
                "answer": answer,
                "qa_path": qa_path,
                "resolved_model": task.metadata.review.qa_resolved_model,
                "session_id": task.metadata.review.qa_session_id,
                "total_tokens": task.metadata.review.qa_last_run_tokens,
                "log_name": log_path.name,
            }

    def _build_reviewer_source(self, task_dir: Path, metadata) -> str:
        language = generation_language_code(metadata.request.language)
        implementation_iteration = metadata.cycle
        strings = REVIEWER_TEXT[language]
        sections = [
            f"# {strings['original_request']}",
            "",
            (task_dir / "REQUEST.md").read_text().rstrip(),
            "",
            f"# {strings['plan']}",
            "",
            (task_dir / "PLAN.md").read_text().rstrip(),
        ]

        work_files = sorted(task_dir.glob("WORK-*.md"))
        if work_files:
            sections.extend(["", f"# {strings['work_history']}"])
            for work_file in work_files:
                sections.extend(["", f"## {work_file.name}", "", work_file.read_text().rstrip()])

        human_verify_files = sorted(task_dir.glob("HUMAN-VERIFY-*.md"))
        if human_verify_files:
            sections.extend(
                [
                    "",
                    f"# {strings['primary_human_rework_goal']}",
                    "",
                    cast(str, strings['primary_human_rework_goal_instruction']),
                    "",
                    human_verify_files[-1].read_text().rstrip(),
                ]
            )
            sections.extend(["", f"# {strings['human_verification_history']}"])
            for verify_file in human_verify_files:
                sections.extend(["", f"## {verify_file.name}", "", verify_file.read_text().rstrip()])

        reviewer_qa_files = sorted(task_dir.glob("REVIEWER-QA-*.md"))
        if reviewer_qa_files:
            sections.extend(["", f"# {strings['reviewer_qa_history']}"])
            for qa_file in reviewer_qa_files:
                sections.extend(["", f"## {qa_file.name}", "", qa_file.read_text().rstrip()])

        review_files = sorted(task_dir.glob("REVIEW-*.md"))
        if review_files:
            sections.extend(["", f"# {strings['previous_reviews']}"])
            for review_file in review_files:
                sections.extend(["", f"## {review_file.name}", "", review_file.read_text().rstrip()])

        current_work = task_dir / f"WORK-{implementation_iteration:03d}.md"
        if current_work.exists():
            sections.extend(["", f"# {strings['current_work_artifact']}", "", current_work.read_text().rstrip()])

        sections.extend(
            [
                "",
                f"# {strings['review_instructions']}",
                "",
                *strings["instructions"],
            ]
        )
        return "\n".join(section for section in sections if section is not None)

    def _build_reviewer_qa_prompt(self, task_dir: Path, metadata, question: str) -> str:
        source = self._build_reviewer_source(task_dir, metadata)
        instructions = "\n".join(
            [
                source,
                "",
                "# Human Review Q&A",
                "Answer the human's question directly in markdown.",
                "Use the existing reviewed result and prior task artifacts as the source of truth.",
                "Do not produce the final review artifact JSON.",
                "Do not request file edits or change task state yourself.",
                "If the question reveals a real gap, explain it clearly so the human can send the task back for rework.",
                "",
                "## Human Question",
                question,
            ]
        )
        return self.build_prompt(instructions, metadata, phase="reviewer")

    def _ensure_reviewer_qa_path(self, metadata) -> str:
        expected_path = f"REVIEWER-QA-{metadata.cycle:03d}.md"
        if metadata.review.qa_path != expected_path:
            metadata.review.qa_path = expected_path
        return expected_path

    def _append_reviewer_qa_artifact(self, task_dir: Path, metadata, *, question: str, answer: str, asked_by: str) -> None:
        qa_path = task_dir / self._ensure_reviewer_qa_path(metadata)
        existing = qa_path.read_text().rstrip() if qa_path.exists() else ""
        exchange_count = existing.count("## Question") + 1
        now = datetime.now(timezone.utc).isoformat()
        sections: list[str] = []
        if existing:
            sections.extend([existing, ""])
        else:
            sections.extend([
                "# Reviewer Q&A",
                "",
                f"- Cycle: {metadata.cycle:03d}",
                "",
            ])
        sections.extend(
            [
                f"## Question {exchange_count}",
                f"- Asked by: {asked_by}",
                f"- Asked at: {now}",
                "",
                question,
                "",
                f"## Answer {exchange_count}",
                f"- Model: {metadata.review.qa_resolved_model or metadata.review.resolved_model or 'unknown'}",
                f"- Answered at: {now}",
                "",
                answer,
                "",
            ]
        )
        qa_path.write_text("\n".join(sections).rstrip() + "\n")

    def _reviewer_qa_cwd(self, metadata) -> Path:
        workspace_repo = metadata.implementation.workspace
        if workspace_repo:
            workspace_path = Path(workspace_repo).expanduser().resolve()
            if workspace_path.exists():
                return workspace_path
        raise TransitionError("reviewer Q&A requires an active implementation workspace")

    def _build_handshake_prompt(self, metadata) -> str:
        requested_language = generation_language_name(metadata.request.language)
        return "\n".join(
            [
                "You are preparing a reusable fs-kanban reviewer session.",
                f"Reply with one short greeting in {requested_language}.",
                "Do not review yet.",
                "Do not produce the final review artifact yet.",
            ]
        )

    def _build_finalize_prompt(self, task_dir: Path, metadata) -> str:
        source = self._build_reviewer_source(task_dir, metadata)
        instructions = "\n".join(
            [
                source,
                "",
                "# Finalize Review Artifact",
                "Return only valid JSON with this exact shape:",
                '{"schema_version":1,"artifact_type":"review","task_id":"...","cycle":1,"verdict":"PASS","primary_blocker":null,"markdown":"Verdict: PASS\\n\\n## Acceptance Criteria Check\\n..."}',
                "Allowed verdict values are PASS or NEEDS_CHANGES.",
                "Set `primary_blocker` to null for PASS.",
                "For NEEDS_CHANGES, set `primary_blocker` to a stable short kebab-case key for the main remaining blocker (example: changed-scope-coverage).",
                "The markdown field must contain the complete final review markdown.",
            ]
        )
        return self.build_prompt(instructions, metadata, phase="reviewer")

    def _parse_finalize_artifact(self, assistant_text: str) -> ReviewFinalizeArtifact | None:
        try:
            payload = json.loads(assistant_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        raw_verdict = payload.get("verdict")
        markdown = payload.get("markdown")
        artifact_type = payload.get("artifact_type")
        if raw_verdict not in {"PASS", "NEEDS_CHANGES"}:
            return None
        verdict = cast(Literal["PASS", "NEEDS_CHANGES"], raw_verdict)
        if artifact_type != "review":
            return None
        if not isinstance(markdown, str) or not markdown.strip():
            return None
        primary_blocker = self._normalize_primary_blocker(payload.get("primary_blocker"), verdict=verdict)
        return {
            "schema_version": payload.get("schema_version", 1),
            "artifact_type": "review",
            "task_id": str(payload.get("task_id", "")),
            "cycle": int(payload.get("cycle", 0)),
            "verdict": verdict,
            "primary_blocker": primary_blocker,
            "markdown": markdown.strip(),
        }

    def _normalize_primary_blocker(self, value: object, *, verdict: Literal["PASS", "NEEDS_CHANGES"]) -> str | None:
        if verdict == "PASS":
            return None
        if not isinstance(value, str) or not value.strip():
            return "unspecified-needs-changes"
        normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
        return normalized or "unspecified-needs-changes"

    def _resolve_reviewer_run_config(self, task_dir: Path, metadata):
        run_config = self.resolve_task_run_config(task_dir, metadata)
        if metadata.review.resume_mode is not None:
            metadata.review.resume_mode = None
        backend_override = metadata.review.resume_backend_override
        model_override = metadata.review.resume_model_override
        if backend_override is None and model_override is None:
            self.metadata_store.save(task_dir, metadata)
            return run_config
        overridden = run_config.model_copy(deep=True)
        if backend_override is not None:
            overridden.set_role_backend("reviewer", backend_override)
        overridden.set_role_model("reviewer", model_override)
        metadata.review.resume_backend_override = None
        metadata.review.resume_model_override = None
        self.metadata_store.save(task_dir, metadata)
        return overridden

    def _resolve_reviewer_adapter(self, run_config) -> AssistantAdapter:
        backend = run_config.backend_for_role("reviewer")
        adapter = self.adapter_registry.get(backend)
        return adapter or self.adapter

    def _handle_needs_changes(self, metadata, *, primary_blocker: str, blocker_patch_fingerprint: str | None) -> str:
        review = metadata.review
        plan_revision = metadata.plan.revision
        if review.rework_loop_plan_revision != plan_revision:
            reset_review_loop_tracking(review)
            review.rework_loop_plan_revision = plan_revision
        review.total_rework_loops += 1
        fingerprint_unchanged = (
            blocker_patch_fingerprint is None
            or review.last_blocker_patch_fingerprint == blocker_patch_fingerprint
        )
        if review.primary_blocker == primary_blocker and fingerprint_unchanged:
            review.consecutive_rework_loops += 1
        else:
            review.primary_blocker = primary_blocker
            review.consecutive_rework_loops = 1
        review.last_blocker_patch_fingerprint = blocker_patch_fingerprint
        if review.consecutive_rework_loops >= self.review_loop_escalation_threshold:
            review.human_rework_required = True
            review.human_rework_reason = (
                f"human review required after {review.consecutive_rework_loops} repeated rework loops for blocker '{primary_blocker}'"
            )
            return "review loop capped: repeated blocker requires human review"
        if review.total_rework_loops >= self.total_rework_pause_threshold and (
            review.total_rework_loops - review.last_backstop_pause_total_rework_loops >= self.total_rework_pause_threshold
        ):
            review.last_backstop_pause_total_rework_loops = review.total_rework_loops
            apply_retry_gate(metadata, reason="review-rework-backstop")
            return "review loop paused: too many total rework cycles"
        apply_retry_gate(metadata, reason="review-needs-changes")
        return "review needs changes"


REVIEWER_TEXT = {
    "en": {
        "original_request": "Original Request",
        "plan": "Plan",
        "work_history": "Work History",
        "primary_human_rework_goal": "Primary Human Rework Goal",
        "primary_human_rework_goal_instruction": "Treat the latest human verification request as the highest-priority outcome for this review cycle, but only as a current-cycle refinement inside the bounds of the original request, the approved plan, and the repository invariants. If it conflicts with prior AI review guidance, judge the work against the human request first and only keep older review findings when they still matter after that request is satisfied.",
        "previous_reviews": "Previous AI Reviews",
        "human_verification_history": "Human Verification History",
        "reviewer_qa_history": "Reviewer Q&A History",
        "current_work_artifact": "Current Work Artifact",
        "review_instructions": "Review Instructions",
        "instructions": [
            "- Judge against the original request and approved plan first, then the latest human verification request and reviewer Q&A as the current-cycle delta, and only then older AI review history if it still applies.",
            "- Treat the latest human verification request as the authoritative goal for this cycle, but not in ways that break the original request, the approved plan, or repository invariants.",
            "- Do not repeat earlier findings unless they still apply; explain why they remain unresolved.",
            "- If you return NEEDS_CHANGES, identify the main remaining blocker consistently so repeated reviews can tell whether the same blocker is still unresolved or a new blocker has replaced it.",
            "- Use `Verdict: NEEDS_CHANGES` only when implementation changes are still required.",
            "- If the work is acceptable with only minor notes, prefer `Verdict: PASS` and list the notes under follow-ups.",
        ],
    },
    "ko": {
        "original_request": "원래 요청",
        "plan": "계획",
        "work_history": "작업 이력",
        "primary_human_rework_goal": "최우선 인간 재요청 목표",
        "primary_human_rework_goal_instruction": "최신 사람 검증 요청을 이번 리뷰 사이클의 최우선 결과 기준으로 취급하되, 그것은 원래 요청과 승인된 계획, 저장소 불변 조건 안에서 이루어지는 현재 사이클의 수정이어야 합니다. 이전 AI 리뷰 지침과 충돌하면 먼저 사람 요청을 기준으로 판단하고, 그 요청을 충족한 뒤에도 여전히 중요한 항목만 이전 리뷰 지적으로 유지하세요.",
        "previous_reviews": "이전 AI 리뷰",
        "human_verification_history": "사람 검증 이력",
        "reviewer_qa_history": "리뷰어 질의응답 이력",
        "current_work_artifact": "현재 작업 산출물",
        "review_instructions": "리뷰 지침",
        "instructions": [
            "- 판단하기 전에 먼저 원래 요청과 승인된 계획을 기준으로 보고, 그 다음 최신 사람 검증 요청과 리뷰어 질의응답을 이번 사이클의 변경분으로 확인한 뒤, 마지막으로 이전 AI 리뷰 이력이 아직 유효한지 보세요.",
            "- 최신 사람 검증 요청이 예전 리뷰 지침과 충돌하면 이번 사이클에서는 사람 요청을 우선 기준으로 삼되, 원래 요청과 승인된 계획, 저장소 불변 조건을 깨지는 마세요.",
            "- 예전 지적을 그대로 반복하지 말고, 아직 유효하다면 왜 해결되지 않았는지 설명하세요.",
            "- NEEDS_CHANGES를 반환할 때는 이번 사이클의 주된 남은 blocker를 일관된 짧은 키로 식별해, 같은 blocker의 반복인지 새로운 blocker로 진전된 것인지 구분할 수 있게 하세요.",
            "- 실제 구현 수정이 더 필요할 때만 `Verdict: NEEDS_CHANGES`를 사용하세요.",
            "- 사소한 후속 메모만 남는 수준이면 `Verdict: PASS`를 우선하고 후속 항목 아래에 정리하세요.",
        ],
    },
}
