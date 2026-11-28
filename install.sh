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
# The remote curl bootstrap refreshes modules; the installed local command works offline.
REFRESH_MODULES=0
case "$0" in
  bash|-bash|sh|-sh) REFRESH_MODULES=1 ;;
esac
for file in common.sh system.sh vpn.sh vpn_updates.sh profiles.sh maintenance.sh maintenance_task.sh maintenance_notify.sh maintenance_geodata.py profile_manager.py profile_edit.py install_options.py geoblock_update.py ipv6_support.py; do
  if (( REFRESH_MODULES )) || [[ ! -s "$ROOT/lib/$file" ]]; then
    url="https://raw.githubusercontent.com/$OWNER_REPO/$SOURCE_REF/lib/$file"
    temp="$(mktemp "$ROOT/lib/.download.XXXXXX")" || exit 1
    curl --fail --silent --show-error --location --retry 3 "$url" -o "$temp" || { rm -f "$temp"; exit 1; }
    chmod 600 "$temp"
    mv "$temp" "$ROOT/lib/$file" || exit 1
  fi
done
if (( REFRESH_MODULES )) || [[ ! -s "$ROOT/install.sh" ]]; then
  tmp_installer="$(mktemp "$ROOT/.install.XXXXXX")" || exit 1
  curl --fail --silent --show-error --location --retry 3 \
    "https://raw.githubusercontent.com/$OWNER_REPO/$SOURCE_REF/install.sh" -o "$tmp_installer" || { rm -f "$tmp_installer"; exit 1; }
  chmod 700 "$tmp_installer"
  mv "$tmp_installer" "$ROOT/install.sh" || exit 1
fi
ln -sfn "$ROOT/install.sh" /usr/local/sbin/vps-installer
bash -n "$ROOT"/lib/*.sh || { echo "Invalid Bash module syntax." >&2; exit 1; }
python3 -m py_compile "$ROOT"/lib/*.py || { echo "Invalid Python module syntax." >&2; exit 1; }
source "$ROOT/lib/common.sh"
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
  say "2) Xray"
  say "3) AmneziaWG"
  say "4) Manage clients profiles"
  say "5) Automatic maintenance"
  say "0) Exit"
  ask_choice "Choose option:" "1:VPS defence and customization" "2:Xray" "3:AmneziaWG" "4:Manage clients profiles" "5:Automatic maintenance" "0:Exit" || continue
  case "$REPLY" in
    1) vps_defence_customization || warn "VPS defence stage did not finish." ;;
    2) xray_management_menu ;;
    3) amnezia_installation || warn "AmneziaWG installation did not finish." ;;
    4) profile_menu ;;
    5) automatic_maintenance_menu ;;
    0) exit 0 ;;
    *) warn "Unknown option." ;;
  esac
done
