from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.models import HistoryEntry

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


def test_scanner_preserves_bootstrapped_request_language_after_request_edits(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "stable-language-task", language="ko", body="한국어 요청입니다.")
    scanner = KanbanScanner(config)

    first_task = scanner.scan()[0]
    assert first_task.metadata.request.language == "ko"

    (first_task.task_dir / "REQUEST.md").write_text(
        "\n".join(
            [
                "---",
                "title: stable-language-task",
                "target:",
                f"  repo_root: {config.repo_root}",
                f"  base_branch: {config.base_branch}",
                "---",
                "",
                "# stable-language-task",
                "",
                "This body changed to English later.",
            ]
        )
    )

    rescanned_task = scanner.scan()[0]

    assert rescanned_task.metadata.request.language == "ko"


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


def test_board_snapshot_includes_final_branch_for_done_tasks(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "done-final-branch-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(scanner.find_task(waiting.metadata.task_id), TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    human_verifying = transitions.move(completed, TaskState.HUMAN_VERIFYING, by="human")
    human_verifying.metadata.integration.final_branch = "feature/demo-finish"
    metadata_store.save(human_verifying.task_dir, human_verifying.metadata)
    transitions.move(scanner.find_task(human_verifying.metadata.task_id), TaskState.DONE, by="human")

    snapshot = scanner.board_snapshot()
    done_item = next(item for column in snapshot.columns if column.state == TaskState.DONE for item in column.items)

    assert done_item.final_branch == "feature/demo-finish"


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


def test_board_snapshot_includes_repo_branch_and_total_duration(configured_paths, tmp_path):
    config, _, _ = configured_paths
    target_repo = tmp_path / "sample-repo"
    target_repo.mkdir()
    create_request_task(config, "repo-metadata-task", target_repo_root=target_repo, base_branch="release/v1")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, TaskLockManager(config, metadata_store))
    planning = transitions.move(scanner.scan()[0], TaskState.PLANNING, by="planner")
    now = datetime.now(timezone.utc)
    planning.metadata.history = [
        HistoryEntry(state=TaskState.REQUESTS, entered_at=now - timedelta(minutes=12), by="human"),
        HistoryEntry(state=TaskState.PLANNING, entered_at=now - timedelta(minutes=7), by="planner"),
    ]
    metadata_store.save(planning.task_dir, planning.metadata)

    snapshot = scanner.board_snapshot()
    planning_item = next(item for column in snapshot.columns if column.state == TaskState.PLANNING for item in column.items)

    assert planning_item.target_repo_root == str(target_repo.resolve())
    assert planning_item.target_repo_label == "sample-repo"
    assert planning_item.base_branch == "release/v1"
    assert planning_item.total_duration_ms >= 7 * 60 * 1000 - 2000
    assert planning_item.total_duration_ms < 8 * 60 * 1000
    assert planning_item.current_state_duration_ms >= 7 * 60 * 1000 - 2000


def test_board_snapshot_includes_current_non_agent_stage_duration(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "idle-time-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    now = datetime.now(timezone.utc)
    task = scanner.scan()[0]
    task.metadata.history = [
        HistoryEntry(state=TaskState.REQUESTS, entered_at=now - timedelta(minutes=20), by="human"),
    ]
    metadata_store.save(task.task_dir, task.metadata)

    snapshot = scanner.board_snapshot()
    request_item = next(item for column in snapshot.columns if column.state == TaskState.REQUESTS for item in column.items)

    assert request_item.total_duration_ms >= 20 * 60 * 1000 - 2000
    assert request_item.current_state_duration_ms >= 20 * 60 * 1000 - 2000


def test_scanner_appends_history_when_directory_state_and_metadata_state_diverge(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "state-sync-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    task.metadata.state = TaskState.PLANNING
    task.metadata.history = [HistoryEntry(state=TaskState.PLANNING, entered_at=datetime.now(timezone.utc) - timedelta(minutes=5), by="planner")]
    metadata_store.save(task.task_dir, task.metadata)

    scanned = scanner.scan()[0]

    assert scanned.metadata.state == TaskState.REQUESTS
    assert scanned.metadata.history[-1].state == TaskState.REQUESTS
    assert scanned.metadata.history[-1].by == "scanner"


def test_board_snapshot_does_not_keep_running_time_for_done_tasks(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "done-time-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    now = datetime.now(timezone.utc)
    task_dir = task.task_dir
    done_dir = config.state_dir(TaskState.DONE) / now.astimezone().strftime("%Y") / now.astimezone().strftime("%m") / now.astimezone().strftime("%d") / task_dir.name
    done_dir.parent.mkdir(parents=True, exist_ok=True)
    task_dir.rename(done_dir)
    task.metadata.state = TaskState.DONE
    task.metadata.history = [
        HistoryEntry(state=TaskState.PLANNING, entered_at=now - timedelta(minutes=15), by="planner"),
        HistoryEntry(state=TaskState.DONE, entered_at=now - timedelta(minutes=4), by="human"),
    ]
    metadata_store.save(done_dir, task.metadata)

    snapshot = scanner.board_snapshot()
    done_item = next(item for column in snapshot.columns if column.state == TaskState.DONE for item in column.items)

    assert done_item.total_duration_ms >= 11 * 60 * 1000 - 2000
    assert done_item.total_duration_ms < 12 * 60 * 1000
    assert done_item.current_state_duration_ms == 0


def test_scanner_discovers_nested_done_tasks(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "nested-done-scan-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    task = scanner.scan()[0]
    nested_done_dir = config.state_dir(TaskState.DONE) / "2026" / "03" / "15" / task.metadata.task_id
    nested_done_dir.parent.mkdir(parents=True, exist_ok=True)
    task.task_dir.rename(nested_done_dir)
    task.metadata.state = TaskState.DONE
    metadata_store.save(nested_done_dir, task.metadata)

    scanned = scanner.find_task(task.metadata.task_id)

    assert scanned.state == TaskState.DONE
    assert scanned.task_dir == nested_done_dir


def test_scanner_ignores_runtime_metadata_when_collecting_task_ids(configured_paths):
    config, _, _ = configured_paths
    create_request_task(config, "runtime-ignore-task")
    runtime_metadata = config.kanban_root / "_runtime" / "workspaces" / "fake-task" / "metadata.json"
    runtime_metadata.parent.mkdir(parents=True, exist_ok=True)
    runtime_metadata.write_text(
        """{
  "version": 1,
  "task_id": "runtime1",
  "title": "runtime metadata",
  "slug": "runtime-metadata",
  "state": "todos",
  "created_at": "2026-03-10T00:00:00Z",
  "updated_at": "2026-03-10T00:00:00Z",
  "request": {"path": "REQUEST.md"},
  "target": {"repo_root": ".", "base_branch": "main"},
  "plan": {"revision": 0, "approved": false, "path": null},
  "implementation": {"iteration": 0, "workspace": null, "branch": null, "last_result": null},
  "review": {"iteration": 0, "last_verdict": null},
  "integration": {"applied": false, "base_branch": "main", "base_commit": null, "patch_path": null, "applied_at": null},
  "commit": {"status": "pending", "sha": null, "message_path": null},
  "lease": {"owner": null, "run_id": null, "heartbeat_at": null},
  "history": [],
  "errors": []
}
"""
    )

    tasks = KanbanScanner(config, MetadataStore()).scan()

    assert len(tasks) == 1
    assert tasks[0].metadata.task_id != "runtime1"
