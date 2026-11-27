"""Verify fzf setup is included in the initial VPS configuration."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = (ROOT / "lib/system.sh").read_text(encoding="utf-8")
FUNCTION = "configure_fzf_bash() {" + SYSTEM.split(
    "configure_fzf_bash() {", 1
)[1].split("\ncreate_admin() {", 1)[0]


class FzfBashTests(unittest.TestCase):
    def test_installed_in_initial_apt_packages(self):
        self.assertIn('fail2ban fzf curl wget', SYSTEM)

    def test_configured_for_root_and_admin_outside_color_prompt(self):
        stage = SYSTEM.split('  create_admin "$username"', 1)[1].split(
            '  if yesno "Would you like a colorized Bash prompt?"; then', 1
        )[0]
        self.assertIn('configure_fzf_bash root; configure_fzf_bash "$username"', stage)
        self.assertIn("active after next login", stage)

    def test_bashrc_key_bindings_are_safe_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bashrc = home / ".bashrc"
            bashrc.write_text(
                "# Default Ubuntu shell settings\n"
                "# BEGIN VPS INSTALLER PROMPT\n"
                "PS1='test'\n"
                "# END VPS INSTALLER PROMPT\n",
                encoding="utf-8",
            )
            script = (
                'getent() { printf "demo:x:1000:1000::%s:/bin/bash\\n" "$TEST_HOME"; }\n'
                'chown() { :; }\n'
                'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
                + FUNCTION
                + '\nconfigure_fzf_bash demo\nconfigure_fzf_bash demo\n'
            )
            import os
            result = subprocess.run(
                ["bash", "-c", script],
                env=dict(os.environ, TEST_HOME=str(home)),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = bashrc.read_text(encoding="utf-8")
            self.assertEqual(actual.count("# BEGIN VPS INSTALLER FZF"), 1)
            self.assertEqual(actual.count("# END VPS INSTALLER FZF"), 1)
            self.assertIn(
                "source /usr/share/doc/fzf/examples/key-bindings.bash", actual
            )
            self.assertIn("if [[ $- == *i* && -r ", actual)
            self.assertIn("PS1='test'", actual)
            self.assertIn("# Default Ubuntu shell settings", actual)


if __name__ == "__main__":
    unittest.main()
