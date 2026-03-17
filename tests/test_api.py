from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import subprocess
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from fs_kanban_agent.api.app import create_app
from fs_kanban_agent.api.ui import TEMPLATE_PATH
from fs_kanban_agent import config as config_module
from fs_kanban_agent.config import PROJECT_ROOT, load_config
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.models import HistoryEntry
from fs_kanban_agent.opencode_adapter import _parse_discovered_models
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workspace_manager import WorkspaceManager
from fs_kanban_agent.workers.implementer import ImplementerWorker
from fs_kanban_agent.models import utc_now

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
    assert payload["entries"][0]["rendered_content"] == "plan"
    assert payload["entries"][0]["debug_rendered_content"] == "plan"
    assert "content" not in payload["entries"][0]


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


def test_api_exposes_debug_runtime_log_metadata_for_task(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "debug-log-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    log_dir = config.runs_dir / task.metadata.task_id
    log_dir.mkdir(parents=True)
    (log_dir / "planner-001.jsonl").write_text('{"type":"step_finish","tokens":{"total":42,"input":30,"output":12,"reasoning":7,"cache":{"read":5}},"durationMs":1800}\n')

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.metadata.task_id}/logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entries"][0]["rendered_content"] is None
    assert "Debug tokens" in payload["entries"][0]["debug_rendered_content"]
    assert "reasoning=7" in payload["entries"][0]["debug_rendered_content"]


def test_api_keeps_metadata_only_logs_out_of_readable_view(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "metadata-only-log-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    log_dir = config.runs_dir / task.metadata.task_id
    log_dir.mkdir(parents=True)
    (log_dir / "planner-001.jsonl").write_text('{"type":"step_finish","tokens":{"total":8,"input":5,"output":3,"reasoning":2}}\n')

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.metadata.task_id}/logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entries"][0]["rendered_content"] is None
    assert "Debug tokens" in payload["entries"][0]["debug_rendered_content"]


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


