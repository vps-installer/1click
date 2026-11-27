#!/usr/bin/env bash
# Xray and AmneziaWG installation. No predefined client credentials.
xray_config_is_managed() {
  PYTHONPATH="$ROOT/lib" python3 - <<'PY'
import json
from profile_manager import xray_inbounds
try:
    with open("/usr/local/etc/xray/config.json") as stream:
        xray_inbounds(json.load(stream))
except (OSError, ValueError, RuntimeError, KeyError, TypeError):
    raise SystemExit(1)
PY
}

xray_already_present() {
  # Fail closed if Xray was installed by any method, even without state markers.
  command -v xray >/dev/null 2>&1 ||
    [[ -e /usr/local/etc/xray/config.json || -e /etc/xray/config.json ||
       -e /usr/local/bin/xray || -e /usr/bin/xray ]] ||
    systemctl list-unit-files xray.service --no-legend 2>/dev/null | grep -q '^xray.service'
}
awg_already_present() {
  [[ -e /etc/amnezia/amneziawg/awg0.conf || -e /etc/wireguard/awg0.conf ]] ||
    systemctl is-active --quiet 'awg-quick@awg0.service' ||
    systemctl is-enabled --quiet 'awg-quick@awg0.service'
}
install_xray() {
  phase "Installing Xray VLESS + XHTTP + REALITY"
  local config_path=/usr/local/etc/xray/config.json
  if xray_already_present; then
    warn "Xray is already installed or its configuration exists."
    say "Full installation requires a clean VPS. No Xray files, keys or profiles were changed."
    return 1
  fi
  # Choose REALITY destination only for a new deployment, never on existing Xray.
  # A rejected hostname is recoverable input, not an installation failure.
  local reality_sni reality_names sni_mode
  while :; do
    say ""
    say "Xray REALITY configuration"
    say "1) Default SNI (open.spotify.com)"
    say "2) Custom SNI"
    say "0) Exit"
    if ! ask_default_choice "Choose SNI mode" 1 "1:Default SNI" "2:Custom SNI" "0:Exit"; then
      warn "Choose 0, 1 or 2 and try again."
      continue
    fi
    sni_mode="$REPLY"
    [[ "$sni_mode" == 0 ]] && { say "Xray installation cancelled."; return 0; }
    if [[ "$sni_mode" == 1 ]]; then
      reality_sni="open.spotify.com"
      reality_names='["open.spotify.com","www.spotify.com"]'
    else
      ask "Custom SNI hostname (without https:// or port):"
      reality_sni="$REPLY"
      reality_names=""
    fi
    if python3 /opt/vps-installer/lib/install_options.py check-sni "$reality_sni"; then
      break
    fi
    warn "SNI '$reality_sni' is unsuitable or could not be verified."
    say "Enter another SNI or select the default option."
  done
  say "Selected REALITY SNI: $reality_sni"
  # The official installer is deliberately launched only after SNI validation.
  # Refuse to proceed if Xray appeared while the user was selecting the SNI.
  if xray_already_present; then
    warn "Xray appeared during SNI selection. Refusing to overwrite an existing installation."
    return 1
  fi
  run_step "Install official Xray" bash -c 'bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install' || die "Xray installation failed."
  # The official installer may create its own default config; this is the
  # only existing config file a new installation is allowed to replace.
  if [[ -e "$config_path" ]]; then
    cp -p -- "$config_path" "$config_path.before-vps-installer.$(date +%Y%m%d%H%M%S).bak" || die "Cannot back up Xray's installer-generated default configuration."
  fi
  install -d -m 700 /root/xray-secrets
  local keys short path ip
  keys="$(xray x25519)" || die "Unable to generate REALITY key pair."
  short="$(openssl rand -hex 8)"
  path="/$(openssl rand -hex 12)"
  ip="$(state_get PUBLIC_IPV4)"
  XRAY_KEY_TEXT="$keys" XRAY_SHORT="$short" XRAY_PATH="$path" XRAY_IP="$ip" XRAY_SNI="$reality_sni" XRAY_NAMES="$reality_names" python3 - <<'PY'
import json,os,re,grp
from pathlib import Path
raw=os.environ["XRAY_KEY_TEXT"]
m=re.search(r"(?im)^\s*Private(?:\s*key|Key)\s*:\s*([A-Za-z0-9_-]{40,})\s*$",raw)
if not m: raise SystemExit("Cannot parse xray x25519 private key")
config={
 "log":{"loglevel":"warning"},
 "dns":{"servers":["https+local://1.1.1.1/dns-query","https+local://8.8.8.8/dns-query"],"queryStrategy":"UseIPv4"},
 "inbounds":[{"tag":"vless-xhttp-reality","listen":os.environ["XRAY_IP"],"port":443,"protocol":"vless",
  "settings":{"clients":[],"decryption":"none"},
  "streamSettings":{"network":"xhttp","security":"reality",
   "realitySettings":{"show":False,"target":os.environ["XRAY_SNI"]+":443","xver":0,
     "serverNames":json.loads(os.environ["XRAY_NAMES"]) if os.environ["XRAY_NAMES"] else [os.environ["XRAY_SNI"]],"privateKey":m.group(1),"shortIds":[os.environ["XRAY_SHORT"]]},
   "xhttpSettings":{"path":os.environ["XRAY_PATH"],"mode":"stream-one"}},
  "sniffing":{"enabled":True,"destOverride":["http","tls","quic"],"routeOnly":True}}],
 "outbounds":[{"tag":"direct","protocol":"freedom",
  "settings":{"finalRules":[{"action":"block","ip":["geoip:ru","geoip:private"]}]},
  "streamSettings":{"sockopt":{"domainStrategy":"ForceIPv4"}}},
  {"tag":"block","protocol":"blackhole"}],
 "routing":{"domainStrategy":"IPIfNonMatch","rules":[
  {"type":"field","domain":["geosite:category-ru","domain:ru","domain:xn--p1ai","domain:su"],"outboundTag":"block"},
  {"type":"field","ip":["geoip:ru","geoip:private"],"outboundTag":"block"}]}
}
p=Path("/usr/local/etc/xray/config.json")
p.parent.mkdir(parents=True,exist_ok=True)
tmp=p.with_name(".installer-pending-config.json")
with tmp.open("x") as f: json.dump(config,f,indent=2); f.write("\n")
os.chown(tmp,0,grp.getgrnam("xray").gr_gid); tmp.chmod(0o640)
PY
  if ! run_step "Validate generated Xray configuration" runuser -u xray -- xray run -test -config /usr/local/etc/xray/.installer-pending-config.json; then
    rm -f /usr/local/etc/xray/.installer-pending-config.json
    die "Generated Xray config failed validation. Existing config.json remains untouched."
  fi
  mv -f -- /usr/local/etc/xray/.installer-pending-config.json /usr/local/etc/xray/config.json || die "Cannot activate Xray configuration."
  ufw allow 443/tcp >/dev/null || die "Cannot open TCP 443."
  systemctl enable --now xray >/dev/null || die "Xray failed to start."
  systemctl restart xray || die "Xray restart failed."
  systemctl is-active --quiet xray || die "Xray is inactive."
  state_set XRAY yes
}
install_amnezia() {
  local existing_awg=0 awg_port iface
  if [[ -s /etc/amnezia/amneziawg/awg0.conf ]]; then
    # Continue only the partial deployment created by this installer;
    # never regenerate private keys or overwrite existing client peers.
    if [[ ! -s /etc/awg-geoblock/awg-geoblock.nft ||
          ! -s /etc/awg-geoblock/ru4.zone ||
          ! -f /etc/awg-geoblock/awg-geoblock.nft ]]; then
      warn "An AmneziaWG configuration already exists, but its origin cannot be verified."
      return 1
    fi
    if [[ "$(state_get AWG)" == yes ]] &&
       systemctl is-active --quiet 'awg-quick@awg0.service' &&
       systemctl is-active --quiet 'awg-geoblock.service' &&
       systemctl is-active --quiet 'awg-dns.service'; then
      ok "AmneziaWG is already installed and active; configuration preserved."
      return 0
    fi
    existing_awg=1
    awg_port="$(sed -nE 's/^[[:space:]]*ListenPort[[:space:]]*=[[:space:]]*([0-9]+).*/\1/p' /etc/amnezia/amneziawg/awg0.conf | head -n 1)"
    python3 /opt/vps-installer/lib/install_options.py check-port "$awg_port" || {
      warn "Existing AmneziaWG port is invalid."
      return 1
    }
    say "Continuing incomplete AmneziaWG installation on UDP $awg_port."
    say "Existing server keys and client profiles will be preserved."
  elif awg_already_present; then
    warn "AmneziaWG installation exists outside this installer; refusing to overwrite it."
    return 1
  fi
  if (( existing_awg == 0 )); then
  local random_port
  random_port="$(python3 "$ROOT/lib/install_options.py" random-port)" || {
    warn "Unable to generate an available random UDP port."
    return 1
  }
  while :; do
    say ""
    say "AmneziaWG configuration"
    say "1) Random UDP port ($random_port)"
    say "2) Custom UDP port"
    say "0) Exit"
    ask_default "Choose UDP port option" "$random_port"
    case "$REPLY" in
      0)
        say "AmneziaWG installation cancelled."
        return 0
        ;;
      1|"$random_port")
        awg_port="$random_port"
        ;;
      2)
        ask "Enter AmneziaWG UDP port:"
        awg_port="$REPLY"
        ;;
      *)
        warn "Invalid choice. Choose 1, 2 or 0, or press Enter for the suggested port."
        continue
        ;;
    esac
    if ! python3 /opt/vps-installer/lib/install_options.py check-port "$awg_port"; then
      warn "Invalid AmneziaWG UDP port (1-65535). Please try again."
      continue
    fi
    # Refuse ports occupied by another service, but allow another choice.
    local udp_sockets
    udp_sockets="$(ss -H -lun)" || {
      warn "Cannot inspect local UDP ports. Try again."
      continue
    }
    if awk '{print $4}' <<<"$udp_sockets" | grep -Eq "(^|\\]:|:)$awg_port$"; then
      warn "UDP port $awg_port is already in use. Choose another port."
      continue
    fi
    say "Selected AmneziaWG UDP port: $awg_port"
    break
  done
  fi  # new AmneziaWG installation options
  iface="$(state_get WAN_IF)"
  [[ -n "$iface" ]] || die "WAN interface missing from installer settings."
  run_step "Install Amnezia PPA" apt_retry add-apt-repository -y -n ppa:amnezia/ppa || die "Amnezia PPA failed."
  run_step "Refresh APT" apt_retry apt-get -o DPkg::Lock::Timeout=10 update || die "APT failed."
  run_step "Install AmneziaWG and DNS" apt_retry apt-get -o DPkg::Lock::Timeout=10 install -y amneziawg amneziawg-dkms amneziawg-tools "linux-headers-$(uname -r)" dkms build-essential dnsmasq-base unbound || die "AmneziaWG packages failed."
  modprobe amneziawg || die "AWG kernel module not available."
  install -d -m 700 /etc/amnezia/amneziawg /etc/awg-geoblock /root/awg-clients
  install -d -m 755 /etc/awg-dns
  if (( existing_awg == 0 )); then
  local private hpk
  private="$(awg genkey)"; hpk="$(awg genkey)"
  AWG_PRIVATE="$private" AWG_HPK="$hpk" AWG_PORT="$awg_port" python3 - <<'PY'
