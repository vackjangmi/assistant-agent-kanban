from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
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

    def reject(self, task_id: str, *, by: str, note: str = "") -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification rejection is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-reject"):
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

    def approve(self, task_id: str, *, by: str) -> TaskContext:
        context = self._find_task(task_id)
        if context.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("human verification approval is only allowed from human-verifying")
        with self.locks.acquire(context.task_dir, context.metadata, owner=by, run_id="manual-human-approve"):
            try:
                if context.metadata.commit.review_sha is None:
                    context.metadata.commit.review_sha = context.metadata.commit.sha
                self._write_human_verification_artifact(context.task_dir, context.metadata, verdict="APPROVED")
                self._sync_task_documents_to_target_repo(context.task_dir, context.metadata)
                self.commit_manager.prepare_commit_message(context.task_dir, context.metadata)
                sha = self.commit_manager.finalize_review_branch(context.task_dir, context.metadata)
                self.integration_manager.finalize_workspace(context.metadata)
                context.metadata.commit.status = "committed"
                context.metadata.commit.sha = sha
                return self.transitions.move(context, TaskState.DONE, by=by, note="human verification approved")
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

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _ensure_human_verification_note(self, task_dir: Path, metadata, *, verdict: str) -> None:
        if not metadata.human_verification.note_path:
            metadata.human_verification.note_path = f"HUMAN-VERIFY-{metadata.cycle:03d}.md"
        self._write_human_verification_artifact(task_dir, metadata, verdict=verdict)

    def _write_human_verification_artifact(self, task_dir: Path, metadata, *, verdict: str) -> None:
        note_path = metadata.human_verification.note_path or f"HUMAN-VERIFY-{metadata.cycle:03d}.md"
        metadata.human_verification.note_path = note_path
        artifact_path = task_dir / note_path
        sections = ["# Human Verification", "", f"Verdict: {verdict}", ""]
        sections.extend(["## Notes", metadata.human_verification.note_markdown.strip() or "No notes yet.", ""])
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
        workspace_path = Path(workspace_repo)
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
        patch_path = workspace_path.parent / ".human-review-reject.patch"
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
        docs_root = target_repo_root / "docs" / "ai-kanban" / f"{review_date.year:04d}" / f"{review_date.month:02d}" / f"{review_date.day:02d}" / metadata.task_id
        shutil.rmtree(docs_root, ignore_errors=True)
        docs_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(task_dir.glob("*.md")):
            shutil.copy2(path, docs_root / path.name)

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
