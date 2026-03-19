from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from pathlib import Path
import pytest

from fs_kanban_agent.exceptions import AdapterRunError
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workers.planner import PlanningWorker
from fs_kanban_agent.models import RunResult, utc_now

from .conftest import FakeAdapter, create_request_task


PLAN_ARTIFACT_RE = re.compile(r"<plan-artifact-path>\s*(.*?)\s*</plan-artifact-path>", re.DOTALL)


class PlannerArtifactAdapter(FakeAdapter):
    def run(self, **kwargs):
        prompt = kwargs["prompt"]
        match = PLAN_ARTIFACT_RE.search(prompt)
        if match:
            artifact_path = Path(match.group(1).strip())
            if not artifact_path.is_absolute():
                artifact_path = Path(kwargs["cwd"]) / artifact_path
            if self.responses:
                artifact_content = self.responses[0]
            elif self._last_response is not None:
                artifact_content = self._last_response
            else:
                artifact_content = f"{kwargs['agent']}: ok"
            artifact_path.write_text(artifact_content + "\n")
        return super().run(**kwargs)


def test_planner_worker_generates_plan(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = PlannerArtifactAdapter(["bootstrap", "## Summary\nplan"], resolved_models=["openai/gpt-5.4"], session_ids=["ses_plan_bootstrap"], total_tokens=[41])
    worker = PlanningWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=adapter,
    )

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    assert task.state == TaskState.WAITING_CHECK_PLANS
    assert (task.task_dir / "PLAN.md").exists()
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"] == "## Summary\nplan"
    assert plan_json["resolved_model"] == "openai/gpt-5.4"
    assert plan_json["session_id"] == "ses_plan_bootstrap"
    assert plan_json["total_tokens"] == 41
    assert plan_json["markdown_path"] == "PLAN.md"
    assert plan_json["sync_policy"] == "markdown_edits_do_not_modify_json"
    assert task.metadata.plan.resolved_model == "openai/gpt-5.4"
    assert task.metadata.plan.session_id == "ses_plan_bootstrap"
    assert task.metadata.plan.last_run_tokens == 41
    assert len(adapter.run_calls) == 2
    assert adapter.run_calls[0]["output_format"] == "json"
    assert adapter.run_calls[1]["output_format"] == "default"
    assert adapter.run_calls[1]["stream_stderr_to_log"] is True
    assert adapter.run_calls[1]["show_thinking"] is True
    assert adapter.run_calls[1]["session_id"] == "ses_plan_bootstrap"
    assert adapter.run_calls[0]["prompt"] != adapter.run_calls[1]["prompt"]
    assert "Do not produce a plan." in str(adapter.run_calls[0]["prompt"])
    assert "## Planner Context Docs" in str(adapter.run_calls[1]["prompt"])


