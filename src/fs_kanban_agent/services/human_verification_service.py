from __future__ import annotations

from pathlib import Path

from ..commit_manager import CommitManager
from ..enums import TaskState
from ..exceptions import IntegrationConflictError, IntegrationError, TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..locks import TaskLockManager
from ..metadata_store import MetadataStore
from ..opencode_adapter import OpenCodeAdapter, AdapterRunError
from ..models import TaskContext, TaskErrorInfo
from ..scanner import KanbanScanner
from ..transitions import TransitionManager
from ..config import AppConfig


class HumanVerificationService:
    def __init__(
        self,
        scanner: KanbanScanner,
        config: AppConfig,
        metadata_store: MetadataStore,
        locks: TaskLockManager,
        transitions: TransitionManager,
        integration_manager: IntegrationManager,
        commit_manager: CommitManager,
        branch_summary_adapter: OpenCodeAdapter | None = None,
    ) -> None:
        self.scanner = scanner
        self.config = config
        self.metadata_store = metadata_store
        self.locks = locks
        self.transitions = transitions
        self.integration_manager = integration_manager
        self.commit_manager = commit_manager
        self.branch_summary_adapter = branch_summary_adapter

    def start(self, task_id: str, *, by: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.COMPLETED_REVIEWS:
            raise TransitionError("human verification can only start from completed-reviews")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-verifying"):
            workspace_repo = context.metadata.implementation.workspace
            if workspace_repo is None:
                raise IntegrationError("workspace path missing")
            context.metadata.integration.final_branch = None
            context.metadata.commit.review_sha = None
            try:
                if not context.metadata.integration.final_branch_summary:
                    context.metadata.integration.final_branch_summary = self._generate_branch_summary(context)
                self.integration_manager.apply_workspace(context.metadata, Path(workspace_repo))
                self.commit_manager.prepare_commit_message(context.task_dir, context.metadata)
                sha = self.commit_manager.commit_task(context.task_dir, context.metadata)
                context.metadata.commit.status = "review-committed"
                context.metadata.commit.sha = sha
                self.metadata_store.save(context.task_dir, context.metadata)
                return self.transitions.move(context, TaskState.HUMAN_VERIFYING, by=by, note="human verification started")
            except IntegrationConflictError as exc:
                self._write_human_verification_artifact(context.task_dir, context.metadata.cycle, "CONFLICT", str(exc))
                context.metadata.errors.append(TaskErrorInfo(code="integration-conflict", message=str(exc)))
                context.metadata.commit.status = "pending"
                context.metadata.commit.sha = None
                self.metadata_store.save(context.task_dir, context.metadata)
                return self.transitions.move(context, TaskState.TODOS, by=by, note="human verification blocked: integration conflict")
            except Exception as exc:
                try:
                    self.integration_manager.rollback_workspace(context.metadata)
                except Exception as cleanup_exc:
                    raise IntegrationError(f"{exc}; cleanup failed: {cleanup_exc}") from exc
                context.metadata.commit.status = "pending"
                context.metadata.commit.sha = None
                self.metadata_store.save(context.task_dir, context.metadata)
                raise

    def reject(self, task_id: str, *, by: str, note: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification rejection is only allowed from human-verifying")
        cleaned_note = note.strip()
        if not cleaned_note:
            raise TransitionError("rejection note is required")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-reject"):
            self._write_human_verification_artifact(context.task_dir, context.metadata.cycle, "REJECTED", cleaned_note)
            self.integration_manager.rollback_workspace(context.metadata)
            context.metadata.commit.status = "pending"
            context.metadata.commit.sha = None
            context.metadata.commit.review_sha = None
            context.metadata.errors.append(TaskErrorInfo(code="human-verification-rejected", message=cleaned_note))
            self.metadata_store.save(context.task_dir, context.metadata)
            return self.transitions.move(context, TaskState.TODOS, by=by, note=cleaned_note)

    def approve(self, task_id: str, *, by: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification approval is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-approve"):
            try:
                sha = self.commit_manager.finalize_review_branch(context.task_dir, context.metadata)
                self.integration_manager.finalize_workspace(context.metadata)
                context.metadata.commit.status = "committed"
                context.metadata.commit.sha = sha
                return self.transitions.move(context, TaskState.DONE, by=by, note="human verification approved")
            except Exception as exc:
                try:
                    self.integration_manager.rollback_workspace(context.metadata)
                except Exception as cleanup_exc:
                    raise IntegrationError(f"{exc}; cleanup failed: {cleanup_exc}") from exc
                context.metadata.commit.status = "pending"
                context.metadata.commit.sha = None
                context.metadata.commit.review_sha = None
                context.metadata.errors.append(TaskErrorInfo(code="human-verification-finalize-failed", message=str(exc)))
                return self.transitions.move(context, TaskState.TODOS, by=by, note=f"human verification finalize failed: {exc}")

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _write_human_verification_artifact(self, task_dir: Path, cycle: int, verdict: str, note: str) -> None:
        artifact_path = task_dir / f"HUMAN-VERIFY-{cycle:03d}.md"
        artifact_path.write_text(
            "\n".join(
                [
                    "# Human Verification",
                    "",
                    f"Verdict: {verdict}",
                    "",
                    "## Follow-ups",
                    note.strip(),
                    "",
                ]
            )
        )

    def _generate_branch_summary(self, context: TaskContext) -> str:
        fallback = self.commit_manager.sanitize_branch_summary(None, fallback_title=context.metadata.title)
        if self.branch_summary_adapter is None:
            return fallback
        prompt = "\n".join(
            [
                "Return only a concise English git branch summary.",
                "Requirements:",
                "- English words only",
                "- 2 to 6 words",
                "- lowercase ascii words separated by hyphens",
                "- no prefix like feature/",
                "- no punctuation other than hyphens",
                "- summarize the implementation goal naturally",
                "",
                f"Task title: {context.metadata.title}",
                f"Task ID: {context.metadata.task_id}",
            ]
        )
        run_log_path = self.config.runs_dir / context.metadata.task_id / f"branch-summary-{context.metadata.cycle:03d}.jsonl"
        try:
            result = self.branch_summary_adapter.run(
                agent="planner",
                prompt=prompt,
                cwd=context.task_dir,
                run_log_path=run_log_path,
                config=self.config,
            )
        except AdapterRunError:
            return fallback
        if not result.ok:
            return fallback
        return self.commit_manager.sanitize_branch_summary(result.assistant_text, fallback_title=context.metadata.title)
