#!/usr/bin/env bash
# Ubuntu 24.04 base setup. Requires functions from common.sh.
valid_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535)); }
prompt_color() {
  local label="$1" default="$2"
  printf '\033[31m1) Red\033[0m  \033[38;5;208m2) Orange\033[0m  \033[33m3) Yellow\033[0m  \033[32m4) Green\033[0m\n' >&4
  printf '\033[34m5) Blue\033[0m  \033[38;5;54m6) Indigo\033[0m  \033[35m7) Violet\033[0m\n' >&4
  while :; do
    ask_default "Color for $label (1-7)" "$default"
    [[ "$REPLY" =~ ^[1-7]$ ]] && { CHOSEN_COLOR="$REPLY"; return; }
    warn "Choose a number 1-7."
  done
}
ansi_color() { case "$1" in 1) echo 31;; 2) echo '38;5;208';; 3) echo 33;; 4) echo 32;; 5) echo 34;; 6) echo '38;5;54';; 7) echo 35;; *) echo 0;; esac; }
set_prompt() {
  local owner="$1" color="$2" home rc tmp
  home="$(getent passwd "$owner" | cut -d: -f6)"
  rc="$home/.bashrc"
  touch "$rc"; tmp="$(mktemp)"
  sed '/^# BEGIN VPS INSTALLER PROMPT$/,/^# END VPS INSTALLER PROMPT$/d' "$rc" > "$tmp"
  cat "$tmp" > "$rc"; rm -f "$tmp"
  printf '%s\n' '# BEGIN VPS INSTALLER PROMPT' >> "$rc"
  local ps1 suffix
  suffix='$'
  [[ "$owner" == root ]] && suffix='#'
  ps1="\\[\\e[1;$(ansi_color "$color")m\\]\\h $owner:\\w"
  ps1+="$suffix"
  ps1+="\\[\\e[0m\\] "
  printf 'if [[ $- == *i* ]]; then\n  PS1=%q\nfi\n' "$ps1" >> "$rc"
  printf '%s\n' '# END VPS INSTALLER PROMPT' >> "$rc"
  chown "$owner:$owner" "$rc"
}
configure_fzf_bash() {
  local owner="$1" home rc tmp
  home="$(getent passwd "$owner" | cut -d: -f6)"
  rc="$home/.bashrc"
  touch "$rc"; tmp="$(mktemp)" || die "Cannot prepare Bash fzf settings."
  sed '/^# BEGIN VPS INSTALLER FZF$/,/^# END VPS INSTALLER FZF$/d' "$rc" > "$tmp" || { rm -f "$tmp"; die "Cannot update Bash fzf settings."; }
  cat "$tmp" > "$rc"; rm -f "$tmp"
  cat >> "$rc" <<'EOF'
# BEGIN VPS INSTALLER FZF
if [[ $- == *i* && -r /usr/share/doc/fzf/examples/key-bindings.bash ]]; then
  source /usr/share/doc/fzf/examples/key-bindings.bash
fi
# END VPS INSTALLER FZF
EOF
  chown "$owner:$owner" "$rc"
}
create_admin() {
  local username="$1"
  [[ "$username" =~ ^[a-z_][a-z0-9_-]{0,30}$ && "$username" != root ]] || die "Invalid administrator username."
  id "$username" >/dev/null 2>&1 || run_step "Create administrator" adduser --disabled-password --gecos '' "$username" || die "Cannot create administrator."
  usermod -aG sudo "$username" || die "Cannot grant sudo."
  printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$username" >"/etc/sudoers.d/90-vps-installer-$username"
  chmod 0440 "/etc/sudoers.d/90-vps-installer-$username"
  visudo -cf "/etc/sudoers.d/90-vps-installer-$username" >/dev/null || die "Invalid sudoers file."
  state_set USERNAME "$username"
}
# Extract a usable public key from an authorized_keys record, including
# provider-injected SSH restrictions before the key type.
extract_ssh_public_key() {
  local line="$1" candidate
  if [[ "$line" =~ (^|[[:space:]])(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)[[:space:]]+([A-Za-z0-9+/=]+)($|[[:space:]]) ]]; then
    candidate="${BASH_REMATCH[2]} ${BASH_REMATCH[3]}"
    if printf '%s\n' "$candidate" | ssh-keygen -lf - >/dev/null 2>&1; then
      REPLY="$candidate"
      return 0
    fi
  fi
  return 1
}
configure_keys() {
  local username="$1" home dest key candidate root_keys=/root/.ssh/authorized_keys copied=0
  home="$(getent passwd "$username" | cut -d: -f6)"
  dest="$home/.ssh/authorized_keys"
  install -d -m 700 -o "$username" -g "$username" "$home/.ssh"
  if [[ -s "$root_keys" ]]; then
    say "Provider/root authorized_keys file found; checking for usable public keys."
  else
    warn "No root authorized_keys file with public keys found."
  fi
  if yesno "Copy the provider SSH public key for the administrator?"; then
    if [[ -r "$root_keys" ]]; then
      # Include the last record even when the file does not end with a newline.
      while IFS= read -r key || [[ -n "$key" ]]; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        if extract_ssh_public_key "$key"; then
          candidate="$REPLY"
          # Preserve any existing administrator keys; avoid duplicates.
          grep -Fxq -- "$candidate" "$dest" 2>/dev/null ||
            printf '%s\n' "$candidate" >> "$dest"
          copied=$((copied+1))
        fi
      done < "$root_keys"
    fi
    if ((copied == 0)) && [[ ! -s "$dest" ]]; then
      warn "No usable SSH public key could be extracted from the provider file."
      say "You can paste the SSH public key manually instead."
    fi
  fi
  if [[ ! -s "$dest" ]]; then
    while :; do
      ask "Paste one SSH PUBLIC key (not your private key):"
      key="$REPLY"
      if extract_ssh_public_key "$key"; then
        printf '%s\n' "$REPLY" >> "$dest"
        break
      fi
      warn "Invalid SSH public key. Paste the complete ssh-ed25519, ssh-rsa or ECDSA public-key line."
    done
  fi
  [[ -s "$dest" ]] || die "No public keys configured."
  chown "$username:$username" "$dest"; chmod 600 "$dest"
  state_set SSH_KEY yes
  ok "Administrator SSH public key configured."
}
configure_ssh() {
  local port="$1" config=/etc/ssh/sshd_config.d/00-vps-installer.conf existing
  valid_port "$port" || die "SSH port must be 1-65535."
  # A clean Ubuntu host normally has no uncommented Port directives.
  existing="$(grep -RhE '^[[:space:]]*Port[[:space:]]+[0-9]+' /etc/ssh/sshd_config /etc/ssh/sshd_config.d 2>/dev/null | grep -Ev "^[[:space:]]*Port[[:space:]]+($port|22)[[:space:]]*$" || true)"
  [[ -z "$existing" ]] || die "Conflicting SSH Port directives. Manual review required."
  ufw default deny incoming >/dev/null || die "Cannot set UFW incoming default."
  ufw default deny routed >/dev/null || die "Cannot set UFW routed default."
  ufw allow "$port/tcp" >/dev/null || die "Cannot open new SSH port."
  if [[ "$port" != 22 ]]; then ufw allow 22/tcp >/dev/null || die "Cannot temporarily allow TCP 22."; fi
  # Configure the final SSH access policy immediately.
  cat > "$config" <<EOF
# Managed by VPS Installer
Port $port
AddressFamily inet
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
ClientAliveInterval 60
ClientAliveCountMax 3
EOF
  install -d -o root -g root -m 755 /run/sshd || die "Cannot create SSH runtime directory."
  /usr/sbin/sshd -t || die "SSH syntax check failed."
  local live_ports
  live_ports="$(/usr/sbin/sshd -T | awk '$1=="port"{print $2}')" || die "Cannot inspect effective SSH ports."
  [[ "$live_ports" == "$port" ]] || die "Unexpected SSH ports: $live_ports"
  systemctl stop ssh.socket >/dev/null 2>&1 || true
  systemctl disable ssh.socket >/dev/null 2>&1 || true
  systemctl mask ssh.socket >/dev/null 2>&1 || true
  systemctl unmask ssh.service >/dev/null 2>&1 || true
  systemctl enable ssh.service >/dev/null || die "SSH enable failed."
  systemctl restart ssh.service || die "SSH restart failed."
  systemctl is-active --quiet ssh.service || die "SSH is inactive."
  ss -H -ltn | awk '{print $4}' | grep -Eq ":$port$" || die "SSH is not listening on TCP $port."
  state_set SSH_PORT "$port"
}
prepare_fail2ban() {
  local port="$1" tries="$2" window="$3" ban="$4" max="$5" value
  [[ "$tries" =~ ^[1-9][0-9]?$ ]] || die "Invalid retry limit."
  for value in "$window" "$ban" "$max"; do
    [[ "$value" =~ ^[1-9][0-9]*[smhdw]$ ]] || die "Invalid time value: $value"
  done
  phase "Preparing Fail2ban SSH protection"
  say "Configuring $tries attempts per $window, initial ban $ban, maximum ban $max."
  cat >/etc/fail2ban/fail2ban.local <<'EOF'
[Definition]
allowipv6 = auto
loglevel = INFO
logtarget = /var/log/fail2ban.log
EOF
  mkdir -p /etc/fail2ban/jail.d
  cat >/etc/fail2ban/jail.d/90-vps-installer.conf <<EOF
[DEFAULT]
bantime.increment = true
bantime.factor = 2
bantime.max = $max
[sshd]
enabled = true
backend = systemd
port = $port
mode = aggressive
bantime = $ban
findtime = $window
maxretry = $tries
EOF
}
fail2ban_diagnostics() {
  warn "Fail2ban did not become ready. Recent service messages:"
  journalctl -u fail2ban --no-pager -n 20 -o cat >&4 2>/dev/null || true
  warn "Fail2ban configuration validation:"
  fail2ban-client -t >&4 2>&1 || true
}
finish_install() {
  local port="$1" attempt
  phase "Finishing firewall and SSH intrusion protection"
  say "Reapplying UFW rules and checking Fail2ban configuration..."
  if ufw status | grep -q '^Status: active'; then ufw reload >/dev/null || die "UFW reload failed."; else ufw --force enable >/dev/null || die "UFW enable failed."; fi
  if ! fail2ban-client -t >>"$LOG_FILE" 2>&1; then
    fail2ban_diagnostics
    die "Fail2ban configuration check failed."
  fi
  ok "Fail2ban configuration validated."
  run_step "Enable Fail2ban" systemctl enable --now fail2ban || { fail2ban_diagnostics; die "Fail2ban failed to start."; }
  run_step "Restart Fail2ban with SSH settings" systemctl restart fail2ban || { fail2ban_diagnostics; die "Fail2ban failed to restart."; }
  for attempt in {1..15}; do
    if systemctl is-active --quiet fail2ban && fail2ban-client status sshd >>"$LOG_FILE" 2>&1; then break; fi
    printf '\r⠹ Waiting for Fail2ban SSH jail (%s/15)...' "$attempt" >&4
    sleep 1
  done
  printf '\r\033[K' >&4
  if ! systemctl is-active --quiet fail2ban || ! fail2ban-client status sshd >>"$LOG_FILE" 2>&1; then
    fail2ban_diagnostics
    die "Fail2ban SSH jail is not ready. SSH/UFW port 22 cleanup was not performed."
  fi
  ok "Fail2ban SSH jail is active on TCP $port."
  if [[ "$port" != 22 ]]; then
    ufw --force delete allow 22/tcp >/dev/null || die "Could not remove temporary UFW TCP 22 allowance."
    ok "Temporary UFW rule for TCP 22 removed."
  fi
  state_set BASE_CONFIGURED yes
  state_set INSTALL_COMPLETE yes
}
summary_screen() {
  local username port
  username="$(state_get USERNAME)"; port="$(state_get SSH_PORT)"
  say "========================================"
  say "         INSTALLATION SUMMARY"
  say "========================================"
  printf 'Administrator: \033[1;%sm%s\033[0m\n' "$(ansi_color "$(state_get USER_COLOR)")" "$username" >&4
  say "SSH public key: $(state_get SSH_KEY)"
  say "SSH port: $port/tcp"
  say "UFW: $(ufw status | head -n 1)"
  say "Fail2ban: $(check_status fail2ban)"
  say "Xray: $(check_status xray)"
  say "AmneziaWG: $(check_status awg-quick@awg0)"
  say "GeoBlock: $(check_status awg-geoblock)"
  say ""
  printf '\033[1;33m========================================================\033[0m\n' >&4
  printf '\033[1;33m  IMPORTANT: RECONNECT TO CONTINUE SETUP\033[0m\n' >&4
  printf '\033[1;33m========================================================\033[0m\n' >&4
  say "1. Open a NEW PuTTY SSH session with these settings:"
  say "     Host / IP: $(state_get PUBLIC_IPV4)"
  say "     SSH port:  $port"
  printf '     Username:  \033[1;%sm%s\033[0m\n' "$(ansi_color "$(state_get USER_COLOR)")" "$username" >&4
  say "     Auth:      Your existing SSH private key"
  say ""
  say "2. After connecting as $username, run this LOCAL command:"
  printf '\033[1;32m     sudo vps-installer\033[0m\n' >&4
  say ""
  say "No curl command or second installation is required."
  printf '\033[1;33m========================================================\033[0m\n' >&4
}
vps_defence_customization() {
  require_ubuntu
  local saved_port
  if [[ "$(state_get INSTALL_COMPLETE)" == yes ]]; then
    saved_port="$(state_get SSH_PORT)"
    valid_port "$saved_port" || die "Saved SSH port is invalid."
    say "VPS defence and customization are already configured."
    return 0
  fi
  if [[ "$(state_get BASE_CONFIGURED)" == yes ]]; then
    # An older installer could leave this marker before Fail2ban finalized.
    saved_port="$(state_get SSH_PORT)"
    valid_port "$saved_port" || die "Cannot complete prior setup: missing SSH port state."
    phase "Completing previous VPS defence setup"
    finish_install "$saved_port"
    summary_screen
    return 0
  fi
  local username port iface ip tries window ban maximum root_color user_color
  while :; do
    ask "Username for administrator (required, no default):"
    username="$REPLY"
    if [[ "$username" =~ ^[a-z_][a-z0-9_-]{0,30}$ && "$username" != root ]]; then
      break
    fi
    warn "Enter a valid Linux username (lowercase letters, digits, '_' or '-')."
  done
  ask_default "SSH port" 221; port="$REPLY"
  valid_port "$port" || die "Invalid SSH port."
  iface="$(wan_if)"; ip="$(public_ip)"
  [[ -n "$iface" && -n "$ip" ]] || die "Public IPv4 or WAN interface cannot be detected. NAT VPS not supported automatically."
  state_set WAN_IF "$iface"; state_set PUBLIC_IPV4 "$ip"
  say "Detected WAN: $iface | Public IPv4: $ip"
  local ip6
  ip6="$(python3 "$ROOT/lib/ipv6_support.py" detect 2>>"$LOG_FILE")" || ip6=""
  if [[ -n "$ip6" ]]; then
    state_set PUBLIC_IPV6 "$ip6"
    say "Available IPv6 endpoint: $ip6 (optional; VPN IPv6 egress remains blocked)"
  fi
  run_step "Update APT" apt_retry apt-get -o DPkg::Lock::Timeout=10 update || die "APT update failed."
  run_step "Upgrade Ubuntu" apt_retry apt-get -o DPkg::Lock::Timeout=10 upgrade -y || die "APT upgrade failed."
  run_step "Install core packages" apt_retry apt-get -o DPkg::Lock::Timeout=10 install -y sudo openssh-server ufw fail2ban fzf curl wget ca-certificates openssl python3 iproute2 iptables nftables dnsutils qrencode software-properties-common gnupg || die "Core packages failed."
  phase "Configuring administrator and SSH access"
  create_admin "$username"
  configure_keys "$username"
  configure_fzf_bash root; configure_fzf_bash "$username"
  ok "fzf Bash key bindings configured for root and $username (active after next login)."
  if yesno "Would you like a colorized Bash prompt?"; then
    prompt_color root 1; root_color="$CHOSEN_COLOR"
    prompt_color "$username" 5; user_color="$CHOSEN_COLOR"
    set_prompt root "$root_color"; set_prompt "$username" "$user_color"
    state_set USER_COLOR "$user_color"; state_set ROOT_COLOR "$root_color"
    ok "Bash prompt colors saved: root=$(ansi_color "$root_color"), $username=$(ansi_color "$user_color")."
    say "Colors will appear after the next login."
  fi
  phase "Configuring SSH and the firewall"
  configure_ssh "$port"
  ok "SSH configuration validated and SSH service restarted on TCP $port."
  phase "Choose Fail2ban SSH security settings"
  say "Defaults: 3 attempts within 10 minutes, 1 hour initial ban, up to 72 hours."
  ask_default "Fail2ban: how many tries" 3; tries="$REPLY"
  ask_default "Fail2ban: how often (findtime)" 10m; window="$REPLY"
  ask_default "Fail2ban: for how long to ban" 1h; ban="$REPLY"
  ask_default "Fail2ban: maximum escalating ban" 72h; maximum="$REPLY"
  prepare_fail2ban "$port" "$tries" "$window" "$ban" "$maximum"
  ok "Fail2ban settings saved; the service will start at the end."
  finish_install "$port"
  summary_screen
}
