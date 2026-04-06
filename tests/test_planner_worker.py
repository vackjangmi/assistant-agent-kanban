from __future__ import annotations

import asyncio
import json
from datetime import timedelta
import pytest

from assistant_agent_kanban.exceptions import AdapterRunError
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.events import EventBus
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.transitions import TransitionManager
from assistant_agent_kanban.workers.planner import PlanningWorker
from assistant_agent_kanban.models import utc_now

from .conftest import FakeAdapter, create_request_task


def valid_plan_artifact(summary: str = "plan", *, language: str = "en") -> str:
    if language == "ko":
        return "\n".join(
            [
                "## 요약",
                summary,
                "",
                "## 범위",
                "- 범위 항목",
                "",
                "## 범위 외",
                "- 범위 외 항목",
                "",
                "## 파일 맵",
                "- `lib/models.dart`: 점수 계산",
                "",
                "## 단계별 계획",
                "1. 점수 규칙 수정",
                "",
                "## 검증 계획",
                "- 관련 테스트 실행",
                "",
                "## 승인 기준",
                "- 요청 요구사항 충족",
                "",
                "## 리스크",
                "- 점수 회귀 가능성",
                "",
                "## 열린 질문",
                "- 없음",
            ]
        )
    return "\n".join(
        [
            "## Summary",
            summary,
            "",
            "## Scope",
            "- Scope item",
            "",
            "## Out of Scope",
            "- Out of scope item",
            "",
            "## File Map",
            "- `lib/models.dart`: scoring logic",
            "",
            "## Step-by-step Plan",
            "1. Update scoring rules",
            "",
            "## Validation Plan",
            "- Run targeted tests",
            "",
            "## Acceptance Criteria",
            "- Request requirements are satisfied",
            "",
            "## Risks",
            "- Possible scoring regressions",
            "",
            "## Open Questions",
            "- None",
        ]
    )


def planner_cycle_responses(greeting: str = "hello", live: str = "live planning", artifact: str | None = None) -> list[str]:
    if artifact is None:
        artifact = valid_plan_artifact("plan")
    return [greeting, live, artifact]


def fenced_markdown(value: str, *, info: str = "markdown") -> str:
    return f"```{info}\n{value}\n```"


