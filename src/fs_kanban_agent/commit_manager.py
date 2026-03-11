from __future__ import annotations

import subprocess
from pathlib import Path

from .exceptions import CommitError
from .models import TaskMetadata
from .target_repo_guard import resolve_safe_target_repo_root


class CommitManager:
    def build_commit_message(self, metadata: TaskMetadata) -> str:
        return f"feat: complete {metadata.slug} task"

    def commit_task(self, task_dir: Path, metadata: TaskMetadata) -> str:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise CommitError(str(exc)) from exc
        message = self.build_commit_message(metadata)
        commit_path = task_dir / "COMMIT.md"
        commit_path.write_text(message + "\n")
        metadata.commit.message_path = "COMMIT.md"
        result = subprocess.run(["git", "-C", str(target_repo_root), "status", "--short"], capture_output=True, text=True, check=False)
        if not result.stdout.strip():
            raise CommitError("no changes to commit")
        commit = subprocess.run(["git", "-C", str(target_repo_root), "commit", "-m", message], capture_output=True, text=True, check=False)
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or "git commit failed")
        sha = subprocess.run(["git", "-C", str(target_repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return sha.stdout.strip()
