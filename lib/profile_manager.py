#!/usr/bin/env python3
"""Manage AmneziaWG and Xray profiles without regenerating existing credentials."""
import argparse
import copy
import fcntl
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

os.umask(0o077)
XRAY = Path("/usr/local/etc/xray/config.json")
AWG = Path("/etc/amnezia/amneziawg/awg0.conf")
XRAY_FILES = Path("/root/xray-secrets")
AWG_FILES = Path("/root/awg-clients")
STATE = Path("/etc/vps-installer/settings.json")
CLIENT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


def command(*args, input=None, preserve_whitespace=False):
    proc = subprocess.run(args, input=input, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"{args[0]} failed (exit {proc.returncode}): {proc.stderr[:500]}")
    # Terminal QR images contain intentional blank edges. Stripping whitespace
    # would crop their quiet zone, especially across the top and left sides.
    return proc.stdout if preserve_whitespace else proc.stdout.strip()


def state():
    # A VPN installed before this manager may have no installer state file.
    try:
        return json.loads(STATE.read_text())
    except FileNotFoundError:
        return {}


def public_ipv4():
    """Use recorded public IPv4, or the host's public route source on legacy VPS.

    Never substitute a private/NAT address into client connection profiles.
    """
    recorded = state().get("PUBLIC_IPV4", "")
    if recorded:
        try:
            if ipaddress.IPv4Address(recorded).is_global:
                return recorded
        except ipaddress.AddressValueError:
            pass
    try:
        route = command("ip", "-4", "route", "get", "1.1.1.1")
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(
            "Cannot determine public IPv4: installer settings are missing or invalid, "
            "and IPv4 routing could not be checked."
        ) from exc
    found = re.search(r"(?:^|\s)src\s+(\d{1,3}(?:\.\d{1,3}){3})(?=\s|$)", route)
    if found:
        try:
            if ipaddress.IPv4Address(found.group(1)).is_global:
                return found.group(1)
        except ipaddress.AddressValueError:
            pass
    raise RuntimeError(
        "Cannot determine a public IPv4 for this existing VPS. "
        "The route source is not a public IPv4 (possibly NAT). "
        "No client was created. Configure /etc/vps-installer/settings.json "
        "with the correct PUBLIC_IPV4 before retrying."
    )


def atomic_json(path, obj, original):
    st = path.stat()
    fd, tmpname = tempfile.mkstemp(prefix=".xray-", suffix=".json", dir=path.parent)
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o777)
        command("xray", "run", "-test", "-config", str(tmp))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def xray_inbounds(conf):
    """Return original inbound and an optional matching IPv6 mirror."""
    matches = [
        i for i in conf.get("inbounds", [])
        if (str(i.get("port", "")) == "443"
            and i.get("protocol", "").lower() == "vless"
            and i.get("streamSettings", {}).get("security", "").lower() == "reality"
            and i.get("streamSettings", {}).get("network", "").lower() == "xhttp")
    ]
    if len(matches) == 1:
        return matches
    if len(matches) == 2:
        primary = next((i for i in matches
                        if not _is_ipv6(i.get("listen", ""))), None)
        if primary:
            mirror = next((i for i in matches if i is not primary), None)
            if (mirror and mirror.get("tag") == primary.get("tag", "") + "-ipv6"
                    and _is_ipv6(mirror.get("listen", ""))):
                expected = copy.deepcopy(primary)
                expected["tag"] = mirror["tag"]
                expected["listen"] = mirror["listen"]
                expected["streamSettings"].setdefault("sockopt", {})["v6only"] = True
                if expected == mirror:
                    return [primary, mirror]
                raise RuntimeError("Xray IPv4 and IPv6 configurations differ; refusing to alter clients.")
    safe = [
        {"tag": i.get("tag"), "port": i.get("port"), "protocol": i.get("protocol"),
         "network": i.get("streamSettings", {}).get("network"),
         "security": i.get("streamSettings", {}).get("security")}
        for i in conf.get("inbounds", [])
    ]
    raise RuntimeError(f"Expected one VLESS/XHTTP/REALITY inbound on port 443 (with optional IPv6 mirror); found {len(matches)}. Inbounds: {safe}")


def _is_ipv6(value):
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv6Address)
    except ValueError:
        return False


def xray_inbound(conf):
    return xray_inbounds(conf)[0]


