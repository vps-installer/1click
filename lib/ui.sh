#!/usr/bin/env bash
# Cobalt Night: presentation-only layer for the installer terminal UI.
# Requires lib/common.sh; deliberately leaves program logic, secrets and stdin alone.

UI_SECTION=''
UI_STATE=''
UI_TITLE=''
UI_COLOR_ACTIVE=0
UI_BLUE='' UI_LIGHT='' UI_GREEN='' UI_AMBER='' UI_MUTED='' UI_WHITE='' UI_RED=''
UI_BOLD='' UI_RESET=''

ui_init() {
  UI_COLOR_ACTIVE=0
  [[ ! -v NO_COLOR ]] || return
  case "${VPS_UI_COLOR:-auto}" in
    never) return ;;
    always) UI_COLOR_ACTIVE=1 ;;
    auto)
      [[ ! -v NO_COLOR && -t 4 && "${TERM:-dumb}" != dumb ]] || return
      UI_COLOR_ACTIVE=1 ;;
    *) return ;;
  esac
  # Use a conservative ANSI fallback for terminals that lack truecolor support.
  local depth=8
  if [[ ${COLORTERM:-} == truecolor || ${COLORTERM:-} == 24bit ]]; then
    depth=24
  elif command -v tput >/dev/null 2>&1; then
    depth="$(tput colors 2>/dev/null || echo 8)"
    [[ "$depth" =~ ^[0-9]+$ ]] || depth=8
  fi
  if ((depth == 24)); then
    UI_BLUE=$'\033[38;2;59;130;246m'
    UI_LIGHT=$'\033[38;2;96;165;250m'
    UI_GREEN=$'\033[38;2;34;197;94m'
    UI_AMBER=$'\033[38;2;251;191;36m'
    UI_RED=$'\033[38;2;248;113;113m'
    UI_MUTED=$'\033[38;2;148;163;184m'
    UI_WHITE=$'\033[38;2;248;250;252m'
  elif (( depth >= 256 )); then
    UI_BLUE=$'\033[38;5;69m'
    UI_LIGHT=$'\033[38;5;75m'
    UI_GREEN=$'\033[38;5;40m'
    UI_AMBER=$'\033[38;5;220m'
    UI_RED=$'\033[38;5;203m'
    UI_MUTED=$'\033[38;5;245m'
    UI_WHITE=$'\033[38;5;255m'
  else
    UI_BLUE=$'\033[34m'; UI_LIGHT=$'\033[94m'; UI_GREEN=$'\033[32m'
    UI_AMBER=$'\033[33m'; UI_RED=$'\033[31m'; UI_MUTED=$'\033[90m'; UI_WHITE=$'\033[37m'
  fi
  UI_BOLD=$'\033[1m'; UI_RESET=$'\033[0m'
}
ui_init

ui_print() { printf '%s\n' "$1" >&4; }
ui_color() {
  local color="$1" value="$2"
  if ((UI_COLOR_ACTIVE)); then printf '%s%s%s' "$color" "$value" "$UI_RESET"; else printf '%s' "$value"; fi
}
ui_line() {
  ui_print "$(ui_color "$UI_BLUE" '────────────────────────────────────────────────────────')"
}
ui_header() {
  UI_TITLE="$1"
  UI_SECTION=''
  UI_STATE=''
  ui_print ''
  ui_print "$(ui_color "$UI_LIGHT" '◆ VPS INSTALLER') $(ui_color "$UI_MUTED" "/ $UI_TITLE")"
  ui_line
}
ui_section() {
  UI_STATE="${1##* }"
  ui_print ''
  ui_print "$(ui_color "$UI_BLUE" "$1")"
}
ui_status_color() {
  local rest="$1" prefix token trailer
  # Color semantics within compound values, e.g. security=enabled, weekly=enabled.
  while [[ -n "$rest" ]]; do
    if [[ "$rest" =~ ^([^[:alpha:]]*)([[:alpha:]][[:alnum:]_-]*)(.*)$ ]]; then
      prefix="${BASH_REMATCH[1]}" token="${BASH_REMATCH[2]}" trailer="${BASH_REMATCH[3]}"
      printf '%s' "$prefix"
      case "${token,,}" in
        yes|enabled|active|running|healthy|success|configured) ui_color "$UI_GREEN" "$token" ;;
        no|disabled|inactive|never|none|unknown) ui_color "$UI_MUTED" "$token" ;;
        failed|error|missing) ui_color "$UI_RED" "$token" ;;
        *) ui_color "$UI_WHITE" "$token" ;;
      esac
      rest="$trailer"
    else
      printf '%s' "$rest"
      break
    fi
  done
}

