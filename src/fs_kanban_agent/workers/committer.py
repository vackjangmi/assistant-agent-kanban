from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from ..enums import TaskState
from ..exceptions import CommitError
from ..opencode_adapter import OpenCodeAdapter
from .base import WorkerBase


class CommitWorker(WorkerBase):
    worker_name = "committer"

    def __init__(self, *args, adapter: OpenCodeAdapter | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter

    async def run_once(self) -> bool:
        tasks = [task for task in self.scanner.scan() if task.state == TaskState.INTEGRATION_TEST_COMPLETED]
        if not tasks:
            return False
        task = tasks[0]
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            sha = await asyncio.to_thread(self._commit_task, task)
            task.metadata.commit.status = "committed"
            task.metadata.commit.sha = sha
            self.metadata_store.save(task.task_dir, task.metadata)
            done = self.transitions.move(task, TaskState.DONE, by=self.worker_name)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    def _build_commit_message(self, metadata) -> str:
        return f"feat: complete {metadata.slug} task"

    def _commit_task(self, task) -> str:
        target_repo_root = Path(task.metadata.target.repo_root)
        message = self._build_commit_message(task.metadata)
        commit_path = task.task_dir / "COMMIT.md"
        commit_path.write_text(message + "\n")
        task.metadata.commit.message_path = "COMMIT.md"
        result = subprocess.run(["git", "-C", str(target_repo_root), "status", "--short"], capture_output=True, text=True, check=False)
        if not result.stdout.strip():
            raise CommitError("no changes to commit")
        commit = subprocess.run(["git", "-C", str(target_repo_root), "commit", "-m", message], capture_output=True, text=True, check=False)
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or "git commit failed")
        sha = subprocess.run(["git", "-C", str(target_repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return sha.stdout.strip()
