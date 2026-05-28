#!/bin/sh
set -eu

usage() {
    printf '%s\n' "Usage: ./init.sh [--config PATH] [--root PATH] [--kanban-root PATH] [--assistant NAME] [--language LANG] [--theme THEME]"
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
DEPS_STAMP_FILE="$VENV_DIR/.assistant-agent-kanban-deps-stamp"

. "$REPO_ROOT/lib/python_runtime.sh"

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

FIRST_RUN_LOCAL_MISSING=0
if [ "$CONFIG_PATH_EXPLICIT" = "0" ] && [ ! -f "$REPO_ROOT/config.local.yaml" ]; then
    FIRST_RUN_LOCAL_MISSING=1
fi

. "$REPO_ROOT/lib/firstrun_prompts.sh"
firstrun_prompts

if [ -n "$CODING_ASSISTANT" ]; then
    case "$CODING_ASSISTANT" in
        claude|codex|antigravity|agy|gemini|opencode)
            if [ "$CODING_ASSISTANT" = "agy" ]; then
                CODING_ASSISTANT=antigravity
            fi
            ;;
        *)
            printf 'Invalid --assistant value: %s (expected claude, codex, antigravity, gemini, or opencode)\n' "$CODING_ASSISTANT" >&2
            exit 1
            ;;
    esac
fi

if [ -n "$LANGUAGE" ]; then
    case "$LANGUAGE" in
        EN|en|English|english) LANGUAGE=EN ;;
        KO|ko|KR|kr|Korean|korean) LANGUAGE=KO ;;
        *)
            printf 'Invalid --language value: %s (expected EN or KO)\n' "$LANGUAGE" >&2
            exit 1
            ;;
    esac
fi

if [ -n "$THEME" ]; then
    case "$THEME" in
        light|Light|LIGHT) THEME=light ;;
        dark|Dark|DARK) THEME=dark ;;
        *)
            printf 'Invalid --theme value: %s (expected light or dark)\n' "$THEME" >&2
            exit 1
            ;;
    esac
fi

if [ -x "$VENV_DIR/bin/python" ]; then
    PYTHON_BIN="$VENV_DIR/bin/python"
    require_python_min_version "$PYTHON_BIN" "existing virtual environment" || exit 1
else
    PYTHON_CMD=$(find_compatible_python) || exit 1
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    PYTHON_BIN="$VENV_DIR/bin/python"
    require_python_min_version "$PYTHON_BIN" "created virtual environment" || exit 1
fi

cd "$REPO_ROOT"

"$PYTHON_BIN" -m pip install -e '.[dev]'
touch "$DEPS_STAMP_FILE"

if [ ! -f "$CONFIG_PATH" ]; then
    mkdir -p "$(dirname -- "$CONFIG_PATH")"
    cp "$REPO_ROOT/examples/config.yaml" "$CONFIG_PATH"
fi

CONFIG_WRITE_PATH=$CONFIG_PATH
if [ "$CONFIG_PATH_EXPLICIT" = "0" ] && { [ -n "$REPO_DISCOVERY_ROOT" ] || [ -n "$KANBAN_ROOT" ] || [ -n "$CODING_ASSISTANT" ] || [ -n "$LANGUAGE" ] || [ -n "$THEME" ]; }; then
    CONFIG_WRITE_PATH="$REPO_ROOT/config.local.yaml"
fi

if [ -n "$REPO_DISCOVERY_ROOT" ] || [ -n "$KANBAN_ROOT" ] || [ -n "$CODING_ASSISTANT" ] || [ -n "$LANGUAGE" ] || [ -n "$THEME" ]; then
    ASSISTANT_AGENT_KANBAN_CONFIG_WRITE=$CONFIG_WRITE_PATH \
    ASSISTANT_AGENT_KANBAN_REPO_DISCOVERY_ROOT=$REPO_DISCOVERY_ROOT \
    ASSISTANT_AGENT_KANBAN_KANBAN_ROOT=$KANBAN_ROOT \
    ASSISTANT_AGENT_KANBAN_CODING_ASSISTANT=$CODING_ASSISTANT \
    ASSISTANT_AGENT_KANBAN_LANGUAGE=$LANGUAGE \
    ASSISTANT_AGENT_KANBAN_THEME=$THEME \
        "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path, PurePosixPath

import yaml

config_path = Path(os.environ["ASSISTANT_AGENT_KANBAN_CONFIG_WRITE"])
root_dir = os.environ.get("ASSISTANT_AGENT_KANBAN_REPO_DISCOVERY_ROOT") or ""
kanban_root = os.environ.get("ASSISTANT_AGENT_KANBAN_KANBAN_ROOT") or ""
coding_assistant = os.environ.get("ASSISTANT_AGENT_KANBAN_CODING_ASSISTANT") or ""
language = os.environ.get("ASSISTANT_AGENT_KANBAN_LANGUAGE") or ""
theme = os.environ.get("ASSISTANT_AGENT_KANBAN_THEME") or ""

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
if coding_assistant or language or theme:
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
        data["runtime"] = runtime
    if coding_assistant:
        runtime["coding_assistant"] = coding_assistant
    if language:
        runtime["language"] = language
    if theme:
        runtime["theme"] = theme

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
if [ -n "$CODING_ASSISTANT" ]; then
    printf 'coding assistant: %s\n' "$CODING_ASSISTANT"
fi
if [ -n "$LANGUAGE" ]; then
    printf 'language: %s\n' "$LANGUAGE"
fi
if [ -n "$THEME" ]; then
    printf 'theme: %s\n' "$THEME"
fi
printf 'next: ./run.sh\n'