def public_ipv6():
    recorded = state().get("PUBLIC_IPV6", "")
    if not _is_ipv6(recorded):
        raise RuntimeError("Public IPv6 is not configured. Enable IPv6 ingress in Xray/AmneziaWG first.")
    # Require the address to remain assigned and usable; do not generate
    # profiles for stale/deleted IPv6 addresses.
    try:
        from ipv6_support import ipv6_assigned
    except ImportError:
        # Tests and older local imports can use the installed standalone module.
        raise RuntimeError("IPv6 support module is missing. Refresh the installer.")
    if not ipv6_assigned(recorded):
        raise RuntimeError("Saved public IPv6 is no longer assigned to the VPS.")
    return recorded



def reality_client_key(output):
    """Recognize Xray x25519 public-key labels from multiple versions."""
    labels = {"publickey", "password", "password(publickey)"}
    for line in output.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = "".join(name.lower().split())
        if name in labels:
            key = value.strip()
            if re.fullmatch(r"[A-Za-z0-9_-]{8,86}", key):
                return key
            raise RuntimeError("Xray returned an invalid REALITY client key.")
    raise RuntimeError("Cannot find REALITY client key in xray x25519 output.")


def xray_link(conf, name, family="ipv4"):
    i = xray_inbound(conf)
    clients = [c for c in i["settings"]["clients"] if c.get("email") == name]
    if len(clients) != 1:
        raise RuntimeError(f"Xray client does not exist or is ambiguous: {name}")
    stream = i["streamSettings"]
    rs = stream["realitySettings"]
    private = rs["privateKey"]
    output = command("xray", "x25519", "-i", private)
    client_key = reality_client_key(output)
    if family == "ipv6":
        pair = xray_inbounds(conf)
        if len(pair) != 2:
            raise RuntimeError("Xray IPv6 inbound has not been enabled.")
        ip = public_ipv6()
        if pair[1]["listen"] != ip:
            raise RuntimeError("Xray IPv6 inbound address differs from the active IPv6 address.")
        endpoint = f"[{ip}]"
    elif family == "ipv4":
        ip = i.get("listen", "")
        if not ip or ip in ("0.0.0.0", "::"):
            ip = public_ipv4()
        endpoint = f"[{ip}]" if _is_ipv6(ip) else ip
    else:
        raise ValueError(f"Unexpected address family: {family}")
    xhttp = stream["xhttpSettings"]
    params = {"encryption": "none", "security": "reality", "sni": rs["serverNames"][0],
              "fp": "chrome", "pbk": client_key, "sid": rs["shortIds"][0],
              "type": "xhttp", "path": xhttp["path"], "mode": xhttp.get("mode", "stream-one")}
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    label = name if family == "ipv4" else name + "-IPv6"
    return f"vless://{clients[0]['id']}@{endpoint}:{i['port']}?{query}#{urllib.parse.quote(label)}"



