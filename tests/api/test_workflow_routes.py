from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import IntegrationError
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.models import utc_now
from assistant_agent_kanban.split_proposals import sync_split_proposal_artifacts
from assistant_agent_kanban.user_settings_store import ProjectSettings, RuntimePreferenceSettings, UserSecretUpdate

from ..conftest import FakeAdapter, create_request_task
from ..test_plan_approval_worker import plan_with_split_proposal


from ._helpers import _task_ready_for_completed_reviews


def _verification_repo_for(scanner: KanbanScanner, task_id: str) -> Path:
    refreshed = scanner.find_task(task_id)
    verification_repo_root = refreshed.metadata.integration.verification_repo_root
    return Path(verification_repo_root) if verification_repo_root else Path(refreshed.metadata.target.repo_root)


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


def test_api_task_actions_require_creator_or_admin(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    config.runtime.auto_dispatch = False
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.create_user("admin", "admin-password", is_admin=True)
    owner = app.state.user_settings_store.create_user("owner", "owner-password", is_admin=False)
    app.state.user_settings_store.create_user("other", "other-password", is_admin=False)

    with TestClient(app) as owner_client:
        assert owner_client.post("/api/auth/login", json={"username": "owner", "password": "owner-password"}).status_code == 200
        created = owner_client.post(
            "/api/requests",
            json={
                "title": "creator gated task",
                "goal": "Only the creator or admin can approve the plan.",
                "target_repo": str(config.repo_root),
                "base_branch": "main",
            },
        )
        assert created.status_code == 200

    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions
    task = scanner.scan()[0]
    assert task.metadata.created_by_user_id == owner.user_id
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text(
        "\n\n".join(
            [
                "## Summary\nPlan summary.",
                "## Scope\nPlan scope.",
                "## Out of Scope\nNone.",
                "## File Map\n- file",
                "## Step-by-step Plan\n1. Change it.",
                "## Validation Plan\n- Run tests.",
                "## Acceptance Criteria\n- Done.",
                "## Risks\nNone.",
                "## Open Questions\nNone.",
            ]
        )
        + "\n"
    )
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    with TestClient(app) as other_client:
        assert other_client.post("/api/auth/login", json={"username": "other", "password": "other-password"}).status_code == 200
        blocked = other_client.post(f"/api/tasks/{waiting.metadata.task_id}/approve-plan")
        assert blocked.status_code == 403
        assert "creator" in blocked.json()["detail"]

    with TestClient(app) as admin_client:
        assert admin_client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}).status_code == 200
        approved = admin_client.post(f"/api/tasks/{waiting.metadata.task_id}/approve-plan")
        assert approved.status_code == 200
        assert approved.json()["state"] == "todos"


def test_api_split_plan_creates_children_and_closes_parent(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "api-split-parent")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions
    task = next(task for task in scanner.scan() if task.metadata.title == "api-split-parent")
    task.metadata.created_by_user_id = "owner-id"
    task.metadata.created_by_username = "owner"
    metadata_store.save(task.task_dir, task.metadata)
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    plan_text = plan_with_split_proposal()
    (planning.task_dir / "PLAN.md").write_text(plan_text)
    planning.metadata.plan.revision = 1
    sync_split_proposal_artifacts(planning.task_dir, planning.metadata, plan_text)
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{waiting.metadata.task_id}/split-plan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "closed"
    assert payload["closure"]["reason"] == "split_into_children"
    assert len(payload["closure"]["child_task_ids"]) == 2
    children = [scanner.find_task(task_id) for task_id in payload["closure"]["child_task_ids"]]
    assert all(child.state == TaskState.REQUESTS for child in children)
    assert all(child.metadata.created_by_user_id == "owner-id" for child in children)
    assert all(child.metadata.created_by_username == "owner" for child in children)


