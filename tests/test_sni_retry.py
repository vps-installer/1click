"""Exercise REALITY SNI retry flow without installing or touching system files."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VPN = ROOT / "lib/vpn.sh"


class SniRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = VPN.read_text(encoding="utf-8")
        cls.choice = source.split(
            "  # Choose REALITY destination only for a new deployment", 1
        )[1].split("\n", 1)[1].split("  # The official installer is deliberately launched", 1)[0]

    def run_choice(self):
        script = """
set -e
config_path=/definitely-not-existing-xray-config-for-tests
say() { printf '%s\\n' "$*"; }
warn() { printf 'WARN: %s\\n' "$*"; }
die() { echo "UNEXPECTED ABORT: $*" >&2; exit 75; }
counter=0
ask_default_choice() {
  counter=$((counter+1))
  if (( counter == 1 )); then REPLY=2; else REPLY=1; fi
  return 0
}
ask() { REPLY=nederland.fm; }
python3() {
  if [[ "$3" == nederland.fm ]]; then
    echo "ERROR: REALITY destination must support TLS 1.3."
    return 1
  fi
  return 0
}
run_choice() {
""" + self.choice + """
printf 'CHOICE:%s;ATTEMPTS:%s\\n' "$reality_sni" "$counter"
}
run_choice
"""
        return subprocess.run(["bash", "-c", script], text=True,
                              capture_output=True, check=False)

    def test_invalid_tls_returns_to_choice_and_then_accepts_default(self):
        p = self.run_choice()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("REALITY destination must support TLS 1.3", p.stdout)
        self.assertIn("Enter another SNI or select the default", p.stdout)
        self.assertIn("CHOICE:open.spotify.com;ATTEMPTS:2", p.stdout)
        self.assertNotIn("UNEXPECTED ABORT", p.stdout + p.stderr)

    def test_installation_is_ordered_after_sni_validation(self):
        source = VPN.read_text(encoding="utf-8")
        start = source.index("install_xray() {")
        section = source[start:source.index("install_amnezia() {")]
        self.assertLess(section.index("if xray_already_present; then"),
                        section.index("Choose SNI mode"))
        self.assertLess(section.index("if python3 /opt/vps-installer"),
                        section.index('run_step "Install official Xray"'))
        self.assertLess(section.index('run_step "Install official Xray"'),
                        section.index('cp -p -- "$config_path"'))


if __name__ == "__main__":
    unittest.main()
