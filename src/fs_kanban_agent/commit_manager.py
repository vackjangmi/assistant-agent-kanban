from __future__ import annotations

import subprocess
from pathlib import Path

from .exceptions import CommitError
from .models import TaskMetadata
from .request_parser import extract_goal_text, parse_request_markdown
from .target_repo_guard import resolve_safe_target_repo_root


class CommitManager:
    def build_commit_message(self, task_dir: Path, metadata: TaskMetadata) -> str:
        title = metadata.title.strip() or metadata.slug
        subject = f"{self._commit_type(title)}: {self._normalize_subject(title)}"
        body_lines = self._build_commit_body(task_dir, metadata)
        if not body_lines:
            return subject
        return "\n".join([subject, "", *body_lines])

    def prepare_commit_message(self, task_dir: Path, metadata: TaskMetadata) -> str:
        message = self.build_commit_message(task_dir, metadata)
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
        metadata.commit.prepared_message or self.prepare_commit_message(task_dir, metadata)
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
        commit_path = task_dir / (metadata.commit.message_path or "COMMIT.md")
        commit = subprocess.run(["git", "-C", str(target_repo_root), "commit", "-F", str(commit_path)], capture_output=True, text=True, check=False)
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or "git commit failed")
        sha = subprocess.run(["git", "-C", str(target_repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return sha.stdout.strip()

    def _build_commit_body(self, task_dir: Path, metadata: TaskMetadata) -> list[str]:
        details: list[str] = []
        goal = self._request_goal(task_dir)
        if goal:
            details.append(f"Goal: {goal}")
        plan = self._artifact_summary(task_dir / "PLAN.md")
        if plan:
            details.append(f"Plan: {plan}")
        review = self._latest_artifact_summary(task_dir, "REVIEW-*.md")
        if review:
            details.append(f"Review: {review}")
        details.append(f"Task: {metadata.task_id}")
        return details

    def _request_goal(self, task_dir: Path) -> str | None:
        request_path = task_dir / "REQUEST.md"
        if not request_path.exists():
            return None
        parsed = parse_request_markdown(request_path.read_text())
        goal = extract_goal_text(parsed.body)
        return self._single_line(goal)

    def _latest_artifact_summary(self, task_dir: Path, pattern: str) -> str | None:
        files = sorted(task_dir.glob(pattern))
        if not files:
            return None
        return self._artifact_summary(files[-1])

    def _artifact_summary(self, path: Path) -> str | None:
        if not path.exists():
            return None
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("Verdict:"):
                continue
            return self._single_line(line)
        return None

    def _commit_type(self, title: str) -> str:
        lowered = title.casefold()
        if lowered.startswith(("fix ", "fix:", "bug ", "bugfix", "hotfix")):
            return "fix"
        if lowered.startswith(("docs ", "docs:", "document ", "readme")):
            return "docs"
        if lowered.startswith(("test ", "test:", "tests ")):
            return "test"
        if lowered.startswith(("refactor ", "refactor:")):
            return "refactor"
        if lowered.startswith(("chore ", "chore:")):
            return "chore"
        return "feat"

    def _normalize_subject(self, title: str) -> str:
        compact = " ".join(title.split())
        return compact[:1].lower() + compact[1:] if compact else "update task"

    def _single_line(self, value: str | None) -> str | None:
        if not value:
            return None
        compact = " ".join(value.split())
        return compact or None

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