def test_api_rejects_empty_plan_md_edit_in_waiting_check_plans(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-empty-edit-task")
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
        response = client.put(
            f"/api/tasks/{waiting.metadata.task_id}/artifacts/PLAN.md",
            json={"content": "   \n\n"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "PLAN.md cannot be empty"
    assert (waiting.task_dir / "PLAN.md").read_text() == "original plan\n"


def test_api_rejects_plan_md_edit_outside_waiting_check_plans(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "plan-edit-reject-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]

    with TestClient(app) as client:
        response = client.put(f"/api/tasks/{task.metadata.task_id}/artifacts/PLAN.md", json={"content": "nope"})

    assert response.status_code == 409


def test_api_uploads_and_serves_plan_attachments(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-attachment-task")
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
        upload = client.post(
            f"/api/tasks/{waiting.metadata.task_id}/attachments?artifact=PLAN.md",
            files={"file": ("diagram.png", b"pngdata", "image/png")},
        )
        assert upload.status_code == 200
        payload = upload.json()
        assert payload["filename"].endswith(".png")
        assert payload["relative_path"].startswith("_attachments/")
        assert payload["url"].endswith(payload["filename"])

        save = client.put(
            f"/api/tasks/{waiting.metadata.task_id}/artifacts/PLAN.md",
            json={"content": f"![diagram]({payload['relative_path']})"},
        )
        assert save.status_code == 200
        assert (waiting.task_dir / "PLAN.md").read_text() == f"![diagram]({payload['relative_path']})\n"

        download = client.get(payload["url"])
        assert download.status_code == 200
        assert download.content == b"pngdata"
        assert download.headers["content-type"] == "image/png"


def test_api_extracts_embedded_plan_data_images_to_attachments(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-embedded-image-task")
    app = create_app(config, FakeAdapter(["## Summary\nplan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    runtime = app.state.runtime
    metadata_store = runtime.planner.metadata_store
    scanner = runtime.planner.scanner
    transitions = runtime.planner.transitions
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("original plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    embedded = base64.b64encode(b"pngdata").decode()
    markdown = f"![diagram](data:image/png;base64,{embedded})"

    with TestClient(app) as client:
        save = client.put(
            f"/api/tasks/{waiting.metadata.task_id}/artifacts/PLAN.md",
            json={"content": markdown},
        )
        assert save.status_code == 200

    saved_content = (waiting.task_dir / "PLAN.md").read_text()
    assert saved_content.startswith("![diagram](_attachments/")
    assert saved_content.endswith(")\n")
    attachments = list((waiting.task_dir / "_attachments").glob("*.png"))
    assert len(attachments) == 1
    assert attachments[0].read_bytes() == b"pngdata"


def test_api_rejects_invalid_embedded_plan_image_data(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-invalid-image-task")
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
        response = client.put(
            f"/api/tasks/{waiting.metadata.task_id}/artifacts/PLAN.md",
            json={"content": "![broken](data:image/png;base64,not-valid)"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "embedded image data is invalid"
    assert (waiting.task_dir / "PLAN.md").read_text() == "original plan\n"


def test_api_rejects_plan_attachment_upload_outside_waiting_check_plans(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "plan-attachment-reject-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task.metadata.task_id}/attachments?artifact=PLAN.md",
            files={"file": ("diagram.png", b"pngdata", "image/png")},
        )

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


def test_api_blocks_reject_without_note_or_line_comment(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-reject-needs-feedback-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-verify-reject-needs-feedback-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        reject = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reject-verification",
            json={"note": ""},
        )

    assert reject.status_code == 409
    assert "request changes is only available after adding a review note or line comment" in reject.json()["detail"]


def test_api_supports_human_verification_approve(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-approve-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-approve-task")

    with TestClient(app) as client:
        client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        approve = client.post(
            f"/api/tasks/{completed.metadata.task_id}/approve-verification",
            json={"completion_mode": "new-branch"},
        )
        assert approve.status_code == 200
        assert approve.json()["state"] == TaskState.DONE.value
        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["integration"]["final_branch"] == f"feature/{completed.metadata.task_id.lower()}-{completed.metadata.slug}"


def test_api_supports_human_verification_approve_to_target_branch(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-approve-target-branch-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-verify-approve-target-branch-task")

    with TestClient(app) as client:
        client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        approve = client.post(
            f"/api/tasks/{completed.metadata.task_id}/approve-verification",
            json={"completion_mode": "target-branch"},
        )
        assert approve.status_code == 200
        assert approve.json()["state"] == TaskState.DONE.value
        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["integration"]["final_branch"] == config.base_branch

    current_branch = subprocess.run(
        ["git", "-C", str(repo_root), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current_branch == config.base_branch


def test_api_creates_and_reads_retrospective(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "retrospective-api-task")
    commit_adapter = FakeAdapter(["# Retrospective\n\n## Summary\nAPI retrospective\n"], resolved_models=["openai/gpt-5-commit"])
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), commit_adapter=commit_adapter)
    _, completed = _task_ready_for_completed_reviews(config, "retrospective-api-task")

    with TestClient(app) as client:
        client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification", json={"completion_mode": "target-branch"})

        inspect_missing = client.post(
            "/api/retrospectives/inspect",
            json={"target_repo_root": str(repo_root), "base_branch": config.base_branch},
        )
        assert inspect_missing.status_code == 200
        assert inspect_missing.json()["exists"] is False

        created = client.post(
            "/api/retrospectives/create",
            json={"target_repo_root": str(repo_root), "base_branch": config.base_branch, "completion_mode": "target-branch"},
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["exists"] is True
        assert payload["created"] is True
        assert payload["can_create"] is True
        assert payload["committed_branch"] == "main"
        assert payload["resolved_model"] == "openai/gpt-5-commit"
        assert (repo_root / payload["repo_relative_path"]).exists()

        inspect_existing = client.post(
            "/api/retrospectives/inspect",
            json={"target_repo_root": str(repo_root), "base_branch": config.base_branch},
        )
        assert inspect_existing.status_code == 200
        assert inspect_existing.json()["exists"] is True
        assert inspect_existing.json()["created"] is False
        assert "API retrospective" in inspect_existing.json()["content"]


def test_api_returns_todos_when_human_verification_rebase_fails(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-approve-conflict-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-approve-conflict-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

    subprocess.run(["git", "-C", str(repo_root), "switch", "main"], check=True, capture_output=True, text=True)
    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    with TestClient(app) as client:
        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")

    assert approve.status_code == 200
    assert approve.json()["state"] == TaskState.TODOS.value
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.final_branch is None


def test_api_exposes_changed_files_for_human_verifying_tasks(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-diff-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-verify-diff-task")

    with TestClient(app) as client:
        before = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert before.status_code == 200
        assert before.json()["changed_files"] == []
        assert before.json()["changed_files_available"] is False

        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["changed_files"] == []
        assert detail.json()["changed_files_available"] is True
        changed_files = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true").json()["changed_files"]
        assert len(changed_files) == 1
        assert changed_files[0]["path"] == "app.txt"
        assert changed_files[0]["display_path"] == "app.txt"
        assert changed_files[0]["change_type"] == "modified"
        assert changed_files[0]["additions"] == 1
        assert changed_files[0]["deletions"] == 1

        diff = client.get(f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}")
        assert diff.status_code == 200
        payload = diff.json()
        assert payload["summary"]["path"] == "app.txt"
        assert payload["hunks"][0]["unified_lines"][0]["kind"] == "remove"
        assert payload["hunks"][0]["unified_lines"][0]["content"] == "hello"
        assert payload["hunks"][0]["unified_lines"][1]["kind"] == "add"
        assert payload["hunks"][0]["unified_lines"][1]["content"] == "review me"
        assert payload["hunks"][0]["rows"][0]["left"]["kind"] == "remove"
        assert payload["hunks"][0]["rows"][0]["right"]["kind"] == "add"


def test_api_rejects_changed_file_access_outside_human_verifying(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-diff-blocked-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-verify-diff-blocked-task")

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{completed.metadata.task_id}/changed-files/0")

    assert response.status_code == 409
    assert "only available during or after human verification" in response.json()["detail"]


def test_api_exposes_changed_files_for_done_tasks(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-done-diff-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-verify-done-diff-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200
        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")
        assert approve.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["changed_files"] == []
        assert detail.json()["changed_files_available"] is True
        changed_files = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true").json()["changed_files"]
        assert len(changed_files) == 1

        diff = client.get(f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}")
        assert diff.status_code == 200


def test_api_saves_human_review_note(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-note-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-note-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        save_note = client.put(
            f"/api/tasks/{completed.metadata.task_id}/human-review-note",
            json={"content": "## Note\nPlease keep the animation timing."},
        )
        assert save_note.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["human_review"]["note_markdown"] == "## Note\nPlease keep the animation timing."


def test_api_creates_line_comment_for_changed_file(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-line-comment-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-line-comment-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        changed_files = detail.json()["changed_files"]
        assert len(changed_files) == 1

        diff_before = client.get(f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}")
        assert diff_before.status_code == 200
        assert diff_before.json()["comments"] == []

        create_comment = client.post(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}/comments",
            json={
                "path": "app.txt",
                "side": "right",
                "line_number": 1,
                "line_kind": "add",
                "hunk_header": "@@ -1 +1 @@",
                "body": "Please keep this rename but adjust the copy.",
            },
        )
        assert create_comment.status_code == 200
        payload = create_comment.json()
        assert len(payload["comments"]) == 1
        assert payload["comments"][0]["anchor"]["path"] == "app.txt"
        assert payload["comments"][0]["anchor"]["side"] == "right"
        assert payload["comments"][0]["anchor"]["line_number"] == 1
        assert payload["comments"][0]["body_markdown"] == "Please keep this rename but adjust the copy."

        refreshed_detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert refreshed_detail.status_code == 200
        assert refreshed_detail.json()["human_review"]["total_comment_count"] == 1
        assert refreshed_detail.json()["human_review"]["unresolved_comment_count"] == 1


def test_api_deletes_line_comment_for_changed_file(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-line-comment-delete-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-line-comment-delete-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        changed_files = detail.json()["changed_files"]
        assert len(changed_files) == 1

        create_comment = client.post(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}/comments",
            json={
                "path": "app.txt",
                "side": "right",
                "line_number": 1,
                "line_kind": "add",
                "hunk_header": "@@ -1 +1 @@",
                "body": "Please keep this rename but adjust the copy.",
            },
        )
        assert create_comment.status_code == 200
        comment_id = create_comment.json()["comments"][0]["id"]

        delete_comment = client.delete(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{create_comment.json()['summary']['id']}/comments/{comment_id}"
        )
        assert delete_comment.status_code == 200
        assert delete_comment.json()["comments"] == []

        refreshed_detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert refreshed_detail.status_code == 200
        assert refreshed_detail.json()["human_review"]["total_comment_count"] == 0
        assert refreshed_detail.json()["human_review"]["unresolved_comment_count"] == 0


def test_api_blocks_approval_when_line_comments_remain(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-line-comment-approval-block-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-line-comment-approval-block-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        changed_files = detail.json()["changed_files"]
        assert len(changed_files) == 1

        create_comment = client.post(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}/comments",
            json={
                "path": "app.txt",
                "side": "right",
                "line_number": 1,
                "line_kind": "add",
                "hunk_header": "@@ -1 +1 @@",
                "body": "Please fix this before approval.",
            },
        )
        assert create_comment.status_code == 200

        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")
        assert approve.status_code == 409
        assert "approval is blocked until all inline comments are removed" in approve.json()["detail"]


def test_api_allows_approval_when_current_line_comments_are_resolved(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-resolved-comment-approval-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-review-resolved-comment-approval-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        changed_files = detail.json()["changed_files"]
        assert len(changed_files) == 1

        create_comment = client.post(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}/comments",
            json={
                "path": "app.txt",
                "side": "right",
                "line_number": 1,
                "line_kind": "add",
                "hunk_header": "@@ -1 +1 @@",
                "body": "Looks good after the fix.",
            },
        )
        assert create_comment.status_code == 200

        task = scanner.find_task(completed.metadata.task_id)
        comments_path = task.task_dir / (task.metadata.human_verification.comments_path or "HUMAN-VERIFY-001.comments.json")
        payload = json.loads(comments_path.read_text())
        payload["comments"][0]["resolved"] = True
        comments_path.write_text(json.dumps(payload, indent=2) + "\n")

        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")

    assert approve.status_code == 200


def test_api_blocks_approval_when_review_note_exists(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-note-approval-block-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-note-approval-block-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        save_note = client.put(
            f"/api/tasks/{completed.metadata.task_id}/human-review-note",
            json={"content": "Please revisit the edge case handling."},
        )
        assert save_note.status_code == 200

        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")

    assert approve.status_code == 409
    assert "approval is blocked until the review note is cleared" in approve.json()["detail"]


def test_api_hides_changed_files_when_patch_path_is_outside_managed_runs_root(configured_paths, tmp_path):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-bad-patch-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-bad-patch-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

    task = scanner.find_task(completed.metadata.task_id)
    task.metadata.integration.patch_path = str((tmp_path / "outside.patch").resolve())
    scanner.metadata_store.save(task.task_dir, task.metadata)

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        changed = client.get(f"/api/tasks/{completed.metadata.task_id}/changed-files/0")

    assert detail.status_code == 200
    assert detail.json()["changed_files"] == []
    assert detail.json()["changed_files_available"] is False
    assert changed.status_code == 409
    assert "outside the managed runs root" in changed.json()["detail"]


def test_api_orders_markdown_artifacts_by_lifecycle_and_cycle(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "artifact-order-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    (task.task_dir / "PLAN.md").write_text("plan\n")
    (task.task_dir / "WORK-002.md").write_text("work 2\n")
    (task.task_dir / "REVIEW-002.md").write_text("review 2\n")
    (task.task_dir / "HUMAN-VERIFY-002.md").write_text("verify 2\n")
    (task.task_dir / "WORK-001.md").write_text("work 1\n")
    (task.task_dir / "REVIEW-001.md").write_text("review 1\n")
    (task.task_dir / "HUMAN-VERIFY-001.md").write_text("verify 1\n")
    (task.task_dir / "NOTES.md").write_text("notes\n")
    (task.task_dir / "COMMIT.md").write_text("commit\n")

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{task.metadata.task_id}")

    assert detail.status_code == 200
    assert detail.json()["markdown_files"] == [
        "REQUEST.md",
        "PLAN.md",
        "WORK-001.md",
        "REVIEW-001.md",
        "HUMAN-VERIFY-001.md",
        "WORK-002.md",
        "REVIEW-002.md",
        "HUMAN-VERIFY-002.md",
        "NOTES.md",
        "COMMIT.md",
    ]


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


def test_api_deletes_active_task_state(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    task_dir = config.state_dir(TaskState.HUMAN_VERIFYING) / "delete-blocked-task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# blocked task\n")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]

    with TestClient(app) as client:
        response = client.delete(f"/api/tasks/{task.metadata.task_id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "task_id": task.metadata.task_id}


def test_api_creates_request_from_dashboard_form(configured_paths, tmp_path):
    config, _, _ = configured_paths
    config.runtime.language = "KO"
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
        assert "## 목표" in request_markdown
        assert "## 승인 기준" in request_markdown
        assert f"repo_root: {target_repo.resolve()}" in request_markdown
        assert "base_branch: develop" in request_markdown
        assert "language: ko" in request_markdown


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
    local_config_path = tmp_path / "config.local.yaml"
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
        assert get_response.json()["language"] == "EN"
        assert get_response.json()["theme"] == "light"
        assert get_response.json()["coding_assistant"] == "opencode"
        assert get_response.json()["available_assistants"] == [
            {"value": "opencode", "label": "OpenCode"},
            {"value": "codex", "label": "Codex CLI"},
        ]
        assert get_response.json()["planner_model"] is None
        assert get_response.json()["planner_session_token_budget"] == 250
        assert get_response.json()["planner_agent_count"] == 1
        assert get_response.json()["implementer_session_token_budget"] == 250
        assert get_response.json()["implementer_agent_count"] == 1
        assert get_response.json()["reviewer_session_token_budget"] == 250
        assert get_response.json()["reviewer_agent_count"] == 1
        assert get_response.json()["commit_session_token_budget"] == 250
        assert get_response.json()["repo_discovery_root"] == str(config.repo_discovery.root)
        assert get_response.json()["repo_discovery_max_depth"] == config.repo_discovery.max_depth
        assert get_response.json()["config_path"] == str(local_config_path.resolve())
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
                "language": "KO",
                "coding_assistant": "opencode",
                "planner_model": "gpt-5",
                "planner_session_token_budget": 210,
                "planner_agent_count": 2,
                "implementer_model": " o3-mini ",
                "implementer_session_token_budget": 230,
                "implementer_agent_count": 3,
                "reviewer_model": "",
                "reviewer_session_token_budget": 190,
                "reviewer_agent_count": 4,
                "commit_model": "gpt-5",
                "commit_session_token_budget": 250,
                "repo_discovery_root": "../",
                "repo_discovery_max_depth": 4,
            },
        )

    assert put_response.status_code == 200
    payload = put_response.json()
    assert payload["saved"] is True
    assert payload["language"] == "KO"
    assert payload["coding_assistant"] == "opencode"
    assert payload["planner_model"] == "gpt-5"
    assert payload["planner_session_token_budget"] == 210
    assert payload["planner_agent_count"] == 2
    assert payload["implementer_model"] == "o3-mini"
    assert payload["implementer_session_token_budget"] == 230
    assert payload["implementer_agent_count"] == 3
    assert payload["reviewer_model"] is None
    assert payload["reviewer_session_token_budget"] == 190
    assert payload["reviewer_agent_count"] == 4
    assert payload["commit_model"] == "gpt-5"
    assert payload["commit_session_token_budget"] == 250
    assert payload["repo_discovery_root"] == "../"
    assert payload["repo_discovery_max_depth"] == 4
    assert app.state.runtime.config.opencode.planner_model == "gpt-5"
    assert app.state.runtime.config.runtime.language == "KO"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.opencode.planner_session_token_budget == 210000
    assert app.state.runtime.config.runtime.planner_agent_count == 2
    assert app.state.runtime.config.opencode.implementer_model == "o3-mini"
    assert app.state.runtime.config.opencode.implementer_session_token_budget == 230000
    assert app.state.runtime.config.runtime.implementer_agent_count == 3
    assert app.state.runtime.config.opencode.reviewer_model is None
    assert app.state.runtime.config.opencode.reviewer_session_token_budget == 190000
    assert app.state.runtime.config.runtime.reviewer_agent_count == 4
    assert app.state.runtime.config.repo_discovery.root == "../"
    assert app.state.runtime.config.repo_discovery.max_depth == 4
    assert load_config(config_path).opencode.commit_model == "gpt-5"
    assert load_config(config_path).opencode.commit_session_token_budget == 250000
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
    planning.metadata.lease.owner = "planner"
    planning.metadata.lease.run_id = "planner-run-1"
    planning.metadata.lease.heartbeat_at = utc_now()
    planning.metadata.target.repo_root = str((config.repo_root / "nested" / "demo-repo").resolve())
    planning.metadata.target.base_branch = "feature/card-layout"
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    board_snapshot = scanner.board_snapshot()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{planning.metadata.task_id}")

    planning_column = next(column for column in board_snapshot.model_dump(mode="json")["columns"] if column["state"] == TaskState.PLANNING.value)
    assert planning_column["items"][0]["active_model"] == "openai/gpt-5.4"
    assert planning_column["items"][0]["agent_status"] == "active"
    assert planning_column["items"][0]["agent_owner"] == "planner"
    assert planning_column["items"][0]["target_repo_label"] == "demo-repo"
    assert planning_column["items"][0]["base_branch"] == "feature/card-layout"
    assert planning_column["items"][0]["total_duration_ms"] >= 0
    assert detail.status_code == 200
    assert detail.json()["metadata"]["plan"]["resolved_model"] == "openai/gpt-5.4"
    assert detail.json()["metadata"]["implementation"]["resolved_model"] == "github-copilot/gpt-5"
    assert detail.json()["metadata"]["review"]["resolved_model"] is None
    assert detail.json()["agent_status"] == "active"


def test_api_task_detail_marks_active_state_without_lease_as_waiting(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "waiting-activity-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(scanner.find_task(waiting.metadata.task_id), TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.lease.heartbeat_at = utc_now()
    metadata_store.save(implementing.task_dir, implementing.metadata)
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{implementing.metadata.task_id}")

    assert detail.status_code == 200
    assert detail.json()["agent_status"] == "waiting"


def test_api_exposes_stage_timing_summary_and_segments(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "stage-timing-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    staged_task = scanner.find_task(waiting.metadata.task_id)
    now = datetime.now(timezone.utc)
    staged_task.metadata.history = [
        HistoryEntry(state=TaskState.REQUESTS, entered_at=now - timedelta(minutes=12), by="human"),
        HistoryEntry(state=TaskState.PLANNING, entered_at=now - timedelta(minutes=10), by="planner"),
        HistoryEntry(state=TaskState.WAITING_CHECK_PLANS, entered_at=now - timedelta(minutes=7), by="planner"),
        HistoryEntry(state=TaskState.TODOS, entered_at=now - timedelta(minutes=3), by="human"),
    ]
    metadata_store.save(staged_task.task_dir, staged_task.metadata)

    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{staged_task.metadata.task_id}")

    assert response.status_code == 200
    payload = response.json()
    stage_timing = payload["stage_timing"]
    assert len(stage_timing["summaries"]) == 10
    assert len(stage_timing["segments"]) == 4
    assert stage_timing["total_duration_ms"] >= 720000
    assert stage_timing["ai_work_duration_ms"] == 180000
    assert stage_timing["human_work_duration_ms"] == 0
    assert stage_timing["waiting_duration_ms"] >= 540000
    requests_summary = next(item for item in stage_timing["summaries"] if item["state"] == TaskState.REQUESTS.value)
    planning_summary = next(item for item in stage_timing["summaries"] if item["state"] == TaskState.PLANNING.value)
    waiting_summary = next(item for item in stage_timing["summaries"] if item["state"] == TaskState.WAITING_CHECK_PLANS.value)
    todos_summary = next(item for item in stage_timing["summaries"] if item["state"] == TaskState.TODOS.value)
    assert requests_summary["total_duration_ms"] == 120000
    assert planning_summary["total_duration_ms"] == 180000
    assert waiting_summary["total_duration_ms"] == 240000
    assert todos_summary["attempt_count"] == 1
    assert todos_summary["is_current"] is True
    assert todos_summary["latest_entered_at"] is not None
    assert todos_summary["total_duration_ms"] >= 180000
    assert stage_timing["segments"][1]["state"] == TaskState.PLANNING.value
    assert stage_timing["segments"][1]["visit_index"] == 1
    assert stage_timing["segments"][1]["duration_ms"] == 180000
    assert stage_timing["segments"][3]["state"] == TaskState.TODOS.value
    assert stage_timing["segments"][3]["is_current"] is True


def test_api_persists_model_settings_to_default_local_config_when_unloaded(configured_paths, tmp_path):
    config, _, _ = configured_paths
    default_base_path = tmp_path / "config.yaml"
    default_local_path = tmp_path / "config.local.yaml"
    default_base_path.write_text("opencode:\n  planner_model: base-planner\n")
    original_default_config_path = config_module.DEFAULT_CONFIG_PATH
    original_default_local_path = config_module.DEFAULT_LOCAL_CONFIG_PATH
    config_module.DEFAULT_CONFIG_PATH = default_base_path
    config_module.DEFAULT_LOCAL_CONFIG_PATH = default_local_path
    try:
        app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

        with TestClient(app) as client:
            response = client.put(
                "/api/settings/models",
                json={
                "planner_model": "planner-x",
                "language": "KO",
                "theme": "dark",
                "coding_assistant": "opencode",
                "planner_session_token_budget": 180,
                "planner_agent_count": 2,
                "implementer_model": None,
                "implementer_session_token_budget": 250,
                "implementer_agent_count": 1,
                "reviewer_model": "reviewer-y",
                "reviewer_session_token_budget": 220,
                "reviewer_agent_count": 3,
                "commit_model": None,
                "commit_session_token_budget": 250,
                "repo_discovery_root": "/tmp/scan-root",
                "repo_discovery_max_depth": 3,
            },
        )

        assert response.status_code == 200
        assert default_local_path.exists()
        persisted = load_config(default_base_path)
        assert persisted.opencode.planner_model == "planner-x"
        assert persisted.runtime.language == "KO"
        assert persisted.runtime.theme == "dark"
        assert persisted.runtime.coding_assistant == "opencode"
        assert persisted.opencode.planner_session_token_budget == 180000
        assert persisted.runtime.planner_agent_count == 2
        assert persisted.opencode.reviewer_model == "reviewer-y"
        assert persisted.opencode.reviewer_session_token_budget == 220000
        assert persisted.runtime.reviewer_agent_count == 3
        assert persisted.repo_discovery.root == "/tmp/scan-root"
        assert persisted.repo_discovery.max_depth == 3
        assert response.json()["config_path"] == str(default_local_path.resolve())
    finally:
        config_module.DEFAULT_CONFIG_PATH = original_default_config_path
        config_module.DEFAULT_LOCAL_CONFIG_PATH = original_default_local_path


def test_api_preserves_repo_discovery_root_when_put_payload_omits_it(configured_paths):
    config, _, _ = configured_paths
    config.repo_discovery.root = "../custom-root"
    config.runtime.planner_agent_count = 5
    config.runtime.language = "KO"
    config.runtime.theme = "dark"
    config.runtime.coding_assistant = "opencode"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "planner_model": "planner-x",
                "theme": "dark",
                "coding_assistant": "opencode",
                "planner_session_token_budget": 260,
                "implementer_agent_count": 2,
                "implementer_model": None,
                "implementer_session_token_budget": 250,
                "reviewer_model": None,
                "reviewer_session_token_budget": 250,
                "commit_model": None,
                "commit_session_token_budget": 250,
                "repo_discovery_max_depth": 5,
            },
        )

    assert response.status_code == 200
    assert response.json()["language"] == "KO"
    assert response.json()["theme"] == "dark"
    assert response.json()["coding_assistant"] == "opencode"
    assert response.json()["repo_discovery_root"] == "../custom-root"
    assert response.json()["repo_discovery_max_depth"] == 5
    assert response.json()["planner_agent_count"] == 5
    assert response.json()["implementer_agent_count"] == 2
    assert app.state.runtime.config.repo_discovery.root == "../custom-root"
    assert app.state.runtime.config.runtime.language == "KO"
    assert app.state.runtime.config.runtime.theme == "dark"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.runtime.planner_agent_count == 5
    assert app.state.runtime.config.runtime.implementer_agent_count == 2


def test_api_does_not_mutate_live_runtime_settings_when_persist_fails(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.opencode.planner_model = "stable-planner"
    config.runtime.planner_agent_count = 3
    config.runtime.language = "EN"
    config.runtime.theme = "light"
    config.runtime.coding_assistant = "opencode"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    def fail_persist(self, path=None):
        raise OSError("disk full")

    monkeypatch.setattr(config_module.AppConfig, "persist", fail_persist)

    with TestClient(app) as client:
        with pytest.raises(OSError, match="disk full"):
            client.put(
                "/api/settings/models",
                json={
                    "planner_model": "new-planner",
                    "language": "KO",
                    "theme": "dark",
                    "coding_assistant": "opencode",
                    "planner_session_token_budget": 250,
                    "planner_agent_count": 7,
                    "implementer_model": None,
                    "implementer_session_token_budget": 250,
                    "reviewer_model": None,
                    "reviewer_session_token_budget": 250,
                    "commit_model": None,
                    "commit_session_token_budget": 250,
                },
            )

    assert app.state.runtime.config.opencode.planner_model == "stable-planner"
    assert app.state.runtime.config.runtime.language == "EN"
    assert app.state.runtime.config.runtime.theme == "light"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.runtime.planner_agent_count == 3


def test_api_rejects_invalid_runtime_language(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "language": "JP",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 422
    assert config.runtime.language == "EN"


def test_api_accepts_codex_runtime_coding_assistant(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "codex",
                "planner_model": "gpt-5.4",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 200
    assert config.runtime.coding_assistant == "codex"
    assert config.codex.planner_model == "gpt-5.4"


def test_api_rejects_unknown_opencode_model_on_save(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"], discovery_responses=[["openai/gpt-5.4"]]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "opencode",
                "planner_model": "not-a-real-model",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "planner_model must be one of the discovered models"


def test_api_rejects_unknown_codex_model_on_save(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "codex",
                "planner_model": "not-a-real-model",
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "planner_model must be one of the discovered models"


def test_api_refresh_can_preview_codex_models_without_switching_runtime(configured_paths):
    config, _, _ = configured_paths
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/settings/models?refresh=true&assistant=codex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "codex"
    assert "gpt-5.4" in payload["available_models"]
    assert payload["planner_model"] == config.codex.planner_model
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"


def test_api_settings_without_assistant_query_returns_persisted_backend(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "codex"
    config.codex.planner_model = "gpt-5.4"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/api/settings/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "codex"
    assert payload["planner_model"] == "gpt-5.4"


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
                "planner_session_token_budget": 250,
                "implementer_model": "openai/gpt-5.4-mini",
                "implementer_session_token_budget": 250,
                "reviewer_model": "github-copilot/gpt-5",
                "reviewer_session_token_budget": 250,
                "commit_model": None,
                "commit_session_token_budget": 250,
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
                "planner_session_token_budget": 250,
                "implementer_model": None,
                "implementer_session_token_budget": 250,
                "reviewer_model": None,
                "reviewer_session_token_budget": 250,
                "commit_model": None,
                "commit_session_token_budget": 250,
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
    assert TEMPLATE_PATH.exists()
    assert "__DEFAULT_TARGET_REPO__" not in response.text
    assert "__DEFAULT_BASE_BRANCH__" not in response.text
    assert "__INITIAL_RUNTIME_LANGUAGE__" not in response.text
    assert "__INITIAL_RUNTIME_THEME__" not in response.text
    assert 'data-theme="light"' in response.text
    assert 'id="board-phase-tabs"' in response.text
    assert 'data-board-phase="plan"' in response.text
    assert 'data-board-phase="implementation"' in response.text
    assert 'data-board-phase="final"' in response.text
    assert "Settings" in response.text
    assert "Refresh" in response.text
    assert "Acceptance criteria" in response.text
    assert "JSON files" in response.text
    assert "/api/requests" in response.text
    assert "/api/settings/models" in response.text
    assert "target-repo-options" in response.text
    assert "base-branch-options" in response.text
    assert "request-modal" in response.text
    assert "settings-modal" in response.text
    assert "request-copy-title" in response.text
    assert "request-basics-heading" in response.text
    assert "request-repo-heading" in response.text
    assert 'class="field span-full"' in response.text
    assert "task-modal" in response.text
    assert "retrospective-modal" in response.text
    assert "task-modal-panel" in response.text
    assert "retrospective-modal-title" in response.text
    assert "retrospective-modal-subtitle" not in response.text
    assert "retrospective-context-row" in response.text
    assert ".retrospective-context-row { margin-top: 0; margin-bottom: 10px; }" in response.text
    assert "retrospective-view-title" in response.text
    assert "retrospective-action-title" in response.text
    assert "retrospective-compare-branch" in response.text
    assert "retrospective-compare-options" in response.text
    assert "retrospective-create-target" in response.text
    assert "retrospective-create-branch" in response.text
    assert 'class="retrospective-status approval-choice-status"' in response.text
    assert 'class="retrospective-action-buttons"' in response.text
    assert "Work log" in response.text
    assert "Viewer mode" in response.text
    assert "Changed files" in response.text
    assert "task-tab-changed-files" in response.text
    assert "task-panel-changed-files" in response.text
    assert "Read-only patch view" in response.text
    assert "Readable log" not in response.text
    assert "Debug log" not in response.text
    assert "Agent activity" in response.text
    assert "task-activity-shell" in response.text
    assert "planner_model" in response.text
    assert "runtime_language" in response.text
    assert "runtime_theme" in response.text
    assert "runtime_coding_assistant" in response.text
    assert "function applyRuntimeTheme(theme)" in response.text
    assert "applyRuntimeTheme(initialRuntimeTheme);" in response.text
    assert "const settingsTranslations = {" in response.text
    assert "applyRuntimeSettingsTranslations();" in response.text
    assert "const taskTranslations = {" in response.text
    assert "applyTaskTranslations();" in response.text
    assert "runtimeLanguageInput.addEventListener('change', () => { applyRuntimeSettingsTranslations(); applyRequestTranslations(); applyHumanReviewTranslations(); applyTaskTranslations(); if (activeTaskDetail) renderTaskOverview(activeTaskDetail); refreshRequestDerivedText(); });" in response.text
    assert 'class="settings-sections"' in response.text
    assert 'id="settings-basics-heading"' in response.text
    assert 'id="settings-agents-heading"' in response.text
    assert 'class="settings-role-inline"' in response.text
    assert 'class="settings-role-inline settings-role-inline-commit"' in response.text
    assert "Agent" in response.text
    assert 'id="close-settings"' not in response.text
    assert 'id="settings-config-path"' not in response.text
    assert "planner_session_token_budget" in response.text
    assert "planner_agent_count" in response.text
    assert "implementer_model" in response.text
    assert "implementer_session_token_budget" in response.text
    assert "implementer_agent_count" in response.text
    assert "reviewer_model" in response.text
    assert "reviewer_session_token_budget" in response.text
    assert "reviewer_agent_count" in response.text
    assert "commit_model" in response.text
    assert "commit_session_token_budget" in response.text
    assert "repo_discovery_root" in response.text
    assert "repo_discovery_max_depth" in response.text
    assert "readNumericSettingInput" in response.text
    assert "assistant-model-options" in response.text
    assert "Refresh discovered models" in response.text
    assert "Save settings" in response.text
    assert "window.location.reload();" in response.text
    assert "Repo discovery root" in response.text
    assert "Repo discovery depth" in response.text
    assert "models loaded ·" in response.text
    assert "task-viewer-host" in response.text
    assert "Approve plan" in response.text
    assert "toastui-editor" in response.text
    assert "buildScopeDefaults" in response.text
    assert "buildOutOfScopeDefaults" in response.text
    assert "const requestTranslations = {" in response.text
    assert "const humanReviewTranslations = {" in response.text
    assert "applyRequestTranslations();" in response.text
    assert "['waiting-check-plans', 'completed-reviews', 'human-verifying', 'done'].includes(metadata?.state) && files.includes('PLAN.md')" in response.text
    assert "task-human-review-panel" in response.text
    assert "save-human-review-note" in response.text
    assert "request-changes-button" in response.text
    assert "approve-human-review-button" in response.text
    assert "/api/tasks/${activeTaskId}/human-review-note" in response.text
    assert "/api/retrospectives/inspect" in response.text
    assert "/api/retrospectives/create" in response.text
    assert 'id="task-tab-review-note"' in response.text
    assert 'id="task-panel-review-note"' in response.text
    assert 'class="diff-grid"' in response.text
    assert 'class="diff-row"' in response.text
    assert 'class="diff-cell ${line.kind}"' in response.text
    assert 'class="diff-unified"' in response.text
    assert "translateRequest('validationGoal')" in response.text
    assert response.text.index('id="title"') < response.text.index('id="target_repo"') < response.text.index('id="base_branch"') < response.text.index('id="background"') < response.text.index('id="goal"')
    assert response.text.index('id="constraints"') < response.text.index('id="acceptance_criteria"') < response.text.index('id="scope"') < response.text.index('id="out_of_scope"') < response.text.index('id="references"')
    assert "fs-kanban-agent.last-target-repo" in response.text
    assert "window.localStorage.setItem(lastTargetRepoStorageKey, normalized)" in response.text
    assert "applyTargetRepoAutofill(currentTargetRepoOptions())" in response.text
    assert "resetFormState(); setModalOpen(true); await loadTargetRepoBranches();" in response.text
    assert "function applyBoardSnapshot(data)" in response.text
    assert "source.addEventListener('board_snapshot', (event) => {" in response.text
    assert "applyBoardSnapshot(message.payload);" in response.text
    assert "function phaseLabel(phase)" in response.text
    assert "function repoTagTone(path)" in response.text
    assert "function renderFinalBoard(columns)" in response.text
    assert "function renderFinalProjectColumn(projectPath, items)" in response.text
    assert "function groupFinalItemsByTargetBranch(items)" in response.text
    assert "board.innerHTML = renderFinalBoard(visibleColumns);" in response.text
    assert "card-meta-row" in response.text
    assert "card-tag-row" in response.text
    assert "card-runtime-meta" in response.text
    assert "const runtimeValue = renderCardRuntime(item);" in response.text
    assert "if (item.agent_status !== 'active') return '';" in response.text
    assert "card-tag-id" in response.text
    assert "card-repo-icon" in response.text
    assert "card-tag-branch" in response.text
    assert "card-branch-icon" in response.text
    assert "title=\"${escapeHtml(title)}\"" in response.text
    assert "function taskIdIconSvg(className = 'card-task-id-icon')" in response.text
    assert "renderTag('', item.task_id || '', 'card-tag-id', '', item.task_id || '', taskIdIconSvg())" in response.text
    assert "renderTag('', repoLabel, 'card-tag-repo', repoStyle, repoPath || repoLabel, repoIconSvg('card-repo-icon'))" in response.text
    assert "renderTag('', branchLabel, 'card-tag-branch', '', branchLabel, branchIconSvg('card-branch-icon'))" in response.text
    assert "renderTag('', finalBranchLabel, 'card-tag-branch card-tag-final-branch'" in response.text
    assert "function renderTaskCard(item, options = {})" in response.text
    assert "renderTaskCard(item, { compactFinal: true })" in response.text
    assert 'id="task-modal-subtitle" class="card-tag-row task-modal-tag-row"' in response.text
    assert ".task-modal-tag-row .card-tag { font-size: 0.73rem; }" in response.text
    assert "function renderTaskSubtitleTags(task)" in response.text
    assert "renderTag('', task.task_id || '', 'card-tag-id', '', task.task_id || '', taskIdIconSvg())" in response.text
    assert "document.getElementById('task-modal-subtitle').innerHTML = renderTaskSubtitleTags(snapshot);" in response.text
    assert "document.getElementById('task-modal-subtitle').innerHTML = renderTaskSubtitleTags({" in response.text
    assert "const activeSince = item.state_entered_at || '';" in response.text
    assert 'buildDurationAttributes(0, activeSince)' in response.text
    assert 'class="card-meta card-runtime-meta">${renderCardActivity(item)}${runtimeValue}' in response.text
    assert "iter ${item.iteration}" not in response.text
    assert "const boardPhaseStates = {" in response.text
    assert "const boardPhasePriorityRules = [" in response.text
    assert "function selectDefaultBoardPhase(columns)" in response.text
    assert "if (!boardPhaseManuallySelected) {" in response.text
    assert "#board.final-board { display: flex;" in response.text
    assert ".final-board .column { flex: 1 1 320px; min-width: 320px; }" in response.text
    assert ".final-project-column { border-top: 6px solid var(--repo-accent, var(--accent));" in response.text
    assert "final-project-title" in response.text
    assert "final-project-path" not in response.text
    assert "final-project-branches" in response.text
    assert "target-branch-group" in response.text
    assert "target-branch-label" in response.text
    assert "target-branch-icon" in response.text
    assert ".final-board .column-cards { overflow-x: hidden; }" in response.text
    assert ".final-board .card { min-width: 0; max-width: 100%; overflow: hidden; }" in response.text
    assert ".final-board .card-button { display: block; min-width: 0; max-width: 100%; padding-right: 0; }" in response.text
    assert ".final-board .card-meta-row { min-width: 0; max-width: 100%; }" in response.text
    assert ".final-board .card-tag-row { min-width: 0; max-width: 100%; overflow: hidden; }" in response.text
    assert ".final-board .card-tag-final-branch { display: inline-flex; flex: 0 1 auto; min-width: 0; max-width: 100%; box-sizing: border-box; overflow: hidden; }" in response.text
    assert ".final-board .card-tag-final-branch .card-tag-value { display: block; flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in response.text
    assert '.target-branch-group[data-expanded="false"] .column-cards { display: none; }' in response.text
    assert 'class="target-branch-retrospective"' in response.text
    assert "retrospectiveCountLabel: '{count} retrospectives'" in response.text
    assert "retrospectiveCountLabel: '{count}건 회고'" in response.text
    assert "retrospectiveModalSubtitle" not in response.text
    assert "${escapeHtml(translateTask('retrospectiveCountLabel', { count: String(branchItems.length) }))}</button>" in response.text
    assert "data-target-repo=\"${escapeHtml(branchItems[0].target_repo_root || '')}\"" in response.text
    assert 'data-base-branch="${escapeHtml(branch)}"' in response.text
    assert "function openRetrospectiveModal(targetRepoRoot, baseBranch)" in response.text
    assert "function createRetrospective(completionMode)" in response.text
    assert "function renderRetrospectiveContextTags(record = null)" in response.text
    assert "function setRetrospectiveMode(mode, record = null)" in response.text
    assert "function normalizedRetrospectiveComparisonBranch()" in response.text
    assert "function loadRetrospectiveCompareBranchOptions(targetRepoRoot, baseBranch)" in response.text
    assert "retrospectiveUnavailable" in response.text
    assert "payload.created" in response.text
    assert "retrospectiveViewTitle: 'Retrospective content'" in response.text
    assert "retrospectiveActionTitle: 'Commit destination'" in response.text
    assert "retrospectiveCompareLabel: 'Comparison branch'" in response.text
    assert "renderTag('', repoLabel, 'card-tag-repo', repoStyle, repoPath || repoLabel, repoIconSvg('card-repo-icon'))" in response.text
    assert "retrospectiveBranchDescription" not in response.text
    assert "retrospectiveTargetDescription" not in response.text
    assert "retrospectiveProjectLabel" not in response.text
    assert "retrospectiveBranchLabel" not in response.text
    assert "setRetrospectiveMode(record?.exists ? 'view' : 'choice', record || null);" in response.text
    assert "setRetrospectiveMode('choice', {" in response.text
    assert "retrospectiveViewShell.style.display = showChoice ? 'none' : 'grid';" in response.text
    assert "retrospectiveStatus.hidden = true;" in response.text
    assert "retrospectiveCreateTargetButton.hidden = false;" in response.text
    assert "retrospectiveCreateBranchButton.hidden = false;" in response.text
    assert "comparison_branch: normalizedRetrospectiveComparisonBranch() || null" in response.text
    assert "retrospectiveCompareBranchInput.addEventListener('input'" in response.text
    assert 'class="target-branch-label" title="${escapeHtml(branch)}" tabindex="0" role="button" aria-expanded="${index === 0 ? ' in response.text
    assert '.target-branch-caret { flex: 0 0 auto; width: 20px; height: 20px; margin-left: 2px;' in response.text
    assert ".final-board .card-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in response.text
    assert ".final-board .card-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in response.text
    assert "function toggleFinalBranchGroup(branchLabel)" in response.text
    assert "const branchLabel = event.target.closest('.target-branch-label');" in response.text
    assert "toggleFinalBranchGroup(branchLabel);" in response.text
    assert "const branch = item.base_branch || 'unknown';" in response.text
    assert "title=\"${escapeHtml(projectPath)}\"" in response.text
    assert "/api/target-repo-branches?target_repo=${encodeURIComponent(repoPath)}" in response.text
    assert ".stage-timing-grid { display: grid; gap: 10px; }" in response.text
    assert ".stage-timing-row { display: grid; gap: 10px; grid-template-columns: repeat(var(--stage-columns, 1), minmax(0, 1fr)); }" in response.text
    assert ".stage-timing-card.upcoming { opacity: 0.48; background: rgba(255,255,255,0.62); }" in response.text
    assert "const summaries = Array.isArray(stageTiming?.summaries) ? stageTiming.summaries.filter((summary) => summary.state !== 'done') : [];" in response.text
    assert "const segments = Array.isArray(stageTiming?.segments) ? stageTiming.segments : [];" in response.text
    assert "function formatStageVisitLabel(segment)" in response.text
    assert "function formatStageSegmentEnd(segment)" in response.text
    assert "translateTask('completedLabel')" in response.text
    assert "const stageTimingRows = [" in response.text
    assert "['requests', 'planning', 'waiting-check-plans']" in response.text
    assert "['todos', 'implementing', 'waiting-reviews']" in response.text
    assert "['reviewing', 'completed-reviews', 'human-verifying']" in response.text
    assert "const summaryMap = new Map(summaries.map((summary) => [summary.state, summary]));" in response.text
    assert "const visitedStates = new Set([" in response.text
    assert "const hiddenDurationMs = Array.isArray(stageTiming?.segments)" in response.text
    assert "translateTask('timelineFromHistory')" not in response.text
    assert "translateTask('timelineFromHistoryBody')" not in response.text
    assert "const currentSummaryIsLive = Boolean(currentSummary && currentSummary.state !== 'done');" in response.text
    assert "const summaryIsLive = summary.is_current && summary.state !== 'done';" in response.text
    assert "const cardStateClass = summary.is_current ? ' current' : reached ? ' reached' : ' upcoming';" in response.text
    assert "<div class=\"stage-timing-row\" style=\"--stage-columns:${states.length}\">${cards}</div>" in response.text
    assert "segment.is_current && segment.state !== 'done' ? 0 : Number(segment.duration_ms || 0)" in response.text
    assert "/api/tasks/${taskId}/logs" not in response.text
    assert "debug_rendered_content" not in response.text
    assert "(no debug metadata for this log yet)" not in response.text
    assert "(no readable log output for this file)" not in response.text
    assert "artifact-group-label" in response.text
    assert "task-artifact-subtabs" in response.text
    assert "function buildArtifactEntries(files)" in response.text
    assert "Implement&Review-${cycle}" in response.text
    assert "function renderArtifactSubtabs(entries)" in response.text
    assert "artifactDisplayLabel(file)" in response.text
    assert "addImageBlobHook" in response.text
    assert "/api/tasks/${activeTaskId}/attachments?artifact=PLAN.md" in response.text
    assert "callback(uploaded.relative_path, uploaded.filename);" in response.text
    assert "return false;" in response.text
    assert "function rewriteAttachmentPaths(markdown, taskId)" in response.text
    assert "async function refreshActiveTaskDetailAfterComment(taskId)" in response.text
    assert "await refreshActiveTaskDetailAfterComment(activeTaskId);" in response.text
    assert "function resetArtifactViewerScroll()" in response.text
    assert "requestAnimationFrame(resetArtifactViewerScroll);" in response.text
    assert "let boardTaskSnapshots = new Map();" in response.text
    assert "function setTaskTab(tab, { load = true } = {})" in response.text
    assert "function taskChromeState(state = '')" in response.text
    assert "hydrateTaskModalChrome(snapshot, { preserveTab });" in response.text
    assert "?include_changed_files=true" in response.text
    assert "showLogEntry(entries.findIndex((entry) => entry.name === activeLogName), false);" not in response.text
    assert "/api/tasks/${taskId}/changed-files/${encodeURIComponent(activeChangedFileId)}" in response.text
    assert "Final branch" in response.text
    assert "width: min(1380px, 100%)" in response.text
    assert "height: min(86vh, calc(100vh - 64px))" in response.text
    assert ".diff-desktop { font-size: 0.82rem; }" in response.text
    assert ".diff-mobile { font-size: 0.82rem; }" in response.text
    assert "loadTaskDetail(button.dataset.taskId, false, { snapshot: boardTaskSnapshots.get(button.dataset.taskId) || null });" in response.text
    assert "worker_log" in response.text
    assert 'id="task-tab-logs"' not in response.text
    assert 'id="task-panel-logs"' not in response.text
    assert "loadTaskLogs(activeTaskId, true)" not in response.text
    assert "maybeStartLogPolling" not in response.text
    assert "setInterval(() => {" not in response.text
    assert "let activeTaskRequestToken = 0;" in response.text
    assert "let activeArtifactRequestToken = 0;" in response.text
    assert "function scheduleActiveTaskRefresh()" in response.text
    assert "if (requestToken !== activeTaskRequestToken || activeTaskId !== taskId) return;" in response.text
    assert "encodeURIComponent(activeArtifactName)" in response.text
    assert "if (requestToken !== activeArtifactRequestToken || taskId !== activeTaskId || activeArtifactName !== resolvedArtifactName) return;" in response.text
    assert "translateTask('stalePlanMessage')" in response.text
    assert "activeBoardPhase = 'implementation';" in response.text
    assert "boardPhaseManuallySelected = true;" in response.text
    assert "source.addEventListener('board_snapshot', (event) => {" in response.text
    assert "scheduleActiveTaskRefresh();" in response.text
    assert "data-active-since" in response.text
    assert "renderRunningMeta(item)" not in response.text
    assert "running 00:00:00" not in response.text
    assert 'class="card-activity"' in response.text
    assert "aria-label=\"${escapeHtml(label)}\"" in response.text
    assert "/api/tasks/${activeTaskId}/approve-plan" in response.text
    assert "/api/tasks/${activeTaskId}/start-verification" in response.text
    assert "/api/tasks/${activeTaskId}/reject-verification" in response.text
    assert "/api/tasks/${activeTaskId}/approve-verification" in response.text
    assert 'id="approval-choice-modal"' in response.text
    assert 'id="approval-choice-target-button"' in response.text
    assert 'id="approval-choice-new-branch-button"' in response.text
    assert "function openApprovalChoiceModal()" in response.text
    assert "approveVerification('target-branch');" in response.text
    assert "approveVerification('new-branch');" in response.text
    assert "body: JSON.stringify({ completion_mode: completionMode || 'new-branch' })" in response.text
    assert "function setApprovalChoiceModalOpen(isOpen, { force = false } = {})" in response.text
    assert "setApprovalChoiceModalOpen(false, { force: true });" in response.text
    assert "Approve" in response.text
    assert "Request changes" in response.text
    assert "Agent active" in response.text
    assert "Agent waiting" in response.text
    assert "Agent idle" in response.text
    assert "Stage timing" in response.text
    assert "stage-timing-grid" in response.text
    assert "stage-timing-breakdown-item" in response.text
    assert "stage-timing-breakdown-separator" in response.text
    assert "translateTask('trackedSuffix')" in response.text
    assert "renderStageTiming(stageTiming)" in response.text
    assert "${renderStageTiming(detail.stage_timing)}" in response.text
    assert "function latestVisibleError(errors)" in response.text
    assert "item.code !== 'human-verification-rejected'" in response.text
    assert 'id="task-tab-timeline"' not in response.text
    assert 'id="task-panel-timeline"' not in response.text
    assert "setTaskTab('timeline')" not in response.text
    assert "const taskTabTimeline = document.getElementById('task-tab-timeline');" not in response.text
    assert "Delete task" in response.text
    assert "This stops any running task work and removes managed artifacts" in response.text
    assert "method: 'DELETE'" in response.text
    assert 'const defaultTargetRepo = "";' in response.text


def test_dashboard_page_includes_korean_runtime_settings_translations(configured_paths):
    config, _, _ = configured_paths
    config.runtime.language = "KO"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'const initialRuntimeLanguage = "KO";' in response.text
    assert "설정" in response.text
    assert "기본값" in response.text
    assert "런타임 역할" in response.text
    assert "어시스턴트" in response.text
    assert "에이전트" in response.text
    assert "요청 생성" in response.text
    assert "새로고침" in response.text
    assert "요청 기본값" in response.text
    assert "저장소 범위" in response.text
    assert "플랜 단계" in response.text
    assert "구현 단계" in response.text
    assert "최종 완료" in response.text
    assert "작업 상세" in response.text
    assert "대상 프로젝트" in response.text
    assert "작업 내역" in response.text
    assert "변경 파일" in response.text
    assert "요구사항" in response.text
    assert "계획 작성중" in response.text
    assert "계획 승인 대기" in response.text
    assert "구현 대기" in response.text
    assert "구현중" in response.text
    assert "리뷰 대기중" in response.text
    assert "리뷰중" in response.text
    assert "리뷰 완료" in response.text
    assert "인간 리뷰중" in response.text
    assert "완료" in response.text
    assert "발견된 모델 새로고침" in response.text
    assert "저장" in response.text


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