def test_api_cancel_task_moves_to_closed_and_removes_workspace(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "api-cancel-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions
    task = next(task for task in scanner.scan() if task.metadata.title == "api-cancel-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    workspace_root = config.workspace.root / todo.metadata.task_id
    repo_dir = workspace_root / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "scratch.txt").write_text("scratch\n")
    todo.metadata.implementation.workspace = str(repo_dir)
    metadata_store.save(todo.task_dir, todo.metadata)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{todo.metadata.task_id}/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "closed"
    assert payload["closure"]["reason"] == "cancelled_by_human"
    assert not workspace_root.exists()
    closed = scanner.find_task(todo.metadata.task_id)
    assert closed.state == TaskState.CLOSED


def test_api_rerequests_cancelled_task_from_original_request(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    task_dir = create_request_task(config, "api-rerequest-task", body="Implement again.\n\n![shot](_attachments/shot.png)")
    attachments_dir = task_dir / "_attachments"
    attachments_dir.mkdir()
    (attachments_dir / "shot.png").write_bytes(b"png")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    scanner = runtime.task_service.scanner
    task = next(task for task in scanner.scan() if task.metadata.title == "api-rerequest-task")
    closed = runtime.cancellation_service.cancel(task.metadata.task_id, by="human")

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{closed.metadata.task_id}/rerequest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] != closed.metadata.task_id
    assert payload["title"] == "api-rerequest-task"
    assert payload["state"] == "planning"
    rerequested = scanner.find_task(payload["task_id"])
    assert rerequested.state == TaskState.PLANNING
    assert (rerequested.task_dir / "REQUEST.md").read_text() == (closed.task_dir / "REQUEST.md").read_text()
    assert (rerequested.task_dir / "_attachments" / "shot.png").read_bytes() == b"png"
    assert scanner.find_task(closed.metadata.task_id).state == TaskState.CLOSED


def test_api_rerequest_rejects_non_cancelled_closed_task(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "api-rerequest-blocked-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions
    locks = runtime.task_service.locks
    metadata_store = runtime.task_service.metadata_store
    task = next(task for task in scanner.scan() if task.metadata.title == "api-rerequest-blocked-task")
    with locks.acquire(task.task_dir, task.metadata, owner="human", run_id="manual-close"):
        task.metadata.closure.reason = "other"
        task.metadata.closure.closed_by = "human"
        task.metadata.closure.closed_at = utc_now()
        metadata_store.save(task.task_dir, task.metadata)
        closed = transitions.move(task, TaskState.CLOSED, by="human")

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{closed.metadata.task_id}/rerequest")

    assert response.status_code == 409
    assert "human-cancelled" in response.json()["detail"]



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
        assert payload["implementation"]["session_id"] == "ses_impl"
        assert payload["implementation"]["session_tokens"] == 321
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
        assert response.json()["detail"] == (
            "implementer resume is only allowed when an active implementation retry gate or paused review backstop is present"
        )



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
        assert second.json()["detail"] == (
            "implementer resume is only allowed when an active implementation retry gate or paused review backstop is present"
        )



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
        assert response.json()["detail"] == (
            "implementer resume is only allowed when an active implementation retry gate or paused review backstop is present"
        )



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



def test_api_resumes_implementer_from_review_rework_backstop(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-implementer-review-backstop-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-implementer-review-backstop-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos.metadata.retry_gate.reason = "review-rework-backstop"
    todos.metadata.retry_gate.consecutive_count = 1
    todos.metadata.retry_gate.not_before = utc_now()
    todos.metadata.review.human_rework_required = False
    metadata_store.save(todos.task_dir, todos.metadata)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{todos.metadata.task_id}/resume-implementer",
            json={"message": "Continue implementing after the paused review backstop."},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["retry_gate"]["reason"] is None
        assert payload["retry_gate"]["not_before"] is None
        assert payload["implementation"]["resume_mode"] == "pinned"

        detail = client.get(f"/api/tasks/{todos.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["retry_gate"]["reason"] is None
        assert "Continue implementing after the paused review backstop." in detail.json()["human_review"]["reviewer_qa_markdown"]



def test_api_rejects_resume_implementer_for_human_review_required_rework(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "resume-implementer-human-review-required-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    runtime = app.state.runtime
    metadata_store = runtime.task_service.metadata_store
    scanner = runtime.task_service.scanner
    transitions = runtime.task_service.transitions

    task = next(task for task in scanner.scan() if task.metadata.title == "resume-implementer-human-review-required-task")
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("plan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos.metadata.retry_gate.reason = "review-rework-backstop"
    todos.metadata.retry_gate.consecutive_count = 1
    todos.metadata.retry_gate.not_before = utc_now()
    todos.metadata.review.human_rework_required = True
    metadata_store.save(todos.task_dir, todos.metadata)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{todos.metadata.task_id}/resume-implementer")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "implementer resume is only allowed when an active implementation retry gate or paused review backstop is present"
        )



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
        verification_repo = _verification_repo_for(scanner, completed.metadata.task_id)
        assert (repo_root / "app.txt").read_text() == "review me\n"
        assert (verification_repo / "app.txt").read_text() == "review me\n"

        reject = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reject-verification",
            json={"note": "Need another pass."},
        )
        assert reject.status_code == 200
        assert reject.json()["state"] == TaskState.TODOS.value
        assert (repo_root / "app.txt").read_text() == "hello\n"


def test_api_authenticated_verification_requires_git_token(configured_paths):
    config, _, _ = configured_paths
    config.auth.enabled = False
    config.runtime.auto_dispatch = False
    create_request_task(config, "auth-human-verify-token-required-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    admin = app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)
    _, completed = _task_ready_for_completed_reviews(config, "auth-human-verify-token-required-task")

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": admin.username, "password": "secret-password"}).status_code == 200
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")

    assert start.status_code == 409
    assert start.json()["detail"] == "Git token is required to push review branches in multi-user mode"


def test_api_authenticated_verification_pushes_remote_review_branch_even_when_global_setting_is_disabled(configured_paths, tmp_path):
    config, repo_root, _ = configured_paths
    config.auth.enabled = False
    config.review_branch_remote.enabled = False
    config.runtime.auto_dispatch = False
    remote_repo = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_repo)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "remote", "add", "origin", str(remote_repo)], check=True, capture_output=True, text=True)
    create_request_task(config, "auth-human-verify-force-remote-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    admin = app.state.user_settings_store.create_user("admin", "secret-password", is_admin=True)
    app.state.user_settings_store.update_user_settings(
        admin.user_id,
        RuntimePreferenceSettings(),
        secrets_update=UserSecretUpdate(git_token="test-token"),
    )
    _, completed = _task_ready_for_completed_reviews(config, "auth-human-verify-force-remote-task")

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": admin.username, "password": "secret-password"}).status_code == 200
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")

    assert start.status_code == 200
    remote_branch_ref = f"refs/heads/review/{completed.metadata.task_id.lower()}"
    show_ref = subprocess.run(["git", "--git-dir", str(remote_repo), "show-ref", "--verify", remote_branch_ref], capture_output=True, text=True, check=False)
    assert show_ref.returncode == 0
    refreshed = app.state.runtime.scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.remote_review_branch == f"review/{completed.metadata.task_id.lower()}"
    assert refreshed.metadata.integration.remote_name == "origin"


def test_api_local_verification_ignores_project_remote_push_override(configured_paths, tmp_path):
    config, repo_root, _ = configured_paths
    config.auth.enabled = False
    config.review_branch_remote.enabled = False
    config.runtime.auto_dispatch = False
    remote_repo = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_repo)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "remote", "add", "origin", str(remote_repo)], check=True, capture_output=True, text=True)
    create_request_task(config, "local-human-verify-no-remote-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    app.state.user_settings_store.update_project_settings(
        ProjectSettings(
            repo_root=str(repo_root),
            review_branch_push_enabled=True,
            review_branch_require_push_success=True,
        )
    )
    _, completed = _task_ready_for_completed_reviews(config, "local-human-verify-no-remote-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")

    assert start.status_code == 200
    assert start.json()["state"] == TaskState.HUMAN_VERIFYING.value
    remote_branch_ref = f"refs/heads/review/{completed.metadata.task_id.lower()}"
    show_ref = subprocess.run(["git", "--git-dir", str(remote_repo), "show-ref", "--verify", remote_branch_ref], capture_output=True, text=True, check=False)
    assert show_ref.returncode != 0
    refreshed = app.state.runtime.scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.remote_review_branch is None
    assert refreshed.metadata.integration.remote_name is None



