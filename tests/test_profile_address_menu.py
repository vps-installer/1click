"""Profile address selection contains exactly IPv4, IPv6 and Exit."""
from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "lib" / "profiles.sh").read_text()


class ProfileAddressMenuTests(unittest.TestCase):
    def test_address_menu_choices_and_order(self):
        menu = SOURCE.split('profile_family() {', 1)[1].split('  choice="$REPLY"', 1)[0]
        labels = [line.strip() for line in menu.splitlines()
                  if line.strip().startswith('say "') and ')' in line]
        self.assertEqual(labels, ['say "1) IPv4"', 'say "2) IPv6"', 'say "0) Exit"'])
        self.assertIn('ask_default_choice "Choose address family" 1 "1:IPv4" "2:IPv6" "0:Exit"', menu)
        self.assertNotIn('Both (IPv4 + IPv6)', menu)

    def test_only_requested_address_families_selected(self):
        block = SOURCE.split('profile_family() {', 1)[1].split('  local flag', 1)[0]
        self.assertIn('1) FAMILY=ipv4; return 0;;', block)
        self.assertIn('2) FAMILY=ipv6;;', block)
        self.assertIn('0) return 1;;', block)
        self.assertNotIn('FAMILY=both', block)


if __name__ == "__main__":
    unittest.main()
