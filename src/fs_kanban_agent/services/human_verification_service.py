from __future__ import annotations

from pathlib import Path

from ..commit_manager import CommitManager
from ..enums import TaskState
from ..exceptions import IntegrationError, TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..locks import TaskLockManager
from ..metadata_store import MetadataStore
from ..models import TaskContext, TaskErrorInfo
from ..scanner import KanbanScanner
from ..transitions import TransitionManager


class HumanVerificationService:
    def __init__(
        self,
        scanner: KanbanScanner,
        metadata_store: MetadataStore,
        locks: TaskLockManager,
        transitions: TransitionManager,
        integration_manager: IntegrationManager,
        commit_manager: CommitManager,
    ) -> None:
        self.scanner = scanner
        self.metadata_store = metadata_store
        self.locks = locks
        self.transitions = transitions
        self.integration_manager = integration_manager
        self.commit_manager = commit_manager

    def start(self, task_id: str, *, by: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.COMPLETED_REVIEWS:
            raise TransitionError("human verification can only start from completed-reviews")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-verifying"):
            workspace_repo = context.metadata.implementation.workspace
            if workspace_repo is None:
                raise IntegrationError("workspace path missing")
            self.integration_manager.apply_workspace(context.metadata, Path(workspace_repo))
            self.metadata_store.save(context.task_dir, context.metadata)
            return self.transitions.move(context, TaskState.HUMAN_VERIFYING, by=by, note="human verification started")

    def reject(self, task_id: str, *, by: str, note: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification rejection is only allowed from human-verifying")
        cleaned_note = note.strip()
        if not cleaned_note:
            raise TransitionError("rejection note is required")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-reject"):
            self._write_human_verification_artifact(context.task_dir, context.metadata.review.iteration, "REJECTED", cleaned_note)
            self.integration_manager.rollback_workspace(context.metadata)
            context.metadata.errors.append(TaskErrorInfo(code="human-verification-rejected", message=cleaned_note))
            self.metadata_store.save(context.task_dir, context.metadata)
            return self.transitions.move(context, TaskState.TODOS, by=by, note=cleaned_note)

    def approve(self, task_id: str, *, by: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification approval is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-approve"):
            sha = self.commit_manager.commit_task(context.task_dir, context.metadata)
            context.metadata.commit.status = "committed"
            context.metadata.commit.sha = sha
            self.metadata_store.save(context.task_dir, context.metadata)
            return self.transitions.move(context, TaskState.DONE, by=by, note="human verification approved")

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _write_human_verification_artifact(self, task_dir: Path, review_iteration: int, verdict: str, note: str) -> None:
        artifact_path = task_dir / f"HUMAN-VERIFY-{review_iteration:03d}.md"
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
