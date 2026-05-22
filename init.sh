#!/bin/sh
set -eu

usage() {
    printf '%s\n' "Usage: ./init.sh [--config PATH] [--root PATH] [--kanban-root PATH]"
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$SCRIPT_DIR
VENV_DIR=${VENV_DIR:-"$REPO_ROOT/.venv"}
CONFIG_PATH=${CONFIG_PATH:-}
CONFIG_PATH_EXPLICIT=0
REPO_DISCOVERY_ROOT=${ASSISTANT_AGENT_KANBAN_REPO_DISCOVERY_ROOT:-}
KANBAN_ROOT=${ASSISTANT_AGENT_KANBAN_KANBAN_ROOT:-}
DEPS_STAMP_FILE="$VENV_DIR/.assistant-agent-kanban-deps-stamp"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --config)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --config" >&2
                exit 1
            fi
            CONFIG_PATH=$2
            CONFIG_PATH_EXPLICIT=1
            shift 2
            ;;
        --root)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --root" >&2
                exit 1
            fi
            REPO_DISCOVERY_ROOT=$2
            shift 2
            ;;
        --kanban-root)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --kanban-root" >&2
                exit 1
            fi
            KANBAN_ROOT=$2
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$CONFIG_PATH" ]; then
    CONFIG_PATH="$REPO_ROOT/config.yaml"
fi

if [ -d "$VENV_DIR" ]; then
    PYTHON_BIN="$VENV_DIR/bin/python"
else
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD=python3
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD=python
    else
        printf '%s\n' "python3 or python is required" >&2
        exit 1
    fi

    "$PYTHON_CMD" -m venv "$VENV_DIR"
    PYTHON_BIN="$VENV_DIR/bin/python"
fi

cd "$REPO_ROOT"

"$PYTHON_BIN" -m pip install -e '.[dev]'
touch "$DEPS_STAMP_FILE"

if [ ! -f "$CONFIG_PATH" ]; then
    mkdir -p "$(dirname -- "$CONFIG_PATH")"
    cp "$REPO_ROOT/examples/config.yaml" "$CONFIG_PATH"
fi

CONFIG_WRITE_PATH=$CONFIG_PATH
if [ "$CONFIG_PATH_EXPLICIT" = "0" ] && { [ -n "$REPO_DISCOVERY_ROOT" ] || [ -n "$KANBAN_ROOT" ]; }; then
    CONFIG_WRITE_PATH="$REPO_ROOT/config.local.yaml"
fi

if [ -n "$REPO_DISCOVERY_ROOT" ] || [ -n "$KANBAN_ROOT" ]; then
    ASSISTANT_AGENT_KANBAN_CONFIG_WRITE=$CONFIG_WRITE_PATH \
    ASSISTANT_AGENT_KANBAN_REPO_DISCOVERY_ROOT=$REPO_DISCOVERY_ROOT \
    ASSISTANT_AGENT_KANBAN_KANBAN_ROOT=$KANBAN_ROOT \
        "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path, PurePosixPath

import yaml

config_path = Path(os.environ["ASSISTANT_AGENT_KANBAN_CONFIG_WRITE"])
root_dir = os.environ.get("ASSISTANT_AGENT_KANBAN_REPO_DISCOVERY_ROOT") or ""
kanban_root = os.environ.get("ASSISTANT_AGENT_KANBAN_KANBAN_ROOT") or ""

if config_path.exists():
    data = yaml.safe_load(config_path.read_text()) or {}
else:
    data = {}
if not isinstance(data, dict):
    raise SystemExit(f"config file must contain a mapping: {config_path}")

if root_dir:
    repo_discovery = data.get("repo_discovery")
    if not isinstance(repo_discovery, dict):
        repo_discovery = {}
        data["repo_discovery"] = repo_discovery
    repo_discovery["root"] = root_dir
if kanban_root:
    data["kanban_root"] = kanban_root
    workspace = data.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
        data["workspace"] = workspace
    workspace["root"] = str(PurePosixPath(kanban_root) / "_runtime" / "workspaces")

config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False))
PY
fi

ASSISTANT_AGENT_KANBAN_CONFIG=$CONFIG_PATH \
    "$PYTHON_BIN" -c 'import os; from assistant_agent_kanban.config import load_config; load_config(os.environ["ASSISTANT_AGENT_KANBAN_CONFIG"])' >/dev/null

printf '%s\n' "Initialized assistant-agent-kanban"
printf 'venv: %s\n' "$VENV_DIR"
printf 'config: %s\n' "$CONFIG_PATH"
if [ "$CONFIG_WRITE_PATH" != "$CONFIG_PATH" ]; then
    printf 'local overrides: %s\n' "$CONFIG_WRITE_PATH"
fi
if [ -n "$REPO_DISCOVERY_ROOT" ]; then
    printf 'repo discovery root: %s\n' "$REPO_DISCOVERY_ROOT"
fi
if [ -n "$KANBAN_ROOT" ]; then
    printf 'kanban root: %s\n' "$KANBAN_ROOT"
fi
printf 'next: ./run.sh\n'
