from __future__ import annotations

from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.locks import TaskLockManager

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


def test_scanner_detects_request_language_from_request_markdown(configured_paths):
    config, _, _ = configured_paths
    task_dir = create_request_task(config, "language-task")
    (task_dir / "REQUEST.md").write_text(
        "\n".join(
            [
                "---",
                "title: 언어 테스트",
                "target:",
                f"  repo_root: {config.repo_root}",
                f"  base_branch: {config.base_branch}",
                "---",
                "",
                "# 언어 테스트",
                "",
                "이 요청은 한국어로 작성되었습니다.",
            ]
        )
    )

    task = KanbanScanner(config).scan()[0]

    assert task.metadata.request.language == "ko"


def test_scanner_refreshes_request_metadata_after_initial_bootstrap(configured_paths, tmp_path):
    config, _, _ = configured_paths
    initial_repo = tmp_path / "initial-repo"
    updated_repo = tmp_path / "updated-repo"
    initial_repo.mkdir()
    updated_repo.mkdir()
    create_request_task(config, "refresh-task", target_repo_root=initial_repo, base_branch="main")
    scanner = KanbanScanner(config)

    first_task = scanner.scan()[0]
    assert first_task.metadata.target.repo_root == str(initial_repo.resolve())

    (first_task.task_dir / "REQUEST.md").write_text(
        "\n".join(
            [
                "---",
                "title: refresh-task-updated",
                "target:",
                f"  repo_root: {updated_repo}",
                "  base_branch: feature/rescanned",
                "---",
                "",
                "# refresh-task-updated",
                "",
                "## Goal",
                "Apply the updated request metadata.",
                "",
            ]
        )
    )

    refreshed_task = scanner.scan()[0]

    assert refreshed_task.metadata.title == "refresh-task-updated"
    assert refreshed_task.metadata.slug == "refresh-task-updated"
    assert refreshed_task.metadata.target.repo_root == str(updated_repo.resolve())
    assert refreshed_task.metadata.target.base_branch == "feature/rescanned"
    assert refreshed_task.metadata.integration.base_branch == "feature/rescanned"


def test_board_snapshot_includes_active_state_entered_at(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "active-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")

    snapshot = scanner.board_snapshot()
    planning_item = next(item for column in snapshot.columns if column.state == TaskState.PLANNING for item in column.items)

    assert planning_item.task_id == planning.metadata.task_id
    assert planning_item.state_entered_at is not None
