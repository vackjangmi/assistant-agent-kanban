from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.models import RunResult
from fs_kanban_agent.opencode_adapter import OpenCodeAdapter


class FakeAdapter(OpenCodeAdapter):
    def __init__(
        self,
        responses: list[str] | None = None,
        side_effect: Callable[[Path], None] | None = None,
        *,
        ok: bool = True,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.responses = responses or []
        self.side_effect = side_effect
        self.ok = ok
        self.returncode = returncode
        self.stderr = stderr

    def run(self, *, agent: str, prompt: str, cwd: Path, run_log_path: Path, config: AppConfig) -> RunResult:
        if self.side_effect is not None:
            self.side_effect(cwd)
        content = self.responses.pop(0) if self.responses else f"{agent}: ok"
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        run_log_path.write_text(content + "\n")
        return RunResult(
            ok=self.ok,
            returncode=self.returncode,
            assistant_text=content,
            stdout=content,
            stderr=self.stderr,
            raw_events_path=str(run_log_path),
            command=[agent],
        )


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True, capture_output=True, text=True)
    (path / "app.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True, text=True)


@pytest.fixture
def configured_paths(tmp_path: Path) -> tuple[AppConfig, Path, Path]:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    init_git_repo(repo_root)
    kanban_root = tmp_path / "ai-kanban"
    config = AppConfig(kanban_root=kanban_root, repo_root=repo_root)
    config.bootstrap()
    return config, repo_root, kanban_root


def create_request_task(
    config: AppConfig,
    name: str = "sample-task",
    *,
    target_repo_root: Path | None = None,
    base_branch: str | None = None,
) -> Path:
    task_dir = config.state_dir(TaskState.REQUESTS) / name
    task_dir.mkdir(parents=True, exist_ok=True)
    repo_root = (target_repo_root or config.repo_root).expanduser().resolve()
    branch = base_branch or config.base_branch
    (task_dir / "REQUEST.md").write_text(
        "\n".join(
            [
                "---",
                f"title: {name}",
                "target:",
                f"  repo_root: {repo_root}",
                f"  base_branch: {branch}",
                "---",
                "",
                f"# {name}",
                "",
                f"Implement {name}.",
                "",
            ]
        )
    )
    return task_dir
