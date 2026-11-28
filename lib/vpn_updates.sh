#!/usr/bin/env bash
# Manual Xray Core updates; preserve managed config, private keys, services and GeoData.

# Latest release is read-only. Network failures are never interpreted as "up to date".
xray_current_version() {
  local line
  command -v xray >/dev/null 2>&1 || return 1
  line="$(xray version 2>/dev/null)" || return 1
  [[ "$line" =~ ^Xray[[:space:]]+v?([0-9][A-Za-z0-9.+-]*) ]] || return 1
  printf '%s\n' "${BASH_REMATCH[1]}"
}

xray_latest_version() {
  local response version
  response="$(curl --fail --silent --show-error --location \
    --connect-timeout 4 --max-time 12 --retry 0 \
    -H 'Accept: application/vnd.github+json' \
    https://api.github.com/repos/XTLS/Xray-core/releases/latest)" || return 1
  version="$(python3 -c 'import json,sys,re
try:
    tag=json.load(sys.stdin)["tag_name"]
    if not isinstance(tag,str) or not re.fullmatch(r"v?[0-9][A-Za-z0-9.+-]*",tag): raise ValueError()
    print(tag.removeprefix("v"))
except (ValueError, KeyError, TypeError): sys.exit(1)' <<< "$response")" || return 1
  [[ -n "$version" ]] || return 1
  printf '%s\n' "$version"
}

xray_core_status() {
  local installed="${1:-}" latest="${2:-}"
  [[ -n "$installed" ]] || installed="$(xray_current_version)" || installed='Not installed / Unknown'
  [[ -n "$latest" ]] || latest='Unavailable (GitHub check failed)'
  say "Installed version: $installed"
  say "Latest release:    $latest"
  if [[ "$installed" == 'Not installed / Unknown' || "$latest" == Unavailable* ]]; then
    say 'Update status:     Unknown'
  elif dpkg --compare-versions "$installed" lt "$latest"; then
    say 'Update status:     Update available'
  elif dpkg --compare-versions "$installed" eq "$latest"; then
    say 'Update status:     Up to date'
  elif dpkg --compare-versions "$installed" gt "$latest"; then
    say 'Update status:     Installed version is newer'
  else
    say 'Update status:     Unknown'
  fi
}

# Refreshing APT metadata does not install or upgrade packages.
awg_refresh_versions() {
  say 'Refreshing APT package lists (no packages will be installed)...'
  if timeout 90s apt-get -o DPkg::Lock::Timeout=15 -o Acquire::Retries=1 \
      -o Acquire::http::Timeout=12 -o Acquire::https::Timeout=12 update >/dev/null 2>&1; then
    AWG_APT_FRESH=yes
  else
    AWG_APT_FRESH=no
    warn 'Could not refresh APT indexes; available package versions may be outdated.'
  fi
}

awg_package_status() {
  local package installed candidate update=no unknown=no
  for package in amneziawg amneziawg-dkms amneziawg-tools; do
    installed="$(dpkg-query -W -f='${Version}' "$package" 2>/dev/null)" || installed='Not installed'
    [[ -n "$installed" ]] || installed='Not installed'
    candidate="$(apt-cache policy "$package" 2>/dev/null | awk '$1=="Candidate:" {print $2; exit}')"
    [[ -n "$candidate" && "$candidate" != '(none)' ]] || candidate='Unavailable'
    say "$package:"
    say "  Installed: $installed"
    say "  Latest (APT): $candidate"
    if [[ "$candidate" == 'Unavailable' ]]; then
      unknown=yes
    elif [[ "$installed" == 'Not installed' ]] || dpkg --compare-versions "$installed" lt "$candidate"; then
      update=yes
    fi
  done
  if [[ "${AWG_APT_FRESH:-no}" != yes ]]; then
    say 'Version status: Unverified (APT indexes not refreshed)'
  elif [[ "$unknown" == yes ]]; then
    say 'Version status: Unknown (missing candidate for at least one package)'
  elif [[ "$update" == yes ]]; then
    say 'Version status: Update available via weekly APT'
  else
    say 'Version status: Up to date'
  fi
}

amnezia_management_menu() {
  local choice
  awg_refresh_versions
  while :; do
    say ''
    say 'AMNEZIAWG MANAGEMENT'
    awg_package_status
    say 'Package updates: Managed by weekly Ubuntu APT'
    say '1) Install / Configure AmneziaWG'
    say '2) Refresh available versions (APT update only)'
    say '3) Configure IPv6 ingress'
    say '0) Exit'
    ask_choice 'Choose option:' '1:Install / Configure' '2:Refresh versions' '3:IPv6 ingress' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) amnezia_installation || warn 'AmneziaWG installation/configuration failed.' ;;
      2) awg_refresh_versions ;;
      3) vpn_installation_preflight && [[ -s /etc/amnezia/amneziawg/awg0.conf ]] && offer_awg_ipv6 || warn 'Unable to configure IPv6 ingress.' ;;
      0) return ;;
    esac
  done
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
  local choice installed latest
  # Query remote release once on entry; use the refresh option to retry.
  installed="$(xray_current_version)" || installed='Not installed / Unknown'
  latest="$(xray_latest_version)" || latest='Unavailable (GitHub check failed)'
  while :; do
    say ''
    say 'XRAY MANAGEMENT'
    xray_core_status "$installed" "$latest"
    say '1) Install / Configure Xray'
    say '2) Update Xray Core'
    say '3) Refresh available version'
    say '0) Exit'
    ask_choice 'Choose option:' '1:Install / Configure' '2:Update Core' '3:Refresh version' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) xray_installation || warn 'Xray installation/configuration failed.' ;;
      2) if [[ "$latest" == Unavailable* ]]; then
           warn 'Latest release could not be verified. No update started.'
         elif [[ "$installed" != 'Not installed / Unknown' ]] && ! dpkg --compare-versions "$installed" lt "$latest"; then
           ok 'Xray Core is already up to date; no service restart.'
         else
           xray_core_update || warn 'Xray Core update failed.'
         fi ;;
      3) latest="$(xray_latest_version)" || latest='Unavailable (GitHub check failed)' ;;
      0) return ;;
    esac
    installed="$(xray_current_version)" || installed='Not installed / Unknown'
  done
}
