"""Verify the final launcher notice is omitted only after successful fresh setup."""
import pathlib
import subprocess
import unittest

INSTALLER = pathlib.Path(__file__).resolve().parents[1] / "install.sh"


class ExitFooterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = INSTALLER.read_text()
        cls.footer_source = source.split("installer_exit_footer() {", 1)[1].split(
            "\ntrap 'installer_exit_footer", 1
        )[0]
        cls.footer_source = "installer_exit_footer() {" + cls.footer_source
        cls.installer_source = source

    def render_footer(self, status, suppress):
        program = (
            self.footer_source + "\n" +
            f"SUPPRESS_LOCAL_FOOTER_ON_FRESH_INSTALL={suppress}\n" +
            f"installer_exit_footer {status}\n"
        )
        process = subprocess.run(
            ["bash", "-c", program], capture_output=True, text=True, check=True
        )
        return process.stdout + process.stderr

    def test_successful_fresh_install_does_not_duplicate_reconnect_notice(self):
        self.assertEqual(self.render_footer(0, 1), "")

    def test_normal_exit_still_shows_local_launcher(self):
        message = self.render_footer(0, 0)
        self.assertIn("LOCAL VPS INSTALLER", message)
        self.assertIn("sudo vps-installer", message)

    def test_failure_still_shows_local_launcher(self):
        message = self.render_footer(1, 1)
        self.assertIn("LOCAL VPS INSTALLER", message)
        self.assertIn("did not finish successfully", message)

    def test_main_menu_uses_four_requested_options(self):
        for label in (
            "1) VPS defence and customization",
            "2) Xray",
            "3) AmneziaWG",
            "4) Manage clients profiles",
            "0) Exit",
        ):
            self.assertIn(label, self.installer_source)

    def test_operations_return_to_main_menu(self):
        self.assertIn("1) vps_defence_customization", self.installer_source)
        self.assertIn("2) xray_installation", self.installer_source)
        self.assertIn("3) amnezia_installation", self.installer_source)
        self.assertIn("4) profile_menu", self.installer_source)
        self.assertIn("0) exit 0", self.installer_source)


if __name__ == "__main__":
    unittest.main()
