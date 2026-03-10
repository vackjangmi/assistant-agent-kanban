from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from fs_kanban_agent.api.app import create_app
from fs_kanban_agent.config import PROJECT_ROOT
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


def test_api_creates_request_from_dashboard_form(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.post(
            "/api/requests",
            json={
                "title": "Refactor login flow",
                "goal": "Ship a safer login refactor.",
                "background": "Current auth flow is hard to change.",
                "scope": "Refactor login form\nKeep API contract stable",
                "out_of_scope": "No signup changes",
                "constraints": "Keep tests passing",
                "references": "src/auth.py\ntests/test_auth.py",
                "acceptance_criteria": "Users can still sign in\nTests stay green",
                "target_repo": str(target_repo),
                "base_branch": "develop",
            },
        )

        assert response.status_code == 200
        created_path = response.json()["task_path"]
        request_markdown = (config.kanban_root / "requests" / "refactor-login-flow" / "REQUEST.md").read_text()
        assert created_path.endswith("requests/refactor-login-flow")
        assert "## Goal" in request_markdown
        assert "## Acceptance Criteria" in request_markdown
        assert f"repo_root: {target_repo.resolve()}" in request_markdown
        assert "base_branch: develop" in request_markdown


def test_api_creates_default_scope_sections_when_blank(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.post(
            "/api/requests",
            json={
                "title": "Sudoku cleanup task",
                "goal": "Improve the sudoku app safely.",
                "target_repo": str(target_repo),
                "base_branch": "main",
            },
        )

    assert response.status_code == 200
    request_markdown = (config.kanban_root / "requests" / "sudoku-cleanup-task" / "REQUEST.md").read_text()
    assert "## Scope" in request_markdown
    assert f"Limit code changes to `{target_repo}`." in request_markdown
    assert "## Out of Scope" in request_markdown
    assert f"Do not modify files outside `{target_repo}`." in request_markdown


def test_dashboard_page_includes_request_form(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Create request" in response.text
    assert "Acceptance criteria" in response.text
    assert "/api/requests" in response.text
    assert "target-repo-options" in response.text
    assert "request-modal" in response.text
    assert "buildScopeDefaults" in response.text
    assert "buildOutOfScopeDefaults" in response.text
    assert f'const defaultTargetRepo = "{PROJECT_ROOT.parent}";' in response.text


def test_dashboard_page_uses_custom_discovery_root_as_default_target(configured_paths, tmp_path):
    config, _, _ = configured_paths
    config.repo_discovery.root = tmp_path / "custom-root"
    config.repo_discovery.root.mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert f'const defaultTargetRepo = "{config.repo_discovery.root}";' in response.text


def test_api_lists_target_repo_suggestions_by_configured_depth(configured_paths, tmp_path):
    config, _, _ = configured_paths
    scan_root = tmp_path / "workspace-root"
    alpha = scan_root / "alpha"
    nested = scan_root / "app" / "sudoku"
    alpha.mkdir(parents=True)
    nested.mkdir(parents=True)
    config.repo_discovery.root = scan_root
    config.repo_discovery.max_depth = 2
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/target-repos")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root"] == str(scan_root)
    assert payload["max_depth"] == 2
    assert str(alpha.resolve()) in payload["items"]
    assert str(nested.resolve()) in payload["items"]


def test_api_target_repo_suggestions_respect_depth_limit(configured_paths, tmp_path):
    config, _, _ = configured_paths
    scan_root = tmp_path / "workspace-root"
    allowed = scan_root / "app" / "sudoku"
    blocked = scan_root / "app" / "games" / "sudoku-deep"
    allowed.mkdir(parents=True)
    blocked.mkdir(parents=True)
    config.repo_discovery.root = scan_root
    config.repo_discovery.max_depth = 2
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/target-repos")

    assert response.status_code == 200
    items = response.json()["items"]
    assert str(allowed.resolve()) in items
    assert str(blocked.resolve()) not in items


def test_api_target_repo_suggestions_default_to_workboard_parent(configured_paths):
    config, _, _ = configured_paths
    child = PROJECT_ROOT.parent / "tmp-target-root-child"
    child.mkdir(exist_ok=True)
    try:
        config.repo_discovery.root = PROJECT_ROOT.parent
        config.repo_discovery.max_depth = 2
        app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

        with TestClient(app) as client:
            response = client.get("/api/target-repos")

        assert response.status_code == 200
        payload = response.json()
        items = payload["items"]
        assert payload["root"] == str(PROJECT_ROOT.parent)
        assert str(child) in items
        assert str(PROJECT_ROOT) not in items
        assert all(not item.startswith(f"{PROJECT_ROOT}/") for item in items)
        assert str(config.kanban_root) not in items
    finally:
        shutil.rmtree(child, ignore_errors=True)


def test_api_target_repo_suggestions_include_second_depth_from_parent_root(configured_paths):
    config, _, _ = configured_paths
    parent = PROJECT_ROOT.parent / "tmp-repo-discovery"
    nested = parent / "sudoku"
    nested.mkdir(parents=True, exist_ok=True)
    try:
        config.repo_discovery.root = PROJECT_ROOT.parent
        config.repo_discovery.max_depth = 2
        app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

        with TestClient(app) as client:
            response = client.get("/api/target-repos")

        assert response.status_code == 200
        assert str(nested) in response.json()["items"]
    finally:
        shutil.rmtree(parent, ignore_errors=True)
