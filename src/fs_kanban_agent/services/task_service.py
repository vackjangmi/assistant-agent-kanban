from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import re
import subprocess
import uuid

from ..enums import STATE_ORDER, TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..log_parser import render_opencode_log
from ..models import (
    ChangedFileDetail,
    ChangedFileHunk,
    ChangedFileLine,
    ChangedFileRow,
    ChangedFileSide,
    ChangedFileSummary,
    HumanReviewState,
    StageTimingSegment,
    StageTimingSummary,
    TaskDetail,
    TaskLogEntry,
    TaskLogs,
    TaskMetadata,
    TaskStageTiming,
)
from ..scanner import KanbanScanner
from ..target_repo_guard import resolve_safe_target_repo_root


HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<header>.*)$")
ARTIFACT_CYCLE_RE = re.compile(r"^(WORK|REVIEW|HUMAN-VERIFY)-(?P<cycle>\d{3})\.md$")
ATTACHMENT_NAME_RE = re.compile(r"[^a-zA-Z0-9]+")
ALLOWED_ATTACHMENT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


PatchFileState = dict[str, str | int | bool | list[ChangedFileHunk] | None]


class TaskService:
    def __init__(self, scanner: KanbanScanner, runs_root: Path, kanban_root: Path) -> None:
        self.scanner = scanner
        self.runs_root = runs_root
        self.kanban_root = kanban_root

    def get_task(self, task_id: str) -> TaskDetail:
        task = self._find_task(task_id)
        request_markdown_path = str((task.task_dir / task.metadata.request.path).resolve())
        markdown_files = self._sorted_markdown_files(task.task_dir)
        json_files = sorted(path.name for path in task.task_dir.glob("*.json") if path.name != "metadata.json")
        log_dir = self.runs_root / task.metadata.task_id
        log_files = sorted(path.name for path in log_dir.glob("*")) if log_dir.exists() else []
        changed_files = self._load_changed_files(task.metadata.task_id, require_available=False)
        return TaskDetail(
            metadata=task.metadata,
            task_path=str(task.task_dir),
            request_markdown_path=request_markdown_path,
            markdown_files=markdown_files,
            json_files=json_files,
            log_files=log_files,
            changed_files=[entry.summary for entry in changed_files],
            stage_timing=self._build_stage_timing(task.metadata),
            human_review=self._build_human_review_state(task.metadata),
        )

    def get_logs(self, task_id: str) -> TaskLogs:
        try:
            task = self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc
        log_dir = self.runs_root / task.metadata.task_id
        entries: list[TaskLogEntry] = []
        if log_dir.exists():
            paths = sorted(
                [path for path in log_dir.glob("*.jsonl") if path.is_file()],
                key=lambda path: path.stat().st_mtime,
                reverse=False,
            )
            for path in paths:
                raw_content = path.read_text()
                rendered_content = render_opencode_log(raw_content)
                debug_rendered_content = render_opencode_log(raw_content, debug=True)
                entries.append(
                    TaskLogEntry(
                        name=path.name,
                        path=str(path),
                        rendered_content=rendered_content or None,
                        debug_rendered_content=debug_rendered_content or None,
                        updated_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                    )
                )
        return TaskLogs(task_id=task.metadata.task_id, entries=entries)

    def get_changed_file(self, task_id: str, changed_file_id: str) -> ChangedFileDetail:
        task = self._find_task(task_id)
        changed_files = self._load_changed_files_for_task(task, require_available=True)
        for entry in changed_files:
            if entry.summary.id == changed_file_id:
                return entry
        raise TaskNotFoundError(changed_file_id)

    def get_markdown_artifact(self, task_id: str, filename: str) -> str:
        task = self._find_task(task_id)
        path = self._validate_readable_markdown_artifact(task.task_dir, filename)
        return path.read_text()

    def update_markdown_artifact(self, task_id: str, filename: str, content: str) -> None:
        task = self._find_task(task_id)
        if task.state != "waiting-check-plans":
            raise TransitionError("markdown editing is only allowed in waiting-check-plans")
        path = self._validate_writable_markdown_artifact(task.task_dir, filename)
        if not content.strip():
            raise TransitionError("PLAN.md cannot be empty")
        path.write_text(content.rstrip() + "\n")

    def save_attachment(self, task_id: str, artifact_filename: str, upload_name: str, content_type: str | None, data: bytes) -> dict[str, str]:
        task = self._find_task(task_id)
        if task.state != "waiting-check-plans":
            raise TransitionError("attachments are only allowed in waiting-check-plans")
        self._validate_writable_markdown_artifact(task.task_dir, artifact_filename)
        suffix = Path(upload_name).suffix.lower()
        if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
            raise TransitionError("only png, jpg, jpeg, gif, and webp attachments are supported")
        if content_type and not content_type.startswith("image/"):
            raise TransitionError("only image attachments are supported")
        attachments_dir = self._attachments_dir(task.task_dir, create=True)
        stem = ATTACHMENT_NAME_RE.sub("-", Path(upload_name).stem.lower()).strip("-") or "image"
        stored_name = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
        path = attachments_dir / stored_name
        path.write_bytes(data)
        return {
            "filename": stored_name,
            "content_type": content_type or mimetypes.guess_type(stored_name)[0] or "application/octet-stream",
            "relative_path": f"_attachments/{stored_name}",
            "url": f"/api/tasks/{task_id}/attachments/{stored_name}",
        }

    def get_attachment(self, task_id: str, filename: str) -> tuple[Path, str]:
        task = self._find_task(task_id)
        path = self._validate_readable_attachment(task.task_dir, filename)
        return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def _find_task(self, task_id: str):
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _validate_readable_markdown_artifact(self, task_dir: Path, filename: str) -> Path:
        if not filename.endswith(".md"):
            raise TransitionError("only markdown artifacts can be viewed")
        path = (task_dir / filename).resolve()
        if path.parent != task_dir.resolve() or not path.exists():
            raise TaskNotFoundError(filename)
        return path

    def _validate_writable_markdown_artifact(self, task_dir: Path, filename: str) -> Path:
        if filename != "PLAN.md":
            raise TransitionError("only PLAN.md is editable")
        return self._validate_readable_markdown_artifact(task_dir, filename)

    def _attachments_dir(self, task_dir: Path, *, create: bool = False) -> Path:
        path = task_dir / "_attachments"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        resolved_task_dir = task_dir.resolve()
        resolved = path.resolve()
        if resolved.parent != resolved_task_dir or resolved.name != "_attachments":
            raise TransitionError("attachments directory must stay inside the task directory")
        return resolved

    def _validate_readable_attachment(self, task_dir: Path, filename: str) -> Path:
        attachments_dir = self._attachments_dir(task_dir)
        path = (attachments_dir / filename).resolve()
        if path.parent != attachments_dir or not path.exists():
            raise TaskNotFoundError(filename)
        return path

    def _sorted_markdown_files(self, task_dir: Path) -> list[str]:
        files = [path.name for path in task_dir.glob("*.md")]
        return sorted(files, key=self._artifact_sort_key)

    def _build_stage_timing(self, metadata: TaskMetadata) -> TaskStageTiming:
        summaries_by_state: dict[TaskState, StageTimingSummary] = {
            state: StageTimingSummary(state=state)
            for state in STATE_ORDER
        }
        if not metadata.history:
            return TaskStageTiming(summaries=list(summaries_by_state.values()))

        history = sorted(metadata.history, key=lambda entry: entry.entered_at)
        now = datetime.now(timezone.utc)
        segments: list[StageTimingSegment] = []
        visit_counts: dict[TaskState, int] = {state: 0 for state in STATE_ORDER}
        total_duration_ms = 0

        for index, entry in enumerate(history):
            next_entry = history[index + 1] if index + 1 < len(history) else None
            exited_at = next_entry.entered_at if next_entry else None
            end_time = exited_at or now
            duration_ms = max(0, int((end_time - entry.entered_at).total_seconds() * 1000))
            is_current = next_entry is None and entry.state == metadata.state
            visit_counts[entry.state] = visit_counts.get(entry.state, 0) + 1
            segment = StageTimingSegment(
                state=entry.state,
                entered_at=entry.entered_at,
                exited_at=exited_at,
                duration_ms=duration_ms,
                visit_index=visit_counts[entry.state],
                is_current=is_current,
            )
            segments.append(segment)
            summary = summaries_by_state.setdefault(entry.state, StageTimingSummary(state=entry.state))
            summary.total_duration_ms += duration_ms
            summary.latest_duration_ms = duration_ms
            summary.latest_entered_at = entry.entered_at
            summary.attempt_count += 1
            summary.is_current = is_current
            total_duration_ms += duration_ms

        ordered_summaries = [summaries_by_state[state] for state in STATE_ORDER]
        return TaskStageTiming(
            total_duration_ms=total_duration_ms,
            summaries=ordered_summaries,
            segments=segments,
        )

    def _build_human_review_state(self, metadata: TaskMetadata) -> HumanReviewState:
        unresolved_count = len(metadata.human_verification.comments)
        return HumanReviewState(
            note_path=metadata.human_verification.note_path,
            note_markdown=metadata.human_verification.note_markdown,
            unresolved_comment_count=unresolved_count,
            approval_block_reason="Resolve all file comments before approval." if unresolved_count else None,
        )

    def _artifact_sort_key(self, filename: str) -> tuple[int, int, int, str]:
        if filename == "REQUEST.md":
            return (0, 0, 0, filename)
        if filename == "PLAN.md":
            return (1, 0, 0, filename)
        match = ARTIFACT_CYCLE_RE.match(filename)
        if match:
            kind = match.group(1)
            cycle = int(match.group("cycle"))
            kind_order = {"WORK": 0, "REVIEW": 1, "HUMAN-VERIFY": 2}[kind]
            return (2, cycle, kind_order, filename)
        if filename == "COMMIT.md":
            return (4, 0, 0, filename)
        return (3, 0, 0, filename)

    def _load_changed_files(self, task_id: str, *, require_available: bool) -> list[ChangedFileDetail]:
        task = self._find_task(task_id)
        return self._load_changed_files_for_task(task, require_available=require_available)

    def _load_changed_files_for_task(self, task, *, require_available: bool) -> list[ChangedFileDetail]:
        if task.metadata.state not in {"human-verifying", "done"}:
            if require_available:
                raise TransitionError("changed files are only available during or after human verification")
            return []
        patch_text = self._resolve_changed_files_patch(task.metadata, require_available=require_available)
        if patch_text is None:
            return []
        return self._apply_human_review_comments(self._parse_patch(patch_text), task.metadata)

    def _resolve_changed_files_patch(self, metadata: TaskMetadata, *, require_available: bool) -> str | None:
        if metadata.integration.patch_path:
            try:
                self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
            except TransitionError:
                if require_available:
                    raise
                return None
        if metadata.state == TaskState.HUMAN_VERIFYING:
            patch_text = self._target_repo_diff_against_base(metadata, ref=None)
            if patch_text is not None:
                return patch_text
        try:
            patch_path = self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
        except TransitionError:
            if require_available:
                raise
            return None
        if patch_path is None or not patch_path.exists():
            if require_available:
                raise TaskNotFoundError(f"patch for {metadata.task_id}")
            return None
        return patch_path.read_text()

    def _target_repo_diff_against_base(self, metadata: TaskMetadata, *, ref: str | None) -> str | None:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError:
            return None
        range_spec = metadata.target.base_branch if ref is None else f"{metadata.target.base_branch}..{ref}"
        command = ["git", "-C", str(target_repo_root), "diff", "--binary", range_spec]
        diff = subprocess.run(command, capture_output=True, text=True, check=False)
        if diff.returncode != 0:
            return None
        return diff.stdout

    def _apply_human_review_comments(self, details: list[ChangedFileDetail], metadata: TaskMetadata) -> list[ChangedFileDetail]:
        comments_by_path: dict[str, list] = {}
        for comment in metadata.human_verification.comments:
            comments_by_path.setdefault(comment.file_path, []).append(comment)
        for detail in details:
            file_comments = comments_by_path.get(detail.summary.path, [])
            detail.summary.comment_count = len(file_comments)
            detail.comments = list(file_comments)
        return details

    def _resolve_patch_path(self, task_id: str, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        patch_path = Path(raw_path).expanduser()
        if patch_path.is_absolute():
            resolved = patch_path.resolve()
        else:
            resolved = (self.kanban_root.expanduser().resolve().parent / patch_path).resolve()
        managed_root = (self.runs_root / task_id).resolve()
        try:
            resolved.relative_to(managed_root)
        except ValueError as exc:
            raise TransitionError("changed files are unavailable because patch path is outside the managed runs root") from exc
        return resolved

    def _parse_patch(self, patch_text: str) -> list[ChangedFileDetail]:
        details: list[ChangedFileDetail] = []
        current_file: PatchFileState | None = None
        current_hunk: ChangedFileHunk | None = None
        old_line_number = 0
        new_line_number = 0
        pending_removals: list[ChangedFileLine] = []
        pending_additions: list[ChangedFileLine] = []

        def file_int(key: str) -> int:
            if current_file is None:
                return 0
            value = current_file.get(key)
            return value if isinstance(value, int) else 0

        def file_str(key: str) -> str | None:
            if current_file is None:
                return None
            value = current_file.get(key)
            return value if isinstance(value, str) else None

        def file_bool(key: str) -> bool:
            if current_file is None:
                return False
            value = current_file.get(key)
            return value if isinstance(value, bool) else False

        def file_hunks() -> list[ChangedFileHunk]:
            if current_file is None:
                return []
            value = current_file.get("hunks")
            return value if isinstance(value, list) else []

        def empty_side() -> ChangedFileSide:
            return ChangedFileSide(kind="empty")

        def side_from_line(line: ChangedFileLine) -> ChangedFileSide:
            line_number = line.old_line_number if line.kind != "add" else line.new_line_number
            return ChangedFileSide(kind=line.kind, line_number=line_number, content=line.content)

        def flush_pending() -> None:
            nonlocal pending_removals, pending_additions
            if current_hunk is None:
                pending_removals = []
                pending_additions = []
                return
            pair_count = max(len(pending_removals), len(pending_additions))
            for index in range(pair_count):
                left_line = pending_removals[index] if index < len(pending_removals) else None
                right_line = pending_additions[index] if index < len(pending_additions) else None
                current_hunk.rows.append(
                    ChangedFileRow(
                        left=side_from_line(left_line) if left_line else empty_side(),
                        right=side_from_line(right_line) if right_line else empty_side(),
                    )
                )
            pending_removals = []
            pending_additions = []

        def finish_hunk() -> None:
            nonlocal current_hunk
            if current_hunk is None:
                return
            flush_pending()
            if current_file is not None:
                file_hunks().append(current_hunk)
            current_hunk = None

        def finish_file() -> None:
            nonlocal current_file
            if current_file is None:
                return
            finish_hunk()
            old_path = file_str("old_path") or ""
            new_path = file_str("new_path") or ""
            renamed_from = file_str("rename_from")
            renamed_to = file_str("rename_to")
            if old_path == "/dev/null":
                path = new_path
                display_path = new_path
                previous_path = None
                change_type = "added"
            elif new_path == "/dev/null":
                path = old_path
                display_path = old_path
                previous_path = None
                change_type = "deleted"
            elif isinstance(renamed_from, str) and isinstance(renamed_to, str) and renamed_from != renamed_to:
                path = renamed_to
                display_path = f"{renamed_from} -> {renamed_to}"
                previous_path = renamed_from
                change_type = "renamed"
            else:
                path = new_path or old_path
                display_path = path
                previous_path = None
                change_type = "modified"
            additions = file_int("additions")
            deletions = file_int("deletions")
            hunks = file_hunks()
            summary = ChangedFileSummary(
                id=str(len(details)),
                path=path,
                display_path=display_path,
                previous_path=previous_path,
                change_type=change_type,
                additions=additions,
                deletions=deletions,
                hunk_count=len(hunks),
                is_binary=file_bool("is_binary"),
            )
            details.append(ChangedFileDetail(summary=summary, hunks=hunks.copy()))
            current_file = None

        for raw_line in patch_text.splitlines():
            if raw_line.startswith("diff --git "):
                finish_file()
                old_path, new_path = self._parse_diff_header(raw_line)
                current_file = {
                    "old_path": old_path,
                    "new_path": new_path,
                    "rename_from": None,
                    "rename_to": None,
                    "additions": 0,
                    "deletions": 0,
                    "hunks": [],
                    "is_binary": False,
                }
                continue
            if current_file is None:
                continue
            if raw_line.startswith("rename from "):
                current_file["rename_from"] = raw_line.removeprefix("rename from ")
                continue
            if raw_line.startswith("rename to "):
                current_file["rename_to"] = raw_line.removeprefix("rename to ")
                continue
            if raw_line.startswith("--- "):
                current_file["old_path"] = self._normalize_patch_path(raw_line.removeprefix("--- "))
                continue
            if raw_line.startswith("+++ "):
                current_file["new_path"] = self._normalize_patch_path(raw_line.removeprefix("+++ "))
                continue
            if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
                current_file["is_binary"] = True
                continue
            match = HUNK_HEADER_RE.match(raw_line)
            if match:
                finish_hunk()
                old_line_number = int(match.group("old_start"))
                new_line_number = int(match.group("new_start"))
                current_hunk = ChangedFileHunk(
                    header=raw_line,
                    old_start=old_line_number,
                    new_start=new_line_number,
                )
                continue
            if current_hunk is None:
                continue
            if raw_line.startswith("\\ No newline at end of file"):
                continue
            prefix = raw_line[:1]
            content = raw_line[1:] if raw_line else ""
            if prefix == " ":
                flush_pending()
                line = ChangedFileLine(
                    kind="context",
                    old_line_number=old_line_number,
                    new_line_number=new_line_number,
                    content=content,
                )
                current_hunk.unified_lines.append(line)
                current_hunk.rows.append(
                    ChangedFileRow(
                        left=ChangedFileSide(kind="context", line_number=old_line_number, content=content),
                        right=ChangedFileSide(kind="context", line_number=new_line_number, content=content),
                    )
                )
                old_line_number += 1
                new_line_number += 1
                continue
            if prefix == "-":
                line = ChangedFileLine(kind="remove", old_line_number=old_line_number, content=content)
                current_hunk.unified_lines.append(line)
                pending_removals.append(line)
                current_file["deletions"] = file_int("deletions") + 1
                old_line_number += 1
                continue
            if prefix == "+":
                line = ChangedFileLine(kind="add", new_line_number=new_line_number, content=content)
                current_hunk.unified_lines.append(line)
                pending_additions.append(line)
                current_file["additions"] = file_int("additions") + 1
                new_line_number += 1
        finish_file()
        return details

    def _parse_diff_header(self, raw_line: str) -> tuple[str, str]:
        parts = raw_line.split(" ", 3)
        if len(parts) < 4:
            return "", ""
        suffix = parts[3]
        old_token, _, new_token = suffix.partition(" b/")
        old_path = self._normalize_patch_path(old_token)
        new_path = self._normalize_patch_path(f"b/{new_token}" if new_token else "")
        return old_path, new_path

    def _normalize_patch_path(self, raw_path: str) -> str:
        path = raw_path.strip()
        if path in {"/dev/null", ""}:
            return path
        if path.startswith("a/") or path.startswith("b/"):
            return path[2:]
        return path
