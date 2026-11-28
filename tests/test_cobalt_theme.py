"""Presentation-layer regressions: no service/network changes or menu behavior changes."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]

def shell(body, *, color='never', no_color=False):
    script = f'''exec 4>&1
source "{BASE / 'lib/ui.sh'}"
{body}
'''
    env = os.environ.copy()
    env['VPS_UI_COLOR'] = color
    if no_color:
        env['NO_COLOR'] = '1'
    else:
        env.pop('NO_COLOR', None)
    run = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=env)
    return run


class RenderingTests(unittest.TestCase):
    def test_syntax(self):
        for path in (BASE/'lib').glob('*.sh'):
            with self.subTest(path=path.name):
                done=subprocess.run(['bash','-n',str(path)],capture_output=True,text=True)
                self.assertEqual(done.returncode,0,done.stderr)

    def test_menu_sections_in_plain_mode(self):
        p=shell('''say "UNIVERSAL VPS INSTALLER"
say "1) VPS defence and customization"
say "0) Exit"
say "AUTOMATIC MAINTENANCE"
say "Xray GeoData: enabled"
say "Ubuntu updates: security=enabled, weekly=enabled"
say "Automatic reboot: no"
say "1) Xray GeoData updates"
say "0) Exit"
''')
        self.assertEqual(p.returncode,0,p.stderr)
        for part in ('VPS INSTALLER / MAIN MENU','VPS INSTALLER / AUTOMATIC MAINTENANCE',
                     'SYSTEM STATUS','ACTIONS', 'Xray GeoData', 'enabled', 'Automatic reboot',
                     '0  Exit'):
            self.assertIn(part,p.stdout)
        self.assertNotIn('\x1b[',p.stdout)
        self.assertEqual(p.stdout.count('SYSTEM STATUS'),1)

    def test_cobalt_color_and_reset(self):
        p=shell('''say "AUTOMATIC MAINTENANCE"
say "Email notifications: yes"
say "Automatic reboot: no"
say "2) Ubuntu updates"
say "0) Exit"
warn "Temporarily unavailable"
ok "Configuration saved"
''',color='always')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('\x1b[',p.stdout)
        self.assertIn('Email notifications',p.stdout)
        self.assertIn('[WARN]',p.stdout)
        self.assertIn('[OK]',p.stdout)
        self.assertIn('\x1b[0m',p.stdout)

    def test_no_color_wins_even_with_force_color(self):
        p=shell('say "1) Test"; warn "Stop"',color='always',no_color=True)
        self.assertNotIn('\x1b[',p.stdout)

    def test_no_change_to_selected_value(self):
        p=shell('say "Selected: 2 (Update Core)"')
        self.assertIn('Selected: 2 (Update Core)',p.stdout)

    def test_secret_does_not_get_interpolated(self):
        p=shell('''key='PRIVATE_SECRET_SHOULD_STAY_HIDDEN'
say "1) Configure SMTP"''')
        self.assertNotIn('PRIVATE_SECRET',p.stdout)

    def test_nested_menu_title(self):
        p=shell('''ui_header "PROFILE OUTPUT / XRAY"
say "1) Connection settings"
say "0) Exit"''')
        self.assertIn('PROFILE OUTPUT / XRAY',p.stdout)
        self.assertIn('0  Exit',p.stdout)

    def test_dynamic_profile_number(self):
        p=shell('say "12) my_android_profile"')
        self.assertIn('12  my_android_profile',p.stdout)


class IntegrationTests(unittest.TestCase):
    def test_ui_is_loaded_after_common(self):
        src=(BASE/'install.sh').read_text()
        self.assertIn('INSTALLER_MODULES=(ui.sh common.sh system.sh',src)
        self.assertIn('source "$ROOT/lib/ui.sh"',src)
        self.assertLess(src.index('source "$ROOT/lib/common.sh"'),src.index('source "$ROOT/lib/ui.sh"'))
        self.assertLess(src.index('source "$ROOT/lib/ui.sh"'),src.index('source "$ROOT/lib/system.sh"'))

    def test_all_profile_helpers_receive_titles(self):
        src=(BASE/'lib/profiles.sh').read_text()
        for title in ('PROFILE OUTPUT / ${type^^}', 'CONNECTION ADDRESS / ${type^^}',
                      'SAVED PROFILES', 'MANAGE CLIENT PROFILES', 'CLIENT PROFILES / ${type^^}'):
            self.assertIn('ui_header "'+title+'"',src)

    def test_maintenance_wizards_receive_titles(self):
        src=(BASE/'lib/maintenance.sh').read_text()
        for title in ('UPDATE SCHEDULE / ${title^^}', 'EMAIL ADDRESSES', 'SMTP CONFIGURATION'):
            self.assertIn('ui_header "'+title+'"',src)

    def test_navigation_dispatch_unchanged(self):
        src=(BASE/'install.sh').read_text()
        for action in ('1) vps_defence_customization', '2) xray_management_menu',
                       '3) amnezia_management_menu', '4) profile_menu',
                       '5) automatic_maintenance_menu', '0) exit 0'):
            self.assertIn(action,src)

    def test_no_service_operations_in_ui(self):
        src=(BASE/'lib/ui.sh').read_text()
        for forbidden in ('systemctl ', 'apt-get ', 'nft ', 'ufw ', 'git push', 'curl '):
            self.assertNotIn(forbidden,src)


if __name__ == '__main__':
    unittest.main()
