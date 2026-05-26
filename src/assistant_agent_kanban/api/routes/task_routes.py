from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ...exceptions import IntegrationError, TaskNotFoundError, TransitionError
from ._helpers import _require_task_actor
from ._payloads import (
    CreateLineCommentPayload,
    UpdateChangedFileViewedPayload,
    UpdateHumanQaChecklistItemPayload,
    UpdateMarkdownPayload,
)


def register(router: APIRouter) -> None:
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

    @router.post("/api/tasks/{task_id}/changed-files/{changed_file_id}/viewed")
    async def update_changed_file_viewed(task_id: str, changed_file_id: str, payload: UpdateChangedFileViewedPayload, request: Request):
        runtime = request.app.state.runtime
        _require_task_actor(request, task_id)
        try:
            summary = await asyncio.to_thread(
                runtime.task_service.set_changed_file_viewed,
                task_id,
                changed_file_id,
                by="human",
                viewed=payload.viewed,
            )
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return summary

    @router.post("/api/tasks/{task_id}/human-qa/{item_id}")
    async def update_human_qa_item(task_id: str, item_id: str, payload: UpdateHumanQaChecklistItemPayload, request: Request):
        runtime = request.app.state.runtime
        _require_task_actor(request, task_id)
        try:
            item = await asyncio.to_thread(
                runtime.task_service.set_human_qa_item_state,
                task_id,
                item_id,
                by="human",
                checked=payload.checked,
                skipped=payload.skipped,
                note=payload.note,
            )
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return item

    @router.post("/api/tasks/{task_id}/changed-files/{changed_file_id}/comments")
    async def create_line_comment(task_id: str, changed_file_id: str, payload: CreateLineCommentPayload, request: Request):
        runtime = request.app.state.runtime
        _require_task_actor(request, task_id)
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
        _require_task_actor(request, task_id)
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
        _require_task_actor(request, task_id)
        try:
            runtime.task_service.update_markdown_artifact(task_id, filename, payload.content, by="human")
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {"saved": True, "filename": filename}

    @router.post("/api/tasks/{task_id}/attachments")
    async def upload_task_attachment(task_id: str, request: Request, artifact: str, file: UploadFile = File(...)):
        runtime = request.app.state.runtime
        _require_task_actor(request, task_id)
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
