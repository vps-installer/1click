#!/usr/bin/env bash
# Interactive Ubuntu 24.04 VPS installer and VPN profile manager.
set -o pipefail
ROOT=/opt/vps-installer
OWNER_REPO="vps-installer/1click"
SOURCE_REF="main"

# Always leave the local launcher information as the final terminal output,
# including on intentional exit, failed setup, or bootstrap download errors.
installer_exit_footer() {
  local rc="$1"
  if (( rc == 0 )) && [[ "${SUPPRESS_LOCAL_FOOTER_ON_FRESH_INSTALL:-0}" == 1 ]]; then
    return 0
  fi
  local destination=2
  if [[ -e /proc/self/fd/4 && -w /proc/self/fd/4 ]]; then
    destination=4
  elif [[ -w /dev/tty ]]; then
    exec 5>/dev/tty 2>/dev/null && destination=5
  fi
  printf '\r\033[K\n' >&"$destination"
  printf '========================================================\n' >&"$destination"
  printf '  LOCAL VPS INSTALLER\n' >&"$destination"
  printf '========================================================\n' >&"$destination"
  if ((rc != 0)); then
    printf 'The last operation did not finish successfully.\n' >&"$destination"
  fi
  printf 'For future use, start the installed manager locally:\n' >&"$destination"
  printf '  Command: sudo vps-installer\n' >&"$destination"
  printf '  Path:    /usr/local/sbin/vps-installer\n' >&"$destination"
  printf 'No GitHub download is needed for normal local use.\n' >&"$destination"
  printf '========================================================\n' >&"$destination"
  return 0
}
trap 'installer_exit_footer "$?"' EXIT
if [[ "$EUID" != 0 ]]; then
  echo "Run as root (sudo -i)." >&2
  exit 1
fi
mkdir -p "$ROOT/lib"
chmod 700 "$ROOT"
# A curl/bash bootstrap must run the freshly downloaded local script, not
# continue executing the potentially cached bootstrap body in memory.
# A local sudo vps-installer invocation remains offline if all modules exist.
REFRESH_MODULES=0
case "$0" in
  bash|-bash|sh|-sh) REFRESH_MODULES=1 ;;
