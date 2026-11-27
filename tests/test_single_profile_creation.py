"""Profile creation prompts for a name before selecting output and creates one client."""
from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "lib" / "profiles.sh").read_text()


class SingleProfileCreationTests(unittest.TestCase):
    def test_no_batch_count_prompt(self):
        self.assertNotIn("How many profiles to create", SOURCE)
        self.assertNotIn("for ((i=1;i<=count;i++))", SOURCE)

    def test_profile_name_precedes_output_selection(self):
        body = SOURCE.split('  ask "Provide name for new $type profile:"', 1)[1]
        self.assertLess(body.index('name="$REPLY"'), body.index('profile_format "$type" add'))
        self.assertLess(body.index('grep -Fxq -- "$name"'), body.index('profile_format "$type" add'))
        self.assertLess(body.index('profile_format "$type" add'), body.index('run_step "Creating $type profile: $name"'))

    def test_no_creation_if_format_cancelled(self):
        self.assertIn('profile_format "$type" add || continue', SOURCE)
        self.assertIn('python3 /opt/vps-installer/lib/profile_manager.py "$type" add "$name" --format none', SOURCE)


if __name__ == "__main__":
    unittest.main()
