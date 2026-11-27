#!/usr/bin/env python3
"""Validate first-install network choices and generate AWG obfuscation."""
import ipaddress
import re
import secrets
import socket
import ssl
import sys


def validate_sni(name):
    if (not isinstance(name, str) or len(name) > 253 or len(name) < 4
            or name.lower() != name or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
                name,
            )):
        raise ValueError("Enter a lowercase DNS hostname, without a scheme, IP, port or path.")
    if name.endswith((".ru", ".su", ".xn--p1ai")):
        raise ValueError("Russian destinations are blocked by this VPN configuration.")
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise ValueError("Use a hostname rather than an IP address.")


def check_tls(name):
    validate_sni(name)
    # Validate name resolution and a real, certificate-verified TLS connection.
    context = ssl.create_default_context()
    context.set_alpn_protocols(["h2"])
    with socket.create_connection((name, 443), timeout=7) as sock:
        with context.wrap_socket(sock, server_hostname=name) as tls:
            if tls.version() != "TLSv1.3":
                raise ValueError("REALITY destination must support TLS 1.3.")
            if tls.selected_alpn_protocol() != "h2":
                raise ValueError("REALITY destination must support HTTP/2 (ALPN h2).")
    return name


def validate_port(value):
    if not re.fullmatch(r"[0-9]{1,5}", str(value)):
        raise ValueError("UDP port must be a decimal integer (1-65535).")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("UDP port must be within 1-65535.")
    return port


def generate_random_udp_port():
    """Suggest an unpredictable, currently bindable unprivileged UDP port."""
    for _ in range(128):
        port = 20000 + secrets.randbelow(40000)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("0.0.0.0", port))
        except OSError:
            continue
        return port
    raise ValueError("Unable to find an available random UDP port.")



def generate_awg_obfuscation():
    """Conservative, fresh supported values for the existing AWG v1 layout."""
    jmin = secrets.randbelow(43) + 48     # 48..90
    jmax = secrets.randbelow(91) + 140    # 140..230
    values = {
        "Jc": secrets.randbelow(6) + 3,   # 3..8
        "Jmin": jmin,
        "Jmax": jmax,
        "S1": secrets.randbelow(53) + 48, # 48..100
        "S2": secrets.randbelow(53) + 48,
        "S3": secrets.randbelow(33) + 16, # 16..48
        "S4": secrets.randbelow(33) + 16,
    }
    for i in range(4):
        values[f"H{i+1}"] = 100000000 + 300000000 * i + secrets.randbelow(100000000)
    pmin = secrets.randbelow(17) + 8       # 8..24
    pmax = secrets.randbelow(17) + 40      # 40..56
    values["ContentPaddingAddition"] = f"{pmin}-{pmax}"
    return values


def main(argv):
    if len(argv) != 3 and not (len(argv) == 2 and argv[1] == "random-port"):
        raise ValueError("Usage: install_options.py check-sni HOST | check-port PORT | random-port")
    if argv[1] == "random-port":
        print(generate_random_udp_port())
    elif argv[1] == "check-sni":
        print(f"Validated REALITY TLS 1.3 destination: {check_tls(argv[2])}")
    elif argv[1] == "check-port":
        print(f"Validated AWG UDP port: {validate_port(argv[2])}")
    else:
        raise ValueError("Unsupported operation.")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except (ValueError, OSError, ssl.SSLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
