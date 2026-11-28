"""Offline regression: a curl/bootstrap refresh must execute the newly installed script."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

INSTALLER = (Path(__file__).resolve().parents[1] / "install.sh").read_text()


class BootstrapRefreshTests(unittest.TestCase):
    def run_bootstrap(self, mode):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "app"
            (root / "lib").mkdir(parents=True)
            (root / "install.sh").write_text("echo OLD_SCRIPT\n")
            (root / "lib/vpn.sh").write_text("echo OLD_MODULE\n")
            bindir = Path(td) / "bin"
            bindir.mkdir()
            fake_curl = bindir / "curl"
            fake_curl.write_text("""#!/usr/bin/env bash
out=''
args=("$@")
for ((i=0;i<${#args[@]};i++)); do
  if [[ "${args[i]}" == '-o' ]]; then out="${args[i+1]}"; fi
done
[[ -n "$out" ]] || exit 5
if [[ "$FAKE_CURL_MODE" == fail && "$out" == */lib/vpn.sh ]]; then exit 22; fi
if [[ "$FAKE_CURL_MODE" == invalid && "$out" == */lib/vpn.sh ]]; then
  printf 'if (\n' > "$out"
elif [[ "$out" == *.py ]]; then
  printf 'pass\n' > "$out"
elif [[ "$out" == */install.sh ]]; then
  printf 'echo NEW_SCRIPT\n' > "$out"
else
  printf ':\n' > "$out"
fi
""")
            fake_curl.chmod(0o755)
            start = INSTALLER.index("# A curl/bash bootstrap must run")
            stop = INSTALLER.index('source "$ROOT/lib/common.sh"', start)
            bootstrap = INSTALLER[start:stop]
            bootstrap = bootstrap.replace(
                'ln -sfn "$ROOT/install.sh" /usr/local/sbin/vps-installer',
                'ln -sfn "$ROOT/install.sh" "$ROOT/launcher"',
            )
            script = (
                f"ROOT={shlex.quote(str(root))}\n"
                "OWNER_REPO=vps-installer/1click\nSOURCE_REF=main\n"
                + bootstrap
            )
            environment = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                               FAKE_CURL_MODE=mode)
            run = subprocess.run(["bash", "-c", script], env=environment,
                                 text=True, capture_output=True, timeout=15)
            return run, (root / "install.sh").read_text(), (root / "lib/vpn.sh").read_text()

    def test_refreshed_script_executes_same_run(self):
        run, installer, module = self.run_bootstrap("ok")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("NEW_SCRIPT", run.stdout)
        self.assertNotIn("OLD_SCRIPT", run.stdout)
        self.assertIn("NEW_SCRIPT", installer)
        self.assertNotIn("OLD_MODULE", module)

    def test_failed_download_preserves_existing_installation(self):
        run, installer, module = self.run_bootstrap("fail")
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(installer, "echo OLD_SCRIPT\n")
        self.assertEqual(module, "echo OLD_MODULE\n")

    def test_invalid_module_preserves_existing_installation(self):
        run, installer, module = self.run_bootstrap("invalid")
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(installer, "echo OLD_SCRIPT\n")
        self.assertEqual(module, "echo OLD_MODULE\n")


if __name__ == "__main__":
    unittest.main()