import os,sys
from pathlib import Path
sys.path.insert(0, "/opt/vps-installer/lib")
from install_options import generate_awg_obfuscation
p=Path("/etc/amnezia/amneziawg/awg0.conf")
values=generate_awg_obfuscation()
lines=["[Interface]",f"PrivateKey = {os.environ['AWG_PRIVATE']}",
"Address = 10.88.32.1/24",f"ListenPort = {os.environ['AWG_PORT']}","MTU = 1280"]
lines += [f"{key} = {value}" for key,value in values.items()]
lines += [f"HeaderProtectionKey = {os.environ['AWG_HPK']}"]
with p.open("x") as f: f.write("\n".join(lines)+"\n")
p.chmod(0o600)
PY
  fi
  [[ -s /etc/amnezia/amneziawg/awg0.conf ]] || die "AmneziaWG server configuration was not created."
  cat >/etc/sysctl.d/90-awg-forward.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=0
EOF
  sysctl --system >>"$LOG_FILE" 2>&1 || die "IP forwarding setup failed."
  ufw allow "$awg_port/udp" >/dev/null || die "Cannot open AWG UDP port."
  run_step "Fetch Russian IPv4 network list" curl -fsSL --retry 3 https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone -o /etc/awg-geoblock/ru4.zone || die "RU IP list unavailable; refuse AWG startup."
  chmod 600 /etc/awg-geoblock/ru4.zone
  AWG_WAN_IF="$iface" python3 - <<'PY'
