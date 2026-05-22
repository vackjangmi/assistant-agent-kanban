from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_PATH = TEMPLATE_DIR / "index.html"
CSS_PATH = TEMPLATE_DIR / "index.css"
JS_DIR = TEMPLATE_DIR / "js"
JS_MANIFEST = [
    "00_globals.js",
    "10_translations.js",
    "20_settings.js",
    "30_board_rendering.js",
    "40_modals_forms.js",
    "45_onboarding.js",
    "50_diff.js",
    "60_request_composer.js",
    "70_task_panels.js",
    "71_task_artifacts.js",
    "72_task_actions.js",
    "80_event_wiring.js",
    "99_sse.js",
]

TEMPLATE_DEFAULT_TARGET_REPO = "__DEFAULT_TARGET_REPO__"
TEMPLATE_DEFAULT_BASE_BRANCH = "__DEFAULT_BASE_BRANCH__"
TEMPLATE_INITIAL_RUNTIME_LANGUAGE = "__INITIAL_RUNTIME_LANGUAGE__"
TEMPLATE_INITIAL_RUNTIME_THEME = "__INITIAL_RUNTIME_THEME__"
TEMPLATE_INITIAL_RUNTIME_THEME_ATTR = "__INITIAL_RUNTIME_THEME_ATTR__"
TEMPLATE_TARGET_REPO_DOCS_ROOT = "__TARGET_REPO_DOCS_ROOT__"


def _render_index_html(*, default_target_repo: str, default_base_branch: str, initial_runtime_language: str, initial_runtime_theme: str, target_repo_docs_root: str) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    css_content = CSS_PATH.read_text(encoding="utf-8")
    js_content = "".join((JS_DIR / name).read_text(encoding="utf-8") for name in JS_MANIFEST)

    rendered = template.replace("/* {{ INJECT_CSS }} */", css_content).replace("/* {{ INJECT_JS }} */", js_content)

    return (
        rendered.replace(TEMPLATE_DEFAULT_TARGET_REPO, json.dumps(default_target_repo))
        .replace(TEMPLATE_DEFAULT_BASE_BRANCH, json.dumps(default_base_branch))
        .replace(TEMPLATE_INITIAL_RUNTIME_LANGUAGE, json.dumps(initial_runtime_language))
        .replace(TEMPLATE_INITIAL_RUNTIME_THEME_ATTR, initial_runtime_theme)
        .replace(TEMPLATE_INITIAL_RUNTIME_THEME, json.dumps(initial_runtime_theme))
        .replace(TEMPLATE_TARGET_REPO_DOCS_ROOT, json.dumps(target_repo_docs_root))
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
            initial_runtime_theme=runtime.config.runtime.theme,
            target_repo_docs_root=runtime.config.target_repo_docs_root_value(),
        )

    return router
