from __future__ import annotations

import os

from .app import create_default_app


CONFIG_ENV_VAR = "FS_KANBAN_AGENT_CONFIG"


def create_app():
    return create_default_app(os.environ.get(CONFIG_ENV_VAR) or None)

app = create_app()
