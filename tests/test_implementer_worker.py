from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import timedelta
from pathlib import Path

from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.models import utc_now
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workspace_manager import WorkspaceManager
from fs_kanban_agent.workers.implementer import ImplementerWorker

from .conftest import FakeAdapter, create_request_task, init_git_repo


def test_implementer_worker_uses_external_workspace(configured_paths):
    config, _, _ = configured_paths
    task_dir = create_request_task(config, "implement-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace, resolved_models=["openai/gpt-5.4"]),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.WAITING_REVIEWS
    assert updated.metadata.cycle == 1
    assert updated.metadata.implementation.iteration == 1
    assert updated.metadata.implementation.workspace is not None
    assert str(task_dir) not in updated.metadata.implementation.workspace
    assert (updated.task_dir / "WORK-001.md").exists()
    work_json = json.loads((updated.task_dir / "WORK-001.json").read_text())
    assert work_json["assistant_text"] == "## Summary\nimplemented"
    assert work_json["resolved_model"] == "openai/gpt-5.4"
    assert updated.metadata.implementation.resolved_model == "openai/gpt-5.4"


def test_implementer_worker_persists_and_reuses_session_id(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implement-session-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    adapter = FakeAdapter(
        ["## Summary\nimplemented once", "## Summary\nimplemented twice"],
        side_effect=modify_workspace,
        session_ids=["ses_impl_1", "ses_impl_1"],
    )
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=adapter,
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    first_pass = scanner.scan()[0]
    assert first_pass.metadata.implementation.session_id == "ses_impl_1"
    assert adapter.run_calls[0]["session_id"] is None

    waiting_review = first_pass
    assert waiting_review.state == TaskState.WAITING_REVIEWS
    reviewing = transitions.move(waiting_review, TaskState.REVIEWING, by="reviewer")
    back_to_todos = transitions.move(reviewing, TaskState.TODOS, by="reviewer", note="review needs changes")
    metadata_store.save(back_to_todos.task_dir, back_to_todos.metadata)

    assert asyncio.run(worker.run_once()) is True
    second_pass = scanner.scan()[0]
    assert second_pass.metadata.implementation.session_id == "ses_impl_1"
    assert adapter.run_calls[1]["session_id"] == "ses_impl_1"


def test_implementer_worker_rolls_over_session_after_budget_is_exceeded(configured_paths):
    config, _, _ = configured_paths
    config.opencode.implementer_session_token_budget = 100
    create_request_task(config, "implement-session-budget-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    adapter = FakeAdapter(
        ["## Summary\nimplemented once", "## Summary\nimplemented twice"],
        side_effect=modify_workspace,
        session_ids=["ses_impl_1", "ses_impl_2"],
        total_tokens=[120, 30],
    )
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=adapter,
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    first_pass = scanner.scan()[0]
    assert first_pass.metadata.implementation.session_id == "ses_impl_1"
    assert first_pass.metadata.implementation.last_run_tokens == 120
    assert first_pass.metadata.implementation.session_tokens == 120

    reviewing = transitions.move(first_pass, TaskState.REVIEWING, by="reviewer")
    back_to_todos = transitions.move(reviewing, TaskState.TODOS, by="reviewer", note="review needs changes")
    metadata_store.save(back_to_todos.task_dir, back_to_todos.metadata)

    assert asyncio.run(worker.run_once()) is True
    second_pass = scanner.scan()[0]
    assert adapter.run_calls[1]["session_id"] is None
    assert second_pass.metadata.implementation.session_id == "ses_impl_2"
    assert second_pass.metadata.implementation.last_run_tokens == 30
    assert second_pass.metadata.implementation.session_tokens == 30


def test_implementer_worker_clones_task_target_repo(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    (target_repo / "target.txt").write_text("from target\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "target.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "add target file"], check=True, capture_output=True, text=True)

    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "target-repo-task", target_repo_root=target_repo)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd):
        (cwd / "target.txt").write_text("changed target\n")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    workspace_repo = scanner.scan()[0].metadata.implementation.workspace
    assert workspace_repo is not None
    assert Path(workspace_repo, "target.txt").read_text() == "changed target\n"


