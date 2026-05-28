from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import HistoryEntry, HumanQaChecklistItem
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.models import utc_now

from ..conftest import FakeAdapter, create_request_task


from ._helpers import _settings_adapter_registry, _task_ready_for_completed_reviews, valid_plan_markdown

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


def test_api_exposes_task_inspection_and_inspector_questions(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "inspection-api-task")
    inspector_adapter = FakeAdapter(["The task is idle and waiting for planning."])
    app = create_app(config, inspector_adapter, FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]

    with TestClient(app) as client:
        inspection = client.get(f"/api/tasks/{task.metadata.task_id}/inspection")
        faqs = client.get(f"/api/tasks/{task.metadata.task_id}/inspection/faqs")
        answer = client.post(
            f"/api/tasks/{task.metadata.task_id}/inspection/questions",
            json={"question_id": "is-running"},
        )

    assert inspection.status_code == 200
    assert inspection.json()["health"] == "idle"
    assert faqs.status_code == 200
    assert any(item["id"] == "is-running" for item in faqs.json()["items"])
    assert answer.status_code == 200
    assert answer.json()["answer"] == "The task is idle and waiting for planning."
    assert inspector_adapter.run_calls[0]["agent"] == "fs-kanban-inspector"
    assert "Do not modify files" in str(inspector_adapter.run_calls[0]["prompt"])



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


def test_api_renders_pretty_json_runtime_logs_for_task(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "pretty-json-log-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    task = KanbanScanner(config).scan()[0]
    log_dir = config.runs_dir / task.metadata.task_id
    log_dir.mkdir(parents=True)
    (log_dir / "reviewer-001.jsonl").write_text(
        '\n'.join(
            [
                "```json",
                "{",
                '  "verdict": "PASS",',
                '  "markdown": "Verdict: PASS"',
                "}",
                "```",
            ]
        )
        + "\n"
    )

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.metadata.task_id}/logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entries"][0]["name"] == "reviewer-001.jsonl"
    assert '"verdict": "PASS",' in payload["entries"][0]["rendered_content"]



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



def test_api_updates_human_qa_checklist_item_during_verification(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "human-qa-api-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-qa-api-task")
    completed.metadata.human_verification.qa_cycle = completed.metadata.cycle
    completed.metadata.human_verification.qa_path = f"HUMAN-QA-{completed.metadata.cycle:03d}.md"
    completed.metadata.human_verification.qa_items = [
        HumanQaChecklistItem(
            id="qa-main",
            title="Verify main behavior",
            steps=["Open the target repo", "Exercise the change"],
            expected_result="The requested behavior works.",
            required=True,
        )
    ]
    scanner.metadata_store.save(completed.task_dir, completed.metadata)

    with TestClient(app) as client:
        blocked = client.post(
            f"/api/tasks/{completed.metadata.task_id}/human-qa/qa-main",
            json={"checked": True},
        )
        assert blocked.status_code == 409

        client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        response = client.post(
            f"/api/tasks/{completed.metadata.task_id}/human-qa/qa-main",
            json={"checked": True, "note": "Passed manually."},
        )

        assert response.status_code == 200
        assert response.json()["checked"] is True
        assert response.json()["note"] == "Passed manually."
        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.json()["human_review"]["qa_completed_required_count"] == 1


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
    (task.task_dir / "REQUEST-DRAFT.md").write_text("draft\n")
    (task.task_dir / "PLAN.md").write_text("plan\n")
    (task.task_dir / "PLAN-APPROVAL.md").write_text("approved plan\n")
    (task.task_dir / "PLAN-HUMAN-APPROVAL.md").write_text("human approved plan\n")
    (task.task_dir / "WORK-002.md").write_text("work 2\n")
    (task.task_dir / "REVIEW-002.md").write_text("review 2\n")
    (task.task_dir / "HUMAN-QA-002.md").write_text("qa 2\n")
    (task.task_dir / "HUMAN-VERIFY-002.md").write_text("verify 2\n")
    (task.task_dir / "WORK-001.md").write_text("work 1\n")
    (task.task_dir / "REVIEW-001.md").write_text("review 1\n")
    (task.task_dir / "HUMAN-QA-001.md").write_text("qa 1\n")
    (task.task_dir / "HUMAN-VERIFY-001.md").write_text("verify 1\n")
    (task.task_dir / "NOTES.md").write_text("notes\n")
    (task.task_dir / "COMMIT.md").write_text("commit\n")

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{task.metadata.task_id}")

    assert detail.status_code == 200
    assert detail.json()["markdown_files"] == [
        "REQUEST-DRAFT.md",
        "REQUEST.md",
        "PLAN.md",
        "PLAN-APPROVAL.md",
        "PLAN-HUMAN-APPROVAL.md",
        "WORK-001.md",
        "REVIEW-001.md",
        "HUMAN-QA-001.md",
        "HUMAN-VERIFY-001.md",
        "WORK-002.md",
        "REVIEW-002.md",
        "HUMAN-QA-002.md",
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
    assert len(stage_timing["summaries"]) == 12
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
