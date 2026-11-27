"""Ensure nested profile menus describe their actual exit behavior."""
import pathlib
import re
import unittest

PROFILE_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "lib" / "profiles.sh"


class ProfileMenuExitTests(unittest.TestCase):
    def test_all_nested_zero_options_are_labeled_exit(self):
        source = PROFILE_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('0) Back', source)
        self.assertNotIn('0:Back', source)
        self.assertGreaterEqual(source.count('say "0) Exit"'), 5)
        self.assertGreaterEqual(source.count('"0:Exit"'), 6)

    def test_zero_exits_profile_menu_instead_of_displaying_main_menu(self):
        source = PROFILE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('    0) return;;', source)
        self.assertIn('  if [[ "$REPLY" == 0 ]]; then return 1; fi', source)

    def test_exit_is_last_in_every_printed_menu(self):
        root = PROFILE_SCRIPT.parents[1]
        for script in [root / "install.sh", *sorted((root / "lib").glob("*.sh"))]:
            source = script.read_text(encoding="utf-8")
            lines = source.splitlines()
            for index, line in enumerate(lines):
                if 'say "0) Exit"' not in line:
                    continue
                preceding = [
                    candidate.strip() for candidate in lines[max(0, index - 8):index]
                    if re.search(r'\bsay "(?:[1-9][0-9]*|\$index)\) ', candidate)
                ]
                self.assertTrue(preceding, f"{script.name}:{index + 1}: Exit has no preceding options")
                for candidate in lines[index + 1:index + 5]:
                    self.assertNotRegex(
                        candidate, r'\bsay "[1-9][0-9]*\) ',
                        f"{script.name}:{index + 1}: Exit must be the final printed choice"
                    )

    def test_reality_sni_exit_is_last_in_input_choices(self):
        vpn = (PROFILE_SCRIPT.parent / "vpn.sh").read_text(encoding="utf-8")
        self.assertIn(
            'say "1) Default SNI (open.spotify.com)"\n'
            '    say "2) Custom SNI"\n'
            '    say "0) Exit"\n'
            '    if ! ask_default_choice "Choose SNI mode" 1 '
            '"1:Default SNI" "2:Custom SNI" "0:Exit"',
            vpn
        )

    def test_saved_profile_exit_is_last_in_input_choices(self):
        source = PROFILE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            '  local -a menu_labels=()\n'
            '  for ((index=0;index<${#profiles[@]};index++)); do '
            'menu_labels+=("$((index+1)):${profiles[index]}"); done\n'
            '  menu_labels+=("0:Exit")\n'
            '  ask_choice "Profile number:" "${menu_labels[@]}"',
            source
        )


if __name__ == "__main__":
    unittest.main()
