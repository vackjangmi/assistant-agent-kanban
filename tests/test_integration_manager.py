from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from assistant_agent_kanban import integration_manager as integration_manager_module
from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.exceptions import IntegrationError
from assistant_agent_kanban.integration_manager import IntegrationManager
from assistant_agent_kanban.models import TaskMetadata


def _metadata_with_review_branch(repo_root: Path) -> TaskMetadata:
    repo_root.mkdir(parents=True, exist_ok=True)
    metadata = TaskMetadata(
        task_id="abc123",
        title="Review branch task",
        slug="review-branch-task",
        state=TaskState.HUMAN_VERIFYING,
    )
    metadata.integration.verification_repo_root = str(repo_root)
    metadata.integration.review_branch = "review/abc123"
    return metadata


def test_push_review_branch_uses_token_without_git_credential_fallback(monkeypatch, tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.review_branch_remote.enabled = True
    config.review_branch_remote.require_push_success = True
    metadata = _metadata_with_review_branch(config.repo_root)
    calls: list[dict[str, object]] = []

    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)

    def fake_run(command, *, capture_output, text, check, env=None):
        calls.append({"command": command, "env": env})
        if command[3:] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(command, 0, stdout="https://git.example.com/group/repo.git\n", stderr="")
        if command[3] == "push":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Authentication failed for bad-token")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(integration_manager_module.subprocess, "run", fake_run)

    with pytest.raises(IntegrationError) as exc_info:
        IntegrationManager(config).push_review_branch(
            metadata,
            git_token="bad-token",
            git_token_username="git-user",
        )

    push_call = next(call for call in calls if call["command"][3] == "push")
    push_command = push_call["command"]
    assert push_command[5] == "https://git-user:bad-token@git.example.com/group/repo.git"
    env = push_call["env"]
    assert isinstance(env, dict)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    config_index = int(env["GIT_CONFIG_COUNT"]) - 1
    assert env[f"GIT_CONFIG_KEY_{config_index}"] == "credential.helper"
    assert env[f"GIT_CONFIG_VALUE_{config_index}"] == ""
    assert "bad-token" not in str(exc_info.value)
    assert metadata.integration.remote_push_error == "Authentication failed for [redacted]"


def test_push_review_branch_converts_ssh_remote_when_token_is_provided(monkeypatch, tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.review_branch_remote.enabled = True
    config.review_branch_remote.require_push_success = True
    metadata = _metadata_with_review_branch(config.repo_root)
    calls: list[dict[str, object]] = []

    def fake_run(command, *, capture_output, text, check, env=None):
        calls.append({"command": command, "env": env})
        if command[3:] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(command, 0, stdout="git@git.example.com:group/repo.git\n", stderr="")
        if command[3] == "push":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(integration_manager_module.subprocess, "run", fake_run)

    IntegrationManager(config).push_review_branch(
        metadata,
        git_token="token",
        git_token_username="git-user",
    )

    push_call = next(call for call in calls if call["command"][3] == "push")
    push_command = push_call["command"]
    assert push_command[5] == "https://git-user:token@git.example.com/group/repo.git"
    assert metadata.integration.remote_name == "origin"
    assert metadata.integration.remote_review_branch == "review/abc123"


def test_push_review_branch_converts_ssh_scheme_remote_when_token_is_provided(monkeypatch, tmp_path):
    config = AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo")
    config.review_branch_remote.enabled = True
    metadata = _metadata_with_review_branch(config.repo_root)
    calls: list[list[str]] = []

    def fake_run(command, *, capture_output, text, check, env=None):
        calls.append(command)
        if command[3:] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(command, 0, stdout="ssh://git@git.example.com/group/repo.git\n", stderr="")
        if command[3] == "push":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(integration_manager_module.subprocess, "run", fake_run)

    IntegrationManager(config).push_review_branch(
        metadata,
        git_token="token",
        git_token_username="git-user",
    )

    push_command = next(command for command in calls if command[3] == "push")
    assert push_command[5] == "https://git-user:token@git.example.com/group/repo.git"


def test_extracts_merge_request_url_from_push_output(tmp_path):
    manager = IntegrationManager(AppConfig(kanban_root=tmp_path / ".kanban-agent", repo_root=tmp_path / "repo"))

    url = manager._extract_merge_request_url(
        """
        remote:
        remote: To create a merge request for feature/demo, visit:
        remote:   https://git.example.com/group/repo/-/merge_requests/new?merge_request%5Bsource_branch%5D=feature%2Fdemo
        remote:
        """
    )

    assert url == "https://git.example.com/group/repo/-/merge_requests/new?merge_request%5Bsource_branch%5D=feature%2Fdemo"
