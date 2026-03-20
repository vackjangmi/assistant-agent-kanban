from __future__ import annotations

import pytest

from assistant_agent_kanban.commit_manager import CommitManager
from assistant_agent_kanban.config import AppConfig, PROJECT_ROOT
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import CommitError, IntegrationError
from assistant_agent_kanban.integration_manager import IntegrationManager
from assistant_agent_kanban.metadata_store import MetadataStore, slugify
from assistant_agent_kanban.request_creator import RequestTemplateData, create_request


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
