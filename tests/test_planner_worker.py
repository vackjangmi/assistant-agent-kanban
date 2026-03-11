from __future__ import annotations

import asyncio
import json
from datetime import timedelta
import pytest

from fs_kanban_agent.exceptions import AdapterRunError
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workers.planner import PlanningWorker
from fs_kanban_agent.models import utc_now

from .conftest import FakeAdapter, create_request_task


def test_planner_worker_generates_plan(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nplan"], resolved_models=["openai/gpt-5.4"]),
    )

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    assert task.state == TaskState.WAITING_CHECK_PLANS
    assert (task.task_dir / "PLAN.md").exists()
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"] == "## Summary\nplan"
    assert plan_json["resolved_model"] == "openai/gpt-5.4"
    assert plan_json["markdown_path"] == "PLAN.md"
    assert plan_json["sync_policy"] == "markdown_edits_do_not_modify_json"
    assert task.metadata.plan.resolved_model == "openai/gpt-5.4"


def test_planner_markdown_edits_do_not_modify_plan_json(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-edit-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter(["## Summary\noriginal plan"]))

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    plan_md = task.task_dir / "PLAN.md"
    plan_md.write_text("## Summary\nmanual edit\n")

    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"] == "## Summary\noriginal plan"


def test_planner_worker_does_not_advance_on_failed_adapter(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-failure-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["opencode run [message..]"], ok=False, returncode=1, stderr="planner failed"),
    )

    with pytest.raises(AdapterRunError):
        asyncio.run(worker.run_once())

    planning_task = scanner.scan()[0]
    assert planning_task.state == TaskState.PLANNING
    assert not (planning_task.task_dir / "PLAN.md").exists()
    assert not (planning_task.task_dir / "PLAN.json").exists()
    assert planning_task.metadata.errors[-1].code == "planner-run-failed"


def test_planner_worker_does_not_write_tool_only_json_as_plan(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-empty-artifact-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter([""], ok=True, returncode=0),
    )

    with pytest.raises(AdapterRunError, match="markdown artifact"):
        asyncio.run(worker.run_once())

    planning_task = scanner.scan()[0]
    assert planning_task.state == TaskState.PLANNING
    assert not (planning_task.task_dir / "PLAN.md").exists()
    assert not (planning_task.task_dir / "PLAN.json").exists()
    assert planning_task.metadata.errors[-1].code == "planner-empty-artifact"


