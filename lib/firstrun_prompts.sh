# Shared helper: prompts the user for first-time setup values.
#
# Source this file from run.sh / init.sh. Callers must define these globals
# (any may be empty):
#   FIRST_RUN_LOCAL_MISSING  -- "1" when config.local.yaml is missing
#   REPO_ROOT                -- absolute repository root
#   REPO_DISCOVERY_ROOT
#   CODING_ASSISTANT
#   LANGUAGE
#   THEME
#
# firstrun_prompts() fills any empty value via an interactive prompt, but only
# when FIRST_RUN_LOCAL_MISSING=1 and both stdin and stdout are TTYs. Values
# already set (via flags / env / prior caller) are left untouched.

_firstrun_can_key_select() {
    [ -t 0 ] && [ -t 1 ] && [ "${TERM:-}" != "dumb" ] && command -v stty >/dev/null 2>&1 && command -v dd >/dev/null 2>&1
}

_firstrun_choice_at_index() {
    _wanted_idx=$1
    shift
    _idx=1
    for _opt in "$@"; do
        if [ "$_idx" = "$_wanted_idx" ]; then
            _CHOICE=$_opt
            return 0
        fi
        _idx=$((_idx + 1))
    done
    return 1
}

_firstrun_read_key() {
    _old_stty=$(stty -g) || return 1
    stty raw -echo min 1 time 0
    _first=$(dd bs=1 count=1 2>/dev/null || true)
    if [ "$_first" = "$(printf '\033')" ]; then
        stty min 0 time 1
        _rest=$(dd bs=1 count=2 2>/dev/null || true)
        _KEY=$_first$_rest
    else
        _KEY=$_first
    fi
    stty "$_old_stty"
}

_firstrun_render_choice_menu() {
    _label=$1
    _selected_idx=$2
    _default_idx=$3
    shift 3

    printf '%s\n' "$_label"
    printf '%s\n' "Use Up/Down or j/k, then Enter. Press q to keep the default."
    _idx=1
    for _opt in "$@"; do
        _prefix="  "
        _suffix=
        if [ "$_idx" = "$_selected_idx" ]; then
            _prefix="> "
        fi
        if [ "$_idx" = "$_default_idx" ]; then
            _suffix=" (default)"
        fi
        printf '%s%s%s\n' "$_prefix" "$_opt" "$_suffix"
        _idx=$((_idx + 1))
    done
}

_firstrun_prompt_choice_with_keys() {
    _label=$1
    _default_idx=$2
    shift 2
    _count=$#
    _selected_idx=$_default_idx
    _line_count=$((_count + 2))
    _enter=$(printf '\r')
    _up=$(printf '\033[A')
    _down=$(printf '\033[B')

    printf '\033[?25l'
    _firstrun_render_choice_menu "$_label" "$_selected_idx" "$_default_idx" "$@"
    while true; do
        _firstrun_read_key || {
            printf '\033[?25h'
            return 1
        }
        case "$_KEY" in
            "$_enter")
                printf '\033[?25h'
                _firstrun_choice_at_index "$_selected_idx" "$@"
                return 0
                ;;
            "$_up"|k|K)
                if [ "$_selected_idx" -le 1 ]; then
                    _selected_idx=$_count
                else
                    _selected_idx=$((_selected_idx - 1))
                fi
                ;;
            "$_down"|j|J)
                if [ "$_selected_idx" -ge "$_count" ]; then
                    _selected_idx=1
                else
                    _selected_idx=$((_selected_idx + 1))
                fi
                ;;
            q|Q)
                printf '\033[?25h'
                _firstrun_choice_at_index "$_default_idx" "$@"
                return 0
                ;;
            *)
                continue
                ;;
        esac
        printf '\033[%dA\033[J' "$_line_count"
        _firstrun_render_choice_menu "$_label" "$_selected_idx" "$_default_idx" "$@"
    done
}

_firstrun_prompt_choice() {
    _label="$1"
    _default_idx="$2"
    shift 2
    _count=$#
    if _firstrun_can_key_select; then
        _firstrun_prompt_choice_with_keys "$_label" "$_default_idx" "$@"
        return 0
    fi
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
    _firstrun_choice_at_index "$_ans" "$@"
}

_firstrun_path_key() {
    if [ -d "$1" ]; then
        (cd "$1" 2>/dev/null && pwd -P) || printf '%s\n' "$1"
    else
        printf '%s\n' "$1"
    fi
}

