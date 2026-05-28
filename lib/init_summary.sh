# Shared helper: renders the final init.sh completion summary.

_init_summary_can_use_ansi() {
    [ -t 1 ] && [ "${TERM:-}" != "dumb" ]
}

_init_summary_style_line() {
    _fg=$1
    _bg=$2
    shift 2
    if _init_summary_can_use_ansi; then
        if [ -n "$_bg" ]; then
            printf '\033[48;2;%sm\033[38;2;%sm%s\033[0m\n' "$_bg" "$_fg" "$*"
        else
            printf '\033[38;2;%sm%s\033[0m\n' "$_fg" "$*"
        fi
    else
        printf '%s\n' "$*"
    fi
}

_init_summary_rule() {
    _init_summary_style_line "65;89;112" "" "================================================================================"
}

_init_summary_section() {
    printf '\n'
    _init_summary_style_line "194;172;255" "" "$1"
}

_init_summary_kv() {
    _label=$1
    _value=$2
    if _init_summary_can_use_ansi; then
        printf '  \033[38;2;183;202;255m%-21s\033[0m %s\n' "$_label" "$_value"
    else
        printf '  %-21s %s\n' "$_label" "$_value"
    fi
}

_init_summary_has_selections() {
    [ -n "${REPO_DISCOVERY_ROOT:-}" ] ||
        [ -n "${KANBAN_ROOT:-}" ] ||
        [ -n "${CODING_ASSISTANT:-}" ] ||
        [ -n "${LANGUAGE:-}" ] ||
        [ -n "${THEME:-}" ]
}

init_print_summary() {
    printf '\n'
    _init_summary_rule
    _init_summary_style_line "245;243;255" "88;70;170" "  Assistant Agent Kanban is ready"
    _init_summary_rule

    _init_summary_section "Files"
    [ -n "${VENV_DIR:-}" ] && _init_summary_kv "Virtualenv" "$VENV_DIR"
    [ -n "${CONFIG_PATH:-}" ] && _init_summary_kv "Base config" "$CONFIG_PATH"
    if [ -n "${CONFIG_WRITE_PATH:-}" ] && [ "${CONFIG_WRITE_PATH:-}" != "${CONFIG_PATH:-}" ]; then
        _init_summary_kv "Local config" "$CONFIG_WRITE_PATH"
    fi

    if _init_summary_has_selections; then
        _init_summary_section "Setup selections"
        [ -n "${REPO_DISCOVERY_ROOT:-}" ] && _init_summary_kv "Repo discovery root" "$REPO_DISCOVERY_ROOT"
        [ -n "${KANBAN_ROOT:-}" ] && _init_summary_kv "Kanban root" "$KANBAN_ROOT"
        [ -n "${CODING_ASSISTANT:-}" ] && _init_summary_kv "Assistant" "$CODING_ASSISTANT"
        [ -n "${LANGUAGE:-}" ] && _init_summary_kv "UI language" "$LANGUAGE"
        [ -n "${THEME:-}" ] && _init_summary_kv "UI theme" "$THEME"
    fi

    _init_summary_section "Start the dashboard"
    if _init_summary_can_use_ansi; then
        printf '  \033[38;2;0;169;232m./run.sh\033[0m\n'
    else
        printf '  ./run.sh\n'
    fi

    printf '\n'
    _init_summary_rule
}
