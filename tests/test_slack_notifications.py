from __future__ import annotations

from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import TaskContext
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.slack_notifications import SlackMilestoneNotifier

from .conftest import create_request_task


def test_slack_notifier_sends_milestone_message(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-plan-task")
    task = KanbanScanner(config).scan()[0]
    planning = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.WAITING_CHECK_PLANS)
    planning.metadata.state = TaskState.WAITING_CHECK_PLANS
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(planning, previous_state=TaskState.PLANNING, by="planner", note="plan ready")

    assert calls
    method, token, body = calls[0]
    assert method == "chat.postMessage"
    assert token == "xoxb-test"
    assert body is not None
    text = str(body["text"])
    assert body["channel"] == "#agent-alerts"
    assert text == "🧩 [{}] {}\n• Repo: {}\n• Base branch: {}".format(
        planning.metadata.task_id,
        planning.metadata.title,
        planning.metadata.target.repo_root,
        planning.metadata.target.base_branch,
    )
    blocks = body["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "header"
    assert blocks[0]["text"]["text"] == f"🧩 {planning.metadata.title}"
    assert blocks[2]["type"] == "section"
    assert len(blocks[2]["fields"]) == 4
    assert planning.metadata.slack.thread_ts is None


def test_slack_notifier_uploads_plan_artifact_when_plan_is_ready(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-plan-upload-task")
    task = KanbanScanner(config).scan()[0]
    (task.task_dir / "PLAN.md").write_text("# Plan\n\nShip it\n")
    task.metadata.plan.path = "PLAN.md"
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    waiting = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.WAITING_CHECK_PLANS)
    waiting.metadata.state = TaskState.WAITING_CHECK_PLANS
    uploads: list[tuple[str, str, str, str, bytes]] = []

    def fake_call(method: str, *, token: str, body=None):
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        uploads.append((token, channel_id, thread_ts, filename, content))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_upload_file_to_thread", fake_upload)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(waiting, previous_state=TaskState.PLANNING, by="planner")

    assert uploads
    assert uploads[0][3] == "PLAN.md"
    assert uploads[0][4] == b"# Plan\n\nShip it\n"


def test_slack_notifier_skips_non_milestone_transition(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-non-milestone")
    task = KanbanScanner(config).scan()[0]
    moved = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.PLANNING)
    moved.metadata.state = TaskState.PLANNING
    calls: list[str] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append(method)
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(moved, previous_state=TaskState.REQUESTS, by="planner")

    assert calls == []


def test_slack_notifier_handles_plan_approving_review_milestone(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-plan-approval-task")
    task = KanbanScanner(config).scan()[0]
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    waiting = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.WAITING_CHECK_PLANS)
    waiting.metadata.state = TaskState.WAITING_CHECK_PLANS
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(waiting, previous_state=TaskState.PLAN_APPROVING, by="plan_approval", note="plan review required")

    assert calls
    payload = calls[0][2]
    assert payload is not None
    assert payload["thread_ts"] == "173.456"
    assert str(payload["text"]).startswith("📝 Plan ready for review\n")
    assert "slack-plan-approval-task" not in str(payload["text"])
    assert "Repo:" not in str(payload["text"])
    assert "Base branch:" not in str(payload["text"])
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == "📝 *Plan ready for review*"
    assert len(blocks[1]["fields"]) == 1
    assert blocks[1]["fields"][0]["text"] == "*State change*\n`plan-approving` → `waiting-check-plans`"


def test_slack_notifier_handles_reviewing_to_todos_milestone(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-review-changes-task")
    task = KanbanScanner(config).scan()[0]
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    todo = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.TODOS)
    todo.metadata.state = TaskState.TODOS
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(todo, previous_state=TaskState.REVIEWING, by="reviewer", note="fix remaining issues")

    assert calls
    payload = calls[0][2]
    assert payload is not None
    assert payload["thread_ts"] == "173.456"
    assert str(payload["text"]).startswith("🔁 Review requested changes\n")
    assert "slack-review-changes-task" not in str(payload["text"])
    assert "Repo:" not in str(payload["text"])
    assert "Base branch:" not in str(payload["text"])
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["text"]["text"] == "🔁 *Review requested changes*"
    elements = blocks[-1]["elements"]
    assert isinstance(elements, list)
    assert len(elements) == 1
    assert elements[0]["action_id"] == "resume_review_loop"


def test_slack_notifier_clears_resume_review_loop_when_task_reenters_implementing(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-resume-clear-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    task.metadata.slack.channel = "C123"
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.action_message_ts["resume_review_loop"] = "173.789"
    task.metadata.slack.action_message_text["resume_review_loop"] = "🔁 Review requested changes"
    MetadataStore().save(task.task_dir, task.metadata)
    implementing = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.IMPLEMENTING)
    implementing.metadata.state = TaskState.IMPLEMENTING
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(
        implementing,
        previous_state=TaskState.TODOS,
        by="implementer",
    )

    assert calls == [
        (
            "chat.update",
            "xoxb-test",
            {
                "channel": "C123",
                "ts": "173.789",
                "text": "🔁 Review requested changes",
                "blocks": [],
            },
        )
    ]
    persisted = MetadataStore().load(task.task_dir)
    assert "resume_review_loop" not in persisted.slack.action_message_ts
    assert "resume_review_loop" not in persisted.slack.action_message_text


def test_slack_notifier_creates_parent_message_and_persists_thread(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-thread-parent-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    (task.task_dir / "WORK-000.md").write_text("# Work\n\nImplemented\n")
    waiting = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.WAITING_REVIEWS)
    waiting.metadata.state = TaskState.WAITING_REVIEWS
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    uploads: list[tuple[str, str, str, str, bytes]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True, "ts": "173.456", "channel": "C123"}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        uploads.append((token, channel_id, thread_ts, filename, content))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_upload_file_to_thread", fake_upload)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(waiting, previous_state=TaskState.IMPLEMENTING, by="implementer")

    assert calls
    payload = calls[0][2]
    assert payload is not None
    assert "thread_ts" not in payload
    assert str(payload["text"]).startswith(f"🧩 [{waiting.metadata.task_id}] {waiting.metadata.title}")
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "header"
    assert blocks[1]["text"]["text"].startswith("*Task opened in Slack thread*")
    assert uploads
    assert uploads[0][1] == "C123"
    assert uploads[0][2] == "173.456"
    assert uploads[0][3] == "WORK-000.md"
    assert waiting.metadata.slack.thread_ts == "173.456"
    assert waiting.metadata.slack.channel == "C123"
    persisted = MetadataStore().load(waiting.task_dir)
    assert persisted.slack.thread_ts == "173.456"
    assert persisted.slack.uploaded_markdown["WORK-000.md"]


def test_slack_notifier_reuses_existing_thread(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-thread-reply-task")
    task = KanbanScanner(config).scan()[0]
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    completed = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.COMPLETED_REVIEWS)
    completed.metadata.state = TaskState.COMPLETED_REVIEWS
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(completed, previous_state=TaskState.REVIEWING, by="reviewer")

    assert calls
    payload = calls[0][2]
    assert payload is not None
    assert payload["thread_ts"] == "173.456"
    assert payload["channel"] == "C123"
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["text"]["text"] == "🔍 *AI review passed*"
    assert blocks[-1]["type"] == "actions"
    assert completed.metadata.slack.thread_ts == "173.456"


def test_slack_notifier_skips_duplicate_markdown_upload_when_content_unchanged(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-duplicate-upload-task")
    task = KanbanScanner(config).scan()[0]
    review_path = task.task_dir / "REVIEW-000.md"
    review_path.write_text("# Review\n\nLooks good\n")
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    completed = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.COMPLETED_REVIEWS)
    completed.metadata.state = TaskState.COMPLETED_REVIEWS
    uploads: list[str] = []

    def fake_call(method: str, *, token: str, body=None):
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        uploads.append(filename)
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_upload_file_to_thread", fake_upload)

    notifier = SlackMilestoneNotifier(config, MetadataStore())
    notifier.notify_transition(completed, previous_state=TaskState.REVIEWING, by="reviewer")
    notifier.notify_transition(completed, previous_state=TaskState.REVIEWING, by="reviewer")

    assert uploads == ["REVIEW-000.md"]


def test_slack_notifier_reuploads_markdown_when_content_changes(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-reupload-task")
    task = KanbanScanner(config).scan()[0]
    review_path = task.task_dir / "REVIEW-000.md"
    review_path.write_text("# Review\n\nInitial\n")
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    completed = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.COMPLETED_REVIEWS)
    completed.metadata.state = TaskState.COMPLETED_REVIEWS
    uploads: list[str] = []

    def fake_call(method: str, *, token: str, body=None):
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        uploads.append(content.decode("utf-8"))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_upload_file_to_thread", fake_upload)

    notifier = SlackMilestoneNotifier(config, MetadataStore())
    notifier.notify_transition(completed, previous_state=TaskState.REVIEWING, by="reviewer")
    review_path.write_text("# Review\n\nUpdated\n")
    notifier.notify_transition(completed, previous_state=TaskState.REVIEWING, by="reviewer")

    assert uploads == ["# Review\n\nInitial\n", "# Review\n\nUpdated\n"]


def test_slack_notifier_does_not_persist_digest_when_upload_fails(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-upload-fail-task")
    task = KanbanScanner(config).scan()[0]
    review_path = task.task_dir / "REVIEW-000.md"
    review_path.write_text("# Review\n\nInitial\n")
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    completed = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.COMPLETED_REVIEWS)
    completed.metadata.state = TaskState.COMPLETED_REVIEWS

    def fake_call(method: str, *, token: str, body=None):
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        return {"ok": False, "error": "missing_scope"}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_upload_file_to_thread", fake_upload)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(completed, previous_state=TaskState.REVIEWING, by="reviewer")

    persisted = MetadataStore().load(completed.task_dir)
    assert "REVIEW-000.md" not in persisted.slack.uploaded_markdown


def test_slack_notifier_adds_human_verification_action_buttons(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-human-action-task")
    task = KanbanScanner(config).scan()[0]
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    verifying = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.HUMAN_VERIFYING)
    verifying.metadata.state = TaskState.HUMAN_VERIFYING
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(verifying, previous_state=TaskState.COMPLETED_REVIEWS, by="human")

    assert calls
    payload = calls[0][2]
    assert payload is not None
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    first_block = blocks[-1]
    assert first_block["type"] == "actions"
    elements = first_block["elements"]
    assert isinstance(elements, list)
    assert elements[0]["action_id"] == "approve_verification"
    assert elements[1]["action_id"] == "reject_verification"


def test_slack_notifier_uploads_verification_note_changed_files_and_patch(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-human-artifact-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    task.metadata.cycle = 1
    task.metadata.state = TaskState.HUMAN_VERIFYING
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    note_path = task.task_dir / "HUMAN-VERIFY-001.md"
    note_path.write_text("# Human Verification\n\nVerdict: IN_PROGRESS\n")
    task.metadata.human_verification.note_path = "HUMAN-VERIFY-001.md"
    runs_dir = config.runs_dir / task.metadata.task_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    patch_path = runs_dir / "review-001.patch"
    patch_path.write_text(
        "diff --git a/app.txt b/app.txt\n"
        "index ce01362..2ee250a 100644\n"
        "--- a/app.txt\n"
        "+++ b/app.txt\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+review me\n"
    )
    task.metadata.integration.patch_path = str(patch_path)
    MetadataStore().save(task.task_dir, task.metadata)
    verifying = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.HUMAN_VERIFYING)
    verifying.metadata.state = TaskState.HUMAN_VERIFYING
    uploads: list[tuple[str, bytes]] = []

    def fake_call(method: str, *, token: str, body=None):
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        uploads.append((filename, content))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_upload_file_to_thread", fake_upload)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(verifying, previous_state=TaskState.COMPLETED_REVIEWS, by="human")

    assert [name for name, _content in uploads] == [
        "HUMAN-VERIFY-001.md",
        "CHANGED-FILES-001.md",
        "review-001.patch",
    ]
    changed_files_content = dict(uploads)["CHANGED-FILES-001.md"].decode("utf-8")
    assert "# Changed Files (1)" in changed_files_content
    assert "`app.txt` — modified (+1 / -1, hunks=1)" in changed_files_content
    assert dict(uploads)["review-001.patch"].decode("utf-8").startswith("diff --git a/app.txt b/app.txt")


def test_slack_notifier_deduplicates_verification_bundle_uploads(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-human-dedupe-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    task.metadata.cycle = 1
    task.metadata.state = TaskState.HUMAN_VERIFYING
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    (task.task_dir / "HUMAN-VERIFY-001.md").write_text("# Human Verification\n")
    task.metadata.human_verification.note_path = "HUMAN-VERIFY-001.md"
    runs_dir = config.runs_dir / task.metadata.task_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    patch_path = runs_dir / "review-001.patch"
    patch_path.write_text(
        "diff --git a/app.txt b/app.txt\n"
        "index ce01362..2ee250a 100644\n"
        "--- a/app.txt\n"
        "+++ b/app.txt\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+review me\n"
    )
    task.metadata.integration.patch_path = str(patch_path)
    MetadataStore().save(task.task_dir, task.metadata)
    verifying = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.HUMAN_VERIFYING)
    verifying.metadata.state = TaskState.HUMAN_VERIFYING
    uploads: list[str] = []

    def fake_call(method: str, *, token: str, body=None):
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    def fake_upload(*, token: str, channel_id: str, thread_ts: str, filename: str, title: str, content: bytes):
        uploads.append(filename)
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_upload_file_to_thread", fake_upload)

    notifier = SlackMilestoneNotifier(config, MetadataStore())
    notifier.notify_transition(verifying, previous_state=TaskState.COMPLETED_REVIEWS, by="human")
    notifier.notify_transition(verifying, previous_state=TaskState.COMPLETED_REVIEWS, by="human")

    assert uploads == [
        "HUMAN-VERIFY-001.md",
        "CHANGED-FILES-001.md",
        "review-001.patch",
    ]


def test_slack_notifier_adds_start_verification_button(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-start-verification-task")
    task = KanbanScanner(config).scan()[0]
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    MetadataStore().save(task.task_dir, task.metadata)
    completed = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.COMPLETED_REVIEWS)
    completed.metadata.state = TaskState.COMPLETED_REVIEWS
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True, "ts": "173.789", "channel": "C123"}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(completed, previous_state=TaskState.REVIEWING, by="reviewer")

    assert calls
    payload = calls[0][2]
    assert payload is not None
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["text"]["text"] == "🔍 *AI review passed*"
    elements = blocks[-1]["elements"]
    assert isinstance(elements, list)
    assert len(elements) == 1
    assert elements[0]["action_id"] == "start_verification"


