#!/usr/bin/env bash
# Interactive VLESS and AmneziaWG profile manager.

list_profiles() {
  local kind="$1"
  if [[ "$kind" == xray ]]; then
    python3 - <<'PY'
import json
from pathlib import Path
p=Path("/usr/local/etc/xray/config.json")
try:
    data=json.loads(p.read_text())
    names=sorted({c["email"] for i in data.get("inbounds",[])
                  if i.get("protocol")=="vless"
                  for c in i.get("settings",{}).get("clients",[])
                  if c.get("email")})
    print("\n".join(names))
except (OSError,ValueError,KeyError,TypeError) as e:
    raise SystemExit(f"Cannot list Xray profiles: {e}")
PY
  elif [[ -d /root/awg-clients ]]; then
    find /root/awg-clients -maxdepth 1 -type f -name '*.conf' -printf '%f\n' |
      sed 's/\.conf$//' | sort
  fi
}

profile_format() {
  local type="$1" action="$2"
  if [[ "$action" == get ]]; then
    say "What would you like to view?"
    say "1) Connection settings (readable)"
    local connection_label
    if [[ "$type" == xray ]]; then connection_label="VLESS connection link"; else connection_label="Full AmneziaWG configuration"; fi
    say "2) $connection_label"
    say "3) QR code"
    if [[ "$type" == xray ]]; then
      say "4) YAML (Clash/Mihomo proxy)"
      say "0) Exit"
      ask_default_choice "Choose output" 1 "1:Connection settings (readable)" "2:$connection_label" "3:QR code" "4:YAML (Clash/Mihomo proxy)" "0:Exit" || return 1
      case "$REPLY" in
        0) return 1;; 1) FORMAT=details;; 2) FORMAT=text;; 3) FORMAT=qr;; 4) FORMAT=yaml;;
        *) warn "Unknown format."; return 1;;
      esac
    else
      say "0) Exit"
      ask_default_choice "Choose output" 1 "1:Connection settings (readable)" "2:$connection_label" "3:QR code" "0:Exit" || return 1
      case "$REPLY" in
        0) return 1;; 1) FORMAT=details;; 2) FORMAT=text;; 3) FORMAT=qr;;
        *) warn "Unknown format."; return 1;;
      esac
    fi
  else
    say "Output format:"
    local output_label
    if [[ "$type" == xray ]]; then
      output_label="VLESS link to copy"
      say "1) $output_label"
      say "2) QR code"
      say "3) YAML (Clash/Mihomo proxy)"
      say "0) Exit"
      ask_default_choice "Choose output format" 1 "1:$output_label" "2:QR code" "3:YAML (Clash/Mihomo proxy)" "0:Exit" || return 1
      case "$REPLY" in
        0) return 1;; 1) FORMAT=text;; 2) FORMAT=qr;; 3) FORMAT=yaml;;
        *) warn "Unknown format."; return 1;;
      esac
    else
      output_label="Configuration text"
      say "1) $output_label"
      say "2) QR code"
      say "0) Exit"
      ask_default_choice "Choose output format" 1 "1:$output_label" "2:QR code" "0:Exit" || return 1
      case "$REPLY" in
        0) return 1;; 1) FORMAT=text;; 2) FORMAT=qr;;
        *) warn "Unknown format."; return 1;;
      esac
    fi
  fi
}

profile_family() {
  local type="$1" choice
  say "Connection address:"
  say "1) IPv4"
  say "2) IPv6"
  say "0) Exit"
  ask_default_choice "Choose address family" 1 "1:IPv4" "2:IPv6" "0:Exit" || return 1
  choice="$REPLY"
  case "$choice" in
    0) return 1;;
    1) FAMILY=ipv4; return 0;;
    2) FAMILY=ipv6;;
    *) return 1;;
  esac
  local flag
  if [[ "$type" == xray ]]; then flag=XRAY_IPV6; else flag=AWG_IPV6; fi
  if [[ "$(state_get "$flag")" != yes ]] ||
     ! python3 "$ROOT/lib/ipv6_support.py" check-ipv6 >/dev/null 2>&1; then
    warn "IPv6 ingress for $type is not enabled or the public IPv6 address is no longer assigned."
    say "Select the corresponding installation option to enable IPv6 first."
    return 1
  fi
  if [[ "$type" == xray ]]; then
    if ! PYTHONPATH="$ROOT/lib" python3 - <<'PY'
import json
from pathlib import Path
from profile_manager import xray_inbounds
state=json.loads(Path("/etc/vps-installer/settings.json").read_text())
conf=json.loads(Path("/usr/local/etc/xray/config.json").read_text())
pair=xray_inbounds(conf)
raise SystemExit(0 if len(pair)==2 and pair[1]["listen"]==state.get("PUBLIC_IPV6") else 1)
PY
    then
      warn "Xray IPv6 inbound is not active in the saved configuration."
      return 1
    fi
  elif ! systemctl is-active --quiet 'awg-quick@awg0.service'; then
    warn "AmneziaWG is not active; refusing to generate IPv6 endpoint profiles."
    return 1
  fi
}

