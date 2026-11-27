#!/usr/bin/env bash
# Shared terminal helpers for the VPS installer.
set -o pipefail
umask 077
export LC_ALL=C.UTF-8
STATE_DIR=/etc/vps-installer
LOG_DIR=/var/log/vps-installer
RUN_DIR=/run/vps-installer
mkdir -p "$STATE_DIR" "$LOG_DIR" "$RUN_DIR"
chmod 700 "$STATE_DIR" "$LOG_DIR" "$RUN_DIR"
LOG_FILE="$LOG_DIR/install.log"
touch "$LOG_FILE"; chmod 600 "$LOG_FILE"
TTY=/dev/tty
if [[ ! -r "$TTY" || ! -w "$TTY" ]]; then
  echo "An interactive terminal is required." >&2; exit 1
fi
exec 3<"$TTY" 4>"$TTY"
C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
say() { printf '%s\n' "$*" >&4; }
ok() { printf '%s[OK]%s %s\n' "$C_GREEN" "$C_RESET" "$*" >&4; }
warn() { printf '%s[WARN]%s %s\n' "$C_RED" "$C_RESET" "$*" >&4; }
die() {
  local message="$*"
  warn "$message"
  printf '\n========================================\n' >&4
  printf '  INSTALLATION INCOMPLETE\n' >&4
  printf '========================================\n' >&4
  printf 'Last error: %s\n' "$message" >&4
  printf 'Diagnostic log: %s\n' "$LOG_FILE" >&4
  printf 'Your existing configuration has not been deliberately reset.\n' >&4
  printf 'After reviewing the error, rerun the installer to resume.\n' >&4
  exit 1
}
ask() { local answer; printf '%s ' "$1" >&4; IFS= read -r answer <&3 || die "Input terminated"; REPLY="$answer"; }
ask_default() { local label="$1" default="$2"; ask "$label [$default]:"; REPLY="${REPLY:-$default}"; }
ask_choice() {
  local prompt="$1" selected description pair
  shift
  say ""
  ask "$prompt"
  selected="$REPLY"
  for pair in "$@"; do
    if [[ "$pair" == "$selected:"* ]]; then
      description="${pair#*:}"
      say "Selected: $selected ($description)"
      say ""
      return 0
    fi
  done
  warn "Invalid choice: $selected"
  say ""
  return 1
}
ask_default_choice() {
  local prompt="$1" default="$2" selected description pair
  shift 2
  say ""
  ask_default "$prompt" "$default"
  selected="$REPLY"
  for pair in "$@"; do
    if [[ "$pair" == "$selected:"* ]]; then
      description="${pair#*:}"
      say "Selected: $selected ($description)"
      say ""
      return 0
    fi
  done
  warn "Invalid choice: $selected"
  say ""
  return 1
}
yesno() { local answer; while :; do ask "$1 (y/n):"; answer="${REPLY,,}"; case "$answer" in y|yes) return 0;; n|no) return 1;; *) warn "Enter y or n.";; esac; done; }
pause() { ask "Press Enter to continue:"; }
spinner_wait() {
  local pid="$1" label="$2" frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏) n=0
  # Show at least one visible Braille frame, even for very fast commands.
  printf '\r\033[K%s %s' "${frames[0]}" "$label" >&4
  while kill -0 "$pid" 2>/dev/null; do
    printf '\r\033[K%s %s' "${frames[n++ % ${#frames[@]}]}" "$label" >&4
    sleep 0.12
  done
  printf '\r\033[K' >&4
}
run_secret_step() {
  local title="$1"; shift
  local output error pid rc
  output="$(mktemp "$RUN_DIR/.secret-output.XXXXXX")" || die "Cannot create temporary profile output."
  error="$(mktemp "$RUN_DIR/.secret-error.XXXXXX")" || { rm -f "$output"; die "Cannot create temporary error output."; }
  chmod 600 "$output" "$error"
  ("$@" >"$output" 2>"$error") &
  pid=$!
  spinner_wait "$pid" "$title"
  wait "$pid"; rc=$?
  if ((rc != 0)); then
    warn "$title failed (exit $rc)"
    cat "$error" >&4
    rm -f "$output" "$error"
    return "$rc"
  fi
  cat "$output" >&4
  rm -f "$output" "$error"
  return 0
}
phase() { printf '\n--- %s ---\n' "$*" >&4; }
run_step() {
  local title="$1"; shift
  local step_log pid rc
  step_log="$(mktemp "$RUN_DIR/.step.XXXXXX")" || die "Cannot create command log."
  chmod 600 "$step_log"
  say "Starting: $title"
  ("$@" >"$step_log" 2>&1) &
  pid=$!
  spinner_wait "$pid" "$title"
  wait "$pid"; rc=$?
  if ((rc != 0)); then
    warn "$title failed (exit $rc)"
    # Show only this step's output, never unrelated lines from earlier commands.
    tail -n 18 "$step_log" >&4
    printf '\n[%s] %s (exit %s)\n' "$(date -Is)" "$title" "$rc" >>"$LOG_FILE"
    cat "$step_log" >>"$LOG_FILE"
    rm -f "$step_log"
    return "$rc"
  fi
  printf '\n[%s] %s (success)\n' "$(date -Is)" "$title" >>"$LOG_FILE"
  cat "$step_log" >>"$LOG_FILE"
  rm -f "$step_log"
  ok "$title"
}
# Retry only APT/dpkg lock contention, never mask real package errors.
# Use a single 5-minute deadline for the entire operation (including dpkg waits).
apt_retry() {
  local started=$SECONDS remaining rc output
  output="$(mktemp "$RUN_DIR/.apt-output.XXXXXX")" || return 1
  chmod 600 "$output"
  while :; do
    remaining=$((300 - (SECONDS - started)))
    if (( remaining <= 0 )); then
      printf 'APT remained locked for 300 seconds. Another package manager is running.\n' >&2
      cat "$output"
      rm -f "$output"
      return 100
    fi
    # Short dpkg-internal wait, plus retries for apt-list and archive locks.
    # This also handles a lock released between two commands.
    if env DEBIAN_FRONTEND=noninteractive "$@" >"$output" 2>&1; then
      cat "$output"
      rm -f "$output"
      return 0
    else
      rc=$?
    fi
    if ! grep -Eiq 'Could not get lock|Unable to acquire (the )?(dpkg|.*lock)|is another process using it|Could not open lock file|Unable to lock directory' "$output"; then
      cat "$output"
      rm -f "$output"
      return "$rc"
    fi
    remaining=$((300 - (SECONDS - started)))
    if (( remaining <= 0 )); then
      cat "$output"
      printf 'APT lock wait exceeded 300 seconds; retry after the other process finishes.\n' >&2
      rm -f "$output"
      return "$rc"
    fi
    printf 'APT is locked by another process; retrying (up to %ss remaining).\n' "$remaining"
    sleep 5
  done
}
state_set() {
  local key="$1" value="$2"
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "Invalid state key: $key"
  python3 - "$STATE_DIR/settings.json" "$key" "$value" <<'PY'
import json,os,sys,tempfile
p,k,v=sys.argv[1:]
try: d=json.load(open(p))
except FileNotFoundError: d={}
d[k]=v
fd,t=tempfile.mkstemp(dir=os.path.dirname(p),prefix=".state-")
with os.fdopen(fd,"w") as f: json.dump(d,f,indent=2); f.write("\n")
os.chmod(t,0o600); os.replace(t,p)
PY
}
state_get() {
  python3 - "$STATE_DIR/settings.json" "$1" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2],""))
except FileNotFoundError: print("")
PY
}
require_root() { [[ "$EUID" == 0 ]] || die "Run as root (sudo -i)."; }
require_ubuntu() {
  . /etc/os-release
  [[ "$ID" == ubuntu && "$VERSION_ID" == "24.04" ]] || die "Ubuntu 24.04 is required."
}
wan_if() { ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++)if($i=="dev"){print $(i+1);exit}}'; }
public_ip() {
  local ip; ip="$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}')"
  python3 - "$ip" <<'PY'
import ipaddress,sys
try:
 a=ipaddress.ip_address(sys.argv[1])
 print(a if a.is_global else "")
except ValueError: print("")
PY
}
check_status() { systemctl is-active --quiet "$1" && printf ACTIVE || printf INACTIVE; }
