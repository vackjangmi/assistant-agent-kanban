PYTHON_MIN_VERSION=${PYTHON_MIN_VERSION:-3.11}

python_satisfies_min_version() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

python_version_text() {
    "$1" -c 'import sys; print(".".join(str(part) for part in sys.version_info[:3]))' 2>/dev/null || printf '%s\n' "unknown"
}

print_python_version_help() {
    python_cmd=$1
    context=$2
    detected_version=$(python_version_text "$python_cmd")

    printf 'Python %s or newer is required for assistant-agent-kanban.\n' "$PYTHON_MIN_VERSION" >&2
    printf 'Detected %s from %s: %s\n' "$context" "$python_cmd" "$detected_version" >&2
    printf '\n' >&2
    printf '%s\n' "Install Python $PYTHON_MIN_VERSION or newer, then recreate the virtual environment:" >&2
    printf '%s\n' "  rm -rf .venv" >&2
    printf '%s\n' "  ./init.sh" >&2
    printf '\n' >&2
    printf '%s\n' "macOS examples:" >&2
    printf '%s\n' "  brew install python@3.11" >&2
    printf '%s\n' "  pyenv install 3.11" >&2
}

require_python_min_version() {
    python_cmd=$1
    context=$2

    if python_satisfies_min_version "$python_cmd"; then
        return 0
    fi

    print_python_version_help "$python_cmd" "$context"
    return 1
}

find_compatible_python() {
    if [ -n "${PYTHON:-}" ]; then
        if command -v "$PYTHON" >/dev/null 2>&1 && python_satisfies_min_version "$PYTHON"; then
            printf '%s\n' "$PYTHON"
            return 0
        fi
        if command -v "$PYTHON" >/dev/null 2>&1; then
            print_python_version_help "$PYTHON" "PYTHON"
        else
            printf 'PYTHON is set but not executable: %s\n' "$PYTHON" >&2
        fi
        return 1
    fi

    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_satisfies_min_version "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    printf 'Python %s or newer is required for assistant-agent-kanban.\n' "$PYTHON_MIN_VERSION" >&2
    printf '%s\n' "No compatible python executable was found on PATH." >&2
    printf '\n' >&2
    printf '%s\n' "Install Python $PYTHON_MIN_VERSION or newer, then run ./init.sh again." >&2
    printf '%s\n' "macOS examples:" >&2
    printf '%s\n' "  brew install python@3.11" >&2
    printf '%s\n' "  pyenv install 3.11" >&2
    return 1
}
