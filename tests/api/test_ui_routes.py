from __future__ import annotations

from fastapi.testclient import TestClient

from assistant_agent_kanban.api.app import create_app

from ..conftest import FakeAdapter


def _app(configured_paths):
    config, _, _ = configured_paths
    return create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))


def test_clean_ui_paths_render_dashboard(configured_paths):
    app = _app(configured_paths)

    with TestClient(app) as client:
        for path in [
            "/",
            "/board/implementation",
            "/tasks/task-123/logs",
            "/settings/roles",
            "/requests/new/assistant",
        ]:
            response = client.get(path, headers={"accept": "text/html"})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            assert "<title>Assistant Agent Kanban</title>" in response.text


def test_unknown_paths_do_not_fall_back_to_dashboard(configured_paths):
    app = _app(configured_paths)

    with TestClient(app) as client:
        response = client.get("/not-a-dashboard-route")
        assert response.status_code == 404

