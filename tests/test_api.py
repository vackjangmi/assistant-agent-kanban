from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import subprocess
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.api.routes import _resolve_settings_snapshots
from assistant_agent_kanban.api.ui import TEMPLATE_PATH
from assistant_agent_kanban import config as config_module
from assistant_agent_kanban.config import PROJECT_ROOT, load_config
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import AdapterRunError
from assistant_agent_kanban.exceptions import IntegrationError
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import HistoryEntry
from assistant_agent_kanban.opencode_adapter import _parse_discovered_models
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workspace_manager import WorkspaceManager
from assistant_agent_kanban.workers.implementer import ImplementerWorker
from assistant_agent_kanban.models import utc_now

from .conftest import FakeAdapter, create_request_task


def valid_plan_markdown(summary: str = "plan") -> str:
    return "\n".join(
        [
            "## Summary",
            summary,
            "",
            "## Scope",
            "- Scope item",
            "",
            "## Out of Scope",
            "- Out of scope item",
            "",
            "## File Map",
            "- `app.txt`: example file",
            "",
            "## Step-by-step Plan",
            "1. Update the task.",
            "",
            "## Validation Plan",
            "- Run focused tests.",
            "",
            "## Acceptance Criteria",
            "- The request is satisfied.",
            "",
            "## Risks",
            "- Low risk.",
            "",
            "## Open Questions",
            "- None.",
        ]
    )


def _settings_adapter_registry(opencode_adapter=None, codex_adapter=None):
    return {
        "opencode": opencode_adapter or FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]]),
        "codex": codex_adapter or FakeAdapter(["codex"], discovery_responses=[["gpt-5.4", "gpt-5"]]),
    }


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


def test_api_renders_default_format_runtime_logs_for_task(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "default-log-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    log_dir = config.runs_dir / task.metadata.task_id
    log_dir.mkdir(parents=True)
    (log_dir / "planner-001.jsonl").write_text("\x1b[32m## Summary\x1b[0m\nplan line\n")

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.metadata.task_id}/logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entries"][0]["rendered_content"] == "## Summary\n\nplan line"


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
            json={"content": valid_plan_markdown("edited plan")},
        )
        assert put_response.status_code == 200
        approve_response = client.post(f"/api/tasks/{waiting.metadata.task_id}/approve-plan")
        assert approve_response.status_code == 200

    updated_task = scanner.find_task(waiting.metadata.task_id)
    assert updated_task.state == TaskState.TODOS
    assert (updated_task.task_dir / "PLAN.md").read_text() == valid_plan_markdown("edited plan") + "\n"
    assert (updated_task.task_dir / "PLAN-HUMAN-APPROVAL.md").exists()
    assert (updated_task.task_dir / "PLAN-HUMAN-APPROVAL.json").exists()


