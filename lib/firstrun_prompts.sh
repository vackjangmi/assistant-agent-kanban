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

_firstrun_can_use_ansi() {
    [ -t 1 ] && [ "${TERM:-}" != "dumb" ]
}

_firstrun_cancel() {
    printf '%s\n' "Setup cancelled."
    return 130
}

_firstrun_back() {
    return 125
}

_firstrun_banner_line() {
    _rgb=$1
    shift
    if _firstrun_can_use_ansi; then
        printf '\033[48;2;13;18;25m\033[38;2;%sm%s\033[0m\n' "$_rgb" "$*"
    else
        printf '%s\n' "$*"
    fi
}

_firstrun_render_intro() {
    if _firstrun_can_use_ansi; then
        printf '\033[2J\033[H'
    fi

    _firstrun_banner_line "65;89;112" "------------------------------------------------------------------------------------------------------------"
    _firstrun_banner_line "216;207;252" " █████╗ ███████╗███████╗██╗███████╗████████╗ █████╗ ███╗   ██╗████████╗"
    _firstrun_banner_line "194;172;255" "██╔══██╗██╔════╝██╔════╝██║██╔════╝╚══██╔══╝██╔══██╗████╗  ██║╚══██╔══╝"
    _firstrun_banner_line "165;124;255" "███████║███████╗███████╗██║███████╗   ██║   ███████║██╔██╗ ██║   ██║"
    _firstrun_banner_line "139;83;245" "██╔══██║╚════██║╚════██║██║╚════██║   ██║   ██╔══██║██║╚██╗██║   ██║"
    _firstrun_banner_line "125;46;232" "██║  ██║███████║███████║██║███████║   ██║   ██║  ██║██║ ╚████║   ██║"
    _firstrun_banner_line "104;31;207" "╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝"
    _firstrun_banner_line "216;226;255" " █████╗  ██████╗ ███████╗███╗   ██╗████████╗    ██╗  ██╗ █████╗ ███╗   ██╗██████╗  █████╗ ███╗   ██╗"
    _firstrun_banner_line "183;202;255" "██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝    ██║ ██╔╝██╔══██╗████╗  ██║██╔══██╗██╔══██╗████╗  ██║"
    _firstrun_banner_line "139;160;255" "███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║       █████╔╝ ███████║██╔██╗ ██║██████╔╝███████║██╔██╗ ██║"
    _firstrun_banner_line "100;114;236" "██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║       ██╔═██╗ ██╔══██║██║╚██╗██║██╔══██╗██╔══██║██║╚██╗██║"
    _firstrun_banner_line "54;159;246" "██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║       ██║  ██╗██║  ██║██║ ╚████║██████╔╝██║  ██║██║ ╚████║"
    _firstrun_banner_line "0;169;232" "╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝       ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝"
    _firstrun_banner_line "65;89;112" "------------------------------------------------------------------------------------------------------------"
    printf '\n'
}

_firstrun_style_line() {
    _fg=$1
    _bg=$2
    shift 2
    if _firstrun_can_use_ansi; then
        if [ -n "$_bg" ]; then
            printf '\033[48;2;%sm\033[38;2;%sm%s\033[0m\n' "$_bg" "$_fg" "$*"
        else
            printf '\033[38;2;%sm%s\033[0m\n' "$_fg" "$*"
        fi
    else
        printf '%s\n' "$*"
    fi
}

_firstrun_render_step_header() {
    _step_num=$1
    _step_total=$2
    _title=$3
    _description=$4

    _firstrun_render_intro
    _firstrun_style_line "216;226;255" "" "Setup step $_step_num/$_step_total"
    _firstrun_style_line "194;172;255" "" "$_title"
    printf '%s\n' "$_description"
    printf '\n'
}

