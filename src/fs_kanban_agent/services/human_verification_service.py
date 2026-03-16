from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import uuid

from ..commit_manager import CommitManager
from ..enums import TaskState
from ..exceptions import IntegrationConflictError, IntegrationError, TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..locks import TaskLockManager
from ..metadata_store import MetadataStore
from ..opencode_adapter import OpenCodeAdapter, AdapterRunError
from ..models import HumanLineComment, HumanLineCommentAnchor, HumanLineCommentsArtifact, TaskContext, TaskErrorInfo, utc_now
from ..scanner import KanbanScanner
from ..target_repo_guard import resolve_safe_target_repo_root
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
                self._ensure_human_verification_note(context.task_dir, context.metadata, verdict="IN_PROGRESS")
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
                context.metadata.human_verification.note_markdown = str(exc)
                self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="CONFLICT")
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

    def save_note(self, task_id: str, *, by: str, content: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification note editing is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-note"):
            context.metadata.human_verification.note_markdown = content.rstrip()
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

    def reject(self, task_id: str, *, by: str, note: str = "") -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification rejection is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-reject"):
            if not self._has_current_human_review_feedback(context.task_dir, context.metadata, note=note):
                raise TransitionError("request changes is only available after adding a review note or line comment")
            if note.strip():
                context.metadata.human_verification.note_markdown = note.strip()
            self._capture_review_branch_to_workspace(context.metadata)
            self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="REQUEST_CHANGES")
            self.integration_manager.rollback_workspace(context.metadata)
            context.metadata.commit.status = "pending"
            context.metadata.commit.sha = None
            context.metadata.commit.review_sha = None
            summary = self._human_review_summary(context.metadata)
            context.metadata.errors.append(TaskErrorInfo(code="human-verification-rejected", message=summary or "human verification requested changes"))
            self.metadata_store.save(context.task_dir, context.metadata)
            return self.transitions.move(context, TaskState.TODOS, by=by, note=summary or "human verification requested changes")

    def approve(self, task_id: str, *, by: str, completion_mode: str = "new-branch") -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification approval is only allowed from human-verifying")
        if completion_mode not in {"new-branch", "target-branch"}:
            raise TransitionError(f"unsupported completion mode: {completion_mode}")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-approve"):
            done_context: TaskContext | None = None
            try:
                if context.metadata.human_verification.note_markdown.strip():
                    raise TransitionError("approval is blocked until the review note is cleared")
                unresolved_comments = [comment for comment in self._load_comments_artifact(context.task_dir, context.metadata).comments if not comment.resolved]
                if unresolved_comments:
                    count = len(unresolved_comments)
                    raise TransitionError(f"approval is blocked until all inline comments are removed ({count} remaining)")
                if context.metadata.commit.review_sha is None:
                    context.metadata.commit.review_sha = context.metadata.commit.sha
                self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="APPROVED")
                self._sync_task_documents_to_target_repo(context.task_dir, context.metadata)
                self.commit_manager.prepare_commit_message(context.task_dir, context.metadata)
                sha = self.commit_manager.finalize_review_branch(
                    context.task_dir,
                    context.metadata,
                    completion_mode=completion_mode,
                )
                self.integration_manager.finalize_workspace(context.metadata)
                context.metadata.commit.status = "committed"
                context.metadata.commit.sha = sha
                completion_label = "new branch" if completion_mode == "new-branch" else "target branch"
                done_context = self.transitions.move(context, TaskState.DONE, by=by, note=f"human verification approved ({completion_label})")
            except TransitionError:
                raise
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
            assert done_context is not None
            self._cleanup_done_runtime(done_context)
            return done_context

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _ensure_human_verification_note(self, task_dir: Path, metadata, *, verdict: str) -> None:
        expected_note_path = f"HUMAN-VERIFY-{metadata.cycle:03d}.md"
        expected_comments_path = f"HUMAN-VERIFY-{metadata.cycle:03d}.comments.json"
        if metadata.human_verification.note_path != expected_note_path:
            metadata.human_verification.note_path = expected_note_path
            metadata.human_verification.note_markdown = ""
        if metadata.human_verification.comments_path != expected_comments_path:
            metadata.human_verification.comments_path = expected_comments_path
        self._save_comments_artifact(task_dir, metadata, self._load_comments_artifact(task_dir, metadata))
        self._write_human_verification_artifact(task_dir, metadata, verdict=verdict)

    def _has_current_human_review_feedback(self, task_dir: Path, metadata, *, note: str = "") -> bool:
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
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
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
                    raise IntegrationError(apply_result.stderr.strip() or "failed to apply reviewed code back into workspace")
        finally:
            patch_path.unlink(missing_ok=True)

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

    def _sync_task_documents_to_target_repo(self, task_dir: Path, metadata) -> None:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        review_date = datetime.now(timezone.utc)
        docs_root = target_repo_root / "docs" / "kanban-agent" / f"{review_date.year:04d}" / f"{review_date.month:02d}" / f"{review_date.day:02d}" / metadata.task_id
        shutil.rmtree(docs_root, ignore_errors=True)
        docs_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(task_dir.glob("*.md")):
            shutil.copy2(path, docs_root / path.name)
        comments_path = self._comments_artifact_path(task_dir, metadata)
        if comments_path.exists():
            shutil.copy2(comments_path, docs_root / comments_path.name)

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
