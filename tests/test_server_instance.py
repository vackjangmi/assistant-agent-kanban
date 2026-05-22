from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.exceptions import NoSupportedAssistantError, ServerAlreadyRunningError

from .conftest import FakeAdapter


class UnavailableAdapter(FakeAdapter):
    def __init__(self, binary: str) -> None:
        super().__init__([])
        self.binary = binary

    def availability_error(self, *, config, backend):
        del config, backend
        return f"binary not found on PATH: {self.binary}"


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


def test_server_startup_warms_model_snapshots(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.runtime.role_backends.implementer = "codex"
    config.runtime.role_backends.reviewer = "claude"
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    implementer_adapter = FakeAdapter(["impl"], discovery_responses=[["gpt-5.4", "gpt-5"]])
    reviewer_adapter = FakeAdapter(["Verdict: PASS"], discovery_responses=[["default", "sonnet"]])
    app = create_app(
        config,
        planner_adapter,
        implementer_adapter,
        reviewer_adapter,
        adapter_registry={
            "opencode": planner_adapter,
            "codex": implementer_adapter,
            "claude": reviewer_adapter,
        },
    )

    with TestClient(app):
        assert planner_adapter.discovery_calls == [False]
        assert implementer_adapter.discovery_calls == [False]
        assert reviewer_adapter.discovery_calls == [False]


def test_server_startup_fails_when_no_supported_assistant_cli_is_available(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    adapters = {
        "opencode": UnavailableAdapter("opencode"),
        "codex": UnavailableAdapter("codex"),
        "gemini": UnavailableAdapter("gemini"),
        "claude": UnavailableAdapter("claude"),
    }
    app = create_app(
        config,
        adapters["opencode"],
        adapters["opencode"],
        adapters["opencode"],
        adapter_registry=adapters,
    )

    with pytest.raises(NoSupportedAssistantError, match="No supported assistant CLI is available"):
        with TestClient(app):
            pass
