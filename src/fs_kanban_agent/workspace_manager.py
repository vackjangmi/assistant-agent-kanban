from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import AppConfig
from .exceptions import WorkspaceSyncError
from .models import TaskMetadata


class WorkspaceManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def prepare(self, metadata: TaskMetadata) -> Path:
        workspace_root = self.config.workspace.root or (self.config.kanban_root / "_runtime/workspaces")
        workspace_dir = workspace_root / metadata.task_id
        repo_dir = workspace_dir / "repo"
        target_repo_root = Path(metadata.target.repo_root)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        if not repo_dir.exists():
            git_dir = target_repo_root / ".git"
            if git_dir.exists():
                self._clone_task_repo(target_repo_root, repo_dir, metadata)
            else:
                shutil.copytree(target_repo_root, repo_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "ai-kanban"))
        elif (target_repo_root / ".git").exists():
            self._refresh_git_workspace(repo_dir, target_repo_root, metadata)
        self._apply_overlays(repo_dir, target_repo_root)
        metadata.implementation.workspace = str(repo_dir)
        metadata.implementation.branch = f"task/{metadata.task_id.lower()}"
        return repo_dir

    def _clone_task_repo(self, target_repo_root: Path, repo_dir: Path, metadata: TaskMetadata) -> None:
        clone = subprocess.run(["git", "clone", str(target_repo_root), str(repo_dir)], capture_output=True, text=True, check=False)
        if clone.returncode != 0:
            raise WorkspaceSyncError(clone.stderr.strip() or "git clone failed")
        checkout = subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "-B", f"task/{metadata.task_id.lower()}", metadata.target.base_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if checkout.returncode != 0:
            raise WorkspaceSyncError(checkout.stderr.strip() or "git checkout failed")

    def _refresh_git_workspace(self, repo_dir: Path, target_repo_root: Path, metadata: TaskMetadata) -> None:
        workspace_dir = repo_dir.parent
        snapshot_dir = (workspace_dir / ".sync-snapshot").expanduser().resolve()
        candidate_dir = (workspace_dir / ".sync-candidate").expanduser().resolve()
        patch_path = (workspace_dir / ".sync.patch").expanduser().resolve()
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        shutil.rmtree(candidate_dir, ignore_errors=True)
        patch_path.unlink(missing_ok=True)
        try:
            shutil.copytree(repo_dir, snapshot_dir, symlinks=True)
            patch_text = self._workspace_patch(snapshot_dir, metadata)
            self._clone_task_repo(target_repo_root, candidate_dir, metadata)
            self._apply_overlays(candidate_dir, target_repo_root)
            if patch_text.strip():
                patch_path.write_text(patch_text)
                apply_result = subprocess.run(
                    ["git", "-C", str(candidate_dir), "apply", "--3way", "--index", str(patch_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if apply_result.returncode != 0:
                    raise WorkspaceSyncError(apply_result.stderr.strip() or "failed to replay workspace changes onto latest base")
            shutil.rmtree(repo_dir)
            shutil.move(str(candidate_dir), str(repo_dir))
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            shutil.rmtree(candidate_dir, ignore_errors=True)
            patch_path.unlink(missing_ok=True)

    def _workspace_patch(self, repo_dir: Path, metadata: TaskMetadata) -> str:
        local_commits = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-list", "--count", f"{metadata.target.base_branch}..HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if local_commits.returncode != 0:
            raise WorkspaceSyncError(local_commits.stderr.strip() or "failed to inspect workspace commits")
        if int(local_commits.stdout.strip() or "0") > 0:
            raise WorkspaceSyncError("workspace contains local commits and cannot be refreshed onto latest base")
        add_all = subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], capture_output=True, text=True, check=False)
        if add_all.returncode != 0:
            raise WorkspaceSyncError(add_all.stderr.strip() or "failed to stage workspace changes")
        diff = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "--cached", "--binary"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode != 0:
            raise WorkspaceSyncError(diff.stderr.strip() or "failed to snapshot workspace changes")
        return diff.stdout

    def _apply_overlays(self, repo_dir: Path, target_repo_root: Path) -> None:
        for relative in self.config.workspace.overlay_copy:
            source = target_repo_root / relative
            target = repo_dir / relative
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, target)
        for source_text in self.config.workspace.overlay_symlink:
            source = Path(source_text)
            target = repo_dir / source.name
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source)
