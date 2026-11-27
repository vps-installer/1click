#!/usr/bin/env python3
"""IPv6 ingress for the existing IPv4-only egress VPS configuration.

No IPv6 forwarding, DNS, geoblock, or client credentials are changed here.
"""
import copy
import ipaddress
import json
import os
import socket
import time
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

XRAY_PATH = Path("/usr/local/etc/xray/config.json")
STATE_PATH = Path("/etc/vps-installer/settings.json")


def run(*args):
    process = subprocess.run(args, text=True, capture_output=True)
    if process.returncode:
        raise RuntimeError(f"{args[0]} returned {process.returncode}: {process.stderr[:250]}")
    return process.stdout


def global_ipv6(ip):
    try:
        addr = ipaddress.IPv6Address(ip)
        return addr.is_global and not addr.is_link_local and not addr.is_multicast
    except ValueError:
        return False


def detect_ipv6():
    """Only accept a routable, configured and non-tentative interface address."""
    routes = json.loads(run("ip", "-6", "-j", "route", "get", "2606:4700:4700::1111"))
    for route in routes:
        device = route.get("dev", "")
        address = route.get("prefsrc") or route.get("src", "")
        if not (device and global_ipv6(address)):
            continue
        interfaces = json.loads(run("ip", "-6", "-j", "addr", "show", "dev", device))
        for interface in interfaces:
            for info in interface.get("addr_info", []):
                if info.get("local") != address or info.get("scope") != "global":
                    continue
                flags = set(info.get("flags", []))
                if flags & {"tentative", "dadfailed", "deprecated", "temporary"}:
                    continue
                return address
    return ""


def ipv6_assigned(address):
    if not global_ipv6(address):
        return False
    try:
        interfaces = json.loads(run("ip", "-6", "-j", "addr", "show"))
    except (OSError, RuntimeError, ValueError):
        return False
    return any(item.get("local") == address and item.get("scope") == "global"
               and not (set(item.get("flags", [])) & {"tentative", "dadfailed", "deprecated"})
               for iface in interfaces for item in iface.get("addr_info", []))


def managed_pair(conf):
    """Recognize the original IPv4 inbound and optional exact-tag IPv6 mirror."""
    matching = [i for i in conf.get("inbounds", [])
                if i.get("protocol") == "vless" and str(i.get("port")) == "443"
                and i.get("streamSettings", {}).get("network") == "xhttp"
                and i.get("streamSettings", {}).get("security") == "reality"]
    original = [i for i in matching if not global_ipv6(i.get("listen", ""))]
    if len(original) != 1:
        raise RuntimeError("Expected exactly one existing IPv4 VLESS/XHTTP/REALITY inbound.")
    primary = original[0]
    tag = primary.get("tag", "")
    if not tag:
        raise RuntimeError("Original inbound has no tag; refusing to attach IPv6.")
    mirror_tag = tag + "-ipv6"
    mirror = [i for i in matching if i.get("tag") == mirror_tag]
    if len(mirror) > 1 or len(matching) != 1 + len(mirror):
        raise RuntimeError("Other VLESS/XHTTP/REALITY inbounds detected; refusing ambiguous migration.")
    if mirror:
        peer = mirror[0]
        if not global_ipv6(peer.get("listen", "")):
            raise RuntimeError("The managed IPv6 inbound does not listen on global IPv6.")
        expected = copy.deepcopy(primary)
        expected["tag"] = mirror_tag
        expected["listen"] = peer["listen"]
        expected["streamSettings"].setdefault("sockopt", {})["v6only"] = True
        if expected != peer:
            raise RuntimeError("IPv6 mirror differs from IPv4 inbound; refusing unsafe change.")
    return primary, mirror[0] if mirror else None