def test_planner_worker_skips_retry_gated_requests(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-gated-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task_dir = scanner.scan()[0].task_dir
    metadata = metadata_store.load(task_dir)
    metadata.retry_gate.reason = "planner-empty-artifact"
    metadata.retry_gate.consecutive_count = 1
    metadata.retry_gate.not_before = utc_now() + timedelta(minutes=5)
    metadata_store.save(task_dir, metadata)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = FakeAdapter(["## Summary\nplan"])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is False
    assert adapter.responses == ["## Summary\nplan"]


def test_planner_worker_skips_incomplete_requests_without_goal(configured_paths):
    config, _, _ = configured_paths
    task_dir = create_request_task(config, "planner-incomplete-task")
    (task_dir / "REQUEST.md").write_text(
        "\n".join(
            [
                "---",
                "title: planner-incomplete-task",
                "target:",
                f"  repo_root: {config.repo_root}",
                f"  base_branch: {config.base_branch}",
                "---",
                "",
                "# planner-incomplete-task",
                "",
            ]
        )
    )
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = FakeAdapter(["## Summary\nplan"])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is False
    pending_task = scanner.scan()[0]
    assert pending_task.state == TaskState.REQUESTS
    assert adapter.responses == ["## Summary\nplan"]


def test_planner_worker_offloads_adapter_run_to_thread(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "planner-thread-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter(["## Summary\nplan"]))
    called = {"value": False}

    async def fake_to_thread(func, /, *args, **kwargs):
        called["value"] = True
        return func(*args, **kwargs)

    monkeypatch.setattr("fs_kanban_agent.workers.planner.asyncio.to_thread", fake_to_thread)

    assert asyncio.run(worker.run_once()) is True
    assert called["value"] is True


def test_planner_worker_includes_request_language_in_prompt(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(["## Summary\nplan"])
            self.prompt = ""

        def run(self, **kwargs):
            self.prompt = kwargs["prompt"]
            return super().run(**kwargs)

    config, _, _ = configured_paths
    task_dir = create_request_task(config, "planner-korean-task")
    (task_dir / "REQUEST.md").write_text(
        "\n".join(
            [
                "---",
                "title: 한국어 계획",
                "target:",
                f"  repo_root: {config.repo_root}",
                f"  base_branch: {config.base_branch}",
                "---",
                "",
                "# 한국어 계획",
                "",
                "이 문서는 한국어로 결과를 받아야 합니다.",
            ]
        )
    )
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = PromptCapturingAdapter()
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    assert "Return the markdown artifact in Korean." in adapter.prompt
    assert "<task-document>" in adapter.prompt
    assert "## Planner Context Docs" in adapter.prompt
    assert "## docs/01-architecture-review.md" in adapter.prompt
    assert "## docs/02-implementation-plan.md" in adapter.prompt
    assert "## docs/03-agent-task.md" in adapter.prompt


def test_planner_worker_runs_from_target_repo(configured_paths):
    class CwdCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(["## Summary\nplan"])
            self.cwd = None

        def run(self, **kwargs):
            self.cwd = kwargs["cwd"]
            return super().run(**kwargs)

    config, _, _ = configured_paths
    target_repo = config.repo_root.parent / "planner-target-repo"
    target_repo.mkdir()
    create_request_task(config, "planner-cwd-task", target_repo_root=target_repo)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = CwdCapturingAdapter()
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    assert adapter.cwd == target_repo.resolve()


def test_planner_worker_uses_updated_request_metadata_after_request_completion(configured_paths, tmp_path):
    class CwdCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(["## Summary\nplan"])
            self.cwd = None

        def run(self, **kwargs):
            self.cwd = kwargs["cwd"]
            return super().run(**kwargs)

    config, _, _ = configured_paths
    updated_repo = tmp_path / "completed-target-repo"
    updated_repo.mkdir()
    task_dir = create_request_task(config, "planner-refresh-task")
    (task_dir / "REQUEST.md").write_text(
        "\n".join(
            [
                "---",
                "title: planner-refresh-task-updated",
                "target:",
                f"  repo_root: {updated_repo}",
                "  base_branch: feature/late-goal",
                "---",
                "",
                "# planner-refresh-task-updated",
                "",
                "## Goal",
                "Finish the task after manual completion.",
                "",
            ]
        )
    )
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = CwdCapturingAdapter()
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    assert task.metadata.title == "planner-refresh-task-updated"
    assert task.metadata.slug == "planner-refresh-task-updated"
    assert task.metadata.target.repo_root == str(updated_repo.resolve())
    assert task.metadata.target.base_branch == "feature/late-goal"
    assert task.metadata.integration.base_branch == "feature/late-goal"
    assert adapter.cwd == updated_repo.resolve()


def test_planner_worker_emits_realtime_worker_log_events(configured_paths):
    async def receive_worker_log(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log":
                return event

    config, _, _ = configured_paths
    create_request_task(config, "planner-log-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    event_bus = EventBus()
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, event_bus, adapter=FakeAdapter(["## Summary\nplan"]))

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())

    assert event is not None
    assert event.task_id is not None
    assert event.payload["log_name"].startswith("planner-")
    assert event.payload["raw_line"] == "## Summary\nplan"
    assert event.payload["content"] == "## Summary\nplan\n"
    assert event.payload["rendered_content"] == "## Summary\n\nplan"
