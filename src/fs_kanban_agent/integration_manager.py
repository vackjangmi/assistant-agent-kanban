from __future__ import annotations

import subprocess
from pathlib import Path

from .config import AppConfig
from .exceptions import IntegrationError
from .models import TaskMetadata, utc_now


class IntegrationManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def apply_workspace(self, metadata: TaskMetadata, workspace_repo: Path) -> Path:
        target_repo_root = Path(metadata.target.repo_root)
        patch_path = self.config.runs_dir / metadata.task_id / f"review-{metadata.review.iteration:03d}.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        head = subprocess.run(
            ["git", "-C", str(target_repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        metadata.integration.base_commit = head.stdout.strip() or None
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
        if not diff.stdout.strip():
            metadata.integration.patch_path = str(patch_path)
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
        apply_result = subprocess.run(
            ["git", "-C", str(target_repo_root), "apply", "--3way", "--index", str(patch_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if apply_result.returncode != 0:
            raise IntegrationError(apply_result.stderr.strip() or "failed to apply patch")
        metadata.integration.patch_path = str(patch_path)
        metadata.integration.applied = True
        metadata.integration.applied_at = utc_now()
        return patch_path

    def rollback_workspace(self, metadata: TaskMetadata) -> None:
        target_repo_root = Path(metadata.target.repo_root)
        patch_path = Path(metadata.integration.patch_path) if metadata.integration.patch_path else None
        if patch_path is None or not patch_path.exists():
            metadata.integration.applied = False
            metadata.integration.applied_at = None
            return
        if patch_path.read_text().strip():
            rollback = subprocess.run(
                ["git", "-C", str(target_repo_root), "apply", "-R", "--index", str(patch_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if rollback.returncode != 0:
                raise IntegrationError(rollback.stderr.strip() or "failed to rollback patch")
        metadata.integration.applied = False
        metadata.integration.applied_at = None
