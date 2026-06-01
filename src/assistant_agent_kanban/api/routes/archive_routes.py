from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ...exceptions import TaskNotFoundError, TransitionError
from ._payloads import ArchiveGroupPayload


def register(router: APIRouter) -> None:
    @router.get("/api/archives")
    async def list_archives(request: Request):
        runtime = request.app.state.runtime
        archives = await asyncio.to_thread(runtime.archive_service.list_groups)
        return archives.model_dump(mode="json")

    @router.get("/api/archives/{archive_id}")
    async def archive_detail(archive_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            archive = await asyncio.to_thread(runtime.archive_service.get_group, archive_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return archive.model_dump(mode="json")

    @router.post("/api/archives")
    async def archive_done_group(payload: ArchiveGroupPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            manifest = await asyncio.to_thread(
                runtime.archive_service.archive_done_group,
                payload.target_repo_root,
                payload.base_branch,
                by="human",
            )
        except (TaskNotFoundError, TransitionError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return manifest.model_dump(mode="json")
