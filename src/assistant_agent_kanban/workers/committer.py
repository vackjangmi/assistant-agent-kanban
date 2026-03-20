from __future__ import annotations

import asyncio

from ..commit_manager import CommitManager
from ..assistant_adapter import AssistantAdapter
from .base import WorkerBase


class CommitWorker(WorkerBase):
    worker_name = "committer"

    def __init__(self, *args, adapter: AssistantAdapter | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.commit_manager = CommitManager()

    async def run_once(self) -> bool:
        return False
