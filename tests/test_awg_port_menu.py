"""Regression tests for the AmneziaWG random/default and custom UDP port menu."""
from pathlib import Path
import shlex
import subprocess
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "lib" / "vpn.sh").read_text()
START = '  if (( existing_awg == 0 )); then\n  local random_port\n'
END = '  fi  # new AmneziaWG installation options'


class AwgPortMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block = START + SOURCE.split(START, 1)[1].split(END, 1)[0] + '  fi'

    def run_choices(self, entries, custom="41234", random_port="39617"):
        test_program = r'''
ROOT=/opt/vps-installer
say() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; }
ask_default() {
  local value
  value="$(sed -n "$((count+1))p" <<< "$answers")"
  count=$((count+1))
  REPLY="$value"
  [[ -n "$REPLY" ]] || REPLY="$2"
}
ask() { REPLY="$custom"; }
python3() {
  case "$2" in
    random-port) printf '%s\n' "$generated"; return 0 ;;
    check-port) [[ "$3" =~ ^[0-9]+$ ]] && (( 10#$3 >= 1 && 10#$3 <= 65535 )); return ;;
  esac
  return 1
}
ss() { printf '%s\n' 'UNCONN 0 0 0.0.0.0:31507 0.0.0.0:*'; }
select_port() {
  local existing_awg=0 awg_port
''' + self.block + r'''
  printf 'PORT:%s\n' "$awg_port"
}
count=0
answers=__ANSWERS__
custom=__CUSTOM__
generated=__GENERATED__
select_port
printf 'ATTEMPTS:%s\n' "$count"
'''
        for placeholder, value in (
            ("__ANSWERS__", "\n".join(entries)),
            ("__CUSTOM__", custom),
            ("__GENERATED__", random_port),
        ):
            test_program = test_program.replace(placeholder, shlex.quote(value))
        return subprocess.run(["bash", "-c", test_program], text=True,
                              capture_output=True, check=False)

    def test_exact_menu_and_prompt(self):
        self.assertIn('say "1) Random UDP port ($random_port)"', self.block)
        self.assertIn('say "2) Custom UDP port"', self.block)
        self.assertIn('say "0) Exit"', self.block)
        self.assertIn('ask_default "Choose UDP port option" "$random_port"', self.block)
        self.assertNotIn("enter UDP port directly", self.block)

    def test_enter_accepts_generated_default(self):
        p = self.run_choices([""])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PORT:39617", p.stdout)
        self.assertIn("ATTEMPTS:1", p.stdout)

    def test_option_one_accepts_random_port(self):
        p = self.run_choices(["1"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PORT:39617", p.stdout)

    def test_option_two_accepts_custom_port(self):
        p = self.run_choices(["2"], custom="41234")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PORT:41234", p.stdout)

    def test_direct_numeric_port_is_rejected(self):
        p = self.run_choices(["41234", "1"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Invalid choice", p.stdout)
        self.assertIn("PORT:39617", p.stdout)
        self.assertIn("ATTEMPTS:2", p.stdout)

    def test_invalid_custom_port_is_retried(self):
        p = self.run_choices(["2", "1"], custom="65536")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Invalid AmneziaWG UDP port", p.stdout)
        self.assertIn("PORT:39617", p.stdout)

    def test_busy_custom_port_is_retried(self):
        p = self.run_choices(["2", "1"], custom="31507")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("already in use", p.stdout)
        self.assertIn("PORT:39617", p.stdout)

    def test_exit(self):
        p = self.run_choices(["0"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("installation cancelled", p.stdout)
        self.assertNotIn("PORT:", p.stdout)


if __name__ == "__main__":
    unittest.main()
