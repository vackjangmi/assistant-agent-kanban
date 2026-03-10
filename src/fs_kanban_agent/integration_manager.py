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
        patch_path = self.config.runs_dir / metadata.task_id / f"review-{metadata.review.iteration:03d}.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        diff = subprocess.run(
            ["git", "-C", str(workspace_repo), "diff", "--binary"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode != 0:
            raise IntegrationError(diff.stderr.strip() or "failed to generate patch")
        patch_path.write_text(diff.stdout)
        if not diff.stdout.strip():
            metadata.integration.patch_path = str(patch_path)
            metadata.integration.applied = True
            metadata.integration.applied_at = utc_now()
            return patch_path
        status = subprocess.run(
            ["git", "-C", str(self.config.repo_root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.stdout.strip():
            raise IntegrationError("integration repo must be clean before apply")
        apply_result = subprocess.run(
            ["git", "-C", str(self.config.repo_root), "apply", "--3way", "--index", str(patch_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if apply_result.returncode != 0:
            raise IntegrationError(apply_result.stderr.strip() or "failed to apply patch")
        metadata.integration.patch_path = str(patch_path)
        metadata.integration.applied = True
        metadata.integration.applied_at = utc_now()
        head = subprocess.run(
            ["git", "-C", str(self.config.repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        metadata.integration.base_commit = head.stdout.strip() or None
        return patch_path