def test_planner_worker_generates_plan(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.plan_approval.attempt_count = 2
    task.metadata.plan_approval.last_attempt_plan_revision = 1
    task.metadata.plan_approval.last_retry_reason = "approval_output_invalid"
    task.metadata.plan_approval.attempts = [{"attempt": 1}, {"attempt": 2}]
    metadata_store.save(task.task_dir, task.metadata)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    artifact = valid_plan_artifact("plan")
    adapter = FakeAdapter(planner_cycle_responses(artifact=artifact), resolved_models=["openai/gpt-5.4", "openai/gpt-5.4"], session_ids=["ses_plan_bootstrap", "ses_plan_bootstrap", "ses_plan_bootstrap"], total_tokens=[20, 0, 21])
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
    assert task.state == TaskState.PLAN_APPROVING
    assert (task.task_dir / "PLAN.md").exists()
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"] == artifact
    assert plan_json["resolved_model"] == "openai/gpt-5.4"
    assert plan_json["session_id"] == "ses_plan_bootstrap"
    assert plan_json["total_tokens"] == 21
    assert plan_json["markdown_path"] == "PLAN.md"
    assert plan_json["sync_policy"] == "markdown_edits_do_not_modify_json"
    assert task.metadata.plan.resolved_model == "openai/gpt-5.4"
    assert task.metadata.plan.session_id == "ses_plan_bootstrap"
    assert task.metadata.plan.last_run_tokens == 21
    assert task.metadata.plan_approval.attempt_count == 0
    assert task.metadata.plan_approval.last_retry_reason is None
    assert task.metadata.plan_approval.attempts == []
    assert len(adapter.run_calls) == 3
    assert adapter.run_calls[0]["output_format"] == "json"
    assert adapter.run_calls[1]["output_format"] == "default"
    assert adapter.run_calls[1]["stream_stderr_to_log"] is True
    assert adapter.run_calls[1]["show_thinking"] is True
    assert adapter.run_calls[1]["session_id"] == "ses_plan_bootstrap"
    assert adapter.run_calls[2]["output_format"] == "json"
    assert adapter.run_calls[0]["prompt"] != adapter.run_calls[1]["prompt"]
    assert "Do not produce a plan yet." in str(adapter.run_calls[0]["prompt"])
    assert "## Planner Context Docs" in str(adapter.run_calls[1]["prompt"])


def test_planner_worker_strips_outer_markdown_fence_before_persisting_plan(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-fenced-plan-task", language="ko")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    artifact = fenced_markdown(valid_plan_artifact("plan", language="ko"))
    adapter = FakeAdapter(
        planner_cycle_responses(artifact=artifact),
        session_ids=["ses_fenced", "ses_fenced", "ses_fenced"],
        total_tokens=[10, 0, 11],
    )
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    task = scanner.scan()[0]
    persisted_markdown = (task.task_dir / "PLAN.md").read_text().strip()
    persisted_json = json.loads((task.task_dir / "PLAN.json").read_text())
    expected = valid_plan_artifact("plan", language="ko")
    assert persisted_markdown == expected
    assert persisted_json["assistant_text"] == expected


def test_planner_worker_pins_runtime_backend_and_models(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "codex"
    config.codex.planner_model = "gpt-5.4"
    config.codex.plan_approval_model = "gpt-5.4"
    config.codex.implementer_model = "gpt-5.3-codex"
    config.codex.reviewer_model = "gpt-5.4"
    config.codex.commit_model = "gpt-5.3-codex"
    create_request_task(config, "planner-runtime-pin-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    codex_adapter = FakeAdapter([valid_plan_artifact("plan")], resolved_models=["gpt-5.4"])
    worker = PlanningWorker(
        config,
        scanner,
        metadata_store,
        locks,
        transitions,
        EventBus(),
        adapter=codex_adapter,
        adapter_registry={"opencode": FakeAdapter(), "codex": codex_adapter},
    )

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    assert task.metadata.runtime_pin is not None
    assert task.metadata.runtime_pin.backend == "codex"
    assert task.metadata.runtime_pin.planner_model == "gpt-5.4"
    assert task.metadata.runtime_pin.plan_approval_model == "gpt-5.4"
    assert task.metadata.runtime_pin.implementer_model == "gpt-5.3-codex"


def test_gemini_planner_forces_multi_phase_path(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "gemini"
    config.gemini.planner_model = "gemini-2.5-flash"
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "planner-gemini-multiphase-task", language="ko")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = FakeAdapter(
        planner_cycle_responses(artifact=valid_plan_artifact("plan", language="ko")),
        session_ids=["ses_gemini", "ses_gemini", "ses_gemini"],
        total_tokens=[10, 0, 11],
    )
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    task = scanner.scan()[0]
    assert task.state == TaskState.PLAN_APPROVING
    assert len(adapter.run_calls) == 3
    assert adapter.run_calls[0]["output_format"] == "json"
    assert adapter.run_calls[1]["output_format"] == "default"
    assert adapter.run_calls[2]["output_format"] == "json"
    assert "# Finalize Plan Artifact" in str(adapter.run_calls[2]["prompt"])


def test_non_gemini_planner_stays_single_shot_when_live_logs_disabled(configured_paths):
    config, _, _ = configured_paths
    config.runtime.coding_assistant = "codex"
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "planner-codex-single-shot-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = FakeAdapter([valid_plan_artifact("plan")], resolved_models=["gpt-5.4"])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    assert len(adapter.run_calls) == 1
    assert adapter.run_calls[0]["output_format"] == "json"


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
    adapter = FakeAdapter(planner_cycle_responses(artifact=valid_plan_artifact("plan")), session_ids=["ses_plan_1", "ses_plan_1", "ses_plan_1"], total_tokens=[2100, 0, 2100])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert adapter.run_calls[0]["session_id"] == "ses_plan_1"
    assert adapter.run_calls[1]["session_id"] == "ses_plan_1"
    assert adapter.run_calls[2]["session_id"] == "ses_plan_1"
    assert updated.metadata.plan.session_id == "ses_plan_1"
    assert updated.metadata.plan.last_run_tokens == 2100
    assert updated.metadata.plan.session_tokens == 94200


def test_planner_worker_finalizes_plan_from_finalize_run(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-hybrid-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    finalized_artifact = valid_plan_artifact("finalized plan")
    adapter = FakeAdapter(planner_cycle_responses(live="live plan logs", artifact=finalized_artifact), resolved_models=["openai/gpt-5.4", "openai/gpt-5.4"], session_ids=["ses_hybrid", "ses_hybrid", "ses_hybrid"], total_tokens=[40, 0, 48])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())

    assert plan_json["assistant_text"] == finalized_artifact
    assert plan_json["session_id"] == "ses_hybrid"
    assert plan_json["total_tokens"] == 48
    assert (config.runs_dir / task.metadata.task_id / "planner.jsonl").exists()


def test_planner_worker_uses_finalize_artifact_instead_of_live_stdout(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-draft-source-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    clean_artifact = valid_plan_artifact("clean plan")
    adapter = FakeAdapter(planner_cycle_responses(live="Thinking: hidden\n## Summary\nnoisy stdout", artifact=clean_artifact), session_ids=["ses_plan", "ses_plan", "ses_plan"], total_tokens=[10, 0, 10])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    task = scanner.scan()[0]
    assert (task.task_dir / "PLAN.md").read_text() == clean_artifact + "\n"
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"] == clean_artifact
    assert "Thinking:" not in plan_json["assistant_text"]


def test_planner_worker_uses_handshake_and_finalize_prompts_around_live_prompt(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-bootstrap-prompt-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = FakeAdapter(planner_cycle_responses(artifact=valid_plan_artifact("plan")))
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    bootstrap_prompt = str(adapter.run_calls[0]["prompt"])
    live_prompt = str(adapter.run_calls[1]["prompt"])
    finalize_prompt = str(adapter.run_calls[2]["prompt"])
    assert "Do not analyze the request yet." in bootstrap_prompt
    assert "Do not produce a plan yet." in bootstrap_prompt
    assert "## Planner Context Docs" not in bootstrap_prompt
    assert "<task-document>" not in bootstrap_prompt
    assert "## Planner Context Docs" in live_prompt
    assert "<task-document>" in live_prompt
    assert "Finalize Plan Artifact" in finalize_prompt
    assert "Return only the final markdown artifact" in finalize_prompt


def test_planner_worker_includes_restart_message_in_planner_source_text(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-restart-note-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    task.metadata.plan.restart_message_path = "PLANNER-RESTART.md"
    (task.task_dir / "PLANNER-RESTART.md").write_text(
        "# Planner Restart Notes\n\n## Note 1\n- Source: manual planner restart\n\nRetry with the exact required headings.\n"
    )
    metadata_store.save(task.task_dir, task.metadata)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter())

    source = worker._planner_source_text((task.task_dir / "REQUEST.md").read_text(), task.metadata)

    assert "## Planner Restart Note" in source
    assert "Retry with the exact required headings." in source
    assert "## Planner Context Docs" in source


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
    adapter = FakeAdapter(planner_cycle_responses(artifact=valid_plan_artifact("plan")), session_ids=["ses_plan_2", "ses_plan_2", "ses_plan_2"], total_tokens=[1600, 0, 1600])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True
    updated = scanner.scan()[0]
    assert adapter.run_calls[0]["session_id"] is None
    assert updated.metadata.plan.session_id == "ses_plan_2"
    assert updated.metadata.plan.last_run_tokens == 1600
    assert updated.metadata.plan.session_tokens == 3200


def test_planner_worker_uses_single_json_run_when_live_logs_disabled(configured_paths):
    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "planner-single-run-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    artifact = valid_plan_artifact("single plan")
    adapter = FakeAdapter([artifact], session_ids=["ses_single_plan"], total_tokens=[77])
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    task = scanner.scan()[0]
    assert len(adapter.run_calls) == 1
    assert adapter.run_calls[0]["output_format"] == "json"
    assert adapter.run_calls[0]["show_thinking"] is False
    assert (task.task_dir / "PLAN.md").read_text() == artifact + "\n"
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["session_id"] == "ses_single_plan"
    assert plan_json["total_tokens"] == 77


def test_planner_markdown_edits_do_not_modify_plan_json(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-edit-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    original_artifact = valid_plan_artifact("original plan")
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter(planner_cycle_responses(artifact=original_artifact)))

    assert asyncio.run(worker.run_once()) is True
    task = scanner.scan()[0]
    plan_md = task.task_dir / "PLAN.md"
    plan_md.write_text(valid_plan_artifact("manual edit") + "\n")

    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"] == original_artifact


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
    assert planning_task.state == TaskState.REQUESTS
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
        adapter=FakeAdapter(planner_cycle_responses(artifact=""), ok=True, returncode=0),
    )

    with pytest.raises(AdapterRunError, match="markdown artifact"):
        asyncio.run(worker.run_once())

    planning_task = scanner.scan()[0]
    assert planning_task.state == TaskState.REQUESTS
    assert not (planning_task.task_dir / "PLAN.md").exists()
    assert not (planning_task.task_dir / "PLAN.json").exists()
    assert planning_task.metadata.errors[-1].code == "planner-empty-artifact"


def test_planner_worker_rejects_malformed_nonempty_plan_artifact(configured_paths):
    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "planner-invalid-artifact-task")
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
        adapter=FakeAdapter(["했습니다."], ok=True, returncode=0),
    )

    with pytest.raises(AdapterRunError, match="missing required section"):
        asyncio.run(worker.run_once())

    planning_task = scanner.scan()[0]
    assert planning_task.state == TaskState.REQUESTS
    assert not (planning_task.task_dir / "PLAN.md").exists()
    assert not (planning_task.task_dir / "PLAN.json").exists()
    assert planning_task.metadata.errors[-1].code == "planner-invalid-artifact"
    assert planning_task.metadata.retry_gate.reason == "planner-invalid-artifact"
    assert planning_task.metadata.retry_gate.not_before is None


def test_planner_worker_ignores_heading_false_positives_inside_fenced_code_blocks(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-fenced-heading-false-positive-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter())
    artifact = "\n".join(
        [
            "```md",
            "## Summary",
            "This heading is inside a code fence and must not count.",
            "```",
            "",
            "## Scope",
            "- Scope item",
            "",
            "## Out of Scope",
            "- Out of scope item",
            "",
            "## File Map",
            "- `lib/models.dart`: scoring logic",
            "",
            "## Step-by-step Plan",
            "1. Update scoring rules",
            "",
            "## Validation Plan",
            "- Run targeted tests",
            "",
            "## Acceptance Criteria",
            "- Request requirements are satisfied",
            "",
            "## Risks",
            "- Possible scoring regressions",
            "",
            "## Open Questions",
            "- None",
        ]
    )

    validated, missing_marker = worker._validated_plan_artifact(artifact, task.metadata)

    assert validated is None
    assert missing_marker == "## Summary"


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
    artifact = valid_plan_artifact("plan")
    adapter = FakeAdapter(planner_cycle_responses(artifact=artifact))
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is False
    assert adapter.responses == planner_cycle_responses(artifact=artifact)


def test_planner_finalize_prompt_includes_exact_localized_heading_skeleton(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-heading-prompt-task", language="ko")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter([]))

    task = scanner.scan()[0]
    request_text = (task.task_dir / "REQUEST.md").read_text()
    prompt = worker._finalize_prompt(request_text, task.metadata)

    assert "Use the exact required headings below, in this exact order." in prompt
    assert "## 요약" in prompt
    assert "## 범위" in prompt
    assert "## 범위 외" in prompt
    assert "## 파일 맵" in prompt
    assert "## 단계별 계획" in prompt
    assert "## 검증 계획" in prompt
    assert "## 승인 기준" in prompt
    assert "## 리스크" in prompt
    assert "## 열린 질문" in prompt


def test_planner_worker_uses_repair_result_metadata_when_repair_succeeds(configured_paths):
    class RepairingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                responses=["hello", "live planning", "했습니다.", valid_plan_artifact("repaired plan")],
                session_ids=["ses_plan", "ses_plan", "ses_plan", "ses_plan"],
                total_tokens=[10, 0, 11, 12],
            )
            self.ok_values = [True, True, True, True]
            self.returncodes = [0, 0, 0, 0]
            self.stderrs = ["", "", "", ""]
            self.raw_suffixes = ["handshake", "live", "finalize", "repair"]
            self.commands = [["handshake"], ["live"], ["finalize"], ["repair"]]

        def run(self, **kwargs):
            result = super().run(**kwargs)
            result.ok = self.ok_values.pop(0)
            result.returncode = self.returncodes.pop(0)
            result.stderr = self.stderrs.pop(0)
            result.raw_events_path = self.raw_suffixes.pop(0)
            result.command = self.commands.pop(0)
            return result

    config, _, _ = configured_paths
    create_request_task(config, "planner-repair-success-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = RepairingAdapter()
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    task = scanner.scan()[0]
    plan_json = json.loads((task.task_dir / "PLAN.json").read_text())
    assert plan_json["assistant_text"].startswith("## Summary")
    assert plan_json["total_tokens"] == 12
    assert plan_json["raw_events_path"] == "repair"
    assert plan_json["command"] == ["repair"]
    assert task.metadata.plan.session_tokens == 33


def test_planner_worker_clears_restart_message_pointer_after_success(configured_paths):
    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "planner-restart-pointer-reset-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.plan.restart_message_path = "PLANNER-RESTART.md"
    (task.task_dir / "PLANNER-RESTART.md").write_text(
        "# Planner Restart Notes\n\n## Note 1\n- Source: manual planner restart\n\nRetry with exact headings.\n"
    )
    metadata_store.save(task.task_dir, task.metadata)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter([valid_plan_artifact("plan")]))

    assert asyncio.run(worker.run_once()) is True

    updated = scanner.scan()[0]
    assert updated.metadata.plan.restart_message_path is None


def test_planner_worker_counts_live_tokens_in_multi_phase_flow(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-live-token-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    adapter = FakeAdapter(
        planner_cycle_responses(artifact=valid_plan_artifact("plan")),
        session_ids=["ses_plan", "ses_plan", "ses_plan"],
        total_tokens=[10, 7, 11],
    )
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is True

    task = scanner.scan()[0]
    assert task.metadata.plan.session_tokens == 28


def test_planner_invalid_artifact_cools_down_after_second_consecutive_failure(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "planner-invalid-artifact-repeat-task")
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
        adapter=FakeAdapter(["했습니다."], ok=True, returncode=0),
    )

    with pytest.raises(AdapterRunError, match="missing required section"):
        asyncio.run(worker.run_once())
    first_task = scanner.scan()[0]
    assert first_task.state == TaskState.REQUESTS
    assert first_task.metadata.retry_gate.reason == "planner-invalid-artifact"
    assert first_task.metadata.retry_gate.consecutive_count == 1
    assert first_task.metadata.retry_gate.not_before is None

    with pytest.raises(AdapterRunError, match="missing required section"):
        asyncio.run(worker.run_once())
    second_task = scanner.scan()[0]
    assert second_task.state == TaskState.REQUESTS
    assert second_task.metadata.retry_gate.reason == "planner-invalid-artifact"
    assert second_task.metadata.retry_gate.consecutive_count == 2
    assert second_task.metadata.retry_gate.not_before is not None


def test_planner_worker_marks_failed_finalize_as_run_failure(configured_paths):
    class FinalizeFailureAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(responses=["hello", "live planning", "planner finalize failed"]) 
            self.ok_values = [True, True, False]
            self.returncodes = [0, 0, 1]
            self.stderrs = ["", "", "planner finalize failed"]

        def run(self, **kwargs):
            result = super().run(**kwargs)
            result.ok = self.ok_values.pop(0)
            result.returncode = self.returncodes.pop(0)
            result.stderr = self.stderrs.pop(0)
            return result

    config, _, _ = configured_paths
    create_request_task(config, "planner-finalize-failure-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FinalizeFailureAdapter())

    with pytest.raises(AdapterRunError, match="planner finalize failed"):
        asyncio.run(worker.run_once())

    task = scanner.scan()[0]
    assert task.state == TaskState.REQUESTS
    assert task.metadata.errors[-1].code == "planner-run-failed"
    assert task.metadata.retry_gate.reason == "planner-run-failed"


def test_planner_worker_marks_failed_repair_as_run_failure(configured_paths):
    class RepairFailureAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(responses=["hello", "live planning", "했습니다.", "repair failed"])
            self.ok_values = [True, True, True, False]
            self.returncodes = [0, 0, 0, 1]
            self.stderrs = ["", "", "", "repair failed"]

        def run(self, **kwargs):
            result = super().run(**kwargs)
            result.ok = self.ok_values.pop(0)
            result.returncode = self.returncodes.pop(0)
            result.stderr = self.stderrs.pop(0)
            return result

    config, _, _ = configured_paths
    create_request_task(config, "planner-repair-failure-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=RepairFailureAdapter())

    with pytest.raises(AdapterRunError, match="repair failed"):
        asyncio.run(worker.run_once())

    task = scanner.scan()[0]
    assert task.state == TaskState.REQUESTS
    assert task.metadata.errors[-1].code == "planner-run-failed"
    assert task.metadata.retry_gate.reason == "planner-run-failed"


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
    artifact = valid_plan_artifact("plan")
    adapter = FakeAdapter(planner_cycle_responses(artifact=artifact))
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=adapter)

    assert asyncio.run(worker.run_once()) is False
    pending_task = scanner.scan()[0]
    assert pending_task.state == TaskState.REQUESTS
    assert adapter.responses == planner_cycle_responses(artifact=artifact)


def test_planner_worker_offloads_adapter_run_to_thread(configured_paths, monkeypatch):
    config, _, _ = configured_paths
    create_request_task(config, "planner-thread-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, EventBus(), adapter=FakeAdapter(planner_cycle_responses(artifact=valid_plan_artifact("plan"))))
    called = {"value": False}

    async def fake_to_thread(func, /, *args, **kwargs):
        called["value"] = True
        return func(*args, **kwargs)

    monkeypatch.setattr("assistant_agent_kanban.workers.planner.asyncio.to_thread", fake_to_thread)

    assert asyncio.run(worker.run_once()) is True
    assert called["value"] is True


def test_planner_worker_includes_request_language_in_prompt(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(planner_cycle_responses(artifact=valid_plan_artifact("plan", language="ko")))
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
    class CwdCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(planner_cycle_responses(artifact=valid_plan_artifact("plan")))
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
    class CwdCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(planner_cycle_responses(artifact=valid_plan_artifact("plan")))
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
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, event_bus, adapter=FakeAdapter(planner_cycle_responses(artifact=valid_plan_artifact("plan"))))

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())

    assert event is not None
    assert event.task_id is not None
    assert event.payload["log_name"] == "planner.jsonl"
    assert event.payload["rendered_delta"] == "live planning"
    assert event.payload["debug_rendered_delta"] == "live planning"
    assert event.payload["rendered_content"] == "live planning"
    assert event.payload["debug_rendered_content"] == "live planning"


def test_planner_worker_announces_log_file(configured_paths):
    async def receive_worker_log_file(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log_file":
                return event

    config, _, _ = configured_paths
    create_request_task(config, "planner-log-file-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    event_bus = EventBus()
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, event_bus, adapter=FakeAdapter(planner_cycle_responses(artifact=valid_plan_artifact("plan"))))

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log_file(event_bus))
        await asyncio.sleep(0)
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())
    assert event is not None
    assert event.payload["log_name"] == "planner.jsonl"


def test_planner_worker_emits_realtime_worker_log_events_when_live_logs_disabled(configured_paths):
    async def receive_worker_log(event_bus):
        async for event in event_bus.subscribe():
            if event.event == "worker_log":
                return event

    config, _, _ = configured_paths
    config.opencode.worker_live_logs_enabled = False
    create_request_task(config, "planner-log-default-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    event_bus = EventBus()
    default_artifact = valid_plan_artifact("plan")
    worker = PlanningWorker(config, scanner, metadata_store, locks, transitions, event_bus, adapter=FakeAdapter([default_artifact]))

    async def scenario():
        event_task = asyncio.create_task(receive_worker_log(event_bus))
        await asyncio.sleep(0)
        await worker.run_once()
        return await asyncio.wait_for(event_task, timeout=1)

    event = asyncio.run(scenario())
    assert event is not None
    assert event.payload["log_name"] == "planner.jsonl"
    assert event.payload["rendered_content"] == default_artifact
