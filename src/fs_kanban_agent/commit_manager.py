from __future__ import annotations

import subprocess
from pathlib import Path

from .exceptions import CommitError
from .models import TaskMetadata
from .target_repo_guard import resolve_safe_target_repo_root


class CommitManager:
    def build_commit_message(self, metadata: TaskMetadata) -> str:
        title = metadata.title.strip()
        lowered = title[:1].lower() + title[1:] if title else metadata.slug
        return f"feat: {lowered}"

    def prepare_commit_message(self, task_dir: Path, metadata: TaskMetadata) -> str:
        message = self.build_commit_message(metadata)
        commit_path = task_dir / "COMMIT.md"
        commit_path.write_text(message + "\n")
        metadata.commit.message_path = "COMMIT.md"
        metadata.commit.prepared_message = message
        return message

    def commit_task(self, task_dir: Path, metadata: TaskMetadata) -> str:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise CommitError(str(exc)) from exc
        message = metadata.commit.prepared_message or self.prepare_commit_message(task_dir, metadata)
        review_branch = metadata.integration.review_branch
        if review_branch:
            current_branch = self._current_branch(target_repo_root)
            if current_branch != review_branch:
                switch = subprocess.run(
                    ["git", "-C", str(target_repo_root), "switch", review_branch],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if switch.returncode != 0:
                    raise CommitError(switch.stderr.strip() or "failed to switch to review branch")
        result = subprocess.run(["git", "-C", str(target_repo_root), "status", "--short"], capture_output=True, text=True, check=False)
        if not result.stdout.strip():
            raise CommitError("no changes to commit")
        commit = subprocess.run(["git", "-C", str(target_repo_root), "commit", "-m", message], capture_output=True, text=True, check=False)
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or "git commit failed")
        sha = subprocess.run(["git", "-C", str(target_repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return sha.stdout.strip()

    def _current_branch(self, repo_root: Path) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        branch = result.stdout.strip()
        return branch or None