def test_planner_worker_pins_runtime_backend_and_models(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "codex"
    config.codex.planner_model = "gpt-5.4"
    config.codex.implementer_model = "gpt-5.3-codex"
    config.codex.reviewer_model = "gpt-5.4"
    config.codex.commit_model = "gpt-5.3-codex"
    create_request_task(config, "planner-runtime-pin-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    codex_adapter = PlannerArtifactAdapter(["## Summary\nplan"], resolved_models=["gpt-5.4"])
    worker = PlanningWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=codex_adapter,
        adapter_registry={"opencode": PlannerArtifactAdapter(), "codex": codex_adapter},
    )

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    assert task.metadata.runtime_pin is not None
    assert task.metadata.runtime_pin.backend == "codex"
    assert task.metadata.runtime_pin.planner_model == "gpt-5.4"
    assert task.metadata.runtime_pin.implementer_model == "gpt-5.3-codex"


def test_planner_worker_reuses_session_under_budget_and_tracks_tokens(configured_paths):
    config, _, _ = configured_paths
    config.opencode.planner_session_token_budget = 250000
    create_request_task(config, "planner-session-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.plan.session_id = "ses_plan_1"
    task.metadata.plan.session_tokens = 90000
    metadata_store.save(task.task_dir, task.metadata)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = PlannerArtifactAdapter(["hello", "## Summary\nplan"], session_ids=["ses_plan_1"], total_tokens=[4200])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert adapter.run_calls[0]["session_id"] == "ses_plan_1"
    assert adapter.run_calls[1]["session_id"] == "ses_plan_1"
    assert updated.metadata.plan.session_id == "ses_plan_1"
    assert updated.metadata.plan.last_run_tokens == 4200
    assert updated.metadata.plan.session_tokens == 94200


def test_planner_worker_persists_bootstrap_metadata_but_finalizes_plan_from_live_run(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-hybrid-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = PlannerArtifactAdapter(["bootstrap run", "## Summary\nlive plan"], resolved_models=["openai/gpt-5.4"], session_ids=["ses_hybrid"], total_tokens=[88])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())

    assert plan_json["assistant_text"] == "## Summary\nlive plan"
    assert plan_json["session_id"] == "ses_hybrid"
    assert plan_json["total_tokens"] == 88
    assert (config.runs_dir / task.metadata.task_id / "planner-001-bootstrap.jsonl").exists()
    assert (config.runs_dir / task.metadata.task_id / "planner-001.jsonl").exists()


def test_planner_worker_uses_verified_draft_instead_of_live_stdout(configured_paths):
    class ContaminatedLiveAdapter(PlannerArtifactAdapter):
        def run(self, **kwargs):
            prompt = kwargs["prompt"]
            match = PLAN_ARTIFACT_RE.search(prompt)
            if match:
                Path(match.group(1).strip()).write_text("## Summary\nclean plan\n")
                self.run_calls.append(
                    {
                        "agent": kwargs["agent"],
                        "prompt": prompt,
                        "cwd": kwargs["cwd"],
                        "run_log_path": kwargs["run_log_path"],
                        "session_id": kwargs.get("session_id"),
                        "cancel_key": kwargs.get("cancel_key"),
                        "output_format": kwargs.get("output_format", "json"),
                        "stream_stderr_to_log": kwargs.get("stream_stderr_to_log", False),
                        "show_thinking": kwargs.get("show_thinking", False),
                    }
                )
                content = "Thinking: hidden\n## Summary\nnoisy stdout"
                kwargs["run_log_path"].parent.mkdir(parents=True, exist_ok=True)
                kwargs["run_log_path"].write_text(content + "\n")
                if kwargs.get("on_log_line") is not None:
                    kwargs["on_log_line"](content, content)
                return RunResult(
                    ok=True,
                    returncode=0,
                    assistant_text=content,
                    stdout=content,
                    stderr="",
                    raw_events_path=str(kwargs["run_log_path"]),
                    command=[kwargs["agent"]],
                    resolved_model=None,
                    session_id=kwargs.get("session_id"),
                    total_tokens=0,
                )
            return super().run(**kwargs)

    config, _, _ = configured_paths
    create_request_task(config, "planner-draft-source-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = ContaminatedLiveAdapter(["hello", "placeholder"])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    task = scanner.scan()[0]
    assert (task.task_dir / "PLAN.md").read_text() == "## Summary\nclean plan\n"
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"] == "## Summary\nclean plan"
    assert "Thinking:" in plan_json["stdout"]


def test_planner_worker_uses_minimal_bootstrap_prompt_before_full_live_prompt(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-bootstrap-prompt-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = PlannerArtifactAdapter(["hello", "## Summary\nplan"])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    bootstrap_prompt = str(adapter.run_calls[0]["prompt"])
    live_prompt = str(adapter.run_calls[1]["prompt"])
    assert "Do not analyze the request yet." in bootstrap_prompt
    assert "Do not produce a plan." in bootstrap_prompt
    assert "## Planner Context Docs" not in bootstrap_prompt
    assert "<task-document>" not in bootstrap_prompt
    assert "## Planner Context Docs" in live_prompt
    assert "<task-document>" in live_prompt
    assert "<plan-artifact-path>" in live_prompt
    assert str((config.runs_dir / scanner.scan()[0].metadata.task_id / "planner-001-draft.md").resolve()) in live_prompt


def test_planner_worker_rolls_over_session_after_budget_is_exceeded(configured_paths):
    config, _, _ = configured_paths
    config.opencode.planner_session_token_budget = 100000
    create_request_task(config, "planner-session-budget-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.plan.session_id = "ses_plan_1"
    task.metadata.plan.session_tokens = 120000
    metadata_store.save(task.task_dir, task.metadata)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = PlannerArtifactAdapter(["hello", "## Summary\nplan"], session_ids=["ses_plan_2"], total_tokens=[3200])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert adapter.run_calls[0]["session_id"] is None
    assert updated.metadata.plan.session_id == "ses_plan_2"
    assert updated.metadata.plan.last_run_tokens == 3200
    assert updated.metadata.plan.session_tokens == 3200


def test_planner_markdown_edits_do_not_modify_plan_json(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-edit-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=PlannerArtifactAdapter(["hello", "## Summary\noriginal plan"]))

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
        adapter=PlannerArtifactAdapter(["bootstrap", ""], ok=True, returncode=0),
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
    adapter = PlannerArtifactAdapter(["hello", "## Summary\nplan"])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is False
    assert adapter.responses == ["hello", "## Summary\nplan"]


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
    adapter = PlannerArtifactAdapter(["hello", "## Summary\nplan"])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is False
    pending_task = scanner.scan()[0]
    assert pending_task.state == TaskState.REQUESTS
    assert adapter.responses == ["hello", "## Summary\nplan"]


def test_planner_worker_offloads_adapter_run_to_thread(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "planner-thread-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=PlannerArtifactAdapter(["hello", "## Summary\nplan"]))
    called = {"value": False}

    async def fake_to_thread(func, /, *args, **kwargs):
        called["value"] = True
        return func(*args, **kwargs)

    monkeypatch.setattr("fs_kanban_agent.workers.planner.asyncio.to_thread", fake_to_thread)

    assert asyncio.run(worker.run_once()) is True
    assert called["value"] is True


def test_planner_worker_includes_request_language_in_prompt(configured_paths):
    class PromptCapturingAdapter(PlannerArtifactAdapter):
        def __init__(self):
            super().__init__(["hello", "## Summary\nplan"])
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


def test_planner_worker_runs_from_project_repo_for_runtime_artifacts(configured_paths):
    class CwdCapturingAdapter(PlannerArtifactAdapter):
        def __init__(self):
            super().__init__(["hello", "## Summary\nplan"])
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
    assert adapter.cwd == config.repo_root.resolve()


def test_planner_worker_uses_updated_request_metadata_after_request_completion(configured_paths, tmp_path):
    class CwdCapturingAdapter(PlannerArtifactAdapter):
        def __init__(self):
            super().__init__(["hello", "## Summary\nplan"])
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
    assert adapter.cwd == config.repo_root.resolve()


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
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, event_bus, adapter=PlannerArtifactAdapter(["hello", "## Summary\nplan"]))

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())

    assert event is not None
    assert event.task_id is not None
    assert event.payload["log_name"].startswith("planner-")
    assert event.payload["rendered_content"] == "## Summary\n\nplan"
    assert event.payload["debug_rendered_content"] == "## Summary\n\nplan"