_firstrun_render_choice_screen() {
    _step_num=$1
    _step_total=$2
    _title=$3
    _description=$4
    _label=$5
    _selected_idx=$6
    _default_idx=$7
    _allow_back=$8
    shift 8

    _firstrun_render_step_header "$_step_num" "$_step_total" "$_title" "$_description"
    printf '%s\n' "$_label"
    if [ "$_allow_back" = "1" ]; then
        _firstrun_style_line "139;160;255" "" "Use Up/Down or j/k, Enter to choose, b/Left to go back, q/Esc/Ctrl-C to quit."
    else
        _firstrun_style_line "139;160;255" "" "Use Up/Down or j/k, Enter to choose, q/Esc/Ctrl-C to quit."
    fi
    printf '\n'

    _idx=1
    for _opt in "$@"; do
        _suffix=
        if [ "$_idx" = "$_default_idx" ]; then
            _suffix="  default"
        fi

        if [ "$_idx" = "$_selected_idx" ]; then
            if _firstrun_can_use_ansi; then
                printf '\033[48;2;88;70;170m\033[38;2;245;243;255m  ▶ %s%s  \033[0m\n' "$_opt" "$_suffix"
            else
                printf '=> %s%s\n' "$_opt" "$_suffix"
            fi
        else
            printf '   %s%s\n' "$_opt" "$_suffix"
        fi
        _idx=$((_idx + 1))
    done
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

_firstrun_choice_index() {
    _wanted_choice=$1
    shift
    _CHOICE_INDEX=1
    for _opt in "$@"; do
        if [ "$_opt" = "$_wanted_choice" ]; then
            return 0
        fi
        _CHOICE_INDEX=$((_CHOICE_INDEX + 1))
    done
    _CHOICE_INDEX=1
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

_firstrun_prompt_choice_screen_with_keys() {
    _step_num=$1
    _step_total=$2
    _title=$3
    _description=$4
    _label=$5
    _default_idx=$6
    _allow_back=$7
    shift 7
    _count=$#
    _selected_idx=$_default_idx
    _enter=$(printf '\r')
    _newline=$(printf '\n')
    _esc=$(printf '\033')
    _up=$(printf '\033[A')
    _down=$(printf '\033[B')
    _left=$(printf '\033[D')
    _backspace=$(printf '\177')
    _ctrl_h=$(printf '\010')
    _ctrl_c=$(printf '\003')

    printf '\033[?25l'
    _firstrun_render_choice_screen "$_step_num" "$_step_total" "$_title" "$_description" "$_label" "$_selected_idx" "$_default_idx" "$_allow_back" "$@"
    while true; do
        _firstrun_read_key || {
            printf '\033[?25h'
            return 1
        }
        case "$_KEY" in
            "$_enter"|"$_newline")
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
            "$_left"|b|B|"$_backspace"|"$_ctrl_h")
                if [ "$_allow_back" = "1" ]; then
                    printf '\033[?25h'
                    _firstrun_back
                    return $?
                fi
                continue
                ;;
            "$_esc"|q|Q|"$_ctrl_c")
                printf '\033[?25h'
                _firstrun_cancel
                return $?
                ;;
            *)
                continue
                ;;
        esac
        _firstrun_render_choice_screen "$_step_num" "$_step_total" "$_title" "$_description" "$_label" "$_selected_idx" "$_default_idx" "$_allow_back" "$@"
    done
}

_firstrun_prompt_choice_screen() {
    _step_num=$1
    _step_total=$2
    _title=$3
    _description=$4
    _label=$5
    _default_idx=$6
    _allow_back=$7
    shift 7
    _count=$#

    if _firstrun_can_key_select; then
        _firstrun_prompt_choice_screen_with_keys "$_step_num" "$_step_total" "$_title" "$_description" "$_label" "$_default_idx" "$_allow_back" "$@"
        return $?
    fi

    _firstrun_render_choice_screen "$_step_num" "$_step_total" "$_title" "$_description" "$_label" "$_default_idx" "$_default_idx" "$_allow_back" "$@"
    if [ "$_allow_back" = "1" ]; then
        printf 'Selection [%d, b to go back, q to quit]: ' "$_default_idx"
    else
        printf 'Selection [%d, q to quit]: ' "$_default_idx"
    fi
    IFS= read -r _ans || _ans=
    case "$_ans" in
        '') _ans=$_default_idx ;;
        b|B|back|BACK)
            if [ "$_allow_back" = "1" ]; then
                _firstrun_back
                return $?
            fi
            _ans=$_default_idx
            ;;
        q|Q|quit|QUIT|exit|EXIT)
            _firstrun_cancel
            return $?
            ;;
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

