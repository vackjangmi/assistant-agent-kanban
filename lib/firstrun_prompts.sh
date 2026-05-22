# Shared helper: prompts the user for first-time setup values.
#
# Source this file from run.sh / init.sh. Callers must define these globals
# (any may be empty):
#   FIRST_RUN_LOCAL_MISSING  -- "1" when config.local.yaml is missing
#   REPO_DISCOVERY_ROOT
#   CODING_ASSISTANT
#   LANGUAGE
#   THEME
#
# firstrun_prompts() fills any empty value via an interactive prompt, but only
# when FIRST_RUN_LOCAL_MISSING=1 and both stdin and stdout are TTYs. Values
# already set (via flags / env / prior caller) are left untouched.

_firstrun_prompt_choice() {
    _label="$1"
    _default_idx="$2"
    shift 2
    _count=$#
    printf '%s\n' "$_label"
    _idx=1
    for _opt in "$@"; do
        if [ "$_idx" = "$_default_idx" ]; then
            printf '  %d) %s (default)\n' "$_idx" "$_opt"
        else
            printf '  %d) %s\n' "$_idx" "$_opt"
        fi
        _idx=$((_idx + 1))
    done
    printf 'Selection [%d]: ' "$_default_idx"
    IFS= read -r _ans || _ans=
    case "$_ans" in
        '') _ans=$_default_idx ;;
        *[!0-9]*)
            printf '%s\n' "Invalid selection; defaulting to $_default_idx"
            _ans=$_default_idx
            ;;
    esac
    if [ "$_ans" -lt 1 ] || [ "$_ans" -gt "$_count" ]; then
        printf '%s\n' "Selection out of range; defaulting to $_default_idx"
        _ans=$_default_idx
    fi
    _idx=1
    for _opt in "$@"; do
        if [ "$_idx" = "$_ans" ]; then
            _CHOICE=$_opt
            return 0
        fi
        _idx=$((_idx + 1))
    done
}

firstrun_prompts() {
    if [ "${FIRST_RUN_LOCAL_MISSING:-0}" != "1" ]; then
        return 0
    fi
    if [ ! -t 0 ] || [ ! -t 1 ]; then
        return 0
    fi

    if [ -z "${REPO_DISCOVERY_ROOT:-}" ]; then
        printf '%s\n' "First-time setup: choose the root directory that contains your target repositories."
        printf '%s' "Repo discovery root [../]: "
        IFS= read -r _input || _input=
        if [ -n "$_input" ]; then
            REPO_DISCOVERY_ROOT=$_input
        else
            REPO_DISCOVERY_ROOT=../
        fi
    fi

    if [ -z "${CODING_ASSISTANT:-}" ]; then
        _installed=
        _count=0
        for _tool in opencode codex gemini claude; do
            if command -v "$_tool" >/dev/null 2>&1; then
                _installed="${_installed:+$_installed }$_tool"
                _count=$((_count + 1))
            fi
        done
        if [ "$_count" -eq 0 ]; then
            printf '%s\n' "No coding assistant CLI detected (opencode/codex/gemini/claude). Keeping the default; install one and re-run with --assistant <name> to update."
        elif [ "$_count" -eq 1 ]; then
            CODING_ASSISTANT=$_installed
            printf 'Coding assistant: %s (only one detected, selected automatically)\n' "$CODING_ASSISTANT"
        else
            # shellcheck disable=SC2086
            _firstrun_prompt_choice "Choose the default coding assistant:" 1 $_installed
            CODING_ASSISTANT=$_CHOICE
        fi
    fi

    if [ -z "${LANGUAGE:-}" ]; then
        _firstrun_prompt_choice "Choose UI language:" 1 EN KO
        LANGUAGE=$_CHOICE
    fi

    if [ -z "${THEME:-}" ]; then
        _firstrun_prompt_choice "Choose UI theme:" 1 light dark
        THEME=$_CHOICE
    fi
}
