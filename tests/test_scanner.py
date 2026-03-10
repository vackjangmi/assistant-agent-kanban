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
    assert len(task.metadata.task_id) == 7
    assert task.task_dir.name == task.metadata.task_id
    assert (task.task_dir / "metadata.json").exists()


def test_scanner_bootstraps_target_repo_from_request(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "another-repo"
    target_repo.mkdir()
    create_request_task(config, "targeted-task", target_repo_root=target_repo, base_branch="develop")

    task = KanbanScanner(config).scan()[0]

    assert task.metadata.target.repo_root == str(target_repo.resolve())
    assert task.metadata.target.base_branch == "develop"
    assert task.metadata.integration.base_branch == "develop"


def test_scanner_renames_generic_request_directory_to_task_key(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "task")

    task = KanbanScanner(config).scan()[0]

    assert task.task_dir.name == task.metadata.task_id
    assert not (config.state_dir(TaskState.REQUESTS) / "task").exists()
