from __future__ import annotations

import json
from datetime import timedelta

from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import utc_now
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.task_inspection_service import TaskInspectionService
from assistant_agent_kanban.transitions import TransitionManager

from .conftest import FakeAdapter, create_request_task


def test_task_inspection_reports_active_worker_and_workspace_changes(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "inspect-active-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(scanner.find_task(waiting.metadata.task_id), TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.lease.owner = "implementer"
    implementing.metadata.lease.run_id = "implementer-run-1"
    implementing.metadata.lease.heartbeat_at = utc_now()
    implementing.metadata.implementation.workspace = str(repo_root)
    (repo_root / "app.txt").write_text("changed\n")
    metadata_store.save(implementing.task_dir, implementing.metadata)

    inspection = TaskInspectionService(config=config, scanner=scanner).inspect(implementing.metadata.task_id)

    assert inspection.health == "active"
    assert inspection.lease_owner == "implementer"
    assert inspection.workspace_change_count == 1
    assert inspection.workspace_changes == [" M app.txt"]


def test_task_inspection_keeps_active_worker_active_with_recorded_retry_reason(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "inspect-active-retry-reason-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(scanner.find_task(waiting.metadata.task_id), TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.lease.owner = "implementer"
    implementing.metadata.lease.run_id = "implementer-run-1"
    implementing.metadata.lease.heartbeat_at = utc_now()
    implementing.metadata.retry_gate.reason = "review-needs-changes"
    implementing.metadata.retry_gate.not_before = None
    metadata_store.save(implementing.task_dir, implementing.metadata)

    inspection = TaskInspectionService(config=config, scanner=scanner).inspect(implementing.metadata.task_id)

    assert inspection.health == "active"
    retry_signal = next(signal for signal in inspection.signals if signal.label == "Retry gate")
    assert retry_signal.tone == "neutral"
    assert "not currently blocking" in retry_signal.detail


def test_task_inspection_marks_future_retry_gate_as_blocked_without_worker(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "inspect-blocked-retry-gate-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    planning.metadata.retry_gate.reason = "implementation-failed"
    planning.metadata.retry_gate.not_before = utc_now() + timedelta(minutes=5)
    metadata_store.save(planning.task_dir, planning.metadata)

    inspection = TaskInspectionService(config=config, scanner=scanner).inspect(planning.metadata.task_id)

    assert inspection.health == "blocked"
    retry_signal = next(signal for signal in inspection.signals if signal.label == "Retry gate")
    assert retry_signal.tone == "warning"
    assert "Automatic dispatch is paused" in retry_signal.detail


def test_task_inspection_marks_stale_lease(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "inspect-stale-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    planning.metadata.lease.owner = "planner"
    planning.metadata.lease.run_id = "planner-run-1"
    planning.metadata.lease.heartbeat_at = utc_now() - timedelta(seconds=config.locks.stale_after_seconds + 10)
    metadata_store.save(planning.task_dir, planning.metadata)

    inspection = TaskInspectionService(config=config, scanner=scanner).inspect(planning.metadata.task_id)

    assert inspection.health == "stale"
    assert inspection.lease_age_seconds is not None
    assert inspection.lease_age_seconds > config.locks.stale_after_seconds


def test_task_inspection_keeps_stale_health_when_only_runtime_log_is_recent(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    create_request_task(config, "inspect-log-active-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(scanner.find_task(waiting.metadata.task_id), TaskState.IMPLEMENTING, by="implementer")
    implementing.metadata.lease.owner = "implementer"
    implementing.metadata.lease.run_id = "implementer-run-1"
    implementing.metadata.lease.heartbeat_at = utc_now() - timedelta(seconds=config.locks.stale_after_seconds + 90)
    metadata_store.save(implementing.task_dir, implementing.metadata)
    log_dir = config.runs_dir / implementing.metadata.task_id
    log_dir.mkdir(parents=True)
    (log_dir / "implementer.jsonl").write_text('{"type":"text","part":{"text":"still working"}}\n')

    inspection = TaskInspectionService(config=config, scanner=scanner).inspect(implementing.metadata.task_id)

    assert inspection.health == "stale"
    assert inspection.last_log_name == "implementer.jsonl"
    heartbeat_signal = next(signal for signal in inspection.signals if signal.label == "Heartbeat")
    assert heartbeat_signal.tone == "danger"


def test_task_inspector_answer_uses_readonly_bundle_cwd(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "inspect-answer-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    adapter = FakeAdapter(["Looks active enough."])
    service = TaskInspectionService(config=config, scanner=scanner, adapter_registry={"opencode": adapter})

    answer = service.answer(task.metadata.task_id, question_id="is-running")

    assert answer.answer == "Looks active enough."
    assert answer.question_id == "is-running"
    assert adapter.run_calls[0]["agent"] == "fs-kanban-inspector"
    assert adapter.run_calls[0]["cwd"] == config.inspections_dir / task.metadata.task_id
    assert "You are read-only" in str(adapter.run_calls[0]["prompt"])
    assert (config.inspections_dir / task.metadata.task_id / "INSPECTION-BUNDLE.md").exists()


def test_task_inspector_reuses_one_session_per_task_with_commit_profile(configured_paths):
    config, _, _ = configured_paths
    config.runtime.role_backends.commit = "codex"
    config.codex.commit_model = "gpt-5.5 (low)"
    config.codex.commit_session_token_budget = 123000
    create_request_task(config, "inspect-session-task")
    scanner = KanbanScanner(config)
    task = scanner.scan()[0]
    adapter = FakeAdapter(
        ["First answer.", "Second answer."],
        resolved_models=["gpt-5.5 (low)", "gpt-5.5 (low)"],
        session_ids=["ses-inspector-task", "ses-inspector-task"],
        total_tokens=[11, 13],
    )
    service = TaskInspectionService(config=config, scanner=scanner, adapter_registry={"codex": adapter})

    first = service.answer(task.metadata.task_id, question_id="is-running")
    second = service.answer(task.metadata.task_id, question="What changed?")

    assert first.session_id == "ses-inspector-task"
    assert second.session_id == "ses-inspector-task"
    assert adapter.run_calls[0]["session_id"] is None
    assert adapter.run_calls[1]["session_id"] == "ses-inspector-task"
    first_run_config = service._inspector_run_config()
    assert first_run_config.backend_for_role("inspector") == "codex"
    assert first_run_config.role_model("inspector") == "gpt-5.5 (low)"
    assert first_run_config.role_session_token_budget("inspector") == 123000
    session_state = json.loads((config.inspections_dir / task.metadata.task_id / "SESSION.json").read_text())
    assert session_state["session_id"] == "ses-inspector-task"
