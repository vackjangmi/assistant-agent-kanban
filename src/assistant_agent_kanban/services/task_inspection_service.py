from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from filelock import FileLock, Timeout

from ..assistant_adapter import AssistantAdapter
from ..config import AppConfig
from ..enums import TaskState
from ..exceptions import AdapterRunError, InspectionError, TaskNotFoundError
from ..log_parser import render_assistant_log
from ..models import (
    TaskContext,
    TaskInspectionAnswer,
    TaskInspectionFaq,
    TaskInspectionSignal,
    TaskInspectionSnapshot,
    utc_now,
)
from ..scanner import AGENT_ACTIVE_STATES, KanbanScanner


MAX_LOG_EXCERPT_CHARS = 5000
MAX_ARTIFACT_EXCERPT_CHARS = 9000
MAX_WORKSPACE_STATUS_LINES = 80
INSPECTOR_SESSION_STATE_NAME = "SESSION.json"


INSPECTION_FAQS: tuple[TaskInspectionFaq, ...] = (
    TaskInspectionFaq(
        id="is-running",
        label="Running normally?",
        question="Is this task currently running normally, or does it look stuck?",
    ),
    TaskInspectionFaq(
        id="latest-activity",
        label="Latest activity",
        question="What happened most recently on this task?",
    ),
    TaskInspectionFaq(
        id="why-waiting",
        label="Why waiting?",
        question="Why is this task waiting, blocked, or not progressing?",
    ),
    TaskInspectionFaq(
        id="workspace-changes",
        label="Workspace changes",
        question="What workspace changes are visible right now?",
    ),
    TaskInspectionFaq(
        id="next-step",
        label="Next step",
        question="What should I check or do next?",
    ),
)