from pathlib import Path
import os,ipaddress
base=Path("/etc/awg-geoblock")
nets=[ipaddress.IPv4Network(x) for x in (base/"ru4.zone").read_text().split() if x]
if not 5000<=len(nets)<=20000 or any(x.prefixlen<8 for x in nets): raise SystemExit("Invalid RU IPv4 list")
merged=",\n".join(map(str,ipaddress.collapse_addresses(nets)))
wan=os.environ["AWG_WAN_IF"]
blocked="0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.168.0.0/16, 198.18.0.0/15, 224.0.0.0/3"
script=f'''table inet awg_geoblock {{
 set ru4 {{ type ipv4_addr; flags interval; auto-merge; elements = {{ {merged} }} }}
 set blocked4 {{ type ipv4_addr; flags interval; auto-merge; elements = {{ {blocked} }} }}
 chain forward {{
 type filter hook forward priority -150; policy accept;
 iifname "awg0" meta nfproto ipv6 counter drop
 iifname "awg0" oifname != "{wan}" counter drop
 iifname "awg0" ip daddr @blocked4 counter drop
 iifname "awg0" ip daddr @ru4 counter drop
 iifname "awg0" udp dport 53 counter drop
 iifname "awg0" tcp dport 53 counter drop
 iifname "awg0" oifname "{wan}" ip saddr 10.88.32.0/24 meta mark set 0xa609
 }}
}}
'''
p=base/"awg-geoblock.nft"; p.write_text(script); p.chmod(0o600)
PY
  AWG_WAN_IF="$iface" python3 - <<'PY'
