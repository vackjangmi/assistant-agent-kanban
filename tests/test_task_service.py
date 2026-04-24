from assistant_agent_kanban.metadata_store import MetadataStore
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

    assert filename == f"{task.metadata.task_id}-summary.md"
    summary_text = content.decode("utf-8")
    assert "## Changed Files (0)" in summary_text
    assert "`app.txt` — modified" not in summary_text
