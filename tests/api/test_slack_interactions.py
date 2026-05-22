from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.transitions import TransitionManager

from ..conftest import FakeAdapter, create_request_task


from ._helpers import _task_ready_for_completed_reviews

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

        monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_api_call", fake_call)
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

        monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_api_call", fake_call)
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

        monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_api_call", fake_call)
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

        monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_api_call", fake_call)
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

    monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_upload_file_to_thread", fake_upload)

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

    monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_api_call", fake_call)

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
    view_body = calls[0][2]
    assert isinstance(view_body, dict)
    view = view_body["view"]
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

    monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_api_call", fake_call)
    monkeypatch.setattr("assistant_agent_kanban.runtime._slack.slack_upload_file_to_thread", fake_upload)

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
        opened_view_body = next(body for method, _token, body in calls if method == "views.open")
        assert isinstance(opened_view_body, dict)
        opened_view = opened_view_body["view"]
        assert isinstance(opened_view, dict)
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
