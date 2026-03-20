from __future__ import annotations

import os

from assistant_agent_kanban.main import main


def test_main_serve_forwards_reload_flag(monkeypatch):
    recorded = {}

    def fake_uvicorn_run(app, *, host, port, reload, factory=False):
        recorded["app"] = app
        recorded["host"] = host
        recorded["port"] = port
        recorded["reload"] = reload
        recorded["factory"] = factory
        recorded["config_env"] = os.environ.get("ASSISTANT_AGENT_KANBAN_CONFIG")

    monkeypatch.setattr("assistant_agent_kanban.main.uvicorn.run", fake_uvicorn_run)
    monkeypatch.delenv("ASSISTANT_AGENT_KANBAN_CONFIG", raising=False)

    main(["serve", "--config", "config.yaml", "--host", "0.0.0.0", "--port", "9000", "--reload"])

    assert recorded == {
        "app": "assistant_agent_kanban.api.main:create_app",
        "host": "0.0.0.0",
        "port": 9000,
        "reload": True,
        "factory": True,
        "config_env": "config.yaml",
    }


def test_main_serve_uses_app_object_without_reload(monkeypatch):
    recorded = {}

    def fake_create_default_app(config_path):
        recorded["config"] = config_path
        return "app"

    def fake_uvicorn_run(app, *, host, port, reload, factory=False):
        recorded["app"] = app
        recorded["host"] = host
        recorded["port"] = port
        recorded["reload"] = reload
        recorded["factory"] = factory

    monkeypatch.setattr("assistant_agent_kanban.main.create_default_app", fake_create_default_app)
    monkeypatch.setattr("assistant_agent_kanban.main.uvicorn.run", fake_uvicorn_run)

    main(["serve", "--config", "config.yaml", "--host", "127.0.0.1", "--port", "8001"])

    assert recorded == {
        "config": "config.yaml",
        "app": "app",
        "host": "127.0.0.1",
        "port": 8001,
        "reload": False,
        "factory": False,
    }
