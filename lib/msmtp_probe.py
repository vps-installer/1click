#!/usr/bin/env python3
"""Read public metadata of msmtp's default account without evaluating commands or passwords.

Only plain local msmtprc directives are supported. Fail closed on includes,
ambiguous accounts, unsupported quoting and conflicting sender/username.
"""
import argparse
import re
import shlex
import sys
from pathlib import Path

HOST_RE = re.compile(r"(?i)^[a-z0-9][a-z0-9.-]*$")
EMAIL_RE = re.compile(r"(?i)^[a-z0-9._%+\-]+@[a-z0-9.-]+\.[a-z]{2,}$")
OUTPUT_KEYS = ('host', 'port', 'tls', 'tls_starttls', 'from', 'user')


def probe(path: Path) -> dict:
    defaults = {}
    accounts = {}
    section = None
    for raw in path.read_text(encoding='utf-8').splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            continue
        # Never parse credentials. Passwords may contain shell-significant
        # characters that msmtp handles differently from shlex.
        leading = stripped.split(None, 1)[0].lower()
        if leading in ('password', 'passwordeval'):
            continue
        # Any include or unsafe escape means we cannot prove what msmtp uses.
        try:
            words = shlex.split(stripped, comments=True, posix=True)
        except ValueError as exc:
            raise ValueError('Unsupported msmtp syntax') from exc
        if not words:
            continue
        key = words[0].lower()
        if key == 'include':
            raise ValueError('Included msmtp files are not supported for automatic import')
        if key == 'defaults':
            if len(words) != 1:
                raise ValueError('Invalid defaults directive')
            section = defaults
            continue
        if key == 'account':
            if len(words) == 2:
                if words[1] in accounts:
                    raise ValueError('Duplicate msmtp account')
                section = accounts[words[1]] = dict(defaults)
                continue
            if len(words) == 4 and words[2] == ':':
                name, base = words[1], words[3]
                if name in accounts or base not in accounts:
                    raise ValueError('Invalid account alias')
                section = accounts[name] = dict(accounts[base])
                continue
            raise ValueError('Unsupported account directive')
        if section is None:
            # msmtp directives without defaults/account belong to implicit defaults.
            section = defaults
        if key in OUTPUT_KEYS:
            if len(words) != 2:
                raise ValueError('Ambiguous SMTP metadata directive')
            section[key] = words[1]
        # Never inspect/execute password/passwordeval or expose any auth credentials.
    if 'default' not in accounts:
        raise ValueError('No default msmtp account')
    info = accounts['default']
    host = info.get('host', '')
    sender = info.get('from', '')
    username = info.get('user', '')
    if not HOST_RE.fullmatch(host) or not EMAIL_RE.fullmatch(sender):
        raise ValueError('Missing or invalid SMTP host/sender')
    if username != sender:
        raise ValueError('Default account SMTP username differs from sender')
    port = info.get('port', '')
    if port and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise ValueError('Invalid SMTP port')
    tls = info.get('tls', '')
    starttls = info.get('tls_starttls', '')
    if tls not in ('on', '') or starttls not in ('on', 'off', ''):
        raise ValueError('Unsupported TLS configuration')
    encryption = 'STARTTLS' if tls == 'on' and starttls != 'off' else ('TLS-on-connect' if tls == 'on' else 'Not configured')
    return {'host': host, 'port': port or 'Default', 'encryption': encryption, 'sender': sender}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', default='/etc/msmtprc')
    args = parser.parse_args()
    try:
        values = probe(Path(args.file))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f'Cannot safely read default SMTP account: {exc}', file=sys.stderr)
        return 1
    for name in ('host', 'port', 'encryption', 'sender'):
        print(f'{name}={values[name]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
