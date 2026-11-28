#!/usr/bin/env bash
# Manual Xray Core updates; preserve managed config, private keys, services and GeoData.

xray_core_status() {
  if ! command -v xray >/dev/null 2>&1; then
    warn 'Xray binary not found.'
    return 1
  fi
  xray version | head -n 1
}

xray_core_update() {
  local config=/usr/local/etc/xray/config.json
  local binary=/usr/local/bin/xray
  local script_url=https://raw.githubusercontent.com/XTLS/Xray-install/main/install-release.sh
  local temp backup oldsum changed=0
  [[ $EUID -eq 0 ]] || { warn 'Root required.'; return 1; }
  [[ -f "$config" && -x "$binary" ]] || { warn 'Managed Xray not found.'; return 1; }
  if ! xray_config_is_managed; then
    warn 'Unrecognized Xray configuration. Refusing update.'
    return 1
  fi
  if ! systemctl is-active --quiet xray.service; then
    warn 'Xray is not running. Resolve existing fault first.'
    return 1
  fi
  if ! runuser -u xray -- "$binary" run -test -config "$config"; then
    warn 'Current Xray configuration is invalid. No update performed.'
    return 1
  fi
  if ! grep -q '"geoip:ru"' "$config" || ! grep -q '"geosite:category-ru"' "$config"; then
    warn 'Required RU policy not found. Refusing update.'
    return 1
  fi
  if ! yesno 'Update Xray Core using official installer? Brief VPN interruption possible.'; then
    return 0
  fi
  exec {xray_update_fd}>/run/lock/vps-xray-core-update.lock || return 1
  if ! flock -n "$xray_update_fd"; then
    warn 'Another Xray update is running.'
    exec {xray_update_fd}>&-
    return 1
  fi
  temp="$(mktemp -d /root/.xray-core-update.XXXXXXXX)" || return 1
  chmod 700 "$temp"
  # Stable backup of binary and config. Do not copy secret config to public logs.
  if ! cp -p -- "$binary" "$temp/xray.previous" || ! cp -p -- "$config" "$temp/config.previous"; then
    warn 'Cannot create Xray backup.'
    rm -rf -- "$temp"
    return 1
  fi
  oldsum="$(sha256sum "$binary" | awk '{print $1}')" || { rm -rf -- "$temp"; return 1; }
  if ! curl --fail --silent --show-error --location --retry 3 --connect-timeout 15 --max-time 120 \
       "$script_url" -o "$temp/install-release.sh" || ! bash -n "$temp/install-release.sh"; then
    warn 'Official installer download or syntax check failed.'
    rm -rf -- "$temp"
    return 1
  fi
  local rc=0
  # Official installer will not replace geodata, service units or reboot Xray.
  bash "$temp/install-release.sh" install --without-geodata --no-update-service --manually-restart-later || rc=$?
  if ((rc == 0)) && [[ -x "$binary" ]] && \
     runuser -u xray -- "$binary" run -test -config "$config" && \
     cmp -s "$config" "$temp/config.previous"; then
    if [[ "$(sha256sum "$binary" | awk '{print $1}')" != "$oldsum" ]]; then
      changed=1
      if ! systemctl restart xray.service || ! systemctl is-active --quiet xray.service; then
        rc=1
      fi
    fi
  else
    rc=1
  fi
  if (( rc != 0 )); then
    warn 'Xray upgrade failed. Restoring previous executable and configuration.'
    cp -p -- "$temp/xray.previous" "$binary" || { warn 'CRITICAL: Failed to restore binary.'; return 1; }
    cp -p -- "$temp/config.previous" "$config" || { warn 'CRITICAL: Failed to restore config.'; return 1; }
    systemctl restart xray.service || warn 'CRITICAL: Previous Xray failed to restart.'
    rm -rf -- "$temp"
    return 1
  fi
  if ((changed)); then
    local history=/var/backups/vps-installer/xray
    if install -d -m 700 "$history"; then
      cp -p -- "$temp/xray.previous" "$history/xray.previous" || warn 'Could not retain previous Xray binary for later manual rollback.'
      cp -p -- "$temp/config.previous" "$history/config.previous" || warn 'Could not retain previous Xray config backup.'
      chmod 700 "$history" 2>/dev/null || :
      chmod 600 "$history/config.previous" 2>/dev/null || :
    else
      warn 'Could not retain successful-update rollback backup.'
    fi
  fi
  rm -rf -- "$temp"
  if ((changed)); then
    ok 'Xray Core updated; existing config, secrets and GeoData preserved.'
  else
    ok 'Xray Core already up to date; no service restart.'
  fi
  return 0
}

xray_management_menu() {
  local choice
  while :; do
    say ''
    say 'XRAY MANAGEMENT'
    xray_core_status || :
    say '1) Install / Configure Xray'
    say '2) Update Xray Core'
    say '0) Exit'
    ask_choice 'Choose option:' '1:Install / Configure' '2:Update Core' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) xray_installation || warn 'Xray installation/configuration failed.' ;;
      2) xray_core_update || warn 'Xray Core update failed.' ;;
      0) return ;;
    esac
  done
}
