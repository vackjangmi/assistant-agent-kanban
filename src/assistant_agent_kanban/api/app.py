from __future__ import annotations

from contextlib import asynccontextmanager

from filelock import BaseFileLock, FileLock, Timeout
from fastapi import FastAPI

from ..assistant_factory import build_adapter_registry, build_role_adapters
from ..config import AppConfig, load_config
from ..exceptions import ServerAlreadyRunningError
from ..runtime import build_runtime
from ..user_settings_store import UserSettingsStore
from .auth import install_auth_middleware
from .routes import build_router
from .sse import build_sse_router
from .ui import build_ui_router


def create_app(config: AppConfig, planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter=None, branch_summary_adapter=None, adapter_registry=None) -> FastAPI:
    user_settings_store = UserSettingsStore(config)
    runtime = build_runtime(
        config,
        planner_adapter,
        implementer_adapter,
        reviewer_adapter,
        commit_adapter,
        branch_summary_adapter,
        adapter_registry,
        user_settings_store=user_settings_store,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        app.state.user_settings_store = user_settings_store
        server_lock = _acquire_server_lock(config)
        app.state.server_lock = server_lock
        try:
            await runtime.start()
            try:
                yield
            finally:
                await runtime.stop()
        finally:
            server_lock.release()
            app.state.server_lock = None

    app = FastAPI(title="Assistant Agent Kanban", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.user_settings_store = user_settings_store
    app.state.server_lock = None
    install_auth_middleware(app, user_settings_store)
    app.include_router(build_router())
    app.include_router(build_sse_router())
    app.include_router(build_ui_router())
    return app


def create_default_app(config_path: str | None = None) -> FastAPI:
    config = load_config(config_path)
    adapter_registry = build_adapter_registry()
    planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter = build_role_adapters(config, adapter_registry=adapter_registry)
    return create_app(config, planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter, adapter_registry)


def _server_lock_path(config: AppConfig):
    return config.locks_dir / "serve.lock"


def _acquire_server_lock(config: AppConfig) -> BaseFileLock:
    lock = FileLock(str(_server_lock_path(config)))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise ServerAlreadyRunningError(
            f"assistant-agent-kanban server is already running for {config.kanban_root}"
        ) from exc
    return lock
