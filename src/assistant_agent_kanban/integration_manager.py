from __future__ import annotations

import os
import re
import subprocess
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from .config import AppConfig
from .exceptions import IntegrationConflictError, IntegrationError
from .models import TaskMetadata, utc_now
from .target_repo_guard import resolve_safe_target_repo_root


class IntegrationManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def apply_workspace(
        self,
        metadata: TaskMetadata,
        workspace_repo: Path,
        *,
        git_token: str | None = None,
        git_token_username: str | None = None,
    ) -> Path:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        patch_path = self._patch_path(metadata.task_id, metadata.cycle)
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        if self._is_git_repository(target_repo_root) and not self.config.review_branch_remote.enabled:
            head = subprocess.run(
                ["git", "-C", str(target_repo_root), "rev-parse", metadata.target.base_branch],
                capture_output=True,
                text=True,
                check=False,
            )
            if head.returncode != 0:
                raise IntegrationError(head.stderr.strip() or "failed to resolve target base branch")
            metadata.integration.base_commit = head.stdout.strip() or None
            return self._apply_workspace_patch_to_git_repo(
                metadata,
                workspace_repo,
                target_repo_root,
                patch_path,
                cleanup_verification_workspace=False,
            )
        verification_repo_root, initialized_empty_verification_repo = self._prepare_verification_repo(metadata)
        head = subprocess.run(
            ["git", "-C", str(verification_repo_root), "rev-parse", metadata.target.base_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode != 0:
            self._delete_verification_workspace(metadata)
            raise IntegrationError(head.stderr.strip() or "failed to resolve verification base branch")
        metadata.integration.base_commit = head.stdout.strip() or None
        if initialized_empty_verification_repo:
            return self._apply_workspace_snapshot(metadata, workspace_repo, verification_repo_root, patch_path)
        if self.config.review_branch_remote.enabled and metadata.integration.remote_review_branch:
            return self._apply_workspace_on_remote_review_branch(
                metadata,
                workspace_repo,
                verification_repo_root,
                patch_path,
                git_token=git_token,
                git_token_username=git_token_username,
            )
        return self._apply_workspace_patch_to_git_repo(
            metadata,
            workspace_repo,
            verification_repo_root,
            patch_path,
            cleanup_verification_workspace=True,
        )

    def _apply_workspace_patch_to_git_repo(
        self,
        metadata: TaskMetadata,
        workspace_repo: Path,
        repo_root: Path,
        patch_path: Path,
        *,
        cleanup_verification_workspace: bool,
    ) -> Path:
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
            if cleanup_verification_workspace:
                self._delete_verification_workspace(metadata)
            raise IntegrationError("workspace has no changes to apply")
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.stdout.strip():
            repo_label = "verification repo" if cleanup_verification_workspace else "target repo"
            raise IntegrationError(f"{repo_label} must be clean before apply")
        original_branch = self._current_branch(repo_root) or metadata.target.base_branch
        review_branch = metadata.integration.review_branch or self._review_branch_name(metadata)
        self._switch_to_review_branch(repo_root, metadata.target.base_branch, review_branch)
        metadata.integration.original_branch = original_branch
        metadata.integration.review_branch = review_branch
        apply_result = subprocess.run(
            ["git", "-C", str(repo_root), "apply", "--3way", "--index", str(patch_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if apply_result.returncode != 0:
            self._cleanup_review_branch(repo_root, metadata)
            if cleanup_verification_workspace:
                self._delete_verification_workspace(metadata)
            self._reset_transient_integration_state(metadata)
            raise IntegrationConflictError(apply_result.stderr.strip() or "failed to apply patch")
        metadata.integration.applied = True
        metadata.integration.applied_at = utc_now()
        return patch_path

    def rollback_workspace(
        self,
        metadata: TaskMetadata,
        *,
        git_token: str | None = None,
        git_token_username: str | None = None,
        delete_remote_review_branch: bool = True,
        preserve_remote_review_branch: bool = False,
    ) -> None:
        preserved_remote_name = metadata.integration.remote_name
        preserved_remote_review_branch = metadata.integration.remote_review_branch
        if not self._has_active_integration(metadata):
            self._reset_transient_integration_state(metadata)
            if preserve_remote_review_branch:
                metadata.integration.remote_name = preserved_remote_name
                metadata.integration.remote_review_branch = preserved_remote_review_branch
            return
        repo_root = self._active_integration_repo(metadata)
        if repo_root is None:
            self._reset_transient_integration_state(metadata)
            if preserve_remote_review_branch:
                metadata.integration.remote_name = preserved_remote_name
                metadata.integration.remote_review_branch = preserved_remote_review_branch
            return
        initialized_target_repo = metadata.integration.initialized_target_repo
        try:
            if self._is_git_repository(repo_root):
                if delete_remote_review_branch:
                    self.delete_remote_review_branch(
                        metadata,
                        target_repo_root=repo_root,
                        git_token=git_token,
                        git_token_username=git_token_username,
                    )
                self._cleanup_managed_branches(repo_root, metadata)
            self._delete_verification_workspace(metadata)
            self._reset_transient_integration_state(metadata)
            if preserve_remote_review_branch:
                metadata.integration.remote_name = preserved_remote_name
                metadata.integration.remote_review_branch = preserved_remote_review_branch
        finally:
            if initialized_target_repo:
                try:
                    target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
                except ValueError as exc:
                    raise IntegrationError(str(exc)) from exc
                self._remove_initialized_target_repo(target_repo_root)
                metadata.integration.initialized_target_repo = False

    def finalize_workspace(
        self,
        metadata: TaskMetadata,
        *,
        git_token: str | None = None,
        git_token_username: str | None = None,
    ) -> None:
        repo_root = self._active_integration_repo(metadata)
        if repo_root is None:
            self._reset_transient_review_state_after_finalize(metadata)
            return
        review_branch = metadata.integration.review_branch
        current_branch = self._current_branch(repo_root)
        if review_branch and current_branch == review_branch:
            raise IntegrationError("cannot finalize while still on review branch")
        self.delete_remote_review_branch(
            metadata,
            target_repo_root=repo_root,
            git_token=git_token,
            git_token_username=git_token_username,
        )
        if review_branch and not metadata.integration.verification_repo_root:
            self._delete_branch(repo_root, review_branch)
        self._delete_verification_workspace(metadata)
        self._reset_transient_review_state_after_finalize(metadata)

    def push_review_branch(
        self,
        metadata: TaskMetadata,
        *,
        git_token: str | None = None,
        git_token_username: str | None = None,
    ) -> None:
        if not self.config.review_branch_remote.enabled:
            return
        if not metadata.integration.review_branch:
            raise IntegrationError("review branch is missing")
        target_repo_root = self._active_integration_repo(metadata)
        if target_repo_root is None:
            raise IntegrationError("verification repository is missing")
        remote_name = self.config.review_branch_remote.remote_name
        review_branch = metadata.integration.review_branch
        repository = self._push_repository(target_repo_root, remote_name, git_token=git_token, git_token_username=git_token_username)
        env = self._git_auth_env(git_token)
        command = ["git", "-C", str(target_repo_root), "push", "-u", repository, f"{review_branch}:{review_branch}"]
        pushed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
        if pushed.returncode != 0:
            message = self._scrub_secret((pushed.stderr or pushed.stdout).strip() or "failed to push review branch", git_token)
            metadata.integration.remote_push_error = message
            if self.config.review_branch_remote.require_push_success:
                raise IntegrationError(message)
            return
        metadata.integration.remote_name = remote_name
        metadata.integration.remote_review_branch = review_branch
        metadata.integration.remote_pushed_at = utc_now()
        metadata.integration.remote_push_error = None

    def delete_remote_review_branch(
        self,
        metadata: TaskMetadata,
        *,
        target_repo_root: Path | None = None,
        git_token: str | None = None,
        git_token_username: str | None = None,
    ) -> None:
        if not self.config.review_branch_remote.enabled or not self.config.review_branch_remote.delete_on_cleanup:
            return
        review_branch = metadata.integration.remote_review_branch or metadata.integration.review_branch
        if not review_branch:
            return
        repo_root = target_repo_root
        if repo_root is None:
            repo_root = self._active_integration_repo(metadata)
            if repo_root is None:
                return
        remote_name = metadata.integration.remote_name or self.config.review_branch_remote.remote_name
        repository = self._push_repository(repo_root, remote_name, git_token=git_token, git_token_username=git_token_username)
        env = self._git_auth_env(git_token)
        deleted = subprocess.run(
            ["git", "-C", str(repo_root), "push", repository, "--delete", review_branch],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        output = f"{deleted.stderr}\n{deleted.stdout}".casefold()
        if deleted.returncode != 0 and "remote ref does not exist" not in output and "unable to delete" not in output:
            metadata.integration.remote_push_error = self._scrub_secret(
                (deleted.stderr or deleted.stdout).strip() or "failed to delete remote review branch",
                git_token,
            )
            return
        metadata.integration.remote_name = None
        metadata.integration.remote_review_branch = None
        metadata.integration.remote_pushed_at = None
        metadata.integration.remote_push_error = None

    def push_final_review_branch(
        self,
        metadata: TaskMetadata,
        *,
        final_branch: str,
        git_token: str | None = None,
        git_token_username: str | None = None,
    ) -> str:
        if not self.config.review_branch_remote.enabled:
            raise IntegrationError("remote review branch push is disabled")
        if not metadata.integration.review_branch:
            raise IntegrationError("review branch is missing")
        repo_root = self._active_integration_repo(metadata)
        if repo_root is None:
            raise IntegrationError("verification repository is missing")
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            raise IntegrationError(status.stderr.strip() or "failed to inspect verification repository status")
        if status.stdout.strip():
            raise IntegrationError("verification repository has uncommitted local changes; commit and push them before final approval")
        remote_name = metadata.integration.remote_name or self.config.review_branch_remote.remote_name
        repository = self._push_repository(repo_root, remote_name, git_token=git_token, git_token_username=git_token_username)
        env = self._git_auth_env(git_token)
        resolved_final_branch = self._available_remote_branch(repo_root, repository, final_branch, env=env)
        pushed = subprocess.run(
            ["git", "-C", str(repo_root), "push", "-u", repository, f"{metadata.integration.review_branch}:{resolved_final_branch}"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        output = f"{pushed.stderr}\n{pushed.stdout}".strip()
        if pushed.returncode != 0:
            message = self._scrub_secret(output or "failed to push final review branch", git_token)
            metadata.integration.remote_push_error = message
            raise IntegrationError(message)
        sha = self._resolve_ref(repo_root, metadata.integration.review_branch)
        if sha is None:
            raise IntegrationError("failed to resolve pushed review branch")
        metadata.integration.final_branch = resolved_final_branch
        metadata.integration.final_remote_name = remote_name
        metadata.integration.final_remote_branch = resolved_final_branch
        metadata.integration.final_remote_pushed_at = utc_now()
        metadata.integration.remote_merge_request_url = self._extract_merge_request_url(output)
        metadata.integration.remote_push_error = None
        return sha

    def sync_remote_review_branch(
        self,
        metadata: TaskMetadata,
        *,
        target_repo_root: Path | None = None,
        git_token: str | None = None,
        git_token_username: str | None = None,
    ) -> bool:
        if not self.config.review_branch_remote.enabled:
            return False
        remote_review_branch = metadata.integration.remote_review_branch
        if not remote_review_branch:
            return False
        repo_root = target_repo_root
        if repo_root is None:
            repo_root = self._active_integration_repo(metadata)
            if repo_root is None:
                raise IntegrationError("verification repository is missing")
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            raise IntegrationError(status.stderr.strip() or "failed to inspect verification repository status")
        if status.stdout.strip():
            raise IntegrationError("verification repository has uncommitted local changes; commit and push them before requesting changes")
        remote_name = metadata.integration.remote_name or self.config.review_branch_remote.remote_name
        repository = self._push_repository(repo_root, remote_name, git_token=git_token, git_token_username=git_token_username)
        remote_ref = f"refs/remotes/{remote_name}/{remote_review_branch}"
        env = self._git_auth_env(git_token)
        fetched = subprocess.run(
            ["git", "-C", str(repo_root), "fetch", repository, f"+{remote_review_branch}:{remote_ref}"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if fetched.returncode != 0:
            message = self._scrub_secret((fetched.stderr or fetched.stdout).strip() or "failed to fetch remote review branch", git_token)
            metadata.integration.remote_push_error = message
            raise IntegrationError(message)
        review_branch = metadata.integration.review_branch or remote_review_branch
        switched = subprocess.run(
            ["git", "-C", str(repo_root), "switch", "-C", review_branch, remote_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if switched.returncode != 0:
            raise IntegrationError(switched.stderr.strip() or "failed to update local review branch from remote")
        metadata.integration.review_branch = review_branch
        metadata.integration.remote_name = remote_name
        metadata.integration.remote_review_branch = remote_review_branch
        metadata.integration.remote_push_error = None
        return True

    def finalize_remote_workspace(
        self,
        metadata: TaskMetadata,
        *,
        git_token: str | None = None,
        git_token_username: str | None = None,
    ) -> None:
        repo_root = self._active_integration_repo(metadata)
        if repo_root is not None and self._is_git_repository(repo_root):
            self.delete_remote_review_branch(
                metadata,
                target_repo_root=repo_root,
                git_token=git_token,
                git_token_username=git_token_username,
            )
        self._delete_verification_workspace(metadata)
        self._reset_transient_review_state_after_finalize(metadata)

    def _reset_transient_integration_state(self, metadata: TaskMetadata) -> None:
        metadata.integration.applied = False
        metadata.integration.applied_at = None
        metadata.integration.verification_repo_root = None
        metadata.integration.original_branch = None
        metadata.integration.review_branch = None
        metadata.integration.remote_name = None
        metadata.integration.remote_review_branch = None
        metadata.integration.remote_pushed_at = None
        metadata.integration.remote_push_error = None
        metadata.integration.final_branch = None
        metadata.integration.final_remote_name = None
        metadata.integration.final_remote_branch = None
        metadata.integration.final_remote_pushed_at = None
        metadata.integration.remote_merge_request_url = None

    def _reset_transient_review_state_after_finalize(self, metadata: TaskMetadata) -> None:
        metadata.integration.applied = False
        metadata.integration.applied_at = None
        metadata.integration.verification_repo_root = None
        metadata.integration.original_branch = None
        metadata.integration.review_branch = None
        metadata.integration.remote_name = None
        metadata.integration.remote_review_branch = None
        metadata.integration.remote_pushed_at = None
        metadata.integration.remote_push_error = None
        metadata.integration.initialized_target_repo = False

    def _has_active_integration(self, metadata: TaskMetadata) -> bool:
        return bool(
            metadata.integration.verification_repo_root
            or metadata.integration.original_branch
            or metadata.integration.review_branch
            or metadata.integration.final_branch
            or metadata.integration.initialized_target_repo
        )

    def _prepare_verification_repo(self, metadata: TaskMetadata) -> tuple[Path, bool]:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc
        verification_root = self._verification_workspace_root(metadata)
        verification_repo_root = verification_root / "repo"
        shutil.rmtree(verification_root, ignore_errors=True)
        verification_root.mkdir(parents=True, exist_ok=True)
        try:
            if not self._is_git_repository(target_repo_root):
                self._initialize_empty_verification_repo(verification_repo_root, metadata.target.base_branch)
                metadata.integration.verification_repo_root = str(verification_repo_root)
                return verification_repo_root, True
            clone = subprocess.run(
                ["git", "clone", "--no-checkout", str(target_repo_root), str(verification_repo_root)],
                capture_output=True,
                text=True,
                check=False,
            )
            if clone.returncode != 0:
                raise IntegrationError(clone.stderr.strip() or "failed to create verification checkout")
            base_ref = f"origin/{metadata.target.base_branch}"
            base_commit = self._resolve_ref(verification_repo_root, base_ref) or self._resolve_ref(verification_repo_root, metadata.target.base_branch)
            if base_commit is None:
                raise IntegrationError(f"failed to resolve verification base branch '{metadata.target.base_branch}'")
            switch = subprocess.run(
                ["git", "-C", str(verification_repo_root), "switch", "-C", metadata.target.base_branch, base_commit],
                capture_output=True,
                text=True,
                check=False,
            )
            if switch.returncode != 0:
                raise IntegrationError(switch.stderr.strip() or "failed to checkout verification base branch")
            self._ensure_local_git_identity(verification_repo_root)
            self._copy_target_remotes(target_repo_root, verification_repo_root)
            metadata.integration.verification_repo_root = str(verification_repo_root)
            return verification_repo_root, False
        except Exception:
            shutil.rmtree(verification_root, ignore_errors=True)
            metadata.integration.verification_repo_root = None
            raise

    def _initialize_empty_verification_repo(self, repo_root: Path, base_branch: str) -> None:
        repo_root.mkdir(parents=True, exist_ok=True)
        init = subprocess.run(["git", "init", "-b", base_branch, str(repo_root)], capture_output=True, text=True, check=False)
        if init.returncode != 0:
            raise IntegrationError(init.stderr.strip() or "failed to initialize verification repository")
        self._ensure_local_git_identity(repo_root)
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "commit", "--allow-empty", "-m", "chore: initialize verification repository"],
            capture_output=True,
            text=True,
            check=False,
        )
        if commit.returncode != 0:
            raise IntegrationError(commit.stderr.strip() or "failed to create verification base commit")

    def _copy_target_remotes(self, target_repo_root: Path, verification_repo_root: Path) -> None:
        listed = subprocess.run(["git", "-C", str(target_repo_root), "remote"], capture_output=True, text=True, check=False)
        if listed.returncode != 0:
            raise IntegrationError(listed.stderr.strip() or "failed to inspect target repository remotes")
        target_remotes = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        existing = subprocess.run(["git", "-C", str(verification_repo_root), "remote"], capture_output=True, text=True, check=False)
        existing_remotes = {line.strip() for line in existing.stdout.splitlines() if line.strip()} if existing.returncode == 0 else set()
        for remote_name in sorted(existing_remotes - set(target_remotes)):
            subprocess.run(["git", "-C", str(verification_repo_root), "remote", "remove", remote_name], capture_output=True, text=True, check=False)
        for remote_name in target_remotes:
            url = subprocess.run(
                ["git", "-C", str(target_repo_root), "remote", "get-url", remote_name],
                capture_output=True,
                text=True,
                check=False,
            )
            if url.returncode != 0:
                raise IntegrationError(url.stderr.strip() or f"failed to resolve git remote '{remote_name}'")
            remote_url = url.stdout.strip()
            if remote_name in existing_remotes:
                configured = subprocess.run(
                    ["git", "-C", str(verification_repo_root), "remote", "set-url", remote_name, remote_url],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                configured = subprocess.run(
                    ["git", "-C", str(verification_repo_root), "remote", "add", remote_name, remote_url],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if configured.returncode != 0:
                raise IntegrationError(configured.stderr.strip() or f"failed to configure verification remote '{remote_name}'")

    def _resolve_ref(self, repo_root: Path, ref: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _active_integration_repo(self, metadata: TaskMetadata) -> Path | None:
        if metadata.integration.verification_repo_root:
            repo_root = Path(metadata.integration.verification_repo_root).expanduser().resolve()
            return repo_root if repo_root.exists() else None
        try:
            return resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise IntegrationError(str(exc)) from exc

    def _verification_workspace_root(self, metadata: TaskMetadata) -> Path:
        return (self.config.human_verifications_dir / metadata.task_id).expanduser().resolve()

    def _delete_verification_workspace(self, metadata: TaskMetadata) -> None:
        verification_repo_root = metadata.integration.verification_repo_root
        expected_root = self._verification_workspace_root(metadata)
        if verification_repo_root:
            resolved_repo = Path(verification_repo_root).expanduser().resolve()
            try:
                resolved_repo.relative_to(expected_root)
            except ValueError as exc:
                raise IntegrationError("verification repository path is outside the managed verification root") from exc
        shutil.rmtree(expected_root, ignore_errors=True)
        metadata.integration.verification_repo_root = None

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

    def _apply_workspace_on_remote_review_branch(
        self,
        metadata: TaskMetadata,
        workspace_repo: Path,
        verification_repo_root: Path,
        patch_path: Path,
        *,
        git_token: str | None,
        git_token_username: str | None,
    ) -> Path:
        status = subprocess.run(
            ["git", "-C", str(verification_repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            raise IntegrationError(status.stderr.strip() or "failed to inspect verification repository status")
        if status.stdout.strip():
            raise IntegrationError("verification repo must be clean before apply")
        remote_review_branch = metadata.integration.remote_review_branch
        if not remote_review_branch:
            raise IntegrationError("remote review branch is missing")
        remote_name = metadata.integration.remote_name or self.config.review_branch_remote.remote_name
        repository = self._push_repository(
            verification_repo_root,
            remote_name,
            git_token=git_token,
            git_token_username=git_token_username,
        )
        remote_ref = f"refs/remotes/{remote_name}/{remote_review_branch}"
        env = self._git_auth_env(git_token)
        fetched = subprocess.run(
            ["git", "-C", str(verification_repo_root), "fetch", repository, f"+{remote_review_branch}:{remote_ref}"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if fetched.returncode != 0:
            message = self._scrub_secret((fetched.stderr or fetched.stdout).strip() or "failed to fetch remote review branch", git_token)
            metadata.integration.remote_push_error = message
            raise IntegrationError(message)
        original_branch = self._current_branch(verification_repo_root) or metadata.target.base_branch
        review_branch = metadata.integration.review_branch or remote_review_branch
        switched = subprocess.run(
            ["git", "-C", str(verification_repo_root), "switch", "-C", review_branch, remote_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if switched.returncode != 0:
            raise IntegrationError(switched.stderr.strip() or "failed to continue remote review branch")
        metadata.integration.original_branch = original_branch
        metadata.integration.review_branch = review_branch
        metadata.integration.remote_name = remote_name
        metadata.integration.remote_review_branch = remote_review_branch
        self._copy_workspace_snapshot(workspace_repo, verification_repo_root)
        stage_all = subprocess.run(["git", "-C", str(verification_repo_root), "add", "-A"], capture_output=True, text=True, check=False)
        if stage_all.returncode != 0:
            raise IntegrationError(stage_all.stderr.strip() or "failed to stage workspace snapshot")
        incremental_status = self._cached_diff_status(verification_repo_root)
        diff = subprocess.run(
            ["git", "-C", str(verification_repo_root), "diff", "--cached", "--binary", metadata.target.base_branch],
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
        if incremental_status.returncode == 0:
            metadata.integration.applied = False
            metadata.integration.applied_at = None
            raise IntegrationError("workspace has no changes since the remote review branch")
        if incremental_status.returncode not in (0, 1):
            raise IntegrationError(incremental_status.stderr.strip() or "failed to inspect staged changes")
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

    def _cached_diff_status(self, repo_root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--cached", "--quiet"],
            capture_output=True,
            text=True,
            check=False,
        )

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

    def _push_repository(
        self,
        repo_root: Path,
        remote_name: str,
        *,
        git_token: str | None,
        git_token_username: str | None,
    ) -> str:
        if not git_token:
            return remote_name
        remote_url = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", remote_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if remote_url.returncode != 0:
            raise IntegrationError(remote_url.stderr.strip() or f"failed to resolve git remote '{remote_name}'")
        url = remote_url.stdout.strip()
        split = urlsplit(url)
        if split.scheme not in {"http", "https"} or not split.netloc:
            if self._looks_like_remote_ssh_url(url):
                raise IntegrationError(
                    f"Git token can only be used with an HTTP(S) remote URL for '{remote_name}'"
                )
            return remote_name
        username = quote((git_token_username or "x-access-token").strip() or "x-access-token", safe="")
        password = quote(git_token, safe="")
        netloc = split.netloc.split("@", 1)[-1]
        return urlunsplit((split.scheme, f"{username}:{password}@{netloc}", split.path, split.query, split.fragment))

    def _available_remote_branch(self, repo_root: Path, repository: str, preferred_branch: str, *, env: dict[str, str] | None) -> str:
        if not self._remote_branch_exists(repo_root, repository, preferred_branch, env=env):
            return preferred_branch
        fallback = f"{preferred_branch}-{preferred_branch.rsplit('/', 1)[-1]}"
        task_id = preferred_branch.split("/", 2)[1].split("-", 1)[0] if preferred_branch.startswith("feature/") else ""
        if task_id:
            fallback = f"{preferred_branch}-{task_id}"
        if not self._remote_branch_exists(repo_root, repository, fallback, env=env):
            return fallback
        raise IntegrationError("failed to create final remote branch: fallback branch already exists")

    def _remote_branch_exists(self, repo_root: Path, repository: str, branch: str, *, env: dict[str, str] | None) -> bool:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-remote", "--heads", repository, branch],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise IntegrationError(result.stderr.strip() or "failed to inspect remote branches")
        return bool(result.stdout.strip())

    def _extract_merge_request_url(self, output: str) -> str | None:
        urls = [url.rstrip(".,)") for url in re.findall(r"https?://[^\s<>()]+", output or "")]
        preferred_markers = ("merge_requests/new", "pull/new", "/compare/", "compare/")
        for url in urls:
            if any(marker in url for marker in preferred_markers):
                return url
        return urls[0] if urls else None

    def _git_auth_env(self, git_token: str | None) -> dict[str, str] | None:
        if not git_token:
            return None
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        askpass = shutil.which("false")
        if askpass:
            env["GIT_ASKPASS"] = askpass
        try:
            config_count = int(env.get("GIT_CONFIG_COUNT", "0"))
        except ValueError:
            config_count = 0
        env["GIT_CONFIG_COUNT"] = str(config_count + 1)
        env[f"GIT_CONFIG_KEY_{config_count}"] = "credential.helper"
        env[f"GIT_CONFIG_VALUE_{config_count}"] = ""
        return env

    def _looks_like_remote_ssh_url(self, value: str) -> bool:
        split = urlsplit(value)
        if split.scheme in {"ssh", "git+ssh"}:
            return True
        if "://" in value:
            return False
        if value.startswith(("/", "./", "../", "~")):
            return False
        return "@" in value and ":" in value.split("/", 1)[0]

    def _scrub_secret(self, value: str, secret: str | None) -> str:
        if not secret:
            return value
        return value.replace(secret, "[redacted]")

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

    def _abort_merge(self, repo_root: Path) -> None:
        git_dir = repo_root / ".git"
        if not (git_dir / "MERGE_HEAD").exists():
            return
        aborted = subprocess.run(
            ["git", "-C", str(repo_root), "merge", "--abort"],
            capture_output=True,
            text=True,
            check=False,
        )
        if aborted.returncode != 0:
            reset = subprocess.run(
                ["git", "-C", str(repo_root), "reset", "--merge"],
                capture_output=True,
                text=True,
                check=False,
            )
            if reset.returncode != 0:
                raise IntegrationError((aborted.stderr or reset.stderr).strip() or "failed to abort merge")

    def _has_worktree_changes(self, repo_root: Path) -> bool:
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            raise IntegrationError(status.stderr.strip() or "failed to inspect repository status")
        return bool(status.stdout.strip())

    def _cleanup_managed_branches(self, repo_root: Path, metadata: TaskMetadata) -> None:
        original_branch = metadata.integration.original_branch
        review_branch = metadata.integration.review_branch
        final_branch = metadata.integration.final_branch
        current_branch = self._current_branch(repo_root)
        self._abort_rebase(repo_root)
        self._abort_merge(repo_root)
        cleanup_branch = current_branch in {review_branch, final_branch} or (
            bool(current_branch)
            and current_branch in {original_branch, metadata.target.base_branch}
            and self._has_worktree_changes(repo_root)
        )
        if cleanup_branch:
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
        if final_branch and final_branch not in {original_branch, metadata.target.base_branch}:
            self._delete_branch(repo_root, final_branch)
        if review_branch:
            self._delete_branch(repo_root, review_branch)

    def _cleanup_review_branch(self, repo_root: Path, metadata: TaskMetadata) -> None:
        self._cleanup_managed_branches(repo_root, metadata)
