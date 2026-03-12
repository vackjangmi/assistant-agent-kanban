#!/bin/sh
set -eu

usage() {
    printf '%s\n' "Usage: ./init.sh [--config PATH]"
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$SCRIPT_DIR
VENV_DIR=${VENV_DIR:-"$REPO_ROOT/.venv"}
CONFIG_PATH=${CONFIG_PATH:-}
DEPS_STAMP_FILE="$VENV_DIR/.fs-kanban-agent-deps-stamp"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --config)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --config" >&2
                exit 1
            fi
            CONFIG_PATH=$2
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
    if [ -f "$REPO_ROOT/config.yaml" ]; then
        CONFIG_PATH="$REPO_ROOT/config.yaml"
    else
        CONFIG_PATH="$REPO_ROOT/examples/config.yaml"
    fi
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

FS_KANBAN_CONFIG=$CONFIG_PATH \
    "$PYTHON_BIN" -c 'import os; from fs_kanban_agent.config import load_config; load_config(os.environ["FS_KANBAN_CONFIG"])' >/dev/null

printf '%s\n' "Initialized fs-kanban-agent"
printf 'venv: %s\n' "$VENV_DIR"
printf 'config: %s\n' "$CONFIG_PATH"
printf 'next: ./run.sh\n'