def add_xray(name):
    if not XRAY.is_file():
        raise RuntimeError("Xray is not installed.")
    XRAY_FILES.mkdir(mode=0o700, exist_ok=True)
    with open("/run/vps-installer/xray-profiles.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old_bytes = XRAY.read_bytes()
        conf = json.loads(old_bytes)
        i = xray_inbound(conf)
        if any(c.get("email") == name for c in i["settings"]["clients"]):
            raise RuntimeError("Client already exists. Use the get operation.")
        uuid_file = XRAY_FILES / (name + ".uuid")
        if uuid_file.exists():
            raise RuntimeError("UUID file already exists; refusing to overwrite.")
        client_id = str(uuid.uuid4())
        for inbound in xray_inbounds(conf):
            inbound["settings"]["clients"].append({"id": client_id, "email": name})
        xray_link(conf, name)  # Validate all connection parameters before changing the server.
        backup = XRAY.with_name(XRAY.name + ".before-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + ".bak")
        with backup.open("xb") as f:
            f.write(old_bytes)
        backup.chmod(0o600)
        atomic_json(XRAY, conf, old_bytes)
        try:
            command("systemctl", "restart", "xray")
            command("systemctl", "is-active", "--quiet", "xray")
        except Exception:
            st = backup.stat()
            temp = XRAY.with_name(".xray-rollback")
            temp.write_bytes(old_bytes)
            os.chown(temp, 0, XRAY.stat().st_gid)
            os.chmod(temp, XRAY.stat().st_mode & 0o777)
            os.replace(temp, XRAY)
            subprocess.run(["systemctl", "restart", "xray"], check=False)
            raise RuntimeError("Xray restart failed. Configuration restored.")
        with uuid_file.open("x") as f:
            f.write(client_id + "\n")
        uuid_file.chmod(0o600)
        print(f"Xray profile created: {name} (backup: {backup.name})")


def awg_value(config, name):
    # Only read values from the Interface section.
    interface = config.split("[Peer]", 1)[0]
    results = re.findall(rf"(?mi)^\s*{re.escape(name)}\s*=\s*(.*?)\s*$", interface)
    if len(results) != 1:
        raise RuntimeError(f"Missing/ambiguous AWG interface option: {name}")
    return results[0]


def awg_endpoint(family="ipv4"):
    if family == "ipv6":
        if state().get("AWG_IPV6") != "yes":
            raise RuntimeError("AmneziaWG IPv6 ingress has not been enabled.")
        return "[" + public_ipv6() + "]"
    return public_ipv4()


def add_awg(name):
    if not AWG.is_file():
        raise RuntimeError("AmneziaWG is not installed.")
    AWG_FILES.mkdir(mode=0o700, exist_ok=True)
    with open("/run/vps-installer/awg-profiles.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for ext in (".conf", ".private", ".public"):
            if (AWG_FILES / (name + ext)).exists():
                raise RuntimeError("AWG profile already exists; use get operation.")
        server = AWG.read_text()
        if re.search(r"(?mi)^\s*SaveConfig\s*=\s*true\s*$", server):
            raise RuntimeError("SaveConfig=true not supported.")
        addresses = {ipaddress.IPv4Address("10.88.32.1")}
        for match in re.findall(r"(?mi)^\s*AllowedIPs\s*=\s*(.+)$", server):
            for item in match.split(","):
                try:
                    addresses.add(ipaddress.ip_network(item.strip(), strict=False).network_address)
                except ValueError:
                    pass
        live = command("awg", "show", "awg0", "allowed-ips")
        for line in live.splitlines():
            for item in line.split()[1:]:
                try:
                    addresses.add(ipaddress.ip_network(item.rstrip(","), strict=False).network_address)
                except ValueError:
                    pass
        address = next((ip for ip in ipaddress.IPv4Network("10.88.32.0/24").hosts() if ip not in addresses), None)
        if address is None:
            raise RuntimeError("No free AWG address.")
        private = command("awg", "genkey")
        public = command("awg", "pubkey", input=private + "\n")
        server_public = command("awg", "pubkey", input=awg_value(server, "PrivateKey") + "\n")
        options = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4",
                   "HeaderProtectionKey", "ContentPaddingAddition")
        obf = "\n".join(f"{option} = {awg_value(server, option)}" for option in options)
        conf = (f"[Interface]\nPrivateKey = {private}\nAddress = {address}/32\n"
                f"DNS = 10.88.32.1\nMTU = 1280\n{obf}\n\n"
                f"[Peer]\nPublicKey = {server_public}\n"
                f"Endpoint = {awg_endpoint()}:{awg_value(server,'ListenPort')}\n"
                "AllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n")
        command("awg", "set", "awg0", "peer", public, "allowed-ips", f"{address}/32")
        made = []
        try:
            for ext, content in ((".private", private + "\n"), (".public", public + "\n"), (".conf", conf)):
                p = AWG_FILES / (name + ext)
                with p.open("x") as f:
                    f.write(content)
                p.chmod(0o600)
                made.append(p)
            with AWG.open("a") as f:
                f.write(f"\n# {name}\n[Peer]\nPublicKey = {public}\nAllowedIPs = {address}/32\n")
        except Exception:
            subprocess.run(["awg", "set", "awg0", "peer", public, "remove"], check=False)
            for p in made:
                p.unlink(missing_ok=True)
            raise
        print(f"AWG profile created: {name}; assigned IP: {address}/32")


def connection_details(kind, name, family="ipv4"):
    """Show client connection settings without disclosing server private keys."""
    if kind == "xray":
        conf = json.loads(XRAY.read_text())
        link = xray_link(conf, name) if family == "ipv4" else xray_link(conf, name, family)
        parsed = urllib.parse.urlsplit(link)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        client_id = parsed.username
        fields = [
            ("Profile", name), ("Protocol", "VLESS"),
            ("Server", parsed.hostname), ("Port", parsed.port),
            ("UUID", client_id), ("Transport", query.get("type", "")),
            ("Security", query.get("security", "")), ("SNI", query.get("sni", "")),
            ("Fingerprint", query.get("fp", "")), ("REALITY public key", query.get("pbk", "")),
            ("Short ID", query.get("sid", "")), ("XHTTP path", query.get("path", "")),
            ("XHTTP mode", query.get("mode", "")),
        ]
    else:
        contents = awg_client_config(name, family)
        sections = {"Interface": {}, "Peer": {}}
        section = None
        for line in contents.splitlines():
            line = line.strip()
            if line in ("[Interface]", "[Peer]"):
                section = line[1:-1]
            elif section and "=" in line:
                key, value = line.split("=", 1)
                sections[section][key.strip()] = value.strip()
        interface, peer = sections["Interface"], sections["Peer"]
        fields = [
            ("Profile", name), ("Protocol", "AmneziaWG"),
            ("Endpoint", peer.get("Endpoint", "")),
            ("Client address", interface.get("Address", "")),
            ("DNS", interface.get("DNS", "")),
            ("Allowed IPs", peer.get("AllowedIPs", "")),
            ("MTU", interface.get("MTU", "")),
            ("Server public key", peer.get("PublicKey", "")),
            ("Keepalive", peer.get("PersistentKeepalive", "")),
            ("Obfuscation", "Jc=" + interface.get("Jc", "") +
             ", Jmin=" + interface.get("Jmin", "") +
             ", Jmax=" + interface.get("Jmax", "")),
            ("Private key", interface.get("PrivateKey", "")),
        ]
    print(f"\n--- CONNECTION SETTINGS: {name} ---")
    for key, value in fields:
        print(f"{key}: {value}")
    print("--- END SETTINGS ---\n")


def xray_yaml(conf, name, family="ipv4"):
    """Render a VLESS/XHTTP/REALITY Mihomo proxy in the requested field order.

    Server address and client credentials are read from the current Xray
    configuration. Client-side YAML defaults match the requested template.
    """
    incoming = xray_inbound(conf)
    clients = [c for c in incoming["settings"]["clients"] if c.get("email") == name]
    if len(clients) != 1:
        raise RuntimeError(f"Xray client does not exist or is ambiguous: {name}")
    stream = incoming["streamSettings"]
    reality = stream["realitySettings"]
    xhttp = stream["xhttpSettings"]
    if family == "ipv6":
        pair = xray_inbounds(conf)
        if len(pair) != 2:
            raise RuntimeError("Xray IPv6 inbound has not been enabled.")
        server = public_ipv6()
        if pair[1]["listen"] != server:
            raise RuntimeError("Xray IPv6 inbound address is not the active IPv6 address.")
    else:
        server = incoming.get("listen")
        if not server or server in ("0.0.0.0", "::"):
            server = public_ipv4()
    key = reality_client_key(command("xray", "x25519", "-i", reality["privateKey"]))
    server_names = reality.get("serverNames") or []
    short_ids = reality.get("shortIds") or []
    if not server_names or not short_ids or not xhttp.get("path"):
        raise RuntimeError("REALITY SNI, short ID or XHTTP path missing from server configuration.")
    # Quote address, UUID, key, short ID and path exactly as in the template.
    quoted = lambda value: json.dumps(str(value), ensure_ascii=False)
    lines = [
        f"- name: {name if family == 'ipv4' else name + '-IPv6'}",
        "  type: vless",
        f"  server: {quoted(server)}",
        f"  port: {int(incoming['port'])}",
        f"  uuid: {quoted(clients[0]['id'])}",
        "  udp: true",
        "  tls: true",
        f"  servername: {server_names[0]}",
        "  client-fingerprint: chrome",
        f"  network: {stream['network']}",
        "  alpn:",
        "    - h2",
        '  encryption: ""',
        "  reality-opts:",
        f"    public-key: {quoted(key)}",
        "    support-x25519mlkem768: true",
        f"    short-id: {quoted(short_ids[0])}",
        "  xhttp-opts:",
        f"    path: {quoted(xhttp['path'])}",
        f"    mode: {xhttp.get('mode', 'stream-one')}",
    ]
    return "\n".join(lines) + "\n"


def awg_client_config(name, family="ipv4"):
    """Build an alternate IPv6 endpoint without rewriting the saved .conf."""
    original = (AWG_FILES / (name + ".conf")).read_text()
    if family == "ipv4":
        return original
    if family != "ipv6":
        raise ValueError(f"Unknown address family: {family}")
    endpoint = awg_endpoint("ipv6")
    server = AWG.read_text()
    port = awg_value(server, "ListenPort")
    # Only the existing single Endpoint directive is changed.
    matches = re.findall(r"(?m)^[ \t]*Endpoint[ \t]*=[^\r\n]*$", original)
    if len(matches) != 1:
        raise RuntimeError("Expected one AWG client Endpoint; refusing to alter the profile.")
    return original.replace(matches[0], f"Endpoint = {endpoint}:{port}", 1)


def compact_awg_qr_payload(config):
    """Reduce whitespace in a client config without omitting any directives.

    This changes only the data encoded into QR images, not the saved .conf.
    AWG/WireGuard parsers accept Key=Value as well as Key = Value.
    """
    lines = []
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line in ("[Interface]", "[Peer]"):
            lines.append(line)
        elif "=" in line:
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key) or not value:
                raise RuntimeError("Unexpected AWG configuration directive; cannot safely compact QR.")
            lines.append(f"{key}={value}")
        else:
            raise RuntimeError("Unexpected AWG configuration line; cannot safely compact QR.")
    if lines.count("[Interface]") != 1 or lines.count("[Peer]") != 1:
        raise RuntimeError("AWG client profile is missing an expected section.")
    return "\n".join(lines) + "\n"


def save_client_qr_png(kind, name, value, family="ipv4", separate=False):
    """Persist a scannable QR image with restricted access."""
    directory = XRAY_FILES if kind == "xray" else AWG_FILES
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    suffix = f".{family}.png" if separate else ".png"
    path = directory / (name + suffix)
    fd, temporary = tempfile.mkstemp(prefix=".qr-", suffix=".png", dir=directory)
    os.close(fd)
    try:
        png_margin = "4" if kind == "xray" else "3"
        png_scale = "6" if kind == "xray" else "3"
        command("qrencode", "-t", "PNG", "-l", "L", "-s", png_scale,
                "-m", png_margin, "-o", temporary, value)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def get_profile(kind, name, output, family="ipv4"):
    """Render one or both transports while keeping one set of client keys."""
    families = ("ipv4", "ipv6") if family == "both" else (family,)
    if family not in ("ipv4", "ipv6", "both"):
        raise ValueError("Invalid address family.")
    conf = json.loads(XRAY.read_text()) if kind == "xray" else None
    for selected_family in families:
        if kind == "xray":
            value = (xray_link(conf, name) if selected_family == "ipv4"
                     else xray_link(conf, name, selected_family))
            label = "VLESS connection link"
        else:
            value = awg_client_config(name, selected_family)
            label = "AWG configuration"
        if family != "ipv4":
            print(f"\n========== {name} / {selected_family.upper()} ==========")
        if output in ("yaml", "all") and kind == "xray":
            print(f"--- YAML PROXY: {name} ---")
            print(xray_yaml(conf, name, selected_family), end="")
            print("--- END YAML ---\n")
        if output in ("details", "all"):
            connection_details(kind, name, selected_family)
        if output in ("text", "both", "all"):
            print(f"{label} for {name}:")
            print(value)
            if kind == "awg" and selected_family == "ipv4":
                print(f"Saved at: {AWG_FILES / (name + '.conf')}")
            elif kind == "awg":
                print("IPv6 endpoint variant generated in memory; saved .conf unchanged.")
        if output in ("qr", "both", "all"):
            print(f"\n--- QR CODE: {name} ({selected_family}) ---")
            qr_value = compact_awg_qr_payload(value) if kind == "awg" else value
            if family == "ipv4":
                path = save_client_qr_png(kind, name, qr_value)
            else:
                path = save_client_qr_png(kind, name, qr_value, selected_family, separate=True)
            margin = "2" if kind == "awg" else "4"
            qr = command("qrencode", "-t", "ANSIUTF8", "-l", "L",
                         "-m", margin, qr_value, preserve_whitespace=True)
            sys.stdout.write(qr)
            if not qr.endswith("\n"):
                print()
            if kind == "awg":
                print(f"QR payload: {len(qr_value.encode('utf-8'))} bytes "
                      f"(saved config: {len(value.encode('utf-8'))} bytes).")
            print(f"Scannable PNG saved: {path}")
            print("--- END QR CODE ---\n")


def main():
    parser = argparse.ArgumentParser(description="VPS VPN profile manager")
    parser.add_argument("kind", choices=("xray", "awg"))
    parser.add_argument("action", choices=("add", "get"))
    parser.add_argument("name")
    parser.add_argument("--family", choices=("ipv4", "ipv6", "both"), default="ipv4")
    parser.add_argument("--format", choices=("none", "details", "text", "qr", "both", "yaml", "all"), default="both")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run as root.")
    if not CLIENT_NAME.fullmatch(args.name):
        raise RuntimeError("Name must be 1-48 letters, digits, dashes or underscores.")
    if args.action == "add":
        if args.kind == "xray":
            add_xray(args.name)
        else:
            add_awg(args.name)
    if args.format != "none":
        get_profile(args.kind, args.name, args.format, args.family)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
