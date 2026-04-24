from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import timedelta
from pathlib import Path

from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.exceptions import WorkspaceSyncError
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import RunResult, utc_now
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.task_service import TaskService
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workspace_manager import WorkspaceManager
from assistant_agent_kanban.workers.implementer import ImplementerWorker

from .conftest import FakeAdapter, create_request_task, init_git_repo


def implementer_cycle_responses(greeting: str = "hello", live: str = "implemented live", artifact: str = "## Summary\nimplemented") -> list[str]:
    return [greeting, live, artifact]


class InterruptedThenSuccessAdapter(FakeAdapter):
    def __init__(self, *, side_effect=None) -> None:
        super().__init__(responses=implementer_cycle_responses(), side_effect=side_effect)
        self.calls = 0

    def run(self, **kwargs) -> RunResult:
        if kwargs.get("output_format") == "default":
            self.calls += 1
        if kwargs.get("output_format") == "default" and self.calls == 1:
            run_log_path = kwargs["run_log_path"]
            run_log_path.parent.mkdir(parents=True, exist_ok=True)
            run_log_path.write_text("")
            return RunResult(
                ok=False,
                returncode=-2,
                assistant_text="",
                stdout="",
                stderr="",
                raw_events_path=str(run_log_path),
                command=["implementer"],
                resolved_model="openai/gpt-5.3-codex",
                session_id=None,
                total_tokens=0,
            )
        return super().run(**kwargs)


