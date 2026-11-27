#!/usr/bin/env python3
"""Safely rename or revoke Xray and AmneziaWG client profiles."""
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import profile_manager as pm

os.umask(0o077)


def backup(path):
    dest = path.with_name(path.name + ".before-profile-" +
                          datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + ".bak")
    shutil.copy2(path, dest)
    dest.chmod(0o600)
    return dest


def write_atomic(path, content):
    st = path.stat()
    fd, name = tempfile.mkstemp(prefix=".profile-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.chown(name, st.st_uid, st.st_gid)
        os.chmod(name, st.st_mode & 0o777)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def profile_files(kind, name):
    base = pm.XRAY_FILES if kind == "xray" else pm.AWG_FILES
    suffixes = (".uuid", ".png", ".ipv4.png", ".ipv6.png") if kind == "xray" else (".conf", ".private", ".public", ".png", ".ipv4.png", ".ipv6.png")
    return [base / (name + suffix) for suffix in suffixes]


def move_files(kind, name, new_name=None):
    paths = [p for p in profile_files(kind, name) if p.exists()]
    if new_name:
        destinations = [p.with_name(new_name + p.name[len(name):]) for p in paths]
        if any(p.exists() for p in profile_files(kind, new_name)):
            raise RuntimeError("Destination profile files already exist.")
    else:
        archive = (pm.XRAY_FILES if kind == "xray" else pm.AWG_FILES) / ".revoked"
        archive.mkdir(mode=0o700, exist_ok=True)
        archive.chmod(0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        destinations = [archive / (stamp + "-" + p.name) for p in paths]
    moved = []
    try:
        for src, dst in zip(paths, destinations):
            os.replace(src, dst)
            moved.append((src, dst))
    except Exception:
        for src, dst in reversed(moved):
            os.replace(dst, src)
        raise
    return moved


def xray_change(action, name, new_name):
    with open("/run/vps-installer/xray-profiles.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = pm.XRAY.read_bytes()
        data = json.loads(before)
        inbounds = pm.xray_inbounds(data)
        clients = inbounds[0]["settings"]["clients"]
        matches = [c for c in clients if c.get("email") == name]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one Xray client with that name.")
        if action == "rename":
            if any(c.get("email") == new_name for c in clients):
                raise RuntimeError("Destination Xray profile name already exists.")
            if any(p.exists() for p in profile_files("xray", new_name)):
                raise RuntimeError("Destination Xray files already exist.")
            for inbound in inbounds:
                own = [c for c in inbound["settings"]["clients"] if c.get("email") == name]
                if len(own) != 1 or own[0].get("id") != matches[0].get("id"):
                    raise RuntimeError("Xray IPv4/IPv6 client credentials disagree.")
                own[0]["email"] = new_name
        else:
            for inbound in inbounds:
                own = [c for c in inbound["settings"]["clients"] if c.get("email") == name]
                if len(own) != 1 or own[0].get("id") != matches[0].get("id"):
                    raise RuntimeError("Xray IPv4/IPv6 client credentials disagree.")
                inbound["settings"]["clients"].remove(own[0])
        snapshot = backup(pm.XRAY)
        try:
            pm.atomic_json(pm.XRAY, data, before)
            try:
                pm.command("systemctl", "restart", "xray")
                pm.command("systemctl", "is-active", "--quiet", "xray")
                move_files("xray", name, new_name if action == "rename" else None)
            except Exception:
                write_atomic(pm.XRAY, before)
                subprocess.run(["systemctl", "restart", "xray"], check=False)
                raise
        except Exception as exc:
            raise RuntimeError(f"Xray update failed; configuration restored from memory. Backup: {snapshot}") from exc
        print(f"Xray profile {action} completed: {name}" +
              (f" -> {new_name}" if new_name else "") + f"; backup: {snapshot}")


def awg_peer_block(server, pub):
    """Locate one server peer by actual public key, not by a display comment."""
    matches = list(re.finditer(r"(?m)^[ \t]*\[Peer\][ \t]*$", server))
    found = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(server)
        # A "# name" line immediately before the next [Peer] belongs to that
        # next peer; do not delete it when revoking the current client.
        if idx + 1 < len(matches):
            segment = server[match.start():end]
            trailing = re.search(r"(?m)^# [A-Za-z0-9_-]{1,48}\n[ \t]*$", segment)
            if trailing:
                end = match.start() + trailing.start()
        block = server[match.start():end]
        keys = re.findall(r"(?mi)^[ \t]*PublicKey[ \t]*=[ \t]*([^\r\n#]+)", block)
        if len(keys) == 1 and keys[0].strip() == pub:
            found.append((match.start(), end, block))
    if len(found) != 1:
        raise RuntimeError("Cannot uniquely identify the AWG peer by its public key.")
    return found[0]


def replace_awg_comment(server, name, new_name):
    # Only replace the exact existing profile comment, leaving all other text intact.
    old = "# " + name
    new = "# " + new_name
    lines = server.splitlines(keepends=True)
    count = sum(line.rstrip("\r\n") == old for line in lines)
    if count > 1:
        raise RuntimeError("Ambiguous AWG profile comment.")
    if count == 1:
        return "".join((new + line[len(old):]) if line.rstrip("\r\n") == old else line for line in lines)
    return server


def awg_change(action, name, new_name):
    with open("/run/vps-installer/awg-profiles.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        conf = pm.AWG_FILES / (name + ".conf")
        pubpath = pm.AWG_FILES / (name + ".public")
        if not conf.is_file() or not pubpath.is_file():
            raise RuntimeError("AWG client configuration/public key is missing; refusing unsafe change.")
        pub = pubpath.read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", pub):
            raise RuntimeError("AWG client public key is invalid.")
        if action == "rename" and any(p.exists() for p in profile_files("awg", new_name)):
            raise RuntimeError("Destination AWG profile files already exist.")
        before = pm.AWG.read_bytes()
        server = before.decode("utf-8")
        start, end, block = awg_peer_block(server, pub)
        snapshot = backup(pm.AWG)
        if action == "rename":
            # Rename only the comment directly attached to this exact public-key peer.
            prefix = server[:start]
            old_comment = "# " + name + "\n"
            if prefix.endswith(old_comment):
                prefix = prefix[:-len(old_comment)] + "# " + new_name + "\n"
            updated = prefix + server[start:]
            if updated != server:
                write_atomic(pm.AWG, updated.encode())
            try:
                move_files("awg", name, new_name)
            except Exception:
                write_atomic(pm.AWG, before)
                raise
        else:
            allowed = re.findall(r"(?mi)^[ \t]*AllowedIPs[ \t]*=[ \t]*([^\r\n#]+)", block)
            if len(allowed) != 1:
                raise RuntimeError("Cannot safely identify AWG peer AllowedIPs.")
            # Remove the matching name comment immediately preceding its peer.
            prefix = server[:start]
            marker = "# " + name + "\n"
            if prefix.endswith(marker):
                prefix = prefix[:-len(marker)]
            updated = prefix + server[end:]
            # Confirm the live peer is present before removing its access.
            live = pm.command("awg", "show", "awg0", "allowed-ips")
            live_peers = {parts[0]: parts[1:] for row in live.splitlines()
                          if (parts := row.split())}
            if pub not in live_peers:
                raise RuntimeError("Peer missing from live awg0; refusing to edit a divergent state.")
            disk_ips = {part.strip() for part in allowed[0].split(",")}
            active_ips = {part.strip().rstrip(",") for part in live_peers[pub]}
            if disk_ips != active_ips:
                raise RuntimeError("AWG peer addresses differ between disk and live interface; refusing deletion.")
            pm.command("awg", "set", "awg0", "peer", pub, "remove")
            try:
                write_atomic(pm.AWG, updated.encode())
                move_files("awg", name)
            except Exception:
                write_atomic(pm.AWG, before)
                subprocess.run(["awg", "set", "awg0", "peer", pub,
                                "allowed-ips", allowed[0].strip()], check=False)
                raise
        print(f"AWG profile {action} completed: {name}" +
              (f" -> {new_name}" if new_name else "") + f"; backup: {snapshot}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=("xray", "awg"))
    p.add_argument("action", choices=("rename", "delete"))
    p.add_argument("name")
    p.add_argument("new_name", nargs="?")
    args = p.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run as root.")
    if not pm.CLIENT_NAME.fullmatch(args.name):
        raise RuntimeError("Invalid profile name.")
    if args.action == "rename":
        if not args.new_name or not pm.CLIENT_NAME.fullmatch(args.new_name):
            raise RuntimeError("New name must be 1-48 letters, digits, dashes or underscores.")
        if args.name == args.new_name:
            raise RuntimeError("New name must differ from the old one.")
    elif args.new_name is not None:
        raise RuntimeError("Delete operation does not accept a new name.")
    if args.kind == "xray":
        xray_change(args.action, args.name, args.new_name)
    else:
        awg_change(args.action, args.name, args.new_name)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
