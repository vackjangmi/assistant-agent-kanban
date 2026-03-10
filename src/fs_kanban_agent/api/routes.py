from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..enums import TaskState
from ..exceptions import TaskNotFoundError, TransitionError
from ..repo_discovery import discover_target_repos
from ..request_creator import RequestTemplateData, build_default_scope_sections, create_request, split_lines


class CreateRequestPayload(BaseModel):
    title: str
    goal: str
    background: str | None = None
    scope: str | None = None
    out_of_scope: str | None = None
    constraints: str | None = None
    references: str | None = None
    acceptance_criteria: str | None = None
    target_repo: str
    base_branch: str = Field(default="main")


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

    @router.get("/api/target-repos")
    async def target_repos(request: Request):
        runtime = request.app.state.runtime
        return {
            "root": str(runtime.config.repo_discovery.root),
            "max_depth": runtime.config.repo_discovery.max_depth,
            "items": discover_target_repos(runtime.config),
        }

    @router.post("/api/requests")
    async def create_request_task(payload: CreateRequestPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            default_scope, default_out_of_scope = build_default_scope_sections(payload.target_repo)
            task_dir = create_request(
                runtime.config,
                template=RequestTemplateData(
                    title=payload.title.strip(),
                    goal=payload.goal.strip(),
                    background=payload.background.strip() if payload.background else None,
                    scope=split_lines(payload.scope) or default_scope,
                    out_of_scope=split_lines(payload.out_of_scope) or default_out_of_scope,
                    constraints=split_lines(payload.constraints),
                    references=split_lines(payload.references),
                    acceptance_criteria=split_lines(payload.acceptance_criteria),
                ),
                target_repo_root=Path(payload.target_repo),
                base_branch=payload.base_branch.strip() or runtime.config.base_branch,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"task_path": str(task_dir), "created": True}

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
