from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from fs_kanban_agent.api.app import create_app
from fs_kanban_agent import config as config_module
from fs_kanban_agent.config import PROJECT_ROOT, load_config
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.opencode_adapter import _parse_discovered_models
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workspace_manager import WorkspaceManager
from fs_kanban_agent.workers.implementer import ImplementerWorker

from .conftest import FakeAdapter, create_request_task


def _task_ready_for_completed_reviews(config, task_name: str):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = next(task for task in scanner.scan() if task.metadata.title == task_name)
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd: Path):
        (cwd / "app.txt").write_text("review me\n")

    implementer = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )
    import asyncio

    asyncio.run(implementer.run_once())
    waiting_reviews = next(task for task in scanner.scan() if task.metadata.title == task_name and task.state == TaskState.WAITING_REVIEWS)
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    return scanner, completed


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
        assert "metadata.json" not in detail.json()["json_files"]
        logs = client.get(f"/api/tasks/{task.metadata.task_id}/logs")
        assert logs.status_code == 200
        assert any(getattr(route, "path", None) == "/api/events" for route in app.routes)


def test_api_returns_runtime_logs_for_task(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "log-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    log_dir = config.runs_dir / task.metadata.task_id
    log_dir.mkdir(parents=True)
    (log_dir / "planner-001.jsonl").write_text('{"type":"final","content":"plan"}\n')

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.metadata.task_id}/logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task.metadata.task_id
    assert payload["entries"][0]["name"] == "planner-001.jsonl"
    assert payload["entries"][0]["content"].startswith('{"type":"final"')
    assert payload["entries"][0]["rendered_content"] == "plan"
    assert "plan" in payload["entries"][0]["content"]


