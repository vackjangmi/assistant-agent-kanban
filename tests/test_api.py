from __future__ import annotations

from fastapi.testclient import TestClient

from fs_kanban_agent.api.app import create_app
from fs_kanban_agent.scanner import KanbanScanner

from .conftest import FakeAdapter, create_request_task


def test_api_exposes_health_board_task_and_events(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "api-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        board = client.get("/api/board")
        assert board.status_code == 200
        assert board.json()["columns"][0]["state"] == "requests"
        detail = client.get(f"/api/tasks/{task.metadata.task_id}")
        assert detail.status_code == 200
        assert any(route.path == "/api/events" for route in app.routes)
