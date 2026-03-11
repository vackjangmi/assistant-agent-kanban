from __future__ import annotations

import json
import subprocess
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
        detail_payload = detail.json()
        assert detail_payload["request_markdown_path"] == str((Path(detail_payload["task_path"]) / "REQUEST.md").resolve())
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


def test_api_renders_tool_only_runtime_logs_for_task(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "tool-log-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    log_dir = config.runs_dir / task.metadata.task_id
    log_dir.mkdir(parents=True)
    (log_dir / "planner-001.jsonl").write_text(
        '\n'.join(
            [
                '{"type":"step_start"}',
                '{"type":"tool_use","part":{"tool":"read","state":{"status":"error","error":"Error: File not found"}}}',
            ]
        )
        + '\n'
    )

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.metadata.task_id}/logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entries"][0]["rendered_content"] == "Started agent step\n\nTool `read` failed: Error: File not found"


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


def test_api_deletes_task_and_owned_runtime_artifacts(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "delete-api-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    workspace_root = config.workspace.root / task.metadata.task_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "repo").mkdir()
    run_dir = config.runs_dir / task.metadata.task_id
    run_dir.mkdir(parents=True)
    (run_dir / "planner-001.jsonl").write_text("log\n")

    with TestClient(app) as client:
        response = client.delete(f"/api/tasks/{task.metadata.task_id}")
        assert response.status_code == 200
        assert response.json() == {"deleted": True, "task_id": task.metadata.task_id}
        assert client.get(f"/api/tasks/{task.metadata.task_id}").status_code == 404

    assert not workspace_root.exists()
    assert not run_dir.exists()
    board = KanbanScanner(config).board_snapshot().model_dump(mode="json")
    assert all(item["task_id"] != task.metadata.task_id for column in board["columns"] for item in column["items"])


def test_api_rejects_delete_for_active_task_state(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    task_dir = config.state_dir(TaskState.HUMAN_VERIFYING) / "delete-blocked-task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# blocked task\n")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]

    with TestClient(app) as client:
        response = client.delete(f"/api/tasks/{task.metadata.task_id}")

    assert response.status_code == 409
    assert "blocked while state is human-verifying" in response.json()["detail"]


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


def test_api_uses_runtime_default_base_branch_when_request_omits_it(configured_paths, tmp_path):
    config, _, _ = configured_paths
    config.base_branch = "develop"
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.post(
            "/api/requests",
            json={
                "title": "Default branch task",
                "goal": "Use the runtime default branch.",
                "target_repo": str(target_repo),
            },
        )

    assert response.status_code == 200
    task_dir = _locate_task_dir(config, Path(response.json()["task_path"]).name)
    request_markdown = (task_dir / "REQUEST.md").read_text()
    assert "base_branch: develop" in request_markdown


def test_api_reads_and_updates_model_settings(configured_paths, tmp_path, monkeypatch):
    config, _, _ = configured_paths
    config_path = tmp_path / "dashboard-config.yaml"
    config.persist(config_path)
    omo_root = tmp_path / "xdg-config"
    omo_config_dir = omo_root / "opencode"
    omo_config_dir.mkdir(parents=True)
    (omo_config_dir / "oh-my-opencode.json").write_text(
        json.dumps(
            {
                "agents": {
                    "explore": {"model": "openai/gpt-5-mini", "variant": "low"},
                    "librarian": {"model": "openai/gpt-5-mini", "variant": "low"},
                },
                "categories": {
                    "quick": {"model": "openai/gpt-5-nano", "variant": "low"},
                },
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(omo_root))
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        get_response = client.get("/api/settings/models")
        assert get_response.status_code == 200
        assert get_response.json()["planner_model"] is None
        assert get_response.json()["repo_discovery_root"] == str(config.repo_discovery.root)
        assert get_response.json()["repo_discovery_max_depth"] == config.repo_discovery.max_depth
        assert get_response.json()["config_path"] == str(config_path.resolve())
        assert get_response.json()["available_models"] == ["gpt-5", "o3-mini"]
        assert get_response.json()["discovery_status"] == "ready"
        assert get_response.json()["discovery_error"] is None
        assert get_response.json()["delegated_model_status"] == "ready"
        assert get_response.json()["delegated_model_source_path"] == str((omo_config_dir / "oh-my-opencode.json").resolve())
        assert get_response.json()["delegated_models"] == [
            {"key": "quick", "source_type": "category", "model": "openai/gpt-5-nano", "variant": "low"},
            {"key": "explore", "source_type": "agent", "model": "openai/gpt-5-mini", "variant": "low"},
            {"key": "librarian", "source_type": "agent", "model": "openai/gpt-5-mini", "variant": "low"},
        ]
        assert planner_adapter.discovery_calls == [False]

        put_response = client.put(
            "/api/settings/models",
            json={
                "planner_model": "gpt-5-planner",
                "implementer_model": " gpt-5-implementer ",
                "reviewer_model": "",
                "commit_model": "gpt-5-commit",
                "repo_discovery_root": "../",
                "repo_discovery_max_depth": 4,
            },
        )

    assert put_response.status_code == 200
    payload = put_response.json()
    assert payload["saved"] is True
    assert payload["planner_model"] == "gpt-5-planner"
    assert payload["implementer_model"] == "gpt-5-implementer"
    assert payload["reviewer_model"] is None
    assert payload["commit_model"] == "gpt-5-commit"
    assert payload["repo_discovery_root"] == "../"
    assert payload["repo_discovery_max_depth"] == 4
    assert app.state.runtime.config.opencode.planner_model == "gpt-5-planner"
    assert app.state.runtime.config.opencode.implementer_model == "gpt-5-implementer"
    assert app.state.runtime.config.opencode.reviewer_model is None
    assert app.state.runtime.config.repo_discovery.root == "../"
    assert app.state.runtime.config.repo_discovery.max_depth == 4
    assert load_config(config_path).opencode.commit_model == "gpt-5-commit"
    assert load_config(config_path).repo_discovery.root == "../"
    assert load_config(config_path).repo_discovery.max_depth == 4


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
                "repo_discovery_root": "/tmp/scan-root",
                "repo_discovery_max_depth": 3,
            },
        )

        assert response.status_code == 200
        assert default_local_path.exists()
        persisted = load_config(default_local_path)
        assert persisted.opencode.planner_model == "planner-x"
        assert persisted.opencode.reviewer_model == "reviewer-y"
        assert persisted.repo_discovery.root == "/tmp/scan-root"
        assert persisted.repo_discovery.max_depth == 3
        assert response.json()["config_path"] == str(default_local_path.resolve())
    finally:
        config_module.DEFAULT_LOCAL_CONFIG_PATH = original_default_local_path