def test_api_rejects_malformed_human_edited_plan_on_approval(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "plan-approve-malformed-task")
    app = create_app(config, FakeAdapter(["## Summary\nplan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    runtime = app.state.runtime
    metadata_store = runtime.planner.metadata_store
    scanner = runtime.planner.scanner
    transitions = runtime.planner.transitions
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text(valid_plan_markdown("original plan") + "\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    with TestClient(app) as client:
        save_response = client.put(
            f"/api/tasks/{waiting.metadata.task_id}/artifacts/PLAN.md",
            json={"content": "## Summary\nOnly summary\n"},
        )
        assert save_response.status_code == 200

        approve_response = client.post(f"/api/tasks/{waiting.metadata.task_id}/approve-plan")

    assert approve_response.status_code == 409
    assert approve_response.json()["detail"] == "PLAN.md missing required section: ## Scope"
    updated = scanner.find_task(waiting.metadata.task_id)
    assert updated.state == TaskState.WAITING_CHECK_PLANS
    assert (updated.task_dir / "PLAN.md").read_text() == "## Summary\nOnly summary\n"
    assert not (updated.task_dir / "PLAN-HUMAN-APPROVAL.md").exists()
    assert not (updated.task_dir / "PLAN-HUMAN-APPROVAL.json").exists()


def test_api_creates_request_with_plan_auto_approve_flag(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.post(
            "/api/requests",
            json={
                "title": "auto-approved plan request",
                "goal": "Create a request that auto-approves plans.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "plan_auto_approve": True,
            },
        )

    assert response.status_code == 200
    task = KanbanScanner(config).scan()[0]
    assert task.metadata.request.plan_auto_approve is True
    assert "plan_auto_approve: true" in (task.task_dir / "REQUEST.md").read_text()


def test_api_drafts_request_without_creating_task_dirs(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.runtime.coding_assistant = "codex"
    draft_adapter = FakeAdapter(
        [
            json.dumps(
                {
                    "reply": "I tightened the request and added a more testable acceptance list.",
                    "field_updates": {
                        "goal": "Implement an AI-assisted drafting flow in the existing request composer without creating tasks before final submit.",
                        "acceptance_criteria": [
                            "Users can chat with the drafting assistant in the composer.",
                            "Suggested field updates are optional and applied per field.",
                        ],
                    },
                }
            )
        ],
        resolved_models=["gpt-5.4"],
        session_ids=["ses_request_draft"],
        total_tokens=[31],
    )
    config.runtime.role_backends.request_draft = "codex"
    config.codex.request_draft_model = "gpt-5.4"
    app = create_app(
        config,
        draft_adapter,
        FakeAdapter(["impl"]),
        FakeAdapter(["Verdict: PASS"]),
        adapter_registry={"codex": draft_adapter},
    )

    with TestClient(app) as client:
        before = sorted(path.name for path in config.state_dir(TaskState.REQUESTS).iterdir())
        response = client.post(
            "/api/request-drafts",
            json={
                "title": "Composer drafting flow",
                "goal": "Add a draft assistant.",
                "background": "Keep the final create flow authoritative.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "message": "Please tighten the goal and acceptance criteria.",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reply"] == "I tightened the request and added a more testable acceptance list."
        assert payload["field_updates"]["goal"].startswith("Implement an AI-assisted drafting flow")
        assert payload["field_updates"]["acceptance_criteria"] == (
            "Users can chat with the drafting assistant in the composer.\n"
            "Suggested field updates are optional and applied per field."
        )
        assert payload["backend"] == "codex"
        assert payload["session_id"] == "ses_request_draft"
        assert payload["request_draft_id"]
        assert len(payload["transcript"]) == 2
        stored = client.get(f"/api/request-drafts/{payload['request_draft_id']}")
        assert stored.status_code == 200
        assert stored.json()["transcript"][0]["content"] == "Please tighten the goal and acceptance criteria."
        after = sorted(path.name for path in config.state_dir(TaskState.REQUESTS).iterdir())
        assert after == before

    assert draft_adapter.run_calls[0]["agent"] == "fs-kanban-request-draft"
    assert draft_adapter.run_calls[0]["cwd"] == config.repo_root.resolve()
    assert "Please tighten the goal and acceptance criteria." in str(draft_adapter.run_calls[0]["prompt"])


def test_api_can_create_load_update_and_delete_request_draft_state(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        created = client.post(
            "/api/request-drafts/state",
            json={
                "title": "Server draft",
                "goal": "Keep draft state on the server.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_upload_token": "server-draft-token",
            },
        )
        assert created.status_code == 200
        draft_id = created.json()["draft_id"]

        loaded = client.get(f"/api/request-drafts/{draft_id}")
        assert loaded.status_code == 200
        assert loaded.json()["goal"] == "Keep draft state on the server."

        updated = client.put(
            f"/api/request-drafts/{draft_id}",
            json={
                "background": "Updated background.",
                "active_tab": "fields",
                "request_draft_input": "pending prompt",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["background"] == "Updated background."
        assert updated.json()["active_tab"] == "fields"
        assert updated.json()["request_draft_input"] == "pending prompt"

        deleted = client.delete(f"/api/request-drafts/{draft_id}")
        assert deleted.status_code == 200

        missing = client.get(f"/api/request-drafts/{draft_id}")
        assert missing.status_code == 404
        assert not (config.request_uploads_dir / "server-draft-token").exists()


def test_api_lists_request_drafts_without_affecting_board(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        created = client.post(
            "/api/request-drafts/state",
            json={
                "title": "Visible draft",
                "goal": "Show drafts separately from requests.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_draft_input": "still editing",
            },
        )
        assert created.status_code == 200
        listing = client.get("/api/request-drafts")
        assert listing.status_code == 200
        payload = listing.json()
        assert len(payload["items"]) == 1
        assert payload["items"][0]["title"] == "Visible draft"
        assert payload["items"][0]["has_unsent_input"] is True

        board = client.get("/api/board")
        assert board.status_code == 200
        board_payload = board.json()
        request_column = next(column for column in board_payload["columns"] if column["state"] == "requests")
        assert request_column["items"] == []


def test_api_rejects_request_draft_for_overlapping_target_repo(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.post(
            "/api/request-drafts",
            json={
                "title": "Unsafe draft target",
                "goal": "Keep drafting safe.",
                "target_repo": str(PROJECT_ROOT),
                "base_branch": "main",
                "message": "Please refine this.",
            },
        )

    assert response.status_code == 400
    assert "target repo" in response.json()["detail"].lower()


def test_api_rejects_request_draft_for_missing_target_repo_directory(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.post(
            "/api/request-drafts",
            json={
                "title": "Missing target repo",
                "goal": "Keep drafting safe.",
                "target_repo": str(config.repo_root / "missing-repo"),
                "base_branch": "main",
                "message": "Please refine this.",
            },
        )

    assert response.status_code == 400
    assert "existing directory" in response.json()["detail"]


def test_api_rejects_request_draft_adapter_failures_cleanly(configured_paths):
    config, _, _ = configured_paths

    class FailingDraftAdapter(FakeAdapter):
        def run(self, **kwargs):
            raise AdapterRunError("request drafting backend unavailable")

    failing_adapter = FailingDraftAdapter([])
    config.runtime.role_backends.request_draft = "codex"
    app = create_app(
        config,
        FakeAdapter(["plan"]),
        FakeAdapter(["impl"]),
        FakeAdapter(["Verdict: PASS"]),
        adapter_registry={"codex": failing_adapter},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/request-drafts",
            json={
                "title": "Draft backend failure",
                "goal": "Show a clean API error.",
                "message": "Please refine this.",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "request drafting backend unavailable"


def test_api_request_creation_flow_stays_authoritative_after_draft_assistance(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    draft_adapter = FakeAdapter(
        [
            json.dumps(
                {
                    "reply": "Here is a stronger version of the goal.",
                    "field_updates": {
                        "goal": "Suggested drafted goal that should only matter if the user applies it.",
                    },
                }
            )
        ]
    )
    app = create_app(
        config,
        FakeAdapter(["plan"]),
        FakeAdapter(["impl"]),
        FakeAdapter(["Verdict: PASS"]),
        adapter_registry={"opencode": draft_adapter},
    )

    with TestClient(app) as client:
        draft_response = client.post(
            "/api/request-drafts",
            json={
                "title": "Keep create flow authoritative",
                "goal": "Original goal text.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "message": "Improve the goal.",
            },
        )
        assert draft_response.status_code == 200
        draft_id = draft_response.json()["request_draft_id"]
        assert not any(config.state_dir(TaskState.REQUESTS).iterdir())

        create_response = client.post(
            "/api/requests",
            json={
                "title": "Keep create flow authoritative",
                "goal": "Original goal text.",
                "background": "The draft assistant should not create tasks.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "plan_auto_approve": True,
                "request_draft_id": draft_id,
            },
        )
        assert create_response.status_code == 200

        deleted_draft = client.get(f"/api/request-drafts/{draft_id}")
        assert deleted_draft.status_code == 404

    tasks = KanbanScanner(config).scan()
    assert len(tasks) == 1
    request_markdown = (tasks[0].task_dir / "REQUEST.md").read_text()
    assert "Original goal text." in request_markdown
    assert "Suggested drafted goal that should only matter if the user applies it." not in request_markdown


def test_api_request_creation_finalizes_shared_request_upload_links_once(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    image_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0bIDATx\x9cc``\x00\x00\x00\x02\x00\x01\xe2!\xbc3"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(app) as client:
        upload = client.post(
            "/api/request-uploads?upload_token=req-shared-upload",
            files={"file": ("shared.png", image_bytes, "image/png")},
        )
        assert upload.status_code == 200
        upload_url = upload.json()["url"]

        response = client.post(
            "/api/requests",
            json={
                "title": "Shared upload request",
                "goal": f"See image twice\n\n![first]({upload_url})\n\n![second]({upload_url})",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_upload_token": "req-shared-upload",
            },
        )

    assert response.status_code == 200
    task = KanbanScanner(config).scan()[0]
    request_markdown = (task.task_dir / "REQUEST.md").read_text()
    assert request_markdown.count("_attachments/") == 2
    attachments = list((task.task_dir / "_attachments").iterdir())
    assert len(attachments) == 1


def test_api_request_creation_finalizes_request_upload_links_in_all_request_fields(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    image_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0bIDATx\x9cc``\x00\x00\x00\x02\x00\x01\xe2!\xbc3"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(app) as client:
        upload = client.post(
            "/api/request-uploads?upload_token=req-all-fields",
            files={"file": ("scope.png", image_bytes, "image/png")},
        )
        assert upload.status_code == 200
        upload_url = upload.json()["url"]

        response = client.post(
            "/api/requests",
            json={
                "title": "All field uploads request",
                "goal": "Keep the create path authoritative.",
                "scope": f"Include image\n\n![scope]({upload_url})",
                "constraints": f"Visual note\n\n![constraint]({upload_url})",
                "references": f"Ref path\n\n![reference]({upload_url})",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_upload_token": "req-all-fields",
            },
        )

    assert response.status_code == 200
    task = KanbanScanner(config).scan()[0]
    request_markdown = (task.task_dir / "REQUEST.md").read_text()
    assert "/api/request-uploads/req-all-fields/" not in request_markdown
    assert request_markdown.count("_attachments/") == 3


def test_api_request_creation_writes_request_draft_artifact(configured_paths):
    config, _, _ = configured_paths
    config.runtime.role_backends.request_draft = "codex"
    draft_adapter = FakeAdapter(
        [
            json.dumps(
                {
                    "reply": "I tightened the draft.",
                    "field_updates": {
                        "goal": "",
                        "scope": "Add the retry path",
                    },
                }
            )
        ]
    )
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry={"codex": draft_adapter})

    image_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0bIDATx\x9cc``\x00\x00\x00\x02\x00\x01\xe2!\xbc3"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(app) as client:
        upload = client.post(
            "/api/request-uploads?upload_token=req-draft-artifact",
            files={"file": ("draft.png", image_bytes, "image/png")},
        )
        assert upload.status_code == 200
        upload_url = upload.json()["url"]

        draft = client.post(
            "/api/request-drafts",
            json={
                "title": "Draft artifact request",
                "goal": "Persist the authoritative request normally.",
                "background": "The draft transcript should be saved separately.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_upload_token": "req-draft-artifact",
                "message": f"Please consider this image.\n\n![draft]({upload_url})",
            },
        )
        assert draft.status_code == 200
        draft_id = draft.json()["request_draft_id"]

        response = client.post(
            "/api/requests",
            json={
                "title": "Draft artifact request",
                "goal": "Persist the authoritative request normally.",
                "background": "The draft transcript should be saved separately.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_upload_token": "req-draft-artifact",
                "request_draft_id": draft_id,
            },
        )

    assert response.status_code == 200
    task = KanbanScanner(config).scan()[0]
    request_draft_markdown = (task.task_dir / "REQUEST-DRAFT.md").read_text()
    assert "Non-authoritative drafting context" in request_draft_markdown
    assert "/api/request-uploads/req-draft-artifact/" not in request_draft_markdown
    assert "_attachments/" in request_draft_markdown
    assert "### Suggested updates" in request_draft_markdown
    assert "**Scope**: Add the retry path" in request_draft_markdown


def test_api_request_creation_failure_does_not_consume_request_uploads(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    image_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0bIDATx\x9cc``\x00\x00\x00\x02\x00\x01\xe2!\xbc3"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(app) as client:
        upload = client.post(
            "/api/request-uploads?upload_token=req-failed-create",
            files={"file": ("draft.png", image_bytes, "image/png")},
        )
        assert upload.status_code == 200
        upload_url = upload.json()["url"]

        created_draft = client.post(
            "/api/request-drafts/state",
            json={
                "title": "Failed create keeps uploads",
                "goal": f"![draft]({upload_url})",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_upload_token": "req-failed-create",
            },
        )
        assert created_draft.status_code == 200
        draft_id = created_draft.json()["draft_id"]

        failed = client.post(
            "/api/requests",
            json={
                "title": "Failed create keeps uploads",
                "goal": f"![draft]({upload_url})",
                "target_repo": str(PROJECT_ROOT),
                "base_branch": "main",
                "request_upload_token": "req-failed-create",
                "request_draft_id": draft_id,
            },
        )
        assert failed.status_code == 400

        still_exists = client.get(upload_url)
        assert still_exists.status_code == 200
        preserved_draft = client.get(f"/api/request-drafts/{draft_id}")
        assert preserved_draft.status_code == 200

        succeeded = client.post(
            "/api/requests",
            json={
                "title": "Retry create keeps uploads",
                "goal": f"![draft]({upload_url})",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
                "request_upload_token": "req-failed-create",
                "request_draft_id": draft_id,
            },
        )

        deleted_draft = client.get(f"/api/request-drafts/{draft_id}")

    assert succeeded.status_code == 200
    assert deleted_draft.status_code == 404
    task = KanbanScanner(config).scan()[0]
    request_markdown = (task.task_dir / "REQUEST.md").read_text()
    assert "/api/request-uploads/req-failed-create/" not in request_markdown
    assert "_attachments/" in request_markdown


def test_api_resumes_human_blocked_review_loop(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "resume-review-loop-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-review-loop-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    blocked = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    blocked.metadata.review.consecutive_rework_loops = 3
    blocked.metadata.review.total_rework_loops = 6
    blocked.metadata.review.rework_loop_plan_revision = blocked.metadata.plan.revision
    blocked.metadata.review.primary_blocker = "changed-scope-coverage"
    blocked.metadata.review.human_rework_required = True
    blocked.metadata.review.human_rework_reason = "human review required after 3 consecutive review rework loops"
    metadata_store.save(blocked.task_dir, blocked.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{blocked.metadata.task_id}/resume-review-loop",
            json={"message": "Please keep the existing review direction, but incorporate the human note."},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["review"]["consecutive_rework_loops"] == 0
        assert payload["review"]["total_rework_loops"] == 0
        assert payload["review"]["primary_blocker"] is None
        assert payload["review"]["human_rework_required"] is False

        detail = client.get(f"/api/tasks/{blocked.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["review"]["human_rework_required"] is False
        assert "Please keep the existing review direction" in detail.json()["human_review"]["reviewer_qa_markdown"]

    qa_artifact = blocked.task_dir / "REVIEWER-QA-000.md"
    assert qa_artifact.exists()
    qa_markdown = qa_artifact.read_text()
    assert "## Question 1" in qa_markdown
    assert "- Source: human resume note" in qa_markdown
    assert "Please keep the existing review direction, but incorporate the human note." in qa_markdown


def test_api_resumes_planner_from_requests_retry_gate_with_message(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-planner-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-planner-task")
    task.metadata.plan.session_id = "ses_bad_planner"
    task.metadata.plan.session_tokens = 77
    task.metadata.plan.last_run_tokens = 13
    task.metadata.plan.resolved_model = "openai/gpt-bad"
    task.metadata.retry_gate.reason = "planner-invalid-artifact"
    metadata_store.save(task.task_dir, task.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task.metadata.task_id}/resume-planner",
            json={"message": "Please regenerate PLAN.md with the required headings and keep the request scope tight."},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["retry_gate"]["reason"] is None
        assert payload["retry_gate"]["consecutive_count"] == 0
        assert payload["retry_gate"]["not_before"] is None
        assert payload["plan"]["session_id"] is None
        assert payload["plan"]["session_tokens"] == 0
        assert payload["plan"]["last_run_tokens"] == 0
        assert payload["plan"]["resolved_model"] is None
        assert payload["plan"]["restart_message_path"] == "PLANNER-RESTART.md"

        detail = client.get(f"/api/tasks/{task.metadata.task_id}")
        assert detail.status_code == 200
        assert "PLANNER-RESTART.md" in detail.json()["markdown_files"]

    restart_artifact = task.task_dir / "PLANNER-RESTART.md"
    assert restart_artifact.exists()
    restart_markdown = restart_artifact.read_text()
    assert "# Planner Restart Notes" in restart_markdown
    assert "- Source: manual planner restart" in restart_markdown
    assert "Please regenerate PLAN.md with the required headings and keep the request scope tight." in restart_markdown


def test_api_resume_planner_without_message_clears_restart_pointer(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-planner-empty-message-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-planner-empty-message-task")
    task.metadata.plan.restart_message_path = "PLANNER-RESTART.md"
    task.metadata.retry_gate.reason = "planner-invalid-artifact"
    (task.task_dir / "PLANNER-RESTART.md").write_text("# Planner Restart Notes\n\n## Note 1\nold note\n")
    metadata_store.save(task.task_dir, task.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task.metadata.task_id}/resume-planner",
            json={"message": "   "},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["plan"]["restart_message_path"] is None


def test_api_resumes_reviewer_from_waiting_reviews_retry_gate(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-reviewer-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-reviewer-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    planning.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    metadata_store.save(implementing.task_dir, implementing.metadata)
    implementing.metadata.implementation.workspace = str(config.repo_root)
    metadata_store.save(implementing.task_dir, implementing.metadata)
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    waiting_reviews.metadata.review.last_verdict = None
    waiting_reviews.metadata.review.session_id = "ses_review"
    waiting_reviews.metadata.review.session_tokens = 123
    waiting_reviews.metadata.review.last_run_tokens = 45
    waiting_reviews.metadata.retry_gate.reason = "review-finalize-failed"
    waiting_reviews.metadata.retry_gate.consecutive_count = 1
    waiting_reviews.metadata.retry_gate.not_before = utc_now()
    metadata_store.save(waiting_reviews.task_dir, waiting_reviews.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{waiting_reviews.metadata.task_id}/resume-reviewer",
            json={"message": "Please focus on the reviewer concerns from the prior retry."},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["retry_gate"]["reason"] is None
        assert payload["retry_gate"]["consecutive_count"] == 0
        assert payload["retry_gate"]["not_before"] is None
        assert payload["review"]["last_verdict"] is None
        assert payload["review"]["resume_mode"] == "pinned"
        assert payload["review"]["resume_backend_override"] is None
        assert payload["review"]["resume_model_override"] is None
        assert payload["review"]["session_id"] is None
        assert payload["review"]["session_tokens"] == 0
        assert payload["review"]["last_run_tokens"] == 0

        detail = client.get(f"/api/tasks/{waiting_reviews.metadata.task_id}")
        assert detail.status_code == 200
        assert "Please focus on the reviewer concerns from the prior retry." in detail.json()["human_review"]["reviewer_qa_markdown"]

    reviewer_qa_artifact = waiting_reviews.task_dir / "REVIEWER-QA-000.md"
    assert reviewer_qa_artifact.exists()
    reviewer_qa_markdown = reviewer_qa_artifact.read_text()
    assert "## Question 1" in reviewer_qa_markdown
    assert "- Source: human resume note" in reviewer_qa_markdown
    assert "Please focus on the reviewer concerns from the prior retry." in reviewer_qa_markdown


def test_api_resume_message_resets_reviewer_qa_session_on_cycle_change(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-reviewer-cycle-reset-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-reviewer-cycle-reset-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.cycle = 1
    implementing.metadata.implementation.workspace = str(config.repo_root)
    implementing.metadata.review.qa_path = "REVIEWER-QA-000.md"
    implementing.metadata.review.qa_session_id = "ses_old"
    implementing.metadata.review.qa_last_run_tokens = 11
    implementing.metadata.review.qa_session_tokens = 29
    implementing.metadata.review.qa_resolved_model = "old-model"
    metadata_store.save(implementing.task_dir, implementing.metadata)
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    waiting_reviews.metadata.retry_gate.reason = "review-finalize-failed"
    waiting_reviews.metadata.retry_gate.consecutive_count = 1
    waiting_reviews.metadata.retry_gate.not_before = utc_now()
    metadata_store.save(waiting_reviews.task_dir, waiting_reviews.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{waiting_reviews.metadata.task_id}/resume-reviewer",
            json={"message": "Use the fresh cycle context for this rerun."},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["review"]["qa_path"] == "REVIEWER-QA-001.md"
        assert payload["review"]["qa_session_id"] is None
        assert payload["review"]["qa_last_run_tokens"] == 0
        assert payload["review"]["qa_session_tokens"] == 0
        assert payload["review"]["qa_resolved_model"] is None


def test_api_resumes_reviewer_with_current_settings_override(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.runtime.coding_assistant = "codex"
    config.runtime.role_backends.reviewer = "codex"
    config.codex.reviewer_model = "gpt-5.4"
    create_request_task(config, "resume-reviewer-current-settings-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-reviewer-current-settings-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    planning.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.runtime_pin.backend = "gemini"
    implementing.metadata.runtime_pin.role_backends.reviewer = "gemini"
    implementing.metadata.runtime_pin.reviewer_model = "gemini-2.5-pro"
    implementing.metadata.implementation.workspace = str(config.repo_root)
    metadata_store.save(implementing.task_dir, implementing.metadata)
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    waiting_reviews.metadata.retry_gate.reason = "review-finalize-failed"
    waiting_reviews.metadata.retry_gate.consecutive_count = 1
    waiting_reviews.metadata.retry_gate.not_before = utc_now()
    metadata_store.save(waiting_reviews.task_dir, waiting_reviews.metadata)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{waiting_reviews.metadata.task_id}/resume-reviewer", json={"resume_mode": "current-settings"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["review"]["resume_mode"] == "current-settings"
        assert payload["review"]["resume_backend_override"] == "codex"
        assert payload["review"]["resume_model_override"] == "gpt-5.4"
        assert payload["runtime_pin"]["role_backends"]["reviewer"] == "gemini"
        assert payload["runtime_pin"]["reviewer_model"] == "gemini-2.5-pro"


def test_api_resumes_implementer_from_todos_retry_gate(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-implementer-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-implementer-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    planning.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos.metadata.implementation.last_result = "failure"
    todos.metadata.implementation.session_id = "ses_impl"
    todos.metadata.implementation.session_tokens = 321
    todos.metadata.implementation.last_run_tokens = 123
    todos.metadata.retry_gate.reason = "implementation-no-changes"
    todos.metadata.retry_gate.consecutive_count = 1
    todos.metadata.retry_gate.not_before = utc_now()
    metadata_store.save(todos.task_dir, todos.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{todos.metadata.task_id}/resume-implementer",
            json={"message": "Please address the missing implementation details before retrying."},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["retry_gate"]["reason"] is None
        assert payload["retry_gate"]["consecutive_count"] == 0
        assert payload["retry_gate"]["not_before"] is None
        assert payload["implementation"]["last_result"] is None
        assert payload["implementation"]["resume_mode"] == "pinned"
        assert payload["implementation"]["resume_backend_override"] is None
        assert payload["implementation"]["resume_model_override"] is None
        assert payload["implementation"]["session_id"] is None
        assert payload["implementation"]["session_tokens"] == 0
        assert payload["implementation"]["last_run_tokens"] == 0

        detail = client.get(f"/api/tasks/{todos.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["retry_gate"]["reason"] is None
        assert "Please address the missing implementation details before retrying." in detail.json()["human_review"]["reviewer_qa_markdown"]

    implementer_qa_artifact = todos.task_dir / "REVIEWER-QA-000.md"
    assert implementer_qa_artifact.exists()
    implementer_qa_markdown = implementer_qa_artifact.read_text()
    assert "## Question 1" in implementer_qa_markdown
    assert "- Source: human resume note" in implementer_qa_markdown
    assert "Please address the missing implementation details before retrying." in implementer_qa_markdown


def test_api_rejects_resume_implementer_without_implementation_failure(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-implementer-reject-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-implementer-reject-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    metadata_store.save(todos.task_dir, todos.metadata)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{todos.metadata.task_id}/resume-implementer")
        assert response.status_code == 409
        assert response.json()["detail"] == "implementer resume is only allowed when an active implementation retry gate is present"


def test_api_rejects_second_resume_implementer_after_gate_is_cleared(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-implementer-once-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-implementer-once-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos.metadata.implementation.last_result = "failure"
    todos.metadata.retry_gate.reason = "implementation-failed"
    todos.metadata.retry_gate.consecutive_count = 1
    todos.metadata.retry_gate.not_before = utc_now()
    metadata_store.save(todos.task_dir, todos.metadata)

    with TestClient(app) as client:
        first = client.post(f"/api/tasks/{todos.metadata.task_id}/resume-implementer")
        assert first.status_code == 200
        second = client.post(f"/api/tasks/{todos.metadata.task_id}/resume-implementer")
        assert second.status_code == 409
        assert second.json()["detail"] == "implementer resume is only allowed when an active implementation retry gate is present"


def test_api_rejects_resume_implementer_when_retry_gate_not_active(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-implementer-inactive-gate-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-implementer-inactive-gate-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos.metadata.retry_gate.reason = "implementation-failed"
    todos.metadata.retry_gate.consecutive_count = 1
    todos.metadata.retry_gate.not_before = None
    metadata_store.save(todos.task_dir, todos.metadata)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{todos.metadata.task_id}/resume-implementer")
        assert response.status_code == 409
        assert response.json()["detail"] == "implementer resume is only allowed when an active implementation retry gate is present"


def test_api_resumes_implementer_with_current_settings_override(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.runtime.coding_assistant = "codex"
    config.runtime.role_backends.implementer = "codex"
    config.codex.implementer_model = "gpt-5.4"
    create_request_task(config, "resume-implementer-current-settings-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-implementer-current-settings-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    planning.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos.metadata.runtime_pin.backend = "gemini"
    todos.metadata.runtime_pin.role_backends.implementer = "gemini"
    todos.metadata.runtime_pin.implementer_model = "gemini-2.5-pro"
    todos.metadata.implementation.last_result = "failure"
    todos.metadata.retry_gate.reason = "implementation-failed"
    todos.metadata.retry_gate.consecutive_count = 1
    todos.metadata.retry_gate.not_before = utc_now()
    metadata_store.save(todos.task_dir, todos.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{todos.metadata.task_id}/resume-implementer",
            json={"resume_mode": "current-settings"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["implementation"]["resume_mode"] == "current-settings"
        assert payload["implementation"]["resume_backend_override"] == "codex"
        assert payload["implementation"]["resume_model_override"] == "gpt-5.4"
        assert payload["runtime_pin"]["role_backends"]["implementer"] == "gemini"
        assert payload["runtime_pin"]["implementer_model"] == "gemini-2.5-pro"


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


def test_api_uploads_and_serves_request_attachments(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        upload = client.post(
            "/api/request-uploads?upload_token=request-upload-token",
            files={"file": ("diagram.png", b"pngdata", "image/png")},
        )
        assert upload.status_code == 200
        payload = upload.json()
        assert payload["upload_token"] == "request-upload-token"
        assert payload["url"].endswith(payload["filename"])
        assert payload["relative_path"] == payload["url"]

        upload_dir = config.request_uploads_dir / "request-upload-token"
        assert (upload_dir / payload["filename"]).read_bytes() == b"pngdata"

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


def test_api_keeps_human_verifying_and_allows_retry_on_verification_conflict(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-start-conflict-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-start-conflict-task")

    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")

        assert start.status_code == 200
        assert start.json()["state"] == TaskState.HUMAN_VERIFYING.value
        conflict_detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        assert conflict_detail.status_code == 200
        assert conflict_detail.json()["changed_files_available"] is True
        assert len(conflict_detail.json()["changed_files"]) > 0

        subprocess.run(["git", "-C", str(repo_root), "reset", "--hard", "HEAD~1"], check=True, capture_output=True, text=True)
        retry = client.post(f"/api/tasks/{completed.metadata.task_id}/retry-verification-apply")

    assert retry.status_code == 200
    assert retry.json()["state"] == TaskState.HUMAN_VERIFYING.value
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.applied is True
    assert refreshed.metadata.commit.status == "review-committed"
    assert refreshed.metadata.retry_gate.reason is None


def test_api_rejects_retry_when_verification_apply_is_already_active(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-retry-guard-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-verify-retry-guard-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200
        retry = client.post(f"/api/tasks/{completed.metadata.task_id}/retry-verification-apply")

    assert retry.status_code == 409
    assert "verification apply has already succeeded" in retry.json()["detail"]


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


def test_api_allows_reject_after_verification_conflict_without_extra_feedback(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-reject-conflict-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-reject-conflict-task")

    (repo_root / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200
        reject = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reject-verification",
            json={"note": ""},
        )

    assert reject.status_code == 200
    assert reject.json()["state"] == TaskState.TODOS.value
    assert scanner.find_task(completed.metadata.task_id).state == TaskState.TODOS


def test_api_allows_reject_when_review_recapture_fails(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-verify-reject-recapture-failure-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-reject-recapture-failure-task")

    runtime = app.state.runtime
    original_capture = runtime.verification_service._capture_review_branch_to_workspace

    def fail_capture(metadata):
        raise IntegrationError("failed to apply reviewed code back into workspace")

    runtime.verification_service._capture_review_branch_to_workspace = fail_capture
    try:
        with TestClient(app) as client:
            start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
            assert start.status_code == 200
            reject = client.post(
                f"/api/tasks/{completed.metadata.task_id}/reject-verification",
                json={"note": "Need another pass"},
            )
    finally:
        runtime.verification_service._capture_review_branch_to_workspace = original_capture

    assert reject.status_code == 200
    assert reject.json()["state"] == TaskState.TODOS.value
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert any(error.code == "human-verification-recapture-failed" for error in refreshed.metadata.errors)


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


def test_runtime_handles_slack_interactive_approve_action(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "slack-interactive-approve-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "slack-interactive-approve-task")

    with TestClient(app):
        app.state.runtime.verification_service.start(completed.metadata.task_id, by="human")
        task = app.state.runtime.scanner.find_task(completed.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        error = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "user": {"id": "U123"},
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "173.456", "ts": "173.789"},
                    "actions": [
                        {
                            "action_id": "approve_verification",
                            "value": json.dumps({"task_id": completed.metadata.task_id, "action": "approve_verification"}),
                        }
                    ],
                }
            )
        )

    assert error == {"status": "success", "clear_buttons": True}
    assert app.state.runtime.scanner.find_task(completed.metadata.task_id).state == TaskState.DONE


def test_runtime_handles_slack_interactive_start_verification_action(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "slack-interactive-start-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "slack-interactive-start-task")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(completed.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        error = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "user": {"id": "U123"},
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "173.456", "ts": "173.789"},
                    "actions": [
                        {
                            "action_id": "start_verification",
                            "value": json.dumps({"task_id": completed.metadata.task_id, "action": "start_verification"}),
                        }
                    ],
                }
            )
        )

    assert error == {"status": "success", "clear_buttons": True}
    assert app.state.runtime.scanner.find_task(completed.metadata.task_id).state == TaskState.HUMAN_VERIFYING


def test_runtime_rejects_slack_start_verification_from_wrong_thread(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "slack-interactive-start-wrong-thread-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "slack-interactive-start-wrong-thread-task")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(completed.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        error = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "user": {"id": "U123"},
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "wrong-thread", "ts": "173.789"},
                    "actions": [
                        {
                            "action_id": "start_verification",
                            "value": json.dumps({"task_id": completed.metadata.task_id, "action": "start_verification"}),
                        }
                    ],
                }
            )
        )

    assert error == {"status": "error", "message": "This Slack action no longer matches the current task thread."}
    assert app.state.runtime.scanner.find_task(completed.metadata.task_id).state == TaskState.COMPLETED_REVIEWS


def test_runtime_handles_slack_interactive_resume_review_loop_action(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-resume-review-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    reviewing.metadata.review.human_rework_required = True
    metadata_store.save(reviewing.task_dir, reviewing.metadata)
    todos = transitions.move(reviewing, TaskState.TODOS, by="reviewer", note="needs rework")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(todos.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        modal_calls: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_call(method: str, *, token: str, body=None):
            modal_calls.append((method, token, body))
            return {"ok": True}

        monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)
        error = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "block_actions",
                    "user": {"id": "U123"},
                    "trigger_id": "trigger-123",
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "173.456", "ts": "173.789", "text": "Resume message"},
                    "actions": [
                        {
                            "action_id": "resume_review_loop",
                            "value": json.dumps({"task_id": todos.metadata.task_id, "action": "resume_review_loop"}),
                        }
                    ],
                }
            )
        )

    assert error == {"status": "opened_modal", "clear_buttons": False}
    assert modal_calls
    assert modal_calls[0][0] == "views.open"
    modal_body = modal_calls[0][2]
    assert modal_body is not None
    assert modal_body["trigger_id"] == "trigger-123"
    view = modal_body["view"]
    assert isinstance(view, dict)
    assert view["callback_id"] == "resume_review_loop_modal"
    blocks = view["blocks"]
    assert isinstance(blocks, list)
    input_block = blocks[0]
    assert input_block["block_id"] == "resume_review_loop_input"
    assert input_block["element"]["action_id"] == "message_input"
    resumed = app.state.runtime.scanner.find_task(todos.metadata.task_id)
    assert resumed.state == TaskState.TODOS
    assert resumed.metadata.review.human_rework_required is True


def test_runtime_handles_slack_resume_review_loop_modal_submission(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-resume-review-submit-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    reviewing.metadata.review.human_rework_required = True
    metadata_store.save(reviewing.task_dir, reviewing.metadata)
    todos = transitions.move(reviewing, TaskState.TODOS, by="reviewer", note="needs rework")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(todos.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        modal_calls: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_call(method: str, *, token: str, body=None):
            modal_calls.append((method, token, body))
            return {"ok": True}

        monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "resume_review_loop_modal",
                        "private_metadata": json.dumps(
                            {
                                "task_id": todos.metadata.task_id,
                                "channel_id": "C123",
                                "thread_ts": "173.456",
                                "message_ts": "173.789",
                                "message_text": "🔁 Review requested changes",
                            }
                        ),
                        "state": {
                            "values": {
                                "resume_review_loop_input": {
                                    "message_input": {"value": "Please re-run the review with the new fix."}
                                }
                            }
                        },
                    },
                }
            )
        )

    assert result == {"status": "success"}
    resumed = app.state.runtime.scanner.find_task(todos.metadata.task_id)
    assert resumed.state == TaskState.TODOS
    assert resumed.metadata.review.human_rework_required is False
    assert modal_calls
    assert modal_calls[0][0] == "chat.update"


def test_runtime_rejects_blank_slack_resume_review_loop_modal_submission(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-resume-review-blank-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    reviewing.metadata.review.human_rework_required = True
    metadata_store.save(reviewing.task_dir, reviewing.metadata)
    todos = transitions.move(reviewing, TaskState.TODOS, by="reviewer", note="needs rework")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(todos.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "resume_review_loop_modal",
                        "private_metadata": json.dumps(
                            {
                                "task_id": todos.metadata.task_id,
                                "channel_id": "C123",
                                "thread_ts": "173.456",
                                "message_ts": "173.789",
                                "message_text": "🔁 Review requested changes",
                            }
                        ),
                        "state": {
                            "values": {
                                "resume_review_loop_input": {
                                    "message_input": {"value": "   "}
                                }
                            }
                        },
                    },
                }
            )
        )

    assert result == {"status": "error", "message": "Resume message is required."}
    resumed = app.state.runtime.scanner.find_task(todos.metadata.task_id)
    assert resumed.metadata.review.human_rework_required is True


def test_runtime_handles_slack_interactive_request_changes_action(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-request-changes-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    verifying = transitions.move(completed, TaskState.HUMAN_VERIFYING, by="human")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        modal_calls: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_call(method: str, *, token: str, body=None):
            modal_calls.append((method, token, body))
            return {"ok": True}

        monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "block_actions",
                    "user": {"id": "U123"},
                    "trigger_id": "trigger-123",
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "173.456", "ts": "173.789", "text": "Human verification started"},
                    "actions": [
                        {
                            "action_id": "reject_verification",
                            "value": json.dumps({"task_id": verifying.metadata.task_id, "action": "reject_verification"}),
                        }
                    ],
                }
            )
        )

    assert result == {"status": "opened_modal", "clear_buttons": False}
    assert modal_calls
    assert modal_calls[0][0] == "views.open"
    modal_body = modal_calls[0][2]
    assert modal_body is not None
    assert modal_body["trigger_id"] == "trigger-123"
    view = modal_body["view"]
    assert isinstance(view, dict)
    assert view["callback_id"] == "reject_verification_modal"
    blocks = view["blocks"]
    assert isinstance(blocks, list)
    input_block = blocks[0]
    assert input_block["block_id"] == "reject_verification_input"
    assert input_block["element"]["action_id"] == "message_input"
    persisted = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
    assert persisted.state == TaskState.HUMAN_VERIFYING


def test_runtime_handles_slack_request_changes_modal_submission(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-request-changes-submit-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    verifying = transitions.move(completed, TaskState.HUMAN_VERIFYING, by="human")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        modal_calls: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_call(method: str, *, token: str, body=None):
            modal_calls.append((method, token, body))
            return {"ok": True}

        monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "reject_verification_modal",
                        "private_metadata": json.dumps(
                            {
                                "task_id": verifying.metadata.task_id,
                                "channel_id": "C123",
                                "thread_ts": "173.456",
                                "message_ts": "173.789",
                                "message_text": "🧪 Human verification started",
                            }
                        ),
                        "state": {
                            "values": {
                                "reject_verification_input": {
                                    "message_input": {"value": "Please fix the failing verification comments."}
                                }
                            }
                        },
                    },
                }
            )
        )

    assert result == {"status": "success"}
    rejected = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
    assert rejected.state == TaskState.TODOS
    assert rejected.metadata.human_verification.note_markdown == "Please fix the failing verification comments."
    assert modal_calls
    assert modal_calls[0][0] == "chat.update"


def test_runtime_rejects_blank_slack_request_changes_modal_submission(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-request-changes-blank-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    verifying = transitions.move(completed, TaskState.HUMAN_VERIFYING, by="human")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "reject_verification_modal",
                        "private_metadata": json.dumps(
                            {
                                "task_id": verifying.metadata.task_id,
                                "channel_id": "C123",
                                "thread_ts": "173.456",
                                "message_ts": "173.789",
                                "message_text": "🧪 Human verification started",
                            }
                        ),
                        "state": {
                            "values": {
                                "reject_verification_input": {
                                    "message_input": {"value": "   "}
                                }
                            }
                        },
                    },
                }
            )
        )

    assert result == {"status": "error", "message": "Request changes message is required."}
    persisted = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
    assert persisted.state == TaskState.HUMAN_VERIFYING


def test_runtime_rejects_slack_request_changes_modal_submission_for_wrong_thread(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-request-changes-wrong-thread-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    verifying = transitions.move(completed, TaskState.HUMAN_VERIFYING, by="human")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "reject_verification_modal",
                        "private_metadata": json.dumps(
                            {
                                "task_id": verifying.metadata.task_id,
                                "channel_id": "C123",
                                "thread_ts": "wrong-thread",
                                "message_ts": "173.789",
                                "message_text": "🧪 Human verification started",
                            }
                        ),
                        "state": {
                            "values": {
                                "reject_verification_input": {
                                    "message_input": {"value": "Please address the verification note."}
                                }
                            }
                        },
                    },
                }
            )
        )

    assert result == {"status": "error", "message": "This Slack action no longer matches the current task thread."}
    persisted = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
    assert persisted.state == TaskState.HUMAN_VERIFYING


def test_runtime_rejects_slack_request_changes_modal_submission_for_wrong_channel(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "slack-interactive-request-changes-wrong-channel-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.move(waiting, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    verifying = transitions.move(completed, TaskState.HUMAN_VERIFYING, by="human")

    with TestClient(app):
        task = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
        task.metadata.slack.thread_ts = "173.456"
        task.metadata.slack.channel = "C123"
        app.state.runtime.scanner.metadata_store.save(task.task_dir, task.metadata)
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "reject_verification_modal",
                        "private_metadata": json.dumps(
                            {
                                "task_id": verifying.metadata.task_id,
                                "channel_id": "C999",
                                "thread_ts": "173.456",
                                "message_ts": "173.789",
                                "message_text": "🧪 Human verification started",
                            }
                        ),
                        "state": {
                            "values": {
                                "reject_verification_input": {
                                    "message_input": {"value": "Please address the verification note."}
                                }
                            }
                        },
                    },
                }
            )
        )

    assert result == {"status": "error", "message": "This Slack action was submitted from the wrong Slack channel."}
    persisted = app.state.runtime.scanner.find_task(verifying.metadata.task_id)
    assert persisted.state == TaskState.HUMAN_VERIFYING


def test_runtime_posts_slack_request_intake_button_on_app_mention(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        calls.append(("slack_upload_file_to_thread", token, {"channel": channel_id, "thread_ts": thread_ts, "filename": filename, "content": content.decode("utf-8")}))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_upload_file_to_thread", fake_upload)

    with TestClient(app):
        asyncio.run(
            app.state.runtime.handle_slack_app_mention(
                {"team_id": "T123"},
                {"channel": "C123", "ts": "173.456", "text": "<@U1> create request"},
            )
        )

    assert calls
    assert calls[0][0] == "chat.postMessage"
    payload = calls[0][2]
    assert payload is not None
    assert payload["channel"] == "C123"
    assert payload["thread_ts"] == "173.456"
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    assert blocks[1]["elements"][0]["action_id"] == "open_request_intake"


def test_runtime_opens_slack_request_intake_modal_without_creating_task(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    create_request_task(config, "previous-project-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)

    with TestClient(app):
        before = len(app.state.runtime.scanner.scan())
        result = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "block_actions",
                    "team": {"id": "T123"},
                    "user": {"id": "U123"},
                    "trigger_id": "trigger-123",
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "173.456", "ts": "173.789", "text": "Create request"},
                    "actions": [{"action_id": "open_request_intake", "value": json.dumps({"action": "open_request_intake"})}],
                }
            )
        )
        after = len(app.state.runtime.scanner.scan())

    assert result == {"status": "opened_modal", "clear_buttons": False}
    assert before == after
    assert len(list(config.request_drafts_dir.glob("*.json"))) == 1
    assert calls
    assert calls[0][0] == "views.open"
    view = calls[0][2]["view"]
    assert isinstance(view, dict)
    assert view["callback_id"] == "request_intake_modal"
    blocks = view["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["block_id"] == "request_intake_intro"
    assert view["title"]["text"] == "Draft request"
    assert view["submit"]["text"] == "Post draft to thread"
    project_block = blocks[1]
    assert project_block["block_id"] == "request_intake_project"
    assert project_block["element"]["action_id"] == "project_select"
    assert project_block["element"]["options"][0]["value"] == str(config.repo_root)
    assert blocks[2]["element"]["initial_value"] == "main"


def test_runtime_slack_request_intake_requires_assistant_then_creates_task(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.slack.bot_token = "xoxb-test"
    config.runtime.role_backends.request_draft = "codex"
    create_request_task(config, "previous-project-task")
    draft_adapter = FakeAdapter(
        [
            json.dumps(
                {
                    "reply": "I tightened the request.",
                    "field_updates": {"title": "Slack drafted title", "goal": "Slack drafted goal"},
                }
            )
        ]
    )
    app = create_app(
        config,
        FakeAdapter(["plan"]),
        FakeAdapter(["impl"]),
        FakeAdapter(["Verdict: PASS"]),
        adapter_registry={"codex": draft_adapter},
    )
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        calls.append(("slack_upload_file_to_thread", token, {"channel": channel_id, "thread_ts": thread_ts, "filename": filename, "content": content.decode("utf-8")}))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.runtime.slack_upload_file_to_thread", fake_upload)

    with TestClient(app):
        opened = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "block_actions",
                    "team": {"id": "T123"},
                    "user": {"id": "U123"},
                    "trigger_id": "trigger-123",
                    "channel": {"id": "C123"},
                    "message": {"thread_ts": "173.456", "ts": "173.789", "text": "Create request"},
                    "actions": [{"action_id": "open_request_intake", "value": json.dumps({"action": "open_request_intake"})}],
                }
            )
        )
        assert opened == {"status": "opened_modal", "clear_buttons": False}
        opened_view = next(body for method, _token, body in calls if method == "views.open")["view"]
        draft_id = json.loads(opened_view["private_metadata"])["draft_id"]

        blocked = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "request_intake_modal",
                        "private_metadata": json.dumps({"draft_id": draft_id}),
                        "state": {
                            "values": {
                                "request_intake_project": {"project_select": {"selected_option": {"value": str(config.repo_root)}}},
                                "request_intake_base_branch": {"base_branch_input": {"value": "main"}},
                                "request_intake_assistant_prompt": {"assistant_prompt_input": {"value": "   "}},
                            }
                        },
                    },
                }
            )
        )
        assert blocked == {
            "response_action": "errors",
            "errors": {
                "request_intake_assistant_prompt": "Assistant request is required before posting a draft to the thread.",
            },
        }

        async def generate_scenario():
            result = await app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "view_submission",
                    "user": {"id": "U123"},
                    "view": {
                        "callback_id": "request_intake_modal",
                        "private_metadata": json.dumps({"draft_id": draft_id}),
                        "state": {
                            "values": {
                                "request_intake_project": {"project_select": {"selected_option": {"value": str(config.repo_root)}}},
                                "request_intake_base_branch": {"base_branch_input": {"value": "main"}},
                                "request_intake_assistant_prompt": {"assistant_prompt_input": {"value": "Please tighten this request."}},
                            }
                        },
                    },
                }
            )
            current_loop = asyncio.get_running_loop()
            detached = [
                task
                for task in app.state.runtime._background_tasks
                if task.get_name().startswith("fs-kanban-slack-request-draft-") and not task.done() and task.get_loop() is current_loop
            ]
            if detached:
                await asyncio.gather(*detached)
            return result

        generated = asyncio.run(generate_scenario())
        assert generated == {"status": "success"}
        from assistant_agent_kanban.request_draft_store import RequestDraftStore

        draft = RequestDraftStore(config).load(draft_id)
        assert any(entry.role == "assistant" for entry in draft.transcript)
        assert draft.title == "Slack drafted title"
        assert draft.goal == "Slack drafted goal"

        submitted = asyncio.run(
            app.state.runtime.handle_slack_interactive_action(
                {
                    "type": "block_actions",
                    "user": {"id": "U123"},
                    "channel": {"id": "C123"},
                    "message": {
                        "ts": "msg-submit",
                        "text": "Assistant draft ready for review.",
                        "blocks": [
                            {"type": "section", "text": {"type": "mrkdwn", "text": "draft"}},
                            {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Submit final request"}}]},
                        ],
                    },
                    "actions": [{"action_id": "request_intake_submit", "value": json.dumps({"draft_id": draft_id})}],
                }
            )
        )

    assert submitted == {"status": "success"}
    tasks = KanbanScanner(config).scan()
    created = next(task for task in tasks if task.metadata.title == "Slack drafted title")
    assert created.metadata.slack.channel == "C123"
    assert created.metadata.slack.thread_ts == "173.456"
    assert (created.task_dir / "REQUEST-DRAFT.md").exists()
    assert not (config.request_drafts_dir / f"{draft_id}.json").exists()
    assert any(call[0] == "slack_upload_file_to_thread" for call in calls)
    assert any(call[0] == "chat.postMessage" and call[2] and call[2].get("thread_ts") == "173.456" for call in calls)


def test_runtime_start_auto_starts_slack_listener_when_configured(configured_paths):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.socket_mode_enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.app_token = "xapp-test"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    calls: list[str] = []

    async def fake_start_if_configured():
        calls.append("started")

    app.state.runtime.slack_runtime.start_if_configured = fake_start_if_configured  # type: ignore[method-assign]

    with TestClient(app):
        pass

    assert calls == ["started"]


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


def test_api_allows_setting_and_clearing_completed_group_override_for_done_tasks(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "done-group-override-api-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "done-group-override-api-task")

    with TestClient(app) as client:
        client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification", json={"completion_mode": "target-branch"})
        assert approve.status_code == 200
        assert approve.json()["state"] == TaskState.DONE.value

        set_group = client.put(f"/api/tasks/{completed.metadata.task_id}/completed-group", json={"group": "release/v3"})
        assert set_group.status_code == 200
        assert set_group.json()["completed_group_override"] == "release/v3"

        cleared = client.put(f"/api/tasks/{completed.metadata.task_id}/completed-group", json={"group": None})
        assert cleared.status_code == 200
        assert cleared.json()["completed_group_override"] is None

    done = scanner.find_task(completed.metadata.task_id)
    assert done.state == TaskState.DONE
    assert done.metadata.completed_group_override is None


def test_api_rejects_completed_group_override_updates_before_done(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "done-group-override-guard-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "done-group-override-guard-task")

    with TestClient(app) as client:
        response = client.put(f"/api/tasks/{completed.metadata.task_id}/completed-group", json={"group": "release/v9"})

    assert response.status_code == 409
    assert response.json()["detail"] == "completed group override can only be updated for done tasks"


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


def test_api_runs_reviewer_qa_and_exposes_saved_transcript(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.runtime.role_backends.reviewer = "codex"
    create_request_task(config, "reviewer-qa-api-task")
    reviewer_adapter = FakeAdapter(["## Answer\n\nThe current naming is acceptable, but the helper copy should still be updated."], session_ids=["ses_reviewer_qa"], total_tokens=[17])
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), reviewer_adapter, adapter_registry={"codex": reviewer_adapter})
    _, completed = _task_ready_for_completed_reviews(config, "reviewer-qa-api-task")

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reviewer-qa",
            json={"question": "Can we keep the existing label?"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["qa_path"] == "REVIEWER-QA-001.md"
        assert payload["session_id"] == "ses_reviewer_qa"
        assert "helper copy should still be updated" in payload["answer"]

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["human_review"]["reviewer_qa_path"] == "REVIEWER-QA-001.md"
        assert "Can we keep the existing label?" in detail.json()["human_review"]["reviewer_qa_markdown"]

    task = KanbanScanner(config).find_task(completed.metadata.task_id)
    assert (task.task_dir / "REVIEWER-QA-001.md").exists()
    assert reviewer_adapter.run_calls[0]["show_thinking"] is True


def test_api_prefers_existing_reviewer_qa_artifact_over_stale_metadata_path(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "reviewer-qa-stale-path-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "reviewer-qa-stale-path-task")
    task = scanner.find_task(completed.metadata.task_id)
    (task.task_dir / "REVIEWER-QA-001.md").write_text("# Reviewer Q&A\n\n## Question 1\nWhat changed?\n\n## Answer 1\nThe helper copy changed.\n")
    task.metadata.review.qa_path = "REVIEWER-QA-002.md"
    scanner.metadata_store.save(task.task_dir, task.metadata)

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["human_review"]["reviewer_qa_path"] == "REVIEWER-QA-001.md"
        assert "The helper copy changed." in detail.json()["human_review"]["reviewer_qa_markdown"]


def test_api_uploads_human_review_note_attachments(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-note-attachment-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-note-attachment-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        upload = client.post(
            f"/api/tasks/{completed.metadata.task_id}/attachments?artifact=HUMAN-VERIFY-001.md",
            files={"file": ("review-diagram.png", b"pngdata", "image/png")},
        )
        assert upload.status_code == 200
        payload = upload.json()
        assert payload["filename"].endswith(".png")
        assert payload["relative_path"].startswith("_attachments/")

        save_note = client.put(
            f"/api/tasks/{completed.metadata.task_id}/human-review-note",
            json={"content": f"![diagram]({payload['relative_path']})"},
        )
        assert save_note.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["human_review"]["note_markdown"] == f"![diagram]({payload['relative_path']})"

        download = client.get(payload["url"])
        assert download.status_code == 200
        assert download.content == b"pngdata"
        assert download.headers["content-type"] == "image/png"


def test_api_extracts_embedded_human_review_note_images_to_attachments(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-note-embedded-image-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-note-embedded-image-task")

    embedded = base64.b64encode(b"pngdata").decode()
    markdown = f"![diagram](data:image/png;base64,{embedded})"

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        save_note = client.put(
            f"/api/tasks/{completed.metadata.task_id}/human-review-note",
            json={"content": markdown},
        )
        assert save_note.status_code == 200
        assert save_note.json()["content"].startswith("![diagram](_attachments/")

    task = KanbanScanner(config).find_task(completed.metadata.task_id)
    assert task.metadata.human_verification.note_markdown.startswith("![diagram](_attachments/")
    assert task.metadata.human_verification.note_markdown.endswith(")")
    attachments = list((task.task_dir / "_attachments").glob("*.png"))
    assert len(attachments) == 1
    assert attachments[0].read_bytes() == b"pngdata"


def test_api_reject_verification_normalizes_embedded_note_images(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-note-reject-image-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-review-note-reject-image-task")

    embedded = base64.b64encode(b"pngdata").decode()

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        reject = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reject-verification",
            json={"note": f"![diagram](data:image/png;base64,{embedded})"},
        )
        assert reject.status_code == 200

    task = scanner.find_task(completed.metadata.task_id)
    assert task.metadata.human_verification.note_markdown.startswith("![diagram](_attachments/")
    attachments = list((task.task_dir / "_attachments").glob("*.png"))
    assert len(attachments) == 1
    assert attachments[0].read_bytes() == b"pngdata"


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


def test_api_updates_changed_file_viewed_state(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-review-viewed-file-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "human-review-viewed-file-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        assert detail.status_code == 200
        changed_files = detail.json()["changed_files"]
        assert len(changed_files) == 1
        assert changed_files[0]["viewed"] is False
        changed_file_id = changed_files[0]["id"]

        mark_viewed = client.post(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_file_id}/viewed",
            json={"viewed": True},
        )
        assert mark_viewed.status_code == 200
        assert mark_viewed.json()["viewed"] is True

        refreshed_detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        assert refreshed_detail.status_code == 200
        assert refreshed_detail.json()["changed_files"][0]["viewed"] is True
        assert refreshed_detail.json()["changed_files"][0]["id"] == changed_file_id

        diff = client.get(f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_file_id}")
        assert diff.status_code == 200
        assert diff.json()["summary"]["viewed"] is True
        assert diff.json()["summary"]["id"] == changed_file_id

        clear_viewed = client.post(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_file_id}/viewed",
            json={"viewed": False},
        )
        assert clear_viewed.status_code == 200
        assert clear_viewed.json()["viewed"] is False

        cleared_detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        assert cleared_detail.status_code == 200
        assert cleared_detail.json()["changed_files"][0]["viewed"] is False
        assert cleared_detail.json()["changed_files"][0]["id"] == changed_file_id


def test_api_blocks_changed_file_viewed_updates_after_done(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "done-viewed-file-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    _, completed = _task_ready_for_completed_reviews(config, "done-viewed-file-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200
        approve = client.post(f"/api/tasks/{completed.metadata.task_id}/approve-verification")
        assert approve.status_code == 200

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}?include_changed_files=true")
        assert detail.status_code == 200
        changed_files = detail.json()["changed_files"]
        assert len(changed_files) == 1
        assert changed_files[0]["viewed"] is False

        response = client.post(
            f"/api/tasks/{completed.metadata.task_id}/changed-files/{changed_files[0]['id']}/viewed",
            json={"viewed": True},
        )
        assert response.status_code == 409
        assert "only available during human verification" in response.json()["detail"]


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
        assert "이 요청으로 추가하거나 변경한 코드의 모든 케이스를 테스트해야 하며, 그 변경 범위의 테스트 커버리지는 100%여야 한다." in request_markdown
        assert "저장소 전체 커버리지 100%를 요구하는 뜻은 아니며, 전체 테스트 suite 는 작업 범위와 별개로 수행에 성공해야 한다." in request_markdown
        assert "Users can still sign in" in request_markdown
        assert f"repo_root: {target_repo.resolve()}" in request_markdown
        assert "base_branch: develop" in request_markdown
        assert "language: ko" in request_markdown


def test_api_creates_default_scope_sections_when_blank(configured_paths, tmp_path):
    config, _, _ = configured_paths
    config.target_repo_docs_root = "records/kanban-docs"
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
    assert "plan_auto_approve: true" in request_markdown
    assert "## Scope" in request_markdown
    assert f"Limit code changes to `{target_repo}`." in request_markdown
    assert "## Out of Scope" in request_markdown
    assert f"Do not modify files outside `{target_repo}`." in request_markdown
    assert "Do not modify files under `records/kanban-docs` unless the request explicitly asks for it." in request_markdown
    assert "## Acceptance Criteria" in request_markdown
    assert "Add tests for every case introduced by the code added or changed for this request, and keep test coverage for that changed scope at 100%." in request_markdown
    assert "This does not require 100% coverage across the entire repository; the full test suite must still pass separately from the changed-scope coverage target." in request_markdown


def test_api_extracts_embedded_request_goal_images_to_attachments(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    embedded = base64.b64encode(b"pngdata").decode()

    with TestClient(app) as client:
        response = client.post(
            "/api/requests",
            json={
                "title": "Request image attachment task",
                "goal": f"Need this diagram.\n\n![diagram](data:image/png;base64,{embedded})",
                "target_repo": str(target_repo),
                "base_branch": "main",
            },
        )

    assert response.status_code == 200
    task_dir = _locate_task_dir(config, Path(response.json()["task_path"]).name)
    request_markdown = (task_dir / "REQUEST.md").read_text()
    assert "![diagram](_attachments/" in request_markdown
    attachments = list((task_dir / "_attachments").glob("*.png"))
    assert len(attachments) == 1
    assert attachments[0].read_bytes() == b"pngdata"


def test_api_finalizes_uploaded_request_goal_images_to_task_attachments(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        upload = client.post(
            "/api/request-uploads?upload_token=request-upload-token",
            files={"file": ("diagram.png", b"pngdata", "image/png")},
        )
        assert upload.status_code == 200
        upload_payload = upload.json()

        response = client.post(
            "/api/requests",
            json={
                "title": "Request uploaded image attachment task",
                "goal": f"Need this diagram.\n\n![diagram]({upload_payload['url']})",
                "request_upload_token": "request-upload-token",
                "target_repo": str(target_repo),
                "base_branch": "main",
            },
        )

    assert response.status_code == 200
    task_dir = _locate_task_dir(config, Path(response.json()["task_path"]).name)
    request_markdown = (task_dir / "REQUEST.md").read_text()
    assert "![diagram](_attachments/" in request_markdown
    attachments = list((task_dir / "_attachments").glob("*.png"))
    assert len(attachments) == 1
    assert attachments[0].read_bytes() == b"pngdata"
    assert not (config.request_uploads_dir / "request-upload-token").exists()


def test_ui_injects_configured_target_repo_docs_root_into_request_defaults(configured_paths):
    config, _, _ = configured_paths
    config.target_repo_docs_root = "records/kanban-docs"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "records/kanban-docs" in response.text
    assert "__TARGET_REPO_DOCS_ROOT__" not in response.text


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
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        get_response = client.get("/api/settings/models")
        assert get_response.status_code == 200
        assert get_response.json()["language"] == "EN"
        assert get_response.json()["theme"] == "light"
        assert get_response.json()["coding_assistant"] == "opencode"
        assert get_response.json()["role_backends"] == {
            "planner": None,
            "request_draft": None,
            "plan_approval": None,
            "implementer": None,
            "reviewer": None,
            "commit": None,
        }
        assert get_response.json()["effective_role_backends"] == {
            "planner": "opencode",
            "request_draft": "opencode",
            "plan_approval": "opencode",
            "implementer": "opencode",
            "reviewer": "opencode",
            "commit": "opencode",
        }
        assert get_response.json()["worker_live_logs_enabled"] is True
        assert get_response.json()["available_assistants"] == [
            {"value": "opencode", "label": "OpenCode"},
            {"value": "codex", "label": "Codex CLI"},
            {"value": "gemini", "label": "Gemini CLI"},
        ]
        assert get_response.json()["planner_model"] is None
        assert get_response.json()["request_draft_model"] is None
        assert get_response.json()["planner_session_token_budget"] == 250
        assert get_response.json()["planner_agent_count"] == 1
        assert get_response.json()["implementer_session_token_budget"] == 250
        assert get_response.json()["implementer_agent_count"] == 1
        assert get_response.json()["reviewer_session_token_budget"] == 250
        assert get_response.json()["reviewer_agent_count"] == 1
        assert get_response.json()["commit_session_token_budget"] == 250
        assert get_response.json()["repo_discovery_root"] == str(config.repo_discovery.root)
        assert get_response.json()["repo_discovery_max_depth"] == config.repo_discovery.max_depth
        assert get_response.json()["slack_enabled"] is False
        assert get_response.json()["slack_socket_mode_enabled"] is True
        assert get_response.json()["slack_default_channel"] is None
        assert get_response.json()["slack_app_mention_enabled"] is False
        assert get_response.json()["slack_bot_token_configured"] is False
        assert get_response.json()["slack_bot_token_masked"] is None
        assert get_response.json()["slack_app_token_configured"] is False
        assert get_response.json()["slack_app_token_masked"] is None
        assert get_response.json()["config_path"] == str(local_config_path.resolve())
        assert get_response.json()["available_models"] == ["gpt-5", "o3-mini"]
        assert get_response.json()["available_models_by_backend"]["opencode"] == ["gpt-5", "o3-mini"]
        assert "gpt-5.4" in get_response.json()["available_models_by_backend"]["codex"]
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
                "role_backends": {
                    "request_draft": "gemini",
                    "implementer": "codex",
                    "commit": "codex",
                },
                "worker_live_logs_enabled": False,
                "planner_model": "gpt-5",
                "request_draft_model": "gemini-2.5-flash",
                "planner_session_token_budget": 210,
                "planner_agent_count": 2,
                "implementer_model": " gpt-5.4 ",
                "implementer_session_token_budget": 230,
                "implementer_agent_count": 3,
                "reviewer_model": "",
                "reviewer_session_token_budget": 190,
                "reviewer_agent_count": 4,
                "commit_model": "gpt-5",
                "commit_session_token_budget": 250,
                "repo_discovery_root": "../",
                "repo_discovery_max_depth": 4,
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_bot_token": "xoxb-test-12345678",
                "slack_app_token": "xapp-test-87654321",
                "slack_default_channel": "#agent-alerts",
                "slack_app_mention_enabled": True,
            },
        )

    assert put_response.status_code == 200
    payload = put_response.json()
    assert payload["saved"] is True
    assert payload["language"] == "KO"
    assert payload["coding_assistant"] == "opencode"
    assert payload["role_backends"] == {
        "planner": None,
        "request_draft": "gemini",
        "plan_approval": None,
        "implementer": "codex",
        "reviewer": None,
        "commit": "codex",
    }
    assert payload["effective_role_backends"] == {
        "planner": "opencode",
        "request_draft": "gemini",
        "plan_approval": "opencode",
        "implementer": "codex",
        "reviewer": "opencode",
        "commit": "codex",
    }
    assert payload["worker_live_logs_enabled"] is False
    assert payload["planner_model"] == "gpt-5"
    assert payload["request_draft_model"] == "gemini-2.5-flash"
    assert payload["planner_session_token_budget"] == 210
    assert payload["planner_agent_count"] == 2
    assert payload["implementer_model"] == "gpt-5.4"
    assert payload["implementer_session_token_budget"] == 230
    assert payload["implementer_agent_count"] == 3
    assert payload["reviewer_model"] is None
    assert payload["reviewer_session_token_budget"] == 190
    assert payload["reviewer_agent_count"] == 4
    assert payload["commit_model"] == "gpt-5"
    assert payload["commit_session_token_budget"] == 250
    assert payload["repo_discovery_root"] == "../"
    assert payload["repo_discovery_max_depth"] == 4
    assert payload["slack_enabled"] is True
    assert payload["slack_socket_mode_enabled"] is True
    assert payload["slack_default_channel"] is None
    assert payload["slack_default_channel_display"] is None
    assert payload["slack_app_mention_enabled"] is True
    assert payload["slack_bot_token_configured"] is True
    assert payload["slack_bot_token_masked"] == "••••••••••••••5678"
    assert payload["slack_app_token_configured"] is True
    assert payload["slack_app_token_masked"] == "••••••••••••••4321"
    assert app.state.runtime.config.opencode.planner_model == "gpt-5"
    assert app.state.runtime.config.runtime.language == "KO"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.runtime.role_backends.request_draft == "gemini"
    assert app.state.runtime.config.runtime.role_backends.implementer == "codex"
    assert app.state.runtime.config.runtime.role_backends.commit == "codex"
    assert app.state.runtime.config.opencode.worker_live_logs_enabled is False
    assert app.state.runtime.config.opencode.planner_session_token_budget == 210000
    assert app.state.runtime.config.runtime.planner_agent_count == 2
    assert app.state.runtime.config.gemini.request_draft_model == "gemini-2.5-flash"
    assert app.state.runtime.config.codex.implementer_model == "gpt-5.4"
    assert app.state.runtime.config.codex.implementer_session_token_budget == 230000
    assert app.state.runtime.config.runtime.implementer_agent_count == 3
    assert app.state.runtime.config.opencode.reviewer_model is None
    assert app.state.runtime.config.opencode.reviewer_session_token_budget == 190000
    assert app.state.runtime.config.runtime.reviewer_agent_count == 4
    assert app.state.runtime.config.repo_discovery.root == "../"
    assert app.state.runtime.config.repo_discovery.max_depth == 4
    assert app.state.runtime.config.slack.enabled is True
    assert app.state.runtime.config.slack.socket_mode_enabled is True
    assert app.state.runtime.config.slack.bot_token == "xoxb-test-12345678"
    assert app.state.runtime.config.slack.app_token == "xapp-test-87654321"
    assert app.state.runtime.config.slack.default_channel is None
    assert app.state.runtime.config.slack.default_channel_display is None
    assert app.state.runtime.config.slack.app_mention_enabled is True
    assert load_config(config_path).codex.commit_model == "gpt-5"
    assert load_config(config_path).codex.commit_session_token_budget == 250000
    assert load_config(config_path).runtime.role_backends.implementer == "codex"
    assert load_config(config_path).runtime.role_backends.commit == "codex"
    assert load_config(config_path).repo_discovery.root == "../"
    assert load_config(config_path).repo_discovery.max_depth == 4
    assert load_config(config_path).slack.bot_token == "xoxb-test-12345678"
    assert load_config(config_path).slack.app_token == "xapp-test-87654321"
    assert load_config(config_path).slack.default_channel is None
    assert load_config(config_path).slack.default_channel_display is None


def test_api_settings_can_clear_slack_tokens(configured_paths):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-existing-1234"
    config.slack.app_token = "xapp-existing-5678"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "slack_bot_token": "",
                "slack_app_token": "",
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_app_mention_enabled": False,
            },
        )

    assert response.status_code == 200
    assert response.json()["slack_bot_token_configured"] is False
    assert response.json()["slack_bot_token_masked"] is None
    assert response.json()["slack_app_token_configured"] is False
    assert response.json()["slack_app_token_masked"] is None
    assert app.state.runtime.config.slack.bot_token is None
    assert app.state.runtime.config.slack.app_token is None


def test_api_runs_slack_settings_test_with_posted_values(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    monkeypatch.setattr(
        "assistant_agent_kanban.api.routes.run_slack_settings_test",
        lambda slack_config, *, uses_posted_values: type(
            "SlackResult",
            (),
            {
                "to_payload": lambda self: {
                    "ok": True,
                    "summary": f"tested {slack_config.default_channel}",
                    "checks": [
                        {"name": "enabled", "ok": slack_config.enabled, "message": "enabled"},
                        {"name": "bot_token", "ok": slack_config.bot_token == "xoxb-posted", "message": "bot"},
                    ],
                    "uses_posted_values": uses_posted_values,
                    "receive_verification_mode": "readiness",
                    "resolved_channel_id": "C123",
                    "resolved_channel_display": "#agent-alerts",
                }
            },
        )(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/settings/slack-test",
            json={
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_bot_token": "xoxb-posted",
                "slack_app_token": "xapp-posted",
                "slack_default_channel": "#agent-alerts",
                "slack_app_mention_enabled": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Effective channel updated" in response.json()["summary"]
    assert response.json()["uses_posted_values"] is True
    assert config.slack.default_channel == "C123"
    assert config.slack.default_channel_display == "#agent-alerts"
    assert config.slack.enabled is False
    reloaded = load_config(config.config_path_for_persistence())
    assert reloaded.slack.default_channel == "C123"
    assert reloaded.slack.default_channel_display == "#agent-alerts"


def test_api_preserves_saved_slack_tokens_when_put_payload_omits_them(configured_paths):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-existing-1234"
    config.slack.app_token = "xapp-existing-5678"
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_default_channel": "#agent-alerts",
                "slack_app_mention_enabled": True,
            },
        )

    assert response.status_code == 200
    assert app.state.runtime.config.slack.bot_token == "xoxb-existing-1234"
    assert app.state.runtime.config.slack.app_token == "xapp-existing-5678"


def test_api_slack_settings_test_reports_failure(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    monkeypatch.setattr(
        "assistant_agent_kanban.api.routes.run_slack_settings_test",
        lambda slack_config, *, uses_posted_values: type(
            "SlackResult",
            (),
            {
                "to_payload": lambda self: {
                    "ok": False,
                    "summary": "missing channel",
                    "checks": [{"name": "send_test", "ok": False, "message": "channel required"}],
                    "uses_posted_values": uses_posted_values,
                    "receive_verification_mode": "readiness",
                }
            },
        )(),
    )

    with TestClient(app) as client:
        response = client.post("/api/settings/slack-test", json={"slack_enabled": True, "slack_bot_token": "xoxb-posted"})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["checks"][0]["message"] == "channel required"


def test_api_starts_and_reads_slack_receive_test(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    async def fake_start_receive_test():
        return {"listener_enabled": True, "listener_connected": False, "listener_last_error": None, "receive_test": {"status": "pending", "token": "abc123"}}

    app.state.runtime.slack_runtime.start_receive_test = fake_start_receive_test  # type: ignore[method-assign]
    app.state.runtime.slack_runtime.snapshot = lambda: {"listener_enabled": True, "listener_connected": True, "listener_last_error": None, "receive_test": {"status": "received", "token": "abc123"}}  # type: ignore[method-assign]

    with TestClient(app) as client:
        start_response = client.post("/api/settings/slack-receive-test/start", json={})
        status_response = client.get("/api/settings/slack-receive-test")

    assert start_response.status_code == 200
    assert start_response.json()["receive_test"]["token"] == "abc123"
    assert status_response.status_code == 200
    assert status_response.json()["receive_test"]["status"] == "received"


def test_settings_snapshot_refreshes_only_selected_backend(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    calls: list[tuple[str, bool]] = []

    class Snapshot:
        def __init__(self, backend: str):
            self.backend = backend

    def fake_get(backend, *, refresh=False):
        calls.append((backend, refresh))
        return Snapshot(backend)

    app.state.runtime.model_registry.get = fake_get  # type: ignore[method-assign]

    asyncio.run(_resolve_settings_snapshots(app.state.runtime, refresh=True, assistant="opencode"))

    assert ("opencode", True) in calls
    assert all(refresh is False for backend, refresh in calls if backend != "opencode")


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
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

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
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

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

    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{staged_task.metadata.task_id}")

    assert response.status_code == 200
    payload = response.json()
    stage_timing = payload["stage_timing"]
    assert len(stage_timing["summaries"]) == 11
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
                "worker_live_logs_enabled": False,
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
                "slack_enabled": True,
                "slack_socket_mode_enabled": True,
                "slack_bot_token": "xoxb-local-persist",
                "slack_app_token": "xapp-local-persist",
                "slack_default_channel": "C123",
                "slack_app_mention_enabled": True,
            },
        )

        assert response.status_code == 200
        assert default_local_path.exists()
        persisted = load_config(default_base_path)
        assert persisted.opencode.planner_model == "planner-x"
        assert persisted.runtime.language == "KO"
        assert persisted.runtime.theme == "dark"
        assert persisted.runtime.coding_assistant == "opencode"
        assert persisted.opencode.worker_live_logs_enabled is False
        assert persisted.opencode.planner_session_token_budget == 180000
        assert persisted.runtime.planner_agent_count == 2
        assert persisted.opencode.reviewer_model == "reviewer-y"
        assert persisted.opencode.reviewer_session_token_budget == 220000
        assert persisted.runtime.reviewer_agent_count == 3
        assert persisted.repo_discovery.root == "/tmp/scan-root"
        assert persisted.repo_discovery.max_depth == 3
        assert persisted.slack.enabled is True
        assert persisted.slack.socket_mode_enabled is True
        assert persisted.slack.bot_token == "xoxb-local-persist"
        assert persisted.slack.app_token == "xapp-local-persist"
        assert persisted.slack.default_channel is None
        assert persisted.slack.default_channel_display is None
        assert persisted.slack.app_mention_enabled is True
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
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "planner_model": "planner-x",
                "theme": "dark",
                "coding_assistant": "opencode",
                "worker_live_logs_enabled": False,
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
    assert response.json()["worker_live_logs_enabled"] is False
    assert response.json()["repo_discovery_root"] == "../custom-root"
    assert response.json()["repo_discovery_max_depth"] == 5
    assert response.json()["planner_agent_count"] == 5
    assert response.json()["implementer_agent_count"] == 2
    assert app.state.runtime.config.repo_discovery.root == "../custom-root"
    assert app.state.runtime.config.runtime.language == "KO"
    assert app.state.runtime.config.runtime.theme == "dark"
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"
    assert app.state.runtime.config.opencode.worker_live_logs_enabled is False
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
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["openai/gpt-5.4"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

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
    assert response.json()["detail"] == {
        "code": "settings.model_not_discovered",
        "field": "planner_model",
    }


def test_api_rejects_unknown_codex_model_on_save(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

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
    assert response.json()["detail"] == {
        "code": "settings.model_not_discovered",
        "field": "planner_model",
    }


def test_api_refresh_can_preview_codex_models_without_switching_runtime(configured_paths):
    config, _, _ = configured_paths
    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models?refresh=true&assistant=codex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "codex"
    assert "gpt-5.4" in payload["available_models"]
    assert payload["planner_model"] == config.codex.planner_model
    assert app.state.runtime.config.runtime.coding_assistant == "opencode"


def test_api_rejects_unavailable_role_backend_on_save(configured_paths):
    config, _, _ = configured_paths

    class UnavailableCodexAdapter(FakeAdapter):
        def availability_error(self, *, config, backend):
            return "binary not found on PATH: codex"

    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter, codex_adapter=UnavailableCodexAdapter(["codex"]))
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "coding_assistant": "opencode",
                "role_backends": {"implementer": "codex"},
                "planner_session_token_budget": 250,
                "implementer_session_token_budget": 250,
                "reviewer_session_token_budget": 250,
                "commit_session_token_budget": 250,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "settings.backend_unavailable",
        "field": "role_backends.implementer",
        "message": "binary not found on PATH: codex",
    }


def test_api_settings_only_lists_startup_available_assistants(configured_paths):
    config, _, _ = configured_paths

    class UnavailableCodexAdapter(FakeAdapter):
        def availability_error(self, *, config, backend):
            return "binary not found on PATH: codex"

    planner_adapter = FakeAdapter(["plan"], discovery_responses=[["gpt-5", "o3-mini"]])
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter, codex_adapter=UnavailableCodexAdapter(["codex"]))
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models")

    assert response.status_code == 200
    assert response.json()["available_assistants"] == [
        {"value": "opencode", "label": "OpenCode"},
        {"value": "gemini", "label": "Gemini CLI"},
    ]


def test_api_settings_without_assistant_query_returns_persisted_backend(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "codex"
    config.codex.planner_model = "gpt-5.4"
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        response = client.get("/api/settings/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coding_assistant"] == "codex"
    assert payload["planner_model"] == "gpt-5.4"


def test_api_save_materializes_runtime_agents_immediately(configured_paths):
    config, _, _ = configured_paths
    adapter_registry = _settings_adapter_registry()
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)
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
        assert "If the prompt says this is a handshake/session-prep step, return only a short greeting." in planner_agent_path.read_text()
        assert "Do not delegate the final file edits" in implementer_agent_path.read_text()
        assert "If the prompt says this is a final review-artifact step, return only the requested strict JSON object." in reviewer_agent_path.read_text()
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
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

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


def test_api_refreshes_models_without_refreshing_cached_availability(configured_paths):
    config, _, _ = configured_paths

    class AvailabilityTrackingAdapter(FakeAdapter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.availability_calls = 0

        def availability_error(self, *, config, backend):
            self.availability_calls += 1
            return None

    planner_adapter = AvailabilityTrackingAdapter(
        ["plan"],
        discovery_responses=[["gpt-5", "claude-3.7-sonnet"], ["gpt-5", "o3-mini"]],
    )
    adapter_registry = _settings_adapter_registry(opencode_adapter=planner_adapter)
    app = create_app(config, planner_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]), adapter_registry=adapter_registry)

    with TestClient(app) as client:
        initial = client.get("/api/settings/models")
        assert initial.status_code == 200
        refreshed = client.get("/api/settings/models?refresh=true")

    assert refreshed.status_code == 200
    assert planner_adapter.discovery_calls == [False, True]
    assert planner_adapter.availability_calls == 1


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
    assert 'class="field compact span-full field-checkbox"' in response.text
    assert 'class="field-checkbox-row"' in response.text
    assert 'id="plan_auto_approve"' in response.text
    assert 'for="plan_auto_approve"' in response.text
    assert 'id="plan_auto_approve" name="plan_auto_approve" type="checkbox" value="true" checked' in response.text
    assert '.field-checkbox-row input[type="checkbox"] { width: auto;' in response.text
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
    assert "planner_backend" in response.text
    assert "plan_approval_backend" in response.text
    assert "implementer_backend" in response.text
    assert "reviewer_backend" in response.text
    assert "commit_backend" in response.text
    assert "planner_model" in response.text
    assert "runtime_language" in response.text
    assert "runtime_theme" in response.text
    assert "runtime_coding_assistant" in response.text
    assert "worker_live_logs_enabled" in response.text
    assert "slack_enabled" in response.text
    assert "slack_socket_mode_enabled" in response.text
    assert "slack_bot_token" in response.text
    assert "slack_app_token" in response.text
    assert "slack_default_channel" in response.text
    assert "slack_app_mention_enabled" in response.text
    assert "test-slack-settings" in response.text
    assert "start-slack-receive-test" in response.text
    assert "copy-slack-receive-test" in response.text
    assert "clear-slack-bot-token" in response.text
    assert "clear-slack-app-token" in response.text
    assert "settings-slack-test-status" in response.text
    assert "settings-slack-receive-test-status" in response.text
    assert "slack_enabled: slackEnabledInput.checked" in response.text
    assert "slack_socket_mode_enabled: slackSocketModeEnabledInput.checked" in response.text
    assert "slack_app_mention_enabled: slackAppMentionEnabledInput.checked" in response.text
    assert "await loadModelSettings(false);" in response.text
    assert "await navigator.clipboard.writeText(lastSlackReceiveInstruction);" in response.text
    assert "THINK LOG" in response.text
    assert "DEFAULT LOG" in response.text
    assert "OpenCode LogMode" not in response.text
    assert "function applyRuntimeTheme(theme)" in response.text
    assert "applyRuntimeTheme(initialRuntimeTheme);" in response.text
    assert "let requestModalFocusToken = 0;" in response.text
    assert "function focusRequestTitleWhenReady(token)" in response.text
    assert "if (token !== requestModalFocusToken || modal.hidden) return;" in response.text
    assert "requestTitleInput.focus();" in response.text
    assert "setRequestGoalEditorContent('', { initialize: false });" in response.text
    assert "const editor = initialize ? ensureRequestGoalEditor() : requestGoalEditor;" in response.text
    assert "const settingsTranslations = {" in response.text
    assert "applyRuntimeSettingsTranslations();" in response.text
    assert "const taskTranslations = {" in response.text
    assert "applyTaskTranslations();" in response.text
    assert "runtimeLanguageInput.addEventListener('change', () => { applyRuntimeSettingsTranslations(); applyRequestTranslations(); applyHumanReviewTranslations(); applyTaskTranslations(); if (activeTaskDetail) renderTaskOverview(activeTaskDetail); refreshRequestDerivedText(); });" in response.text
    assert 'class="settings-sections"' in response.text
    assert 'id="settings-basics-heading"' in response.text
    assert 'id="settings-agents-heading"' in response.text
    assert 'class="settings-grid-basic-top"' in response.text
    assert 'class="settings-grid-basic-bottom"' in response.text
    assert ".settings-grid-basic { display: grid; gap: 14px; }" in response.text
    assert ".settings-grid-basic-top { display: grid; grid-template-columns: repeat(2, minmax(240px, 1fr)); gap: 14px; }" in response.text
    assert "@media (max-width: 960px) {" in response.text
    assert ".settings-toolbar-actions { justify-content: flex-start; margin-left: 0; }" in response.text
    assert ".settings-toolbar-field { display: inline-grid; grid-template-columns: auto auto; gap: 8px; align-items: center; justify-content: start; }" in response.text
    assert 'id="settings-live-logs-field"' in response.text
    assert 'class="settings-role-inline"' in response.text
    assert 'class="settings-role-inline settings-role-inline-no-agents"' in response.text
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
    assert "request_draft_backend" in response.text
    assert "request_draft_model" in response.text
    assert "assistant-agent-kanban.request-composer-draft" in response.text
    assert "requestComposerDraftSyncDelayMs" in response.text
    assert "request-drafts-shell" in response.text
    assert "request-drafts-grid" in response.text
    assert "function restoreRequestComposerDraftState()" in response.text
    assert "function ensureRequestComposerDraft(options = {})" in response.text
    assert "function loadRequestDrafts()" in response.text
    assert "function renderRequestDrafts()" in response.text
    assert "function serializeRequestDraftArtifactMarkdown()" in response.text
    assert "Assistant updates merge into the form automatically" in response.text
    assert "function applyRequestDraftFieldUpdates(fieldUpdates, options = {})" in response.text
    assert "data-request-draft-apply=" not in response.text
    assert "repo_discovery_root" in response.text
    assert "repo_discovery_max_depth" in response.text
    assert "readNumericSettingInput" in response.text
    assert "planner-model-options" in response.text
    assert "plan-approval-model-options" in response.text
    assert "implementer-model-options" in response.text
    assert "reviewer-model-options" in response.text
    assert "commit-model-options" in response.text
    assert "const previousRoleSelections = Object.fromEntries(roleSettingConfigs.map(({ role, backendInput }) => [role, backendInput.value || 'default']));" in response.text
    assert "backendInput.value = roleOptions.some((item) => item.value === nextValue) ? nextValue : 'default';" in response.text
    assert "Refresh models" in response.text
    assert "Save settings" in response.text
    assert "Slack credentials" in response.text
    assert "Test Slack" in response.text
    assert "Start receive test" in response.text
    assert "/api/settings/slack-test" in response.text
    assert "/api/settings/slack-receive-test/start" in response.text
    assert "window.location.reload();" not in response.text
    assert "await loadModelSettings(true);" not in response.text
    assert "Repo discovery root" in response.text
    assert "Repo discovery depth" in response.text
    assert "OpenCode LogMode" not in response.text
    assert "Log display mode." not in response.text
    assert "This mode uses more tokens." not in response.text
    assert "loaded ·" in response.text
    assert "task-viewer-host" in response.text
    assert "Approve plan" in response.text
    assert "toastui-editor" in response.text
    assert 'id="request-goal-editor-host"' in response.text
    assert 'id="request-goal-editor-fallback"' in response.text
    assert "buildScopeDefaults" in response.text
    assert "buildOutOfScopeDefaults" in response.text
    assert "const requestTranslations = {" in response.text
    assert "const humanReviewTranslations = {" in response.text
    assert "applyRequestTranslations();" in response.text
    assert "['plan-approving', 'waiting-check-plans', 'completed-reviews', 'human-verifying', 'done'].includes(metadata?.state) && files.includes('PLAN.md')" in response.text
    assert "task-human-review-panel" in response.text
    assert "task-reviewer-qa-panel" in response.text
    assert 'class="reviewer-qa-log"' in response.text
    assert 'class="reviewer-qa-composer"' in response.text
    assert 'id="request-composer-tab-assistant" class="active"' in response.text
    assert 'id="request-composer-panel-fields" class="request-composer-panel" role="tabpanel" aria-labelledby="request-composer-tab-fields" hidden' in response.text
    assert "Assistant updates merge into the form automatically" in response.text
    assert 'id="request-draft-starters" class="request-draft-starters"' not in response.text
    assert 'data-request-draft-starter="scope"' not in response.text
    assert 'id="request-draft-composer" class="request-draft-composer"' in response.text
    assert 'id="request-draft-image-input" type="file" accept="image/*" hidden' in response.text
    assert 'id="attach-request-draft-image" class="ghost-button request-draft-attach"' in response.text
    assert 'id="request-draft-attachment-status" class="muted request-draft-attachment-status"' in response.text
    assert "Please help turn these notes into a crisp request. Tighten the goal, highlight missing constraints, and suggest clearer acceptance criteria." in response.text
    assert 'class="editor-textarea reviewer-qa-input"' in response.text
    assert 'class="reviewer-qa-send"' in response.text
    assert "function appendReviewerQaWorkerLogPayload(payload)" in response.text
    assert "let reviewerQaQuestionInFlight = false;" in response.text
    assert "Reviewer is answering…" in response.text
    assert "save-human-review-note" in response.text
    assert "request-changes-button" in response.text
    assert "approve-human-review-button" in response.text
    assert "/api/tasks/${activeTaskId}/human-review-note" in response.text
    assert "/api/tasks/${activeTaskId}/reviewer-qa" in response.text
    assert "/api/retrospectives/inspect" in response.text
    assert "/api/retrospectives/create" in response.text
    assert 'id="task-tab-reviewer-qa"' in response.text
    assert 'id="task-panel-reviewer-qa"' in response.text
    assert 'id="task-tab-review-note"' in response.text
    assert 'id="task-panel-review-note"' in response.text
    assert "reviewerQaVisible: state === 'completed-reviews' || state === 'human-verifying'" in response.text
    assert "reviewNoteVisible: state === 'human-verifying'" in response.text
    assert "function parseReviewerQaTranscript(source)" in response.text
    assert "function updateReviewerQaLiveRefresh()" in response.text
    assert "if (activeTaskTab === 'reviewer-qa' && appendReviewerQaWorkerLogPayload(payload)) return;" in response.text
    assert "const previousTab = activeTaskTab;" in response.text
    assert "if (tab === 'reviewer-qa' && previousTab !== 'reviewer-qa')" in response.text
    assert 'class="diff-grid"' in response.text
    assert 'class="diff-row"' in response.text
    assert 'class="diff-cell ${line.kind}"' in response.text
    assert 'class="diff-unified"' in response.text
    assert "translateRequest('validationGoal')" in response.text
    assert "function ensureRequestGoalEditor()" in response.text
    assert "syncRequestGoalField();" in response.text
    assert "body[data-theme=\"dark\"] .request-goal-editor-shell .toastui-editor-defaultUI" in response.text
    assert "body[data-theme=\"dark\"] .request-goal-editor-shell .toastui-editor-toolbar" in response.text
    assert "background: var(--review-toolbar) !important;" in response.text
    assert "border-color: var(--review-toolbar-divider) !important;" in response.text
    assert "body[data-theme=\"dark\"] .request-goal-editor-shell .toastui-editor-defaultUI button:hover" in response.text
    assert "body[data-theme=\"dark\"] .request-goal-editor-shell .toastui-editor-mode-switch .tab-item.active" in response.text
    assert "color: #ffe2bd !important;" in response.text
    assert "body[data-theme=\"dark\"] .request-goal-editor-shell .toastui-editor-contents" in response.text
    assert "body[data-theme=\"dark\"] .request-goal-editor-shell .CodeMirror-cursor" in response.text
    assert "body[data-theme=\"dark\"] .request-goal-editor-shell .CodeMirror-selected" in response.text
    assert "addImageBlobHook: async (blob, callback) => {" in response.text
    assert "async function uploadRequestAttachment(blob, options = {})" in response.text
    assert "function buildRequestDraftImageMarkdown(uploaded)" in response.text
    assert "function insertTextAtTextareaCursor(textarea, text)" in response.text
    assert "async function attachImagesToRequestDraft(files)" in response.text
    assert "function requestDraftClipboardImageFiles(event)" in response.text
    assert "requestDraftInput.addEventListener('paste', (event) => {" in response.text
    assert "attachRequestDraftImageButton.addEventListener('click', () => requestDraftImageInput.click());" in response.text
    assert "requestDraftImageInput.addEventListener('change', () => {" in response.text
    assert "requestDraftComposer.addEventListener('drop', (event) => {" in response.text
    assert "insertTextAtTextareaCursor(requestDraftInput, buildRequestDraftImageMarkdown(uploaded));" in response.text
    assert "payload.request_upload_token = requestUploadToken;" in response.text
    assert "function generateRequestUploadToken()" in response.text
    assert "fetch(`/api/request-uploads?upload_token=${encodeURIComponent(uploadToken)}`" in response.text
    assert "boardPhaseManuallySelected = true;" in response.text
    assert "activeBoardPhase = 'plan';" in response.text
    assert response.text.index('id="title"') < response.text.index('id="target_repo"') < response.text.index('id="base_branch"') < response.text.index('id="background"') < response.text.index('id="goal"')
    assert response.text.index('id="constraints"') < response.text.index('id="acceptance_criteria"') < response.text.index('id="scope"') < response.text.index('id="out_of_scope"') < response.text.index('id="references"')
    assert "function buildAcceptanceCriteriaDefaults()" in response.text
    assert "이 요청으로 추가하거나 변경한 코드의 모든 케이스를 테스트해야 하며, 그 변경 범위의 테스트 커버리지는 100%여야 한다." in response.text
    assert "저장소 전체 커버리지 100%를 요구하는 뜻은 아니며, 전체 테스트 suite 는 작업 범위와 별개로 수행에 성공해야 한다." in response.text
    assert "assistant-agent-kanban.last-target-repo" in response.text
    assert "window.localStorage.setItem(lastTargetRepoStorageKey, normalized)" in response.text
    assert "applyTargetRepoAutofill(currentTargetRepoOptions())" in response.text
    assert "if (!await restoreRequestComposerDraftState()) resetFormState({ clearSavedDraft: false });" in response.text
    assert "void syncRequestComposerDraftState({ immediate: true, silent: true }); setModalOpen(false);" in response.text
    assert "let activeRequestComposerTab = 'assistant';" in response.text
    assert "setRequestComposerTab('assistant');" in response.text
    assert "function seedRequestDraftInput(force = false)" in response.text
    assert "requestDraftInput.value = '';" in response.text
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
    assert ".final-board .card { min-width: 0; max-width: 100%; min-height: 88px; overflow: hidden; }" in response.text
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
    assert "const branch = item.completed_group || item.base_branch || 'unknown';" in response.text
    assert "completedGroupTitle" in response.text
    assert "saveCompletedGroupOverride(nextGroup)" in response.text
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
    assert "['requests', 'planning', 'plan-approving', 'waiting-check-plans']" in response.text
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
    assert "/api/tasks/${taskId}/logs" in response.text
    assert "debug_rendered_content" in response.text
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
    assert "const planAttachmentMaxDimension = 1280;" in response.text
    assert "const planAttachmentWebpQuality = 0.6;" in response.text
    assert "async function compressPlanAttachmentBlob(blob, uploadName = '')" in response.text
    assert "async function replaceEmbeddedPlanImagesWithUploads(content)" in response.text
    assert "const compressedBlob = await canvasToBlob(canvas, 'image/webp', planAttachmentWebpQuality);" in response.text
    assert "const normalizedContent = await replaceEmbeddedPlanImagesWithUploads(getPlanEditorContent());" in response.text
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
    assert "width: calc(100vw - 48px)" in response.text
    assert "height: min(86vh, calc(100vh - 64px))" in response.text
    assert ".diff-desktop { font-size: 0.82rem; }" in response.text
    assert ".diff-mobile { font-size: 0.82rem; }" in response.text
    assert "loadTaskDetail(button.dataset.taskId, false, { snapshot: boardTaskSnapshots.get(button.dataset.taskId) || null });" in response.text
    assert "worker_log" in response.text
    assert "worker_log_file" in response.text
    assert 'id="task-tab-logs"' in response.text
    assert 'id="task-panel-logs"' in response.text
    assert "const taskTabLogs = document.getElementById('task-tab-logs');" in response.text
    assert "function renderTaskLogs(logs, { preserveSelection = true } = {})" in response.text
    assert "function appendWorkerLogPayload(payload)" in response.text
    assert "function appendWorkerLogFilePayload(payload)" in response.text
    assert "function appendTaskLogDelta(renderedDelta, debugDelta)" in response.text
    assert "function captureTaskLogScrollState()" in response.text
    assert "function restoreTaskLogScrollState(state)" in response.text
    assert "function scrollTaskLogViewerToBottom()" in response.text
    assert "function updateWorkerLiveLogsControlVisibility()" in response.text
    assert "if (!isOpenCode) workerLiveLogsModeInput.value = 'false';" in response.text
    assert "workerLiveLogsModeInput.disabled = !isOpenCode;" in response.text
    assert "field.style.display = isOpenCode ? '' : 'none';" in response.text
    assert "runtimeCodingAssistantInput.addEventListener('input', handleAssistantModeVisibilityChange);" in response.text
    assert "let taskLogViewerPinnedToBottom = true;" in response.text
    assert "function updateTaskLogViewerPinnedToBottom()" in response.text
    assert "function updateTaskLogViewerContent(previousContent, nextContent)" in response.text
    assert "hadScrollableOverflow: maxScrollTop > 0," in response.text
    assert "wasNearBottom: taskLogViewerPinnedToBottom || maxScrollTop - taskLogViewer.scrollTop <= 24," in response.text
    assert "if (state.wasNearBottom || (!state.hadScrollableOverflow && nextMax > 0)) {" in response.text
    assert "if (appendTaskLogDelta(payload.rendered_delta, payload.debug_rendered_delta)) {" in response.text
    assert "if (suffix) taskLogViewer.textContent += suffix;" in response.text
    assert "restoreTaskLogScrollState(scrollState);" in response.text
    assert "async function loadTaskLogs(taskId, { preserveSelection = true } = {})" in response.text
    assert "const shouldScrollToBottomAfterLoad = !preserveSelection || !activeTaskLogs || !activeLogName;" in response.text
    assert "if (!preserveSelection || !activeTaskLogs || !taskLogViewer.textContent || taskLogViewer.textContent === translateTask('runtimeLogSummaryEmpty')) {" in response.text
    assert "if (shouldScrollToBottomAfterLoad) scrollTaskLogViewerToBottom();" in response.text
    assert "window.alert('이 모드는 더 많은 토큰을 사용합니다.');" in response.text
    assert "taskLogViewer.addEventListener('scroll', updateTaskLogViewerPinnedToBottom);" in response.text
    assert "if (activeTaskTab === 'logs') {" in response.text
    assert "if (appendWorkerLogPayload(payload)) return;" in response.text
    assert "source.addEventListener('worker_log_file', (event) => {" in response.text
    assert "loadTaskLogs(activeTaskId).catch((error) => {" in response.text
    assert "maybeStartLogPolling" not in response.text
    assert "reviewerQaRefreshInterval = window.setInterval(() => {" in response.text
    assert "let activeTaskRequestToken = 0;" in response.text
    assert "let activeArtifactRequestToken = 0;" in response.text
    assert "function scheduleActiveTaskRefresh(options = {})" in response.text
    assert "if (requestToken !== activeTaskRequestToken || activeTaskId !== taskId) return;" in response.text
    assert "if (resolvedTab === 'logs' && (!softRefresh || !activeTaskLogs)) await loadTaskLogs(taskId);" in response.text
    assert "encodeURIComponent(activeArtifactName)" in response.text
    assert "if (requestToken !== activeArtifactRequestToken || taskId !== activeTaskId || activeArtifactName !== resolvedArtifactName) return;" in response.text
    assert "translateTask('stalePlanMessage')" in response.text
    assert "activeBoardPhase = 'implementation';" in response.text
    assert "boardPhaseManuallySelected = true;" in response.text
    assert "source.addEventListener('board_snapshot', (event) => {" in response.text
    assert "scheduleActiveTaskRefresh({ reloadArtifact: true });" in response.text
    assert "scheduleActiveTaskRefresh({ reloadArtifact: false });" in response.text
    assert "data-active-since" in response.text
    assert "renderRunningMeta(item)" not in response.text
    assert "running 00:00:00" not in response.text
    assert 'class="card-activity"' in response.text
    assert "aria-label=\"${escapeHtml(label)}\"" in response.text
    assert "/api/tasks/${activeTaskId}/approve-plan" in response.text
    assert "/api/tasks/${activeTaskId}/start-verification" in response.text
    assert "/api/tasks/${activeTaskId}/retry-verification-apply" in response.text
    assert "/api/tasks/${activeTaskId}/resume-implementer" in response.text
    assert "/api/tasks/${activeTaskId}/resume-reviewer" in response.text
    assert "/api/tasks/${activeTaskId}/resume-review-loop" in response.text
    assert "/api/tasks/${activeTaskId}/reject-verification" in response.text
    assert "/api/tasks/${activeTaskId}/approve-verification" in response.text
    assert 'id="retry-verification-apply"' in response.text
    assert 'id="resume-implementer"' in response.text
    assert 'id="resume-reviewer"' in response.text
    assert 'id="resume-review-loop"' in response.text
    assert "function stripOuterMarkdownFence(value)" in response.text
    assert "const normalizedValue = activeArtifactName === 'PLAN.md' ? stripOuterMarkdownFence(value || '') : (value || '');" in response.text
    assert "const canResumeImplementerFromSnapshot = state === 'todos'" in response.text
    assert "const canResumeReviewerFromSnapshot = state === 'waiting-reviews'" in response.text
    assert "const canResumeReviewLoopFromSnapshot = state === 'todos' && snapshot?.metadata?.review?.human_rework_required === true;" in response.text
    assert "...(snapshotMetadata.review || {})," in response.text
    assert "function retryVerificationApply()" in response.text
    assert "async function resumeImplementer(resumeMode)" in response.text
    assert "body: JSON.stringify({ resume_mode: normalizedResumeMode, message })" in response.text
    assert "async function resumeReviewer(resumeMode)" in response.text
    assert "function resumeReviewLoop()" in response.text
    assert "await loadTaskDetail(activeTaskId, true);" in response.text
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
    assert "로그" in response.text
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
    assert "모델 새로고침" in response.text
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
