#!/usr/bin/env python3
"""Atomic refresh of the Russian IPv4 set used by AmneziaWG GeoBlock."""
import fcntl
import ipaddress
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = Path("/etc/awg-geoblock")
ZONE = BASE / "ru4.zone"
CONFIG = BASE / "awg-geoblock.nft"
URL = "https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone"


def execute(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def parse(data):
    values = [ipaddress.IPv4Network(x, strict=True) for x in data.split() if x]
    if not 5000 <= len(values) <= 20000 or any(x.prefixlen < 8 for x in values):
        raise ValueError("Suspicious Russian network list")
    return values


def atomic(path, contents):
    fd, temp = tempfile.mkstemp(dir=BASE, prefix=".update-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Root is required")
    # Never update a compromised or missing policy.
    chain = execute("nft", "list", "chain", "inet", "awg_geoblock", "forward")
    if not re.search(r"meta mark set (?:0x0*a609|42505)(?:\s|$)", chain):
        raise RuntimeError("Expected GeoBlock marking rule is missing")
    execute("iptables", "-C", "ufw-before-forward", "-i", "awg0", "-j", "DROP")
    old_zone = ZONE.read_text()
    old_config = CONFIG.read_text()
    old_nets = parse(old_zone)
    req = urllib.request.Request(URL, headers={"User-Agent": "VPS-Installer-Geoblock/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = r.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("Downloaded network list is too large")
    new_zone = payload.decode("ascii")
    new_nets = parse(new_zone)
    if len(new_nets) < len(old_nets) * 0.8:
        raise ValueError("Unexpectedly large shrink in network list")
    if new_zone == old_zone:
        print(f"UNCHANGED: {len(new_nets)} networks")
        return
    joined = ",\n".join(map(str, ipaddress.collapse_addresses(new_nets)))
    match = re.search(r"(?s)set ru4 \{\s*type ipv4_addr;\s*flags interval;\s*auto-merge;\s*elements = \{.*?\}\s*\}", old_config)
    if match is None:
        raise RuntimeError("Unexpected persistent nftables config format")
    new_config = old_config[:match.start()] + (
        "set ru4 { type ipv4_addr; flags interval; auto-merge; elements = { " + joined + " } }"
    ) + old_config[match.end():]
    batch = f"flush set inet awg_geoblock ru4\nadd element inet awg_geoblock ru4 {{\n{joined}\n}}\n"
    with tempfile.TemporaryDirectory(dir=BASE) as td:
        batch_path = Path(td) / "update.nft"
        batch_path.write_text(batch)
        execute("nft", "-c", "-f", str(batch_path))
        atomic(BASE / "ru4.zone.bak", old_zone)
        atomic(BASE / "awg-geoblock.nft.bak-update", old_config)
        atomic(ZONE, new_zone)
        atomic(CONFIG, new_config)
        try:
            execute("nft", "-f", str(batch_path))
        except Exception:
            atomic(ZONE, old_zone)
            atomic(CONFIG, old_config)
            raise
    print(f"UPDATED: {len(old_nets)} -> {len(new_nets)} networks")


if __name__ == "__main__":
    try:
        with open("/run/awg-geoblock-update.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