def test_api_preserves_repo_discovery_root_when_put_payload_omits_it(configured_paths):
    config, _, _ = configured_paths
    config.repo_discovery.root = "../custom-root"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "planner_model": "planner-x",
                "implementer_model": None,
                "reviewer_model": None,
                "commit_model": None,
                "repo_discovery_max_depth": 5,
            },
        )

    assert response.status_code == 200
    assert response.json()["repo_discovery_root"] == "../custom-root"
    assert response.json()["repo_discovery_max_depth"] == 5
    assert app.state.runtime.config.repo_discovery.root == "../custom-root"


def test_api_save_materializes_runtime_agents_immediately(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime_agents_dir = config.kanban_root / "_runtime" / "opencode-config" / "opencode" / "agents"
    planner_agent_path = runtime_agents_dir / f"{config.opencode.planner_agent}.md"
    implementer_agent_path = runtime_agents_dir / f"{config.opencode.implementer_agent}.md"
    reviewer_agent_path = runtime_agents_dir / f"{config.opencode.reviewer_agent}.md"

    with TestClient(app) as client:
        first_save = client.put(
            "/api/settings/models",
            json={
                "planner_model": "openai/gpt-5.4",
                "implementer_model": "openai/gpt-5.4-mini",
                "reviewer_model": "github-copilot/gpt-5",
                "commit_model": None,
            },
        )
        assert first_save.status_code == 200
        assert planner_agent_path.exists()
        assert implementer_agent_path.exists()
        assert reviewer_agent_path.exists()
        assert "model: openai/gpt-5.4" in planner_agent_path.read_text()
        assert "model: openai/gpt-5.4-mini" in implementer_agent_path.read_text()
        assert "model: github-copilot/gpt-5" in reviewer_agent_path.read_text()
        assert "Do not call `task()` or delegate helper subtasks." in planner_agent_path.read_text()
        assert "Write the plan directly in this response." in planner_agent_path.read_text()
        assert "Do not delegate the final file edits" in implementer_agent_path.read_text()
        assert "Write the review directly in this response." in reviewer_agent_path.read_text()
        assert "Prefer `Verdict: PASS` when only minor follow-up notes remain" in reviewer_agent_path.read_text()

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
    assert implementer_agent_path.read_text() == (PROJECT_ROOT / ".opencode" / "agents" / f"{config.opencode.implementer_agent}.md").read_text()
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
    assert "Runtime settings" in response.text
    assert "Acceptance criteria" in response.text
    assert "JSON files" in response.text
    assert "/api/requests" in response.text
    assert "/api/settings/models" in response.text
    assert "target-repo-options" in response.text
    assert "base-branch-options" in response.text
    assert "request-modal" in response.text
    assert "settings-modal" in response.text
    assert "task-modal" in response.text
    assert "Viewer" in response.text
    assert "Viewer mode" in response.text
    assert "planner_model" in response.text
    assert "implementer_model" in response.text
    assert "reviewer_model" in response.text
    assert "commit_model" in response.text
    assert "repo_discovery_root" in response.text
    assert "repo_discovery_max_depth" in response.text
    assert "opencode-model-options" in response.text
    assert "Refresh discovered models" in response.text
    assert "Save runtime settings" in response.text
    assert "Repo discovery root" in response.text
    assert "Repo discovery depth" in response.text
    assert "task-viewer-host" in response.text
    assert "Approve plan" in response.text
    assert "toastui-editor" in response.text
    assert "buildScopeDefaults" in response.text
    assert "buildOutOfScopeDefaults" in response.text
    assert "fs-kanban-agent.last-target-repo" in response.text
    assert "window.localStorage.setItem(lastTargetRepoStorageKey, normalized)" in response.text
    assert "applyTargetRepoAutofill(currentTargetRepoOptions())" in response.text
    assert "resetFormState(); setModalOpen(true); await loadTargetRepoBranches();" in response.text
    assert "/api/target-repo-branches?target_repo=${encodeURIComponent(repoPath)}" in response.text
    assert "/api/tasks/${taskId}/logs" in response.text
    assert "typeof payload.content !== 'string'" in response.text
    assert "worker_log" in response.text
    assert "loadTaskLogs(activeTaskId, true)" not in response.text
    assert "maybeStartLogPolling" not in response.text
    assert "setInterval(() => {" not in response.text
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
    assert "REQUEST.md path" in response.text
    assert "Delete task" in response.text
    assert "This permanently removes the task directory and any managed workspace artifacts created for it." in response.text
    assert "method: 'DELETE'" in response.text
    assert 'const defaultTargetRepo = "";' in response.text


def test_dashboard_page_uses_custom_discovery_root_as_default_target(configured_paths, tmp_path):
    config, _, _ = configured_paths
    config.repo_discovery.root = str(tmp_path / "custom-root")
    Path(config.repo_discovery.root).mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'const defaultTargetRepo = "";' in response.text


def test_api_lists_target_repo_suggestions_by_configured_depth(configured_paths, tmp_path):
    config, _, _ = configured_paths
    scan_root = tmp_path / "workspace-root"
    alpha = scan_root / "alpha"
    nested = scan_root / "app" / "sudoku"
    alpha.mkdir(parents=True)
    nested.mkdir(parents=True)
    config.repo_discovery.root = str(scan_root)
    config.repo_discovery.max_depth = 2
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/target-repos")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root"] == str(scan_root)
    assert payload["resolved_root"] == str(scan_root.resolve())
    assert payload["max_depth"] == 2
    assert str(alpha.resolve()) in payload["items"]
    assert str(nested.resolve()) in payload["items"]