def add_ipv6_mirror(conf, address):
    if not global_ipv6(address):
        raise ValueError("A globally routable IPv6 address is required.")
    primary, mirror = managed_pair(conf)
    if mirror and mirror["listen"] != address:
        raise RuntimeError("Existing IPv6 inbound uses a different address; not replacing it.")
    if not mirror:
        try:
            original_ip = ipaddress.IPv4Address(primary["listen"])
        except (ValueError, KeyError) as exc:
            raise RuntimeError("Original inbound must bind a specific IPv4 address; wildcard listeners are unsafe for a second IPv6 socket.") from exc
        if original_ip.is_unspecified:
            raise RuntimeError("A wildcard IPv4 listener may already include IPv6; refusing conflicting bind.")
    # Keep incoming IPv6 connectivity while preventing proxied IPv6 destinations.
    rules = conf.get("routing", {}).get("rules")
    if not isinstance(rules, list) or not any(
            x.get("protocol") == "blackhole" and x.get("tag") == "block"
            for x in conf.get("outbounds", [])):
        raise RuntimeError("Managed Xray blackhole routing is missing; refusing IPv6 migration.")
    blocked = any(r.get("outboundTag") == "block" and "::/0" in r.get("ip", [])
                  for r in rules)
    if not blocked:
        rules.insert(0, {"type": "field", "ip": ["::/0"], "outboundTag": "block"})
    if mirror:
        return not blocked
    duplicate = copy.deepcopy(primary)
    duplicate["tag"] = primary["tag"] + "-ipv6"
    duplicate["listen"] = address
    duplicate["streamSettings"].setdefault("sockopt", {})["v6only"] = True
    conf["inbounds"].append(duplicate)
    return True



def wait_for_ipv6_listener(address, port, retries=12, interval=0.4):
    """Probe the real IPv6 TCP endpoint instead of matching ss text layout.

    Socket rendering differs across iproute2 builds and an active systemd
    service does not guarantee its listener has finished starting yet.
    """
    if not global_ipv6(address):
        raise RuntimeError("The Xray listener address is not a global IPv6 address.")
    failure = "no successful TCP connection"
    for attempt in range(retries):
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as conn:
                conn.settimeout(0.7)
                conn.connect((address, port, 0, 0))
            return
        except (OSError, TimeoutError) as exc:
            failure = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < retries:
            time.sleep(interval)
    # Keep diagnostics free from keys, UUIDs, client URLs or config dumps.
    raise RuntimeError(
        f"Xray IPv6 TCP/{port} connection to [{address}] failed after "
        f"{retries} attempts ({failure}). The original config will be restored. "
        "Check 'ss -ltnp', 'journalctl -u xray' and VPS provider IPv6 settings."
    )


def enable_xray(address, config_path=XRAY_PATH):
    if not ipv6_assigned(address):
        raise RuntimeError("The selected IPv6 address is not assigned to a server interface.")
    raw = config_path.read_bytes()
    conf = json.loads(raw)
    if not add_ipv6_mirror(conf, address):
        print(f"Xray IPv6 inbound already configured: {address}")
        return
    original = config_path.stat()
    backup = config_path.with_name(config_path.name + ".before-ipv6-" +
                                   datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + ".bak")
    fd, staged_name = tempfile.mkstemp(prefix=".xray-ipv6-", suffix=".json", dir=config_path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(conf, indent=2, ensure_ascii=False) + "\n").encode())
        os.chown(staged, original.st_uid, original.st_gid)
        os.chmod(staged, original.st_mode & 0o777)
        run("xray", "run", "-test", "-config", str(staged))
        # Copy without following links and preserve exact original for rollback.
        with backup.open("xb") as stream:
            stream.write(raw)
        backup.chmod(0o600)
        os.replace(staged, config_path)
        try:
            run("systemctl", "restart", "xray")
            run("systemctl", "is-active", "--quiet", "xray")
            # Probe IPv6 TCP directly: ss may show a normalized or wildcard
            # address, and systemd may report active before the socket is ready.
            wait_for_ipv6_listener(address, 443)
        except Exception:
            recovery = config_path.with_name(".xray-ipv6-rollback.json")
            with recovery.open("wb") as stream:
                stream.write(raw)
            os.chown(recovery, original.st_uid, original.st_gid)
            os.chmod(recovery, original.st_mode & 0o777)
            os.replace(recovery, config_path)
            subprocess.run(["systemctl", "restart", "xray"], check=False)
            raise
    finally:
        staged.unlink(missing_ok=True)
    print(f"Xray IPv6 enabled on [{address}]:443; original backup: {backup}")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("detect", "enable-xray", "check-ipv6"):
        raise ValueError("Usage: ipv6_support.py detect|enable-xray|check-ipv6")
    action = sys.argv[1]
    if action == "detect":
        print(detect_ipv6())
    elif action == "check-ipv6":
        state = json.loads(STATE_PATH.read_text())
        address = state.get("PUBLIC_IPV6", "")
        if not address or not ipv6_assigned(address):
            raise RuntimeError("No verified public IPv6 address configured.")
        print(address)
    else:
        if os.geteuid() != 0:
            raise RuntimeError("Root is required.")
        state = json.loads(STATE_PATH.read_text())
        address = state.get("PUBLIC_IPV6", "")
        enable_xray(address)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
