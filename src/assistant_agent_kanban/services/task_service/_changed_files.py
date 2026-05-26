from __future__ import annotations

from collections import OrderedDict
import hashlib
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING

from ...enums import TaskState
from ...exceptions import TaskNotFoundError, TransitionError
from ...models import (
    ChangedFileDetail,
    ChangedFileHunk,
    ChangedFileLine,
    ChangedFileRow,
    ChangedFileSide,
    ChangedFileSummary,
    HumanLineComment,
    HumanLineCommentsArtifact,
    HumanQaChecklistItem,
    TaskMetadata,
)
from ...target_repo_guard import resolve_safe_target_repo_root

from ._data import HUNK_HEADER_RE, PatchFileState

if TYPE_CHECKING:
    from ._protocol import _TaskServiceLike
else:
    _TaskServiceLike = object


class _ChangedFilesMixin(_TaskServiceLike):
    def get_changed_file(self, task_id: str, changed_file_id: str) -> ChangedFileDetail:
        task = self._find_task(task_id)
        changed_files = self._load_changed_files_for_task(task, require_available=True)
        for entry in changed_files:
            if entry.summary.id == changed_file_id:
                return entry.model_copy(update={"comments": self._comments_for_file(task, entry.summary.path)})
        raise TaskNotFoundError(changed_file_id)


    def get_changed_file_by_path(self, task_id: str, path: str) -> ChangedFileDetail:
        task = self._find_task(task_id)
        changed_files = self._load_changed_files_for_task(task, require_available=True)
        for entry in changed_files:
            if entry.summary.path == path:
                return entry.model_copy(update={"comments": self._comments_for_file(task, entry.summary.path)})
        raise TaskNotFoundError(path)


    def set_changed_file_viewed(self, task_id: str, changed_file_id: str, *, by: str, viewed: bool) -> ChangedFileSummary:
        task = self._find_task(task_id)
        if task.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("changed file viewed state is only available during human verification")
        if self.metadata_store is None or self.locks is None:
            raise TransitionError("changed file viewed updates require a configured runtime")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-changed-file-viewed"):
            changed_files = self._load_changed_files_for_task(task, require_available=True)
            for entry in changed_files:
                if entry.summary.id != changed_file_id:
                    continue
                viewed_files = self._current_cycle_viewed_files(task.metadata, create=True)
                if viewed:
                    viewed_files[entry.summary.path] = True
                else:
                    viewed_files.pop(entry.summary.path, None)
                self.metadata_store.save(task.task_dir, task.metadata)
                return entry.summary.model_copy(update={"viewed": viewed})
        raise TaskNotFoundError(changed_file_id)


    def set_human_qa_item_state(
        self,
        task_id: str,
        item_id: str,
        *,
        by: str,
        checked: bool | None = None,
        skipped: bool | None = None,
        note: str | None = None,
    ) -> HumanQaChecklistItem:
        task = self._find_task(task_id)
        if task.state != TaskState.HUMAN_VERIFYING:
            raise TransitionError("QA checklist updates are only available during human verification")
        if self.metadata_store is None or self.locks is None:
            raise TransitionError("QA checklist updates require a configured runtime")
        with self.locks.acquire(task.task_dir, task.metadata, owner=by, run_id="manual-human-qa-checklist"):
            human_verification = task.metadata.human_verification
            if human_verification.qa_cycle != task.metadata.cycle or not human_verification.qa_items:
                raise TaskNotFoundError(item_id)
            for index, item in enumerate(human_verification.qa_items):
                if item.id != item_id:
                    continue
                next_checked = item.checked if checked is None else checked
                next_skipped = item.skipped if skipped is None else skipped
                if next_checked and next_skipped:
                    next_skipped = False
                normalized_note = item.note if note is None else (note.strip() or None)
                updated = item.model_copy(update={"checked": next_checked, "skipped": next_skipped, "note": normalized_note})
                human_verification.qa_items[index] = updated
                self.metadata_store.save(task.task_dir, task.metadata)
                return updated
        raise TaskNotFoundError(item_id)


    def _load_changed_files(self, task_id: str, *, require_available: bool) -> list[ChangedFileDetail]:
        task = self._find_task(task_id)
        return self._load_changed_files_for_task(task, require_available=require_available)


    def _changed_files_available_for_task(self, task) -> bool:
        metadata = task.metadata
        if metadata.state not in {TaskState.HUMAN_VERIFYING, TaskState.DONE}:
            return False
        if metadata.integration.patch_path:
            try:
                self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
            except TransitionError:
                return False
        if metadata.state == TaskState.HUMAN_VERIFYING:
            return True
        patch_path = self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
        return bool(patch_path and patch_path.exists())


    def _load_changed_files_for_task(self, task, *, require_available: bool) -> list[ChangedFileDetail]:
        if task.metadata.state not in {"human-verifying", "done"}:
            if require_available:
                raise TransitionError("changed files are only available during or after human verification")
            return []
        patch_text = self._resolve_changed_files_patch(task.metadata, require_available=require_available)
        if patch_text is None:
            return []
        return self._apply_changed_file_viewed_state(self._parse_patch(patch_text), task.metadata)


    def _apply_changed_file_viewed_state(self, details: list[ChangedFileDetail], metadata: TaskMetadata) -> list[ChangedFileDetail]:
        viewed_files = self._current_cycle_viewed_files(metadata)
        if not viewed_files:
            return details
        return [
            entry.model_copy(update={"summary": entry.summary.model_copy(update={"viewed": bool(viewed_files.get(entry.summary.path))})})
            for entry in details
        ]


    def _current_cycle_viewed_files(self, metadata: TaskMetadata, *, create: bool = False) -> dict[str, bool]:
        human_verification = metadata.human_verification
        if human_verification.viewed_cycle != metadata.cycle:
            if not create:
                return {}
            human_verification.viewed_cycle = metadata.cycle
            human_verification.viewed_files = {}
        return human_verification.viewed_files


    def _resolve_changed_files_patch(self, metadata: TaskMetadata, *, require_available: bool) -> str | None:
        if metadata.integration.patch_path:
            try:
                self._resolve_patch_path(metadata.task_id, metadata.integration.patch_path)
            except TransitionError:
                if require_available:
                    raise
                return None
        if metadata.state == TaskState.HUMAN_VERIFYING and metadata.integration.applied:
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


    def _load_current_cycle_line_comments(self, task, metadata: TaskMetadata) -> list[HumanLineComment]:
        comments_path = self._resolve_comments_path(task.task_dir, metadata)
        if comments_path is None or not comments_path.exists():
            return []
        artifact = HumanLineCommentsArtifact.model_validate_json(comments_path.read_text())
        return [self._annotate_comment(comment, metadata.cycle, editable=True) for comment in artifact.comments]


    def _load_all_line_comments(self, task, metadata: TaskMetadata) -> list[HumanLineComment]:
        comments_by_id: OrderedDict[str, HumanLineComment] = OrderedDict()
        for path in sorted(task.task_dir.glob("HUMAN-VERIFY-*.comments.json")):
            cycle = self._cycle_from_comment_artifact_name(path.name)
            if cycle is None:
                continue
            artifact = HumanLineCommentsArtifact.model_validate_json(path.read_text())
            editable = cycle == metadata.cycle
            for comment in artifact.comments:
                annotated = self._annotate_comment(comment, cycle, editable=editable)
                comments_by_id[annotated.id] = annotated
        return list(comments_by_id.values())


    def _annotate_comment(self, comment: HumanLineComment, cycle: int, *, editable: bool) -> HumanLineComment:
        return comment.model_copy(update={"cycle": comment.cycle or cycle, "editable": editable})


    def _resolve_comments_path(self, task_dir: Path, metadata: TaskMetadata) -> Path | None:
        raw_path = metadata.human_verification.comments_path
        if not raw_path and metadata.human_verification.note_path:
            raw_path = f"{metadata.human_verification.note_path[:-3]}.comments.json"
        if not raw_path:
            return None
        resolved_task_dir = task_dir.resolve()
        resolved = (resolved_task_dir / raw_path).resolve()
        if resolved.parent != resolved_task_dir:
            raise TransitionError("human verification comments are unavailable because comments path is outside the task directory")
        return resolved


    def _comments_for_file(self, task, path: str) -> list[HumanLineComment]:
        return [comment for comment in self._load_all_line_comments(task, task.metadata) if comment.anchor.path == path]


    def _cycle_from_comment_artifact_name(self, filename: str) -> int | None:
        match = re.match(r"^HUMAN-VERIFY-(\d{3})\.comments\.json$", filename)
        if not match:
            return None
        return int(match.group(1))


    def _target_repo_diff_against_base(self, metadata: TaskMetadata, *, ref: str | None) -> str | None:
        if metadata.integration.verification_repo_root:
            target_repo_root = Path(metadata.integration.verification_repo_root).expanduser().resolve()
            if not target_repo_root.exists():
                return None
        else:
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



    def _resolve_patch_path(self, task_id: str, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        patch_path = Path(raw_path).expanduser()
        if patch_path.is_absolute():
            resolved = patch_path.resolve()
        else:
            resolved = (self.kanban_root.expanduser().resolve().parent / patch_path).resolve()
        managed_roots = [
            (self.runs_root / task_id).resolve(),
            (self.archive_runs_root / task_id).resolve(),
        ]
        for managed_root in managed_roots:
            try:
                resolved.relative_to(managed_root)
                return resolved
            except ValueError:
                continue
        raise TransitionError("changed files are unavailable because patch path is outside the managed runs roots")
        return resolved


    def _task_runs_dir(self, task_id: str) -> Path:
        live_dir = self.runs_root / task_id
        if live_dir.exists():
            return live_dir
        return self.archive_runs_root / task_id


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
                id=self._changed_file_id(path, previous_path),
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


    def _changed_file_id(self, path: str, previous_path: str | None) -> str:
        stable_key = f"{previous_path or ''}\0{path}"
        return hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:12]


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