def test_api_lists_branches_for_selected_target_repo(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "branch-target"
    target_repo.mkdir()
    from .conftest import init_git_repo

    init_git_repo(target_repo)
    subprocess.run(["git", "-C", str(target_repo), "checkout", "-b", "develop"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "checkout", "main"], check=True, capture_output=True, text=True)
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/target-repo-branches", params={"target_repo": str(target_repo)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_root"] == str(target_repo.resolve())
    assert payload["git_repository"] is True
    assert payload["current_branch"] == "main"
    assert payload["suggested_base_branch"] == "main"
    assert "main" in payload["branches"]
    assert "develop" in payload["branches"]


def test_api_reports_non_git_target_repo_without_branch_options(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "plain-dir"
    target_repo.mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/target-repo-branches", params={"target_repo": str(target_repo)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_root"] == str(target_repo.resolve())
    assert payload["git_repository"] is False
    assert payload["branches"] == []
    assert payload["suggested_base_branch"] == config.base_branch


def test_api_suggests_current_feature_branch_when_selected_repo_is_on_it(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "feature-branch-target"
    target_repo.mkdir()
    from .conftest import init_git_repo

    init_git_repo(target_repo)
    subprocess.run(["git", "-C", str(target_repo), "checkout", "-b", "feature/request-form"], check=True, capture_output=True, text=True)
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/target-repo-branches", params={"target_repo": str(target_repo)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_branch"] == "feature/request-form"
    assert payload["suggested_base_branch"] == "feature/request-form"
    assert payload["branches"][0] == "feature/request-form"


def test_api_target_repo_suggestions_respect_depth_limit(configured_paths, tmp_path):
    config, _, _ = configured_paths
    scan_root = tmp_path / "workspace-root"
    allowed = scan_root / "app" / "sudoku"
    blocked = scan_root / "app" / "games" / "sudoku-deep"
    allowed.mkdir(parents=True)
    blocked.mkdir(parents=True)
    config.repo_discovery.root = str(scan_root)
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
        config.repo_discovery.root = str(PROJECT_ROOT.parent)
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
        config.repo_discovery.root = str(PROJECT_ROOT.parent)
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
