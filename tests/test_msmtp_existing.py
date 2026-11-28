"""Read-only adoption tests for pre-existing msmtp account."""
from pathlib import Path
import importlib.util
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'lib/msmtp_probe.py'
spec = importlib.util.spec_from_file_location('msmtp_probe', MOD)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
MENU = (ROOT / 'lib/maintenance.sh').read_text()
NOTIFY = (ROOT / 'lib/maintenance_notify.sh').read_text()


class ExistingMsmtpTests(unittest.TestCase):
    def run_config(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'msmtprc'
            path.write_text(source)
            before = path.read_bytes()
            result = probe.probe(path)
            self.assertEqual(path.read_bytes(), before)
            return result

    def test_existing_simple_config(self):
        result = self.run_config('''defaults
  auth on
  tls on
  tls_starttls on
account default
 host mail.example.com
 port 587
 from status@example.com
 user status@example.com
 password SuperSecretDoNotPrint
''')
        self.assertEqual(result, {'host':'mail.example.com','port':'587','encryption':'STARTTLS','sender':'status@example.com'})

    def test_default_account_inherits_named_account(self):
        result=self.run_config('''defaults
 tls on
 tls_starttls on
account outgoing
 host smtp.example.net
 port 587
 user status@example.com
 from status@example.com
account default : outgoing
''')
        self.assertEqual(result['sender'], 'status@example.com')

    def test_other_accounts_do_not_override_default(self):
        result = self.run_config('''defaults
 tls on
account default
 host smtp.example.com
 user status@example.com
 from status@example.com
account another
 host smtp.attacker.invalid
 from stranger@example.net
 user stranger@example.net
''')
        self.assertEqual(result['host'], 'smtp.example.com')

    def test_unsafe_configs_rejected(self):
        for source in ('account default\nhost a.example.com\nfrom x@example.com\nuser y@example.com\n',
                       'include /tmp/a\naccount default\n',
                       'account first\naccount default : not-defined\n',
                       'account default\nhost mail.example.com\nfrom x@example.com\nuser x@example.com\nport -9\n'):
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory() as tmp:
                    p=Path(tmp)/'config';p.write_text(source)
                    with self.assertRaises(ValueError): probe.probe(p)

    def test_adoption_does_not_overwrite_existing_file(self):
        adopted=MENU.split('maint_smtp_use_existing() {',1)[1].split('\nmaint_smtp_configure()',1)[0]
        self.assertNotIn('rm -f "$MAINT_MSMTPRC"',adopted)
        self.assertNotIn('mv -f "$tmp" "$MAINT_MSMTPRC"',adopted)
        self.assertIn('MAINT_SOURCE=external',adopted)
        self.assertIn('MAINT_NOTIFY=no',adopted)
        self.assertIn('if maint_smtp_test; then',adopted)
        self.assertIn('MAINT_NOTIFY=yes',adopted)

    def test_external_sender_and_password_are_not_modified(self):
        addr=MENU.split('maint_smtp_addresses() {',1)[1].split('\nmaint_smtp_password()',1)[0]
        self.assertIn('maint_smtp_owned ||',addr)
        self.assertIn('External SMTP passwords must be changed outside the installer',MENU)
        self.assertIn('if maint_smtp_owned; then rm -f "$MAINT_MSMTPRC"', MENU)
        self.assertIn('External SMTP sender changed',NOTIFY)

    def test_syntax(self):
        for p in (ROOT/'lib/maintenance.sh',ROOT/'lib/maintenance_notify.sh'):
            self.assertEqual(subprocess.run(['bash','-n',str(p)]).returncode,0)

if __name__ == '__main__':
    unittest.main()
