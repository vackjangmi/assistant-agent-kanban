from __future__ import annotations

import subprocess
from pathlib import Path

from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.main import main

from .conftest import init_git_repo


def test_request_cli_creates_request_with_target_repo(tmp_path, capsys):
    kanban_root = tmp_path / ".kanban-agent"
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()

    main(
        [
            "request",
            "my task",
            "--target-repo",
            str(target_repo),
            "--kanban-root",
            str(kanban_root),
            "--base-branch",
            "develop",
            "--body",
            "Do the thing.",
        ]
    )

    output = capsys.readouterr().out.strip()
    request_path = Path(output) / "REQUEST.md"
    content = request_path.read_text()

    assert request_path.exists()
    assert len(Path(output).name) == 7
    assert f"repo_root: {target_repo.resolve()}" in content
    assert "base_branch: develop" in content
    assert "language: en" in content
    assert "## Goal" in content
    assert "Do the thing." in content


def test_request_cli_defaults_target_repo_and_branch_from_current_directory(tmp_path, capsys, monkeypatch):
    kanban_root = tmp_path / ".kanban-agent"
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    init_git_repo(target_repo)
    subprocess.run(["git", "-C", str(target_repo), "checkout", "-b", "feature/manual-request"], check=True, capture_output=True, text=True)
    monkeypatch.chdir(target_repo)

    main([
        "request",
        "manual task",
        "--kanban-root",
        str(kanban_root),
    ])

    output = capsys.readouterr().out.strip()
    request_path = Path(output) / "REQUEST.md"
    content = request_path.read_text()

    assert request_path.exists()
    assert f"repo_root: {target_repo.resolve()}" in content
    assert "base_branch: feature/manual-request" in content
    assert "language: en" in content
    assert "# manual task" in content
    assert "## Goal" not in content


def test_logs_cli_prints_task_logs(tmp_path, capsys):
    kanban_root = tmp_path / ".kanban-agent"
    config = AppConfig(kanban_root=kanban_root, repo_root=tmp_path / "repo")
    config.bootstrap()
    log_dir = config.runs_dir / "TASK-0001"
    log_dir.mkdir(parents=True)
    (log_dir / "planner-001.jsonl").write_text('{"type":"final","content":"plan"}\n')
    task_dir = config.state_dir(TaskState.REQUESTS) / "task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# task\n")
    (task_dir / "metadata.json").write_text(
        """{
  "version": 1,
  "task_id": "TASK-0001",
  "title": "task",
  "slug": "task",
  "state": "requests",
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

    main(["logs", "TASK-0001", "--kanban-root", str(kanban_root)])

    output = capsys.readouterr().out
    assert "planner-001.jsonl" in output
    assert "plan" in output


def test_logs_cli_reports_missing_logs(tmp_path, capsys):
    kanban_root = tmp_path / ".kanban-agent"
    config = AppConfig(kanban_root=kanban_root, repo_root=tmp_path / "repo")
    config.bootstrap()
    task_dir = config.state_dir(TaskState.REQUESTS) / "task"
    task_dir.mkdir(parents=True)
    (task_dir / "REQUEST.md").write_text("# task\n")
    (task_dir / "metadata.json").write_text(
        """{
  "version": 1,
  "task_id": "TASK-0002",
  "title": "task",
  "slug": "task",
  "state": "requests",
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

    main(["logs", "TASK-0002", "--kanban-root", str(kanban_root)])

    assert "No logs found for TASK-0002" in capsys.readouterr().out
