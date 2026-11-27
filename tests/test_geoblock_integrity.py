"""Verify loaded nftables GeoBlock policy without live firewall changes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VPN = (ROOT / "lib/vpn.sh").read_text()
CHECK = VPN.split("  # Capture the complete nft output first:", 1)[1].split(
    "  cat >/etc/systemd/system/awg-geoblock.service", 1
)[0]

SAFE_CHAIN = """table inet awg_geoblock {
 chain forward {
  type filter hook forward priority -150; policy accept;
  iifname "awg0" meta nfproto ipv6 counter packets 0 bytes 0 drop
  iifname "awg0" oifname != "eth0" counter drop
  iifname "awg0" ip daddr @blocked4 counter drop
  iifname "awg0" ip daddr @ru4 counter drop
  iifname "awg0" udp dport 53 counter drop
  iifname "awg0" tcp dport 53 counter drop
  iifname "awg0" oifname "eth0" ip saddr 10.88.32.0/24 meta mark set 0x0000a609
 }
}"""


class GeoBlockCheckTests(unittest.TestCase):
    def execute(self, chain):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "loaded-chain"
            out.write_text(chain)
            log = Path(td) / "installer.log"
            script = """
set -o pipefail
warn() { printf 'WARN: %s\\n' "$*"; }
ok() { printf 'OK: %s\\n' "$*"; }
die() { printf 'DIE: %s\\n' "$*"; exit 91; }
nft() { cat "$CHAIN_FILE"; }
verify() {
""" + CHECK + """
}
verify
"""
            result = subprocess.run(
                ["bash", "-c", script],
                env=dict(os.environ, CHAIN_FILE=str(out), LOG_FILE=str(log)),
                text=True, capture_output=True, timeout=5,
            )
            return result, log.read_text() if log.exists() else ""

    def test_valid_geo_policy_is_accepted(self):
        result, _ = self.execute(SAFE_CHAIN)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("verified", result.stdout)

    def test_missing_ru_block_is_rejected(self):
        result, log = self.execute(SAFE_CHAIN.replace("ip daddr @ru4", "ip daddr @wrong"))
        self.assertEqual(result.returncode, 91)
        self.assertIn("GeoBlock integrity failed", result.stdout)
        self.assertIn("@wrong", log)

    def test_missing_mark_is_rejected(self):
        result, _ = self.execute(SAFE_CHAIN.replace("meta mark set 0x0000a609", "counter accept"))
        self.assertEqual(result.returncode, 91)


if __name__ == "__main__":
    unittest.main()
