from __future__ import annotations

from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
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

    SlackMilestoneNotifier(config).notify_transition(planning, previous_state=TaskState.PLANNING, by="planner", note="plan ready")

    assert calls
    method, token, body = calls[0]
    assert method == "chat.postMessage"
    assert token == "xoxb-test"
    assert body is not None
    text = str(body["text"])
    assert body["channel"] == "#agent-alerts"
    assert "Plan ready for review" in text
    assert planning.metadata.task_id in text


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

    SlackMilestoneNotifier(config).notify_transition(moved, previous_state=TaskState.REQUESTS, by="planner")

    assert calls == []


def test_slack_notifier_handles_plan_approving_review_milestone(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    config.slack.enabled = True
    config.slack.bot_token = "xoxb-test"
    config.slack.default_channel = "#agent-alerts"
    create_request_task(config, "slack-plan-approval-task")
    task = KanbanScanner(config).scan()[0]
    waiting = TaskContext(metadata=task.metadata, task_dir=task.task_dir, state=TaskState.WAITING_CHECK_PLANS)
    waiting.metadata.state = TaskState.WAITING_CHECK_PLANS
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_call(method: str, *, token: str, body=None):
        calls.append((method, token, body))
        return {"ok": True}

    monkeypatch.setattr("assistant_agent_kanban.slack_notifications.slack_api_call", fake_call)

    SlackMilestoneNotifier(config).notify_transition(waiting, previous_state=TaskState.PLAN_APPROVING, by="plan_approval", note="plan review required")

    assert calls
    payload = calls[0][2]
    assert payload is not None
    assert "Plan ready for review" in str(payload["text"])
