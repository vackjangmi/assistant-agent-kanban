from __future__ import annotations

from pathlib import Path
import asyncio

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from fastapi.responses import FileResponse

from ..assistant_factory import build_role_adapters
from ..agent_materializer import ensure_runtime_agents
from ..assistant_adapter import AssistantModelRegistry
from ..config import DEFAULT_REPO_DISCOVERY_ROOT, DEFAULT_SESSION_TOKEN_BUDGET, SUPPORTED_RUNTIME_ASSISTANTS, normalize_runtime_assistant
from ..enums import TaskState
from ..exceptions import CommitError, IntegrationError, TaskNotFoundError, TransitionError
from ..language import normalize_runtime_language
from ..omo_config import read_omo_delegation_snapshot
from ..repo_branches import describe_target_repo_branches
from ..repo_discovery import discover_target_repos
from ..language import runtime_language_code_to_request_language
from ..request_creator import RequestTemplateData, build_default_scope_sections_for_language, create_request, split_lines


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


class HumanVerificationApprovePayload(BaseModel):
    completion_mode: Literal["new-branch", "target-branch"] = "new-branch"


class HumanReviewNotePayload(BaseModel):
    content: str = ""


class RetrospectivePayload(BaseModel):
    target_repo_root: str
    base_branch: str
    comparison_branch: str | None = None


class RetrospectiveCreatePayload(RetrospectivePayload):
    completion_mode: Literal["new-branch", "target-branch"]


class CreateLineCommentPayload(BaseModel):
    path: str
    side: Literal["left", "right"]
    line_number: int = Field(ge=1)
    line_kind: Literal["context", "add", "remove"]
    hunk_header: str | None = None
    body: str = ""


