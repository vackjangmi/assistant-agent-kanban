from __future__ import annotations

import asyncio
import subprocess

from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.events import EventBus
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.transitions import TransitionManager
from fs_kanban_agent.workers.committer import CommitWorker

from .conftest import create_request_task, init_git_repo


def test_committer_worker_creates_final_commit(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "commit-task")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    transitions.manual_move(completed.metadata.task_id, TaskState.INTEGRATION_TEST_COMPLETED, by="human")
    (repo_root / "app.txt").write_text("committed\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "app.txt"], check=True, capture_output=True, text=True)
    worker = CommitWorker(config, scanner, metadata_store, locks, transitions, EventBus())

    assert asyncio.run(worker.run_once()) is True
    done = scanner.scan()[0]
    assert done.state == TaskState.DONE
    assert done.metadata.commit.sha
    assert (done.task_dir / "COMMIT.md").exists()


def test_committer_worker_commits_to_task_target_repo(tmp_path):
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    config = AppConfig(kanban_root=tmp_path / "ai-kanban", repo_root=tmp_path / "unused-default")
    config.bootstrap()
    create_request_task(config, "commit-target-task", target_repo_root=target_repo)
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    transitions = TransitionManager(config, metadata_store, scanner, locks)
    task = scanner.scan()[0]
    planning = transitions.move(task, TaskState.PLANNING, by="planner")
    waiting = transitions.move(planning, TaskState.WAITING_CHECK_PLANS, by="planner")
    todo = transitions.manual_move(waiting.metadata.task_id, TaskState.TODOS, by="human")
    implementing = transitions.move(todo, TaskState.IMPLEMENTING, by="implementer")
    waiting_reviews = transitions.move(implementing, TaskState.WAITING_REVIEWS, by="implementer")
    reviewing = transitions.move(waiting_reviews, TaskState.REVIEWING, by="reviewer")
    completed = transitions.move(reviewing, TaskState.COMPLETED_REVIEWS, by="reviewer")
    transitions.manual_move(completed.metadata.task_id, TaskState.INTEGRATION_TEST_COMPLETED, by="human")
    (target_repo / "app.txt").write_text("committed\n")
    subprocess.run(["git", "-C", str(target_repo), "add", "app.txt"], check=True, capture_output=True, text=True)
    worker = CommitWorker(config, scanner, metadata_store, locks, transitions, EventBus())

    assert asyncio.run(worker.run_once()) is True
    done = scanner.scan()[0]
    assert done.state == TaskState.DONE
    assert done.metadata.commit.sha
