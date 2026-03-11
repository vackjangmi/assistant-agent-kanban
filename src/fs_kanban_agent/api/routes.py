from __future__ import annotations

from pathlib import Path
import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..agent_materializer import ensure_runtime_agents
from ..config import DEFAULT_REPO_DISCOVERY_ROOT
from ..enums import TaskState
from ..exceptions import CommitError, IntegrationError, TaskNotFoundError, TransitionError
from ..omo_config import read_omo_delegation_snapshot
from ..repo_branches import describe_target_repo_branches
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
    base_branch: str | None = None


class UpdateMarkdownPayload(BaseModel):
    content: str


class HumanVerificationPayload(BaseModel):
    note: str = ""


class ModelSettingsPayload(BaseModel):
    planner_model: str | None = None
    implementer_model: str | None = None
    reviewer_model: str | None = None
    commit_model: str | None = None
    repo_discovery_root: str | None = None
    repo_discovery_max_depth: int | None = Field(default=None, ge=1)


def _normalize_model_override(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_repo_discovery_root(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or DEFAULT_REPO_DISCOVERY_ROOT


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/board")
    async def board(request: Request):
        runtime = request.app.state.runtime
        return runtime.board_service.get_board()

    @router.get("/api/settings/models")
    async def get_model_settings(request: Request, refresh: bool = False) -> dict[str, object]:
        runtime = request.app.state.runtime
        snapshot = await asyncio.to_thread(runtime.model_registry.get, refresh=refresh)
        omo_snapshot = read_omo_delegation_snapshot()
        return {
            "planner_model": runtime.config.opencode.planner_model,
            "implementer_model": runtime.config.opencode.implementer_model,
            "reviewer_model": runtime.config.opencode.reviewer_model,
            "commit_model": runtime.config.opencode.commit_model,
            "repo_discovery_root": runtime.config.repo_discovery_root_value(),
            "repo_discovery_max_depth": runtime.config.repo_discovery.max_depth,
            "config_path": str(runtime.config.config_path_for_persistence()),
            "available_models": snapshot.models,
            "discovery_status": snapshot.status,
            "discovered_at": snapshot.discovered_at,
            "discovery_error": snapshot.error,
            "discovery_attempted": snapshot.attempted,
            "delegated_model_source_path": str(omo_snapshot.source_path) if omo_snapshot.source_path else None,
            "delegated_model_status": omo_snapshot.status,
            "delegated_model_error": omo_snapshot.error,
            "delegated_models": [
                {
                    "key": target.key,
                    "source_type": target.source_type,
                    "model": target.model,
                    "variant": target.variant,
                }
                for target in omo_snapshot.targets
            ],
        }

    @router.put("/api/settings/models")
    async def update_model_settings(payload: ModelSettingsPayload, request: Request) -> dict[str, str | int | bool | None]:
        runtime = request.app.state.runtime
        runtime.config.opencode.planner_model = _normalize_model_override(payload.planner_model)
        runtime.config.opencode.implementer_model = _normalize_model_override(payload.implementer_model)
        runtime.config.opencode.reviewer_model = _normalize_model_override(payload.reviewer_model)
        runtime.config.opencode.commit_model = _normalize_model_override(payload.commit_model)
        if payload.repo_discovery_root is not None:
            runtime.config.repo_discovery.root = _normalize_repo_discovery_root(payload.repo_discovery_root)
        if payload.repo_discovery_max_depth is not None:
            runtime.config.repo_discovery.max_depth = payload.repo_discovery_max_depth
        config_path = runtime.config.persist()
        ensure_runtime_agents(runtime.config)
        await runtime.rescan_and_publish()
        return {
            "planner_model": runtime.config.opencode.planner_model,
            "implementer_model": runtime.config.opencode.implementer_model,
            "reviewer_model": runtime.config.opencode.reviewer_model,
            "commit_model": runtime.config.opencode.commit_model,
            "repo_discovery_root": runtime.config.repo_discovery_root_value(),
            "repo_discovery_max_depth": runtime.config.repo_discovery.max_depth,
            "config_path": str(config_path),
            "saved": True,
        }

    @router.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_task(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/tasks/{task_id}/logs")
    async def task_logs(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_logs(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/tasks/{task_id}/artifacts/{filename}")
    async def task_markdown_artifact(task_id: str, filename: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return {"filename": filename, "content": runtime.task_service.get_markdown_artifact(task_id, filename)}
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.put("/api/tasks/{task_id}/artifacts/{filename}")
    async def update_task_markdown_artifact(task_id: str, filename: str, payload: UpdateMarkdownPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            runtime.task_service.update_markdown_artifact(task_id, filename, payload.content)
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"saved": True, "filename": filename}

    @router.get("/api/target-repos")
    async def target_repos(request: Request):
        runtime = request.app.state.runtime
        try:
            items = discover_target_repos(runtime.config)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "root": runtime.config.repo_discovery_root_value(),
            "resolved_root": str(runtime.config.resolve_repo_discovery_root()),
            "max_depth": runtime.config.repo_discovery.max_depth,
            "items": items,
        }

    @router.get("/api/target-repo-branches")
    async def target_repo_branches(target_repo: str, request: Request):
        runtime = request.app.state.runtime
        try:
            snapshot = describe_target_repo_branches(runtime.config, Path(target_repo))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return snapshot.model_dump(mode="json")

    @router.post("/api/requests")
    async def create_request_task(payload: CreateRequestPayload, request: Request):
        runtime = request.app.state.runtime
        normalized_base_branch = payload.base_branch.strip() if payload.base_branch else runtime.config.base_branch
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
                base_branch=normalized_base_branch,
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

    @router.post("/api/tasks/{task_id}/start-verification")
    async def start_verification(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = await asyncio.to_thread(runtime.verification_service.start, task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/reject-verification")
    async def reject_verification(task_id: str, payload: HumanVerificationPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = await asyncio.to_thread(runtime.verification_service.reject, task_id, by="human", note=payload.note)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/approve-verification")
    async def approve_verification(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = await asyncio.to_thread(runtime.verification_service.approve, task_id, by="human")
        except (TransitionError, TaskNotFoundError, CommitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            await asyncio.to_thread(runtime.deletion_service.delete, task_id, by="human")
        except (TransitionError, TaskNotFoundError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"deleted": True, "task_id": task_id}

    return router