class HandshakeFailureWithTargetRepoMutationAdapter(FakeAdapter):
    def __init__(self, *, target_repo: Path, side_effect=None) -> None:
        super().__init__(responses=["hello"], side_effect=side_effect)
        self.target_repo = target_repo
        self.calls = 0

    def run(self, **kwargs) -> RunResult:
        self.calls += 1
        if self.calls == 1:
            cwd = kwargs["cwd"]
            if self.side_effect is not None:
                self.side_effect(cwd)
            (self.target_repo / "app.txt").write_text("dirty during handshake\n")
            run_log_path = kwargs["run_log_path"]
            run_log_path.parent.mkdir(parents=True, exist_ok=True)
            run_log_path.write_text("")
            return RunResult(
                ok=False,
                returncode=1,
                assistant_text="",
                stdout="",
                stderr="handshake failed",
                raw_events_path=str(run_log_path),
                command=["implementer"],
                resolved_model="openai/gpt-5.4",
                session_id=None,
                total_tokens=0,
            )
        return super().run(**kwargs)


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

    adapter = FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace, resolved_models=["openai/gpt-5.4", "openai/gpt-5.4"])
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
    assert [call["output_format"] for call in adapter.run_calls] == ["json", "default", "json"]
    assert updated.metadata.implementation.target_repo_baseline is not None
    baseline = updated.metadata.implementation.target_repo_baseline
    assert baseline.base_branch == "main"
    assert baseline.dirty is False
    assert baseline.status_short == ""
    expected_head = subprocess.run(
        ["git", "-C", str(config.repo_root), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert baseline.head_sha == expected_head


def test_implementer_worker_uses_pinned_backend_after_global_change(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "opencode"
    config.opencode.implementer_model = "openai/gpt-5.3-codex"
    create_request_task(config, "implement-pinned-backend-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(task.task_dir, task.metadata)
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    config.runtime.coding_assistant = "codex"
    config.codex.implementer_model = "gpt-5.4"

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    opencode_adapter = FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace, resolved_models=["openai/gpt-5.3-codex", "openai/gpt-5.3-codex"])
    codex_adapter = FakeAdapter(["## Summary\nwrong backend"], side_effect=modify_workspace, resolved_models=["gpt-5.4"])
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=codex_adapter,
        workspace_manager=WorkspaceManager(config),
        adapter_registry={"opencode": opencode_adapter, "codex": codex_adapter},
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert len(opencode_adapter.run_calls) == 3
    assert len(codex_adapter.run_calls) == 0
    assert updated.metadata.implementation.resolved_model == "openai/gpt-5.3-codex"


def test_implementer_worker_uses_current_settings_override_when_requested(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "gemini"
    config.gemini.implementer_model = "gemini-2.5-pro"
    create_request_task(config, "implement-current-settings-override-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(task.task_dir, task.metadata)
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    config.runtime.coding_assistant = "codex"
    config.runtime.role_backends.implementer = "codex"
    config.codex.implementer_model = "gpt-5.4"
    todos.metadata.implementation.resume_mode = "current-settings"
    todos.metadata.implementation.resume_backend_override = "codex"
    todos.metadata.implementation.resume_model_override = "gpt-5.4"
    metadata_store.save(todos.task_dir, todos.metadata)

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    gemini_adapter = FakeAdapter(["## Summary\nwrong backend"], side_effect=modify_workspace, resolved_models=["gemini-2.5-pro"])
    codex_adapter = FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace, resolved_models=["gpt-5.4", "gpt-5.4"])
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=codex_adapter,
        workspace_manager=WorkspaceManager(config),
        adapter_registry={"gemini": gemini_adapter, "codex": codex_adapter},
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert len(codex_adapter.run_calls) == 1
    assert len(gemini_adapter.run_calls) == 0
    assert codex_adapter.run_calls[0]["output_format"] == "json"
    assert updated.metadata.implementation.resolved_model == "gpt-5.4"
    assert updated.metadata.implementation.resume_mode is None
    assert updated.metadata.implementation.resume_backend_override is None
    assert updated.metadata.implementation.resume_model_override is None


def test_implementer_current_settings_override_is_one_shot(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "gemini"
    config.gemini.implementer_model = "gemini-2.5-pro"
    create_request_task(config, "implement-current-settings-one-shot-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = config.capture_runtime_pin(captured_by="planner")
    metadata_store.save(task.task_dir, task.metadata)
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    config.runtime.coding_assistant = "codex"
    config.runtime.role_backends.implementer = "codex"
    config.codex.implementer_model = "gpt-5.4"
    todos.metadata.implementation.resume_mode = "current-settings"
    todos.metadata.implementation.resume_backend_override = "codex"
    todos.metadata.implementation.resume_model_override = "gpt-5.4"
    metadata_store.save(todos.task_dir, todos.metadata)

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    gemini_adapter = FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace, resolved_models=["gemini-2.5-pro", "gemini-2.5-pro"])
    codex_adapter = FakeAdapter(["run failed"], side_effect=modify_workspace, resolved_models=["gpt-5.4"], ok=False)
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=codex_adapter,
        workspace_manager=WorkspaceManager(config),
        adapter_registry={"gemini": gemini_adapter, "codex": codex_adapter},
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.implementation.resume_backend_override is None
    assert updated.metadata.implementation.resume_model_override is None
    updated.metadata.retry_gate.reason = None
    updated.metadata.retry_gate.consecutive_count = 0
    updated.metadata.retry_gate.not_before = None
    metadata_store.save(updated.task_dir, updated.metadata)

    assert asyncio.run(worker.run_once()) is True
    final_task = scanner.scan()[0]
    assert len(codex_adapter.run_calls) == 1
    assert len(gemini_adapter.run_calls) == 1
    assert gemini_adapter.run_calls[0]["output_format"] == "json"
    assert final_task.metadata.implementation.resolved_model == "gemini-2.5-pro"


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
        implementer_cycle_responses(greeting="hello-1", live="live-1", artifact="## Summary\nimplemented once")
        + implementer_cycle_responses(greeting="hello-2", live="live-2", artifact="## Summary\nimplemented twice"),
        side_effect=modify_workspace,
        session_ids=["ses_impl_1", "ses_impl_1", "ses_impl_1", "ses_impl_1", "ses_impl_1", "ses_impl_1"],
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
    assert adapter.run_calls[1]["output_format"] == "default"
    assert adapter.run_calls[2]["output_format"] == "json"

    waiting_review = first_pass
    assert waiting_review.state == TaskState.WAITING_REVIEWS
    reviewing = transitions.move(waiting_review, TaskState.REVIEWING, by="reviewer")
    back_to_todos = transitions.move(reviewing, TaskState.TODOS, by="reviewer", note="review needs changes")
    metadata_store.save(back_to_todos.task_dir, back_to_todos.metadata)

    assert asyncio.run(worker.run_once()) is True
    second_pass = scanner.scan()[0]
    assert second_pass.metadata.implementation.session_id == "ses_impl_1"
    assert adapter.run_calls[3]["session_id"] == "ses_impl_1"


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
        implementer_cycle_responses(greeting="hello-1", live="live-1", artifact="## Summary\nimplemented once")
        + implementer_cycle_responses(greeting="hello-2", live="live-2", artifact="## Summary\nimplemented twice"),
        side_effect=modify_workspace,
        session_ids=["ses_impl_1", "ses_impl_1", "ses_impl_1", "ses_impl_2", "ses_impl_2", "ses_impl_2"],
        total_tokens=[80, 0, 40, 20, 0, 10],
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
    assert first_pass.metadata.implementation.last_run_tokens == 40
    assert first_pass.metadata.implementation.session_tokens == 120

    reviewing = transitions.move(first_pass, TaskState.REVIEWING, by="reviewer")
    back_to_todos = transitions.move(reviewing, TaskState.TODOS, by="reviewer", note="review needs changes")
    metadata_store.save(back_to_todos.task_dir, back_to_todos.metadata)

    assert asyncio.run(worker.run_once()) is True
    second_pass = scanner.scan()[0]
    assert adapter.run_calls[3]["session_id"] is None
    assert second_pass.metadata.implementation.session_id == "ses_impl_2"
    assert second_pass.metadata.implementation.last_run_tokens == 10
    assert second_pass.metadata.implementation.session_tokens == 30


def test_implementer_source_includes_latest_reviewer_qa(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implementer-reviewer-qa-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    (planning.task_dir / "REVIEW-001.md").write_text("Verdict: NEEDS_CHANGES\n\n- tighten the copy\n")
    (planning.task_dir / "REVIEWER-QA-001.md").write_text("# Reviewer Q&A\n\n## Question 1\nCan the label stay?\n\n## Answer 1\nYes, but the helper text should change.\n")
    (planning.task_dir / "HUMAN-VERIFY-001.md").write_text("Please revisit the helper text.\n")
    metadata_store.save(planning.task_dir, planning.metadata)

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["implemented"]),
        workspace_manager=WorkspaceManager(config),
    )

    prompt_source = worker._build_implementer_source(planning.task_dir)

    assert "# Latest AI Review" in prompt_source
    assert "# Latest Reviewer Q&A" in prompt_source
    assert "Can the label stay?" in prompt_source
    assert "Yes, but the helper text should change." in prompt_source
    assert "# Latest Human Verification" in prompt_source


def test_implementer_source_includes_persisted_resume_message_artifact(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implementer-resume-message-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task_service = TaskService(
        scanner,
        config.runs_dir,
        config.kanban_root,
        metadata_store=metadata_store,
        transitions=transitions,
        locks=locks,
    )
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("implement this\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todos = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos.metadata.retry_gate.reason = "implementation-failed"
    todos.metadata.retry_gate.consecutive_count = 1
    todos.metadata.retry_gate.not_before = utc_now()
    metadata_store.save(todos.task_dir, todos.metadata)

    task_service.resume_implementer(
        todos.metadata.task_id,
        by="human",
        message="Please keep the existing structure but fix the missing edge case handling.",
    )

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(["implemented"]),
        workspace_manager=WorkspaceManager(config),
    )

    prompt_source = worker._build_implementer_source(todos.task_dir)

    assert "# Latest Reviewer Q&A" in prompt_source
    assert "- Source: human resume note" in prompt_source
    assert "Please keep the existing structure but fix the missing edge case handling." in prompt_source


def test_implementer_worker_uses_single_json_run_when_live_logs_disabled(configured_paths):
    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "implementer-single-run-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    (planning.task_dir / "PLAN.md").write_text("## Summary\nplan\n")
    metadata_store.save(planning.task_dir, planning.metadata)
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")

    def modify_workspace(cwd: Path) -> None:
        (cwd / "app.txt").write_text("implemented\n")

    adapter = FakeAdapter(
        ["## Summary\nimplemented"],
        side_effect=modify_workspace,
        side_effect_output_formats={"json"},
        session_ids=["ses_impl_single"],
        total_tokens=[55],
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

    updated = scanner.scan()[0]
    assert len(adapter.run_calls) == 1
    assert adapter.run_calls[0]["output_format"] == "json"
    assert updated.state == TaskState.WAITING_REVIEWS
    work_json = json.loads((updated.task_dir / "WORK-001.json").read_text())
    assert work_json["session_id"] == "ses_impl_single"
    assert work_json["total_tokens"] == 55


def test_implementer_worker_clones_task_target_repo(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    (target_repo / "target.txt").write_text("from target\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "target.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "add target file"], check=True, capture_output=True, text=True)

    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.opencode.worker_live_logs_enabled = True
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
        adapter=FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    workspace_repo = scanner.scan()[0].metadata.implementation.workspace
    assert workspace_repo is not None
    assert Path(workspace_repo, "target.txt").read_text() == "changed target\n"


def test_implementer_worker_returns_to_todos_when_target_repo_becomes_dirty(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)

    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.runtime.coding_assistant = "codex"
    config.runtime.role_backends.implementer = "codex"
    config.codex.implementer_model = "gpt-5.5"
    config.bootstrap()
    create_request_task(config, "target-repo-drift-task", target_repo_root=target_repo)
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

    def mutate_workspace_and_target_repo(cwd: Path):
        (cwd / "app.txt").write_text("changed in workspace\n")
        (target_repo / "app.txt").write_text("dirty outside workspace\n")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(
            responses=["implemented live"],
            side_effect=mutate_workspace_and_target_repo,
            side_effect_output_formats={"json"},
            resolved_models=["gpt-5.5"],
        ),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.retry_gate.reason == "implementation-target-repo-drift"
    assert updated.metadata.implementation.last_result == "failure"
    assert any(error.code == "implementation-target-repo-drift" for error in updated.metadata.errors)
    assert list(updated.task_dir.glob("WORK-*.md")) == []
    assert list(updated.task_dir.glob("WORK-*.json")) == []


def test_implementer_worker_detects_target_repo_drift_during_handshake(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.coding_assistant = "opencode"
    config.opencode.worker_live_logs_enabled = True
    create_request_task(config, "implement-handshake-target-drift-task")
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

    def modify_workspace(cwd: Path):
        (cwd / "app.txt").write_text("changed in workspace\n")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=HandshakeFailureWithTargetRepoMutationAdapter(target_repo=repo_root, side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.retry_gate.reason == "implementation-target-repo-drift"
    assert updated.metadata.implementation.last_result == "failure"
    assert any(error.code == "implementation-target-repo-drift" for error in updated.metadata.errors)
    assert list(updated.task_dir.glob("WORK-*.md")) == []
    assert list(updated.task_dir.glob("WORK-*.json")) == []


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
    config.opencode.worker_live_logs_enabled = True
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
        adapter=FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace),
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
        adapter=FakeAdapter(implementer_cycle_responses()),
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


def test_implementer_worker_retries_interrupted_run_once(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implement-interrupted-task")
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
        (cwd / "app.txt").write_text("changed after retry\n")

    adapter = InterruptedThenSuccessAdapter(side_effect=modify_workspace)
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
    updated = scanner.scan()[0]
    assert adapter.calls == 2
    assert updated.state == TaskState.WAITING_REVIEWS
    assert updated.metadata.implementation.last_result == "success"
    assert not any(error.code == "implementation-failed" for error in updated.metadata.errors)


def test_implementer_worker_restarts_from_latest_base_on_workspace_sync_conflict(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    (target_repo / "app.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "app.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-m", "add app file"], check=True, capture_output=True, text=True)

    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "unused-default")
    config.opencode.worker_live_logs_enabled = True
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

    adapter = FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace)
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
    config.opencode.worker_live_logs_enabled = True
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

    adapter = FakeAdapter(implementer_cycle_responses())
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
    assert adapter.responses == implementer_cycle_responses()


def test_implementer_worker_moves_to_implementing_before_workspace_prepare(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implement-state-first-task")
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

    class InspectingWorkspaceManager(WorkspaceManager):
        def prepare(self, metadata):
            current = scanner.find_task(metadata.task_id)
            assert current.state == TaskState.IMPLEMENTING
            return super().prepare(metadata)

    def modify_workspace(cwd):
        (cwd / "app.txt").write_text("changed\n")

    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace),
        workspace_manager=InspectingWorkspaceManager(config),
    )

    assert asyncio.run(worker.run_once()) is True
    assert scanner.find_task(todo.metadata.task_id).state == TaskState.WAITING_REVIEWS


def test_implementer_worker_returns_to_todos_when_workspace_prepare_fails_after_entering_implementing(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "implement-prepare-failure-task")
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

    class FailingWorkspaceManager(WorkspaceManager):
        def prepare(self, metadata):
            raise WorkspaceSyncError("git clone failed")

    event_bus = EventBus()
    task_moved_states = []

    original_publish = event_bus.publish

    async def capture_publish(event):
        if event.event == "task_moved" and event.task_id == todo.metadata.task_id:
            task_moved_states.append(event.payload["state"])
        await original_publish(event)

    event_bus.publish = capture_publish
    adapter = FakeAdapter(implementer_cycle_responses())
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        event_bus,
        adapter=adapter,
        workspace_manager=FailingWorkspaceManager(config),
    )

    result = asyncio.run(worker.run_once())
    updated = scanner.find_task(todo.metadata.task_id)

    assert result is True
    assert task_moved_states == [TaskState.IMPLEMENTING.value, TaskState.TODOS.value]
    assert updated.state == TaskState.TODOS
    assert updated.metadata.implementation.last_result == "failure"
    assert updated.metadata.cycle == 0
    assert updated.metadata.implementation.iteration == 0
    assert updated.metadata.retry_gate.reason == "implementation-base-sync-conflict"
    assert any(error.code == "implementation-base-sync-conflict" for error in updated.metadata.errors)
    assert adapter.run_calls == []
    assert list(updated.task_dir.glob("WORK-*.md")) == []
    assert list(updated.task_dir.glob("WORK-*.json")) == []


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
        adapter=FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())

    assert event is not None
    assert event.task_id is not None
    assert event.payload["log_name"] == "implementer.jsonl"
    assert event.payload["rendered_delta"] == "implemented live"
    assert event.payload["debug_rendered_delta"] == "implemented live"
    assert event.payload["rendered_content"] == "implemented live"
    assert event.payload["debug_rendered_content"] == "implemented live"


def test_implementer_worker_announces_log_file(configured_paths):
    async def receive_worker_log_file(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log_file":
                return event

    config, _, _ = configured_paths
    create_request_task(config, "implement-log-file-task")
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

    def modify_workspace(cwd: Path) -> None:
        (cwd / "app.txt").write_text("implemented\n")

    event_bus = EventBus()
    worker = ImplementerWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        event_bus,
        adapter=FakeAdapter(implementer_cycle_responses(), side_effect=modify_workspace),
        workspace_manager=WorkspaceManager(config),
    )

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log_file(event_bus))
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())
    assert event is not None
    assert event.payload["log_name"] == "implementer.jsonl"


def test_implementer_worker_emits_realtime_worker_log_events_when_live_logs_disabled(configured_paths):
    async def receive_worker_log(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log":
                return event

    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "implement-log-default-task")
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
        adapter=FakeAdapter(["## Summary\nimplemented"], side_effect=modify_workspace, side_effect_output_formats={"json"}),
        workspace_manager=WorkspaceManager(config),
    )

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await asyncio.sleep(0)
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())
    assert event is not None
    assert event.payload["log_name"] == "implementer.jsonl"
    assert event.payload["rendered_content"] == "## Summary\nimplemented"
