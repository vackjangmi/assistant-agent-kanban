from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .models import WorkerEvent


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[WorkerEvent]] = set()

    async def publish(self, event: WorkerEvent) -> None:
        for queue in list(self._subscribers):
            await queue.put(event)

    async def subscribe(self) -> AsyncIterator[WorkerEvent]:
        queue: asyncio.Queue[WorkerEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
