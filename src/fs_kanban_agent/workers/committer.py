from __future__ import annotations

import asyncio

from ..commit_manager import CommitManager
from ..opencode_adapter import OpenCodeAdapter
from .base import WorkerBase


class CommitWorker(WorkerBase):
    worker_name = "committer"

    def __init__(self, *args, adapter: OpenCodeAdapter | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.commit_manager = CommitManager()

    async def run_once(self) -> bool:
        return False
