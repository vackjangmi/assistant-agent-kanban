from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..assistant_factory import build_role_adapters
from ..config import AppConfig, load_config
from ..runtime import build_runtime
from .routes import build_router
from .sse import build_sse_router
from .ui import build_ui_router


def create_app(config: AppConfig, planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter=None, branch_summary_adapter=None) -> FastAPI:
    runtime = build_runtime(config, planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="fs-kanban-agent", lifespan=lifespan)
    app.state.runtime = runtime
    app.include_router(build_router())
    app.include_router(build_sse_router())
    app.include_router(build_ui_router())
    return app


def create_default_app(config_path: str | None = None) -> FastAPI:
    config = load_config(config_path)
    planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter = build_role_adapters(config)
    return create_app(config, planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter)