choose_saved_profile() {
  local existing="$1" name index=0
  local -a profiles=()
  while IFS= read -r name; do
    [[ -n "$name" ]] && profiles+=("$name")
  done <<< "$existing"
  if (( ${#profiles[@]} == 0 )); then
    warn "There are no existing profiles."
    return 1
  fi
  say "Choose a saved profile:"
  for name in "${profiles[@]}"; do
    ((index++))
    say "$index) $name"
  done
  say "0) Exit"
  local -a menu_labels=()
  for ((index=0;index<${#profiles[@]};index++)); do menu_labels+=("$((index+1)):${profiles[index]}"); done
  menu_labels+=("0:Exit")
  ask_choice "Profile number:" "${menu_labels[@]}" || return 1
  if [[ "$REPLY" == 0 ]]; then return 1; fi
  if [[ ! "$REPLY" =~ ^[0-9]+$ ]] || ((10#$REPLY < 1 || 10#$REPLY > ${#profiles[@]})); then
    warn "Invalid profile number."
    return 1
  fi
  CHOSEN_PROFILE="${profiles[10#$REPLY-1]}"
}

profile_menu() {
  local type action name existing
  say "1) VLESS / Xray"
  say "2) AmneziaWG"
  say "0) Exit"
  ask_choice "Choose profile type:" "1:VLESS / Xray" "2:AmneziaWG" "0:Exit" || return
  case "$REPLY" in
    1) type=xray;;
    2) type=awg;;
    0) return;;
    *) warn "Unknown profile type."; return;;
  esac
  if [[ "$type" == xray ]]; then
    [[ -s /usr/local/etc/xray/config.json ]] || { warn "Xray is not installed."; return; }
  else
    [[ -s /etc/amnezia/amneziawg/awg0.conf ]] || { warn "AmneziaWG is not installed."; return; }
  fi

  while :; do
  existing="$(list_profiles "$type")" || { warn "Cannot enumerate existing profiles."; return 1; }
  if [[ -n "$existing" ]]; then
    say ""
    say "Existing $type profiles:"
    while IFS= read -r name; do say "  - $name"; done <<< "$existing"
  else
    say "No $type profiles have been created yet."
  fi

  say ""
  say "1) Add new profiles"
  say "2) View existing profiles"
  say "3) Rename profile"
  say "4) Delete profile"
  say "0) Exit"
  ask_choice "Choose action:" "1:Add new profiles" "2:View existing profiles" "3:Rename profile" "4:Delete profile" "0:Exit" || continue
  case "$REPLY" in
    1) action=add;;
    2) action=get;;
    3) action=rename;;
    4) action=delete;;
    0) return;;
    *) warn "Unknown action."; continue;;
  esac
  if [[ "$action" == rename || "$action" == delete ]]; then
    [[ -n "$existing" ]] || { warn "There are no existing profiles."; continue; }
    choose_saved_profile "$existing" || continue
    name="$CHOSEN_PROFILE"
    local new_name
    if [[ "$action" == rename ]]; then
      ask "New name for profile '$name':"
      new_name="$REPLY"
      [[ "$new_name" =~ ^[A-Za-z0-9_-]{1,48}$ ]] || { warn "Invalid new profile name."; continue; }
      if grep -Fxq -- "$new_name" <<< "$existing"; then
        warn "Profile '$new_name' already exists."
        continue
      fi
      say "Rename '$name' to '$new_name' without changing client credentials."
      if ! yesno "Confirm profile rename?"; then say "No changes made."; continue; fi
      run_step "Renaming $type profile: $name" python3 /opt/vps-installer/lib/profile_edit.py "$type" rename "$name" "$new_name" ||
        warn "Rename failed. See error details above."
    else
      say "WARNING: Deleting '$name' will revoke this client's VPN access."
      say "Other client profiles will remain unchanged."
      if ! yesno "Revoke access and delete profile '$name'?"; then say "No changes made."; continue; fi
      ask "To confirm deletion, type the profile name '$name':"
      [[ "$REPLY" == "$name" ]] || { warn "Deletion cancelled: confirmation did not match."; continue; }
      run_step "Deleting $type profile: $name" python3 /opt/vps-installer/lib/profile_edit.py "$type" delete "$name" ||
        warn "Deletion failed. See error details above."
    fi
    say ""
    say "Profile operation finished."
    continue
  fi
  if [[ "$action" == get ]]; then
    if [[ -z "$existing" ]]; then
      warn "There are no existing profiles to view."
      if yesno "Would you like to create a new $type profile instead?"; then
        action=add
      else
        say "No changes were made."
        continue
      fi
    else
      choose_saved_profile "$existing" || continue
      name="$CHOSEN_PROFILE"
      profile_family "$type" || continue
      profile_format "$type" get || continue
      run_secret_step "Reading $type profile: $name" \
        python3 /opt/vps-installer/lib/profile_manager.py "$type" get "$name" --format "$FORMAT" --family "$FAMILY" ||
        warn "Could not display saved profile: $name"
      say ""
      say "Profile viewing finished. No client keys were changed."
      continue
    fi
  fi

  say ""
  ask "Provide name for new $type profile:"
  name="$REPLY"
  [[ "$name" =~ ^[A-Za-z0-9_-]{1,48}$ ]] || {
    warn "Allowed: A-Z, a-z, 0-9, underscore and dash (1-48)."
    continue
  }
  if grep -Fxq -- "$name" <<< "$existing"; then
    warn "Profile '$name' already exists. Choose View existing profiles instead."
    continue
  fi
  say "Selected profile name: $name"
  say ""
  profile_family "$type" || continue
  profile_format "$type" add || continue
  if ! run_step "Creating $type profile: $name" \
    python3 /opt/vps-installer/lib/profile_manager.py "$type" add "$name" --format none; then
    warn "Profile creation failed: $name"
    continue
  fi
  run_secret_step "Preparing $type connection for $name" \
    python3 /opt/vps-installer/lib/profile_manager.py "$type" get "$name" --format "$FORMAT" --family "$FAMILY" ||
    warn "Could not display newly created profile: $name"
  say ""
  say "Profile operation finished."
  done
}