def test_api_returns_to_todos_on_verification_target_repo_drift(configured_paths):
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
        assert start.json()["state"] == TaskState.TODOS.value

    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.state == TaskState.TODOS
    assert refreshed.metadata.integration.applied is False
    assert refreshed.metadata.commit.status == "pending"
    assert refreshed.metadata.retry_gate.reason == "verification-target-repo-drift"


def test_api_blocks_human_verification_start_when_target_worktree_is_dirty(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    dirty_file = docs_dir / "unrelated.md"
    dirty_file.write_text("existing note\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "docs/unrelated.md"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "add unrelated doc"], check=True, capture_output=True, text=True)
    dirty_file.unlink()

    create_request_task(config, "human-verify-dirty-target-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-dirty-target-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")

    assert start.status_code == 409
    assert "target repo must be clean before apply" in start.json()["detail"]
    assert scanner.find_task(completed.metadata.task_id).state == TaskState.COMPLETED_REVIEWS
    assert (repo_root / "app.txt").read_text() == "hello\n"
    assert not dirty_file.exists()
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--short"], check=True, capture_output=True, text=True)
    assert "D docs/unrelated.md" in status.stdout


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


def test_api_supports_human_verification_start_into_empty_non_git_target(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    empty_target = config.kanban_root.parent / "empty-target"
    empty_target.mkdir()
    create_request_task(
        config,
        "human-verify-empty-target-task",
        target_repo_root=empty_target,
        body="Create a new file named app.txt.",
    )
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-empty-target-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")

    assert start.status_code == 200
    payload = start.json()
    assert payload["state"] == TaskState.HUMAN_VERIFYING.value
    assert payload["integration"]["initialized_target_repo"] is False
    assert not (empty_target / ".git").exists()
    assert not (empty_target / "app.txt").exists()
    verification_repo = _verification_repo_for(scanner, completed.metadata.task_id)
    assert (verification_repo / "app.txt").read_text() == "review me\n"
    branch = subprocess.run(["git", "-C", str(verification_repo), "branch", "--show-current"], check=True, capture_output=True, text=True)
    assert branch.stdout.strip() == f"review/{completed.metadata.task_id.lower()}"
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.patch_path
    assert "new file mode" in open(refreshed.metadata.integration.patch_path).read()


def test_api_reject_restores_empty_non_git_target(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    empty_target = config.kanban_root.parent / "empty-target"
    empty_target.mkdir()
    create_request_task(
        config,
        "human-verify-empty-target-reject-task",
        target_repo_root=empty_target,
        body="Create a new file named app.txt.",
    )
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "human-verify-empty-target-reject-task")

    with TestClient(app) as client:
        start = client.post(f"/api/tasks/{completed.metadata.task_id}/start-verification")
        assert start.status_code == 200
        reject = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reject-verification",
            json={"note": "Need another pass."},
        )

    assert reject.status_code == 200
    assert reject.json()["state"] == TaskState.TODOS.value
    assert list(empty_target.iterdir()) == []
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.initialized_target_repo is False



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



def test_api_blocks_reject_after_verification_target_repo_drift(configured_paths):
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
        assert start.json()["state"] == TaskState.TODOS.value
        reject = client.post(
            f"/api/tasks/{completed.metadata.task_id}/reject-verification",
            json={"note": ""},
        )

    assert reject.status_code == 409
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
        approve = client.post(
            f"/api/tasks/{completed.metadata.task_id}/approve-verification",
            json={"completion_mode": "target-branch"},
        )

    assert approve.status_code == 200
    assert approve.json()["state"] == TaskState.TODOS.value
    refreshed = scanner.find_task(completed.metadata.task_id)
    assert refreshed.metadata.integration.final_branch is None



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
        assert "Verdict:" not in payload["answer"]
        assert "Acceptance Criteria Check" not in payload["answer"]

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["human_review"]["reviewer_qa_path"] == "REVIEWER-QA-001.md"
        assert "Can we keep the existing label?" in detail.json()["human_review"]["reviewer_qa_markdown"]
        assert "Verdict:" not in detail.json()["human_review"]["reviewer_qa_markdown"]

    task = KanbanScanner(config).find_task(completed.metadata.task_id)
    assert (task.task_dir / "REVIEWER-QA-001.md").exists()
    assert reviewer_adapter.run_calls[0]["show_thinking"] is True



def test_api_rerequests_from_latest_reviewer_qa_answer(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "reviewer-qa-rerequest-api-task")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))
    scanner, completed = _task_ready_for_completed_reviews(config, "reviewer-qa-rerequest-api-task")
    task = scanner.find_task(completed.metadata.task_id)
    (task.task_dir / "REVIEWER-QA-001.md").write_text(
        "\n".join(
            [
                "# Reviewer Q&A",
                "",
                "## Question 1",
                "- Asked by: human",
                "- Asked at: 2026-01-01T00:00:00+00:00",
                "",
                "Can we keep the existing label?",
                "",
                "## Answer 1",
                "- Answered by: reviewer",
                "- Answered at: 2026-01-01T00:00:05+00:00",
                "",
                "The label can stay, but the helper copy still needs to change.",
                "",
            ]
        )
    )
    task.metadata.review.qa_path = "REVIEWER-QA-001.md"
    scanner.metadata_store.save(task.task_dir, task.metadata)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{completed.metadata.task_id}/reviewer-qa-rerequest")
        assert response.status_code == 200
        payload = response.json()
        assert payload["state"] == "todos"

        detail = client.get(f"/api/tasks/{completed.metadata.task_id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["state"] == "todos"
        assert "## Re-request Note" in detail.json()["human_review"]["note_markdown"]
        assert "helper copy still needs to change" in detail.json()["human_review"]["note_markdown"]



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