_firstrun_render_review_screen() {
    _step_num=$1
    _step_total=$2
    _allow_back=$3

    _firstrun_render_step_header "$_step_num" "$_step_total" "Review selections" "Confirm these setup values before initialization starts."
    printf '%s\n' "Selected values:"
    printf '  Repo discovery root: %s\n' "${REPO_DISCOVERY_ROOT:-not set}"
    printf '  Coding assistant: %s\n' "${CODING_ASSISTANT:-built-in default}"
    printf '  UI language: %s\n' "${LANGUAGE:-EN}"
    printf '  UI theme: %s\n' "${THEME:-light}"
    if [ -n "${_FIRSTRUN_ASSISTANT_NOTICE:-}" ]; then
        printf '\n%s\n' "$_FIRSTRUN_ASSISTANT_NOTICE"
    fi
    printf '\n'
    if [ "$_allow_back" = "1" ]; then
        _firstrun_style_line "139;160;255" "" "Press Enter/OK to continue, b/Left to go back, q/Esc/Ctrl-C to quit."
    else
        _firstrun_style_line "139;160;255" "" "Press Enter/OK to continue, q/Esc/Ctrl-C to quit."
    fi
}

_firstrun_prompt_review_screen_with_keys() {
    _step_num=$1
    _step_total=$2
    _allow_back=$3
    _enter=$(printf '\r')
    _newline=$(printf '\n')
    _esc=$(printf '\033')
    _left=$(printf '\033[D')
    _backspace=$(printf '\177')
    _ctrl_h=$(printf '\010')
    _ctrl_c=$(printf '\003')

    printf '\033[?25l'
    _firstrun_render_review_screen "$_step_num" "$_step_total" "$_allow_back"
    while true; do
        _firstrun_read_key || {
            printf '\033[?25h'
            return 1
        }
        case "$_KEY" in
            "$_enter"|"$_newline")
                printf '\033[?25h'
                return 0
                ;;
            "$_left"|b|B|"$_backspace"|"$_ctrl_h")
                if [ "$_allow_back" = "1" ]; then
                    printf '\033[?25h'
                    _firstrun_back
                    return $?
                fi
                ;;
            "$_esc"|q|Q|"$_ctrl_c")
                printf '\033[?25h'
                _firstrun_cancel
                return $?
                ;;
        esac
    done
}

_firstrun_prompt_review_screen() {
    _step_num=$1
    _step_total=$2
    _allow_back=$3

    if _firstrun_can_key_select; then
        _firstrun_prompt_review_screen_with_keys "$_step_num" "$_step_total" "$_allow_back"
        return $?
    fi

    while true; do
        _firstrun_render_review_screen "$_step_num" "$_step_total" "$_allow_back"
        if [ "$_allow_back" = "1" ]; then
            printf '%s' "Continue? [Y, b back, q quit]: "
        else
            printf '%s' "Continue? [Y, q quit]: "
        fi
        IFS= read -r _ans || _ans=
        case "$_ans" in
            ''|y|Y|yes|YES|ok|OK|Ok)
                return 0
                ;;
            b|B|back|BACK)
                if [ "$_allow_back" = "1" ]; then
                    _firstrun_back
                    return $?
                fi
                ;;
            q|Q|quit|QUIT|exit|EXIT)
                _firstrun_cancel
                return $?
                ;;
            *)
                printf '%s\n' "Invalid answer; press Enter to continue, b to go back, or q to quit."
                ;;
        esac
    done
}

_firstrun_render_choice_menu() {
    _label=$1
    _selected_idx=$2
    _default_idx=$3
    shift 3

    printf '%s\n' "$_label"
    printf '%s\n' "Use Up/Down or j/k, then Enter. Press q, Esc, or Ctrl-C to quit."
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
    _newline=$(printf '\n')
    _esc=$(printf '\033')
    _up=$(printf '\033[A')
    _down=$(printf '\033[B')
    _ctrl_c=$(printf '\003')

    printf '\033[?25l'
    _firstrun_render_choice_menu "$_label" "$_selected_idx" "$_default_idx" "$@"
    while true; do
        _firstrun_read_key || {
            printf '\033[?25h'
            return 1
        }
        case "$_KEY" in
            "$_enter"|"$_newline")
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
            "$_esc"|q|Q|"$_ctrl_c")
                printf '\033[?25h'
                _firstrun_cancel
                return $?
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
        return $?
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
    printf 'Selection [%d, q to quit]: ' "$_default_idx"
    IFS= read -r _ans || _ans=
    case "$_ans" in
        '') _ans=$_default_idx ;;
        q|Q|quit|QUIT|exit|EXIT)
            _firstrun_cancel
            return $?
            ;;
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
    printf '%s' "Repo discovery root path (q to quit): "
    IFS= read -r _input || _input=
    case "$_input" in
        q|Q|quit|QUIT|exit|EXIT)
            _firstrun_cancel
            return $?
            ;;
    esac
    if [ -n "$_input" ]; then
        REPO_DISCOVERY_ROOT=$_input
    else
        REPO_DISCOVERY_ROOT=$_fallback_root
    fi
}