from pathlib import Path
import os
p=Path("/etc/ufw/before.rules"); s=p.read_text()
iface=os.environ["AWG_WAN_IF"]
nat=f"-A POSTROUTING -s 10.88.32.0/24 -o {iface} -j MASQUERADE"
if nat not in s: s="*nat\n:POSTROUTING ACCEPT [0:0]\n"+nat+"\nCOMMIT\n\n"+s
anchor="-A ufw-before-forward -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
rules=f"-A ufw-before-forward -s 10.88.32.0/24 -i awg0 -o {iface} -m mark --mark 0xa609 -j ACCEPT\n-A ufw-before-forward -i awg0 -j DROP\n"
if rules not in s:
 if "-A ufw-before-forward -i awg0 -j DROP" in s: raise SystemExit("Conflicting AWG rules already present")
 if s.count(anchor)!=1: raise SystemExit("Missing ufw-before-forward anchor")
 s=s.replace(anchor,rules+anchor,1)
p.write_text(s)
PY
  ufw --force enable >/dev/null || die "Cannot enable UFW."
  ufw reload >/dev/null || die "UFW cannot load AWG rules."
  iptables -C ufw-before-forward -s 10.88.32.0/24 -i awg0 -o "$iface" -m mark --mark 0xa609 -j ACCEPT || die "AWG marked-traffic rule missing."
  iptables -C ufw-before-forward -i awg0 -j DROP || die "AWG fail-closed rule missing."
  # Never start the tunnel if its firewall protection cannot be verified.
  if nft list table inet awg_geoblock >/dev/null 2>&1; then
    say "Existing GeoBlock table detected; validating in-place without interrupting rules."
  else
    nft -c -f /etc/awg-geoblock/awg-geoblock.nft || die "GeoBlock syntax invalid."
    nft -f /etc/awg-geoblock/awg-geoblock.nft || die "GeoBlock load failed."
  fi
  # Capture the complete nft output first: with pipefail, nft | grep -q can
  # falsely fail if grep exits before nft finishes writing (SIGPIPE).
  local geoblock_chain
  geoblock_chain="$(nft list chain inet awg_geoblock forward)" || die "Cannot read loaded GeoBlock chain."
  if ! grep -Fq 'meta nfproto ipv6' <<< "$geoblock_chain" ||
     ! grep -Fq 'ip daddr @ru4' <<< "$geoblock_chain" ||
     ! grep -Fq 'ip daddr @blocked4' <<< "$geoblock_chain" ||
     ! grep -Fq 'udp dport 53' <<< "$geoblock_chain" ||
     ! grep -Fq 'tcp dport 53' <<< "$geoblock_chain" ||
     ! grep -Eq 'meta mark set (0x0*a609|42505)([[:space:]]|$)' <<< "$geoblock_chain"; then
    warn "Loaded GeoBlock chain does not match the required fail-closed policy."
    printf '%s\\n' "$geoblock_chain" >> "$LOG_FILE"
    die "GeoBlock integrity failed. Loaded nft rules saved to $LOG_FILE."
  fi
  ok "GeoBlock IPv6/RU/private/DNS blocking and traffic marking verified."
  cat >/etc/systemd/system/awg-geoblock.service <<'EOF'
