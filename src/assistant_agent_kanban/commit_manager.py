from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .exceptions import CommitError
from .models import TaskMetadata
from .request_parser import extract_goal_text, parse_request_markdown
from .target_repo_guard import resolve_safe_target_repo_root


class CommitManager:
    def build_commit_message(self, task_dir: Path, metadata: TaskMetadata, *, summary_markdown: str | None = None) -> str:
        title = metadata.title.strip() or metadata.slug
        subject = f"{self._commit_type(title)}: {self._normalize_subject(title)}"
        body_lines = self._build_commit_body(task_dir, metadata, summary_markdown=summary_markdown)
        if not body_lines:
            return subject
        return "\n".join([subject, "", *body_lines])

    def prepare_commit_message(self, task_dir: Path, metadata: TaskMetadata, *, summary_markdown: str | None = None) -> str:
        message = self.build_commit_message(task_dir, metadata, summary_markdown=summary_markdown)
        commit_path = task_dir / "COMMIT.md"
        commit_path.write_text(message + "\n")
        metadata.commit.message_path = "COMMIT.md"
        metadata.commit.prepared_message = message
        return message

    def commit_task(self, task_dir: Path, metadata: TaskMetadata) -> str:
        return self._commit_review_branch(task_dir, metadata, allow_existing_commit=False)

    def finalize_review_branch(self, task_dir: Path, metadata: TaskMetadata, *, completion_mode: str = "new-branch") -> str:
        if completion_mode not in {"new-branch", "target-branch"}:
            raise CommitError(f"unsupported completion mode: {completion_mode}")
        review_sha = self._ensure_review_branch_tip(task_dir, metadata)
        metadata.commit.review_sha = review_sha
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise CommitError(str(exc)) from exc
        if completion_mode == "target-branch":
            final_sha = self._squash_review_branch_onto(target_repo_root, task_dir, metadata, metadata.target.base_branch)
            metadata.integration.final_branch = metadata.target.base_branch
            return final_sha
        base_head = self._branch_head(target_repo_root, metadata.target.base_branch)
        if base_head is None:
            raise CommitError(f"failed to resolve base branch '{metadata.target.base_branch}'")
        final_branch = self._ensure_final_branch(target_repo_root, metadata, base_head)
        metadata.integration.final_branch = final_branch
        return self._squash_review_branch_onto(target_repo_root, task_dir, metadata, final_branch)

    def _commit_review_branch(self, task_dir: Path, metadata: TaskMetadata, *, allow_existing_commit: bool) -> str:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise CommitError(str(exc)) from exc
        metadata.commit.prepared_message or self.prepare_commit_message(task_dir, metadata)
        review_branch = metadata.integration.review_branch
        if review_branch:
            current_branch = self._current_branch(target_repo_root)
            if current_branch != review_branch and allow_existing_commit and metadata.commit.review_sha is None:
                self._restore_review_branch_from_patch(target_repo_root, metadata, review_branch)
            elif current_branch != review_branch:
                switch = subprocess.run(
                    ["git", "-C", str(target_repo_root), "switch", review_branch],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if switch.returncode != 0:
                    raise CommitError(switch.stderr.strip() or "failed to switch to review branch")
        stage_all = subprocess.run(["git", "-C", str(target_repo_root), "add", "-A"], capture_output=True, text=True, check=False)
        if stage_all.returncode != 0:
            raise CommitError(stage_all.stderr.strip() or "failed to stage target repo changes")
        staged = self._cached_diff_status(target_repo_root)
        if staged.returncode == 0 and allow_existing_commit and metadata.commit.review_sha is None:
            self._reapply_review_patch(target_repo_root, metadata)
            stage_all = subprocess.run(["git", "-C", str(target_repo_root), "add", "-A"], capture_output=True, text=True, check=False)
            if stage_all.returncode != 0:
                raise CommitError(stage_all.stderr.strip() or "failed to restage target repo changes")
            staged = self._cached_diff_status(target_repo_root)
        if staged.returncode == 0:
            if allow_existing_commit and metadata.commit.review_sha is not None:
                sha = self._current_head(target_repo_root)
                if sha:
                    return sha
            raise CommitError("no changes to commit")
        if staged.returncode not in (0, 1):
            raise CommitError(staged.stderr.strip() or "failed to inspect staged changes")
        commit_path = (task_dir / (metadata.commit.message_path or "COMMIT.md")).expanduser().resolve()
        commit = subprocess.run(["git", "-C", str(target_repo_root), "commit", "-F", str(commit_path)], capture_output=True, text=True, check=False)
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or "git commit failed")
        return self._current_head(target_repo_root) or ""

    def _ensure_review_branch_tip(self, task_dir: Path, metadata: TaskMetadata) -> str:
        review_branch = metadata.integration.review_branch
        if not review_branch:
            raise CommitError("review branch is missing")
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError as exc:
            raise CommitError(str(exc)) from exc
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
        return self._commit_review_branch(task_dir, metadata, allow_existing_commit=True)

    def _squash_review_branch_onto(self, repo_root: Path, task_dir: Path, metadata: TaskMetadata, destination_branch: str) -> str:
        review_branch = metadata.integration.review_branch
        if not review_branch:
            raise CommitError("review branch is missing")
        self._switch_to_branch(repo_root, destination_branch)
        squash = subprocess.run(
            ["git", "-C", str(repo_root), "merge", "--squash", review_branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if squash.returncode != 0:
            subprocess.run(["git", "-C", str(repo_root), "merge", "--abort"], capture_output=True, text=True, check=False)
            raise CommitError(squash.stderr.strip() or "failed to squash review branch")
        stage_status = self._cached_diff_status(repo_root)
        if stage_status.returncode == 0:
            raise CommitError("no squashed changes to commit")
        if stage_status.returncode not in (0, 1):
            raise CommitError(stage_status.stderr.strip() or "failed to inspect squashed changes")
        commit_path = (task_dir / (metadata.commit.message_path or "COMMIT.md")).expanduser().resolve()
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "commit", "-F", str(commit_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or "git commit failed")
        return self._current_head(repo_root) or ""

    def _cached_diff_status(self, repo_root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--cached", "--quiet"],
            capture_output=True,
            text=True,
            check=False,
        )

    def _reapply_review_patch(self, repo_root: Path, metadata: TaskMetadata) -> None:
        patch_path = metadata.integration.patch_path
        if not patch_path:
            raise CommitError("review patch path is missing")
        resolved_patch = Path(patch_path).expanduser().resolve()
        if not resolved_patch.exists():
            raise CommitError("review patch is missing")
        apply_result = subprocess.run(
            ["git", "-C", str(repo_root), "apply", "--3way", "--index", str(resolved_patch)],
            capture_output=True,
            text=True,
            check=False,
        )
        if apply_result.returncode != 0:
            raise CommitError(apply_result.stderr.strip() or "failed to reapply review patch")

    def _restore_review_branch_from_patch(self, repo_root: Path, metadata: TaskMetadata, review_branch: str) -> None:
        start_point = metadata.integration.base_commit or metadata.target.base_branch
        switch = subprocess.run(
            ["git", "-C", str(repo_root), "switch", "-C", review_branch, start_point],
            capture_output=True,
            text=True,
            check=False,
        )
        if switch.returncode != 0:
            raise CommitError(switch.stderr.strip() or "failed to restore review branch")
        self._reapply_review_patch(repo_root, metadata)

    def _build_commit_body(self, task_dir: Path, metadata: TaskMetadata, *, summary_markdown: str | None = None) -> list[str]:
        if summary_markdown is not None:
            return self._commit_body_from_summary(summary_markdown)
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
        human_verify = self._latest_artifact_summary(task_dir, "HUMAN-VERIFY-*.md")
        if human_verify:
            details.append(f"Human review: {human_verify}")
        details.append(f"Task: {metadata.task_id}")
        return details

    def _commit_body_from_summary(self, summary_markdown: str) -> list[str]:
        sections = self._parse_summary_sections(summary_markdown)
        missing_sections = [name for name in ("Overview", "Why / Keywords") if name not in sections]
        if missing_sections:
            missing = ", ".join(missing_sections)
            raise CommitError(f"completion summary is missing required section(s): {missing}")

        body_lines: list[str] = []

        for line in sections.get("Why / Keywords", []):
            cleaned = self._summary_line_to_commit_line(line)
            if cleaned is not None:
                body_lines.append(cleaned)

        for line in sections.get("Overview", []):
            overview_line = self._summary_overview_to_commit_line(line)
            if overview_line is not None:
                body_lines.append(overview_line)

        if not body_lines:
            raise CommitError("completion summary did not contain any commit-ready lines")

        return body_lines[:10]

    def _parse_summary_sections(self, summary_markdown: str) -> dict[str, list[str]]:
        sections: dict[str, list[str]] = {}
        current_section: str | None = None
        for raw_line in summary_markdown.splitlines():
            line = raw_line.rstrip()
            if line.startswith("## "):
                current_section = line[3:].strip()
                sections.setdefault(current_section, [])
                continue
            if current_section is None:
                continue
            stripped = line.strip()
            if not stripped:
                continue
            sections.setdefault(current_section, []).append(stripped)
        return sections

    def _summary_line_to_commit_line(self, line: str) -> str | None:
        normalized = self._single_line(line.lstrip("- "))
        if not normalized:
            return None
        if normalized.startswith("Keywords:"):
            return None
        if normalized.startswith("Goal:"):
            return normalized
        if normalized.startswith("Plan summary:"):
            return normalized.replace("Plan summary:", "Plan:", 1)
        if normalized.startswith("Review summary:"):
            return normalized.replace("Review summary:", "Review:", 1)
        if normalized.startswith("Human review summary:"):
            return normalized.replace("Human review summary:", "Human review:", 1)
        return None

    def _summary_overview_to_commit_line(self, line: str) -> str | None:
        normalized = self._single_line(line.lstrip("- "))
        if not normalized:
            return None
        if normalized.startswith("Task ID:"):
            task_id = normalized.removeprefix("Task ID:").strip().strip("`")
            return f"Task: {task_id}" if task_id else None
        if normalized.startswith("Branch summary:"):
            summary = normalized.removeprefix("Branch summary:").strip().strip("`")
            return f"Branch: {summary}" if summary else None
        return None

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
            if line in {"No notes yet.", "No unresolved comments."}:
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

    def _current_head(self, repo_root: Path) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        return sha or None

    def _switch_to_branch(self, repo_root: Path, branch: str) -> None:
        for command in (
            ["git", "-C", str(repo_root), "switch", branch],
            ["git", "-C", str(repo_root), "switch", "-C", branch, f"origin/{branch}"],
        ):
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return
        raise CommitError(f"failed to switch to target branch '{branch}'")

    def _ensure_final_branch(self, repo_root: Path, metadata: TaskMetadata, start_point: str) -> str:
        branch = self._preferred_final_branch(metadata)
        if self._branch_exists(repo_root, branch):
            existing_sha = self._branch_head(repo_root, branch)
            if existing_sha != start_point:
                branch = f"{branch}-{metadata.task_id.lower()}"
                if self._branch_exists(repo_root, branch):
                    fallback_sha = self._branch_head(repo_root, branch)
                    if fallback_sha != start_point:
                        raise CommitError("failed to create final branch: fallback branch already exists")
        switch = subprocess.run(
            ["git", "-C", str(repo_root), "switch", "-C", branch, start_point],
            capture_output=True,
            text=True,
            check=False,
        )
        if switch.returncode != 0:
            raise CommitError(switch.stderr.strip() or "failed to create final branch")
        return branch

    def _preferred_final_branch(self, metadata: TaskMetadata) -> str:
        summary = self.sanitize_branch_summary(metadata.integration.final_branch_summary, fallback_title=metadata.title)
        return f"feature/{metadata.task_id.lower()}-{summary}"

    def sanitize_branch_summary(self, summary: str | None, *, fallback_title: str) -> str:
        if summary:
            ascii_slug = self._slugify_ascii(summary)
            if ascii_slug and ascii_slug != "task":
                return ascii_slug
        return self._branch_summary_slug(fallback_title)

    def _branch_summary_slug(self, title: str) -> str:
        ascii_slug = self._slugify_ascii(title)
        if ascii_slug and ascii_slug != "task":
            return ascii_slug
        romanized = self._romanize_korean(title)
        romanized_slug = self._slugify_ascii(romanized)
        if romanized_slug and romanized_slug != "task":
            return romanized_slug
        return "task"

    def _slugify_ascii(self, value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return normalized or "task"

    def _romanize_korean(self, value: str) -> str:
        choseong = [
            "g",
            "kk",
            "n",
            "d",
            "tt",
            "r",
            "m",
            "b",
            "pp",
            "s",
            "ss",
            "",
            "j",
            "jj",
            "ch",
            "k",
            "t",
            "p",
            "h",
        ]
        jungseong = [
            "a",
            "ae",
            "ya",
            "yae",
            "eo",
            "e",
            "yeo",
            "ye",
            "o",
            "wa",
            "wae",
            "oe",
            "yo",
            "u",
            "wo",
            "we",
            "wi",
            "yu",
            "eu",
            "ui",
            "i",
        ]
        jongseong = [
            "",
            "k",
            "k",
            "ks",
            "n",
            "nj",
            "nh",
            "t",
            "l",
            "lk",
            "lm",
            "lb",
            "ls",
            "lt",
            "lp",
            "lh",
            "m",
            "p",
            "ps",
            "t",
            "t",
            "ng",
            "t",
            "t",
            "k",
            "t",
            "p",
            "t",
        ]
        pieces: list[str] = []
        for char in value:
            code = ord(char)
            if 0xAC00 <= code <= 0xD7A3:
                syllable_index = code - 0xAC00
                lead = syllable_index // 588
                vowel = (syllable_index % 588) // 28
                tail = syllable_index % 28
                pieces.append(choseong[lead] + jungseong[vowel] + jongseong[tail])
            elif char.isascii() and (char.isalnum() or char in {" ", "-"}):
                pieces.append(char)
            else:
                pieces.append(" ")
        return "".join(pieces)

    def _branch_exists(self, repo_root: Path, branch: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show-ref", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _branch_head(self, repo_root: Path, branch: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
