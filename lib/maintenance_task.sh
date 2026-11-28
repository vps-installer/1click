#!/usr/bin/env bash
# Runtime jobs for Ubuntu APT and existing AmneziaWG GeoBlock.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C.UTF-8
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 077
NOTIFY=/usr/local/sbin/vps-maintenance-notify
CONFIG=/etc/vps-installer/maintenance.env
[[ $EUID -eq 0 ]] || { echo 'Root required' >&2; exit 1; }
[[ -f "$CONFIG" ]] && source "$CONFIG"
notify() { [[ -x "$NOTIFY" ]] && "$NOTIFY" "$1" "$2" "$3" || :; }
# The weekly APT job installs AmneziaWG packages through the official PPA.
# Runtime checks never modify DKMS, packages, client profiles or firewall rules.
check_awg_health() {
  [[ -s /etc/amnezia/amneziawg/awg0.conf ]] || return 0
  local failures=()
  modinfo -k "$(uname -r)" amneziawg >/dev/null 2>&1 || failures+=(dkms-current-kernel)
  for unit in awg-quick@awg0.service awg-geoblock.service awg-dns.service; do
    systemctl is-active --quiet "$unit" || failures+=("$unit")
  done
  if command -v awg >/dev/null 2>&1; then
    awg show awg0 >/dev/null 2>&1 || failures+=(awg0)
  else
    failures+=(awg-tools)
  fi
  if [[ -x /usr/local/sbin/awg-geoblock-check.sh ]]; then
    /usr/local/sbin/awg-geoblock-check.sh >/dev/null 2>&1 || failures+=(geoblock-policy)
  else
    failures+=(geoblock-checker)
  fi
  if (( ${#failures[@]} )); then
    printf 'AmneziaWG health check failed: %s\n' "${failures[*]}" >&2
    return 1
  fi
}

# Determine the kernel selected by default, not just the highest /boot filename.
# If we cannot determine the next kernel safely, refuse automated reboot.
check_awg_reboot_ready() {
  [[ -s /etc/amnezia/amneziawg/awg0.conf ]] || return 0
  local next_kernel
  next_kernel="$(find /boot -maxdepth 1 -name 'vmlinuz-*' -printf '%f\n' | sed 's/^vmlinuz-//' | sort -V | tail -n 1)"
  [[ -n "$next_kernel" ]] || { echo 'Cannot identify next kernel.' >&2; return 1; }
  modinfo -k "$next_kernel" amneziawg >/dev/null 2>&1 || {
    echo "Missing AmneziaWG module for kernel $next_kernel." >&2
    return 1
  }
  # With custom boot loaders the highest-version kernel may not be the default.
  # This is intentionally conservative: do not automatically reboot if uncertain.
  if command -v grub-editenv >/dev/null 2>&1; then
    local saved
    saved="$(grub-editenv list 2>/dev/null | sed -n 's/^saved_entry=//p')"
    if [[ -n "$saved" ]]; then
      echo 'GRUB saved_entry overrides default; manual reboot verification required.' >&2
      return 1
    fi
  fi
}

mode="${1:-}"
case "$mode" in
  apt)
    exec 9>/run/lock/vps-maintenance-apt.lock
    flock -n 9 || { echo 'Maintenance APT already running' >&2; exit 1; }
    current="$(mktemp /var/log/vps-maintenance-apt.XXXXXXXX)"
    trap 'rm -f -- "$current"' EXIT
    # Keep historical execution output in journal; bound this email report to 18 KB.
    echo "Starting APT: $(date -Is)" | tee "$current"
    result=0
    apt-get --error-on=any -o DPkg::Lock::Timeout=180 -o Acquire::Retries=2 update 2>&1 | tee -a "$current" || result=1
    if (( result == 0 )); then
      apt-get -y --with-new-pkgs \
        -o DPkg::Lock::Timeout=180 -o Acquire::Retries=2 \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        upgrade 2>&1 | tee -a "$current" || result=1
    fi
    if (( result != 0 )); then
      notify apt failed "APT upgrade failed on $(hostname). Check journalctl -u vps-maintenance-apt.service"
      exit 1
    fi
    echo "APT completed: $(date -Is)"
    # Post-upgrade module and fail-closed checks. Failure must not trigger reboot.
    if ! check_awg_health; then
      notify apt failed "APT succeeded but AmneziaWG health check failed on $(hostname); automatic reboot blocked."
      exit 1
    fi
    echo 'AWG_HEALTH: OK'
    if [[ -f /run/reboot-required ]]; then
      notify apt reboot-required "APT upgrade completed. Reboot required on $(hostname)."
      if [[ ${MAINT_AUTO_REBOOT:-no} == yes ]]; then
        if ! check_awg_reboot_ready; then
          notify apt failed "Automatic reboot blocked: cannot verify AmneziaWG module for next kernel."
          exit 1
        fi
        install -d -m 700 /var/lib/vps-maintenance
        date -Is > /var/lib/vps-maintenance/reboot-pending
        systemctl reboot
      fi
    else
      notify apt updated "APT upgrade completed successfully on $(hostname)."
    fi
    ;;
  awg)
    updater=/usr/local/sbin/awg-geoblock-update.py
    [[ -x "$updater" ]] || { echo "Missing updater: $updater" >&2; exit 1; }
    output="$(mktemp)"
    trap 'rm -f -- "$output"' EXIT
    if "$updater" >"$output" 2>&1; then
      cat "$output"
      if grep -q '^UPDATED:' "$output"; then
        notify awg updated "AmneziaWG GeoBlock updated on $(hostname)."
      fi
    else
      cat "$output" >&2
      notify awg failed "AmneziaWG GeoBlock update failed on $(hostname)."
      exit 1
    fi
    ;;
  boot-check)
    flag=/var/lib/vps-maintenance/reboot-pending
    [[ -f "$flag" ]] || exit 0
    issue=()
    for unit in ssh.service fail2ban.service; do
      systemctl is-active --quiet "$unit" || issue+=("$unit")
    done
    if [[ -f /usr/local/etc/xray/config.json ]]; then
      systemctl is-active --quiet xray.service || issue+=(xray.service)
    fi
    if [[ -f /etc/amnezia/amneziawg/awg0.conf ]]; then
      for unit in awg-quick@awg0.service awg-geoblock.service awg-dns.service; do
        systemctl is-active --quiet "$unit" || issue+=("$unit")
      done
      check_awg_health || issue+=(awg-health)
    fi
    if (( ${#issue[@]} == 0 )); then
      notify boot healthy "VPS reboot health check passed on $(hostname)."
      rm -f "$flag"
    else
      notify boot failed "VPS post-reboot problems on $(hostname): ${issue[*]}"
      echo "Post-boot failures: ${issue[*]}" >&2
      exit 1
    fi
    ;;
  *) echo 'Usage: maintenance_task.sh {apt|awg|boot-check}' >&2; exit 2 ;;
esac
