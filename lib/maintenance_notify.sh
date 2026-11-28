#!/usr/bin/env bash
# Send configured notifications through root-owned msmtp; never log credentials.
set -euo pipefail
CONFIG=/etc/vps-installer/maintenance.env
[[ -r "$CONFIG" ]] || exit 0
# This file is created by the installer, root-only, using validated values.
source "$CONFIG"
[[ ${MAINT_NOTIFY:-no} == yes && -n ${MAINT_RECIPIENT:-} && -n ${MAINT_SENDER:-} ]] || exit 0
if [[ ${MAINT_SOURCE:-} == external ]]; then
  PROBE=/usr/local/sbin/vps-maintenance-msmtp-probe
  [[ -x "$PROBE" ]] || { echo 'Missing SMTP metadata probe' >&2; exit 1; }
  metadata="$(python3 "$PROBE" 2>/dev/null)" || { echo 'External SMTP configuration is no longer readable' >&2; exit 1; }
  sender="$(printf '%s\n' "$metadata" | sed -n 's/^sender=//p')"
  [[ "$sender" == "$MAINT_SENDER" ]] || { echo 'External SMTP sender changed; import it again' >&2; exit 1; }
fi
[[ -x /usr/sbin/sendmail ]] || { echo 'msmtp/sendmail unavailable' >&2; exit 1; }
kind="${1:-event}" result="${2:-unknown}" message="${3:-}"
if [[ $result == unchanged ]]; then exit 0; fi
subject="[$(hostname)] ${kind} ${result}"
{
  printf 'From: %s\nTo: %s\nSubject: %s\n' "$MAINT_SENDER" "$MAINT_RECIPIENT" "$subject"
  printf 'MIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n\n'
  printf '%s\n\n' "$message"
  printf 'Date: %s\n' "$(date -Is)"
} | /usr/sbin/sendmail -oi -f "$MAINT_SENDER" "$MAINT_RECIPIENT"
