#!/usr/bin/env python3
"""Fail-closed Xray GeoData updater: stage, validate, restore on failure."""
import fcntl
import hashlib
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ASSETS = Path('/usr/local/share/xray')
CONFIG = Path('/usr/local/etc/xray/config.json')
SOURCE = 'https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/'
LIMIT = 64 * 1024 * 1024


def run(*argv, **kw):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=40, **kw)


def notify(result, message):
    helper = '/usr/local/sbin/vps-maintenance-notify'
    if os.path.isfile(helper):
        subprocess.run([helper, 'xray', result, message], timeout=20, check=False)


def download(name, dest):
    req = urllib.request.Request(SOURCE + name, headers={'User-Agent': '1click-maintenance/1.0'})
    with urllib.request.urlopen(req, timeout=45) as response, dest.open('wb') as out:
        length = 0
        while True:
            data = response.read(64 * 1024)
            if not data:
                break
            length += len(data)
            if length > LIMIT:
                raise ValueError(f'{name} exceeds size limit')
            out.write(data)
    if length < 4096:
        raise ValueError(f'{name} suspiciously small ({length} bytes)')


def digest(file):
    sha = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def as_xray(asset_dir):
    user = pwd.getpwnam('xray')
    return run('runuser', '-u', user.pw_name, '--', 'env',
               f'XRAY_LOCATION_ASSET={asset_dir}', 'xray', 'run',
               '-test', '-config', str(CONFIG))


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    if not CONFIG.is_file() or not ASSETS.is_dir():
        raise RuntimeError('Xray configuration or assets directory not found')
    run('systemctl', 'is-active', '--quiet', 'xray')
    old = {name: ASSETS / name for name in ('geoip.dat', 'geosite.dat')}
    if any(not p.is_file() for p in old.values()):
        raise RuntimeError('Current GeoData files missing')
    # Assets may be group-owned; temporary directory must be readable by the xray user.
    with tempfile.TemporaryDirectory(prefix='.geodata-', dir=ASSETS) as temp:
        staging = Path(temp)
        os.chmod(staging, 0o755)
        for name in old:
            download(name, staging / name)
            os.chmod(staging / name, 0o644)
        if all(digest(staging / name) == digest(old[name]) for name in old):
            print('UNCHANGED: GeoData databases')
            return
        # All routing GeoIP/GeoSite lookups must resolve against the candidate pair.
        as_xray(staging)
        backup = staging / 'backup'
        backup.mkdir(mode=0o700)
        for name in old:
            shutil.copy2(old[name], backup / name)
        changed = []
        try:
            for name in old:
                candidate = staging / name
                os.chown(candidate, 0, pwd.getpwnam('xray').pw_gid)
                os.chmod(candidate, 0o640)
                os.replace(candidate, old[name])
                changed.append(name)
            run('systemctl', 'restart', 'xray')
            run('systemctl', 'is-active', '--quiet', 'xray')
            # Keep full previous pair until service health is established.
            print('UPDATED: GeoData databases; Xray active')
            notify('updated', 'Xray GeoData updated and service active')
        except Exception as error:
            for name in changed:
                previous = backup / name
                # The backup is on the same filesystem for atomic restore.
                os.replace(previous, old[name])
            try:
                run('systemctl', 'restart', 'xray')
                run('systemctl', 'is-active', '--quiet', 'xray')
                recovery = 'previous GeoData restored; Xray active'
            except Exception as restore_error:
                recovery = f'CRITICAL: restoration or Xray restart failed ({restore_error})'
                # Never leave an unverified Xray service running.
                subprocess.run(['systemctl', 'stop', 'xray'], check=False)
            raise RuntimeError(f'{error}; {recovery}') from error


if __name__ == '__main__':
    try:
        with open('/run/lock/vps-maintenance-xray.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'FAILED: {error}', file=sys.stderr)
        notify('failed', f'Xray GeoData update failed: {error}')
        sys.exit(1)
