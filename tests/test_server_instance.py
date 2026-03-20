from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.exceptions import ServerAlreadyRunningError

from .conftest import FakeAdapter


def test_server_startup_blocks_second_instance_for_same_kanban_root(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    first_app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    second_app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(first_app):
        with pytest.raises(ServerAlreadyRunningError, match="already running"):
            with TestClient(second_app):
                pass


def test_server_lock_releases_after_shutdown(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    first_app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    second_app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(first_app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}

    with TestClient(second_app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