class TaskInspectionService:
    def __init__(
        self,
        *,
        config: AppConfig,
        scanner: KanbanScanner,
        adapter_registry: Mapping[Any, AssistantAdapter] | None = None,
    ) -> None:
        self.config = config
        self.scanner = scanner
        self.adapter_registry = dict(adapter_registry or {})

    def faqs(self) -> list[TaskInspectionFaq]:
        return [faq.model_copy() for faq in INSPECTION_FAQS]

    def inspect(self, task_id: str) -> TaskInspectionSnapshot:
        task = self._find_task(task_id)
        now = datetime.now(timezone.utc)
        lease_age_seconds = self._age_seconds(task.metadata.lease.heartbeat_at, now)
        last_log_name, last_log_updated_at, log_files = self._log_summary(task.metadata.task_id)
        last_log_age_seconds = self._age_seconds(last_log_updated_at, now)
        workspace_path = Path(task.metadata.implementation.workspace).expanduser() if task.metadata.implementation.workspace else None
        workspace_exists = bool(workspace_path and workspace_path.exists())
        workspace_changes = self._workspace_status(workspace_path) if workspace_path else []
        health = self._health(task, lease_age_seconds=lease_age_seconds, last_log_age_seconds=last_log_age_seconds)
        snapshot = TaskInspectionSnapshot(
            task_id=task.metadata.task_id,
            title=task.metadata.title,
            state=task.state,
            health=health,
            summary=self._summary(task, health=health, lease_age_seconds=lease_age_seconds, last_log_age_seconds=last_log_age_seconds),
            state_entered_at=self._state_entered_at(task),
            lease_owner=task.metadata.lease.owner,
            lease_run_id=task.metadata.lease.run_id,
            lease_heartbeat_at=task.metadata.lease.heartbeat_at,
            lease_age_seconds=lease_age_seconds,
            stale_after_seconds=self.config.locks.stale_after_seconds,
            active_model=self._active_model(task),
            retry_gate_reason=task.metadata.retry_gate.reason,
            retry_not_before=task.metadata.retry_gate.not_before,
            last_log_name=last_log_name,
            last_log_updated_at=last_log_updated_at,
            last_log_age_seconds=last_log_age_seconds,
            log_files=log_files,
            workspace_path=str(workspace_path) if workspace_path else None,
            workspace_exists=workspace_exists,
            workspace_change_count=len(workspace_changes),
            workspace_changes=workspace_changes[:MAX_WORKSPACE_STATUS_LINES],
            recent_log_excerpt=self._recent_log_excerpt(task.metadata.task_id, last_log_name),
            recent_errors=task.metadata.errors[-5:],
            signals=self._signals(task, health=health, lease_age_seconds=lease_age_seconds, last_log_age_seconds=last_log_age_seconds, workspace_changes=workspace_changes),
            faqs=self.faqs(),
        )
        return snapshot

    def answer(self, task_id: str, *, question: str | None = None, question_id: str | None = None) -> TaskInspectionAnswer:
        normalized_question = self._resolve_question(question=question, question_id=question_id)
        inspection = self.inspect(task_id)
        prompt = self._build_prompt(inspection, normalized_question)
        inspection_dir = self._inspection_dir(inspection.task_id)
        bundle_path = inspection_dir / "INSPECTION-BUNDLE.md"
        bundle_path.write_text(self._bundle_markdown(inspection, normalized_question))
        log_path = self.config.runs_dir / inspection.task_id / "inspector.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        run_config = self._inspector_run_config()
        adapter = self._resolve_adapter(run_config)
        lock = self._acquire_session_lock(inspection.task_id)
        try:
            session_state = self._read_session_state(inspection.task_id)
            session_id = session_state.get("session_id")
            if not isinstance(session_id, str):
                session_id = None
            result = adapter.run(
                agent=run_config.role_agent("inspector"),
                prompt=prompt,
                cwd=inspection_dir,
                run_log_path=log_path,
                config=run_config,
                include_directories=None,
                session_id=session_id,
                cancel_key=f"inspector-{inspection.task_id}",
                output_format="json",
                stream_stderr_to_log=True,
                show_thinking=False,
            )
            resolved_session_id = result.session_id or session_id
            if resolved_session_id:
                session_tokens = self._session_state_int(session_state.get("session_tokens"))
                session_tokens += self._session_tokens_from_result(result.total_tokens, result.session_budget_tokens)
                self._write_session_state(
                    inspection.task_id,
                    session_id=resolved_session_id,
                    resolved_model=result.resolved_model,
                    total_tokens=result.total_tokens,
                    session_tokens=session_tokens,
                )
        except AdapterRunError:
            raise
        except Exception as exc:
            raise InspectionError("inspector runtime failed") from exc
        finally:
            lock.release()
        answer = result.assistant_text.strip()
        if not result.ok or not answer:
            raise InspectionError(result.stderr.strip() or "inspector did not return an answer")
        return TaskInspectionAnswer(
            task_id=inspection.task_id,
            question=normalized_question,
            question_id=question_id,
            answer=answer,
            inspection=inspection,
            resolved_model=result.resolved_model,
            session_id=result.session_id,
            total_tokens=result.total_tokens,
            log_name=log_path.name,
        )

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _inspector_run_config(self) -> AppConfig:
        return self.config.model_copy(deep=True)

    def _resolve_adapter(self, run_config: AppConfig) -> AssistantAdapter:
        backend = run_config.backend_for_role("inspector")
        adapter = self.adapter_registry.get(backend)
        if adapter is None:
            raise InspectionError(f"no adapter registered for inspector backend: {backend}")
        availability_error = adapter.availability_error(config=run_config, backend=backend)
        if availability_error is not None:
            raise InspectionError(f"{backend} backend is unavailable for inspector: {availability_error}")
        return adapter

    def _resolve_question(self, *, question: str | None, question_id: str | None) -> str:
        normalized = (question or "").strip()
        if normalized:
            return normalized
        if question_id:
            faq = next((item for item in INSPECTION_FAQS if item.id == question_id), None)
            if faq is not None:
                return faq.question
        raise InspectionError("inspection question cannot be empty")

    def _inspection_dir(self, task_id: str) -> Path:
        path = self.config.inspections_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _health(
        self,
        task: TaskContext,
        *,
        lease_age_seconds: int | None,
        last_log_age_seconds: int | None,
    ) -> Literal["active", "stale", "waiting", "blocked", "idle"]:
        if task.metadata.retry_gate.reason:
            return "blocked"
        if task.state not in AGENT_ACTIVE_STATES:
            return "idle"
        if not task.metadata.lease.owner:
            return "waiting"
        if lease_age_seconds is not None and lease_age_seconds > self.config.locks.stale_after_seconds:
            return "stale"
        return "active"

    def _summary(
        self,
        task: TaskContext,
        *,
        health: str,
        lease_age_seconds: int | None,
        last_log_age_seconds: int | None,
    ) -> str:
        if health == "blocked":
            return f"Task is gated by retry reason `{task.metadata.retry_gate.reason}`."
        if health == "active":
            lease_text = f"{lease_age_seconds}s ago" if lease_age_seconds is not None else "not recorded"
            log_text = f"; latest log {last_log_age_seconds}s ago" if last_log_age_seconds is not None else ""
            return f"Worker `{task.metadata.lease.owner}` is active; heartbeat {lease_text}{log_text}."
        if health == "stale":
            if self._is_recent(last_log_age_seconds):
                return f"Worker lease is stale; heartbeat was {lease_age_seconds}s ago, but latest runtime log is {last_log_age_seconds}s ago."
            return f"Worker lease is stale; last heartbeat was {lease_age_seconds}s ago."
        if health == "waiting":
            if self._is_recent(last_log_age_seconds):
                return f"Task has recent runtime log activity, but no worker lease is currently attached; latest log {last_log_age_seconds}s ago."
            return "Task is in an agent-run state, but no worker lease is currently attached."
        return "Task is not currently in an agent-run state."

    def _signals(
        self,
        task: TaskContext,
        *,
        health: str,
        lease_age_seconds: int | None,
        last_log_age_seconds: int | None,
        workspace_changes: list[str],
    ) -> list[TaskInspectionSignal]:
        signals = [
            TaskInspectionSignal(label="Health", value=health, tone=self._health_tone(health), detail=self._health_detail(health)),
            TaskInspectionSignal(
                label="Heartbeat",
                value=f"{lease_age_seconds}s ago" if lease_age_seconds is not None else "none",
                tone=self._heartbeat_tone(health=health, lease_age_seconds=lease_age_seconds),
                detail=f"Stale threshold is {self.config.locks.stale_after_seconds}s.",
            ),
            TaskInspectionSignal(
                label="Latest log",
                value=f"{last_log_age_seconds}s ago" if last_log_age_seconds is not None else "none",
                tone="neutral" if last_log_age_seconds is None else "good",
                detail="Runtime logs are read from the task run directory.",
            ),
            TaskInspectionSignal(
                label="Workspace",
                value=f"{len(workspace_changes)} changed paths",
                tone="good" if workspace_changes else "neutral",
                detail="Read-only git status from the isolated workspace.",
            ),
        ]
        if task.metadata.retry_gate.reason:
            signals.append(
                TaskInspectionSignal(
                    label="Retry gate",
                    value=task.metadata.retry_gate.reason,
                    tone="warning",
                    detail="Automatic dispatch is paused until the retry gate clears or a human resumes it.",
                )
            )
        if task.metadata.errors:
            signals.append(
                TaskInspectionSignal(
                    label="Recent errors",
                    value=str(len(task.metadata.errors)),
                    tone="warning",
                    detail=task.metadata.errors[-1].message,
                )
            )
        return signals

    def _health_tone(self, health: str) -> Literal["neutral", "good", "warning", "danger"]:
        if health == "active":
            return "good"
        if health in {"stale", "blocked"}:
            return "danger" if health == "stale" else "warning"
        return "neutral"

    def _health_detail(self, health: str) -> str:
        if health == "active":
            return "A worker lease is attached and its heartbeat is fresh."
        if health == "stale":
            return "The worker lease has not heartbeated within the configured threshold."
        if health == "blocked":
            return "Retry gating is currently preventing automatic progress."
        if health == "waiting":
            return "The state expects an agent worker, but no lease is attached."
        return "No worker is expected for this state."

    def _log_summary(self, task_id: str) -> tuple[str | None, datetime | None, list[str]]:
        log_dir = self.config.runs_dir / task_id
        if not log_dir.exists():
            return None, None, []
        paths = sorted(
            [path for path in log_dir.glob("*.jsonl") if path.is_file() and self._should_show_log_file(path.name)],
            key=lambda path: path.stat().st_mtime,
        )
        if not paths:
            return None, None, []
        latest = paths[-1]
        return latest.name, datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc), [path.name for path in paths]

    def _recent_log_excerpt(self, task_id: str, log_name: str | None) -> str:
        if not log_name:
            return ""
        path = self.config.runs_dir / task_id / log_name
        if not path.exists():
            return ""
        raw_content = path.read_text(errors="replace")
        rendered = render_assistant_log(raw_content, debug=True) or render_assistant_log(raw_content) or raw_content
        return self._tail_text(rendered, MAX_LOG_EXCERPT_CHARS)

    def _workspace_status(self, workspace_path: Path | None) -> list[str]:
        if workspace_path is None or not workspace_path.exists():
            return []
        result = subprocess.run(
            ["git", "-C", str(workspace_path), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _active_model(self, task: TaskContext) -> str | None:
        if task.state == TaskState.PLANNING:
            return task.metadata.plan.resolved_model
        if task.state == TaskState.PLAN_APPROVING:
            return task.metadata.plan_approval.resolved_model
        if task.state == TaskState.IMPLEMENTING:
            return task.metadata.implementation.resolved_model
        if task.state == TaskState.REVIEWING:
            return task.metadata.review.resolved_model
        return None

    def _state_entered_at(self, task: TaskContext) -> datetime | None:
        for entry in reversed(task.metadata.history):
            if entry.state == task.state:
                return entry.entered_at
        return None

    def _age_seconds(self, timestamp: datetime | None, now: datetime) -> int | None:
        if timestamp is None:
            return None
        return max(0, int((now - timestamp).total_seconds()))

    def _should_show_log_file(self, filename: str) -> bool:
        return filename != "inspector.jsonl" and not filename.endswith(("-handshake.jsonl", "-finalize.jsonl"))

    def _is_recent(self, age_seconds: int | None) -> bool:
        return age_seconds is not None and age_seconds <= self.config.locks.stale_after_seconds

    def _heartbeat_tone(self, *, health: str, lease_age_seconds: int | None) -> Literal["neutral", "good", "warning", "danger"]:
        heartbeat_stale = lease_age_seconds is not None and lease_age_seconds > self.config.locks.stale_after_seconds
        if health == "stale":
            return "danger"
        if heartbeat_stale or (health == "active" and lease_age_seconds is None):
            return "warning"
        if health == "active":
            return "good"
        return "neutral"

    def _acquire_session_lock(self, task_id: str):
        lock = FileLock(str(self._inspection_dir(task_id) / "SESSION.lock"))
        try:
            lock.acquire(timeout=self.config.locks.timeout_seconds)
        except Timeout as exc:
            raise InspectionError("inspector is already answering a question for this task") from exc
        return lock

    def _session_state_path(self, task_id: str) -> Path:
        return self._inspection_dir(task_id) / INSPECTOR_SESSION_STATE_NAME

    def _read_session_state(self, task_id: str) -> dict[str, object]:
        path = self._session_state_path(task_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _write_session_state(
        self,
        task_id: str,
        *,
        session_id: str,
        resolved_model: str | None,
        total_tokens: int,
        session_tokens: int,
    ) -> None:
        path = self._session_state_path(task_id)
        payload = {
            "task_id": task_id,
            "session_id": session_id,
            "resolved_model": resolved_model,
            "last_run_tokens": total_tokens,
            "session_tokens": max(0, session_tokens),
            "updated_at": utc_now().isoformat(),
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        tmp_path.replace(path)

    def _session_tokens_from_result(self, total_tokens: int, session_budget_tokens: int | None) -> int:
        if session_budget_tokens is not None:
            return max(0, session_budget_tokens)
        return max(0, total_tokens)

    def _session_state_int(self, value: object) -> int:
        if not isinstance(value, (int, float, str)):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _bundle_markdown(self, inspection: TaskInspectionSnapshot, question: str) -> str:
        sections = [
            "# Task Inspection Bundle",
            "",
            "This bundle is generated by Assistant Agent Kanban for read-only diagnosis.",
            "",
            "## Question",
            question,
            "",
            "## Current Diagnosis",
            f"- Task: {inspection.task_id} - {inspection.title}",
            f"- State: {inspection.state}",
            f"- Health: {inspection.health}",
            f"- Summary: {inspection.summary}",
            f"- Active model: {inspection.active_model or 'unknown'}",
            "",
            "## Lease",
            f"- Owner: {inspection.lease_owner or 'none'}",
            f"- Run id: {inspection.lease_run_id or 'none'}",
            f"- Heartbeat: {inspection.lease_heartbeat_at.isoformat() if inspection.lease_heartbeat_at else 'none'}",
            f"- Heartbeat age seconds: {inspection.lease_age_seconds if inspection.lease_age_seconds is not None else 'unknown'}",
            f"- Stale threshold seconds: {inspection.stale_after_seconds}",
            "",
            "## Retry Gate",
            f"- Reason: {inspection.retry_gate_reason or 'none'}",
            f"- Not before: {inspection.retry_not_before.isoformat() if inspection.retry_not_before else 'none'}",
            "",
            "## Logs",
            f"- Files: {', '.join(inspection.log_files) if inspection.log_files else 'none'}",
            f"- Latest: {inspection.last_log_name or 'none'}",
            f"- Latest age seconds: {inspection.last_log_age_seconds if inspection.last_log_age_seconds is not None else 'unknown'}",
            "",
            "### Recent Log Excerpt",
            inspection.recent_log_excerpt or "(empty)",
            "",
            "## Workspace",
            f"- Path: {inspection.workspace_path or 'none'}",
            f"- Exists: {'yes' if inspection.workspace_exists else 'no'}",
            f"- Changed paths: {inspection.workspace_change_count}",
            "",
            *self._workspace_markdown_lines(inspection.workspace_changes),
            "",
            "## Recent Errors",
            *self._error_markdown_lines(inspection),
        ]
        sections.extend(self._artifact_markdown_sections(inspection.task_id))
        return "\n".join(sections).rstrip() + "\n"

    def _build_prompt(self, inspection: TaskInspectionSnapshot, question: str) -> str:
        return "\n".join(
            [
                "You are the fs-kanban inspector runtime.",
                "You answer questions about a task using only the inspection bundle provided below.",
                "You are read-only. Do not modify files, run state transitions, apply patches, create commits, or request workspace edits.",
                "Do not treat assistant CLI internal state as source of truth.",
                "The source of truth is the generated inspection bundle, which was built from metadata.json, task artifacts, run logs, and read-only git status.",
                "If the evidence is insufficient, say what is unknown and which signal is missing.",
                "Answer concisely in the same language as the user's question when it is clear.",
                "",
                "<inspection-bundle>",
                self._bundle_markdown(inspection, question).rstrip(),
                "</inspection-bundle>",
            ]
        )

    def _workspace_markdown_lines(self, changes: list[str]) -> list[str]:
        if not changes:
            return ["- No workspace changes visible."]
        lines = ["### Git Status"]
        lines.extend(f"- `{line}`" for line in changes)
        return lines

    def _error_markdown_lines(self, inspection: TaskInspectionSnapshot) -> list[str]:
        if not inspection.recent_errors:
            return ["- none"]
        return [
            f"- `{error.code}` at {error.created_at.isoformat()}: {error.message}"
            for error in inspection.recent_errors
        ]

    def _artifact_markdown_sections(self, task_id: str) -> list[str]:
        task = self._find_task(task_id)
        names = ["REQUEST.md", "PLAN.md"]
        names.extend(path.name for path in sorted(task.task_dir.glob("WORK-*.md"))[-2:])
        names.extend(path.name for path in sorted(task.task_dir.glob("REVIEW-*.md"))[-2:])
        sections: list[str] = ["", "## Task Artifacts"]
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            path = task.task_dir / name
            if not path.exists():
                continue
            sections.extend(["", f"### {name}", self._tail_text(path.read_text(errors="replace"), MAX_ARTIFACT_EXCERPT_CHARS)])
        if len(sections) == 2:
            sections.append("- No readable task artifacts found.")
        return sections

    def _tail_text(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[-limit:]
