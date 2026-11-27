"""Profile output menus offer independent formats and Exit last."""
from pathlib import Path
import unittest

PROFILES = (Path(__file__).resolve().parents[1] / "lib" / "profiles.sh").read_text()


class ProfileOutputMenuTests(unittest.TestCase):
    def test_xray_creation_offers_independent_formats(self):
        block = PROFILES.split('  else\n    say "Output format:"', 1)[1].split(
            '\nchoose_saved_profile() {', 1
        )[0]
        self.assertIn('say "3) YAML (Clash/Mihomo proxy)"', block)
        self.assertIn('3) FORMAT=yaml;;', block)
        xray_create = block.split('if [[ "$type" == xray ]]; then', 1)[1].split('    else', 1)[0]
        self.assertNotIn('Both (link and QR)', xray_create)
        self.assertNotIn('Everything', xray_create)

    def test_no_combined_output_choices_in_any_profile_menu(self):
        block = PROFILES.split('profile_format() {', 1)[1].split(
            '\\nprofile_family() {', 1
        )[0]
        self.assertNotIn('Everything', block)
        self.assertNotIn('Both', block)
        self.assertNotIn('FORMAT=all', block)
        self.assertNotIn('FORMAT=both', block)
        for option in ('"Choose output"', '"Choose output format"'):
            prompts = [line for line in block.splitlines()
                       if 'ask_default_choice ' + option in line]
            self.assertEqual(len(prompts), 2)
            for prompt in prompts:
                self.assertIn(option + ' 1 ', prompt)
                self.assertTrue(prompt.rstrip().endswith('"0:Exit" || return 1'))

    def test_exit_is_last_for_create_and_view_formats(self):
        block = PROFILES.split('profile_format() {', 1)[1].split(
            '\nchoose_saved_profile() {', 1
        )[0]
        for marker in ('"Choose output"', '"Choose output format"'):
            lines = [line.strip() for line in block.splitlines() if
                     'ask_default_choice ' + marker in line]
            self.assertTrue(lines)
            for line in lines:
                self.assertTrue(line.endswith('"0:Exit" || return 1'), line)
        self.assertNotIn('say "0) Exit"\n    say "1)', block)
        self.assertIn('0) return 1;;', block)

    def test_menu_still_prompts_for_name_first(self):
        body = PROFILES.split('  ask "Provide name for new $type profile:"', 1)[1]
        self.assertLess(body.index('name="$REPLY"'), body.index('profile_format "$type" add'))


if __name__ == "__main__":
    unittest.main()
