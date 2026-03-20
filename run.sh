#!/bin/sh
set -eu

usage() {
    printf '%s\n' "Usage: ./run.sh [--config PATH] [--host HOST] [--port PORT] [--reload]"
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$SCRIPT_DIR
VENV_DIR=${VENV_DIR:-"$REPO_ROOT/.venv"}
CONFIG_PATH=${CONFIG_PATH:-}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8000}
RELOAD=${RELOAD:-0}
DEPS_STAMP_FILE="$VENV_DIR/.assistant-agent-kanban-deps-stamp"

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
        --host)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --host" >&2
                exit 1
            fi
            HOST=$2
            shift 2
            ;;
        --port)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --port" >&2
                exit 1
            fi
            PORT=$2
            shift 2
            ;;
        --reload)
            RELOAD=1
            shift
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
    elif [ -f "$REPO_ROOT/config.local.yaml" ]; then
        CONFIG_PATH="$REPO_ROOT/config.local.yaml"
    else
        CONFIG_PATH="$REPO_ROOT/examples/config.yaml"
    fi
fi

if [ ! -x "$VENV_DIR/bin/assistant-agent-kanban" ] || [ ! -f "$DEPS_STAMP_FILE" ] || [ "$REPO_ROOT/pyproject.toml" -nt "$DEPS_STAMP_FILE" ]; then
    "$REPO_ROOT/init.sh" --config "$CONFIG_PATH"
fi

cd "$REPO_ROOT"

set -- "$VENV_DIR/bin/assistant-agent-kanban" serve --config "$CONFIG_PATH" --host "$HOST" --port "$PORT"
if [ "$RELOAD" = "1" ]; then
    set -- "$@" --reload
fi

exec "$@"
