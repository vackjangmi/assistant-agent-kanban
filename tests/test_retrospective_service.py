from __future__ import annotations

import subprocess

import pytest

from fs_kanban_agent.commit_manager import CommitManager
from fs_kanban_agent.locks import TaskLockManager
from fs_kanban_agent.metadata_store import MetadataStore
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.exceptions import CommitError
from fs_kanban_agent.services.retrospective_service import RetrospectiveService

from .conftest import FakeAdapter, create_request_task
from .test_human_verification_service import _task_ready_for_human_verification


def _done_task_for_retrospective(config, task_name: str, *, commit_adapter=None):
    create_request_task(config, task_name)
    _, verification_service, completed = _task_ready_for_human_verification(config)
    verification_service.start(completed.metadata.task_id, by="human")
    verification_service.approve(completed.metadata.task_id, by="human", completion_mode="target-branch")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    service = RetrospectiveService(scanner, config, locks, CommitManager(), adapter=commit_adapter)
    return scanner.find_task(completed.metadata.task_id), service


def test_retrospective_service_creates_target_branch_retrospective(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nTarget branch retro\n"], resolved_models=["openai/gpt-5-commit"])
    done, service = _done_task_for_retrospective(config, "retro-target-task", commit_adapter=adapter)

    record = service.create([done.metadata.task_id], by="human", completion_mode="target-branch")

    assert record.exists is True
    assert record.created is True
    assert record.committed_branch == "main"
    assert record.resolved_model == "openai/gpt-5-commit"
    assert record.repo_relative_path is not None
    assert (repo_root / record.repo_relative_path).exists()
    assert (done.task_dir / (record.artifact_filename or "")).exists()
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    assert adapter.run_calls[0]["agent"] == config.opencode.commit_agent


def test_retrospective_service_inspects_existing_group_artifact(configured_paths):
    config, _, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nExisting retro\n"])
    done, service = _done_task_for_retrospective(config, "retro-existing-task", commit_adapter=adapter)
    created = service.create([done.metadata.task_id], by="human", completion_mode="target-branch")

    inspected = service.inspect([done.metadata.task_id])

    assert inspected.exists is True
    assert inspected.created is False
    assert inspected.content == created.content
    assert inspected.commit_sha == created.commit_sha


def test_retrospective_service_creates_new_branch_when_requested(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nBranch retro\n"])
    done, service = _done_task_for_retrospective(config, "retro-branch-task", commit_adapter=adapter)

    record = service.create([done.metadata.task_id], by="human", completion_mode="new-branch")

    assert record.exists is True
    assert record.created is True
    assert record.committed_branch is not None
    assert record.committed_branch.startswith("retro/main-")
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == record.committed_branch


def test_retrospective_service_blocks_dirty_target_repo(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nDirty retro\n"])
    done, service = _done_task_for_retrospective(config, "retro-dirty-task", commit_adapter=adapter)
    (repo_root / "untracked.txt").write_text("dirty\n")

    with pytest.raises(CommitError, match="target repository must be clean"):
        service.create([done.metadata.task_id], by="human", completion_mode="target-branch")


def test_retrospective_service_rejects_mismatched_group_artifacts(configured_paths):
    config, _, _ = configured_paths
    adapter = FakeAdapter([
        "# Retrospective\n\n## Summary\nGroup retro\n",
        "# Retrospective\n\n## Summary\nAnother retro\n",
    ])
    done_one, service = _done_task_for_retrospective(config, "retro-group-one", commit_adapter=adapter)
    done_two, _ = _done_task_for_retrospective(config, "retro-group-two", commit_adapter=adapter)

    created = service.create([done_one.metadata.task_id, done_two.metadata.task_id], by="human", completion_mode="target-branch")
    assert created.exists is True
    retro_path = done_two.task_dir / (created.artifact_filename or "")
    retro_path.write_text("# Retrospective\n\n## Summary\nDrifted content\n")

    inspected = service.inspect([done_one.metadata.task_id, done_two.metadata.task_id])

    assert inspected.exists is False
    assert inspected.created is False
