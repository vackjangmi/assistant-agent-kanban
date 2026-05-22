from __future__ import annotations

import base64
import json
import subprocess
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.api.ui import TEMPLATE_PATH
from assistant_agent_kanban.config import PROJECT_ROOT
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import AdapterRunError
from assistant_agent_kanban.scanner import KanbanScanner

from ..conftest import FakeAdapter


from ._helpers import _locate_task_dir

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



def test_api_creates_parseable_request_when_title_contains_colon(configured_paths):
    config, _, _ = configured_paths
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        response = client.post(
            "/api/requests",
            json={
                "title": "Sonar cleanup: PAYMENT_ID constant",
                "goal": "Fix the Sonar finding.",
                "target_repo": str(config.repo_root),
                "base_branch": "feature/sonar-cleanup",
                "plan_auto_approve": True,
            },
        )

    assert response.status_code == 200
    task = KanbanScanner(config).scan()[0]
    assert task.metadata.title == "Sonar cleanup: PAYMENT_ID constant"
    assert task.metadata.target.repo_root == str(config.repo_root.resolve())
    assert task.metadata.target.base_branch == "feature/sonar-cleanup"
    assert task.metadata.request.plan_auto_approve is True



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



def test_api_returns_json_for_unexpected_request_draft_failures(configured_paths):
    config, _, _ = configured_paths

    class CrashingDraftAdapter(FakeAdapter):
        def run(self, **kwargs):
            raise RuntimeError("Internal Server Error")

    crashing_adapter = CrashingDraftAdapter([])
    config.runtime.role_backends.request_draft = "codex"
    app = create_app(
        config,
        FakeAdapter(["plan"]),
        FakeAdapter(["impl"]),
        FakeAdapter(["Verdict: PASS"]),
        adapter_registry={"codex": crashing_adapter},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/request-drafts",
            json={
                "title": "Draft unexpected failure",
                "goal": "Show a structured API error.",
                "message": "Please refine this.",
            },
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal Server Error"



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
    assert "Do not create new files unless the request or approved plan explicitly asks for them." in request_markdown
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
    assert "target_repo" in response.text
    assert '<input id="target_repo" name="target_repo" required readonly autocomplete="off" spellcheck="false">' in response.text
    assert "Browse to choose the repository used for this request." in response.text
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
    assert 'id="task-approval-gate-notice"' in response.text
    assert "approval-gate-notice" in response.text
    assert "function setApprovalGateNotice" in response.text
    assert "data-approval-gate-action" in response.text
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
    assert "void loadModelSettings(false, { allowHidden: true }).catch(() => {});" in response.text
    assert "if (!lastSettingsPayload) {" in response.text
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
    assert 'id="planner_model_select"' in response.text
    assert 'id="planner_model_options"' in response.text
    assert 'id="planner_model" name="planner_model" class="settings-model-custom" placeholder="Enter a custom model" list="planner_model_options" hidden disabled' in response.text
    assert 'id="plan_approval_model_select"' in response.text
    assert 'id="plan_approval_model_options"' in response.text
    assert 'id="implementer_model_select"' in response.text
    assert 'id="implementer_model_options"' in response.text
    assert 'id="reviewer_model_select"' in response.text
    assert 'id="reviewer_model_options"' in response.text
    assert 'id="commit_model_select"' in response.text
    assert 'id="commit_model_options"' in response.text
    assert 'id="request_draft_model_options"' in response.text
    assert "Other / custom…" in response.text
    assert "function renderRoleModelDatalist(config, items)" in response.text
    assert "renderRoleModelDatalist(config, items);" in response.text
    assert "function mergeSettingsPayload(data)" in response.text
    assert "applyRoleModelSelection(config, modelSelectInput.value);" in response.text
    assert "const useCustom = normalizedValue === customModelOptionValue || (Boolean(normalizedValue) && !knownOptions.includes(normalizedValue));" in response.text
    assert "config.modelInput.value = useCustom && normalizedValue !== customModelOptionValue ? normalizedValue : '';" in response.text
    assert "loadModelSettings(true, { preserveState: true })" in response.text
    assert "function hydrateSettingsDiscovery(data, { preserveState = false, updateSummary = true } = {})" in response.text
    assert "loadModelSettings(true, { preserveState: true, assistantOverride: selectedBackend, updateSummary: false })" in response.text
    assert "renderAllRoleModelOptions();\n        loadModelSettings(true, { preserveState: true, assistantOverride: selectedBackend, updateSummary: false })" not in response.text
    assert "backendInput.addEventListener('change', () => {" in response.text
    assert "renderRoleModelOptions(role);" in response.text
    assert "const selectedBackend = effectiveRoleBackend(role);" in response.text
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
    assert "Final approval is waiting on QA" in response.text
    assert "최종 승인 전에 QA 확인이 필요합니다" in response.text
    assert "Open QA checklist" in response.text
    assert "QA 체크리스트 열기" in response.text
    assert "approvalGateRetryTitle" in response.text
    assert "approvalGateReviewTitle" in response.text
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
    assert "const rawResponseText = await response.text();" in response.text
    assert "payload = JSON.parse(rawResponseText);" in response.text
    assert "if (!response.ok) throw new Error(rawResponseText.trim() || translateRequest('draftReplyError'));" in response.text
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
    assert 'id="task-tab-qa-checklist"' in response.text
    assert 'id="task-panel-qa-checklist"' in response.text
    assert "const taskQaChecklistTitle = document.getElementById('task-qa-checklist-title');" in response.text
    assert "taskQaChecklistTitle.textContent = translateHumanReview('qaChecklistTitle');" in response.text
    assert 'id="task-tab-review-note"' in response.text
    assert 'id="task-panel-review-note"' in response.text
    assert "/^(WORK|REVIEW|HUMAN-QA|HUMAN-VERIFY)-([0-9]{3})\\.md$/" in response.text
    assert "qaChecklistVisible: state === 'completed-reviews' || state === 'human-verifying'" in response.text
    assert "reviewerQaVisible: state === 'completed-reviews' || state === 'human-verifying'" in response.text
    assert "reviewNoteVisible: state === 'human-verifying'" in response.text
    assert "function parseReviewerQaTranscript(source)" in response.text
    assert "function updateReviewerQaLiveRefresh()" in response.text
    assert "const shouldWatchReviewerQa = !taskModal.hidden && Boolean(activeTaskId) && reviewerQaQuestionInFlight;" in response.text
    assert "const reviewerQaUpdated = appendReviewerQaWorkerLogPayload(payload);" in response.text
    assert "if (reviewerQaUpdated) return;" in response.text
    assert "const renderedContent = typeof payload.rendered_content === 'string' ? payload.rendered_content : '';" in response.text
    assert "const renderedDelta = typeof payload.rendered_delta === 'string' ? payload.rendered_delta : '';" in response.text
    assert "const nextAnswer = renderedContent ? renderedContent.trim() : `${reviewerQaPendingAnswer}${renderedDelta}`.trim();" in response.text
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
    assert "targetRepoInput.value = '';" in response.text
    assert "applyTargetRepoAutofill" not in response.text
    assert "lastTargetRepoStorageKey" not in response.text
    assert "if (targetInput === 'target_repo' && !cachedResolvedRepoDiscoveryRoot)" in response.text
    assert "await loadTargetRepoOptions().catch(() => {});" in response.text
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
    assert "const canResumeImplementerFromSnapshot = canResumeImplementerForMetadata(snapshot?.metadata, state);" in response.text
    assert "const canResumeReviewerFromSnapshot = state === 'waiting-reviews'" in response.text
    assert "const canResumeReviewLoopFromSnapshot = state === 'todos' && snapshot?.metadata?.review?.human_rework_required === true;" in response.text
    assert "...(snapshotMetadata.review || {})," in response.text
    assert "function retryVerificationApply()" in response.text
    assert "function canResumeImplementerForMetadata(metadata, state)" in response.text
    assert "return retryReason === 'review-rework-backstop' && metadata?.review?.human_rework_required !== true;" in response.text
    assert "if (!canResumeImplementerForMetadata(activeTaskDetail.metadata, activeTaskDetail.metadata.state)) return;" in response.text
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



def test_dashboard_page_keeps_target_repo_empty_with_custom_discovery_root(configured_paths, tmp_path):
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
    from ..conftest import init_git_repo

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
    from ..conftest import init_git_repo

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
