from __future__ import annotations

from fastapi.testclient import TestClient

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.services.archive_service import ArchiveService
from assistant_agent_kanban.transitions import TransitionManager

from .conftest import FakeAdapter, create_request_task


def _move_requests_to_done(config, *names: str):
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    for name in names:
        create_request_task(config, name)
    done_tasks = []
    for name in names:
        task = next(task for task in scanner.scan() if task.metadata.title == name)
        done_tasks.append(transitions.recover_move(task, TaskState.DONE, by="test"))
    return scanner, locks, done_tasks


def test_archive_service_moves_done_group_out_of_active_board(configured_paths):
    config, _, _ = configured_paths
    scanner, locks, done_tasks = _move_requests_to_done(config, "archive-one", "archive-two")
    service = ArchiveService(config, scanner, locks)

    manifest = service.archive_done_group(str(config.repo_root), "main", by="human")

    assert manifest.task_count == 2
    done_column = next(column for column in scanner.board_snapshot().columns if column.state == TaskState.DONE)
    assert done_column.items == []
    archives = service.list_groups()
    assert len(archives.groups) == 1
    assert archives.groups[0].task_count == 2
    detail = service.get_group(manifest.archive_id)
    assert {task.task_id for task in detail.tasks} == {task.metadata.task_id for task in done_tasks}
    archived = scanner.find_task(done_tasks[0].metadata.task_id)
    assert archived.state == TaskState.DONE
    assert config.archives_dir in archived.task_dir.parents


def test_archive_api_lists_groups_and_keeps_archived_task_details_available(configured_paths):
    config, _, _ = configured_paths
    config.runtime.auto_dispatch = False
    scanner, _, done_tasks = _move_requests_to_done(config, "api-archive-one", "api-archive-two")
    app = create_app(config, FakeAdapter(["plan"]), FakeAdapter(["impl"]), FakeAdapter(["Verdict: PASS"]))

    with TestClient(app) as client:
        archived = client.post(
            "/api/archives",
            json={"target_repo_root": str(config.repo_root), "base_branch": "main"},
        )
        assert archived.status_code == 200
        archive_id = archived.json()["archive_id"]

        board = client.get("/api/board")
        assert board.status_code == 200
        done_items = next(column["items"] for column in board.json()["columns"] if column["state"] == "done")
        assert done_items == []

        archives = client.get("/api/archives")
        assert archives.status_code == 200
        assert archives.json()["groups"][0]["archive_id"] == archive_id

        detail = client.get(f"/api/archives/{archive_id}")
        assert detail.status_code == 200
        assert {task["task_id"] for task in detail.json()["tasks"]} == {task.metadata.task_id for task in done_tasks}

        task_detail = client.get(f"/api/tasks/{done_tasks[0].metadata.task_id}")
        assert task_detail.status_code == 200
        assert task_detail.json()["metadata"]["state"] == "done"

    assert scanner.find_task(done_tasks[1].metadata.task_id).state == TaskState.DONE
