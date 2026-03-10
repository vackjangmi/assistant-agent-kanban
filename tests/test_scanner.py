from __future__ import annotations

from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.scanner import KanbanScanner

from .conftest import create_request_task


def test_scanner_bootstraps_metadata(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "login-refactor")
    scanner = KanbanScanner(config)

    tasks = scanner.scan()

    assert len(tasks) == 1
    task = tasks[0]
    assert task.state == TaskState.REQUESTS
    assert task.metadata.task_id.startswith("TASK-")
    assert (task.task_dir / "metadata.json").exists()
