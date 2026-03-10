from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..enums import TaskState
from ..exceptions import TaskNotFoundError, TransitionError


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/board")
    async def board(request: Request):
        runtime = request.app.state.runtime
        return runtime.board_service.get_board()

    @router.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_task(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/tasks/{task_id}/approve-plan")
    async def approve_plan(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = runtime.planner.transitions.manual_move(task_id, TaskState.TODOS, by="human")
        except TransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/approve-integration")
    async def approve_integration(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = runtime.planner.transitions.manual_move(task_id, TaskState.INTEGRATION_TEST_COMPLETED, by="human")
        except TransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    return router