esac
INSTALLER_MODULES=(ui.sh common.sh system.sh vpn.sh vpn_updates.sh profiles.sh maintenance.sh maintenance_task.sh maintenance_notify.sh maintenance_geodata.py msmtp_probe.py profile_manager.py profile_edit.py install_options.py geoblock_update.py ipv6_support.py)
if (( REFRESH_MODULES )); then
  stage="$(mktemp -d "$ROOT/.refresh.XXXXXXXX")" || exit 1
  mkdir -p "$stage/lib" || { rm -rf -- "$stage"; exit 1; }
  # Bust the raw.githubusercontent.com edge cache on every refresh.
  refresh_id="$(date +%s)-$$"
  refresh_failed=0
  for file in "${INSTALLER_MODULES[@]}"; do
    url="https://raw.githubusercontent.com/$OWNER_REPO/$SOURCE_REF/lib/$file?refresh=$refresh_id"
    curl --fail --silent --show-error --location --retry 3 "$url" -o "$stage/lib/$file" || { refresh_failed=1; break; }
    [[ -s "$stage/lib/$file" ]] || { refresh_failed=1; break; }
  done
  if (( ! refresh_failed )); then
    url="https://raw.githubusercontent.com/$OWNER_REPO/$SOURCE_REF/install.sh?refresh=$refresh_id"
    curl --fail --silent --show-error --location --retry 3 "$url" -o "$stage/install.sh" || refresh_failed=1
    [[ -s "$stage/install.sh" ]] || refresh_failed=1
  fi
  if (( ! refresh_failed )); then
    for file in "$stage/install.sh" "$stage"/lib/*.sh; do
      bash -n "$file" || { refresh_failed=1; break; }
    done
    if (( ! refresh_failed )); then
      python3 -m py_compile "$stage"/lib/*.py || refresh_failed=1
    fi
  fi
  if (( refresh_failed )); then
    echo "Installer refresh failed validation/download; installed files unchanged." >&2
    rm -rf -- "$stage"
    exit 1
  fi

  # Snapshot the previous complete set before touching any installed module.
  previous="$(mktemp -d "$ROOT/.previous.XXXXXXXX")" || { rm -rf -- "$stage"; exit 1; }
  mkdir -p "$previous/lib" || { rm -rf -- "$stage" "$previous"; exit 1; }
  for file in "${INSTALLER_MODULES[@]}"; do
    if [[ -e "$ROOT/lib/$file" ]] && ! cp -p -- "$ROOT/lib/$file" "$previous/lib/$file"; then
      refresh_failed=1
      break
    fi
  done
  if (( ! refresh_failed )) && [[ -e "$ROOT/install.sh" ]]; then
    cp -p -- "$ROOT/install.sh" "$previous/install.sh" || refresh_failed=1
  fi
  if (( refresh_failed )); then
    echo "Cannot back up the existing installer; installed files unchanged." >&2
    rm -rf -- "$stage" "$previous"
    exit 1
  fi

  for file in "${INSTALLER_MODULES[@]}"; do
    chmod 600 "$stage/lib/$file" &&
      mv -f -- "$stage/lib/$file" "$ROOT/lib/$file" || { refresh_failed=1; break; }
  done
  if (( ! refresh_failed )); then
    chmod 700 "$stage/install.sh" &&
      mv -f -- "$stage/install.sh" "$ROOT/install.sh" || refresh_failed=1
  fi
  if (( refresh_failed )); then
    # Roll back all affected files (including newly introduced modules).
    for file in "${INSTALLER_MODULES[@]}"; do
      if [[ -e "$previous/lib/$file" ]]; then
        cp -p -- "$previous/lib/$file" "$ROOT/lib/$file" || echo "CRITICAL: Failed to restore $file" >&2
      else
        rm -f -- "$ROOT/lib/$file"
      fi
    done
    if [[ -e "$previous/install.sh" ]]; then
      cp -p -- "$previous/install.sh" "$ROOT/install.sh" || echo "CRITICAL: Failed to restore install.sh" >&2
    else
      rm -f -- "$ROOT/install.sh"
    fi
    rm -rf -- "$stage" "$previous"
    echo "Installer refresh could not be completed; rollback attempted." >&2
    exit 1
  fi
  rm -rf -- "$stage" "$previous"
  ln -sfn "$ROOT/install.sh" /usr/local/sbin/vps-installer || exit 1
  # The child has a real filename as $0, so it will not download again.
  exec bash "$ROOT/install.sh"
  exit 1
fi

# Normal local launches do not perform a network refresh.
for file in "${INSTALLER_MODULES[@]}"; do
  if [[ ! -s "$ROOT/lib/$file" ]]; then
    url="https://raw.githubusercontent.com/$OWNER_REPO/$SOURCE_REF/lib/$file"
    temp="$(mktemp "$ROOT/lib/.download.XXXXXX")" || exit 1
    if ! curl --fail --silent --show-error --location --retry 3 "$url" -o "$temp" ||
       [[ ! -s "$temp" ]] || ! mv -f -- "$temp" "$ROOT/lib/$file"; then
      rm -f -- "$temp"
      exit 1
    fi
    chmod 600 "$ROOT/lib/$file"
  fi
done
if [[ ! -s "$ROOT/install.sh" ]]; then
  echo "Local installer file is missing: $ROOT/install.sh" >&2
  exit 1
fi
ln -sfn "$ROOT/install.sh" /usr/local/sbin/vps-installer
bash -n "$ROOT/install.sh" || { echo "Invalid local installer syntax." >&2; exit 1; }
for file in "$ROOT"/lib/*.sh; do
  bash -n "$file" || { echo "Invalid Bash module syntax." >&2; exit 1; }
done
python3 -m py_compile "$ROOT"/lib/*.py || { echo "Invalid Python module syntax." >&2; exit 1; }
source "$ROOT/lib/common.sh"
source "$ROOT/lib/ui.sh"
source "$ROOT/lib/system.sh"
source "$ROOT/lib/vpn.sh"
source "$ROOT/lib/vpn_updates.sh"
source "$ROOT/lib/profiles.sh"
source "$ROOT/lib/maintenance.sh"
require_root
exec 9>"$RUN_DIR/installer.lock"
flock -n 9 || die "Another installer instance is already running."
while :; do
  say ""
  say "========================================"
  say "  UNIVERSAL VPS INSTALLER"
  say "========================================"
  say "1) VPS defence and customization"
  say "2) Xray (Install/Update)"
  say "3) AmneziaWG (Install/Update)"
  say "4) Manage clients profiles"
  say "5) Automatic maintenance"
  say "0) Exit"
  ask_choice "Choose option:" "1:VPS defence and customization" "2:Xray (Install/Update)" "3:AmneziaWG (Install/Update)" "4:Manage clients profiles" "5:Automatic maintenance" "0:Exit" || continue
  case "$REPLY" in
    1) vps_defence_customization || warn "VPS defence stage did not finish." ;;
    2) xray_management_menu ;;
    3) amnezia_management_menu ;;
    4) profile_menu ;;
    5) automatic_maintenance_menu ;;
    0) exit 0 ;;
    *) warn "Unknown option." ;;
  esac
done
