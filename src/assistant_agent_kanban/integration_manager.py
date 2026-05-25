from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path

from .config import AppConfig
from .exceptions import IntegrationConflictError, IntegrationError
from .models import TaskMetadata, utc_now
from .target_repo_guard import resolve_safe_target_repo_root


class IntegrationManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def apply_workspace(self, metadata: TaskMetadata, workspace_repo: Path) -> Path:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        initialized_target_repo = False
        if not self._is_git_repository(target_repo_root):
            self._initialize_empty_target_repo(target_repo_root, metadata.target.base_branch)
            metadata.integration.initialized_target_repo = True
            initialized_target_repo = True
        patch_path = self._patch_path(metadata.task_id, metadata.cycle)
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        head = subprocess.run(
            ["git", "-C", str(target_repo_root), "rev-parse", metadata.target.base_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode != 0:
            raise IntegrationError(head.stderr.strip() or "failed to resolve target base branch")
        metadata.integration.base_commit = head.stdout.strip() or None
        if initialized_target_repo:
            return self._apply_workspace_snapshot(metadata, workspace_repo, target_repo_root, patch_path)
        add_all = subprocess.run(
            ["git", "-C", str(workspace_repo), "add", "-A"],
            capture_output=True,
            text=True,
            check=False,
        )
        if add_all.returncode != 0:
            raise IntegrationError(add_all.stderr.strip() or "failed to stage workspace changes")
        diff = subprocess.run(
            ["git", "-C", str(workspace_repo), "diff", "--cached", "--binary"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode != 0:
            raise IntegrationError(diff.stderr.strip() or "failed to generate patch")
        patch_path.write_text(diff.stdout)
        metadata.integration.patch_path = str(patch_path)
        if not diff.stdout.strip():
            metadata.integration.applied = False
            metadata.integration.applied_at = None
            raise IntegrationError("workspace has no changes to apply")
        status = subprocess.run(
            ["git", "-C", str(target_repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.stdout.strip():
            raise IntegrationError("target repo must be clean before apply")
        original_branch = self._current_branch(target_repo_root) or metadata.target.base_branch
        review_branch = metadata.integration.review_branch or self._review_branch_name(metadata)
        self._switch_to_review_branch(target_repo_root, metadata.target.base_branch, review_branch)
        metadata.integration.original_branch = original_branch
        metadata.integration.review_branch = review_branch
        apply_result = subprocess.run(
            ["git", "-C", str(target_repo_root), "apply", "--3way", "--index", str(patch_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if apply_result.returncode != 0:
            self._cleanup_review_branch(target_repo_root, metadata)
            self._reset_transient_integration_state(metadata)
            raise IntegrationConflictError(apply_result.stderr.strip() or "failed to apply patch")
        metadata.integration.applied = True
        metadata.integration.applied_at = utc_now()
        return patch_path

    def rollback_workspace(self, metadata: TaskMetadata) -> None:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        initialized_target_repo = metadata.integration.initialized_target_repo
        try:
            if self._is_git_repository(target_repo_root):
                self._cleanup_managed_branches(target_repo_root, metadata)
            self._reset_transient_integration_state(metadata)
        finally:
            if initialized_target_repo:
                self._remove_initialized_target_repo(target_repo_root)
                metadata.integration.initialized_target_repo = False

    def finalize_workspace(self, metadata: TaskMetadata) -> None:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        review_branch = metadata.integration.review_branch
        current_branch = self._current_branch(target_repo_root)
        if review_branch and current_branch == review_branch:
            raise IntegrationError("cannot finalize while still on review branch")
        if review_branch:
            self._delete_branch(target_repo_root, review_branch)
        metadata.integration.applied = False
        metadata.integration.applied_at = None
        metadata.integration.original_branch = None
        metadata.integration.review_branch = None
        metadata.integration.initialized_target_repo = False

    def _reset_transient_integration_state(self, metadata: TaskMetadata) -> None:
        metadata.integration.applied = False
        metadata.integration.applied_at = None
        metadata.integration.original_branch = None
        metadata.integration.review_branch = None
        metadata.integration.final_branch = None

    def _apply_workspace_snapshot(self, metadata: TaskMetadata, workspace_repo: Path, target_repo_root: Path, patch_path: Path) -> Path:
        status = subprocess.run(
            ["git", "-C", str(target_repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            raise IntegrationError(status.stderr.strip() or "failed to inspect target repository status")
        if status.stdout.strip():
            raise IntegrationError("target repo must be clean before apply")
        original_branch = self._current_branch(target_repo_root) or metadata.target.base_branch
        review_branch = metadata.integration.review_branch or self._review_branch_name(metadata)
        self._switch_to_review_branch(target_repo_root, metadata.target.base_branch, review_branch)
        metadata.integration.original_branch = original_branch
        metadata.integration.review_branch = review_branch
        self._copy_workspace_snapshot(workspace_repo, target_repo_root)
        stage_all = subprocess.run(["git", "-C", str(target_repo_root), "add", "-A"], capture_output=True, text=True, check=False)
        if stage_all.returncode != 0:
            raise IntegrationError(stage_all.stderr.strip() or "failed to stage workspace snapshot")
        diff = subprocess.run(
            ["git", "-C", str(target_repo_root), "diff", "--cached", "--binary"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode != 0:
            raise IntegrationError(diff.stderr.strip() or "failed to generate workspace snapshot patch")
        patch_path.write_text(diff.stdout)
        metadata.integration.patch_path = str(patch_path)
        if not diff.stdout.strip():
            metadata.integration.applied = False
            metadata.integration.applied_at = None
            raise IntegrationError("workspace has no changes to apply")
        metadata.integration.applied = True
        metadata.integration.applied_at = utc_now()
        return patch_path

    def _patch_path(self, task_id: str, cycle: int) -> Path:
        return (self.config.runs_dir / task_id / f"review-{cycle:03d}.patch").expanduser().resolve()

    def _stored_patch_path(self, metadata: TaskMetadata) -> Path | None:
        if not metadata.integration.patch_path:
            return None
        patch_path = Path(metadata.integration.patch_path).expanduser()
        if patch_path.is_absolute():
            return patch_path
        return (self.config.kanban_root.expanduser().resolve().parent / patch_path).resolve()

    def _current_branch(self, repo_root: Path) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        branch = result.stdout.strip()
        return branch or None

    def _is_git_repository(self, repo_root: Path) -> bool:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _initialize_empty_target_repo(self, repo_root: Path, base_branch: str) -> None:
        if not repo_root.exists():
            repo_root.mkdir(parents=True)
        if not repo_root.is_dir():
            raise IntegrationError(f"target repo `{repo_root}` is not a directory")
        if any(repo_root.iterdir()):
            raise IntegrationError("non-git target repo must be empty before verification")
        initialized = False
        try:
            init = subprocess.run(["git", "init", "-b", base_branch, str(repo_root)], capture_output=True, text=True, check=False)
            if init.returncode != 0:
                raise IntegrationError(init.stderr.strip() or "failed to initialize target repository")
            initialized = True
            self._ensure_local_git_identity(repo_root)
            commit = subprocess.run(
                ["git", "-C", str(repo_root), "commit", "--allow-empty", "-m", "chore: initialize empty target repository"],
                capture_output=True,
                text=True,
                check=False,
            )
            if commit.returncode != 0:
                raise IntegrationError(commit.stderr.strip() or "failed to create initial target repository commit")
        except Exception:
            if initialized:
                self._remove_initialized_target_repo(repo_root)
            raise

    def _ensure_local_git_identity(self, repo_root: Path) -> None:
        defaults = {
            "user.name": "Assistant Agent Kanban",
            "user.email": "assistant-agent-kanban@localhost",
        }
        for key, value in defaults.items():
            existing = subprocess.run(
                ["git", "-C", str(repo_root), "config", "--get", key],
                capture_output=True,
                text=True,
                check=False,
            )
            if existing.returncode == 0 and existing.stdout.strip():
                continue
            configured = subprocess.run(
                ["git", "-C", str(repo_root), "config", key, value],
                capture_output=True,
                text=True,
                check=False,
            )
            if configured.returncode != 0:
                raise IntegrationError(configured.stderr.strip() or f"failed to configure {key}")

    def _copy_workspace_snapshot(self, workspace_repo: Path, target_repo_root: Path) -> None:
        self._clear_target_worktree(target_repo_root)
        if self._is_git_repository(workspace_repo):
            self._copy_git_workspace_snapshot(workspace_repo, target_repo_root)
            return
        shutil.copytree(
            workspace_repo,
            target_repo_root,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )

    def _copy_git_workspace_snapshot(self, workspace_repo: Path, target_repo_root: Path) -> None:
        add_all = subprocess.run(["git", "-C", str(workspace_repo), "add", "-A"], capture_output=True, text=True, check=False)
        if add_all.returncode != 0:
            raise IntegrationError(add_all.stderr.strip() or "failed to stage workspace changes")
        listed = subprocess.run(["git", "-C", str(workspace_repo), "ls-files", "-z"], capture_output=True, check=False)
        if listed.returncode != 0:
            stderr = listed.stderr.decode(errors="replace") if isinstance(listed.stderr, bytes) else str(listed.stderr)
            raise IntegrationError(stderr.strip() or "failed to list workspace snapshot files")
        for raw_relative in listed.stdout.split(b"\0"):
            if not raw_relative:
                continue
            relative = Path(os.fsdecode(raw_relative))
            source = workspace_repo / relative
            if not source.exists() and not source.is_symlink():
                continue
            target = target_repo_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            elif source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(source, target)

    def _clear_target_worktree(self, repo_root: Path) -> None:
        for child in repo_root.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    def _remove_initialized_target_repo(self, repo_root: Path) -> None:
        if not repo_root.exists():
            return
        for child in repo_root.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    def _review_branch_name(self, metadata: TaskMetadata) -> str:
        return f"review/{metadata.task_id.lower()}"

    def _switch_to_review_branch(self, repo_root: Path, original_branch: str, review_branch: str) -> None:
        checkout = subprocess.run(
            ["git", "-C", str(repo_root), "switch", "-C", review_branch, original_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if checkout.returncode != 0:
            raise IntegrationError(checkout.stderr.strip() or "failed to create review branch")

    def _restore_original_branch(self, repo_root: Path, original_branch: str) -> None:
        switched = subprocess.run(
            ["git", "-C", str(repo_root), "switch", original_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if switched.returncode != 0:
            raise IntegrationError(switched.stderr.strip() or "failed to restore original branch")

    def _delete_branch(self, repo_root: Path, review_branch: str) -> None:
        deleted = subprocess.run(
            ["git", "-C", str(repo_root), "branch", "-D", review_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if deleted.returncode != 0:
            raise IntegrationError(deleted.stderr.strip() or "failed to delete review branch")

    def _abort_rebase(self, repo_root: Path) -> None:
        git_dir = repo_root / ".git"
        if not (git_dir / "rebase-apply").exists() and not (git_dir / "rebase-merge").exists():
            return
        aborted = subprocess.run(
            ["git", "-C", str(repo_root), "rebase", "--abort"],
            capture_output=True,
            text=True,
            check=False,
        )
        if aborted.returncode != 0:
            raise IntegrationError(aborted.stderr.strip() or "failed to abort rebase")

    def _cleanup_managed_branches(self, repo_root: Path, metadata: TaskMetadata) -> None:
        original_branch = metadata.integration.original_branch
        review_branch = metadata.integration.review_branch
        final_branch = metadata.integration.final_branch
        current_branch = self._current_branch(repo_root)
        self._abort_rebase(repo_root)
        managed_branch = current_branch if current_branch in {review_branch, final_branch} else None
        if managed_branch:
            reset = subprocess.run(
                ["git", "-C", str(repo_root), "reset", "--hard", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if reset.returncode != 0:
                raise IntegrationError(reset.stderr.strip() or "failed to reset review branch")
            clean = subprocess.run(
                ["git", "-C", str(repo_root), "clean", "-fd"],
                capture_output=True,
                text=True,
                check=False,
            )
            if clean.returncode != 0:
                raise IntegrationError(clean.stderr.strip() or "failed to clean review branch")
        if original_branch:
            self._restore_original_branch(repo_root, original_branch)
        if final_branch:
            self._delete_branch(repo_root, final_branch)
        if review_branch:
            self._delete_branch(repo_root, review_branch)

    def _cleanup_review_branch(self, repo_root: Path, metadata: TaskMetadata) -> None:
        self._cleanup_managed_branches(repo_root, metadata)