class ModelSettingsPayload(BaseModel):
    language: str | None = None
    theme: Literal["light", "dark"] | None = None
    coding_assistant: str | None = None
    planner_model: str | None = None
    planner_session_token_budget: int | None = Field(default=None, ge=1)
    planner_agent_count: int | None = Field(default=None, ge=1)
    implementer_model: str | None = None
    implementer_session_token_budget: int | None = Field(default=None, ge=1)
    implementer_agent_count: int | None = Field(default=None, ge=1)
    reviewer_model: str | None = None
    reviewer_session_token_budget: int | None = Field(default=None, ge=1)
    reviewer_agent_count: int | None = Field(default=None, ge=1)
    commit_model: str | None = None
    commit_session_token_budget: int | None = Field(default=None, ge=1)
    repo_discovery_root: str | None = None
    repo_discovery_max_depth: int | None = Field(default=None, ge=1)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_runtime_language(value)
        if normalized is None:
            raise ValueError("language must be EN or KO")
        return normalized

    @field_validator("coding_assistant", mode="before")
    @classmethod
    def normalize_coding_assistant(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_runtime_assistant(value)
        if normalized is None:
            raise ValueError("coding assistant must be OpenCode or Codex CLI")
        return normalized


def _normalize_model_override(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_repo_discovery_root(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or DEFAULT_REPO_DISCOVERY_ROOT


def _normalize_session_token_budget(value: int | None) -> int:
    if value is None:
        return DEFAULT_SESSION_TOKEN_BUDGET
    return max(1, value) * 1000


def _display_session_token_budget(value: int) -> int:
    return max(1, value // 1000)


def _normalize_agent_count(value: int | None) -> int:
    if value is None:
        return 1
    return max(1, value)


def _normalize_runtime_language(value: str | None) -> str:
    normalized = normalize_runtime_language(value)
    if normalized is None:
        raise ValueError("language must be EN or KO")
    return normalized


def _normalize_runtime_coding_assistant(value: str | None) -> str:
    normalized = normalize_runtime_assistant(value)
    if normalized is None:
        raise ValueError("coding assistant must be OpenCode or Codex CLI")
    return normalized


def _apply_config_update(target, updated) -> None:
    target.kanban_root = updated.kanban_root
    target.repo_root = updated.repo_root
    target.base_branch = updated.base_branch
    target.opencode = updated.opencode
    target.codex = updated.codex
    target.workspace = updated.workspace
    target.locks = updated.locks
    target.runtime = updated.runtime
    target.repo_discovery = updated.repo_discovery
    target.loaded_from = updated.loaded_from
    target.loaded_local_from = updated.loaded_local_from


def _settings_response(runtime, snapshot, *, config_path: str | None = None, saved: bool = False) -> dict[str, object]:
    active_backend = snapshot.backend
    active_config = runtime.config if runtime.config.active_backend() == active_backend else runtime.config.model_copy(deep=True)
    active_config.runtime.coding_assistant = active_backend
    omo_snapshot = read_omo_delegation_snapshot() if active_backend == "opencode" else None
    response = {
        "language": runtime.config.runtime.language,
        "theme": runtime.config.runtime.theme,
        "coding_assistant": active_backend,
        "planner_model": active_config.role_model("planner"),
        "planner_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("planner")),
        "planner_agent_count": runtime.config.runtime.planner_agent_count,
        "implementer_model": active_config.role_model("implementer"),
        "implementer_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("implementer")),
        "implementer_agent_count": runtime.config.runtime.implementer_agent_count,
        "reviewer_model": active_config.role_model("reviewer"),
        "reviewer_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("reviewer")),
        "reviewer_agent_count": runtime.config.runtime.reviewer_agent_count,
        "commit_model": active_config.role_model("commit"),
        "commit_session_token_budget": _display_session_token_budget(active_config.role_session_token_budget("commit")),
        "repo_discovery_root": runtime.config.repo_discovery_root_value(),
        "repo_discovery_max_depth": runtime.config.repo_discovery.max_depth,
        "config_path": config_path or str(runtime.config.config_path_for_persistence()),
        "available_assistants": [
            {"value": value, "label": label}
            for value, label in SUPPORTED_RUNTIME_ASSISTANTS.items()
        ],
        "available_models": snapshot.models,
        "discovery_status": snapshot.status,
        "discovered_at": snapshot.discovered_at,
        "discovery_error": snapshot.error,
        "discovery_attempted": snapshot.attempted,
        "supports_model_discovery": snapshot.supports_model_discovery,
        "model_backend": snapshot.backend,
        "saved": saved,
    }
    if omo_snapshot is None:
        response.update(
            {
                "delegated_model_source_path": None,
                "delegated_model_status": "unsupported",
                "delegated_model_error": None,
                "delegated_models": [],
            }
        )
        return response
    response.update(
        {
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
    )
    return response


def _reconfigure_runtime_adapters(runtime) -> None:
    planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter = build_role_adapters(runtime.config, adapter_registry=runtime.adapter_registry)
    runtime.planner.adapter = planner_adapter
    runtime.implementer.adapter = implementer_adapter
    runtime.reviewer.adapter = reviewer_adapter
    runtime.committer.adapter = commit_adapter
    runtime.verification_service.branch_summary_adapter = branch_summary_adapter
    runtime.retrospective_service.adapter = commit_adapter
    runtime.model_registry.adapter = planner_adapter
    runtime.model_registry.config = runtime.config
    runtime._task_adapters = [adapter for adapter in runtime._collect_task_adapters() if adapter is not None]


async def _resolve_settings_snapshot(runtime, *, refresh: bool, assistant: str | None):
    requested_assistant = _normalize_runtime_coding_assistant(assistant) if assistant is not None else runtime.config.active_backend()
    if requested_assistant == runtime.config.active_backend():
        snapshot = await asyncio.to_thread(runtime.model_registry.get, refresh=refresh)
        return snapshot
    preview_config = runtime.config.model_copy(deep=True)
    preview_config.runtime.coding_assistant = requested_assistant
    preview_adapter, _, _, _, _ = build_role_adapters(preview_config, adapter_registry=runtime.adapter_registry)
    preview_registry = AssistantModelRegistry(adapter=preview_adapter, config=preview_config)
    return await asyncio.to_thread(preview_registry.get, refresh=refresh)


def _uses_builtin_runtime_adapter(runtime) -> bool:
    adapter = getattr(runtime.planner, "adapter", None)
    return adapter.__class__.__module__.startswith("fs_kanban_agent.") if adapter is not None else False


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
    async def get_model_settings(request: Request, refresh: bool = False, assistant: str | None = None) -> dict[str, object]:
        runtime = request.app.state.runtime
        snapshot = await _resolve_settings_snapshot(runtime, refresh=refresh, assistant=assistant)
        return _settings_response(runtime, snapshot)

    @router.put("/api/settings/models")
    async def update_model_settings(payload: ModelSettingsPayload, request: Request) -> dict[str, object]:
        runtime = request.app.state.runtime
        next_config = runtime.config.model_copy(deep=True)
        previous_backend = runtime.config.active_backend()
        fields_set = payload.model_fields_set
        if "language" in fields_set:
            next_config.runtime.language = _normalize_runtime_language(payload.language)
        if "theme" in fields_set and payload.theme is not None:
            next_config.runtime.theme = payload.theme
        if "coding_assistant" in fields_set:
            next_config.runtime.coding_assistant = _normalize_runtime_coding_assistant(payload.coding_assistant)
        if "planner_model" in fields_set:
            next_config.set_role_model("planner", _normalize_model_override(payload.planner_model))
        if "planner_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("planner", _normalize_session_token_budget(payload.planner_session_token_budget))
        if "planner_agent_count" in fields_set:
            next_config.runtime.planner_agent_count = _normalize_agent_count(payload.planner_agent_count)
        if "implementer_model" in fields_set:
            next_config.set_role_model("implementer", _normalize_model_override(payload.implementer_model))
        if "implementer_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("implementer", _normalize_session_token_budget(payload.implementer_session_token_budget))
        if "implementer_agent_count" in fields_set:
            next_config.runtime.implementer_agent_count = _normalize_agent_count(payload.implementer_agent_count)
        if "reviewer_model" in fields_set:
            next_config.set_role_model("reviewer", _normalize_model_override(payload.reviewer_model))
        if "reviewer_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("reviewer", _normalize_session_token_budget(payload.reviewer_session_token_budget))
        if "reviewer_agent_count" in fields_set:
            next_config.runtime.reviewer_agent_count = _normalize_agent_count(payload.reviewer_agent_count)
        if "commit_model" in fields_set:
            next_config.set_role_model("commit", _normalize_model_override(payload.commit_model))
        if "commit_session_token_budget" in fields_set:
            next_config.set_role_session_token_budget("commit", _normalize_session_token_budget(payload.commit_session_token_budget))
        if payload.repo_discovery_root is not None:
            next_config.repo_discovery.root = _normalize_repo_discovery_root(payload.repo_discovery_root)
        if payload.repo_discovery_max_depth is not None:
            next_config.repo_discovery.max_depth = payload.repo_discovery_max_depth
        config_path = next_config.persist()
        _apply_config_update(runtime.config, next_config)
        if previous_backend != runtime.config.active_backend() or _uses_builtin_runtime_adapter(runtime):
            _reconfigure_runtime_adapters(runtime)
        ensure_runtime_agents(runtime.config)
        snapshot = await asyncio.to_thread(runtime.model_registry.refresh, refresh_cli=False)
        return _settings_response(runtime, snapshot, config_path=str(config_path), saved=True)

    @router.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str, request: Request, include_changed_files: bool = False):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_task(task_id, include_changed_files=include_changed_files)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/tasks/{task_id}/logs")
    async def task_logs(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_logs(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/tasks/{task_id}/changed-files/{changed_file_id}")
    async def task_changed_file(task_id: str, changed_file_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            return runtime.task_service.get_changed_file(task_id, changed_file_id)
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.post("/api/tasks/{task_id}/changed-files/{changed_file_id}/comments")
    async def create_line_comment(task_id: str, changed_file_id: str, payload: CreateLineCommentPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            await asyncio.to_thread(
                runtime.verification_service.add_line_comment,
                task_id,
                by="human",
                path=payload.path,
                side=payload.side,
                line_number=payload.line_number,
                line_kind=payload.line_kind,
                hunk_header=payload.hunk_header,
                body_markdown=payload.body,
            )
            detail = await asyncio.to_thread(runtime.task_service.get_changed_file_by_path, task_id, payload.path)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return detail

    @router.delete("/api/tasks/{task_id}/changed-files/{changed_file_id}/comments/{comment_id}")
    async def delete_line_comment(task_id: str, changed_file_id: str, comment_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            await asyncio.to_thread(
                runtime.verification_service.delete_line_comment,
                task_id,
                by="human",
                comment_id=comment_id,
            )
            detail = await asyncio.to_thread(runtime.task_service.get_changed_file, task_id, changed_file_id)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return detail

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

    @router.post("/api/tasks/{task_id}/attachments")
    async def upload_task_attachment(task_id: str, request: Request, artifact: str, file: UploadFile = File(...)):
        runtime = request.app.state.runtime
        data = await file.read()
        try:
            saved = runtime.task_service.save_attachment(task_id, artifact, file.filename or "image", file.content_type, data)
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return saved

    @router.get("/api/tasks/{task_id}/attachments/{filename}")
    async def task_attachment(task_id: str, filename: str, request: Request):
        runtime = request.app.state.runtime
        try:
            path, media_type = runtime.task_service.get_attachment(task_id, filename)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=media_type)

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
        request_language = runtime_language_code_to_request_language(runtime.config.runtime.language)
        try:
            default_scope, default_out_of_scope = build_default_scope_sections_for_language(payload.target_repo, language_code=request_language)
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

    @router.put("/api/tasks/{task_id}/human-review-note")
    async def save_human_review_note(task_id: str, payload: HumanReviewNotePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            context = await asyncio.to_thread(runtime.verification_service.save_note, task_id, by="human", content=payload.content)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"saved": True, "task_id": context.metadata.task_id}

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
    async def approve_verification(
        task_id: str,
        request: Request,
        payload: HumanVerificationApprovePayload | None = None,
    ):
        runtime = request.app.state.runtime
        approval_payload = payload or HumanVerificationApprovePayload()
        try:
            moved = await asyncio.to_thread(
                runtime.verification_service.approve,
                task_id,
                by="human",
                completion_mode=approval_payload.completion_mode,
            )
        except (TransitionError, TaskNotFoundError, CommitError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/retrospectives/inspect")
    async def inspect_retrospective(payload: RetrospectivePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            record = await asyncio.to_thread(
                runtime.retrospective_service.inspect,
                payload.target_repo_root,
                payload.base_branch,
                payload.comparison_branch,
            )
        except (TransitionError, TaskNotFoundError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @router.post("/api/retrospectives/create")
    async def create_retrospective(payload: RetrospectiveCreatePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            record = await asyncio.to_thread(
                runtime.retrospective_service.create,
                payload.target_repo_root,
                payload.base_branch,
                payload.comparison_branch,
                by="human",
                completion_mode=payload.completion_mode,
            )
        except (TransitionError, TaskNotFoundError, CommitError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return record.model_dump(mode="json")

    @router.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            await runtime.force_delete(task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"deleted": True, "task_id": task_id}

    return router
