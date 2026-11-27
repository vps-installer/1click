"""Regression tests for provider SSH public-key extraction."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lib/system.sh"


class SSHKeyImportTests(unittest.TestCase):
    def test_import_key_from_file_without_final_newline(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            keyfile = root / "testkey"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(keyfile)],
                check=True, capture_output=True
            )
            pub = keyfile.with_suffix(".pub").read_text().strip()
            source = root / "authorized_keys"
            source.write_text(pub)  # intentionally no final newline
            bash = r'''
source lib/system.sh
key=""
count=0
while IFS= read -r key || [[ -n "$key" ]]; do
  if extract_ssh_public_key "$key"; then
    count=$((count+1))
  fi
done < "$1"
[[ "$count" == 1 ]] || exit 3
printf '%s\n' "$REPLY"
'''
            run = subprocess.run(
                ["bash", "-c", bash, "test", str(source)],
                cwd=ROOT, capture_output=True, text=True
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), " ".join(pub.split()[:2]))

    def test_import_key_with_provider_restrictions(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", str(path)], check=True, capture_output=True)
            pub = path.with_suffix(".pub").read_text().strip()
            restricted = 'no-port-forwarding,no-agent-forwarding ' + pub
            shell = 'source lib/system.sh; extract_ssh_public_key "$1" && printf "%s\n" "$REPLY"'
            run = subprocess.run(["bash", "-c", shell, "test", restricted],
                                 cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), " ".join(pub.split()[:2]))

    def test_invalid_key_is_rejected(self):
        run = subprocess.run(["bash", "-c",
                             'source lib/system.sh; ! extract_ssh_public_key "ssh-ed25519 INVALID"'],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_existing_admin_keys_are_not_truncated(self):
        source = SCRIPT.read_text()
        body = source.split("configure_keys() {", 1)[1].split("\nconfigure_ssh() {", 1)[0]
        self.assertNotIn('/dev/null "$dest"', body)
        self.assertIn('>> "$dest"', body)
        self.assertIn('while IFS= read -r key || [[ -n "$key" ]]', body)


if __name__ == "__main__":
    unittest.main()
