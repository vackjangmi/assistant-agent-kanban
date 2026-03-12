from __future__ import annotations

import argparse
import subprocess
import uvicorn
from pathlib import Path

from .config import AppConfig, load_config
from .api.app import create_default_app
from .request_creator import RequestTemplateData, create_request
from .scanner import KanbanScanner
from .services.task_service import TaskService


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fs-kanban-agent")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--config")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    request_parser = subparsers.add_parser("request")
    request_parser.add_argument("title")
    request_parser.add_argument("--target-repo")
    request_parser.add_argument("--body")
    request_parser.add_argument("--base-branch")
    request_parser.add_argument("--config")
    request_parser.add_argument("--kanban-root")

    logs_parser = subparsers.add_parser("logs")
    logs_parser.add_argument("task_id")
    logs_parser.add_argument("--config")
    logs_parser.add_argument("--kanban-root")

    args = parser.parse_args(argv)
    if args.command in {None, "serve"}:
        app = create_default_app(getattr(args, "config", None))
        uvicorn.run(app, host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", 8000))
        return

    if args.command == "request":
        config = _load_request_config(args.config, args.kanban_root)
        target_repo = Path(args.target_repo).expanduser().resolve() if args.target_repo else Path.cwd().resolve()
        base_branch = args.base_branch or _detect_current_branch(target_repo) or config.base_branch
        task_dir = create_request(
            config,
            template=RequestTemplateData(title=args.title, goal=args.body),
            target_repo_root=target_repo,
            base_branch=base_branch,
        )
        print(task_dir)
        return

    if args.command == "logs":
        config = _load_request_config(args.config, args.kanban_root)
        logs = TaskService(KanbanScanner(config), config.runs_dir, config.kanban_root).get_logs(args.task_id)
        if not logs.entries:
            print(f"No logs found for {args.task_id}")
            return
        for entry in logs.entries:
            print(f"== {entry.name} ==")
            print((entry.rendered_content or entry.debug_rendered_content or "(no readable log output for this file)").rstrip())
            print()


def _load_request_config(config_path: str | None, kanban_root: str | None) -> AppConfig:
    config = load_config(config_path)
    if kanban_root is not None:
        config.kanban_root = Path(kanban_root).expanduser().resolve()
        config.bootstrap()
    return config


def _detect_current_branch(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


if __name__ == "__main__":
    main()
