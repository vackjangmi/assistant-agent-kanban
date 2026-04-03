from __future__ import annotations

import asyncio
import json
from datetime import timezone, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from ..assistant_adapter import AssistantAdapter
from ..enums import TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..language import generation_language_code, generation_language_name
from ..models import RunResult, TaskErrorInfo
from ..retry_policy import apply_retry_gate, can_auto_dispatch, clear_retry_gate
from .base import WorkerBase


class ReviewFinalizeArtifact(TypedDict):
    schema_version: int
    artifact_type: Literal["review"]
    task_id: str
    cycle: int
    verdict: Literal["PASS", "NEEDS_CHANGES"]
    markdown: str


class ReviewerWorker(WorkerBase):
    worker_name = "reviewer"

    def __init__(self, *args, adapter: AssistantAdapter, integration_manager: IntegrationManager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.integration_manager = integration_manager

    def candidate_tasks(self):
        return [task for task in self.scanner.scan() if task.state == TaskState.WAITING_REVIEWS and can_auto_dispatch(task.metadata)]

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
            session_id = self.reuse_session_id(
                session_id=reviewing.metadata.review.session_id,
                session_tokens=reviewing.metadata.review.session_tokens,
                budget=self.resolve_task_run_config(reviewing.task_dir, reviewing.metadata).role_session_token_budget("reviewer"),
            )
            prior_session_tokens = reviewing.metadata.review.session_tokens if session_id else 0
            run_config = self.resolve_task_run_config(reviewing.task_dir, reviewing.metadata)
            adapter = self.resolve_task_adapter(reviewing.task_dir, reviewing.metadata)

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
                    done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review finalize failed")
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
                (reviewing.task_dir / json_path).write_text(json.dumps(review_payload, indent=2) + "\n")
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                if verdict != "PASS":
                    apply_retry_gate(reviewing.metadata, reason="review-needs-changes")
                    self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                    done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review needs changes")
                else:
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
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review finalize failed")
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
            (reviewing.task_dir / json_path).write_text(json.dumps(review_payload, indent=2) + "\n")
            self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
            if verdict != "PASS":
                apply_retry_gate(reviewing.metadata, reason="review-needs-changes")
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review needs changes")
            else:
                clear_retry_gate(reviewing.metadata)
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by=self.worker_name, note="review passed")
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    def answer_human_question(self, task_id: str, *, by: str, question: str) -> dict[str, str | int | None]:
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
            result = adapter.run(
                agent=run_config.role_agent("reviewer"),
                prompt=prompt,
                cwd=cwd,
                run_log_path=log_path,
                config=run_config,
                session_id=session_id,
                cancel_key=task.metadata.task_id,
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
        sections = [f"# {strings['plan']}", "", (task_dir / "PLAN.md").read_text().rstrip()]

        work_files = sorted(task_dir.glob("WORK-*.md"))
        if work_files:
            sections.extend(["", f"# {strings['work_history']}"])
            for work_file in work_files:
                sections.extend(["", f"## {work_file.name}", "", work_file.read_text().rstrip()])

        review_files = sorted(task_dir.glob("REVIEW-*.md"))
        if review_files:
            sections.extend(["", f"# {strings['previous_reviews']}"])
            for review_file in review_files:
                sections.extend(["", f"## {review_file.name}", "", review_file.read_text().rstrip()])

        human_verify_files = sorted(task_dir.glob("HUMAN-VERIFY-*.md"))
        if human_verify_files:
            sections.extend(["", f"# {strings['human_verification_history']}"])
            for verify_file in human_verify_files:
                sections.extend(["", f"## {verify_file.name}", "", verify_file.read_text().rstrip()])

        reviewer_qa_files = sorted(task_dir.glob("REVIEWER-QA-*.md"))
        if reviewer_qa_files:
            sections.extend(["", f"# {strings['reviewer_qa_history']}"])
            for qa_file in reviewer_qa_files:
                sections.extend(["", f"## {qa_file.name}", "", qa_file.read_text().rstrip()])

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
                '{"schema_version":1,"artifact_type":"review","task_id":"...","cycle":1,"verdict":"PASS","markdown":"Verdict: PASS\\n\\n## Acceptance Criteria Check\\n..."}',
                "Allowed verdict values are PASS or NEEDS_CHANGES.",
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
        return {
            "schema_version": payload.get("schema_version", 1),
            "artifact_type": "review",
            "task_id": str(payload.get("task_id", "")),
            "cycle": int(payload.get("cycle", 0)),
            "verdict": verdict,
            "markdown": markdown.strip(),
        }


REVIEWER_TEXT = {
    "en": {
        "plan": "Plan",
        "work_history": "Work History",
        "previous_reviews": "Previous AI Reviews",
        "human_verification_history": "Human Verification History",
        "reviewer_qa_history": "Reviewer Q&A History",
        "current_work_artifact": "Current Work Artifact",
        "review_instructions": "Review Instructions",
        "instructions": [
            "- Check the full work history, previous AI reviews, human verification history, and reviewer Q&A history before deciding.",
            "- Do not repeat earlier findings unless they still apply; explain why they remain unresolved.",
            "- Use `Verdict: NEEDS_CHANGES` only when implementation changes are still required.",
            "- If the work is acceptable with only minor notes, prefer `Verdict: PASS` and list the notes under follow-ups.",
        ],
    },
    "ko": {
        "plan": "계획",
        "work_history": "작업 이력",
        "previous_reviews": "이전 AI 리뷰",
        "human_verification_history": "사람 검증 이력",
        "reviewer_qa_history": "리뷰어 질의응답 이력",
        "current_work_artifact": "현재 작업 산출물",
        "review_instructions": "리뷰 지침",
        "instructions": [
            "- 판단하기 전에 전체 작업 이력, 이전 AI 리뷰, 사람 검증 이력, 리뷰어 질의응답 이력을 모두 확인하세요.",
            "- 예전 지적을 그대로 반복하지 말고, 아직 유효하다면 왜 해결되지 않았는지 설명하세요.",
            "- 실제 구현 수정이 더 필요할 때만 `Verdict: NEEDS_CHANGES`를 사용하세요.",
            "- 사소한 후속 메모만 남는 수준이면 `Verdict: PASS`를 우선하고 후속 항목 아래에 정리하세요.",
        ],
    },
}