[Unit]
Description=AmneziaWG geographic firewall
Requires=ufw.service
After=ufw.service
Before=awg-quick@awg0.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/awg-geoblock-check.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
  cat >/usr/local/sbin/awg-geoblock-check.sh <<'EOF'
#!/bin/bash
set -euo pipefail
CONFIG=/etc/awg-geoblock/awg-geoblock.nft
if ! nft list table inet awg_geoblock >/dev/null 2>&1; then
  nft -c -f "$CONFIG"
  nft -f "$CONFIG"
fi
nft list set inet awg_geoblock ru4 >/dev/null
nft list set inet awg_geoblock blocked4 >/dev/null
chain="$(nft list chain inet awg_geoblock forward)"
grep -Fq 'meta nfproto ipv6' <<<"$chain"
grep -Fq 'ip daddr @ru4' <<<"$chain"
grep -Fq 'ip daddr @blocked4' <<<"$chain"
grep -Fq 'udp dport 53' <<<"$chain"
grep -Fq 'tcp dport 53' <<<"$chain"
grep -Eq 'meta mark set (0x0*a609|42505)([[:space:]]|$)' <<<"$chain"
iface="$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++)if($i=="dev"){print $(i+1);exit}}')"
[[ -n "$iface" ]]
iptables -C ufw-before-forward -s 10.88.32.0/24 -i awg0 -o "$iface" -m mark --mark 0xa609 -j ACCEPT
iptables -C ufw-before-forward -i awg0 -j DROP
EOF
  chmod 700 /usr/local/sbin/awg-geoblock-check.sh
  mkdir -p /etc/systemd/system/awg-quick@awg0.service.d
  cat >/etc/systemd/system/awg-quick@awg0.service.d/10-geoblock.conf <<'EOF'
[Unit]
Requires=awg-geoblock.service
After=awg-geoblock.service
EOF
  cat >/etc/awg-dns/dnsmasq.conf <<'EOF'
port=53
listen-address=10.88.32.1
bind-interfaces
no-resolv
no-poll
domain-needed
bogus-priv
stop-dns-rebind
cache-size=1000
local=/ru/
local=/xn--p1ai/
local=/su/
server=127.0.0.1#5353
strict-order
EOF
  cat >/etc/unbound/unbound.conf.d/awg-forward.conf <<'EOF'
server:
    interface: 127.0.0.1
    port: 5353
    do-ip4: yes
    do-ip6: no
    do-udp: yes
    do-tcp: yes
    access-control: 127.0.0.0/8 allow
    tls-cert-bundle: "/etc/ssl/certs/ca-certificates.crt"
    hide-identity: yes
    hide-version: yes
forward-zone:
    name: "."
    forward-tls-upstream: yes
    forward-addr: 1.1.1.1@853#cloudflare-dns.com
    forward-addr: 8.8.8.8@853#dns.google
EOF
  cat >/etc/systemd/system/awg-dns.service <<'EOF'
[Unit]
Description=Filtered AmneziaWG DNS
Requires=awg-quick@awg0.service
After=awg-quick@awg0.service
[Service]
Type=simple
ExecStart=/usr/sbin/dnsmasq --no-daemon --conf-file=/etc/awg-dns/dnsmasq.conf
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
EOF
  install -m 700 /opt/vps-installer/lib/geoblock_update.py /usr/local/sbin/awg-geoblock-update.py
  cat >/etc/systemd/system/awg-geoblock-update.service <<'EOF'
