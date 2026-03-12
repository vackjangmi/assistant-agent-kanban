from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


TEMPLATE_PATH = Path(__file__).with_name("templates") / "index.html"
TEMPLATE_DEFAULT_TARGET_REPO = "__DEFAULT_TARGET_REPO__"
TEMPLATE_DEFAULT_BASE_BRANCH = "__DEFAULT_BASE_BRANCH__"
TEMPLATE_INITIAL_RUNTIME_LANGUAGE = "__INITIAL_RUNTIME_LANGUAGE__"


def _render_index_html(*, default_target_repo: str, default_base_branch: str, initial_runtime_language: str) -> str:
    template = TEMPLATE_PATH.read_text()
    return (
        template.replace(TEMPLATE_DEFAULT_TARGET_REPO, json.dumps(default_target_repo))
        .replace(TEMPLATE_DEFAULT_BASE_BRANCH, json.dumps(default_base_branch))
        .replace(TEMPLATE_INITIAL_RUNTIME_LANGUAGE, json.dumps(initial_runtime_language))
    )


def build_ui_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> str:
        runtime = request.app.state.runtime
        return _render_index_html(
            default_target_repo="",
            default_base_branch=runtime.config.base_branch,
            initial_runtime_language=runtime.config.runtime.language,
        )

    return router
