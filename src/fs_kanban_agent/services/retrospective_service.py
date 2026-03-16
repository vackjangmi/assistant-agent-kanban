from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict

from ..commit_manager import CommitManager
from ..config import AppConfig
from ..exceptions import AdapterRunError, CommitError, TaskNotFoundError, TransitionError
from ..language import generation_language_code, language_name
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

    def inspect(self, target_repo_root: str, base_branch: str, comparison_branch: str | None = None) -> RetrospectiveRecord:
        resolved_repo_root, group = self._load_group(target_repo_root, base_branch)
        normalized_comparison_branch = self._normalize_comparison_branch(comparison_branch)
        existing = self._load_existing_record(resolved_repo_root, base_branch, normalized_comparison_branch, group)
        if existing is not None:
            return existing
        return RetrospectiveRecord(
            exists=False,
            created=False,
            can_create=self.adapter is not None,
            task_ids=[task.metadata.task_id for task in group],
            target_repo_root=str(resolved_repo_root),
            target_repo_label=resolved_repo_root.name or str(resolved_repo_root),
            base_branch=base_branch,
            comparison_branch=normalized_comparison_branch,
        )

    def create(
        self,
        target_repo_root: str,
        base_branch: str,
        comparison_branch: str | None = None,
        *,
        by: str,
        completion_mode: Literal["new-branch", "target-branch"],
    ) -> RetrospectiveRecord:
        if completion_mode not in {"new-branch", "target-branch"}:
            raise TransitionError(f"unsupported retrospective completion mode: {completion_mode}")
        resolved_repo_root, group = self._load_group(target_repo_root, base_branch)
        if self.adapter is None:
            raise TransitionError("retrospective generation is unavailable because no commit adapter is configured")
        normalized_comparison_branch = self._normalize_comparison_branch(comparison_branch)

        with ExitStack() as stack:
            for task in group:
                stack.enter_context(self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-retrospective"))
            existing = self._load_existing_record(resolved_repo_root, base_branch, normalized_comparison_branch, group)
            if existing is not None:
                return existing.model_copy(update={"created": False, "can_create": True})
            generated_at = datetime.now(timezone.utc)
            result = self._generate_retrospective(group, resolved_repo_root, normalized_comparison_branch)
            content = result.assistant_text.strip()
            if not content:
                raise CommitError("retrospective generation returned empty content")
            normalized_content = content.rstrip() + "\n"
            repo_relative_path, committed_branch, commit_sha = self._commit_retrospective_document(
                resolved_repo_root,
                base_branch,
                comparison_branch=normalized_comparison_branch,
                content=normalized_content,
                completion_mode=completion_mode,
                generated_at=generated_at,
            )
            record = RetrospectiveRecord(
                exists=True,
                created=True,
                can_create=True,
                task_ids=[task.metadata.task_id for task in group],
                target_repo_root=str(resolved_repo_root),
                target_repo_label=resolved_repo_root.name or str(resolved_repo_root),
                base_branch=base_branch,
                comparison_branch=normalized_comparison_branch,
                committed_branch=committed_branch,
                completion_mode=completion_mode,
                repo_relative_path=repo_relative_path,
                artifact_filename=self._canonical_markdown_name(base_branch, normalized_comparison_branch),
                content=normalized_content,
                resolved_model=result.resolved_model,
                session_id=result.session_id,
                total_tokens=result.total_tokens,
                commit_sha=commit_sha,
                generated_at=generated_at,
            )
            self._write_canonical_artifacts(resolved_repo_root, base_branch, normalized_comparison_branch, record, result)
            return record

    def _load_group(self, target_repo_root: str, base_branch: str) -> tuple[Path, list[TaskContext]]:
        normalized_branch = base_branch.strip()
        if not normalized_branch:
            raise TransitionError("retrospective requires a target branch")
        resolved_repo_root = resolve_safe_target_repo_root(Path(target_repo_root))
        tasks = [
            task
            for task in self.scanner.scan()
            if str(task.state) == "done"
            and task.metadata.target.base_branch == normalized_branch
            and resolve_safe_target_repo_root(Path(task.metadata.target.repo_root)) == resolved_repo_root
        ]
        if not tasks:
            raise TransitionError("retrospective requires at least one done task for the selected project and branch")
        return resolved_repo_root, sorted(tasks, key=lambda item: item.metadata.task_id)

    def _load_existing_record(self, target_repo_root: Path, base_branch: str, comparison_branch: str | None, group: list[TaskContext]) -> RetrospectiveRecord | None:
        canonical = self._load_canonical_record(target_repo_root, base_branch, comparison_branch)
        if canonical is not None:
            return canonical.model_copy(update={"created": False, "can_create": self.adapter is not None})
        legacy = self._load_legacy_task_record(group, base_branch, comparison_branch)
        if legacy is not None:
            return legacy.model_copy(update={"created": False, "can_create": self.adapter is not None})
        return None

    def _load_canonical_record(self, target_repo_root: Path, base_branch: str, comparison_branch: str | None) -> RetrospectiveRecord | None:
        json_path = self._latest_canonical_json_path(target_repo_root, base_branch, comparison_branch)
        if json_path is None or not json_path.exists():
            return None
        record = RetrospectiveRecord.model_validate_json(json_path.read_text())
        markdown_path = self._canonical_root(target_repo_root) / (record.artifact_filename or self._canonical_markdown_name(base_branch, comparison_branch))
        if not markdown_path.exists():
            return None
        return record.model_copy(update={"content": markdown_path.read_text()})

    def _load_legacy_task_record(self, group: list[TaskContext], base_branch: str, comparison_branch: str | None) -> RetrospectiveRecord | None:
        if comparison_branch:
            return None
        artifact_name = self._legacy_artifact_json_name(base_branch)
        candidate_records: list[tuple[float, RetrospectiveRecord, str]] = []
        for task in group:
            path = task.task_dir / artifact_name
            if not path.exists():
                continue
            record = RetrospectiveRecord.model_validate_json(path.read_text())
            if not record.exists:
                continue
            markdown_name = record.artifact_filename or self._legacy_artifact_markdown_name(base_branch)
            markdown_path = task.task_dir / markdown_name
            if not markdown_path.exists():
                continue
            candidate_records.append((path.stat().st_mtime, record, markdown_path.read_text()))
        if not candidate_records:
            return None
        _, reference, content = sorted(candidate_records, key=lambda item: item[0])[-1]
        return reference.model_copy(update={"content": content})

    def _generate_retrospective(self, group: list[TaskContext], target_repo_root: Path, comparison_branch: str | None) -> RunResult:
        primary = group[0]
        adapter = self.adapter
        if adapter is None:
            raise CommitError("retrospective generation is unavailable because no commit adapter is configured")
        prompt = self._build_prompt(group, target_repo_root, comparison_branch)
        run_log_path = self.config.runs_dir / primary.metadata.task_id / f"retrospective-{self._retrospective_key(primary.metadata.target.base_branch, comparison_branch)}.jsonl"
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

    def _build_prompt(self, group: list[TaskContext], target_repo_root: Path, comparison_branch: str | None) -> str:
        language_code = self._group_language(group)
        text = RETROSPECTIVE_TEXT[language_code]
        requested_language = language_name(language_code)
        normalized_comparison_branch = self._normalize_comparison_branch(comparison_branch)
        sections = [
            text["intro"],
            text["language_line"].format(language=requested_language),
            text["requirements_heading"],
            *text["requirements"],
            "",
            f"{text['target_repo_label']}: {group[0].metadata.target.repo_root}",
            f"{text['target_branch_label']}: {group[0].metadata.target.base_branch}",
            "",
        ]
        if normalized_comparison_branch:
            diff_summary = self._build_git_diff_summary(target_repo_root, group[0].metadata.target.base_branch, normalized_comparison_branch, text["not_available"])
            sections.extend(
                [
                    f"{text['comparison_branch_label']}: {normalized_comparison_branch}",
                    text["git_diff_heading"].format(comparison_branch=normalized_comparison_branch, base_branch=group[0].metadata.target.base_branch),
                    diff_summary,
                    "",
                ]
            )
        sections.extend(
            [
            text["completed_tasks_heading"],
            ]
        )
        for task in group:
            sections.extend(
                [
                    f"- {text['task_id_label']}: {task.metadata.task_id}",
                    f"  {text['title_label']}: {task.metadata.title}",
                    f"  {text['plan_label']}: {self._artifact_summary(task.task_dir / 'PLAN.md') or text['not_available']}",
                    f"  {text['review_label']}: {self._latest_artifact_summary(task.task_dir, 'REVIEW-*.md') or text['not_available']}",
                    f"  {text['human_verification_label']}: {self._latest_artifact_summary(task.task_dir, 'HUMAN-VERIFY-*.md') or text['not_available']}",
                ]
        )
        return "\n".join(sections)

    def _group_language(self, group: list[TaskContext]) -> str:
        counts = {"en": 0, "ko": 0}
        for task in group:
            code = generation_language_code(task.metadata.request.language)
            counts["ko" if code == "ko" else "en"] += 1
        return "ko" if counts["ko"] > counts["en"] else "en"

    def _commit_retrospective_document(
        self,
        target_repo_root: Path,
        base_branch: str,
        *,
        comparison_branch: str | None,
        content: str,
        completion_mode: Literal["new-branch", "target-branch"],
        generated_at: datetime,
    ) -> tuple[str, str, str]:
        primary_branch = base_branch
        self._ensure_clean_repo(target_repo_root)
        self.commit_manager._switch_to_branch(target_repo_root, primary_branch)
        committed_branch = primary_branch if completion_mode == "target-branch" else self._ensure_retro_branch(target_repo_root, primary_branch, generated_at)
        if completion_mode == "new-branch":
            switch = subprocess.run(
                ["git", "-C", str(target_repo_root), "switch", "-C", committed_branch, primary_branch],
                capture_output=True,
                text=True,
                check=False,
            )
            if switch.returncode != 0:
                raise CommitError(switch.stderr.strip() or "failed to create retrospective branch")
        repo_relative_path = self._repo_relative_path(primary_branch, comparison_branch, generated_at)
        repo_path = target_repo_root / repo_relative_path
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        repo_path.write_text(content.rstrip() + "\n")
        add_result = subprocess.run(["git", "-C", str(target_repo_root), "add", repo_relative_path], capture_output=True, text=True, check=False)
        if add_result.returncode != 0:
            raise CommitError(add_result.stderr.strip() or "failed to stage retrospective artifact")
        compare_suffix = f" vs {comparison_branch}" if comparison_branch else ""
        commit_message = f"docs: add retrospective for {primary_branch} branch{compare_suffix}"
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

    def _ensure_retro_branch(self, repo_root: Path, base_branch: str, generated_at: datetime) -> str:
        date_suffix = generated_at.strftime("%y%m%d")
        candidate = f"retro/{self._branch_slug(base_branch)}-{date_suffix}"
        if not self.commit_manager._branch_exists(repo_root, candidate):
            return candidate
        suffix = 2
        while self.commit_manager._branch_exists(repo_root, f"{candidate}-{suffix}"):
            suffix += 1
        return f"{candidate}-{suffix}"

    def _write_canonical_artifacts(self, target_repo_root: Path, base_branch: str, comparison_branch: str | None, record: RetrospectiveRecord, result: RunResult) -> None:
        root = self._canonical_root(target_repo_root)
        root.mkdir(parents=True, exist_ok=True)
        markdown_name = record.artifact_filename or self._canonical_markdown_name(base_branch, comparison_branch)
        json_name = self._canonical_json_name(base_branch, comparison_branch)
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
        (root / markdown_name).write_text(record.content.rstrip() + "\n")
        (root / json_name).write_text(json.dumps(payload, indent=2) + "\n")

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

    def _repo_relative_path(self, base_branch: str, comparison_branch: str | None, generated_at: datetime) -> Path:
        date_prefix = generated_at.strftime("%Y/%m")
        filename = self._canonical_markdown_name(base_branch, comparison_branch)
        return Path("docs") / "kanban-agent" / "retrospectives" / date_prefix / filename

    def _canonical_markdown_name(self, base_branch: str, comparison_branch: str | None = None) -> str:
        return f"{self._retrospective_key(base_branch, comparison_branch)}.md"

    def _canonical_json_name(self, base_branch: str, comparison_branch: str | None = None) -> str:
        return f"{self._retrospective_key(base_branch, comparison_branch)}.json"

    def _legacy_artifact_markdown_name(self, base_branch: str) -> str:
        return f"RETRO-{self._branch_slug(base_branch)}.md"

    def _legacy_artifact_json_name(self, base_branch: str) -> str:
        return f"RETRO-{self._branch_slug(base_branch)}.json"

    def _canonical_root(self, target_repo_root: Path) -> Path:
        return self.config.retrospectives_dir / self._repo_key(target_repo_root)

    def _latest_canonical_json_path(self, target_repo_root: Path, base_branch: str, comparison_branch: str | None) -> Path | None:
        path = self._canonical_root(target_repo_root) / self._canonical_json_name(base_branch, comparison_branch)
        return path if path.exists() else None

    def _repo_key(self, target_repo_root: Path) -> str:
        resolved = str(target_repo_root.expanduser().resolve())
        digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:10]
        name = target_repo_root.name or "repo"
        return f"{self.commit_manager.sanitize_branch_summary(name, fallback_title='repo')}-{digest}"

    def _branch_slug(self, branch: str) -> str:
        return self.commit_manager.sanitize_branch_summary(branch, fallback_title=branch)

    def _retrospective_key(self, base_branch: str, comparison_branch: str | None) -> str:
        base_slug = self._branch_slug(base_branch)
        if not comparison_branch:
            return base_slug
        compare_slug = self._branch_slug(comparison_branch)
        return f"{base_slug}--vs--{compare_slug}"

    def _normalize_comparison_branch(self, comparison_branch: str | None) -> str | None:
        normalized = (comparison_branch or "").strip()
        if not normalized or normalized == ".":
            return None
        return normalized

    def _resolve_git_ref(self, repo_root: Path, branch: str) -> str:
        normalized = branch.strip()
        candidates = [normalized, f"origin/{normalized}"]
        for candidate in candidates:
            result = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", candidate],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return candidate
        raise TransitionError(f"comparison branch '{branch}' does not exist in the target repository")

    def _build_git_diff_summary(self, repo_root: Path, base_branch: str, comparison_branch: str, not_available: str) -> str:
        base_ref = self._resolve_git_ref(repo_root, base_branch)
        comparison_ref = self._resolve_git_ref(repo_root, comparison_branch)
        if base_ref == comparison_ref:
            return not_available
        range_spec = f"{comparison_ref}..{base_ref}"
        shortstat = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--shortstat", "--no-color", "--find-renames", range_spec],
            capture_output=True,
            text=True,
            check=False,
        )
        if shortstat.returncode != 0:
            raise TransitionError(shortstat.stderr.strip() or "failed to summarize comparison diff")
        name_status = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-status", "--no-color", "--find-renames", range_spec],
            capture_output=True,
            text=True,
            check=False,
        )
        if name_status.returncode != 0:
            raise TransitionError(name_status.stderr.strip() or "failed to list comparison diff files")
        lines: list[str] = []
        if shortstat.stdout.strip():
            lines.append(shortstat.stdout.strip())
        changed_files = [line.strip() for line in name_status.stdout.splitlines() if line.strip()]
        for entry in changed_files[:20]:
            lines.append(f"- {entry}")
        if len(changed_files) > 20:
            lines.append(f"- ... {len(changed_files) - 20} more files")
        return "\n".join(lines) if lines else not_available

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


