from __future__ import annotations

from fastapi.testclient import TestClient

from fs_kanban_agent.api.app import create_app
from fs_kanban_agent.exceptions import IntegrationError

from .conftest import FakeAdapter, create_request_task
from .test_api import _task_ready_for_completed_reviews


def test_api_returns_json_conflict_when_approve_verification_raises_integration_error(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-approve-integration-error-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-verify-approve-integration-error-task")

    def fail_approve(task_id: str, *, by: str):
        raise IntegrationError("finalize exploded")

    app.state.runtime.verification_service.approve = fail_approve

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200
        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")

    assert approve.status_code == 409
    assert approve.json()["detail"] == "finalize exploded"
