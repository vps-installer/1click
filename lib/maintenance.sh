#!/usr/bin/env bash
# 1click optional systemd maintenance management. Requires lib/common.sh helpers.
MAINT_DIR=/etc/vps-installer
MAINT_CONFIG=$MAINT_DIR/maintenance.env
MAINT_UNITS=/etc/systemd/system
MAINT_TASK=/usr/local/sbin/vps-maintenance-task
MAINT_NOTIFY_SCRIPT=/usr/local/sbin/vps-maintenance-notify
MAINT_GEODATA=/usr/local/sbin/vps-maintenance-geodata

maint_load() {
  MAINT_SENDER='' MAINT_RECIPIENT='' MAINT_NOTIFY=no MAINT_AUTO_REBOOT=no
  if [[ -f "$MAINT_CONFIG" ]]; then
    # The installer alone writes this root-owned 0600 file.
    source "$MAINT_CONFIG"
  fi
}
maint_save() {
  install -d -m 700 "$MAINT_DIR"
  local tmp
  tmp="$(mktemp "$MAINT_DIR/.maintenance.XXXXXX")" || return 1
  {
    printf 'MAINT_SENDER=%q\n' "$MAINT_SENDER"
    printf 'MAINT_RECIPIENT=%q\n' "$MAINT_RECIPIENT"
    printf 'MAINT_NOTIFY=%q\n' "$MAINT_NOTIFY"
    printf 'MAINT_AUTO_REBOOT=%q\n' "$MAINT_AUTO_REBOOT"
  } > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$MAINT_CONFIG"
}
maint_ensure_binaries() {
  install -m 700 "$ROOT/lib/maintenance_task.sh" "$MAINT_TASK" || return 1
  install -m 700 "$ROOT/lib/maintenance_notify.sh" "$MAINT_NOTIFY_SCRIPT" || return 1
  install -m 700 "$ROOT/lib/maintenance_geodata.py" "$MAINT_GEODATA" || return 1
}
maint_ask_schedule() {
  local title="$1" current="$2" selection
  say "1) Daily at 04:20"
  say "2) Mon, Wed, Fri at 00:01"
  say "3) Monday at 03:00"
  say "4) Custom systemd OnCalendar expression"
  say "0) Exit"
  ask_choice "Choose schedule:" '1:Daily' '2:Three days per week' '3:Weekly' '4:Custom' '0:Exit' || return 1
  selection="$REPLY"
  case "$selection" in
    1) REPLY='*-*-* 04:20:00' ;;
    2) REPLY='Mon,Wed,Fri *-*-* 00:01:00' ;;
    3) REPLY='Mon *-*-* 03:00:00' ;;
    4) ask "Systemd OnCalendar expression [$current]:"; REPLY="${REPLY:-$current}" ;;
    0) return 1 ;;
  esac
  # Reject special characters that could make a second unit setting.
  [[ "$REPLY" != *$'\n'* && "$REPLY" != *$'\r'* && "$REPLY" != *'%'* ]] || { warn 'Invalid calendar expression.'; return 1; }
  systemd-analyze calendar "$REPLY" >/dev/null 2>&1 || { warn 'Systemd calendar expression rejected.'; return 1; }
}
maint_timer_schedule() {
  local unit="$1" schedule="$2" dir="$MAINT_UNITS/$unit.d" tmp
  mkdir -p "$dir" || return 1
  tmp="$(mktemp "$dir/.schedule.XXXXXX")" || return 1
  printf '[Timer]\nOnCalendar=\nOnCalendar=%s\n' "$schedule" > "$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$dir/10-vps-maintenance.conf" || return 1
  systemctl daemon-reload || return 1
  systemctl try-restart "$unit" || return 1
}
maint_timer_status() {
  local name="$1" unit="${1}.timer" service="${1}.service" enabled active next last result
  enabled="$(systemctl is-enabled "$unit" 2>/dev/null)" || [[ -n "$enabled" ]] || enabled=not-installed
  active="$(systemctl is-active "$unit" 2>/dev/null)" || [[ -n "$active" ]] || active=inactive
  last="$(systemctl show -p LastTriggerUSec --value "$unit" 2>/dev/null || :)"
  next="$(systemctl show -p NextElapseUSecRealtime --value "$unit" 2>/dev/null || :)"
  result="$(systemctl show -p Result --value "$service" 2>/dev/null || :)"
  [[ -n "$last" && "$last" != n/a ]] || last='Never run'
  [[ -n "$next" && "$next" != n/a ]] || next='Not scheduled'
  [[ -n "$result" ]] || result='Unknown'
  say "$name: timer=$enabled/$active; last=$last; next=$next; service=$result"
  if [[ "$result" == success ]]; then
    local outcome
    outcome="$(journalctl -u "$service" -n 30 -o cat --no-pager 2>/dev/null | grep -E '^(UPDATED|UNCHANGED|FAILED|ERROR):' | tail -n 1 || :)"
    [[ -z "$outcome" ]] || say "  Result: $outcome"
  fi
}
maint_migrate_legacy_cron() {
  local script="$1" old backup temp
  old="$(crontab -l 2>/dev/null || :)"
  if grep -Fq -- "$script" <<< "$old"; then
    warn "Legacy root crontab still runs $script. Running both jobs would duplicate updates."
    yesno 'Back up root crontab and disable the matching old job?' || return 1
    backup="$MAINT_DIR/crontab.root.before-maintenance.$(date +%Y%m%d%H%M%S)"
    install -d -m 700 "$MAINT_DIR"
    printf '%s\n' "$old" > "$backup"
    chmod 600 "$backup"
    temp="$(mktemp)" || return 1
    printf '%s\n' "$old" | grep -Fv -- "$script" > "$temp" || :
    crontab "$temp" || { rm -f "$temp"; return 1; }
    rm -f "$temp"
    ok "Matching cron job disabled. Previous crontab saved: $backup"
  fi
  # Other cron sources must not be modified by an unrelated installer.
  if grep -RFql -- "$script" /etc/crontab /etc/cron.d 2>/dev/null; then
    warn "Another cron file still runs $script; disable it before enabling this timer."
    return 1
  fi
}
maint_xray_setup() {
  [[ -f /usr/local/etc/xray/config.json ]] || { warn 'Install Xray first.'; return 1; }
  maint_ensure_binaries || return 1
  cat > "$MAINT_UNITS/vps-maintenance-xray.service" <<EOF
[Unit]
Description=Update Xray GeoIP and GeoSite safely
Wants=network-online.target
After=network-online.target xray.service
[Service]
Type=oneshot
ExecStart=$MAINT_GEODATA
EOF
  if [[ ! -f "$MAINT_UNITS/vps-maintenance-xray.timer" ]]; then
    cat > "$MAINT_UNITS/vps-maintenance-xray.timer" <<'EOF'
[Unit]
Description=Xray GeoData updates
[Timer]
OnCalendar=Mon,Wed,Fri *-*-* 00:01:00
RandomizedDelaySec=5m
Persistent=true
Unit=vps-maintenance-xray.service
[Install]
WantedBy=timers.target
EOF
  fi
  systemctl daemon-reload
}
maint_awg_setup() {
  [[ -f "$MAINT_UNITS/awg-geoblock-update.timer" ]] || { warn 'Install AmneziaWG GeoBlock first.'; return 1; }
  maint_ensure_binaries || return 1
  # Preserve original timer and updater; only wrap service execution for notifications.
  local dir="$MAINT_UNITS/awg-geoblock-update.service.d"
  mkdir -p "$dir"
  cat > "$dir/20-vps-maintenance-notifications.conf" <<EOF
[Service]
ExecStart=
ExecStart=$MAINT_TASK awg
EOF
  systemctl daemon-reload
}
maint_ubuntu_setup() {
  maint_ensure_binaries || return 1
  run_step 'Install unattended-upgrades' apt_retry apt-get -o DPkg::Lock::Timeout=180 install -y unattended-upgrades || return 1
  cat > "$MAINT_UNITS/vps-maintenance-apt.service" <<EOF
[Unit]
Description=Weekly Ubuntu APT upgrade
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
ExecStart=$MAINT_TASK apt
TimeoutStartSec=2h
EOF
  if [[ ! -f "$MAINT_UNITS/vps-maintenance-apt.timer" ]]; then
    cat > "$MAINT_UNITS/vps-maintenance-apt.timer" <<'EOF'
[Unit]
Description=Weekly Ubuntu APT upgrade
[Timer]
OnCalendar=Mon *-*-* 03:00:00
RandomizedDelaySec=10m
Persistent=false
Unit=vps-maintenance-apt.service
[Install]
WantedBy=timers.target
EOF
  fi
  # Only security origins for automatic daily updates (including Ubuntu ESM security).
  cat > /etc/apt/apt.conf.d/52-vps-maintenance-security <<'EOF'
#clear Unattended-Upgrade::Allowed-Origins;
#clear Unattended-Upgrade::Origins-Pattern;
Unattended-Upgrade::Allowed-Origins {
 "${distro_id}:${distro_codename}-security";
 "${distro_id}ESMApps:${distro_codename}-apps-security";
 "${distro_id}ESM:${distro_codename}-infra-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
EOF
  systemctl daemon-reload
  cat > "$MAINT_UNITS/vps-maintenance-boot.service" <<EOF
[Unit]
Description=Verify VPN services after maintenance reboot
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=$MAINT_TASK boot-check
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable vps-maintenance-boot.service >/dev/null || return 1
}
maint_ubuntu_enabled() {
  local choice="$1" v
  if [[ "$choice" == yes ]]; then v=1; else v=0; fi
  cat > /etc/apt/apt.conf.d/99-vps-maintenance-periodic <<EOF
APT::Periodic::Update-Package-Lists "$v";
APT::Periodic::Unattended-Upgrade "$v";
Unattended-Upgrade::Automatic-Reboot "false";
EOF
  if [[ "$choice" == yes ]]; then
    systemctl unmask apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || :
    systemctl enable --now apt-daily.timer apt-daily-upgrade.timer vps-maintenance-apt.timer
  else
    systemctl disable --now apt-daily-upgrade.timer vps-maintenance-apt.timer
    # apt-daily.timer is shared by other APT components; do not disable it globally.
  fi
}
maint_log() {
  local unit="$1"
  journalctl -u "$unit.service" -n 80 --no-pager -o short-iso >&4 2>&1 || warn 'No logs available.'
  pause
}
maint_xray_menu() {
  local choice
  while :; do
    say ''; say 'XRAY GEODATA UPDATES'
    maint_timer_status vps-maintenance-xray
    say '1) Enable / Disable updates'; say '2) Change update schedule'
    say '3) Update now'; say '4) View last update log'; say '0) Exit'
    ask_choice 'Choose option:' '1:Toggle' '2:Schedule' '3:Run' '4:Log' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) maint_xray_setup || continue
         if systemctl is-enabled --quiet vps-maintenance-xray.timer; then
           systemctl disable --now vps-maintenance-xray.timer || warn 'Failed to disable timer.'
         else
           maint_migrate_legacy_cron '/usr/local/bin/update-xray-rules.sh' || continue
           systemctl enable --now vps-maintenance-xray.timer || warn 'Failed to enable timer.'
         fi ;;
      2) maint_xray_setup || continue
         if maint_ask_schedule 'Xray' 'Mon,Wed,Fri *-*-* 00:01:00'; then
           maint_timer_schedule vps-maintenance-xray.timer "$REPLY" || warn 'Schedule update failed.'
         fi ;;
      3) maint_xray_setup && systemctl start vps-maintenance-xray.service || warn 'Xray GeoData update failed.' ;;
      4) maint_log vps-maintenance-xray ;;
      0) return ;;
    esac
  done
}
maint_awg_menu() {
  local choice
  while :; do
    say ''; say 'AMNEZIAWG GEOBLOCK UPDATES'
    maint_timer_status awg-geoblock-update
    say '1) Enable / Disable updates'; say '2) Change update schedule'
    say '3) Update now'; say '4) View last update log'; say '0) Exit'
    ask_choice 'Choose option:' '1:Toggle' '2:Schedule' '3:Run' '4:Log' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) maint_awg_setup || continue
         if systemctl is-enabled --quiet awg-geoblock-update.timer; then
           systemctl disable --now awg-geoblock-update.timer || warn 'Failed to disable timer.'
         else systemctl enable --now awg-geoblock-update.timer || warn 'Failed to enable timer.'; fi ;;
      2) maint_awg_setup || continue
         if maint_ask_schedule 'AmneziaWG' '*-*-* 04:20:00'; then
           maint_timer_schedule awg-geoblock-update.timer "$REPLY" || warn 'Schedule update failed.'
         fi ;;
      3) maint_awg_setup && systemctl start awg-geoblock-update.service || warn 'AWG GeoBlock update failed.' ;;
      4) maint_log awg-geoblock-update ;;
      0) return ;;
    esac
  done
}
maint_ubuntu_reboot_menu() {
  maint_load
  say 'AUTOMATIC REBOOT'; say '1) Disabled (recommended)'; say '2) Reboot when required'; say '0) Exit'
  ask_choice 'Choose option:' '1:Disabled' '2:Enabled' '0:Exit' || return
  case "$REPLY" in 1) MAINT_AUTO_REBOOT=no;; 2) MAINT_AUTO_REBOOT=yes;; 0) return;; esac
  maint_save && ok "Automatic reboot: $MAINT_AUTO_REBOOT"
}
maint_ubuntu_menu() {
  local choice
  while :; do
    maint_load
    say ''; say 'UBUNTU SYSTEM UPDATES'
    maint_timer_status apt-daily-upgrade
    maint_timer_status vps-maintenance-apt
    say "Automatic reboot: $MAINT_AUTO_REBOOT"
    if [[ -f /run/reboot-required ]]; then say 'Reboot required: Yes'; else say 'Reboot required: No'; fi
    say '1) Enable / Disable updates'; say '2) Change update schedule'
    say '3) Automatic reboot settings'; say '4) Run update now'
    say '5) View last update log'; say '0) Exit'
    ask_choice 'Choose option:' '1:Toggle' '2:Schedule' '3:Reboot' '4:Run' '5:Log' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) maint_ubuntu_setup || continue
         if systemctl is-enabled --quiet vps-maintenance-apt.timer; then
           maint_ubuntu_enabled no || warn 'Failed to disable Ubuntu updates.'
         else
           maint_migrate_legacy_cron '/usr/local/bin/vds-upgrade.sh' || continue
           maint_ubuntu_enabled yes || warn 'Failed to enable Ubuntu updates.'
         fi ;;
      2) maint_ubuntu_setup || continue
         say 'Security: daily (managed by Ubuntu apt-daily-upgrade.timer).'
         say 'Set weekly APT schedule:'
         if maint_ask_schedule 'Ubuntu weekly' 'Mon *-*-* 03:00:00'; then
           maint_timer_schedule vps-maintenance-apt.timer "$REPLY" || warn 'Schedule update failed.'
         fi ;;
      3) maint_ubuntu_reboot_menu ;;
      4) maint_ubuntu_setup && systemctl start vps-maintenance-apt.service || warn 'APT update failed.' ;;
      5) maint_log vps-maintenance-apt ;;
      0) return ;;
    esac
  done
}
maint_valid_email() { [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; }
maint_smtp_configure() {
  local host port encryption sender recipient password original tmp save
  maint_load
  ask 'SMTP server hostname:'; host="$REPLY"
  [[ "$host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || { warn 'Invalid SMTP hostname.'; return 1; }
  ask_default 'SMTP port' 587; port="$REPLY"
  [[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || { warn 'Invalid port.'; return 1; }
  say '1) STARTTLS (recommended, port 587)'; say '2) Implicit TLS (port 465)'; say '0) Exit'
  ask_choice 'Choose encryption:' '1:STARTTLS' '2:TLS' '0:Exit' || return 1
  case "$REPLY" in 1) encryption=on;; 2) encryption=off;; 0) return 1;; esac
  ask 'Sender / SMTP username:'; sender="$REPLY"
  maint_valid_email "$sender" || { warn 'Invalid sender.'; return 1; }
  printf 'SMTP password (hidden): ' >&4
  IFS= read -rs password <&3 || return 1
  printf '\n' >&4
  [[ -n "$password" ]] || { warn 'Password required.'; return 1; }
  ask 'Recipient email address:'; recipient="$REPLY"
  maint_valid_email "$recipient" || { warn 'Invalid recipient.'; return 1; }
  if [[ -f /etc/msmtprc ]] && ! grep -Fxq '# Managed by VPS Installer' /etc/msmtprc; then
    warn 'Existing /etc/msmtprc was not created by this installer.'
    yesno 'Back it up and replace it?' || return 1
  fi
  yesno 'Save SMTP configuration?' || return 1
  run_step 'Install msmtp' apt_retry apt-get -o DPkg::Lock::Timeout=180 install -y msmtp msmtp-mta ca-certificates || return 1
  install -d -m 700 "$MAINT_DIR"
  if [[ -f /etc/msmtprc ]]; then
    cp -p /etc/msmtprc "$MAINT_DIR/msmtprc.before-maintenance.$(date +%Y%m%d%H%M%S)" || return 1
  fi
  tmp="$(mktemp "$MAINT_DIR/.smtp-password.XXXXXX")" || return 1
  printf '%s\n' "$password" > "$tmp"; chmod 600 "$tmp"
  mv -f "$tmp" "$MAINT_DIR/smtp-password"
  tmp="$(mktemp /etc/.msmtprc.XXXXXX)" || return 1
  cat > "$tmp" <<EOF
# Managed by VPS Installer
defaults
auth on
tls on
tls_trust_file /etc/ssl/certs/ca-certificates.crt
syslog LOG_MAIL
account default
host $host
port $port
tls_starttls $encryption
from $sender
user $sender
passwordeval cat $MAINT_DIR/smtp-password
EOF
  chmod 600 "$tmp"; mv -f "$tmp" /etc/msmtprc
  MAINT_SENDER="$sender" MAINT_RECIPIENT="$recipient" MAINT_NOTIFY=yes
  maint_save || return 1
  ok 'SMTP configuration saved. Send a test message to verify delivery.'
}
maint_smtp_addresses() {
  maint_load
  say '1) Change sender / SMTP username'; say '2) Change recipient'; say '0) Exit'
  ask_choice 'Choose option:' '1:Sender' '2:Recipient' '0:Exit' || return
  case "$REPLY" in
    1)
      [[ -f /etc/msmtprc ]] && grep -Fxq '# Managed by VPS Installer' /etc/msmtprc || { warn 'No managed SMTP configuration.'; return 1; }
      ask 'New sender / username:'
      maint_valid_email "$REPLY" || { warn 'Invalid email.'; return 1; }
      local sender="$REPLY" tmp
      tmp="$(mktemp /etc/.msmtprc.XXXXXX)" || return 1
      sed -E "s/^from .*/from $sender/; s/^user .*/user $sender/" /etc/msmtprc > "$tmp"
      chmod 600 "$tmp"; mv "$tmp" /etc/msmtprc
      MAINT_SENDER="$sender"; maint_save
      ;;
    2)
      ask 'New recipient:'
      maint_valid_email "$REPLY" || { warn 'Invalid email.'; return 1; }
      MAINT_RECIPIENT="$REPLY"; maint_save
      ;;
    0) return ;;
  esac
}
maint_smtp_password() {
  [[ -f /etc/msmtprc ]] && grep -Fxq '# Managed by VPS Installer' /etc/msmtprc || { warn 'No managed SMTP configuration.'; return 1; }
  local password tmp
  printf 'New SMTP password (hidden): ' >&4
  IFS= read -rs password <&3 || return 1
  printf '\n' >&4
  [[ -n "$password" ]] || return 1
  tmp="$(mktemp "$MAINT_DIR/.smtp-password.XXXXXX")" || return 1
  printf '%s\n' "$password" > "$tmp"; chmod 600 "$tmp"
  mv "$tmp" "$MAINT_DIR/smtp-password"
  ok 'SMTP password updated.'
}
maint_smtp_test() {
  maint_load
  if [[ -z "$MAINT_RECIPIENT" || -z "$MAINT_SENDER" ]]; then warn 'Configure SMTP first.'; return 1; fi
  if printf 'From: %s\nTo: %s\nSubject: [%s] Maintenance SMTP test\n\nTest from VPS Installer.\n' \
       "$MAINT_SENDER" "$MAINT_RECIPIENT" "$(hostname)" |
       /usr/sbin/sendmail -oi -f "$MAINT_SENDER" "$MAINT_RECIPIENT"; then
    ok 'SMTP accepted test message (delivery not guaranteed).'
  else warn 'SMTP submission failed.'; return 1; fi
}
maint_smtp_menu() {
  local choice
  while :; do
    maint_load
    say ''; say 'EMAIL NOTIFICATIONS (MSMTP)'
    say "Status: $MAINT_NOTIFY"
    if [[ -f /etc/msmtprc ]] && grep -Fxq '# Managed by VPS Installer' /etc/msmtprc; then
      say "SMTP host: $(awk '$1=="host"{print $2}' /etc/msmtprc)"
      say "SMTP port: $(awk '$1=="port"{print $2}' /etc/msmtprc)"
      say "Encryption: $(awk '$1=="tls_starttls"{print ($2=="on"?"STARTTLS":"TLS-on-connect")}' /etc/msmtprc)"
    fi
    say "Sender / Username: ${MAINT_SENDER:-Not configured}"
    say "Recipient: ${MAINT_RECIPIENT:-Not configured}"
    say '1) Configure SMTP'; say '2) Change sender / recipient'
    say '3) Change SMTP password'; say '4) Send test email'
    say '5) Enable / Disable notifications'; say '6) Remove SMTP configuration'; say '0) Exit'
    ask_choice 'Choose option:' '1:Configure' '2:Addresses' '3:Password' '4:Test' '5:Toggle' '6:Remove' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) maint_smtp_configure || warn 'SMTP configuration was not saved.' ;;
      2) maint_smtp_addresses ;;
      3) maint_smtp_password ;;
      4) maint_smtp_test ;;
      5) if [[ -z "$MAINT_SENDER" ]]; then warn 'Configure SMTP first.'; else
           [[ "$MAINT_NOTIFY" == yes ]] && MAINT_NOTIFY=no || MAINT_NOTIFY=yes
           maint_save && ok "Notifications: $MAINT_NOTIFY"
         fi ;;
      6) if yesno 'Remove installer-owned SMTP configuration and credentials?'; then
           if [[ -f /etc/msmtprc ]] && grep -Fxq '# Managed by VPS Installer' /etc/msmtprc; then rm -f /etc/msmtprc; fi
           rm -f "$MAINT_DIR/smtp-password"
           MAINT_SENDER='' MAINT_RECIPIENT='' MAINT_NOTIFY=no
           maint_save
         fi ;;
      0) return ;;
    esac
  done
}
maint_status_menu() {
  local choice
  while :; do
    maint_load
    say ''; say 'MAINTENANCE STATUS'
    maint_timer_status vps-maintenance-xray
    maint_timer_status awg-geoblock-update
    maint_timer_status apt-daily-upgrade
    maint_timer_status vps-maintenance-apt
    say "SMTP notifications: $MAINT_NOTIFY"
    say "Automatic reboot: $MAINT_AUTO_REBOOT"
    [[ -f /run/reboot-required ]] && say 'Reboot required: Yes' || say 'Reboot required: No'
    say '1) Refresh status'; say '2) View Xray update log'
    say '3) View AmneziaWG GeoBlock log'; say '4) View Ubuntu update log'
    say '5) Check maintenance services'; say '0) Exit'
    ask_choice 'Choose option:' '1:Refresh' '2:Xray log' '3:AWG log' '4:APT log' '5:Service check' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) : ;;
      2) maint_log vps-maintenance-xray ;;
      3) maint_log awg-geoblock-update ;;
      4) maint_log vps-maintenance-apt ;;
      5) for service in xray.service awg-quick@awg0.service awg-geoblock.service awg-dns.service ssh.service fail2ban.service; do
           local health
           health="$(systemctl is-active "$service" 2>/dev/null)" || [[ -n "$health" ]] || health=inactive
           say "$service: $health"
         done
         pause ;;
      0) return ;;
    esac
  done
}
automatic_maintenance_menu() {
  local choice
  while :; do
    maint_load
    say ''; say 'AUTOMATIC MAINTENANCE'
    local xray_enabled awg_enabled apt_enabled
    xray_enabled="$(systemctl is-enabled vps-maintenance-xray.timer 2>/dev/null)" || [[ -n "$xray_enabled" ]] || xray_enabled=Not-configured
    awg_enabled="$(systemctl is-enabled awg-geoblock-update.timer 2>/dev/null)" || [[ -n "$awg_enabled" ]] || awg_enabled=Not-installed
    apt_enabled="$(systemctl is-enabled vps-maintenance-apt.timer 2>/dev/null)" || [[ -n "$apt_enabled" ]] || apt_enabled=Not-configured
    local security_enabled
    security_enabled="$(systemctl is-enabled apt-daily-upgrade.timer 2>/dev/null)" || [[ -n "$security_enabled" ]] || security_enabled=Not-configured
    say "Xray GeoData: $xray_enabled"
    say "AmneziaWG GeoBlock: $awg_enabled"
    say "Ubuntu updates: security=$security_enabled, weekly=$apt_enabled"
    say "Email notifications: $MAINT_NOTIFY"
    say "Automatic reboot: $MAINT_AUTO_REBOOT"
    say '1) Xray GeoData updates'; say '2) AmneziaWG GeoBlock updates'
    say '3) Ubuntu system updates'; say '4) Email notifications (msmtp)'
    say '5) Maintenance status'; say '0) Exit'
    ask_choice 'Choose option:' '1:Xray' '2:AmneziaWG' '3:Ubuntu' '4:Email' '5:Status' '0:Exit' || continue
    choice="$REPLY"
    case "$choice" in
      1) maint_xray_menu;; 2) maint_awg_menu;; 3) maint_ubuntu_menu;;
      4) maint_smtp_menu;; 5) maint_status_menu;; 0) return;;
    esac
  done
}
