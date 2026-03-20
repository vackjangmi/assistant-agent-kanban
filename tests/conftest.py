from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from assistant_agent_kanban import config as config_module
from assistant_agent_kanban.assistant_adapter import AssistantAdapter
from assistant_agent_kanban.config import AppConfig
from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.models import RunResult
from assistant_agent_kanban.exceptions import AdapterRunError


class FakeAdapter(AssistantAdapter):
    def __init__(
        self,
        responses: list[str] | None = None,
        side_effect: Callable[[Path], None] | None = None,
        side_effect_output_formats: set[str] | None = None,
        *,
        ok: bool = True,
        returncode: int = 0,
        stderr: str = "",
        discovery_responses: list[list[str] | Exception] | None = None,
        resolved_models: list[str | None] | None = None,
        session_ids: list[str | None] | None = None,
        total_tokens: list[int] | None = None,
    ) -> None:
        self.responses = responses or []
        self._last_response: str | None = None
        self.side_effect = side_effect
        self.side_effect_output_formats = side_effect_output_formats or {"default"}
        self.ok = ok
        self.returncode = returncode
        self.stderr = stderr
        self.discovery_responses = discovery_responses or []
        self.discovery_calls: list[bool] = []
        self.resolved_models = resolved_models or []
        self.session_ids = session_ids or []
        self.total_tokens = total_tokens or []
        self.run_calls: list[dict[str, object]] = []
        self.cancelled_task_ids: list[str] = []

    def run(
        self,
        *,
        agent: str,
        prompt: str,
        cwd: Path,
        run_log_path: Path,
        config: AppConfig,
        session_id: str | None = None,
        cancel_key: str | None = None,
        on_log_line: Callable[[str, str | None], None] | None = None,
        output_format: str = "json",
        stream_stderr_to_log: bool = False,
        show_thinking: bool = False,
    ) -> RunResult:
        self.run_calls.append(
            {
                "agent": agent,
                "prompt": prompt,
                "cwd": cwd,
                "run_log_path": run_log_path,
                "session_id": session_id,
                "cancel_key": cancel_key,
                "output_format": output_format,
                "stream_stderr_to_log": stream_stderr_to_log,
                "show_thinking": show_thinking,
            }
        )
        if self.side_effect is not None and output_format in self.side_effect_output_formats:
            self.side_effect(cwd)
        if self.responses:
            content = self.responses.pop(0)
            self._last_response = content
        elif self._last_response is not None:
            content = self._last_response
        else:
            content = f"{agent}: ok"
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        run_log_path.write_text(content + "\n")
        if on_log_line is not None:
            on_log_line(content, content)
        resolved_model = self.resolved_models.pop(0) if self.resolved_models else None
        returned_session_id = self.session_ids.pop(0) if self.session_ids else session_id
        returned_total_tokens = self.total_tokens.pop(0) if self.total_tokens else 0
        return RunResult(
            ok=self.ok,
            returncode=self.returncode,
            assistant_text=content,
            stdout=content,
            stderr=self.stderr,
            raw_events_path=str(run_log_path),
            command=[agent],
            resolved_model=resolved_model,
            session_id=returned_session_id,
            total_tokens=returned_total_tokens,
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

    def cancel_task(self, task_id: str) -> None:
        self.cancelled_task_ids.append(task_id)


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
    kanban_root = tmp_path / ".kanban-agent"
    config = AppConfig(kanban_root=kanban_root, repo_root=repo_root)
    config.opencode.worker_live_logs_enabled = True
    config.bootstrap()
    return config, repo_root, kanban_root


@pytest.fixture(autouse=True)
def isolate_default_local_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    default_base_path = tmp_path / "config.yaml"
    default_local_path = tmp_path / "config.local.yaml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", default_base_path)
    monkeypatch.setattr(config_module, "DEFAULT_LOCAL_CONFIG_PATH", default_local_path)
    return default_local_path


def create_request_task(
    config: AppConfig,
    name: str = "sample-task",
    *,
    target_repo_root: Path | None = None,
    base_branch: str | None = None,
    language: str | None = None,
    body: str | None = None,
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
                *([f"language: {language}"] if language else []),
                "target:",
                f"  repo_root: {repo_root}",
                f"  base_branch: {branch}",
                "---",
                "",
                f"# {name}",
                "",
                body or f"Implement {name}.",
                "",
            ]
        )
    )
    return task_dir