def test_slack_notifier_clears_start_verification_buttons_when_human_verification_starts(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-clear-start-button-task")
    task = KanbanScanner(config).scan()[0]
    task.metadata.slack.thread_ts = "173.456"
    task.metadata.slack.channel = "C123"
    task.metadata.slack.action_message_ts["start_verification"] = "173.400"
    task.metadata.slack.action_message_text["start_verification"] = "🔍 AI review passed"
    MetadataStore().save(task.task_dir, task.metadata)
    verifying = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.HUMAN_VERIFYING)
    verifying.metadata.state = TaskState.HUMAN_VERIFYING
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        if method == "chat.postMessage":
            return {"ok": True, "ts": "173.789", "channel": "C123"}
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(verifying, previous_state=TaskState.COMPLETED_REVIEWS, by="human")

    assert len(calls) >= 2
    assert calls[0][0] == "chat.postMessage"
    assert calls[1][0] == "chat.update"
    update_payload = calls[1][2]
    assert update_payload is not None
    assert update_payload["ts"] == "173.400"
    assert update_payload["blocks"] == []


def test_slack_notifier_leaves_thread_empty_when_parent_ts_missing(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-thread-missing-ts-task")
    task = KanbanScanner(config).scan()[0]
    waiting = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.WAITING_REVIEWS)
    waiting.metadata.state = TaskState.WAITING_REVIEWS

    def fake_call(method: str, *, token: str, body=None):
        return {"ok": True, "channel": "C123"}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config, MetadataStore()).notify_transition(waiting, previous_state=TaskState.IMPLEMENTING, by="implementer")

    assert waiting.metadata.slack.thread_ts is None
    persisted = MetadataStore().load(waiting.task_dir)
    assert persisted.slack.thread_ts is None