[Unit]
Description=Update Russian IPv4 GeoBlock
Requires=awg-geoblock.service
After=network-online.target awg-geoblock.service
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/awg-geoblock-update.py
EOF
  cat >/etc/systemd/system/awg-geoblock-update.timer <<'EOF'
[Unit]
Description=Daily Russian IPv4 GeoBlock update
[Timer]
OnCalendar=*-*-* 04:20:00
RandomizedDelaySec=10m
Persistent=true
Unit=awg-geoblock-update.service
[Install]
WantedBy=timers.target
EOF
  ufw allow in on awg0 from 10.88.32.0/24 to 10.88.32.1 port 53 proto udp >/dev/null || die "Cannot allow filtered DNS."
  ufw allow in on awg0 from 10.88.32.0/24 to 10.88.32.1 port 53 proto tcp >/dev/null || die "Cannot allow filtered DNS."
  systemctl daemon-reload
  systemctl enable --now awg-geoblock.service || die "GeoBlock service failed."
  systemctl enable --now 'awg-quick@awg0.service' || die "AWG service failed."
  systemctl enable --now unbound.service || die "Unbound failed."
  systemctl restart unbound.service || die "Unbound failed."
  systemctl enable --now awg-dns.service || die "AWG DNS failed."
  systemctl enable --now awg-geoblock-update.timer || die "GeoBlock update timer failed."
  dig @10.88.32.1 google.ru A +noall +comments | grep -q NXDOMAIN || die "RU DNS block test failed."
  dig @10.88.32.1 google.com A +short | grep -Eq '^[0-9]+\.' || die "DNS resolution test failed."
  state_set AWG yes
  ok "AmneziaWG and fail-closed filtering installed."
}

discover_ipv6_endpoint() {
  local current
  current="$(python3 "$ROOT/lib/ipv6_support.py" detect 2>>"$LOG_FILE")" || current=""
  if [[ -z "$current" ]]; then
    warn "No routable public IPv6 address and route were detected on this VPS."
    return 1
  fi
  state_set PUBLIC_IPV6 "$current"
  say "Detected IPv6 endpoint: $current"
}

verify_ipv6_port() {
  local port="$1" protocol="$2" status
  [[ "$(grep -E '^IPV6=' /etc/default/ufw 2>/dev/null)" == "IPV6=yes" ]] || {
    warn "UFW IPv6 is disabled; refusing to publish an IPv6 endpoint."
    return 1
  }
  ufw allow "$port/$protocol" >/dev/null || return 1
  status="$(ufw status)" || return 1
  grep -Eq "^${port}/${protocol}.*ALLOW.*\(v6\)" <<<"$status" || {
    warn "UFW IPv6 allowance for $port/$protocol not verified."
    return 1
  }
}

offer_xray_ipv6() {
  if ! discover_ipv6_endpoint; then return 0; fi
  if ! yesno "Enable IPv6 incoming connections for Xray (outgoing traffic stays IPv4)?"; then
    say "Xray IPv6 settings were left unchanged."
    return 0
  fi
  verify_ipv6_port 443 tcp || return 1
  if ! python3 "$ROOT/lib/ipv6_support.py" enable-xray; then
    warn "Xray IPv6 activation failed. Existing IPv4 configuration preserved/restored."
    return 1
  fi
  state_set XRAY_IPV6 yes
  ok "Xray IPv6 inbound enabled; outbound IPv4-only policy unchanged."
}

