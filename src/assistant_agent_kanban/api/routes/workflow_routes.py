from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ...exceptions import CommitError, IntegrationError, TaskNotFoundError, TransitionError
from ._payloads import (
    CompletedGroupOverridePayload,
    HumanReviewNotePayload,
    HumanVerificationApprovePayload,
    HumanVerificationPayload,
    ResumeImplementerPayload,
    ResumePlannerPayload,
    ResumeReviewLoopPayload,
    ResumeReviewerPayload,
    RetrospectiveCreatePayload,
    RetrospectivePayload,
    ReviewerQuestionPayload,
)


def register(router: APIRouter) -> None:
    @router.post("/api/tasks/{task_id}/approve-plan")
    async def approve_plan(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.approve_plan(task_id, by="human")
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-review-loop")
    async def resume_review_loop(task_id: str, request: Request, payload: ResumeReviewLoopPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_review_loop(
                task_id,
                by="human",
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-planner")
    async def resume_planner(task_id: str, request: Request, payload: ResumePlannerPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_planner(
                task_id,
                by="human",
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-implementer")
    async def resume_implementer(task_id: str, request: Request, payload: ResumeImplementerPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_implementer(
                task_id,
                by="human",
                resume_mode=(payload.resume_mode if payload and payload.resume_mode else "pinned"),
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return moved.metadata

    @router.post("/api/tasks/{task_id}/resume-reviewer")
    async def resume_reviewer(task_id: str, request: Request, payload: ResumeReviewerPayload | None = None):
        runtime = request.app.state.runtime
        try:
            moved = runtime.task_service.resume_reviewer(
                task_id,
                by="human",
                resume_mode=(payload.resume_mode if payload and payload.resume_mode else "pinned"),
                message=(payload.message if payload else None),
            )
        except (TransitionError, TaskNotFoundError) as exc:
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

    @router.post("/api/tasks/{task_id}/retry-verification-apply")
    async def retry_verification_apply(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            context = await asyncio.to_thread(runtime.verification_service.retry_apply, task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return context.metadata

    @router.put("/api/tasks/{task_id}/human-review-note")
    async def save_human_review_note(task_id: str, payload: HumanReviewNotePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            context = await asyncio.to_thread(runtime.verification_service.save_note, task_id, by="human", content=payload.content)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return {
            "saved": True,
            "task_id": context.metadata.task_id,
            "content": context.metadata.human_verification.note_markdown,
        }

    @router.post("/api/tasks/{task_id}/reviewer-qa")
    async def ask_reviewer_question(task_id: str, payload: ReviewerQuestionPayload, request: Request):
        runtime = request.app.state.runtime
        try:
            result = await runtime.reviewer.answer_human_question_async(task_id, by="human", question=payload.question)
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return result

    @router.post("/api/tasks/{task_id}/reviewer-qa-rerequest")
    async def rerequest_from_reviewer_qa(task_id: str, request: Request):
        runtime = request.app.state.runtime
        try:
            moved = await asyncio.to_thread(runtime.verification_service.rerequest_from_reviewer_qa, task_id, by="human")
        except (TransitionError, TaskNotFoundError, IntegrationError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
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

    @router.put("/api/tasks/{task_id}/completed-group")
    async def update_completed_group_override(task_id: str, payload: CompletedGroupOverridePayload, request: Request):
        runtime = request.app.state.runtime
        try:
            updated = await asyncio.to_thread(
                runtime.task_service.update_completed_group_override,
                task_id,
                by="human",
                group=payload.group,
            )
        except (TransitionError, TaskNotFoundError) as exc:
            status_code = 404 if isinstance(exc, TaskNotFoundError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        await runtime.rescan_and_publish()
        return updated.metadata

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
