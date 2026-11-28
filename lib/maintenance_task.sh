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
    if [[ -f /run/reboot-required ]]; then
      notify apt reboot-required "APT upgrade completed. Reboot required on $(hostname)."
      if [[ ${MAINT_AUTO_REBOOT:-no} == yes ]]; then
        # Do not reboot if a future AWG kernel module is not present.
        if [[ -f /etc/amnezia/amneziawg/awg0.conf ]]; then
          next_kernel="$(find /boot -maxdepth 1 -name 'vmlinuz-*' -printf '%f\n' | sed 's/^vmlinuz-//' | sort -V | tail -n 1)"
          if [[ -z "$next_kernel" ]] || ! modinfo -k "$next_kernel" amneziawg >/dev/null 2>&1; then
            notify apt failed "Automatic reboot blocked: AmneziaWG unavailable for kernel ${next_kernel:-unknown}."
            echo 'Automatic reboot blocked: missing future AmneziaWG kernel module.' >&2
            exit 1
          fi
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
      /usr/local/sbin/awg-geoblock-check.sh >/dev/null 2>&1 || issue+=(geoblock-policy)
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