class RetrospectiveText(TypedDict):
    intro: str
    language_line: str
    requirements_heading: str
    requirements: list[str]
    comparison_branch_label: str
    git_diff_heading: str
    completed_tasks_heading: str
    task_id_label: str
    title_label: str
    target_repo_label: str
    target_branch_label: str
    plan_label: str
    review_label: str
    human_verification_label: str
    not_available: str


RETROSPECTIVE_TEXT: dict[str, RetrospectiveText] = {
    "en": {
        "intro": "Write a concise engineering retrospective in markdown.",
        "language_line": "Return the retrospective in {language}.",
        "requirements_heading": "Requirements:",
        "requirements": [
            "- Keep it practical and specific to the completed tasks.",
            "- Include these sections in order: # Retrospective, ## Summary, ## Completed tasks, ## What went well, ## Risks and follow-ups.",
            "- Use bullet lists where helpful.",
            "- Do not mention unavailable information.",
            "- Do not include fenced code blocks.",
        ],
        "comparison_branch_label": "Comparison branch",
        "git_diff_heading": "Git diff summary ({comparison_branch}..{base_branch}):",
        "completed_tasks_heading": "Completed tasks:",
        "task_id_label": "Task ID",
        "title_label": "Title",
        "target_repo_label": "Target repo",
        "target_branch_label": "Base branch",
        "plan_label": "Plan",
        "review_label": "Review",
        "human_verification_label": "Human verification",
        "not_available": "n/a",
    },
    "ko": {
        "intro": "엔지니어링 회고를 마크다운으로 간결하게 작성하세요.",
        "language_line": "회고는 반드시 {language}로 작성하세요.",
        "requirements_heading": "요구사항:",
        "requirements": [
            "- 완료된 작업에 구체적으로 연결된 실용적인 내용만 작성하세요.",
            "- 섹션 순서는 다음을 따르세요: # 회고, ## 요약, ## 완료된 작업, ## 잘된 점, ## 리스크와 후속 조치.",
            "- 필요하면 bullet list를 사용하세요.",
            "- 확인할 수 없는 정보는 추측해서 쓰지 마세요.",
            "- fenced code block은 포함하지 마세요.",
        ],
        "comparison_branch_label": "비교 브랜치",
        "git_diff_heading": "Git diff 요약 ({comparison_branch}..{base_branch}):",
        "completed_tasks_heading": "완료된 작업:",
        "task_id_label": "태스크 ID",
        "title_label": "제목",
        "target_repo_label": "대상 저장소",
        "target_branch_label": "기준 브랜치",
        "plan_label": "계획",
        "review_label": "리뷰",
        "human_verification_label": "사람 검증",
        "not_available": "없음",
    },
}
