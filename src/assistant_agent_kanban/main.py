from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uvicorn
from pathlib import Path
from types import FrameType

from .config import AppConfig, load_config
from .api.app import create_default_app
from .api.main import CONFIG_ENV_VAR
from .assistant_factory import build_adapter_registry
from .exceptions import AdapterRunError, InspectionError, TaskNotFoundError
from .request_creator import RequestTemplateData, create_request
from .scanner import KanbanScanner
from .services.task_inspection_service import TaskInspectionService
from .services.task_service import TaskService


BANNER_ART_COLORS = (
    "1;38;2;221;214;254",
    "1;38;2;196;181;253",
    "1;38;2;167;139;250",
    "1;38;2;139;92;246",
    "1;38;2;124;58;237",
    "1;38;2;109;40;217",
    "1;38;2;224;231;255",
    "1;38;2;199;210;254",
    "1;38;2;165;180;252",
    "1;38;2;129;140;248",
    "1;38;2;96;165;250",
    "1;38;2;56;189;248",
)
BANNER_PRIMARY = BANNER_ART_COLORS[0]
BANNER_MUTED = "38;2;71;85;105"
BANNER_STARTING = "1;38;2;167;139;250"
BANNER_ONLINE = "1;38;2;52;211;153"
BANNER_SHUTDOWN = "1;38;2;251;113;133"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="assistant-agent-kanban")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--config")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")

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

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("task_id")
    inspect_parser.add_argument("--config")
    inspect_parser.add_argument("--kanban-root")
    inspect_parser.add_argument("--ask")
    inspect_parser.add_argument("--faq", choices=["is-running", "latest-activity", "why-waiting", "workspace-changes", "next-step"])
    inspect_parser.add_argument("--watch", action="store_true")
    inspect_parser.add_argument("--interval", type=float, default=2.0)
    inspect_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command in {None, "serve"}:
        config_path = getattr(args, "config", None)
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8000)
        if getattr(args, "reload", False):
            if config_path:
                os.environ[CONFIG_ENV_VAR] = config_path
            else:
                os.environ.pop(CONFIG_ENV_VAR, None)
            _print_serve_banner(_load_banner_config(config_path), host=host, port=port, reload=True)
            _run_uvicorn_with_shutdown_message(
                "assistant_agent_kanban.api.main:create_app",
                host=host,
                port=port,
                reload=True,
                factory=True,
                access_log=False,
                log_level="warning",
            )
            return
        app = create_default_app(config_path)
        _print_serve_banner(app.state.runtime.config, host=host, port=port, reload=False)
        _run_uvicorn_with_shutdown_message(
            app,
            host=host,
            port=port,
            reload=False,
            access_log=False,
            log_level="warning",
        )
        return

    if args.command == "request":
        config = _load_request_config(args.config, args.kanban_root)
        target_repo = Path(args.target_repo).expanduser().resolve() if args.target_repo else Path.cwd().resolve()
        base_branch = args.base_branch or _detect_current_branch(target_repo) or config.base_branch
        task_dir = create_request(
            config,
            template=RequestTemplateData(title=args.title, goal=args.body, plan_auto_approve=False),
            target_repo_root=target_repo,
            base_branch=base_branch,
        )
        print(task_dir)
        return

    if args.command == "logs":
        config = _load_request_config(args.config, args.kanban_root)
        logs = TaskService(KanbanScanner(config), config.runs_dir, config.kanban_root, config.archive_runs_dir).get_logs(args.task_id)
        if not logs.entries:
            print(f"No logs found for {args.task_id}")
            return
        for entry in logs.entries:
            print(f"== {entry.name} ==")
            print((entry.rendered_content or entry.debug_rendered_content or "(no readable log output for this file)").rstrip())
            print()
        return

    if args.command == "inspect":
        config = _load_request_config(args.config, args.kanban_root)
        registry = {str(backend): adapter for backend, adapter in build_adapter_registry().items()} if args.ask or args.faq else {}
        inspector = TaskInspectionService(config=config, scanner=KanbanScanner(config), adapter_registry=registry)
        try:
            if args.watch:
                while True:
                    snapshot = inspector.inspect(args.task_id)
                    print(_format_inspection(snapshot, json_output=args.json), flush=True)
                    print("", flush=True)
                    time.sleep(max(0.5, args.interval))
            if args.ask or args.faq:
                answer = inspector.answer(args.task_id, question=args.ask, question_id=args.faq)
                if args.json:
                    print(json.dumps(answer.model_dump(mode="json"), indent=2))
                else:
                    print(_format_inspection(answer.inspection, json_output=False))
                    print()
                    print(f"Question: {answer.question}")
                    print()
                    print(answer.answer.rstrip())
                return
            snapshot = inspector.inspect(args.task_id)
            print(_format_inspection(snapshot, json_output=args.json))
        except (TaskNotFoundError, InspectionError, AdapterRunError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc


def _load_request_config(config_path: str | None, kanban_root: str | None) -> AppConfig:
    config = load_config(config_path, bootstrap=kanban_root is None)
    if kanban_root is not None:
        old_kanban_root = config.kanban_root.expanduser().resolve()
        default_workspace_root = old_kanban_root / "_runtime/workspaces"
        workspace_root = config.workspace.root
        config.kanban_root = Path(kanban_root).expanduser().resolve()
        if workspace_root is None or workspace_root.expanduser().resolve() == default_workspace_root:
            config.workspace.root = config.kanban_root / "_runtime/workspaces"
        config.bootstrap()
    return config


def _format_inspection(snapshot, *, json_output: bool) -> str:
    if json_output:
        return json.dumps(snapshot.model_dump(mode="json"), indent=2)
    lines = [
        f"Task {snapshot.task_id}: {snapshot.state}",
        "",
        f"Health: {snapshot.health}",
        f"Summary: {snapshot.summary}",
        f"Worker: {snapshot.lease_owner or 'none'}",
        f"Heartbeat: {_age_label(snapshot.lease_age_seconds)}",
        f"Latest log: {snapshot.last_log_name or 'none'} ({_age_label(snapshot.last_log_age_seconds)})",
        f"Workspace: {snapshot.workspace_path or 'none'}",
        f"Workspace changes: {snapshot.workspace_change_count}",
    ]
    if snapshot.retry_gate_reason:
        lines.append(f"Retry gate: {snapshot.retry_gate_reason}")
    if snapshot.recent_errors:
        latest_error = snapshot.recent_errors[-1]
        lines.append(f"Latest error: {latest_error.code} - {latest_error.message}")
    if snapshot.workspace_changes:
        lines.extend(["", "Changed paths:", *[f"  {line}" for line in snapshot.workspace_changes[:20]]])
    return "\n".join(lines)


def _age_label(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "none"
    if age_seconds < 60:
        return f"{age_seconds}s ago"
    minutes = age_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    return f"{minutes // 60}h ago"


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


def _load_banner_config(config_path: str | None) -> AppConfig | None:
    try:
        return load_config(config_path)
    except Exception:
        return None


def _print_serve_banner(config: AppConfig | None, *, host: str, port: int, reload: bool) -> None:
    is_tty = sys.stdout.isatty()
    _clear_terminal(is_tty)
    banner = _build_serve_banner(config, host=host, port=port, reload=reload, color=is_tty)
    print(banner, flush=True)


def _build_serve_banner(config: AppConfig | None, *, host: str, port: int, reload: bool, color: bool = False) -> str:
    art = [
        " █████╗ ███████╗███████╗██╗███████╗████████╗ █████╗ ███╗   ██╗████████╗",
        "██╔══██╗██╔════╝██╔════╝██║██╔════╝╚══██╔══╝██╔══██╗████╗  ██║╚══██╔══╝",
        "███████║███████╗███████╗██║███████╗   ██║   ███████║██╔██╗ ██║   ██║   ",
        "██╔══██║╚════██║╚════██║██║╚════██║   ██║   ██╔══██║██║╚██╗██║   ██║   ",
        "██║  ██║███████║███████║██║███████║   ██║   ██║  ██║██║ ╚████║   ██║   ",
        "╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝   ",
        " █████╗  ██████╗ ███████╗███╗   ██╗████████╗    ██╗  ██╗ █████╗ ███╗   ██╗██████╗  █████╗ ███╗   ██╗",
        "██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝    ██║ ██╔╝██╔══██╗████╗  ██║██╔══██╗██╔══██╗████╗  ██║",
        "███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║       █████╔╝ ███████║██╔██╗ ██║██████╔╝███████║██╔██╗ ██║",
        "██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║       ██╔═██╗ ██╔══██║██║╚██╗██║██╔══██╗██╔══██║██║╚██╗██║",
        "██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║       ██║  ██╗██║  ██║██║ ╚████║██████╔╝██║  ██║██║ ╚████║",
        "╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝       ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝",
    ]
    art_width = max(len(line) for line in art)
    width = max(74, min(shutil.get_terminal_size((108, 24)).columns, max(108, art_width)))
    rule = "-" * width
    config_label = _config_label(config)
    kanban_label = str(config.kanban_root) if config is not None else "unavailable"
    security_label = _security_label(config)
    mode_label = "reload enabled" if reload else "steady serve"
    dashboard_url = _dashboard_url(host, port)
    listen_url = _url_for_host(host, port)
    network_url = _network_url(host, port)
    version = _package_version()
    lines = [
        _paint(rule, BANNER_MUTED, color),
        *(_paint(line, BANNER_ART_COLORS[index % len(BANNER_ART_COLORS)], color) for index, line in enumerate(art)),
        _paint(rule, BANNER_MUTED, color),
        f"Assistant Agent Kanban v{version}  |  {mode_label}",
        "",
        _status_line("STARTING", "Assistant Agent Kanban is booting...", BANNER_STARTING, color),
        "",
        f"Dashboard   {dashboard_url}",
        f"Listening   {listen_url}",
    ]
    if network_url and network_url != dashboard_url:
        lines.append(f"Network     {network_url}")
    lines.extend(
        [
            f"Security    {security_label}",
            f"Config      {config_label}",
            f"State       {kanban_label}",
            "Logs        quiet mode; warnings and errors will still appear",
            "Stop        Ctrl+C",
            _paint(rule, BANNER_MUTED, color),
        ]
    )
    return "\n".join(lines)


def _package_version() -> str:
    try:
        return importlib.metadata.version("assistant-agent-kanban")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _config_label(config: AppConfig | None) -> str:
    if config is None:
        return "unavailable"
    if config.loaded_local_from is not None:
        return str(config.loaded_local_from)
    if config.loaded_from is not None:
        return str(config.loaded_from)
    return "defaults"


def _security_label(config: AppConfig | None) -> str:
    if config is None:
        return "unknown until configuration loads"
    if config.auth.enabled or _configured_user_count(config) > 0:
        return "remote use enabled; login required"
    return "remote use disabled; app allows localhost clients only"


def _configured_user_count(config: AppConfig) -> int:
    db_path = config.app_database_path.expanduser().resolve()
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("select count(*) from users").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row is not None else 0


def _dashboard_url(host: str, port: int) -> str:
    if host in {"0.0.0.0", "::"}:
        return _url_for_host("127.0.0.1", port)
    return _url_for_host(host, port)


def _network_url(host: str, port: int) -> str | None:
    if host != "0.0.0.0":
        return None
    return _url_for_host(_detect_lan_ip() or "<your-ip>", port)


def _url_for_host(host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{display_host}:{port}/"


def _detect_lan_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def _paint(text: str, code: str, enabled: bool) -> str:
    if not enabled or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def _run_uvicorn_with_shutdown_message(app, **kwargs) -> None:
    original_handle_exit = uvicorn.Server.handle_exit
    original_startup = uvicorn.Server.startup
    shutdown_announced = False
    online_announced = False
    host = str(kwargs.get("host") or "127.0.0.1")
    port = int(kwargs.get("port") or 8000)

    async def startup(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        nonlocal online_announced
        await original_startup(self, sockets=sockets)
        if self.started and not online_announced:
            _print_online_message(host, port)
            online_announced = True

    def handle_exit(self: uvicorn.Server, sig: int, frame: FrameType | None) -> None:
        nonlocal shutdown_announced
        if not shutdown_announced:
            _print_shutdown_message(sig)
            shutdown_announced = True
        original_handle_exit(self, sig, frame)

    uvicorn.Server.startup = startup
    uvicorn.Server.handle_exit = handle_exit
    try:
        uvicorn.run(app, **kwargs)
    finally:
        uvicorn.Server.startup = original_startup
        uvicorn.Server.handle_exit = original_handle_exit


def _print_online_message(host: str, port: int) -> None:
    if not sys.stdout.isatty():
        return
    _print_runtime_status("ONLINE", f"Serving requests at {_dashboard_url(host, port)}", BANNER_ONLINE)


def _print_shutdown_message(_sig: int | None = None) -> None:
    if not sys.stdout.isatty():
        return
    _print_runtime_status("STOPPING", "Shutting down Assistant Agent Kanban...", BANNER_SHUTDOWN)


def _print_runtime_status(label: str, message: str, color_code: str) -> None:
    print(f"\n{_status_line(label, message, color_code, True)}\n", flush=True)


def _status_line(label: str, message: str, color_code: str, color: bool) -> str:
    return _paint(f"● {label.ljust(9)} {message}", color_code, color)


def _clear_terminal(enabled: bool) -> None:
    if not enabled or os.environ.get("ASSISTANT_AGENT_KANBAN_NO_CLEAR"):
        return
    # Clear the viewport, clear scrollback where supported, then move home.
    print("\033[2J\033[3J\033[H", end="", flush=True)


if __name__ == "__main__":
    main()
