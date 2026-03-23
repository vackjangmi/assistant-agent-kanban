from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..assistant_adapter import AssistantAdapter
from ..enums import TaskState
from ..models import RunResult, TaskContext, utc_now
from ..retry_policy import can_auto_dispatch, clear_retry_gate
from .base import WorkerBase


RECOMMENDED_REVIEW_DELAY = timedelta(minutes=15)


class PlanApprovalDecision(BaseModel):
    disposition: Literal["auto_approve", "review_required", "review_recommended"]
    confidence: Literal["high", "medium", "low"] = "medium"
    risk_signals: list[str] = Field(default_factory=list)
    rationale: str = ""


class PlanApprovalWorker(WorkerBase):
    worker_name = "plan_approval"

    def __init__(self, *args, adapter: AssistantAdapter, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter

    def candidate_tasks(self):
        now = utc_now()
        return [
            task
            for task in self.scanner.scan()
            if can_auto_dispatch(task.metadata) and self._should_process(task, now=now)
        ]

    async def run_once(self) -> bool:
        tasks = self.candidate_tasks()
        if not tasks:
            return False
        return await self.run_task(tasks[0])

    async def run_task(self, task: TaskContext) -> bool:
        if task.state == TaskState.WAITING_CHECK_PLANS:
            return await self._auto_progress_recommended(task)

        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            log_path = self.task_log_dir(task.metadata.task_id) / "plan-approval.jsonl"
            log_name = log_path.name
            run_config = self.resolve_task_run_config(task.task_dir, task.metadata)
            adapter = self.resolve_task_adapter(task.task_dir, task.metadata)
            session_id = self.reuse_session_id(
                session_id=task.metadata.plan_approval.session_id,
                session_tokens=task.metadata.plan_approval.session_tokens,
                budget=run_config.role_session_token_budget("plan_approval"),
            )
            prior_session_tokens = task.metadata.plan_approval.session_tokens if session_id else 0
            prompt = self._build_prompt(task)
            await self.emit("task_moved", task.metadata.task_id, state=task.state.value)
            await self.announce_log_file(task.metadata.task_id, log_name)
            loop = asyncio.get_running_loop()
            result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("plan_approval"),
                prompt=prompt,
                cwd=self.config.repo_root.expanduser().resolve(),
                run_log_path=log_path,
                config=run_config,
                session_id=session_id,
                cancel_key=task.metadata.task_id,
                on_log_line=self.make_log_callback(loop, task.metadata.task_id, log_name),
            )
            task.metadata.plan_approval.resolved_model = result.resolved_model
            task.metadata.plan_approval.session_id = result.session_id
            task.metadata.plan_approval.last_run_tokens = result.total_tokens
            task.metadata.plan_approval.session_tokens = self.next_session_token_total(
                reused_session_id=session_id,
                returned_session_id=result.session_id,
                prior_session_tokens=prior_session_tokens,
                run_tokens=result.total_tokens,
            )
            decision = self._parse_decision(result)
            finalized_result = RunResult(
                ok=result.ok,
                returncode=result.returncode,
                assistant_text=self._decision_markdown(decision),
                stdout=result.stdout,
                stderr=result.stderr,
                raw_events_path=result.raw_events_path,
                command=result.command,
                resolved_model=task.metadata.plan_approval.resolved_model,
                session_id=task.metadata.plan_approval.session_id,
                total_tokens=result.total_tokens,
            )
            approval_path, json_path = self.write_result_artifacts(task.task_dir, "PLAN-APPROVAL", finalized_result)
            self._write_decision_json(task.task_dir / json_path, decision)
            self._apply_decision(task, decision, approval_path)
            self.metadata_store.save(task.task_dir, task.metadata)
            if decision.disposition == "auto_approve":
                clear_retry_gate(task.metadata)
                done = self.transitions.move(task, TaskState.TODOS, by=self.worker_name, note="plan auto-approved")
            elif decision.disposition == "review_recommended":
                done = self.transitions.move(task, TaskState.WAITING_CHECK_PLANS, by=self.worker_name, note="plan review recommended")
            else:
                done = self.transitions.move(task, TaskState.WAITING_CHECK_PLANS, by=self.worker_name, note="plan review required")
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    def _should_process(self, task: TaskContext, *, now) -> bool:
        if task.state == TaskState.PLAN_APPROVING:
            return True
        if task.state != TaskState.WAITING_CHECK_PLANS:
            return False
        auto_progress_at = task.metadata.plan_approval.auto_progress_at
        if task.metadata.plan_approval.disposition != "review_recommended" or auto_progress_at is None:
            return False
        return auto_progress_at <= now and task.metadata.plan_approval.source_plan_revision == task.metadata.plan.revision

    async def _auto_progress_recommended(self, task: TaskContext) -> bool:
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            if not self._should_process(task, now=utc_now()):
                return False
            clear_retry_gate(task.metadata)
            task.metadata.plan.approved = True
            task.metadata.plan_approval.auto_progress_at = None
            task.metadata.plan_approval.resolved_by = self.worker_name
            task.metadata.plan_approval.resolved_at = utc_now()
            done = self.transitions.move(task, TaskState.TODOS, by=self.worker_name, note="recommended review window elapsed")
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    def _build_prompt(self, task: TaskContext) -> str:
        plan_text = (task.task_dir / "PLAN.md").read_text().rstrip()
        request_text = (task.task_dir / "REQUEST.md").read_text().rstrip()
        return "\n".join(
            [
                "You are the fs-kanban plan approval worker.",
                "Decide whether the generated plan should auto-approve, require human review, or recommend human review.",
                "Return only strict JSON with keys: disposition, confidence, risk_signals, rationale.",
                "Allowed disposition values: auto_approve, review_required, review_recommended.",
                "Use review_required for destructive change risk, DB/schema changes, API contract changes, ambiguous requirements, or low confidence.",
                "Use review_recommended for user-visible behavior changes, multi-file scope, or larger implementation scope when the plan is still coherent.",
                "Use auto_approve only for small, clear, low-risk work.",
                "risk_signals must be a JSON array of short snake_case strings.",
                "rationale must be concise and concrete.",
                "",
                "# Request",
                request_text,
                "",
                "# Plan",
                plan_text,
            ]
        )

    def _parse_decision(self, result: RunResult) -> PlanApprovalDecision:
        fallback_message = result.stderr.strip() or result.assistant_text.strip() or "plan approval failed"
        if not result.ok:
            return PlanApprovalDecision(
                disposition="review_required",
                confidence="low",
                risk_signals=["approval_run_failed"],
                rationale=fallback_message,
            )
        try:
            payload = json.loads(result.assistant_text)
            return PlanApprovalDecision.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return PlanApprovalDecision(
                disposition="review_required",
                confidence="low",
                risk_signals=["approval_output_invalid"],
                rationale=fallback_message,
            )

    def _apply_decision(self, task: TaskContext, decision: PlanApprovalDecision, approval_path: str) -> None:
        resolved_at = utc_now()
        task.metadata.plan.approved = decision.disposition == "auto_approve"
        task.metadata.plan_approval.disposition = decision.disposition
        task.metadata.plan_approval.confidence = decision.confidence
        task.metadata.plan_approval.risk_signals = decision.risk_signals
        task.metadata.plan_approval.rationale = decision.rationale
        task.metadata.plan_approval.source_plan_revision = task.metadata.plan.revision
        task.metadata.plan_approval.auto_progress_at = (
            resolved_at + RECOMMENDED_REVIEW_DELAY if decision.disposition == "review_recommended" else None
        )
        task.metadata.plan_approval.resolved_by = self.worker_name
        task.metadata.plan_approval.resolved_at = resolved_at
        task.metadata.plan_approval.path = approval_path

    def _decision_markdown(self, decision: PlanApprovalDecision) -> str:
        signals = ", ".join(decision.risk_signals) if decision.risk_signals else "none"
        return "\n".join(
            [
                f"Disposition: {decision.disposition}",
                f"Confidence: {decision.confidence}",
                f"Risk signals: {signals}",
                "",
                decision.rationale or "No rationale provided.",
            ]
        )

    def _write_decision_json(self, path, decision: PlanApprovalDecision) -> None:
        payload: dict[str, object] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except json.JSONDecodeError:
                existing = {}
            if isinstance(existing, dict):
                payload.update(existing)
        payload.update(decision.model_dump(mode="json"))
        path.write_text(json.dumps(payload, indent=2) + "\n")
