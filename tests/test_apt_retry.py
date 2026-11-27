"""APT lock contention should be retried; unrelated package errors must not be hidden."""
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMON = (ROOT / "lib/common.sh").read_text(encoding="utf-8")
FUNCTION = "apt_retry() {" + COMMON.split("apt_retry() {", 1)[1].split("\nstate_set() {", 1)[0]


class AptLockRetryTests(unittest.TestCase):
    def execute(self, scenario):
        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            script = base / "fake-apt"
            script.write_text(
                "#!/bin/bash\n"
                'counter_file="$COUNTER_FILE"\n'
                'n=0; [[ -f "$counter_file" ]] && n="$(cat "$counter_file")"\n'
                'n=$((n+1)); printf "%s" "$n" >"$counter_file"\n'
                + (
                    'if ((n == 1)); then echo "E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 1714 (apt-get)" >&2; exit 100; fi\n'
                    'echo "APT worked"; exit 0\n'
                    if scenario == "locked"
                    else 'echo "E: Package not found" >&2; exit 100\n'
                )
            )
            script.chmod(0o700)
            shell = (
                'RUN_DIR="$TEST_RUN_DIR"\n'
                'sleep() { :; }\n'
                + FUNCTION + '\n'
                'apt_retry "$FAKE_APT"\n'
            )
            env = dict(os.environ, TEST_RUN_DIR=d,
                       COUNTER_FILE=str(base / "count"), FAKE_APT=str(script))
            result = subprocess.run(
                ["bash", "-c", shell], capture_output=True, text=True, env=env, timeout=15
            )
            count = int((base / "count").read_text())
            return result, count

    def test_retries_lock_and_then_succeeds(self):
        result, count = self.execute("locked")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 2)
        self.assertIn("APT is locked by another process", result.stdout)
        self.assertIn("APT worked", result.stdout)

    def test_other_apt_errors_fail_without_retry(self):
        result, count = self.execute("other_error")
        self.assertEqual(result.returncode, 100)
        self.assertEqual(count, 1)
        self.assertIn("Package not found", result.stdout)

    def test_apt_calls_use_retry_helper(self):
        system = (ROOT / "lib/system.sh").read_text()
        vpn = (ROOT / "lib/vpn.sh").read_text()
        self.assertIn('run_step "Update APT" apt_retry apt-get', system)
        self.assertIn('run_step "Upgrade Ubuntu" apt_retry apt-get', system)
        self.assertIn('run_step "Install core packages" apt_retry apt-get', system)
        self.assertIn('run_step "Install Amnezia PPA" apt_retry add-apt-repository', vpn)
        self.assertIn('run_step "Refresh APT" apt_retry apt-get', vpn)
        self.assertIn('run_step "Install AmneziaWG and DNS" apt_retry apt-get', vpn)


if __name__ == "__main__":
    unittest.main()
