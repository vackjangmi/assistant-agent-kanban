from __future__ import annotations

import asyncio
from pathlib import Path

from ..enums import TaskState
from ..integration_manager import IntegrationManager
from ..language import generation_language_code
from ..models import TaskErrorInfo
from ..assistant_adapter import AssistantAdapter
from ..retry_policy import apply_retry_gate, clear_retry_gate
from .base import WorkerBase


class ReviewerWorker(WorkerBase):
    worker_name = "reviewer"

    def __init__(self, *args, adapter: AssistantAdapter, integration_manager: IntegrationManager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.integration_manager = integration_manager

    def candidate_tasks(self):
        return [task for task in self.scanner.scan() if task.state == TaskState.WAITING_REVIEWS]

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
            run_log_path = self.task_log_dir(task.metadata.task_id) / f"reviewer-{reviewing.metadata.cycle:03d}.jsonl"
            prompt = self.build_prompt(
                self._build_reviewer_source(reviewing.task_dir, reviewing.metadata),
                reviewing.metadata,
                phase="reviewer",
            )
            await self.emit("task_moved", reviewing.metadata.task_id, state=reviewing.state.value)
            loop = asyncio.get_running_loop()
            session_id = self.reuse_session_id(
                session_id=reviewing.metadata.review.session_id,
                session_tokens=reviewing.metadata.review.session_tokens,
                budget=self.config.role_session_token_budget("reviewer"),
            )
            prior_session_tokens = reviewing.metadata.review.session_tokens if session_id else 0
            run_config = self.config.model_copy(deep=True)
            result = await asyncio.to_thread(
                self.adapter.run,
                agent=run_config.role_agent("reviewer"),
                prompt=prompt,
                cwd=workspace_path,
                run_log_path=run_log_path,
                config=run_config,
                session_id=session_id,
                cancel_key=reviewing.metadata.task_id,
                on_log_line=self.make_log_callback(loop, reviewing.metadata.task_id, run_log_path.name),
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
            if not result.assistant_text.strip():
                apply_retry_gate(reviewing.metadata, reason="review-empty-artifact")
                reviewing.metadata.errors.append(
                    TaskErrorInfo(code="review-empty-artifact", message="reviewer did not return a markdown artifact")
                )
                self.metadata_store.save(reviewing.task_dir, reviewing.metadata)
                done = self.transitions.move(reviewing, TaskState.TODOS, by=self.worker_name, note="review failed: empty artifact")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            reviewing.metadata.implementation.iteration = reviewing.metadata.cycle
            reviewing.metadata.review.iteration = reviewing.metadata.cycle
            verdict = "PASS" if "Verdict: PASS" in result.assistant_text or "VERDICT: PASS" in result.assistant_text else "NEEDS_CHANGES"
            reviewing.metadata.review.last_verdict = verdict
            review_name = f"REVIEW-{reviewing.metadata.cycle:03d}"
            self.write_result_artifacts(reviewing.task_dir, review_name, result)
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


REVIEWER_TEXT = {
    "en": {
        "plan": "Plan",
        "work_history": "Work History",
        "previous_reviews": "Previous AI Reviews",
        "human_verification_history": "Human Verification History",
        "current_work_artifact": "Current Work Artifact",
        "review_instructions": "Review Instructions",
        "instructions": [
            "- Check the full work history, previous AI reviews, and human verification history before deciding.",
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
        "current_work_artifact": "현재 작업 산출물",
        "review_instructions": "리뷰 지침",
        "instructions": [
            "- 판단하기 전에 전체 작업 이력, 이전 AI 리뷰, 사람 검증 이력을 모두 확인하세요.",
            "- 예전 지적을 그대로 반복하지 말고, 아직 유효하다면 왜 해결되지 않았는지 설명하세요.",
            "- 실제 구현 수정이 더 필요할 때만 `Verdict: NEEDS_CHANGES`를 사용하세요.",
            "- 사소한 후속 메모만 남는 수준이면 `Verdict: PASS`를 우선하고 후속 항목 아래에 정리하세요.",
        ],
    },
}
