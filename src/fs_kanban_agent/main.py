from __future__ import annotations

import argparse
import uvicorn
from pathlib import Path

from .config import AppConfig, load_config
from .api.app import create_default_app
from .request_creator import RequestTemplateData, create_request


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fs-kanban-agent")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--config")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    request_parser = subparsers.add_parser("request")
    request_parser.add_argument("title")
    request_parser.add_argument("--target-repo", required=True)
    request_parser.add_argument("--body")
    request_parser.add_argument("--base-branch")
    request_parser.add_argument("--config")
    request_parser.add_argument("--kanban-root")

    args = parser.parse_args(argv)
    if args.command in {None, "serve"}:
        app = create_default_app(getattr(args, "config", None))
        uvicorn.run(app, host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", 8000))
        return

    if args.command == "request":
        config = _load_request_config(args.config, args.kanban_root)
        task_dir = create_request(
            config,
            template=RequestTemplateData(title=args.title, goal=args.body or f"Implement {args.title}."),
            target_repo_root=Path(args.target_repo),
            base_branch=args.base_branch,
        )
        print(task_dir)


def _load_request_config(config_path: str | None, kanban_root: str | None) -> AppConfig:
    config = load_config(config_path)
    if kanban_root is not None:
        config.kanban_root = Path(kanban_root).expanduser().resolve()
        config.bootstrap()
    return config


if __name__ == "__main__":
    main()
