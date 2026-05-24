#!/bin/sh
set -eu

usage() {
    printf '%s\n' "Usage: ./run.sh [--config PATH] [--host HOST] [--port PORT] [--reload] [--root PATH] [--kanban-root PATH] [--assistant NAME] [--language LANG] [--theme THEME]"
    printf '%s\n' "Assistant names: antigravity, opencode, codex, gemini, claude"
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$SCRIPT_DIR
VENV_DIR=${VENV_DIR:-"$REPO_ROOT/.venv"}
CONFIG_PATH=${CONFIG_PATH:-}
CONFIG_PATH_EXPLICIT=0
REPO_DISCOVERY_ROOT=${ASSISTANT_AGENT_KANBAN_REPO_DISCOVERY_ROOT:-}
KANBAN_ROOT=${ASSISTANT_AGENT_KANBAN_KANBAN_ROOT:-}
CODING_ASSISTANT=${ASSISTANT_AGENT_KANBAN_CODING_ASSISTANT:-}
LANGUAGE=${ASSISTANT_AGENT_KANBAN_LANGUAGE:-}
THEME=${ASSISTANT_AGENT_KANBAN_THEME:-}
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
        --assistant)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --assistant" >&2
                exit 1
            fi
            CODING_ASSISTANT=$2
            shift 2
            ;;
        --language)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --language" >&2
                exit 1
            fi
            LANGUAGE=$2
            shift 2
            ;;
        --theme)
            if [ "$#" -lt 2 ]; then
                printf '%s\n' "Missing value for --theme" >&2
                exit 1
            fi
            THEME=$2
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
        CONFIG_PATH="$REPO_ROOT/config.yaml"
    fi
fi

FIRST_RUN_LOCAL_MISSING=0
if [ "$CONFIG_PATH_EXPLICIT" = "0" ] && [ ! -f "$REPO_ROOT/config.local.yaml" ]; then
    FIRST_RUN_LOCAL_MISSING=1
fi

. "$REPO_ROOT/lib/firstrun_prompts.sh"
firstrun_prompts

if [ ! -x "$VENV_DIR/bin/assistant-agent-kanban" ] || [ ! -f "$DEPS_STAMP_FILE" ] || [ "$REPO_ROOT/pyproject.toml" -nt "$DEPS_STAMP_FILE" ] || [ ! -f "$CONFIG_PATH" ] || [ -n "$REPO_DISCOVERY_ROOT" ] || [ -n "$KANBAN_ROOT" ] || [ -n "$CODING_ASSISTANT" ] || [ -n "$LANGUAGE" ] || [ -n "$THEME" ]; then
    set -- "$REPO_ROOT/init.sh"
    if [ "$CONFIG_PATH_EXPLICIT" = "1" ]; then
        set -- "$@" --config "$CONFIG_PATH"
    fi
    if [ -n "$REPO_DISCOVERY_ROOT" ]; then
        set -- "$@" --root "$REPO_DISCOVERY_ROOT"
    fi
    if [ -n "$KANBAN_ROOT" ]; then
        set -- "$@" --kanban-root "$KANBAN_ROOT"
    fi
    if [ -n "$CODING_ASSISTANT" ]; then
        set -- "$@" --assistant "$CODING_ASSISTANT"
    fi
    if [ -n "$LANGUAGE" ]; then
        set -- "$@" --language "$LANGUAGE"
    fi
    if [ -n "$THEME" ]; then
        set -- "$@" --theme "$THEME"
    fi
    "$@"
fi

cd "$REPO_ROOT"

set -- "$VENV_DIR/bin/assistant-agent-kanban" serve --config "$CONFIG_PATH" --host "$HOST" --port "$PORT"
if [ "$RELOAD" = "1" ]; then
    set -- "$@" --reload
fi

exec "$@"
