"""Smoke-test the real interactive installer menu on an ephemeral CI runner."""
import os
import pty
import select
import signal
import time

deadline = time.monotonic() + 18
pid, fd = pty.fork()
if pid == 0:
    os.execv("/bin/bash", ["bash", "install.sh"])

seen = b""
sent = False
try:
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.3)
        if readable:
            try:
                data = os.read(fd, 8192)
            except OSError:
                break
            if not data:
                break
            seen += data
            if b"Choose option:" in seen and not sent:
                os.write(fd, b"0\n")
                sent = True
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            pid = 0
            if not sent or not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
                raise RuntimeError(f"Installer failed: {seen.decode(errors='replace')}")
            while True:
                ready, _, _ = select.select([fd], [], [], 0)
                if not ready:
                    break
                try:
                    chunk = os.read(fd, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                seen += chunk
            for required in (b"Manage clients profiles", b"LOCAL VPS INSTALLER", b"sudo vps-installer", b"/usr/local/sbin/vps-installer"):
                if required not in seen:
                    raise AssertionError(f"Missing exit footer {required!r}: {seen.decode(errors='replace')}")
            print("Interactive menu and exit footer smoke tests passed.")
            break
    else:
        raise TimeoutError(f"Installer menu did not finish: {seen.decode(errors='replace')}")
finally:
    if pid:
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
    os.close(fd)
