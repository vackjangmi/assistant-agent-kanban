from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from ..config import AppConfig
from ..enums import CANCELABLE_STATES, TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..integration_manager import IntegrationManager
from ..locks import TaskLockManager
from ..models import TaskContext, TaskMetadata, WorkerLease, utc_now
from ..retry_policy import clear_retry_gate
from ..scanner import KanbanScanner
from ..target_repo_guard import resolve_safe_target_repo_root
from ..transitions import TransitionManager


class TaskCancellationService:
    def __init__(
        self,
        config: AppConfig,
        scanner: KanbanScanner,
        locks: TaskLockManager,
        transitions: TransitionManager,
        integration_manager: IntegrationManager,
    ) -> None:
        self.config = config
        self.scanner = scanner
        self.locks = locks
        self.transitions = transitions
        self.integration_manager = integration_manager

    def cancel(self, task_id: str, *, by: str, note: str | None = None) -> TaskContext:
        with self.locks.acquire_by_task_id(task_id, owner=by, run_id="manual-cancel"):
            context = self._find_task(task_id)
            if context.state not in CANCELABLE_STATES:
                raise TransitionError(f"task cancellation is not allowed from {context.state.value}")
            archive_paths = self._archive_workspace_changes(context)
            self._prepare_for_cancellation(context)
            self._delete_tree(self._workspace_root(task_id, context.metadata))
            context.metadata.implementation.workspace = None
            context.metadata.implementation.branch = None
            context.metadata.closure.reason = "cancelled_by_human"
            context.metadata.closure.closed_by = by
            context.metadata.closure.closed_at = utc_now()
            context.metadata.closure.note = self._closure_note(note, archive_paths)
            context.metadata.lease = WorkerLease()
            clear_retry_gate(context.metadata)
            self.scanner.metadata_store.save(context.task_dir, context.metadata)
            return self.transitions.move(context, target=TaskState.CLOSED, by=by, note="cancelled by human")

    def _find_task(self, task_id: str) -> TaskContext:
        try:
            return self.scanner.find_task(task_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _prepare_for_cancellation(self, context: TaskContext) -> None:
        self._validate_managed_workspace(context.metadata)
        self._validate_managed_patch(context.metadata)
        if context.metadata.integration.applied:
            self.integration_manager.rollback_workspace(context.metadata)
            self._delete_target_repo_docs(context.metadata)

    def _archive_workspace_changes(self, context: TaskContext) -> list[str]:
        repo_dir = self._workspace_repo(context.metadata)
        if repo_dir is None or not repo_dir.exists():
            return []
        archive_root = context.task_dir / "CANCELLED-WORKSPACE"
        shutil.rmtree(archive_root, ignore_errors=True)
        archive_root.mkdir(parents=True)
        archived_paths: list[str] = []
        if (repo_dir / ".git").exists():
            archived_paths = self._archive_git_workspace(repo_dir, archive_root, context.metadata)
        else:
            archived_paths = self._archive_plain_workspace(repo_dir, archive_root, context.metadata)
        if not archived_paths:
            shutil.rmtree(archive_root, ignore_errors=True)
            return []
        summary_path = archive_root / "README.md"
        summary_path.write_text(self._archive_summary(context, archived_paths))
        return [str((archive_root / path).relative_to(context.task_dir)) for path in ["README.md", *archived_paths]]

    def _archive_git_workspace(self, repo_dir: Path, archive_root: Path, metadata: TaskMetadata) -> list[str]:
        base_ref = self._resolve_git_ref(repo_dir, metadata.target.base_branch) or "HEAD"
        status = self._git_output(repo_dir, "status", "--porcelain=v1", "--untracked-files=all")
        diff = self._git_output(repo_dir, "diff", "--binary", base_ref, "--")
        untracked = self._git_bytes(repo_dir, "ls-files", "--others", "--exclude-standard", "-z")
        archived: list[str] = []
        if status.strip():
            (archive_root / "status.txt").write_text(status)
            archived.append("status.txt")
        if diff.strip():
            (archive_root / "changes.patch").write_text(diff)
            archived.append("changes.patch")
        copied = self._copy_paths_from_nul_output(repo_dir, archive_root / "files", untracked)
        if copied:
            (archive_root / "untracked-files.txt").write_text("\n".join(copied) + "\n")
            archived.extend(["untracked-files.txt", "files"])
        return archived

    def _archive_plain_workspace(self, repo_dir: Path, archive_root: Path, metadata: TaskMetadata) -> list[str]:
        target_root = Path(metadata.target.repo_root).expanduser().resolve()
        copied: list[str] = []
        deleted: list[str] = []
        files_root = archive_root / "files"
        for source_path in sorted(path for path in repo_dir.rglob("*") if path.is_file()):
            relative = source_path.relative_to(repo_dir)
            target_path = target_root / relative
            if not target_path.exists() or source_path.read_bytes() != target_path.read_bytes():
                destination = files_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
                copied.append(relative.as_posix())
        if target_root.exists():
            for target_path in sorted(path for path in target_root.rglob("*") if path.is_file()):
                relative = target_path.relative_to(target_root)
                if not (repo_dir / relative).exists():
                    deleted.append(relative.as_posix())
        archived: list[str] = []
        if copied:
            (archive_root / "changed-files.txt").write_text("\n".join(copied) + "\n")
            archived.extend(["changed-files.txt", "files"])
        if deleted:
            (archive_root / "deleted-files.txt").write_text("\n".join(deleted) + "\n")
            archived.append("deleted-files.txt")
        return archived

    def _archive_summary(self, context: TaskContext, archived_paths: list[str]) -> str:
        lines = [
            "# Cancelled Workspace Archive",
            "",
            f"- Task ID: `{context.metadata.task_id}`",
            f"- Previous state: `{context.state.value}`",
            f"- Cancelled at: `{utc_now().isoformat()}`",
            "",
            "Archived files:",
            *[f"- `{path}`" for path in archived_paths],
            "",
        ]
        return "\n".join(lines)

    def _closure_note(self, note: str | None, archive_paths: list[str]) -> str:
        parts: list[str] = []
        if note:
            parts.append(note.strip())
        if archive_paths:
            parts.append("Cancelled workspace changes were archived under `CANCELLED-WORKSPACE/`.")
        return "\n\n".join(part for part in parts if part) or "Cancelled by human."

    def _workspace_repo(self, metadata: TaskMetadata) -> Path | None:
        workspace_path = metadata.implementation.workspace
        if workspace_path is None:
            expected_root = self._workspace_root(metadata.task_id, metadata)
            repo_dir = expected_root / "repo"
            return repo_dir if repo_dir.exists() else None
        resolved = Path(workspace_path).expanduser().resolve()
        expected_root = self._workspace_root(metadata.task_id, metadata).resolve()
        try:
            resolved.relative_to(expected_root)
        except ValueError as exc:
            raise TransitionError("task cancellation is blocked because workspace path is outside the managed workspace root") from exc
        return resolved

    def _workspace_root(self, task_id: str, metadata: TaskMetadata) -> Path:
        workspace_root = metadata.implementation.workspace
        expected_root = (self.config.workspace.root or (self.config.kanban_root / "_runtime/workspaces")) / task_id
        if workspace_root is None:
            return expected_root
        resolved = Path(workspace_root).expanduser().resolve()
        try:
            resolved.relative_to(expected_root.resolve())
        except ValueError as exc:
            raise TransitionError("task cancellation is blocked because workspace path is outside the managed workspace root") from exc
        return expected_root

    def _validate_managed_workspace(self, metadata: TaskMetadata) -> None:
        self._workspace_root(metadata.task_id, metadata)

    def _validate_managed_patch(self, metadata: TaskMetadata) -> None:
        if not metadata.integration.patch_path:
            return
        patch_path = Path(metadata.integration.patch_path).expanduser().resolve()
        managed_roots = [
            (self.config.runs_dir / metadata.task_id).resolve(),
            (self.config.archive_runs_dir / metadata.task_id).resolve(),
        ]
        for managed_root in managed_roots:
            try:
                patch_path.relative_to(managed_root)
                return
            except ValueError:
                continue
        raise TransitionError("task cancellation is blocked because patch path is outside the managed runs roots")

    def _delete_tree(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def _delete_target_repo_docs(self, metadata: TaskMetadata) -> None:
        try:
            target_repo_root = resolve_safe_target_repo_root(Path(metadata.target.repo_root))
        except ValueError:
            return
        try:
            docs_root = self.config.resolve_target_repo_docs_root(target_repo_root)
        except ValueError as exc:
            raise TransitionError(str(exc)) from exc
        if not docs_root.exists():
            return
        for candidate in docs_root.glob(f"*/*/*/{metadata.task_id}"):
            resolved = candidate.resolve()
            try:
                resolved.relative_to(docs_root.resolve())
            except ValueError as exc:
                raise TransitionError("task cancellation is blocked because task docs path is outside the managed docs root") from exc
            self._delete_tree(resolved)
        for candidate in list(docs_root.glob(f"*/*/*/{metadata.task_id}-summary.md")) + list(
            docs_root.glob(f"*/*/*/{metadata.task_id}-*-summary.md")
        ):
            resolved = candidate.resolve()
            try:
                resolved.relative_to(docs_root.resolve())
            except ValueError as exc:
                raise TransitionError("task cancellation is blocked because task docs path is outside the managed docs root") from exc
            resolved.unlink(missing_ok=True)
            self._prune_empty_parents(resolved.parent, stop_at=docs_root)

    def _prune_empty_parents(self, path: Path, *, stop_at: Path) -> None:
        current = path
        resolved_stop = stop_at.resolve()
        while current.exists() and current.resolve() != resolved_stop:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _resolve_git_ref(self, repo_dir: Path, base_branch: str) -> str | None:
        for candidate in [base_branch, f"origin/{base_branch}", "HEAD"]:
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return candidate
        return None

    def _git_output(self, repo_dir: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return ""
        return result.stdout

    def _git_bytes(self, repo_dir: Path, *args: str) -> bytes:
        result = subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True, check=False)
        if result.returncode != 0:
            return b""
        return result.stdout

    def _copy_paths_from_nul_output(self, repo_dir: Path, destination_root: Path, output: bytes) -> list[str]:
        copied: list[str] = []
        for raw_path in [part for part in output.split(b"\0") if part]:
            relative = Path(raw_path.decode("utf-8", errors="replace"))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            source = (repo_dir / relative).resolve()
            try:
                source.relative_to(repo_dir.resolve())
            except ValueError:
                continue
            if not source.is_file():
                continue
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(relative.as_posix())
        return copied
