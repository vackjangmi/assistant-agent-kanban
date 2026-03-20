from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .enums import TaskState
from .models import HistoryEntry, IntegrationInfo, RequestInfo, TargetRepoInfo, TaskErrorInfo, TaskMetadata, utc_now


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "task"


class MetadataStore:
    def metadata_path(self, task_dir: Path) -> Path:
        return task_dir / "metadata.json"

    def load(self, task_dir: Path) -> TaskMetadata:
        return TaskMetadata.model_validate_json(self.metadata_path(task_dir).read_text())

    def save(self, task_dir: Path, metadata: TaskMetadata) -> None:
        metadata.updated_at = utc_now()
        path = self.metadata_path(task_dir)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(metadata.model_dump(mode="json"), indent=2) + "\n")
        os.replace(tmp_path, path)

    def bootstrap(
        self,
        task_dir: Path,
        state: TaskState,
        task_id: str,
        title: str,
        slug: str,
        *,
        target_repo_root: str,
        base_branch: str,
        request_language: str | None = None,
    ) -> TaskMetadata:
        created = utc_now()
        metadata = TaskMetadata(
            task_id=task_id,
            title=title,
            slug=slug,
            state=state,
            created_at=created,
            updated_at=created,
            request=RequestInfo(language=request_language),
            target=TargetRepoInfo(repo_root=target_repo_root, base_branch=base_branch),
            integration=IntegrationInfo(base_branch=base_branch),
            history=[HistoryEntry(state=state, entered_at=created, by="human")],
        )
        self.save(task_dir, metadata)
        return metadata

    def add_error(self, task_dir: Path, metadata: TaskMetadata, code: str, message: str) -> TaskMetadata:
        metadata.errors.append(TaskErrorInfo(code=code, message=message))
        self.save(task_dir, metadata)
        return metadata
