import json

from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.models import TaskRuntimePin, TaskRuntimeRoleBackends
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.task_service import TaskService

from .conftest import create_request_task


def test_task_service_summary_prefers_empty_target_repo_diff_over_patch_fallback(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-empty-diff-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
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
    metadata_store.save(task.task_dir, task.metadata)

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    filename, content = artifact.build_target_repo_summary_artifact(task)

    assert filename == artifact.target_repo_summary_filename(task.metadata)
    summary_text = content.decode("utf-8")
    assert "## Changed Files (0)" in summary_text
    assert "`app.txt` — modified" not in summary_text


def test_task_service_summary_includes_assistant_token_usage(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-token-usage-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = TaskRuntimePin(
        backend="opencode",
        captured_by="test",
        role_backends=TaskRuntimeRoleBackends(
            planner="opencode",
            implementer="claude",
            reviewer="gemini",
        ),
    )
    metadata_store.save(task.task_dir, task.metadata)
    (task.task_dir / "PLAN.json").write_text(
        json.dumps(
            {
                "resolved_model": "gpt-5.5",
                "session_id": "planner-session-1",
                "total_tokens": 100,
            }
        )
    )
    (task.task_dir / "WORK-001.json").write_text(
        json.dumps(
            {
                "resolved_model": "claude-sonnet-4.5",
                "session_id": "implementer-session-1",
                "total_tokens": 200,
            }
        )
    )
    (task.task_dir / "WORK-002.json").write_text(
        json.dumps(
            {
                "resolved_model": "claude-sonnet-4.5",
                "session_id": "implementer-session-2",
                "total_tokens": "1,300",
            }
        )
    )
    (task.task_dir / "REVIEW-001.json").write_text(
        json.dumps(
            {
                "resolved_model": "gemini-2.5-pro",
                "session_id": "reviewer-session-1",
                "total_tokens": 300,
            }
        )
    )

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    _, content = artifact.build_target_repo_summary_artifact(task)

    summary_text = content.decode("utf-8")
    assert "## Assistant Token Usage" in summary_text
    assert "| Runtime Assistant | Used Assistant | Model | Sessions | Input Tokens | Cached Tokens | Output Tokens | Total Tokens |" in summary_text
    assert "| Planner | OpenCode | gpt-5.5 | 1 | unavailable | unavailable | unavailable | 100 |" in summary_text
    assert "| Implementer | Claude Code | claude-sonnet-4.5 | 2 | unavailable | unavailable | unavailable | 1,500 |" in summary_text
    assert "| Reviewer | Gemini CLI | gemini-2.5-pro | 1 | unavailable | unavailable | unavailable | 300 |" in summary_text
    assert summary_text.index("## Assistant Token Usage") < summary_text.index("## Stage Breakdown")


def test_task_service_summary_marks_missing_token_data_unavailable(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-token-unavailable-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = TaskRuntimePin(backend="codex", captured_by="test")
    metadata_store.save(task.task_dir, task.metadata)
    (task.task_dir / "WORK-001.json").write_text(
        json.dumps(
            {
                "resolved_model": "gpt-5-codex",
                "session_id": "implementer-session-1",
            }
        )
    )

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    _, content = artifact.build_target_repo_summary_artifact(task)

    summary_text = content.decode("utf-8")
    assert "| Implementer | Codex CLI | gpt-5-codex | 1 | unavailable | unavailable | unavailable | unavailable |" in summary_text


def test_task_service_summary_escapes_assistant_token_usage_cells(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-token-escaping-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = TaskRuntimePin(backend="opencode", captured_by="test")
    metadata_store.save(task.task_dir, task.metadata)
    (task.task_dir / "PLAN.json").write_text(
        json.dumps(
            {
                "resolved_model": "gpt|5<script>`mini",
                "session_id": "planner-session-1",
                "total_tokens": 100,
            }
        )
    )

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    _, content = artifact.build_target_repo_summary_artifact(task)

    summary_text = content.decode("utf-8")
    assert "gpt\\|5&lt;script&gt;&#96;mini" in summary_text


def test_task_service_summary_includes_rejected_plan_tokens(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-token-rejected-plan-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = TaskRuntimePin(backend="opencode", captured_by="test")
    metadata_store.save(task.task_dir, task.metadata)
    (task.task_dir / "PLAN-REJECTED-001.json").write_text(
        json.dumps(
            {
                "resolved_model": "gpt-5.5",
                "session_id": "planner-rejected-session",
                "total_tokens": 77,
            }
        )
    )

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    _, content = artifact.build_target_repo_summary_artifact(task)

    summary_text = content.decode("utf-8")
    assert "| Planner | OpenCode | gpt-5.5 | 1 | unavailable | unavailable | unavailable | 77 |" in summary_text


def test_task_service_summary_includes_reviewer_qa_and_branch_summary_tokens(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-token-runtime-log-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = TaskRuntimePin(
        backend="opencode",
        captured_by="test",
        planner_model="gpt-5.5",
        role_backends=TaskRuntimeRoleBackends(reviewer="claude"),
    )
    task.metadata.review.qa_resolved_model = "claude-sonnet-4.5"
    metadata_store.save(task.task_dir, task.metadata)
    log_dir = config.runs_dir / task.metadata.task_id
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "reviewer-qa.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"session_id": "reviewer-qa-session-1"}),
                json.dumps({"usage": {"input_tokens": 10, "output_tokens": 5}}),
                json.dumps({"session_id": "reviewer-qa-session-2"}),
                json.dumps({"usage": {"total_tokens": 30}}),
            ]
        )
        + "\n"
    )
    (log_dir / "branch-summary-001.jsonl").write_text(
        json.dumps({"sessionID": "branch-summary-session", "type": "step_finish", "tokens": {"total": 44}}) + "\n"
    )

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    _, content = artifact.build_target_repo_summary_artifact(task)

    summary_text = content.decode("utf-8")
    assert "| Reviewer Q&amp;A | Claude Code | claude-sonnet-4.5 | 2 | 10 (1 unavailable) | unavailable | 5 (1 unavailable) | 45 |" in summary_text
    assert "| Branch Summary | OpenCode | gpt-5.5 | 1 | unavailable | unavailable | unavailable | 44 |" in summary_text


def test_task_service_summary_skips_symlinked_token_artifacts(configured_paths):
    config, repo_root, tmp_path = configured_paths
    create_request_task(config, "summary-token-symlink-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    outside_payload = tmp_path / "outside-plan.json"
    outside_payload.write_text(json.dumps({"resolved_model": "leaked", "session_id": "outside", "total_tokens": 999}))
    (task.task_dir / "PLAN.json").symlink_to(outside_payload)

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    _, content = artifact.build_target_repo_summary_artifact(task)

    summary_text = content.decode("utf-8")
    assert "leaked" not in summary_text
    assert "| unavailable | unavailable | unavailable | 0 | unavailable | unavailable | unavailable | unavailable |" in summary_text


def test_task_service_summary_includes_token_breakdown_from_artifact_stdout(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-token-breakdown-task", target_repo_root=repo_root)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.runtime_pin = TaskRuntimePin(backend="codex", captured_by="test")
    metadata_store.save(task.task_dir, task.metadata)
    (task.task_dir / "WORK-001.json").write_text(
        json.dumps(
            {
                "resolved_model": "gpt-5-codex",
                "session_id": "implementer-session-1",
                "total_tokens": 125,
                "stdout": json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 50,
                            "cached_input_tokens": 25,
                            "output_tokens": 50,
                        },
                    }
                )
                + "\n",
            }
        )
    )

    artifact = TaskService(scanner, config.runs_dir, config.kanban_root, config.archive_runs_dir, metadata_store=metadata_store)
    _, content = artifact.build_target_repo_summary_artifact(task)

    summary_text = content.decode("utf-8")
    assert "| Implementer | Codex CLI | gpt-5-codex | 1 | 50 | 25 | 50 | 100 |" in summary_text
