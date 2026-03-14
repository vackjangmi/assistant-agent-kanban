from __future__ import annotations

import pytest

from fs_kanban_agent.commit_manager import CommitManager
from fs_kanban_agent.config import AppConfig, PROJECT_ROOT
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.exceptions import CommitError, IntegrationError
from fs_kanban_agent.integration_manager import IntegrationManager
from fs_kanban_agent.metadata_store import MetadataStore, slugify
from fs_kanban_agent.request_creator import RequestTemplateData, create_request


def test_create_request_rejects_orchestrator_project_as_target(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.bootstrap()

    with pytest.raises(ValueError, match="overlaps with the orchestrator project root"):
        create_request(
            config,
            template=RequestTemplateData(title="unsafe", goal="should fail"),
            target_repo_root=PROJECT_ROOT,
        )


def test_integration_manager_rejects_orchestrator_project_as_target(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.bootstrap()
    task_dir = config.state_dir(TaskState.COMPLETED_REVIEWS) / "unsafe-task"
    task_dir.mkdir(parents=True)
    metadata = MetadataStore().bootstrap(
        task_dir,
        TaskState.COMPLETED_REVIEWS,
        "unsafe01",
        "unsafe task",
        slugify("unsafe task"),
        target_repo_root=str(PROJECT_ROOT),
        base_branch="main",
    )

    with pytest.raises(IntegrationError, match="overlaps with the orchestrator project root"):
        IntegrationManager(config).apply_workspace(metadata, tmp_path / "workspace")


def test_commit_manager_rejects_orchestrator_project_as_target(tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.bootstrap()
    task_dir = config.state_dir(TaskState.HUMAN_VERIFYING) / "unsafe-task"
    task_dir.mkdir(parents=True)
    metadata = MetadataStore().bootstrap(
        task_dir,
        TaskState.HUMAN_VERIFYING,
        "unsafe02",
        "unsafe task",
        slugify("unsafe task"),
        target_repo_root=str(PROJECT_ROOT),
        base_branch="main",
    )

    with pytest.raises(CommitError, match="overlaps with the orchestrator project root"):
        CommitManager().commit_task(task_dir, metadata)
