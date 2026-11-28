"""Read-only smoke tests for installed/latest VPN version displays."""
from pathlib import Path
import subprocess
import unittest
import os
import tempfile

ROOT=Path(__file__).resolve().parents[1]
VPN=ROOT/'lib/vpn_updates.sh'


def shell(body):
    p=subprocess.run(['bash','-c',f'source "{VPN}"\nsay() {{ printf "%s\\n" "$*"; }}\nwarn() {{ printf "WARN: %s\\n" "$*"; }}\n{body}'],capture_output=True,text=True)
    return p


class VersionMenuTests(unittest.TestCase):
    def test_shell_syntax(self):
        p=subprocess.run(['bash','-n',str(VPN)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_xray_status_up_to_date(self):
        p=shell('xray_core_status 26.11.9 26.11.9')
        self.assertIn('Installed version: 26.11.9',p.stdout)
        self.assertIn('Latest release:    26.11.9',p.stdout)
        self.assertIn('Update status:     Up to date',p.stdout)

    def test_xray_status_available(self):
        p=shell('xray_core_status 26.11.9 26.11.10')
        self.assertIn('Update status:     Update available',p.stdout)

    def test_xray_failed_lookup_not_treated_as_current(self):
        p=shell("xray_core_status 26.11.9 'Unavailable (GitHub check failed)'")
        self.assertIn('Update status:     Unknown',p.stdout)
        self.assertNotIn('Up to date',p.stdout)

    def test_github_tag_parsing(self):
        p=shell('''curl() { printf '%s\\n' '{"tag_name":"v26.11.9"}'; }
xray_latest_version''')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'26.11.9')

    def test_github_invalid_response_fails(self):
        p=shell('''curl() { printf '%s\\n' '{"tag_name":"../../bad"}'; }
xray_latest_version''')
        self.assertNotEqual(p.returncode,0)

    def test_awg_candidate_versions_and_status(self):
        p=shell('''AWG_APT_FRESH=yes
dpkg-query() { echo 3.1.1; }
apt-cache() { printf '%s\\n' '  Installed: 3.1.1' '  Candidate: 3.1.2'; }
awg_package_status''')
        self.assertIn('Latest (APT): 3.1.2',p.stdout)
        self.assertIn('Update available via weekly APT',p.stdout)

    def test_awg_stale_cache_not_reported_current(self):
        p=shell('''AWG_APT_FRESH=no
dpkg-query() { echo 3.1.1; }
apt-cache() { printf '%s\\n' '  Candidate: 3.1.1'; }
awg_package_status''')
        self.assertIn('Unverified',p.stdout)
        self.assertNotIn('Version status: Up to date',p.stdout)

    def test_awg_missing_candidate_not_reported_current(self):
        p=shell('''AWG_APT_FRESH=yes
dpkg-query() { echo 3.1.1; }
apt-cache() { printf '%s\\n' '  Candidate: (none)'; }
awg_package_status''')
        self.assertIn('Unknown',p.stdout)
        self.assertNotIn('Version status: Up to date',p.stdout)

    def test_entering_awg_menu_does_not_run_install_or_ipv6(self):
        p=shell('''awg_refresh_versions() { AWG_APT_FRESH=yes; }
awg_package_status() { :; }
ask_choice() { REPLY=0; }
amnezia_installation() { echo BAD_INSTALL; }
offer_awg_ipv6() { echo BAD_IPV6; }
amnezia_management_menu''')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('AMNEZIAWG MANAGEMENT',p.stdout)
        self.assertNotIn('BAD_INSTALL',p.stdout)
        self.assertNotIn('BAD_IPV6',p.stdout)

if __name__=='__main__': unittest.main()