def test_api_allows_editing_plan_md_in_waiting_check_plans(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-edit-task")
    app = create_app(config, FakeAdapter(["## Summary\nplan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    runtime = app.state.runtime
    metadata_store = runtime.planner.metadata_store
    scanner = runtime.planner.scanner
    transitions = runtime.planner.transitions
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("original plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    with TestClient(app) as client:
        get_response = client.get(f"/api/tasks/{waiting.metadata.task_id}/artifacts/PLAN.md")
        assert get_response.status_code == 200
        assert "original plan" in get_response.json()["content"]
        put_response = client.put(
            f"/api/tasks/{waiting.metadata.task_id}/artifacts/PLAN.md",
            json={"content": "edited plan"},
        )
        assert put_response.status_code == 200
        approve_response = client.post(f"/api/tasks/{waiting.metadata.task_id}/approve-plan")
        assert approve_response.status_code == 200

    updated_task = scanner.find_task(waiting.metadata.task_id)
    assert updated_task.state == TaskState.TODOS
    assert (updated_task.task_dir / "PLAN.md").read_text() == "edited plan\n"


def test_api_rejects_plan_md_edit_outside_waiting_check_plans(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "plan-edit-reject-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]

    with TestClient(app) as client:
        response = client.put(f"/api/tasks/{task.metadata.task_id}/artifacts/PLAN.md", json={"content": "nope"})

    assert response.status_code == 409


def test_api_supports_human_verification_start_and_reject(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-api-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-api-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200
        assert start.json()["state"] == TaskState.HUMAN_VERIFYING.value
        assert (repo_root / "app.txt").read_text() == "review me\n"

        reject = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reject-verification",
            json={"note": "Need another pass."},
        )
        assert reject.status_code == 200
        assert reject.json()["state"] == TaskState.TODOS.value
        assert (repo_root / "app.txt").read_text() == "hello\n"


def test_api_supports_human_verification_approve(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-approve-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-approve-task")

    with TestClient(app) as client:
        client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")
        assert approve.status_code == 200
        assert approve.json()["state"] == TaskState.DONE.value


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
        task_dir = _locate_task_dir(config, Path(created_path).name)
        request_markdown = (task_dir / "REQUEST.md").read_text()
        assert len(task_dir.name) == 7
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
    task_dir = _locate_task_dir(config, Path(response.json()["task_path"]).name)
    request_markdown = (task_dir / "REQUEST.md").read_text()
    assert "## Scope" in request_markdown
    assert f"Limit code changes to `{target_repo}`." in request_markdown
    assert "## Out of Scope" in request_markdown
    assert f"Do not modify files outside `{target_repo}`." in request_markdown


def test_api_reads_and_updates_model_settings(configured_paths, tmp_path):
    config, _, _ = configured_paths
    config_path = tmp_path / "dashboard-config.yaml"
    config.persist(config_path)
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        get_response = client.get("/api/settings/models")
        assert get_response.status_code == 200
        assert get_response.json()["planner_model"] is None
        assert get_response.json()["config_path"] == str(config_path.resolve())
        assert get_response.json()["available_models"] == ["gpt-5", "o3-mini"]
        assert get_response.json()["discovery_status"] == "ready"
        assert get_response.json()["discovery_error"] is None
        assert planner_adapter.discovery_calls == [False]

        put_response = client.put(
            "/api/settings/models",
            json={
                "planner_model": "gpt-5-planner",
                "implementer_model": " gpt-5-implementer ",
                "reviewer_model": "",
                "commit_model": "gpt-5-commit",
            },
        )

    assert put_response.status_code == 200
    payload = put_response.json()
    assert payload["saved"] is True
    assert payload["planner_model"] == "gpt-5-planner"
    assert payload["implementer_model"] == "gpt-5-implementer"
    assert payload["reviewer_model"] is None
    assert payload["commit_model"] == "gpt-5-commit"
    assert app.state.runtime.config.opencode.planner_model == "gpt-5-planner"
    assert app.state.runtime.config.opencode.implementer_model == "gpt-5-implementer"
    assert app.state.runtime.config.opencode.reviewer_model is None
    assert load_config(config_path).opencode.commit_model == "gpt-5-commit"


def test_api_exposes_captured_stage_models_in_board_and_task_detail(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "stage-model-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    planning.metadata.plan.revision = 1
    planning.metadata.plan.resolved_model = "openai/gpt-5.4"
    planning.metadata.implementation.resolved_model = "github-copilot/gpt-5"
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    board_snapshot = scanner.board_snapshot()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{planning.metadata.task_id}")

    planning_column = next(column for column in board_snapshot.model_dump(mode="json")["columns"] if column["state"] == TaskState.PLANNING.value)
    assert planning_column["items"][0]["active_model"] == "openai/gpt-5.4"
    assert detail.status_code == 200
    assert detail.json()["metadata"]["plan"]["resolved_model"] == "openai/gpt-5.4"
    assert detail.json()["metadata"]["implementation"]["resolved_model"] == "github-copilot/gpt-5"
    assert detail.json()["metadata"]["review"]["resolved_model"] is None


def test_api_persists_model_settings_to_default_local_config_when_unloaded(configured_paths, tmp_path):
    config, _, _ = configured_paths
    default_local_path = tmp_path / "config.local.yaml"
    original_default_local_path = config_module.DEFAULT_LOCAL_CONFIG_PATH
    config_module.DEFAULT_LOCAL_CONFIG_PATH = default_local_path
    try:
        app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

        with TestClient(app) as client:
            response = client.put(
                "/api/settings/models",
                json={
                    "planner_model": "planner-x",
                    "implementer_model": None,
                    "reviewer_model": "reviewer-y",
                    "commit_model": None,
                },
            )

        assert response.status_code == 200
        assert default_local_path.exists()
        persisted = load_config(default_local_path)
        assert persisted.opencode.planner_model == "planner-x"
        assert persisted.opencode.reviewer_model == "reviewer-y"
        assert response.json()["config_path"] == str(default_local_path.resolve())
    finally:
        config_module.DEFAULT_LOCAL_CONFIG_PATH = original_default_local_path


def test_api_save_materializes_runtime_agents_immediately(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime_agents_dir = config.kanban_root / "_runtime" / "opencode-config" / "opencode" / "agents"
    planner_agent_path = runtime_agents_dir / f"{config.opencode.planner_agent}.md"
    reviewer_agent_path = runtime_agents_dir / f"{config.opencode.reviewer_agent}.md"

    with TestClient(app) as client:
        first_save = client.put(
            "/api/settings/models",
            json={
                "planner_model": "openai/gpt-5.4",
                "implementer_model": None,
                "reviewer_model": "github-copilot/gpt-5",
                "commit_model": None,
            },
        )
        assert first_save.status_code == 200
        assert planner_agent_path.exists()
        assert reviewer_agent_path.exists()
        assert "model: openai/gpt-5.4" in planner_agent_path.read_text()
        assert "model: github-copilot/gpt-5" in reviewer_agent_path.read_text()

        second_save = client.put(
            "/api/settings/models",
            json={
                "planner_model": None,
                "implementer_model": None,
                "reviewer_model": None,
                "commit_model": None,
            },
        )
        assert second_save.status_code == 200

    assert planner_agent_path.read_text() == (PROJECT_ROOT / ".opencode" / "agents" / f"{config.opencode.planner_agent}.md").read_text()
    assert reviewer_agent_path.read_text() == (PROJECT_ROOT / ".opencode" / "agents" / f"{config.opencode.reviewer_agent}.md").read_text()


def test_api_refreshes_model_discovery_and_keeps_cached_options_on_failure(configured_paths):
    config, _, _ = configured_paths
    planner_adapter = FakeAdapter(
        ["plan"],
        discovery_responses=[["gpt-5", "claude-3.7-sonnet"], RuntimeError("opencode models failed")],
    )
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        initial = client.get("/api/settings/models")
        assert initial.status_code == 200
        assert initial.json()["available_models"] == ["gpt-5", "claude-3.7-sonnet"]
        refreshed = client.get("/api/settings/models?refresh=true")

    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["available_models"] == ["gpt-5", "claude-3.7-sonnet"]
    assert payload["discovery_status"] == "fallback"
    assert payload["discovery_error"] == "opencode models failed"
    assert planner_adapter.discovery_calls == [False, True]


def test_parse_discovered_models_ignores_verbose_json_metadata():
    verbose_output = """openai/gpt-5.4
{
  \"id\": \"gpt-5.4\",
  \"providerID\": \"openai\",
  \"name\": \"GPT-5.4\"
}
github-copilot/gpt-5
{
  \"id\": \"gpt-5\",
  \"providerID\": \"github-copilot\",
  \"name\": \"GPT-5\"
}
"""

    assert _parse_discovered_models(verbose_output) == ["openai/gpt-5.4", "github-copilot/gpt-5"]


def test_dashboard_page_includes_request_form(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Create request" in response.text
    assert "Model settings" in response.text
    assert "Acceptance criteria" in response.text
    assert "JSON files" in response.text
    assert "/api/requests" in response.text
    assert "/api/settings/models" in response.text
    assert "target-repo-options" in response.text
    assert "request-modal" in response.text
    assert "settings-modal" in response.text
    assert "task-modal" in response.text
    assert "Viewer" in response.text
    assert "Viewer mode" in response.text
    assert "planner_model" in response.text
    assert "implementer_model" in response.text
    assert "reviewer_model" in response.text
    assert "commit_model" in response.text
    assert "opencode-model-options" in response.text
    assert "Refresh discovered models" in response.text
    assert "Save model settings" in response.text
    assert "task-viewer-host" in response.text
    assert "Approve plan" in response.text
    assert "toastui-editor" in response.text
    assert "buildScopeDefaults" in response.text
    assert "buildOutOfScopeDefaults" in response.text
    assert "/api/tasks/${taskId}/logs" in response.text
    assert "worker_log" in response.text
    assert "data-active-since" in response.text
    assert "/api/tasks/${activeTaskId}/approve-plan" in response.text
    assert "/api/tasks/${activeTaskId}/start-verification" in response.text
    assert "/api/tasks/${activeTaskId}/reject-verification" in response.text
    assert "/api/tasks/${activeTaskId}/approve-verification" in response.text
    assert "Approve &amp; commit" in response.text
    assert "Captured stage models" in response.text
    assert "Current stage model used" in response.text
    assert "Planner model used" in response.text
    assert "Implementer model used" in response.text
    assert "Reviewer model used" in response.text
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


def _locate_task_dir(config, key: str) -> Path:
    for state_dir in config.kanban_root.iterdir():
        if not state_dir.is_dir() or state_dir.name == "_runtime":
            continue
        candidate = state_dir / key
        if candidate.exists():
            return candidate
    raise FileNotFoundError(key)
