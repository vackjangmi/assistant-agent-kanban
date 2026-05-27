from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from assistant_agent_kanban.main import _print_shutdown_message, main


def test_main_serve_forwards_reload_flag(monkeypatch):
    recorded = {}

    def fake_uvicorn_run(app, *, host, port, reload, factory=False, access_log=True, log_level="info"):
        recorded["app"] = app
        recorded["host"] = host
        recorded["port"] = port
        recorded["reload"] = reload
        recorded["factory"] = factory
        recorded["access_log"] = access_log
        recorded["log_level"] = log_level
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
        "access_log": False,
        "log_level": "warning",
        "config_env": "config.yaml",
    }


def test_main_serve_uses_app_object_without_reload(monkeypatch, capsys):
    recorded = {}

    def fake_create_default_app(config_path):
        recorded["config"] = config_path
        return SimpleNamespace(
            state=SimpleNamespace(
                runtime=SimpleNamespace(
                    config=SimpleNamespace(
                        auth=SimpleNamespace(enabled=False),
                        app_database_path=Path("/tmp/missing.db"),
                        kanban_root="/tmp/kanban",
                        loaded_local_from=None,
                        loaded_from=None,
                    )
                )
            )
        )

    def fake_uvicorn_run(app, *, host, port, reload, factory=False, access_log=True, log_level="info"):
        recorded["app"] = app
        recorded["host"] = host
        recorded["port"] = port
        recorded["reload"] = reload
        recorded["factory"] = factory
        recorded["access_log"] = access_log
        recorded["log_level"] = log_level

    monkeypatch.setattr("assistant_agent_kanban.main.create_default_app", fake_create_default_app)
    monkeypatch.setattr("assistant_agent_kanban.main.uvicorn.run", fake_uvicorn_run)

    main(["serve", "--config", "config.yaml", "--host", "127.0.0.1", "--port", "8001"])

    assert recorded == {
        "config": "config.yaml",
        "app": recorded["app"],
        "host": "127.0.0.1",
        "port": 8001,
        "reload": False,
        "factory": False,
        "access_log": False,
        "log_level": "warning",
    }
    output = capsys.readouterr().out
    assert "Assistant Agent Kanban v" in output
    assert "Dashboard   http://127.0.0.1:8001/" in output
    assert "Logs        quiet mode; warnings and errors will still appear" in output


def test_shutdown_message_is_printed_for_tty(monkeypatch, capsys):
    monkeypatch.setattr("assistant_agent_kanban.main.sys.stdout.isatty", lambda: True)

    _print_shutdown_message()

    assert "Shutting down Assistant Agent Kanban..." in capsys.readouterr().out
