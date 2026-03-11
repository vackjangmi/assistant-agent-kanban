from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from fs_kanban_agent import config as config_module
from fs_kanban_agent.config import AppConfig
from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.models import RunResult
from fs_kanban_agent.opencode_adapter import OpenCodeAdapter
from fs_kanban_agent.exceptions import AdapterRunError


class FakeAdapter(OpenCodeAdapter):
    def __init__(
        self,
        responses: list[str] | None = None,
        side_effect: Callable[[Path], None] | None = None,
        *,
        ok: bool = True,
        returncode: int = 0,
        stderr: str = "",
        discovery_responses: list[list[str] | Exception] | None = None,
        resolved_models: list[str | None] | None = None,
    ) -> None:
        self.responses = responses or []
        self.side_effect = side_effect
        self.ok = ok
        self.returncode = returncode
        self.stderr = stderr
        self.discovery_responses = discovery_responses or []
        self.discovery_calls: list[bool] = []
        self.resolved_models = resolved_models or []

    def run(
        self,
        *,
        agent: str,
        prompt: str,
        cwd: Path,
        run_log_path: Path,
        config: AppConfig,
        on_log_line: Callable[[str, str | None], None] | None = None,
    ) -> RunResult:
        if self.side_effect is not None:
            self.side_effect(cwd)
        content = self.responses.pop(0) if self.responses else f"{agent}: ok"
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        run_log_path.write_text(content + "\n")
        if on_log_line is not None:
            on_log_line(content, content)
        resolved_model = self.resolved_models.pop(0) if self.resolved_models else None
        return RunResult(
            ok=self.ok,
            returncode=self.returncode,
            assistant_text=content,
            stdout=content,
            stderr=self.stderr,
            raw_events_path=str(run_log_path),
            command=[agent],
            resolved_model=resolved_model,
        )

    def discover_models(self, *, config: AppConfig, refresh: bool = False) -> list[str]:
        self.discovery_calls.append(refresh)
        if not self.discovery_responses:
            return []
        response = self.discovery_responses[0]
        if len(self.discovery_responses) > 1:
            response = self.discovery_responses.pop(0)
        if isinstance(response, Exception):
            raise AdapterRunError(str(response)) from response
        return list(response)


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


@pytest.fixture(autouse=True)
def isolate_default_local_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    default_local_path = tmp_path / "config.local.yaml"
    monkeypatch.setattr(config_module, "DEFAULT_LOCAL_CONFIG_PATH", default_local_path)
    return default_local_path


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
