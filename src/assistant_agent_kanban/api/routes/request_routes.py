from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ...exceptions import AdapterRunError
from ...repo_branches import describe_target_repo_branches
from ...repo_discovery import discover_target_repos
from ...request_creator import (
    delete_request_uploads,
    get_request_upload,
    save_request_upload,
)
from ...request_drafting import ALLOWED_DRAFT_UPDATE_FIELDS, RequestDraftPayload as RequestDraftRoutePayload, draft_request
from ...settings_resolver import effective_config_for_user_and_project
from ..auth import auth_is_required, current_user_or_none
from ._helpers import (
    _filter_request_drafts_for_current_user,
    _request_draft_owner_fields,
    _request_draft_state_from_payload,
    _request_draft_store,
    _require_request_draft_access,
)
from ._payloads import (
    CreateRequestDraftPayload,
    CreateRequestPayload,
    UpdateRequestDraftPayload,
)


def register(router: APIRouter) -> None:
    @router.post("/api/request-uploads")
    async def upload_request_attachment(upload_token: str, request: Request, file: UploadFile = File(...)):
        runtime = request.app.state.runtime
        data = await file.read()
        try:
            saved = save_request_upload(runtime.config, upload_token, file.filename or "image", file.content_type, data)
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return saved

    @router.get("/api/request-uploads/{upload_token}/{filename}")
    async def request_attachment(upload_token: str, filename: str, request: Request):
        runtime = request.app.state.runtime
        try:
            path, media_type = get_request_upload(runtime.config, upload_token, filename)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=media_type)

    @router.delete("/api/request-uploads/{upload_token}")
    async def delete_request_attachment_uploads(upload_token: str, request: Request):
        runtime = request.app.state.runtime
        delete_request_uploads(runtime.config, upload_token)
        return {"deleted": True}

    @router.post("/api/request-drafts")
    async def draft_request_response(payload: RequestDraftRoutePayload, request: Request):
        runtime = request.app.state.runtime
        store = _request_draft_store(request)
        try:
            draft_id = (payload.request_draft_id or "").strip() if hasattr(payload, "request_draft_id") else ""
            if draft_id:
                draft = store.load(draft_id)
                _require_request_draft_access(request, draft)
            else:
                draft = store.create(_request_draft_owner_fields(request))
            draft = store.update(draft.draft_id, _request_draft_state_from_payload(payload))
            user_message = payload.message.strip()
            draft_for_run = draft.model_copy(update={
                "request_draft_input": "",
            })
            result = await asyncio.to_thread(
                draft_request,
                config=runtime.config,
                adapter_registry=runtime.adapter_registry,
                payload=draft_for_run.to_drafting_payload(message=user_message),
            )
            draft = store.update(draft.draft_id, {
                **_request_draft_field_updates_state(result.field_updates),
                "request_draft_input": "",
                "transcript": [
                    *draft.transcript,
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": result.reply, "field_updates": result.field_updates},
                ],
            })
        except HTTPException:
            raise
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            detail = str(exc).strip() or "request drafting failed"
            raise HTTPException(status_code=500, detail=detail) from exc
        response = result.model_dump(mode="json")
        response.update(
            {
                "request_draft_id": draft.draft_id,
                "request_upload_token": draft.request_upload_token,
                "transcript": [entry.model_dump(mode="json") for entry in draft.transcript],
            }
        )
        return response

    @router.post("/api/request-drafts/state")
    async def create_request_draft_state(payload: CreateRequestDraftPayload, request: Request):
        store = _request_draft_store(request)
        try:
            draft = store.create({
                **_request_draft_owner_fields(request),
                **_request_draft_state_from_payload(payload),
            })
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return draft.model_dump(mode="json")

    @router.get("/api/request-drafts")
    async def list_request_drafts(request: Request):
        drafts = _filter_request_drafts_for_current_user(request, _request_draft_store(request).list())
        return {
            "items": [
                {
                    "draft_id": draft.draft_id,
                    "created_by_user_id": draft.created_by_user_id,
                    "created_by_username": draft.created_by_username,
                    "title": draft.title,
                    "target_repo": draft.target_repo,
                    "base_branch": draft.base_branch,
                    "updated_at": draft.updated_at,
                    "created_at": draft.created_at,
                    "active_tab": draft.active_tab,
                    "has_transcript": bool(draft.transcript),
                    "has_unsent_input": bool((draft.request_draft_input or "").strip()),
                }
                for draft in drafts
            ]
        }

    @router.get("/api/request-drafts/{draft_id}")
    async def get_request_draft(draft_id: str, request: Request):
        try:
            draft = _request_draft_store(request).load(draft_id)
            _require_request_draft_access(request, draft)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return draft.model_dump(mode="json")

    @router.put("/api/request-drafts/{draft_id}")
    async def update_request_draft(draft_id: str, payload: UpdateRequestDraftPayload, request: Request):
        store = _request_draft_store(request)
        try:
            draft = store.load(draft_id)
            _require_request_draft_access(request, draft)
            draft = store.update(draft_id, _request_draft_state_from_payload(payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return draft.model_dump(mode="json")

    @router.delete("/api/request-drafts/{draft_id}")
    async def delete_request_draft(draft_id: str, request: Request):
        store = _request_draft_store(request)
        try:
            draft = store.load(draft_id)
            _require_request_draft_access(request, draft)
            store.delete(draft_id)
        except FileNotFoundError:
            return {"deleted": True}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if draft.request_upload_token:
            delete_request_uploads(request.app.state.runtime.config, draft.request_upload_token)
        return {"deleted": True}

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
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return snapshot.model_dump(mode="json")

    @router.post("/api/requests")
    async def create_request_task(payload: CreateRequestPayload, request: Request):
        runtime = request.app.state.runtime
        user = current_user_or_none(request)
        effective_auth_enabled = auth_is_required(request)
        effective_config = effective_config_for_user_and_project(
            runtime.config,
            request.app.state.user_settings_store,
            target_repo=payload.target_repo,
            user_id=user.user_id if user and effective_auth_enabled else None,
        )
        slack_channel_id = _initial_slack_channel(
            runtime.config,
            request.app.state.user_settings_store,
            target_repo=payload.target_repo,
            user_id=user.user_id if user and effective_auth_enabled else None,
        )
        try:
            request_draft_id = (payload.request_draft_id or "").strip()
            if request_draft_id:
                _require_request_draft_access(request, _request_draft_store(request).load(request_draft_id))
            task_dir = await asyncio.to_thread(
                runtime.create_request_from_submission,
                title=payload.title,
                goal=payload.goal,
                background=payload.background,
                plan_auto_approve=payload.plan_auto_approve,
                scope=payload.scope,
                out_of_scope=payload.out_of_scope,
                constraints=payload.constraints,
                references=payload.references,
                acceptance_criteria=payload.acceptance_criteria,
                target_repo=payload.target_repo,
                base_branch=payload.base_branch,
                request_upload_token=payload.request_upload_token,
                request_draft_id=payload.request_draft_id,
                request_draft_markdown=payload.request_draft_markdown,
                slack_channel_id=slack_channel_id,
                request_config=effective_config,
                created_by_user_id=user.user_id if user and effective_auth_enabled else None,
                created_by_username=user.username if user and effective_auth_enabled else None,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="request draft not found") from exc
        except (ValueError, AdapterRunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"task_path": str(task_dir), "created": True}


def _initial_slack_channel(config, store, *, target_repo: str, user_id: str | None) -> str | None:
    channel = config.slack.default_channel
    if user_id:
        user_settings = store.get_user_settings(user_id)
        channel = user_settings.runtime.slack_default_channel or channel
    if target_repo:
        project_settings = store.get_project_settings(target_repo)
        channel = project_settings.runtime.slack_default_channel or channel
    return channel


def _request_draft_field_updates_state(field_updates: dict[str, str]) -> dict[str, object]:
    return {
        field_name: value
        for field_name, value in field_updates.items()
        if field_name in ALLOWED_DRAFT_UPDATE_FIELDS and value is not None
    }