offer_awg_ipv6() {
  if ! discover_ipv6_endpoint; then return 0; fi
  if ! yesno "Enable IPv6 incoming connections for AmneziaWG (IPv6 tunnel traffic stays blocked)?"; then
    say "AmneziaWG IPv6 settings were left unchanged."
    return 0
  fi
  [[ "$(sysctl -n net.ipv6.conf.all.forwarding)" == 0 ]] || {
    warn "IPv6 forwarding is enabled; refusing to claim safe IPv4-only tunnel mode."
    return 1
  }
  local port live_port listeners chain
  port="$(sed -nE 's/^[[:space:]]*ListenPort[[:space:]]*=[[:space:]]*([0-9]+).*/\1/p' /etc/amnezia/amneziawg/awg0.conf | head -n 1)"
  valid_port "$port" || { warn "Invalid saved AWG listen port."; return 1; }
  systemctl is-active --quiet 'awg-quick@awg0.service' || {
    warn "AmneziaWG must be running before enabling IPv6 ingress."
    return 1
  }
  ip link show dev awg0 | grep -Eq '<[^>]*UP' || {
    warn "AmneziaWG interface awg0 is down."
    return 1
  }
  live_port="$(awg show awg0 listen-port)" || {
    warn "Cannot read the active AmneziaWG listening port."
    return 1
  }
  if [[ "$live_port" != "$port" ]]; then
    warn "AmneziaWG active UDP port ($live_port) differs from its saved configuration ($port)."
    return 1
  fi
  [[ "$(sysctl -n net.ipv6.conf.all.disable_ipv6)" == 0 ]] || {
    warn "IPv6 is disabled in the kernel; cannot enable IPv6 ingress."
    return 1
  }
  chain="$(nft list chain inet awg_geoblock forward)" || return 1
  grep -Fq 'meta nfproto ipv6' <<< "$chain" || {
    warn "AWG IPv6 tunnel drop is missing; refusing IPv6 ingress."
    return 1
  }
  verify_ipv6_port "$port" udp || return 1
  listeners="$(ss -H -lun6 2>/dev/null)" || listeners=""
  if ! awk '{print $5}' <<<"$listeners" | grep -Eq ":${port}$"; then
    warn "IPv6 UDP/$port is not visible in ss; kernel AWG port and firewall checks passed."
    say "An external IPv6 AmneziaWG handshake is required to confirm connectivity."
  fi
  state_set AWG_IPV6 yes
  ok "AmneziaWG IPv6 endpoint configured; tunnel forwarding remains IPv4-only."
  say "Verify the IPv6 endpoint with an external client before relying on it."
}

vpn_installation_preflight() {
  require_ubuntu
  if [[ "$(state_get INSTALL_COMPLETE)" != yes ||
        "$(state_get BASE_CONFIGURED)" != yes ]]; then
    warn "Run 'VPS defence and customization' first to finish SSH/UFW/Fail2ban."
    return 1
  fi
  local ip iface
  ip="$(state_get PUBLIC_IPV4)"
  iface="$(state_get WAN_IF)"
  [[ -n "$ip" && -n "$iface" ]] || {
    warn "Missing saved VPS network settings."
    return 1
  }
}

xray_installation() {
  vpn_installation_preflight || return 1
  phase "Xray installation"
  if xray_already_present; then
    if xray_config_is_managed && systemctl is-active --quiet xray; then
      ok "Xray is already installed and active. Configuration and keys preserved."
      offer_xray_ipv6 || return 1
    else
      warn "Xray already exists but is incomplete or unrecognized."
      say "Existing keys and configuration were not overwritten."
    fi
    return 0
  fi
  install_xray || return 1
  offer_xray_ipv6 || return 1
  say "Xray installation finished. Returning to the main menu."
}

amnezia_installation() {
  vpn_installation_preflight || return 1
  phase "AmneziaWG installation"
  if awg_already_present; then
    if [[ -s /etc/amnezia/amneziawg/awg0.conf ]] &&
       [[ -s /etc/awg-geoblock/awg-geoblock.nft ]]; then
      if systemctl is-active --quiet 'awg-quick@awg0.service' &&
         systemctl is-active --quiet 'awg-geoblock.service' &&
         systemctl is-active --quiet 'awg-dns.service'; then
        ok "AmneziaWG already active; server configuration and all client keys preserved."
      else
        if ! yesno "AmneziaWG is incomplete. Continue its installation?"; then
          say "AmneziaWG was left unchanged."
          return 0
        fi
        install_amnezia || return 1
      fi
    else
      warn "Unknown AmneziaWG installation detected; preserving it."
      return 0
    fi
  else
    install_amnezia || return 1
  fi
  offer_awg_ipv6 || return 1
  say "AmneziaWG installation finished. Returning to the main menu."
}