_firstrun_add_root_candidate() {
    _candidate=$1
    _default_key=$2
    [ -d "$_candidate" ] || return 0
    _candidate_key=$(_firstrun_path_key "$_candidate")

    _idx=1
    while [ "$_idx" -le "$_ROOT_CHOICE_COUNT" ]; do
        eval "_existing_key=\${_ROOT_CHOICE_KEY_$_idx}"
        if [ "$_existing_key" = "$_candidate_key" ]; then
            return 0
        fi
        _idx=$((_idx + 1))
    done

    _ROOT_CHOICE_COUNT=$((_ROOT_CHOICE_COUNT + 1))
    eval "_ROOT_CHOICE_VALUE_$_ROOT_CHOICE_COUNT=\$_candidate_key"
    eval "_ROOT_CHOICE_KEY_$_ROOT_CHOICE_COUNT=\$_candidate_key"
    if [ "$_candidate_key" = "$_default_key" ]; then
        _ROOT_DEFAULT_IDX=$_ROOT_CHOICE_COUNT
    fi
}

_firstrun_prompt_custom_repo_root() {
    printf '%s' "Repo discovery root path: "
    IFS= read -r _input || _input=
    if [ -n "$_input" ]; then
        REPO_DISCOVERY_ROOT=$_input
    else
        REPO_DISCOVERY_ROOT=$_fallback_root
    fi
}

_firstrun_prompt_repo_root() {
    _repo_parent=$(_firstrun_path_key "$REPO_ROOT/..")
    _repo_grandparent=$(_firstrun_path_key "$REPO_ROOT/../..")
    _fallback_root=$_repo_parent
    _default_root=$_repo_parent

    if [ -d "$HOME/git" ]; then
        _home_git=$(_firstrun_path_key "$HOME/git")
        case "$(_firstrun_path_key "$REPO_ROOT")/" in
            "$_home_git"/*) _default_root=$_home_git ;;
        esac
    else
        _home_git=
    fi

    _ROOT_CHOICE_COUNT=0
    _ROOT_DEFAULT_IDX=1
    if [ -n "$_home_git" ]; then
        _firstrun_add_root_candidate "$_home_git" "$_default_root"
    fi
    _firstrun_add_root_candidate "$_repo_grandparent" "$_default_root"
    _firstrun_add_root_candidate "$_repo_parent" "$_default_root"
    _firstrun_add_root_candidate "$REPO_ROOT" "$_default_root"

    set --
    _idx=1
    while [ "$_idx" -le "$_ROOT_CHOICE_COUNT" ]; do
        eval "_root_choice=\${_ROOT_CHOICE_VALUE_$_idx}"
        set -- "$@" "$_root_choice"
        _idx=$((_idx + 1))
    done
    _custom_choice="Enter a custom path..."
    set -- "$@" "$_custom_choice"

    printf '%s\n' "First-time setup: choose the root directory that contains your target repositories."
    _firstrun_prompt_choice "Choose repo discovery root:" "$_ROOT_DEFAULT_IDX" "$@"
    if [ "$_CHOICE" = "$_custom_choice" ]; then
        _firstrun_prompt_custom_repo_root
    else
        REPO_DISCOVERY_ROOT=$_CHOICE
    fi
}

firstrun_prompts() {
    if [ "${FIRST_RUN_LOCAL_MISSING:-0}" != "1" ]; then
        return 0
    fi
    if [ ! -t 0 ] || [ ! -t 1 ]; then
        return 0
    fi

    if [ -z "${REPO_DISCOVERY_ROOT:-}" ]; then
        _firstrun_prompt_repo_root
    fi

    if [ -z "${CODING_ASSISTANT:-}" ]; then
        _installed=
        _count=0
        for _tool in antigravity opencode codex gemini claude; do
            _binary=$_tool
            if [ "$_tool" = "antigravity" ]; then
                _binary=agy
            fi
            if command -v "$_binary" >/dev/null 2>&1; then
                _installed="${_installed:+$_installed }$_tool"
                _count=$((_count + 1))
            fi
        done
        if [ "$_count" -eq 0 ]; then
            printf '%s\n' "No coding assistant CLI detected (agy/opencode/codex/gemini/claude). Keeping the default; install one and re-run with --assistant <name> to update."
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
