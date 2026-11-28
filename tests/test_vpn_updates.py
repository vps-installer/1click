"""Offline regression tests for standard APT AmneziaWG updates and manual Xray Core updates."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
XRAY = ROOT / 'lib/vpn_updates.sh'
APT = ROOT / 'lib/maintenance_task.sh'
INSTALL = ROOT / 'install.sh'


class VPNUpdateTests(unittest.TestCase):
    def test_shell_syntax(self):
        for path in (XRAY, APT):
            with self.subTest(path=path.name):
                done = subprocess.run(['bash','-n',str(path)],capture_output=True,text=True)
                self.assertEqual(done.returncode,0,done.stderr)

    def test_official_xray_installer_keeps_local_state(self):
        source = XRAY.read_text()
        for token in ('--without-geodata','--no-update-service',
                      '--manually-restart-later','xray_config_is_managed',
                      'runuser -u xray','cmp -s',
                      'cp -p -- "$temp/xray.previous" "$binary"',
                      'systemctl restart xray.service'):
            self.assertIn(token, source)
        self.assertIn('if ! yesno', source)

    def test_apt_updates_amnezia_without_package_hold(self):
        text = APT.read_text()
        self.assertIn('upgrade 2>&1 | tee -a "$current"',text)
        self.assertNotIn('apt-mark hold',text)
        self.assertNotIn('apt-mark unhold',text)
        self.assertNotIn('amneziawg-dkms install',text)

    def test_no_reboot_without_awg_health_and_module(self):
        text = APT.read_text()
        first = text.index('if ! check_awg_health; then')
        reboot = text.index('systemctl reboot')
        self.assertLess(first,reboot)
        self.assertIn('if ! check_awg_reboot_ready; then',text[first:reboot])
        self.assertIn('modinfo -k "$next_kernel" amneziawg',text)
        self.assertIn('awg-geoblock-check.sh',text)
        self.assertIn('systemctl is-active --quiet "$unit"',text)
        self.assertIn('notify apt failed',text)

    def test_menu_integrates_without_duplicate_awg_updater(self):
        text = XRAY.read_text()
        self.assertIn('xray_management_menu() {',text)
        self.assertIn("say '0) Exit'",text)
        self.assertIn("'0:Exit'",text)
        p = INSTALL.read_text()
        self.assertIn('vpn_updates.sh',p)
        self.assertIn('xray_management_menu',p)
        self.assertNotIn('amnezia_management_menu',p)

    def test_xray_update_refuses_unmanaged_files_offline(self):
        # Do not touch the live host; test the refusal guard only.
        cmd = f'''
        source "{XRAY}"
        warn() {{ :; }}
        xray_config_is_managed() {{ return 1; }}
        xray_core_update
        '''
        run = subprocess.run(['bash','-c',cmd],capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)


if __name__ == '__main__':
    unittest.main()
