from __future__ import annotations

from contextlib import ExitStack
from datetime import timezone
import hashlib
import logging
import shutil
from pathlib import Path

from ..config import AppConfig
from ..enums import TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..locks import TaskLockManager
from ..metadata_store import slugify
from ..models import (
    ArchiveGroupDetail,
    ArchiveGroupManifest,
    ArchiveGroupSummary,
    ArchiveList,
    ArchiveTaskLocation,
    TaskContext,
    TaskMetadata,
    utc_now,
)
from ..scanner import KanbanScanner, target_repo_label
from ..target_repo_guard import resolve_safe_target_repo_root


logger = logging.getLogger(__name__)


class ArchiveService:
    def __init__(self, config: AppConfig, scanner: KanbanScanner, locks: TaskLockManager) -> None:
        self.config = config
        self.scanner = scanner
        self.locks = locks

    def list_groups(self) -> ArchiveList:
        groups = [self._summary_from_manifest(manifest) for manifest in self._iter_manifests()]
        groups.sort(key=lambda group: group.archived_at, reverse=True)
        return ArchiveList(groups=groups)

    def get_group(self, archive_id: str) -> ArchiveGroupDetail:
        archive_dir = self._archive_dir(archive_id)
        manifest = self._load_manifest(archive_dir)
        tasks: list[TaskContext] = []
        for location in manifest.task_locations:
            try:
                tasks.append(self._context_from_location(archive_dir, location))
            except FileNotFoundError:
                logger.info(
                    "archive skipped missing task metadata",
                    extra={"archive_id": archive_id, "task_id": location.task_id},
                )
        snapshots = [
            self.scanner._task_snapshot(task, task.state)
            for task in sorted(tasks, key=lambda item: item.metadata.updated_at, reverse=True)
        ]
        return ArchiveGroupDetail(
            **self._summary_from_manifest(manifest).model_dump(),
            tasks=snapshots,
        )

    def archive_done_group(self, target_repo_root: str, base_branch: str, *, by: str) -> ArchiveGroupManifest:
        normalized_branch = base_branch.strip()
        if not normalized_branch:
            raise TransitionError("archive requires a completed group")
        resolved_repo_root = resolve_safe_target_repo_root(Path(target_repo_root))
        group = self._active_done_group(resolved_repo_root, normalized_branch)
        if not group:
            raise TransitionError("archive requires at least one done task for the selected project and group")

        archived_at = utc_now()
        archive_id = self._next_archive_id(resolved_repo_root, normalized_branch, group, archived_at)
        self.config.archives_dir.mkdir(parents=True, exist_ok=True)
        final_archive_dir = self.config.archives_dir / archive_id
        staging_archive_dir = self.config.archives_dir / f".{archive_id}.tmp"
        if staging_archive_dir.exists():
            shutil.rmtree(staging_archive_dir)
        staging_archive_dir.mkdir(parents=True)

        moved: list[tuple[Path, Path]] = []
        try:
            with ExitStack() as stack:
                for task in sorted(group, key=lambda item: item.metadata.task_id):
                    stack.enter_context(self.locks.acquire_by_task_id(task.metadata.task_id, owner=by, run_id="manual-archive"))
                locations: list[ArchiveTaskLocation] = []
                done_root = self.config.state_dir(TaskState.DONE).resolve()
                for task in sorted(group, key=lambda item: item.metadata.task_id):
                    try:
                        active_relative = task.task_dir.resolve().relative_to(done_root)
                    except ValueError as exc:
                        raise TransitionError(f"task {task.metadata.task_id} is not in the active done directory") from exc
                    archive_relative = Path(TaskState.DONE.value) / active_relative
                    destination = staging_archive_dir / archive_relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(task.task_dir), str(destination))
                    moved.append((destination, task.task_dir))
                    locations.append(
                        ArchiveTaskLocation(
                            task_id=task.metadata.task_id,
                            state=TaskState.DONE,
                            relative_path=archive_relative.as_posix(),
                        )
                    )
                manifest = ArchiveGroupManifest(
                    archive_id=archive_id,
                    target_repo_root=str(resolved_repo_root),
                    target_repo_label=target_repo_label(str(resolved_repo_root)),
                    base_branch=normalized_branch,
                    task_count=len(locations),
                    task_ids=[location.task_id for location in locations],
                    task_locations=locations,
                    archived_at=archived_at,
                    archived_by=by,
                )
                self._write_manifest(staging_archive_dir, manifest)
            staging_archive_dir.replace(final_archive_dir)
            return manifest
        except Exception:
            for destination, original in reversed(moved):
                if destination.exists() and not original.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(original))
            if staging_archive_dir.exists():
                shutil.rmtree(staging_archive_dir)
            raise

    def find_task(self, task_id: str) -> TaskContext | None:
        for manifest in self._iter_manifests():
            location = next((item for item in manifest.task_locations if item.task_id == task_id), None)
            if location is None:
                continue
            return self._context_from_location(self.config.archives_dir / manifest.archive_id, location)
        return None

    def _active_done_group(self, target_repo_root: Path, base_branch: str) -> list[TaskContext]:
        tasks: list[TaskContext] = []
        for task in self.scanner.scan():
            if task.state != TaskState.DONE:
                continue
            if self._done_task_group_branch(task) != base_branch:
                continue
            if resolve_safe_target_repo_root(Path(task.metadata.target.repo_root)) != target_repo_root:
                continue
            tasks.append(task)
        return sorted(tasks, key=lambda item: item.metadata.task_id)

    def _done_task_group_branch(self, task: TaskContext) -> str:
        override = (task.metadata.completed_group_override or "").strip()
        return override or task.metadata.target.base_branch

    def _next_archive_id(self, target_repo_root: Path, base_branch: str, group: list[TaskContext], archived_at) -> str:
        stamp = archived_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = slugify(f"{target_repo_root.name}-{base_branch}") or "archive"
        digest_input = "|".join([str(target_repo_root), base_branch, *[task.metadata.task_id for task in group]])
        digest = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:8]
        candidate = f"{stamp}-{slug}-{digest}"
        archive_id = candidate
        suffix = 2
        while (self.config.archives_dir / archive_id).exists() or (self.config.archives_dir / f".{archive_id}.tmp").exists():
            archive_id = f"{candidate}-{suffix}"
            suffix += 1
        return archive_id

    def _iter_manifests(self) -> list[ArchiveGroupManifest]:
        if not self.config.archives_dir.exists():
            return []
        manifests: list[ArchiveGroupManifest] = []
        for archive_dir in sorted(path for path in self.config.archives_dir.iterdir() if path.is_dir() and not path.name.startswith(".")):
            try:
                manifests.append(self._load_manifest(archive_dir))
            except (FileNotFoundError, ValueError):
                logger.info("archive skipped invalid manifest", extra={"archive_dir": str(archive_dir)})
        return manifests

    def _archive_dir(self, archive_id: str) -> Path:
        normalized = archive_id.strip()
        if not normalized:
            raise TaskNotFoundError("archive")
        root = self.config.archives_dir.resolve()
        path = (root / normalized).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise TaskNotFoundError(normalized) from exc
        if not path.is_dir():
            raise TaskNotFoundError(normalized)
        return path

    def _load_manifest(self, archive_dir: Path) -> ArchiveGroupManifest:
        return ArchiveGroupManifest.model_validate_json((archive_dir / "archive.json").read_text())

    def _write_manifest(self, archive_dir: Path, manifest: ArchiveGroupManifest) -> None:
        target = archive_dir / "archive.json"
        tmp = archive_dir / "archive.json.tmp"
        tmp.write_text(manifest.model_dump_json(indent=2) + "\n")
        tmp.replace(target)

    def _summary_from_manifest(self, manifest: ArchiveGroupManifest) -> ArchiveGroupSummary:
        return ArchiveGroupSummary(
            archive_id=manifest.archive_id,
            target_repo_root=manifest.target_repo_root,
            target_repo_label=manifest.target_repo_label,
            base_branch=manifest.base_branch,
            task_count=manifest.task_count,
            task_ids=list(manifest.task_ids),
            archived_at=manifest.archived_at,
            archived_by=manifest.archived_by,
        )

    def _context_from_location(self, archive_dir: Path, location: ArchiveTaskLocation) -> TaskContext:
        task_dir = self._safe_archive_relative_path(archive_dir, location.relative_path)
        metadata = TaskMetadata.model_validate_json((task_dir / "metadata.json").read_text())
        state = TaskState(metadata.state)
        return TaskContext(metadata=metadata, task_dir=task_dir, state=state)

    def _safe_archive_relative_path(self, archive_dir: Path, relative_path: str) -> Path:
        root = archive_dir.resolve()
        path = (root / relative_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise TaskNotFoundError(relative_path) from exc
        return path
