from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse


def build_sse_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/events")
    async def events(request: Request):
        runtime = request.app.state.runtime

        async def event_generator():
            board = runtime.board_service.get_board()
            initial = {"event": "board_snapshot", "data": json.dumps(runtime_to_payload(board))}
            yield initial
            async for event in runtime.events.subscribe():
                yield {"event": event.event, "data": json.dumps(event.model_dump(mode="json"))}

        return EventSourceResponse(event_generator())

    return router


def runtime_to_payload(board):
    return {"event": "board_snapshot", "payload": board.model_dump(mode="json")}
