"""Non-destructive tests of client rename/delete primitives and rollback."""
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "lib"))
import profile_edit as edit
import profile_manager as pm


def xray_config():
    return {"inbounds": [{"port": 443, "protocol": "vless",
                          "streamSettings": {"network": "xhttp", "security": "reality"},
                          "settings": {"clients": [{"email": "ClientA", "id": "client-id"},
                                                   {"email": "ClientB", "id": "other-id"}]}}]}


class ProfileEditTests(unittest.TestCase):
    def test_awg_peer_lookup_requires_public_key_match(self):
        a, b = "A" * 43 + "=", "B" * 43 + "="
        text = ("[Interface]\nPrivateKey = key\n\n# ClientA\n[Peer]\nPublicKey = " + a +
                "\nAllowedIPs = 10.88.32.2/32\n\n# ClientB\n[Peer]\nPublicKey = " + b +
                "\nAllowedIPs = 10.88.32.3/32\n")
        start, end, block = edit.awg_peer_block(text, a)
        self.assertIn("10.88.32.2/32", block)
        self.assertNotIn(b, block)
        updated = text[:start] + text[end:]
        self.assertIn(b, updated)
        self.assertIn("# ClientB", updated)
        with self.assertRaisesRegex(RuntimeError, "uniquely"):
            edit.awg_peer_block(text, "Z" * 43 + "=")

    def test_rename_xray_preserves_uuids_and_other_clients(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            xray = root / "config.json"
            xray.write_text(json.dumps(xray_config()))
            secrets = root / "secrets"
            secrets.mkdir()
            (secrets / "ClientA.uuid").write_text("client-id\n")
            lock = root / "xray.lock"
            original_open = open
            def test_open(path, *args, **kwargs):
                return original_open(lock if path == "/run/vps-installer/xray-profiles.lock"
                                     else path, *args, **kwargs)
            def fake_atomic(path, data, old):
                edit.write_atomic(path, json.dumps(data).encode())
            with patch.object(pm, "XRAY", xray):
                with patch.object(pm, "XRAY_FILES", secrets):
                    with patch.object(edit, "open", side_effect=test_open, create=True):
                        with patch.object(pm, "atomic_json", side_effect=fake_atomic):
                            with patch.object(pm, "command", return_value=""):
                                edit.xray_change("rename", "ClientA", "Phone")
            clients = json.loads(xray.read_text())["inbounds"][0]["settings"]["clients"]
            self.assertEqual(clients, [{"email": "Phone", "id": "client-id"},
                                       {"email": "ClientB", "id": "other-id"}])
            self.assertTrue((secrets / "Phone.uuid").is_file())
            self.assertFalse((secrets / "ClientA.uuid").exists())

    def test_delete_xray_removes_only_selected_uuid_and_archives_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            xray = root / "config.json"
            xray.write_text(json.dumps(xray_config()))
            secrets = root / "secrets"
            secrets.mkdir()
            (secrets / "ClientA.uuid").write_text("client-id\n")
            lock = root / "xray.lock"
            original_open = open
            def test_open(path, *args, **kwargs):
                return original_open(lock if path == "/run/vps-installer/xray-profiles.lock"
                                     else path, *args, **kwargs)
            def fake_atomic(path, data, old):
                edit.write_atomic(path, json.dumps(data).encode())
            with patch.object(pm, "XRAY", xray):
                with patch.object(pm, "XRAY_FILES", secrets):
                    with patch.object(edit, "open", side_effect=test_open, create=True):
                        with patch.object(pm, "atomic_json", side_effect=fake_atomic):
                            with patch.object(pm, "command", return_value=""):
                                edit.xray_change("delete", "ClientA", None)
            clients = json.loads(xray.read_text())["inbounds"][0]["settings"]["clients"]
            self.assertEqual(clients, [{"email": "ClientB", "id": "other-id"}])
            self.assertFalse((secrets / "ClientA.uuid").exists())
            self.assertEqual(len(list((secrets / ".revoked").glob("*ClientA.uuid"))), 1)


if __name__ == "__main__":
    unittest.main()