_firstrun_prepare_repo_root_choices() {
    _repo_parent=$(_firstrun_path_key "$REPO_ROOT/..")
    _repo_grandparent=$(_firstrun_path_key "$REPO_ROOT/../..")
    _fallback_root=$_repo_parent
    _default_root=$_repo_parent

    if [ -n "${REPO_DISCOVERY_ROOT:-}" ]; then
        _default_root=$(_firstrun_path_key "$REPO_DISCOVERY_ROOT")
    fi

    if [ -d "$HOME/git" ]; then
        _home_git=$(_firstrun_path_key "$HOME/git")
        if [ -z "${REPO_DISCOVERY_ROOT:-}" ]; then
            case "$(_firstrun_path_key "$REPO_ROOT")/" in
                "$_home_git"/*) _default_root=$_home_git ;;
            esac
        fi
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
    if [ -n "${REPO_DISCOVERY_ROOT:-}" ]; then
        _firstrun_add_root_candidate "$REPO_DISCOVERY_ROOT" "$_default_root"
    fi
}

_firstrun_prompt_repo_root() {
    _firstrun_prepare_repo_root_choices
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
    _firstrun_prompt_choice "Choose repo discovery root:" "$_ROOT_DEFAULT_IDX" "$@" || return $?
    if [ "$_CHOICE" = "$_custom_choice" ]; then
        _firstrun_prompt_custom_repo_root || return $?
    else
        REPO_DISCOVERY_ROOT=$_CHOICE
    fi
}

_firstrun_prompt_custom_repo_root_screen() {
    _step_num=$1
    _step_total=$2
    _allow_back=$3

    while true; do
        _firstrun_render_step_header "$_step_num" "$_step_total" "Repo discovery root" "Type the directory that contains your target repositories."
        if [ "$_allow_back" = "1" ]; then
            printf '%s' "Repo discovery root path [Enter keeps default, b back, q quit]: "
        else
            printf '%s' "Repo discovery root path [Enter keeps default, q quit]: "
        fi
        IFS= read -r _input || _input=
        case "$_input" in
            b|B|back|BACK)
                if [ "$_allow_back" = "1" ]; then
                    _firstrun_back
                    return $?
                fi
                ;;
            q|Q|quit|QUIT|exit|EXIT)
                _firstrun_cancel
                return $?
                ;;
            *)
                if [ -n "$_input" ]; then
                    REPO_DISCOVERY_ROOT=$_input
                else
                    REPO_DISCOVERY_ROOT=$_fallback_root
                fi
                return 0
                ;;
        esac
    done
}

_firstrun_prompt_repo_root_screen() {
    _step_num=$1
    _step_total=$2
    _allow_back=$3
    _firstrun_prepare_repo_root_choices

    set --
    _idx=1
    while [ "$_idx" -le "$_ROOT_CHOICE_COUNT" ]; do
        eval "_root_choice=\${_ROOT_CHOICE_VALUE_$_idx}"
        set -- "$@" "$_root_choice"
        _idx=$((_idx + 1))
    done
    _custom_choice="Enter a custom path..."
    set -- "$@" "$_custom_choice"

    while true; do
        if _firstrun_prompt_choice_screen "$_step_num" "$_step_total" "Repo discovery root" "Choose the directory that contains your target repositories." "Choose repo discovery root:" "$_ROOT_DEFAULT_IDX" "$_allow_back" "$@"; then
            if [ "$_CHOICE" = "$_custom_choice" ]; then
                if _firstrun_prompt_custom_repo_root_screen "$_step_num" "$_step_total" 1; then
                    return 0
                else
                    _status=$?
                    case "$_status" in
                        125) continue ;;
                        *) return "$_status" ;;
                    esac
                fi
            else
                REPO_DISCOVERY_ROOT=$_CHOICE
                return 0
            fi
        else
            _status=$?
            return "$_status"
        fi
    done
}

_firstrun_step_enabled() {
    case "$1" in
        repo) [ "${_FIRSTRUN_PROMPT_REPO:-0}" = "1" ] ;;
        assistant) [ "${_FIRSTRUN_PROMPT_ASSISTANT:-0}" = "1" ] ;;
        language) [ "${_FIRSTRUN_PROMPT_LANGUAGE:-0}" = "1" ] ;;
        theme) [ "${_FIRSTRUN_PROMPT_THEME:-0}" = "1" ] ;;
        *) return 1 ;;
    esac
}

_firstrun_first_step() {
    for _candidate_step in repo assistant language theme; do
        if _firstrun_step_enabled "$_candidate_step"; then
            _FIRSTRUN_STEP=$_candidate_step
            return 0
        fi
    done
    return 1
}

_firstrun_next_step() {
    _current_step=$1
    _seen_current=0
    for _candidate_step in repo assistant language theme; do
        if [ "$_seen_current" = "1" ] && _firstrun_step_enabled "$_candidate_step"; then
            _FIRSTRUN_STEP=$_candidate_step
            return 0
        fi
        if [ "$_candidate_step" = "$_current_step" ]; then
            _seen_current=1
        fi
    done
    return 1
}

_firstrun_previous_step() {
    _current_step=$1
    _previous_step=
    for _candidate_step in repo assistant language theme; do
        if [ "$_candidate_step" = "$_current_step" ]; then
            if [ -n "$_previous_step" ]; then
                _FIRSTRUN_STEP=$_previous_step
                return 0
            fi
            return 1
        fi
        if _firstrun_step_enabled "$_candidate_step"; then
            _previous_step=$_candidate_step
        fi
    done
    return 1
}

_firstrun_last_step() {
    _last_step=
    for _candidate_step in repo assistant language theme; do
        if _firstrun_step_enabled "$_candidate_step"; then
            _last_step=$_candidate_step
        fi
    done
    if [ -n "$_last_step" ]; then
        _FIRSTRUN_STEP=$_last_step
        return 0
    fi
    return 1
}

_firstrun_step_number() {
    _target_step=$1
    _FIRSTRUN_STEP_NUMBER=0
    for _candidate_step in repo assistant language theme; do
        if _firstrun_step_enabled "$_candidate_step"; then
            _FIRSTRUN_STEP_NUMBER=$((_FIRSTRUN_STEP_NUMBER + 1))
        fi
        if [ "$_candidate_step" = "$_target_step" ]; then
            return 0
        fi
    done
    return 1
}

firstrun_prompts() {
    if [ "${FIRST_RUN_LOCAL_MISSING:-0}" != "1" ]; then
        return 0
    fi
    if [ ! -t 0 ] || [ ! -t 1 ]; then
        return 0
    fi

    _FIRSTRUN_PROMPT_REPO=0
    _FIRSTRUN_PROMPT_ASSISTANT=0
    _FIRSTRUN_PROMPT_LANGUAGE=0
    _FIRSTRUN_PROMPT_THEME=0
    _FIRSTRUN_TOTAL_STEPS=0
    _FIRSTRUN_ASSISTANT_NOTICE=

    if [ -z "${REPO_DISCOVERY_ROOT:-}" ]; then
        _FIRSTRUN_PROMPT_REPO=1
        _FIRSTRUN_TOTAL_STEPS=$((_FIRSTRUN_TOTAL_STEPS + 1))
    fi

    _installed=
    _count=0
    if [ -z "${CODING_ASSISTANT:-}" ]; then
        for _tool in claude codex antigravity gemini opencode; do
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
            _FIRSTRUN_ASSISTANT_NOTICE="No coding assistant CLI detected (claude/codex/agy/gemini/opencode). Keeping the default; install one and re-run with --assistant <name> to update."
        elif [ "$_count" -eq 1 ]; then
            CODING_ASSISTANT=$_installed
            _FIRSTRUN_ASSISTANT_NOTICE="Coding assistant: $CODING_ASSISTANT (only one detected, selected automatically)"
        else
            _FIRSTRUN_PROMPT_ASSISTANT=1
            _FIRSTRUN_TOTAL_STEPS=$((_FIRSTRUN_TOTAL_STEPS + 1))
        fi
    fi

    if [ -z "${LANGUAGE:-}" ]; then
        _FIRSTRUN_PROMPT_LANGUAGE=1
        _FIRSTRUN_TOTAL_STEPS=$((_FIRSTRUN_TOTAL_STEPS + 1))
    fi

    if [ -z "${THEME:-}" ]; then
        _FIRSTRUN_PROMPT_THEME=1
        _FIRSTRUN_TOTAL_STEPS=$((_FIRSTRUN_TOTAL_STEPS + 1))
    fi

    if [ "$_FIRSTRUN_TOTAL_STEPS" -eq 0 ]; then
        if [ -n "$_FIRSTRUN_ASSISTANT_NOTICE" ]; then
            printf '%s\n' "$_FIRSTRUN_ASSISTANT_NOTICE"
        fi
        return 0
    fi

    _FIRSTRUN_TOTAL_STEPS=$((_FIRSTRUN_TOTAL_STEPS + 1))

    _firstrun_first_step || return 0
    while true; do
        _firstrun_step_number "$_FIRSTRUN_STEP" || return 1
        _allow_back=0
        _current_step=$_FIRSTRUN_STEP
        if _firstrun_previous_step "$_current_step"; then
            _allow_back=1
            _FIRSTRUN_STEP=$_current_step
        fi

        case "$_FIRSTRUN_STEP" in
            repo)
                if _firstrun_prompt_repo_root_screen "$_FIRSTRUN_STEP_NUMBER" "$_FIRSTRUN_TOTAL_STEPS" "$_allow_back"; then
                    :
                else
                    _status=$?
                    case "$_status" in
                        125)
                            _firstrun_previous_step "$_FIRSTRUN_STEP" || true
                            continue
                            ;;
                        *) return "$_status" ;;
                    esac
                fi
                ;;
            assistant)
                _assistant_default_idx=1
                if [ -n "${CODING_ASSISTANT:-}" ]; then
                    # shellcheck disable=SC2086
                    _firstrun_choice_index "$CODING_ASSISTANT" $_installed || true
                    _assistant_default_idx=$_CHOICE_INDEX
                fi
                # shellcheck disable=SC2086
                if _firstrun_prompt_choice_screen "$_FIRSTRUN_STEP_NUMBER" "$_FIRSTRUN_TOTAL_STEPS" "Default coding assistant" "Choose which installed assistant CLI should run worker tasks by default." "Choose the default coding assistant:" "$_assistant_default_idx" "$_allow_back" $_installed; then
                    CODING_ASSISTANT=$_CHOICE
                else
                    _status=$?
                    case "$_status" in
                        125)
                            _firstrun_previous_step "$_FIRSTRUN_STEP" || true
                            continue
                            ;;
                        *) return "$_status" ;;
                    esac
                fi
                ;;
            language)
                _language_default_idx=1
                case "${LANGUAGE:-}" in
                    KO) _language_default_idx=2 ;;
                esac
                if _firstrun_prompt_choice_screen "$_FIRSTRUN_STEP_NUMBER" "$_FIRSTRUN_TOTAL_STEPS" "UI language" "Choose the language used by the dashboard UI." "Choose UI language:" "$_language_default_idx" "$_allow_back" EN KO; then
                    LANGUAGE=$_CHOICE
                else
                    _status=$?
                    case "$_status" in
                        125)
                            _firstrun_previous_step "$_FIRSTRUN_STEP" || true
                            continue
                            ;;
                        *) return "$_status" ;;
                    esac
                fi
                ;;
            theme)
                _theme_default_idx=1
                case "${THEME:-}" in
                    dark) _theme_default_idx=2 ;;
                esac
                if _firstrun_prompt_choice_screen "$_FIRSTRUN_STEP_NUMBER" "$_FIRSTRUN_TOTAL_STEPS" "UI theme" "Choose the dashboard color theme." "Choose UI theme:" "$_theme_default_idx" "$_allow_back" light dark; then
                    THEME=$_CHOICE
                else
                    _status=$?
                    case "$_status" in
                        125)
                            _firstrun_previous_step "$_FIRSTRUN_STEP" || true
                            continue
                            ;;
                        *) return "$_status" ;;
                    esac
                fi
                ;;
        esac

        if _firstrun_next_step "$_FIRSTRUN_STEP"; then
            continue
        fi
        if _firstrun_prompt_review_screen "$_FIRSTRUN_TOTAL_STEPS" "$_FIRSTRUN_TOTAL_STEPS" 1; then
            break
        else
            _status=$?
            case "$_status" in
                125)
                    _firstrun_last_step || return 1
                    continue
                    ;;
                *) return "$_status" ;;
            esac
        fi
    done

}
