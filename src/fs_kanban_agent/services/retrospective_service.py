from __future__ import annotations

import json
import subprocess
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ..commit_manager import CommitManager
from ..config import AppConfig
from ..exceptions import AdapterRunError, CommitError, TaskNotFoundError, TransitionError
from ..locks import TaskLockManager
from ..models import RetrospectiveRecord, RunResult, TaskContext
from ..opencode_adapter import OpenCodeAdapter
from ..scanner import KanbanScanner
from ..target_repo_guard import resolve_safe_target_repo_root


class RetrospectiveService:
    scanner: KanbanScanner
    config: AppConfig
    locks: TaskLockManager
    commit_manager: CommitManager
    adapter: OpenCodeAdapter | None

    def __init__(
        self,
        scanner: KanbanScanner,
        config: AppConfig,
        locks: TaskLockManager,
        commit_manager: CommitManager,
        adapter: OpenCodeAdapter | None = None,
    ) -> None:
        self.scanner = scanner
        self.config = config
        self.locks = locks
        self.commit_manager = commit_manager
        self.adapter = adapter

    def inspect(self, task_ids: list[str]) -> RetrospectiveRecord:
        group = self._load_group(task_ids)
        existing = self._load_existing_record(group)
        if existing is not None:
            return existing
        return RetrospectiveRecord(
            exists=False,
            created=False,
            can_create=self.adapter is not None,
            task_ids=[task.metadata.task_id for task in group],
            target_repo_root=group[0].metadata.target.repo_root,
            target_repo_label=Path(group[0].metadata.target.repo_root).name or group[0].metadata.target.repo_root,
            base_branch=group[0].metadata.target.base_branch,
        )

    def create(self, task_ids: list[str], *, by: str, completion_mode: Literal["new-branch", "target-branch"]) -> RetrospectiveRecord:
        if completion_mode not in {"new-branch", "target-branch"}:
            raise TransitionError(f"unsupported retrospective completion mode: {completion_mode}")
        group = self._load_group(task_ids)
        if self.adapter is None:
            raise TransitionError("retrospective generation is unavailable because no commit adapter is configured")

        with ExitStack() as stack:
            for task in group:
                stack.enter_context(self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-retrospective"))
            existing = self._load_existing_record(group)
            if existing is not None:
                return existing.model_copy(update={"created": False, "can_create": True})
            generated_at = datetime.now(timezone.utc)
            result = self._generate_retrospective(group)
            content = result.assistant_text.strip()
            if not content:
                raise CommitError("retrospective generation returned empty content")
            normalized_content = content.rstrip() + "\n"
            repo_relative_path, committed_branch, commit_sha = self._commit_retrospective_document(
                group,
                content=normalized_content,
                completion_mode=completion_mode,
                generated_at=generated_at,
            )
            record = RetrospectiveRecord(
                exists=True,
                created=True,
                can_create=True,
                task_ids=[task.metadata.task_id for task in group],
                target_repo_root=group[0].metadata.target.repo_root,
                target_repo_label=Path(group[0].metadata.target.repo_root).name or group[0].metadata.target.repo_root,
                base_branch=group[0].metadata.target.base_branch,
                committed_branch=committed_branch,
                completion_mode=completion_mode,
                repo_relative_path=repo_relative_path,
                artifact_filename=self._artifact_markdown_name(group[0].metadata.target.base_branch),
                content=normalized_content,
                resolved_model=result.resolved_model,
                session_id=result.session_id,
                total_tokens=result.total_tokens,
                commit_sha=commit_sha,
                generated_at=generated_at,
            )
            self._write_group_artifacts(group, record, result)
            return record

    def _load_group(self, task_ids: list[str]) -> list[TaskContext]:
        normalized_ids = [task_id.strip() for task_id in task_ids if task_id and task_id.strip()]
        if not normalized_ids:
            raise TransitionError("retrospective requires at least one completed task")
        tasks: list[TaskContext] = []
        for task_id in normalized_ids:
            try:
                task = self.scanner.find_task(task_id)
            except FileNotFoundError as exc:
                raise TaskNotFoundError(task_id) from exc
            if str(task.state) != "done":
                raise TransitionError("retrospective is only available for done tasks")
            tasks.append(task)
        repo_roots = {Path(task.metadata.target.repo_root).expanduser().resolve() for task in tasks}
        if len(repo_roots) != 1:
            raise TransitionError("retrospective tasks must share the same target repository")
        base_branches = {task.metadata.target.base_branch for task in tasks}
        if len(base_branches) != 1:
            raise TransitionError("retrospective tasks must share the same target branch")
        return sorted(tasks, key=lambda item: item.metadata.task_id)

    def _load_existing_record(self, group: list[TaskContext]) -> RetrospectiveRecord | None:
        artifact_name = self._artifact_json_name(group[0].metadata.target.base_branch)
        expected_task_ids = [task.metadata.task_id for task in group]
        records: list[RetrospectiveRecord] = []
        markdown_contents: list[str] = []
        for task in group:
            path = task.task_dir / artifact_name
            if not path.exists():
                return None
            record = RetrospectiveRecord.model_validate_json(path.read_text())
            records.append(record)
            markdown_name = record.artifact_filename or self._artifact_markdown_name(group[0].metadata.target.base_branch)
            markdown_path = task.task_dir / markdown_name
            if not markdown_path.exists():
                return None
            markdown_contents.append(markdown_path.read_text())
        reference = records[0]
        if not reference.exists or reference.task_ids != expected_task_ids:
            return None
        for record in records[1:]:
            if record.model_dump(mode="json", exclude={"content"}) != reference.model_dump(mode="json", exclude={"content"}):
                return None
        if any(content != markdown_contents[0] for content in markdown_contents[1:]):
            return None
        return reference.model_copy(update={"content": markdown_contents[0], "created": False, "can_create": self.adapter is not None})

    def _generate_retrospective(self, group: list[TaskContext]) -> RunResult:
        primary = group[0]
        adapter = self.adapter
        if adapter is None:
            raise CommitError("retrospective generation is unavailable because no commit adapter is configured")
        prompt = self._build_prompt(group)
        run_log_path = self.config.runs_dir / primary.metadata.task_id / f"retrospective-{self._branch_slug(primary.metadata.target.base_branch)}.jsonl"
        try:
            result = adapter.run(
                agent=self.config.opencode.commit_agent,
                prompt=prompt,
                cwd=primary.task_dir,
                run_log_path=run_log_path,
                config=self.config,
            )
        except AdapterRunError as exc:
            raise CommitError(str(exc)) from exc
        if not result.ok:
            raise CommitError(result.stderr.strip() or "retrospective generation failed")
        return result

    def _build_prompt(self, group: list[TaskContext]) -> str:
        sections = [
            "Write a concise engineering retrospective in markdown.",
            "Requirements:",
            "- Keep it practical and specific to the completed tasks.",
            "- Include these sections in order: # Retrospective, ## Summary, ## Completed tasks, ## What went well, ## Risks and follow-ups.",
            "- Use bullet lists where helpful.",
            "- Do not mention unavailable information.",
            "- Do not include fenced code blocks.",
            "",
            f"Target repo: {group[0].metadata.target.repo_root}",
            f"Target branch: {group[0].metadata.target.base_branch}",
            "",
            "Completed tasks:",
        ]
        for task in group:
            sections.extend(
                [
                    f"- Task ID: {task.metadata.task_id}",
                    f"  Title: {task.metadata.title}",
                    f"  Plan: {self._artifact_summary(task.task_dir / 'PLAN.md') or 'n/a'}",
                    f"  Review: {self._latest_artifact_summary(task.task_dir, 'REVIEW-*.md') or 'n/a'}",
                    f"  Human verification: {self._latest_artifact_summary(task.task_dir, 'HUMAN-VERIFY-*.md') or 'n/a'}",
                ]
            )
        return "\n".join(sections)

    def _commit_retrospective_document(
        self,
        group: list[TaskContext],
        *,
        content: str,
        completion_mode: Literal["new-branch", "target-branch"],
        generated_at: datetime,
    ) -> tuple[str, str, str]:
        primary = group[0]
        target_repo_root = resolve_safe_target_repo_root(Path(primary.metadata.target.repo_root))
        base_branch = primary.metadata.target.base_branch
        self._ensure_clean_repo(target_repo_root)
        self.commit_manager._switch_to_branch(target_repo_root, base_branch)
        committed_branch = base_branch if completion_mode == "target-branch" else self._ensure_retro_branch(target_repo_root, primary, base_branch)
        if completion_mode == "new-branch":
            switch = subprocess.run(
                ["git", "-C", str(target_repo_root), "switch", "-C", committed_branch, base_branch],
                capture_output=True,
                text=True,
                check=False,
            )
            if switch.returncode != 0:
                raise CommitError(switch.stderr.strip() or "failed to create retrospective branch")
        repo_relative_path = self._repo_relative_path(primary, generated_at)
        repo_path = target_repo_root / repo_relative_path
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        repo_path.write_text(content.rstrip() + "\n")
        add_result = subprocess.run(["git", "-C", str(target_repo_root), "add", repo_relative_path], capture_output=True, text=True, check=False)
        if add_result.returncode != 0:
            raise CommitError(add_result.stderr.strip() or "failed to stage retrospective artifact")
        commit_message = f"docs: add retrospective for {base_branch} tasks"
        commit_result = subprocess.run(
            ["git", "-C", str(target_repo_root), "commit", "-m", commit_message],
            capture_output=True,
            text=True,
            check=False,
        )
        if commit_result.returncode != 0:
            raise CommitError(commit_result.stderr.strip() or "failed to commit retrospective artifact")
        commit_sha = self.commit_manager._current_head(target_repo_root) or ""
        return repo_relative_path.as_posix(), committed_branch, commit_sha

    def _ensure_retro_branch(self, repo_root: Path, primary: TaskContext, base_branch: str) -> str:
        summary = self.commit_manager.sanitize_branch_summary(None, fallback_title=primary.metadata.title)
        candidate = f"retro/{base_branch}-{summary}"
        if not self.commit_manager._branch_exists(repo_root, candidate):
            return candidate
        suffix = 2
        while self.commit_manager._branch_exists(repo_root, f"{candidate}-{suffix}"):
            suffix += 1
        return f"{candidate}-{suffix}"

    def _write_group_artifacts(self, group: list[TaskContext], record: RetrospectiveRecord, result: RunResult) -> None:
        markdown_name = record.artifact_filename or self._artifact_markdown_name(group[0].metadata.target.base_branch)
        json_name = self._artifact_json_name(group[0].metadata.target.base_branch)
        payload = {
            "ok": result.ok,
            "returncode": result.returncode,
            "assistant_text": result.assistant_text,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "raw_events_path": result.raw_events_path,
            "command": result.command,
            "resolved_model": result.resolved_model,
            "session_id": result.session_id,
            "total_tokens": result.total_tokens,
            **record.model_dump(mode="json"),
        }
        for task in group:
            (task.task_dir / markdown_name).write_text(record.content.rstrip() + "\n")
            (task.task_dir / json_name).write_text(json.dumps(payload, indent=2) + "\n")

    def _ensure_clean_repo(self, repo_root: Path) -> None:
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            raise CommitError(status.stderr.strip() or "failed to inspect target repository status")
        if status.stdout.strip():
            raise CommitError("target repository must be clean before creating a retrospective commit")

    def _repo_relative_path(self, primary: TaskContext, generated_at: datetime) -> Path:
        date_prefix = generated_at.strftime("%Y/%m/%d")
        base_slug = self._branch_slug(primary.metadata.target.base_branch)
        filename = f"{generated_at.strftime('%Y-%m-%d')}-{base_slug}-{primary.metadata.task_id.lower()}.md"
        return Path("docs") / "kanban-agent" / "retrospectives" / date_prefix / filename

    def _artifact_markdown_name(self, base_branch: str) -> str:
        return f"RETRO-{self._branch_slug(base_branch)}.md"

    def _artifact_json_name(self, base_branch: str) -> str:
        return f"RETRO-{self._branch_slug(base_branch)}.json"

    def _branch_slug(self, branch: str) -> str:
        return self.commit_manager.sanitize_branch_summary(branch, fallback_title=branch)

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
            if not line or line.startswith("#") or line.startswith("Verdict:"):
                continue
            if line in {"No notes yet.", "No unresolved comments."}:
                continue
            return " ".join(line.split())
        return None
