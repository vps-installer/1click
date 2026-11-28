"""Offline safety tests for the optional automatic maintenance section."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
MENU = (ROOT / 'lib/maintenance.sh').read_text()
GEO = (ROOT / 'lib/maintenance_geodata.py').read_text()
APT = (ROOT / 'lib/maintenance_task.sh').read_text()
NOTIFY = (ROOT / 'lib/maintenance_notify.sh').read_text()
INSTALL = (ROOT / 'install.sh').read_text() if (ROOT / 'install.sh').exists() else (ROOT / 'patches/install.sh.patch').read_text()


class MaintenanceTests(unittest.TestCase):
    def test_syntax(self):
        for path in (ROOT / 'lib').glob('*.sh'):
            with self.subTest(path=path.name):
                result = subprocess.run(['bash', '-n', str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(['python3', '-m', 'py_compile', str(ROOT/'lib/maintenance_geodata.py')])
        self.assertEqual(result.returncode, 0)

    def test_exit_is_last_in_static_menus(self):
        lines = MENU.splitlines()
        for i, line in enumerate(lines):
            if "say '0) Exit'" not in line:
                continue
            for subsequent in lines[i+1:i+4]:
                self.assertNotRegex(subsequent, r"say '[1-9][0-9]*\) ")

    def test_ubuntu_is_combined_without_mode_choice(self):
        self.assertNotIn('Select update mode', MENU)
        self.assertIn('apt-daily-upgrade.timer', MENU)
        self.assertIn('vps-maintenance-apt.timer', MENU)
        self.assertIn('Automatic-Reboot "false"', MENU)
        self.assertIn('52-vps-maintenance-security', MENU)

    def test_existing_awg_timer_is_preserved(self):
        self.assertIn('awg-geoblock-update.timer', MENU)
        self.assertIn('awg-geoblock-update.service.d', MENU)
        self.assertNotIn('cat > "$MAINT_UNITS/awg-geoblock-update.timer"', MENU)

    def test_sender_is_username(self):
        self.assertIn('Sender / SMTP username:', MENU)
        self.assertIn('from $sender', MENU)
        self.assertIn('user $sender', MENU)
        self.assertIn('passwordeval cat', MENU)
        self.assertIn('chmod 600 "$tmp"', MENU)
        self.assertIn('MAINT_RECIPIENT', MENU)

    def test_geodata_has_candidate_validation_and_rollback(self):
        self.assertIn('XRAY_LOCATION_ASSET=', GEO)
        self.assertIn('runuser', GEO)
        self.assertIn("'-test'", GEO)
        self.assertIn('os.replace(previous, old[name])', GEO)
        self.assertIn('systemctl', GEO)

    def test_apt_lock_and_reboot_preflight(self):
        self.assertIn('flock -n 9', APT)
        self.assertIn('modinfo -k "$next_kernel" amneziawg', APT)
        self.assertIn('boot-check)', APT)
        self.assertIn('MAINT_AUTO_REBOOT:-no', APT)

    def test_notifications_disabled_without_credentials(self):
        self.assertIn('MAINT_NOTIFY:-no', NOTIFY)
        self.assertIn('/usr/sbin/sendmail', NOTIFY)

    def test_installer_bootstraps_all_files(self):
        for name in ('maintenance.sh', 'maintenance_geodata.py', 'maintenance_task.sh', 'maintenance_notify.sh'):
            self.assertIn(name, INSTALL)
        self.assertIn('5) Automatic maintenance', INSTALL)
        self.assertIn('5) automatic_maintenance_menu', INSTALL)


if __name__ == '__main__':
    unittest.main()

class MaintenanceBehaviorTests(unittest.TestCase):
    def test_settings_round_trip_and_permissions(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'settings.env'
            commands = f'''source {ROOT / "lib/maintenance.sh"!s}
MAINT_DIR={folder!r}
MAINT_CONFIG={str(config)!r}
MAINT_SENDER='status@example.com'
MAINT_RECIPIENT='receiver@example.com'
MAINT_NOTIFY=yes
MAINT_AUTO_REBOOT=no
maint_save
MAINT_SENDER=''
MAINT_RECIPIENT=''
maint_load
printf '%s|%s|%s|%s\\n' "$MAINT_SENDER" "$MAINT_RECIPIENT" "$MAINT_NOTIFY" "$MAINT_AUTO_REBOOT"
'''
            result = subprocess.run(['bash','-c',commands],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('status@example.com|receiver@example.com|yes|no',result.stdout)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_geodata_rollback_restores_both_files(self):
        import sys
        import tempfile
        from unittest.mock import patch
        sys.path.insert(0,str(ROOT / 'lib'))
        import maintenance_geodata as gd
        with tempfile.TemporaryDirectory() as folder:
            assets=Path(folder)
            cfg=assets/'config.json'
            cfg.write_text('{}')
            original={'geoip.dat':b'ip-old'*1024, 'geosite.dat':b'site-old'*1024}
            for name,data in original.items(): (assets/name).write_bytes(data)
            restarts=[0]
            def run(*args,**kwargs):
                if 'restart' in args:
                    restarts[0]+=1
                    if restarts[0]==1:
                        raise subprocess.CalledProcessError(1,args)
                return None
            def download(name,path): path.write_bytes(b'changed-'+name.encode())
            with patch.object(gd,'ASSETS',assets), patch.object(gd,'CONFIG',cfg), \
                 patch.object(gd,'run',side_effect=run), \
                 patch.object(gd,'download',side_effect=download), \
                 patch.object(gd,'as_xray'), patch.object(gd,'notify'), \
                 patch.object(gd.os,'geteuid',return_value=0), \
                 patch.object(gd.os,'chown'), \
                 patch.object(gd.pwd,'getpwnam',return_value=type('U',(),{'pw_gid':0})()):
                with self.assertRaises(RuntimeError): gd.main()
            self.assertEqual(restarts[0],2)
            for name,data in original.items():
                self.assertEqual((assets/name).read_bytes(),data)

    def test_geodata_unchanged_skips_restart(self):
        import sys
        import tempfile
        from unittest.mock import patch
        sys.path.insert(0,str(ROOT / 'lib'))
        import maintenance_geodata as gd
        with tempfile.TemporaryDirectory() as folder:
            assets=Path(folder)
            cfg=assets/'config.json';cfg.write_text('{}')
            for name in ('geoip.dat','geosite.dat'): (assets/name).write_bytes(b'same'*2048)
            def download(name,path): path.write_bytes((assets/name).read_bytes())
            calls=[]
            with patch.object(gd,'ASSETS',assets), patch.object(gd,'CONFIG',cfg), \
                 patch.object(gd,'run',side_effect=lambda *a,**kw:calls.append(a)), \
                 patch.object(gd,'download',side_effect=download), \
                 patch.object(gd.os,'geteuid',return_value=0):
                gd.main()
            self.assertFalse(any('restart' in arg for arg in calls))
