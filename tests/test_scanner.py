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


def test_scanner_backfills_cycle_from_legacy_iterations(tmp_path):
    from fs_kanban_agent.config import AppConfig

    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.bootstrap()
    task_dir = config.state_dir(TaskState.TODOS) / "legacy-task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# legacy\n")
    (task_dir / "metadata.json").write_text(
        """{
  "version": 1,
  "task_id": "legacy1",
  "title": "legacy task",
  "slug": "legacy-task",
  "state": "todos",
  "created_at": "2026-03-10T00:00:00Z",
  "updated_at": "2026-03-10T00:00:00Z",
  "request": {"path": "REQUEST.md"},
  "target": {"repo_root": ".", "base_branch": "main"},
  "plan": {"revision": 0, "approved": false, "path": null},
  "implementation": {"iteration": 2, "workspace": null, "branch": null, "last_result": null},
  "review": {"iteration": 1, "last_verdict": null},
  "integration": {"applied": false, "base_branch": "main", "base_commit": null, "patch_path": null, "applied_at": null},
  "commit": {"status": "pending", "sha": null, "message_path": null},
  "lease": {"owner": null, "run_id": null, "heartbeat_at": null},
  "history": [],
  "errors": []
}
"""
    )

    task = KanbanScanner(config, MetadataStore()).scan()[0]

    assert task.metadata.cycle == 2


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


def test_board_snapshot_derives_agent_activity_from_state_and_lease(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "agent-activity-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")
    planning.metadata.lease.owner = "planner"
    planning.metadata.lease.run_id = "planner-run-1"
    metadata_store.save(planning.task_dir, planning.metadata)

    snapshot = scanner.board_snapshot()
    planning_item = next(item for column in snapshot.columns if column.state == TaskState.PLANNING for item in column.items)

    assert planning_item.agent_status == "active"
    assert planning_item.agent_owner == "planner"


def test_board_snapshot_marks_active_state_without_lease_as_waiting(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "agent-waiting-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    transitions.move(scanner.find_task(waiting.metadata.task_id), TaskState.IMPLEMENTING, by="implementer")

    snapshot = scanner.board_snapshot()
    implementing_item = next(item for column in snapshot.columns if column.state == TaskState.IMPLEMENTING for item in column.items)

    assert implementing_item.agent_status == "waiting"
    assert implementing_item.agent_owner is None


def test_board_snapshot_keeps_human_verifying_out_of_agent_active(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "human-verifying-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    todos_task = scanner.find_task(waiting.metadata.task_id)
    implementing = transitions.move(todos_task, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    human_verifying = transitions.move(completed, TaskState.HUMAN_VERIFYING, by="human")
    human_verifying.metadata.lease.owner = "human"
    metadata_store.save(human_verifying.task_dir, human_verifying.metadata)

    snapshot = scanner.board_snapshot()
    human_item = next(item for column in snapshot.columns if column.state == TaskState.HUMAN_VERIFYING for item in column.items)

    assert human_item.agent_status == "idle"
