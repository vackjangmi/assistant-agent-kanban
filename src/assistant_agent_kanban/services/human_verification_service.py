from __future__ import annotations

from contextlib import contextmanager
import inspect
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal
import uuid
from typing import Mapping

from ..commit_manager import CommitManager
from ..enums import TaskState
from ..exceptions import AdapterRunError, IntegrationConflictError, IntegrationError, TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..locks import TaskLockManager
from ..markdown_attachments import normalize_markdown_attachments
from ..metadata_store import MetadataStore
from ..retry_policy import apply_retry_gate, clear_retry_gate
from ..assistant_adapter import AssistantAdapter
from ..models import HumanLineComment, HumanLineCommentAnchor, HumanLineCommentsArtifact, TaskContext, TaskErrorInfo, reset_review_loop_tracking, utc_now
from ..repo_branches import describe_target_repo_dirty_drift, describe_target_repo_head_drift, snapshot_target_repo_state
from ..scanner import KanbanScanner
from ..target_repo_guard import resolve_safe_target_repo_root
from ..transitions import TransitionManager
from ..config import AppConfig, AssistantBackend
from .task_service import TaskService


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
        branch_summary_adapter: AssistantAdapter | None = None,
        adapter_registry: Mapping[str | AssistantBackend, AssistantAdapter] | None = None,
    ) -> None:
        self.scanner = scanner
        self.config = config
        self.metadata_store = metadata_store
        self.locks = locks
        self.transitions = transitions
        self.integration_manager = integration_manager
        self.commit_manager = commit_manager
        self.branch_summary_adapter = branch_summary_adapter
        self.adapter_registry = dict(adapter_registry or {})

    def start(
        self,
        task_id: str,
        *,
        by: str,
        git_token: str | None = None,
        git_token_username: str | None = None,
        operation_config: AppConfig | None = None,
    ) -> TaskContext:
        with self._acquire_current_task_lease(task_id, owner=by, run_id="manual-human-verifying") as context:
            if context.state != TaskState.COMPLETED_REVIEWS:
                raise TransitionError("human verification can only start from completed-reviews")
            drift_note = self._target_repo_state_drift_note(context.metadata)
            if drift_note is not None:
                apply_retry_gate(context.metadata, reason="verification-target-repo-drift")
                context.metadata.errors.append(TaskErrorInfo(code="verification-target-repo-drift", message=drift_note))
                self.metadata_store.save(context.task_dir, context.metadata)
                return self.transitions.move(context, TaskState.TODOS, by=by, note=drift_note)
            self._invoke_run_verification_apply(
                context,
                git_token=git_token,
                git_token_username=git_token_username,
                operation_config=operation_config,
            )
            self.metadata_store.save(context.task_dir, context.metadata)
            try:
                return self.transitions.move(context, TaskState.HUMAN_VERIFYING, by=by, note="human verification started")
            except Exception:
                if context.metadata.integration.applied:
                    self._integration_manager(operation_config).rollback_workspace(
                        context.metadata,
                        git_token=git_token,
                        git_token_username=git_token_username,
                    )
                    context.metadata.commit.status = "pending"
                    context.metadata.commit.sha = None
                    context.metadata.commit.review_sha = None
                    self.metadata_store.save(context.task_dir, context.metadata)
                raise

    @contextmanager
    def _acquire_current_task_lease(self, task_id: str, *, owner: str, run_id: str):
        with self.locks.acquire_by_task_id(task_id, owner=owner, run_id=run_id):
            context = self._find_task(task_id)
            self.locks.heartbeat(context.task_dir, context.metadata, owner=owner, run_id=run_id)
            try:
                yield context
            finally:
                try:
                    refreshed = self._find_task(task_id)
                except TaskNotFoundError:
                    return
                refreshed.metadata.lease.owner = None
                refreshed.metadata.lease.run_id = None
                refreshed.metadata.lease.heartbeat_at = None
                self.metadata_store.save(refreshed.task_dir, refreshed.metadata)

    def retry_apply(
        self,
        task_id: str,
        *,
        by: str,
        git_token: str | None = None,
        git_token_username: str | None = None,
        operation_config: AppConfig | None = None,
    ) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("verification apply retry is only allowed from human-verifying")
        if context.metadata.integration.applied:
            raise TransitionError("verification apply has already succeeded")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-verifying-retry"):
            self._invoke_run_verification_apply(
                context,
                git_token=git_token,
                git_token_username=git_token_username,
                operation_config=operation_config,
            )
            self.metadata_store.save(context.task_dir, context.metadata)
            return context

    def save_note(self, task_id: str, *, by: str, content: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification note editing is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-note"):
            normalized = normalize_markdown_attachments(context.task_dir, content)
            context.metadata.human_verification.note_markdown = normalized.rstrip()
            self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="IN_PROGRESS")
            self.metadata_store.save(context.task_dir, context.metadata)
            return context

    def add_line_comment(
        self,
        task_id: str,
        *,
        by: str,
        path: str,
        side: Literal["left", "right"],
        line_number: int,
        line_kind: Literal["context", "add", "remove"],
        hunk_header: str | None,
        body_markdown: str,
    ) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("line comments can only be added from human-verifying")
        body = body_markdown.rstrip()
        if not body.strip():
            raise TransitionError("line comment body cannot be empty")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-line-comment"):
            artifact = self._load_comments_artifact(context.task_dir, context.metadata)
            artifact.comments.append(
                HumanLineComment(
                    id=f"comment-{uuid.uuid4().hex[:12]}",
                    anchor=HumanLineCommentAnchor(
                        path=path,
                        side=side,
                        line_number=line_number,
                        line_kind=line_kind,
                        hunk_header=hunk_header,
                    ),
                    body_markdown=body,
                    cycle=context.metadata.cycle,
                    author=by,
                    updated_at=utc_now(),
                )
            )
            self._save_comments_artifact(context.task_dir, context.metadata, artifact)
            self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="IN_PROGRESS")
            self.metadata_store.save(context.task_dir, context.metadata)
            return context

    def delete_line_comment(self, task_id: str, *, by: str, comment_id: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("line comments can only be deleted from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-line-comment-delete"):
            artifact = self._load_comments_artifact(context.task_dir, context.metadata)
            current_comment_ids = {comment.id for comment in artifact.comments}
            all_comment_ids = {comment.id for comment in self._load_all_comments(context.task_dir)}
            if comment_id in all_comment_ids and comment_id not in current_comment_ids:
                raise TransitionError("historical line comments are read-only")
            remaining_comments = [comment for comment in artifact.comments if comment.id != comment_id]
            if len(remaining_comments) == len(artifact.comments):
                raise TaskNotFoundError(comment_id)
            artifact.comments = remaining_comments
            self._save_comments_artifact(context.task_dir, context.metadata, artifact)
            self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="IN_PROGRESS")
            self.metadata_store.save(context.task_dir, context.metadata)
            return context

    def reject(
        self,
        task_id: str,
        *,
        by: str,
        note: str = "",
        git_token: str | None = None,
        git_token_username: str | None = None,
        operation_config: AppConfig | None = None,
    ) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification rejection is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-reject"):
            if not self._has_current_human_review_feedback(context.task_dir, context.metadata, note=note):
                raise TransitionError("request changes is only available after adding a review note or line comment")
            if note.strip():
                normalized = normalize_markdown_attachments(context.task_dir, note)
                context.metadata.human_verification.note_markdown = normalized.rstrip()
            recapture_error: str | None = None
            if context.metadata.integration.applied:
                remote_sync_required = False
                try:
                    integration_manager = self._integration_manager(operation_config)
                    remote_sync_required = bool(context.metadata.integration.remote_review_branch and integration_manager.config.review_branch_remote.enabled)
                    if remote_sync_required:
                        integration_manager.sync_remote_review_branch(
                            context.metadata,
                            git_token=git_token,
                            git_token_username=git_token_username,
                        )
                    self._capture_review_branch_to_workspace(context.metadata)
                except IntegrationError as exc:
                    if remote_sync_required:
                        raise
                    recapture_error = str(exc)
            self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="REQUEST_CHANGES")
            self._integration_manager(operation_config).rollback_workspace(
                context.metadata,
                git_token=git_token,
                git_token_username=git_token_username,
                delete_remote_review_branch=False,
                preserve_remote_review_branch=True,
            )
            context.metadata.commit.status = "pending"
            context.metadata.commit.sha = None
            context.metadata.commit.review_sha = None
            self._reset_implementation_context(context.metadata)
            reset_review_loop_tracking(context.metadata.review)
            clear_retry_gate(context.metadata)
            summary = self._human_review_summary(context.metadata)
            context.metadata.errors.append(TaskErrorInfo(code="human-verification-rejected", message=summary or "human verification requested changes"))
            if recapture_error:
                context.metadata.errors.append(TaskErrorInfo(code="human-verification-recapture-failed", message=recapture_error))
            self.metadata_store.save(context.task_dir, context.metadata)
            return self.transitions.move(context, TaskState.TODOS, by=by, note=summary or "human verification requested changes")

    def rerequest_from_reviewer_qa(
        self,
        task_id: str,
        *,
        by: str,
        git_token: str | None = None,
        git_token_username: str | None = None,
        operation_config: AppConfig | None = None,
    ) -> TaskContext:
        context = self._find_task(task_id)
        if context.state not in {TaskState.COMPLETED_REVIEWS, TaskState.HUMAN_VERIFYING}:
            raise TransitionError("reviewer Q&A re-request is only allowed from completed-reviews or human-verifying")
        note = self._build_reviewer_qa_rerequest_note(context)
        if not note:
            raise TransitionError("reviewer Q&A re-request requires at least one completed reviewer answer")
        if context.state == TaskState.COMPLETED_REVIEWS:
            self.start(
                task_id,
                by=by,
                git_token=git_token,
                git_token_username=git_token_username,
                operation_config=operation_config,
            )
        self.save_note(task_id, by=by, content=note)
        return self.reject(
            task_id,
            by=by,
            note="",
            git_token=git_token,
            git_token_username=git_token_username,
            operation_config=operation_config,
        )

    def approve(
        self,
        task_id: str,
        *,
        by: str,
        completion_mode: str = "new-branch",
        git_token: str | None = None,
        git_token_username: str | None = None,
        operation_config: AppConfig | None = None,
    ) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification approval is only allowed from human-verifying")
        if completion_mode not in {"new-branch", "target-branch"}:
            raise TransitionError(f"unsupported completion mode: {completion_mode}")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-approve"):
            done_context: TaskContext | None = None
            try:
                if not context.metadata.integration.applied:
                    raise TransitionError("approval is blocked until verification apply succeeds")
                if context.metadata.human_verification.note_markdown.strip():
                    raise TransitionError("approval is blocked until the review note is cleared")
                incomplete_qa_items = [
                    item
                    for item in context.metadata.human_verification.qa_items
                    if context.metadata.human_verification.qa_cycle == context.metadata.cycle
                    and item.required
                    and not item.checked
                    and not item.skipped
                ]
                if incomplete_qa_items:
                    count = len(incomplete_qa_items)
                    raise TransitionError(f"approval is blocked until required QA checklist items are completed ({count} remaining)")
                unresolved_comments = [comment for comment in self._load_comments_artifact(context.task_dir, context.metadata).comments if not comment.resolved]
                if unresolved_comments:
                    count = len(unresolved_comments)
                    raise TransitionError(f"approval is blocked until all inline comments are removed ({count} remaining)")
                self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="APPROVED")
                integration_manager = self._integration_manager(operation_config)
                remote_completion = bool(
                    context.metadata.integration.remote_review_branch
                    and integration_manager.config.review_branch_remote.enabled
                )
                if remote_completion:
                    integration_manager.sync_remote_review_branch(
                        context.metadata,
                        git_token=git_token,
                        git_token_username=git_token_username,
                    )
                    sha = integration_manager.push_final_review_branch(
                        context.metadata,
                        final_branch=self.commit_manager.preferred_final_branch(context.metadata),
                        git_token=git_token,
                        git_token_username=git_token_username,
                    )
                    context.metadata.commit.review_sha = sha
                    integration_manager.finalize_remote_workspace(
                        context.metadata,
                        git_token=git_token,
                        git_token_username=git_token_username,
                    )
                else:
                    summary_markdown = self._sync_task_documents_to_target_repo(context.task_dir, context.metadata)
                    self.commit_manager.prepare_commit_message(
                        context.task_dir,
                        context.metadata,
                        summary_markdown=summary_markdown,
                    )
                    sha = self.commit_manager.finalize_review_branch(
                        context.task_dir,
                        context.metadata,
                        completion_mode=completion_mode,
                    )
                    integration_manager.finalize_workspace(
                        context.metadata,
                        git_token=git_token,
                        git_token_username=git_token_username,
                    )
                context.metadata.commit.status = "committed"
                context.metadata.commit.sha = sha
                if remote_completion:
                    completion_label = "remote branch"
                else:
                    completion_label = "new branch" if completion_mode == "new-branch" else "target branch"
                done_context = self.transitions.move(context, TaskState.DONE, by=by, note=f"human verification approved ({completion_label})")
            except TransitionError:
                raise
            except Exception as exc:
                try:
                    self._integration_manager(operation_config).rollback_workspace(
                        context.metadata,
                        git_token=git_token,
                        git_token_username=git_token_username,
                    )
                except Exception as cleanup_exc:
                    raise IntegrationError(f"{exc}; cleanup failed: {cleanup_exc}") from exc
                context.metadata.commit.status = "pending"
                context.metadata.commit.sha = None
                context.metadata.commit.review_sha = None
                context.metadata.errors.append(TaskErrorInfo(code="human-verification-finalize-failed", message=str(exc)))
                return self.transitions.move(context, TaskState.TODOS, by=by, note=f"human verification finalize failed: {exc}")
            assert done_context is not None
            self._cleanup_done_runtime(done_context)
            return done_context

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _integration_manager(self, operation_config: AppConfig | None) -> IntegrationManager:
        if operation_config is None:
            return self.integration_manager
        return IntegrationManager(operation_config)

    def _invoke_run_verification_apply(
        self,
        context: TaskContext,
        *,
        git_token: str | None,
        git_token_username: str | None,
        operation_config: AppConfig | None,
    ) -> None:
        signature = inspect.signature(self._run_verification_apply)
        if "operation_config" not in signature.parameters:
            self._run_verification_apply(context)
            return
        self._run_verification_apply(
            context,
            git_token=git_token,
            git_token_username=git_token_username,
            operation_config=operation_config,
        )

    def _build_reviewer_qa_rerequest_note(self, context: TaskContext) -> str:
        latest_exchange = self._latest_reviewer_qa_exchange(context)
        if latest_exchange is None:
            return ""
        question, answer = latest_exchange
        sections = [
            "## Re-request Note",
            "",
            "Generated from the latest reviewer Q&A exchange.",
            "",
            "### Reviewer Question",
            question or "No reviewer question recorded.",
            "",
            "### Reviewer Answer",
            answer,
            "",
            "### Requested Follow-up",
            "Please address the reviewer feedback above in the next implementation pass.",
        ]
        return "\n".join(sections).strip()

    def _latest_reviewer_qa_exchange(self, context: TaskContext) -> tuple[str, str] | None:
        artifact_path = self._latest_reviewer_qa_path(context)
        if artifact_path is None or not artifact_path.exists() or not artifact_path.is_file():
            return None
        entries = self._parse_reviewer_qa_entries(artifact_path.read_text())
        for index in range(len(entries) - 1, -1, -1):
            role, text = entries[index]
            if role != "answer" or not text:
                continue
            question = ""
            if index > 0 and entries[index - 1][0] == "question":
                question = entries[index - 1][1]
            return question, text
        return None

    def _latest_reviewer_qa_path(self, context: TaskContext) -> Path | None:
        reviewer_qa_files = sorted(context.task_dir.glob("REVIEWER-QA-*.md"))
        if reviewer_qa_files:
            return reviewer_qa_files[-1]
        if context.metadata.review.qa_path:
            candidate = context.task_dir / context.metadata.review.qa_path
            return candidate
        return None

    def _parse_reviewer_qa_entries(self, markdown: str) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        role: str | None = None
        lines: list[str] = []
        heading_pattern = re.compile(r"^##\s+(Question|Answer)\b.*$", re.IGNORECASE)
        for raw_line in markdown.replace("\r\n", "\n").split("\n"):
            match = heading_pattern.match(raw_line.strip())
            if match:
                if role is not None:
                    text = self._normalize_reviewer_qa_entry_text(lines)
                    if text:
                        entries.append((role, text))
                role = "question" if match.group(1).lower() == "question" else "answer"
                lines = []
                continue
            if role is not None:
                lines.append(raw_line)
        if role is not None:
            text = self._normalize_reviewer_qa_entry_text(lines)
            if text:
                entries.append((role, text))
        return entries

    def _normalize_reviewer_qa_entry_text(self, lines: list[str]) -> str:
        trimmed = list(lines)
        while trimmed and not trimmed[0].strip():
            trimmed.pop(0)
        while trimmed and trimmed[0].lstrip().startswith("- "):
            trimmed.pop(0)
        while trimmed and not trimmed[0].strip():
            trimmed.pop(0)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        return "\n".join(trimmed).strip()

    def _ensure_human_verification_note(self, task_dir: Path, metadata, *, verdict: str) -> None:
        expected_note_path = f"HUMAN-VERIFY-{metadata.cycle:03d}.md"
        expected_comments_path = f"HUMAN-VERIFY-{metadata.cycle:03d}.comments.json"
        if metadata.human_verification.note_path != expected_note_path:
            metadata.human_verification.note_path = expected_note_path
            metadata.human_verification.note_markdown = ""
        if metadata.human_verification.comments_path != expected_comments_path:
            metadata.human_verification.comments_path = expected_comments_path
        if metadata.human_verification.viewed_cycle != metadata.cycle:
            metadata.human_verification.viewed_cycle = metadata.cycle
            metadata.human_verification.viewed_files = {}
        self._save_comments_artifact(task_dir, metadata, self._load_comments_artifact(task_dir, metadata))
        self._write_human_verification_artifact(task_dir, metadata, verdict=verdict)

    def _has_current_human_review_feedback(self, task_dir: Path, metadata, *, note: str = "") -> bool:
        if not metadata.integration.applied:
            return True
        if note.strip() or metadata.human_verification.note_markdown.strip():
            return True
        return bool(self._load_comments_artifact(task_dir, metadata).comments)

    def _write_human_verification_artifact(self, task_dir: Path, metadata, *, verdict: str) -> None:
        note_path = metadata.human_verification.note_path or f"HUMAN-VERIFY-{metadata.cycle:03d}.md"
        metadata.human_verification.note_path = note_path
        if not metadata.human_verification.comments_path:
            metadata.human_verification.comments_path = self._default_comments_path(metadata)
        comments = self._load_comments_artifact(task_dir, metadata).comments
        unresolved_comments = [comment for comment in comments if not comment.resolved]
        artifact_path = task_dir / note_path
        sections = ["# Human Verification", "", f"Verdict: {verdict}", ""]
        sections.extend(["## Notes", metadata.human_verification.note_markdown.strip() or "No notes yet.", ""])
        sections.append("## Line Comments")
        if unresolved_comments:
            sections.append("")
            for comment in unresolved_comments:
                location = f"{comment.anchor.path}:{comment.anchor.line_number} ({comment.anchor.side})"
                sections.extend([f"### {location}", comment.body_markdown.strip(), ""])
        else:
            sections.extend(["", "No unresolved comments.", ""])
        artifact_path.write_text("\n".join(sections))

    def _human_review_summary(self, metadata) -> str:
        note = metadata.human_verification.note_markdown.strip()
        if note:
            return note.splitlines()[0].strip()
        return ""

    def _capture_review_branch_to_workspace(self, metadata) -> None:
        workspace_repo = metadata.implementation.workspace
        if not workspace_repo:
            return
        target_repo_root = self._verification_repo_root(metadata)
        stage_target = subprocess.run(["git", "-C", str(target_repo_root), "add", "-A"], capture_output=True, text=True, check=False)
        if stage_target.returncode != 0:
            raise IntegrationError(stage_target.stderr.strip() or "failed to stage reviewed code")
        patch = subprocess.run(
            ["git", "-C", str(target_repo_root), "diff", "--cached", "--binary", metadata.target.base_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(["git", "-C", str(target_repo_root), "reset"], capture_output=True, text=True, check=False)
        if patch.returncode != 0:
            raise IntegrationError(patch.stderr.strip() or "failed to capture review branch state")
        workspace_path = Path(workspace_repo).expanduser().resolve()
        base_ref = self._resolve_workspace_base_ref(workspace_path, metadata.target.base_branch)
        reset_branch = subprocess.run(
            ["git", "-C", str(workspace_path), "checkout", "-B", f"task/{metadata.task_id.lower()}", base_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if reset_branch.returncode != 0:
            raise IntegrationError(reset_branch.stderr.strip() or "failed to reset workspace branch")
        reset = subprocess.run(["git", "-C", str(workspace_path), "reset", "--hard"], capture_output=True, text=True, check=False)
        clean = subprocess.run(["git", "-C", str(workspace_path), "clean", "-fd"], capture_output=True, text=True, check=False)
        if reset.returncode != 0 or clean.returncode != 0:
            raise IntegrationError((reset.stderr or clean.stderr).strip() or "failed to reset workspace")
        patch_path = (workspace_path.parent / ".human-review-reject.patch").resolve()
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(patch.stdout)
        try:
            if patch.stdout.strip():
                apply_result = subprocess.run(
                    ["git", "-C", str(workspace_path), "apply", "--3way", str(patch_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if apply_result.returncode != 0:
                    subprocess.run(["git", "-C", str(workspace_path), "reset", "--hard"], capture_output=True, text=True, check=False)
                    subprocess.run(["git", "-C", str(workspace_path), "clean", "-fd"], capture_output=True, text=True, check=False)
                    raise IntegrationError(apply_result.stderr.strip() or "failed to apply reviewed code back into workspace")
        finally:
            patch_path.unlink(missing_ok=True)

    def _verification_repo_root(self, metadata) -> Path:
        if metadata.integration.verification_repo_root:
            repo_root = Path(metadata.integration.verification_repo_root).expanduser().resolve()
            if not repo_root.exists():
                raise IntegrationError("verification repository is missing")
            return repo_root
        try:
            return resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc

    def _resolve_workspace_base_ref(self, workspace_path: Path, base_branch: str) -> str:
        for candidate in (base_branch, f"origin/{base_branch}"):
            probe = subprocess.run(
                ["git", "-C", str(workspace_path), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode == 0:
                return candidate
        raise IntegrationError(f"base ref '{base_branch}' does not exist in workspace")

    def _sync_task_documents_to_target_repo(self, task_dir: Path, metadata) -> str:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        verification_repo_root = self._verification_repo_root(metadata)
        self._stage_verification_repo_for_summary(verification_repo_root)
        try:
            task_service = TaskService(
                self.scanner,
                self.config.runs_dir,
                self.config.kanban_root,
                self.config.archive_runs_dir,
                metadata_store=self.metadata_store,
                transitions=self.transitions,
                locks=self.locks,
            )
            target_summary_path = task_service.target_repo_summary_path(metadata)
            target_legacy_summary_path = task_service.legacy_target_repo_summary_path(metadata)
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        summary_path = verification_repo_root / target_summary_path.relative_to(target_repo_root)
        legacy_summary_path = verification_repo_root / target_legacy_summary_path.relative_to(target_repo_root)
        docs_root = summary_path.parent
        docs_root.mkdir(parents=True, exist_ok=True)
        legacy_task_dir = docs_root / metadata.task_id
        if legacy_task_dir.exists():
            shutil.rmtree(legacy_task_dir, ignore_errors=True)
        filename, content = task_service.build_target_repo_summary_artifact(TaskContext(metadata=metadata, task_dir=task_dir, state=metadata.state))
        if summary_path.name != filename:
            summary_path = docs_root / filename
        summary_path.write_bytes(content)
        if legacy_summary_path != summary_path and legacy_summary_path.exists() and legacy_summary_path.is_file():
            legacy_summary_path.unlink(missing_ok=True)
        return content.decode("utf-8")

    def _stage_verification_repo_for_summary(self, repo_root: Path) -> None:
        staged = subprocess.run(["git", "-C", str(repo_root), "add", "-A"], capture_output=True, text=True, check=False)
        if staged.returncode != 0:
            raise IntegrationError(staged.stderr.strip() or "failed to stage verification repository")

    def _default_comments_path(self, metadata) -> str:
        note_path = metadata.human_verification.note_path or f"HUMAN-VERIFY-{metadata.cycle:03d}.md"
        if note_path.endswith(".md"):
            return f"{note_path[:-3]}.comments.json"
        return f"{note_path}.comments.json"

    def _comments_artifact_path(self, task_dir: Path, metadata) -> Path:
        comments_path = metadata.human_verification.comments_path or self._default_comments_path(metadata)
        metadata.human_verification.comments_path = comments_path
        resolved_task_dir = task_dir.resolve()
        resolved = (resolved_task_dir / comments_path).resolve()
        if resolved.parent != resolved_task_dir:
            raise TransitionError("human verification comments must stay inside the task directory")
        return resolved

    def _load_comments_artifact(self, task_dir: Path, metadata) -> HumanLineCommentsArtifact:
        path = self._comments_artifact_path(task_dir, metadata)
        if not path.exists():
            return HumanLineCommentsArtifact()
        return HumanLineCommentsArtifact.model_validate_json(path.read_text())

    def _load_all_comments(self, task_dir: Path) -> list[HumanLineComment]:
        comments: list[HumanLineComment] = []
        for path in sorted(task_dir.glob("HUMAN-VERIFY-*.comments.json")):
            comments.extend(HumanLineCommentsArtifact.model_validate_json(path.read_text()).comments)
        return comments

    def _save_comments_artifact(self, task_dir: Path, metadata, artifact: HumanLineCommentsArtifact) -> None:
        path = self._comments_artifact_path(task_dir, metadata)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n")
        os.replace(tmp_path, path)

    def _generate_branch_summary(self, context: TaskContext) -> str:
        fallback = self.commit_manager.sanitize_branch_summary(None, fallback_title=context.metadata.title)
        run_config = self.config.with_runtime_pin(context.metadata.runtime_pin)
        adapter = self.branch_summary_adapter
        if adapter is None:
            return fallback
        availability_error = adapter.availability_error(config=run_config, backend=run_config.backend_for_role("planner"))
        if availability_error is not None:
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
            result = adapter.run(
                agent=run_config.role_agent("planner"),
                prompt=prompt,
                cwd=context.task_dir,
                run_log_path=run_log_path,
                config=run_config,
            )
        except AdapterRunError:
            return fallback
        if not result.ok:
            return fallback
        return self.commit_manager.sanitize_branch_summary(result.assistant_text, fallback_title=context.metadata.title)

    def _cleanup_done_runtime(self, context: TaskContext) -> None:
        if context.state != TaskState.DONE:
            raise TransitionError("done runtime cleanup requires a done task")
        metadata = context.metadata
        task_id = metadata.task_id
        workspace_path = metadata.implementation.workspace
        live_runs_dir = (self.config.runs_dir / task_id).resolve()
        archive_runs_dir = (self.config.archive_runs_dir / task_id).resolve()
        archived_patch_path = self._archived_patch_path(metadata, live_runs_dir, archive_runs_dir)
        self._archive_runs_dir(live_runs_dir, archive_runs_dir)
        if archived_patch_path is not None:
            metadata.integration.patch_path = str(archived_patch_path)
        metadata.implementation.workspace = None
        self.metadata_store.save(context.task_dir, metadata)
        self._delete_workspace_root(metadata, workspace_path)

    def _run_verification_apply(
        self,
        context: TaskContext,
        *,
        git_token: str | None = None,
        git_token_username: str | None = None,
        operation_config: AppConfig | None = None,
    ) -> None:
        workspace_repo = context.metadata.implementation.workspace
        if workspace_repo is None:
            raise IntegrationError("workspace path missing")
        context.metadata.integration.final_branch = None
        context.metadata.commit.review_sha = None
        self._ensure_human_verification_note(context.task_dir, context.metadata, verdict="IN_PROGRESS")
        if not context.metadata.integration.final_branch_summary:
            context.metadata.integration.final_branch_summary = self._generate_branch_summary(context)
        try:
            integration_manager = self._integration_manager(operation_config)
            integration_manager.apply_workspace(
                context.metadata,
                Path(workspace_repo),
                git_token=git_token,
                git_token_username=git_token_username,
            )
            self.commit_manager.prepare_commit_message(context.task_dir, context.metadata)
            review_sha = self.commit_manager.commit_task(context.task_dir, context.metadata)
            integration_manager.push_review_branch(
                context.metadata,
                git_token=git_token,
                git_token_username=git_token_username,
            )
            context.metadata.commit.status = "review-committed"
            context.metadata.commit.review_sha = review_sha
            context.metadata.commit.sha = review_sha
            self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="IN_PROGRESS")
        except IntegrationConflictError as exc:
            self._integration_manager(operation_config).rollback_workspace(
                context.metadata,
                git_token=git_token,
                git_token_username=git_token_username,
            )
            self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="CONFLICT")
            context.metadata.errors.append(TaskErrorInfo(code="integration-conflict", message=str(exc)))
            context.metadata.commit.status = "pending"
            context.metadata.commit.sha = None
            context.metadata.commit.review_sha = None
            clear_retry_gate(context.metadata)
        except Exception as exc:
            try:
                self._integration_manager(operation_config).rollback_workspace(
                    context.metadata,
                    git_token=git_token,
                    git_token_username=git_token_username,
                )
            except Exception as cleanup_exc:
                raise IntegrationError(f"{exc}; cleanup failed: {cleanup_exc}") from exc
            context.metadata.commit.status = "pending"
            context.metadata.commit.sha = None
            context.metadata.commit.review_sha = None
            raise

    def _reset_implementation_context(self, metadata) -> None:
        metadata.implementation.target_repo_baseline = None
        metadata.implementation.last_result = None
        metadata.implementation.resolved_model = None
        metadata.implementation.last_run_tokens = 0

    def _target_repo_state_drift_note(self, metadata) -> str | None:
        baseline = metadata.implementation.target_repo_baseline
        if baseline is None:
            return None
        current = snapshot_target_repo_state(Path(metadata.target.repo_root), base_branch=metadata.target.base_branch)
        head_drift = describe_target_repo_head_drift(
            expected_branch=baseline.current_branch,
            expected_head_sha=baseline.head_sha,
            current_branch=current.current_branch,
            current_head_sha=current.head_sha,
        )
        if head_drift is not None:
            return head_drift
        return describe_target_repo_dirty_drift(
            expected_dirty=baseline.dirty,
            current_branch=current.current_branch,
            current_dirty=current.dirty,
            current_status_short=current.status_short,
        )

    def _archived_patch_path(self, metadata, live_runs_dir: Path, archive_runs_dir: Path) -> Path | None:
        if not metadata.integration.patch_path:
            return None
        patch_path = Path(metadata.integration.patch_path).expanduser().resolve()
        try:
            relative_path = patch_path.relative_to(live_runs_dir)
        except ValueError:
            try:
                patch_path.relative_to(archive_runs_dir)
            except ValueError as exc:
                raise TransitionError("done runtime cleanup is blocked because patch path is outside the managed runs roots") from exc
            return patch_path
        return archive_runs_dir / relative_path

    def _archive_runs_dir(self, live_runs_dir: Path, archive_runs_dir: Path) -> None:
        if not live_runs_dir.exists():
            return
        archive_runs_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(archive_runs_dir, ignore_errors=True)
        shutil.move(str(live_runs_dir), str(archive_runs_dir))

    def _delete_workspace_root(self, metadata, workspace_path: str | None) -> None:
        expected_root = (self.config.workspace.root or (self.config.kanban_root / "_runtime/workspaces")) / metadata.task_id
        if workspace_path is None:
            shutil.rmtree(expected_root, ignore_errors=True)
            return
        resolved_workspace = Path(workspace_path).expanduser().resolve()
        managed_root = expected_root.resolve()
        try:
            resolved_workspace.relative_to(managed_root)
        except ValueError as exc:
            raise TransitionError("done runtime cleanup is blocked because workspace path is outside the managed workspace root") from exc
        shutil.rmtree(managed_root, ignore_errors=True)