ui_menu_item() {
  local number="$1" label="$2"
  if [[ "$number" == 0 ]]; then
    ui_line
    ui_print "  $(ui_color "$UI_MUTED" '0')  $(ui_color "$UI_MUTED" "$label")"
  else
    if [[ "$UI_STATE" != ACTIONS ]]; then
      [[ "$UI_STATE" != STATUS ]] || ui_line
      ui_section ACTIONS
    fi
    ui_print "  $(ui_color "$UI_BLUE" "$number")  $(ui_color "$UI_WHITE" "$label")"
  fi
}
ui_say() {
  local line="$*" heading number label field value
  case "$line" in
    '========================================'|'========================================================') return 0 ;;
  esac
  case "$line" in
    'UNIVERSAL VPS INSTALLER'|'  UNIVERSAL VPS INSTALLER') ui_header 'MAIN MENU'; return ;;
    '         INSTALLATION SUMMARY') ui_header 'INSTALLATION SUMMARY'; return ;;
    'AUTOMATIC MAINTENANCE'|'MAINTENANCE STATUS'|'XRAY GEODATA UPDATES'|\
    'AMNEZIAWG GEOBLOCK UPDATES'|'UBUNTU SYSTEM UPDATES'|'AUTOMATIC REBOOT'|\
    'EMAIL NOTIFICATIONS (MSMTP)'|'XRAY MANAGEMENT'|'AMNEZIAWG MANAGEMENT')
      ui_header "$line"; return ;;
    'Xray REALITY configuration'|'AmneziaWG configuration')
      ui_header "${line^^}"; return ;;
  esac
  if [[ "$line" =~ ^[[:space:]]*([0-9]+)\)[[:space:]]+(.+)$ ]]; then
    number="${BASH_REMATCH[1]}" label="${BASH_REMATCH[2]}"
    ui_menu_item "$number" "$label"
    return
  fi
  if [[ "$line" == 'Selected: '* ]]; then
    ui_print "$(ui_color "$UI_MUTED" "  $line")"
    return
  fi
  if [[ -n "$UI_TITLE" && "$line" =~ ^([^:]{2,44}):[[:space:]]*(.*)$ ]]; then
    field="${BASH_REMATCH[1]}" value="${BASH_REMATCH[2]}"
    [[ "$UI_STATE" == STATUS || "$UI_STATE" == ACTIONS ]] || ui_section 'SYSTEM STATUS'
    ui_print "  $(ui_color "$UI_WHITE" "$(printf '%-22s' "$field")") $(ui_status_color "$value")"
    return
  fi
  if [[ "$line" == '' ]]; then
    # Avoid gratuitous blank lines before every section/menu item.
    [[ "$UI_STATE" != ACTIONS ]] && ui_print ''
    return
  fi
  ui_print "  $line"
}

# Override only the formatting shell helpers from common.sh. No data/control changes.
say() { ui_say "$@"; }
phase() { ui_header "${*^^}"; }
ask() {
  local answer
  printf '%s ' "$(ui_color "$UI_AMBER" "$1")" >&4
  IFS= read -r answer <&3 || die "Input terminated"
  REPLY="$answer"
}

# Keep standard result prefixes, with semantic Cobalt Night colors.
ok() { printf '%s %s\n' "$(ui_color "$UI_GREEN" '[OK]')" "$*" >&4; }
warn() {
  local color="$UI_AMBER"
  case "${*,,}" in *critical*|*failed*|*error*) color="$UI_RED" ;; esac
  printf '%s %s\n' "$(ui_color "$color" '[WARN]')" "$*" >&4
}