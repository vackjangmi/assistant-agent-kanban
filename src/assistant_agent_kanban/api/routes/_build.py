from __future__ import annotations

from fastapi import APIRouter

from . import request_routes, settings_routes, task_routes, workflow_routes


def build_router() -> APIRouter:
    router = APIRouter()
    settings_routes.register(router)
    task_routes.register(router)
    request_routes.register(router)
    workflow_routes.register(router)
    return router