def test_implementer_worker_supports_named_base_branch_in_cloned_workspace(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    (target_repo / "branch.txt").write_text("main branch\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "branch.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "add branch marker"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "checkout", "-b", "v1.0.8"], check=True, capture_output=True, text=True)
    (target_repo / "branch.txt").write_text("release branch\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "branch.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "release branch commit"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "checkout", "main"], check=True, capture_output=True, text=True)

    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "named-base-branch-task", target_repo_root=target_repo, base_branch="v1.0.8")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd):
        assert (cwd / "branch.txt").read_text() == "release branch\n"
        (cwd / "branch.txt").write_text("release branch updated\n")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.WAITING_REVIEWS
    workspace_repo = Path(updated.metadata.implementation.workspace or "")
    assert (workspace_repo / "branch.txt").read_text() == "release branch updated\n"


def test_implementer_worker_returns_to_todos_when_no_workspace_changes(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implement-noop-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["## Summary\nimplemented"]),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.cycle == 1
    assert updated.metadata.implementation.iteration == 1
    assert updated.metadata.implementation.last_result == "failure"
    assert any(error.code == "implementation-no-changes" for error in updated.metadata.errors)
    assert updated.metadata.retry_gate.reason == "implementation-no-changes"
    assert updated.metadata.retry_gate.not_before is not None


def test_implementer_worker_restarts_from_latest_base_on_workspace_sync_conflict(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    (target_repo / "app.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "add app file"], check=True, capture_output=True, text=True)

    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "implement-conflict-task", target_repo_root=target_repo)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    workspace_manager = WorkspaceManager(config)
    workspace_repo = workspace_manager.prepare(todo.metadata)
    Path(workspace_repo, "app.txt").write_text("workspace change\n")
    Path(workspace_repo, "stale-only.txt").write_text("stale\n")
    (target_repo / "app.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "upstream change"], check=True, capture_output=True, text=True)

    def modify_workspace(cwd):
        assert (cwd / "app.txt").read_text() == "upstream change\n"
        assert (cwd / "stale-only.txt").exists() is False
        (cwd / "app.txt").write_text("fresh implementation\n")

    adapter = FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace)
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=adapter,
        workspace_manager=workspace_manager,
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.WAITING_REVIEWS
    assert updated.metadata.cycle == 1
    assert updated.metadata.implementation.last_result == "success"
    assert any(error.code == "implementation-base-sync-conflict" for error in updated.metadata.errors)
    assert updated.metadata.retry_gate.reason is None
    assert updated.metadata.retry_gate.not_before is None
    assert Path(updated.metadata.implementation.workspace or "", "app.txt").read_text() == "fresh implementation\n"
    assert adapter.responses == []


def test_workspace_manager_refreshes_existing_workspace_with_relative_kanban_root(monkeypatch, tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    (target_repo / "app.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "add app file"], check=True, capture_output=True, text=True)
    monkeypatch.chdir(tmp_path)

    config = AppConfig(kanban_root=Path(".kanban-agent"), repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "implement-relative-refresh-task", target_repo_root=target_repo)
    task = KanbanScanner(config, MetadataStore()).scan()[0]
    workspace_manager = WorkspaceManager(config)

    workspace_repo = workspace_manager.prepare(task.metadata)
    Path(workspace_repo, "local.txt").write_text("local change\n")
    (target_repo / "upstream.txt").write_text("upstream change\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "upstream.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "add upstream file"], check=True, capture_output=True, text=True)

    refreshed_repo = workspace_manager.prepare(task.metadata)

    assert Path(refreshed_repo, "local.txt").read_text() == "local change\n"
    assert Path(refreshed_repo, "upstream.txt").read_text() == "upstream change\n"


def test_implementer_worker_skips_retry_gated_todos(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implement-gated-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todo.metadata.retry_gate.reason = "implementation-no-changes"
    todo.metadata.retry_gate.consecutive_count = 1
    todo.metadata.retry_gate.not_before = utc_now() + timedelta(minutes=5)
    metadata_store.save(todo.task_dir, todo.metadata)

    adapter = FakeAdapter(["## Summary\nimplemented"])
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=adapter,
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is False
    assert adapter.responses == ["## Summary\nimplemented"]


def test_implementer_worker_emits_realtime_worker_log_events(configured_paths):
    async def receive_worker_log(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log":
                return event

    config, _, _ = configured_paths
    create_request_task(config, "implement-log-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    event_bus = EventBus()
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        event_bus,
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())

    assert event is not None
    assert event.task_id is not None
    assert event.payload["log_name"].startswith("implementer-")
    assert event.payload["rendered_content"] == "## Summary\n\nimplemented"
    assert event.payload["debug_rendered_content"] == "## Summary\n\nimplemented"
