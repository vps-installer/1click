"""Regression: viewing an AWG client returns to the profile action menu."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lib/profiles.sh"


class ProfileNavigationTests(unittest.TestCase):
    def test_view_returns_to_action_menu_and_exit_still_exits(self):
        source = SCRIPT.read_text(encoding="utf-8")
        # Replace the installation guard only inside this isolated test script.
        source = source.replace(
            '[[ -s /etc/amnezia/amneziawg/awg0.conf ]]',
            'true',
        )
        shell = source + r"""
say() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; }
list_profiles() { printf 'SampleClient\n'; }
call_count=0
ask_choice() {
  case "$1" in
    "Choose profile type:") REPLY=2 ;;
    "Choose action:")
      call_count=$((call_count+1))
      if (( call_count == 1 )); then REPLY=2; else REPLY=0; fi ;;
    "Profile number:") REPLY=1 ;;
    *) echo "Unexpected menu $1" >&2; return 1 ;;
  esac
}
ask_default_choice() { REPLY=1; }
run_secret_step() { printf 'DISPLAY:%s\n' "$2"; return 0; }
profile_menu
printf 'ACTION_COUNT:%s\n' "$call_count"
"""
        result = subprocess.run(
            ["bash", "-c", shell], capture_output=True, text=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Profile viewing finished", result.stdout)
        self.assertIn("ACTION_COUNT:2", result.stdout)
        self.assertEqual(result.stdout.count("Choose a saved profile:"), 1)


if __name__ == "__main__":
    unittest.main()
