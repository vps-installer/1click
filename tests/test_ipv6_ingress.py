"""IPv6 ingress and profile variants preserve original IPv4 configuration and keys."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import ipv6_support as ipv6
import profile_manager as pm
import profile_edit as editor


def sample():
    return {"inbounds": [{
        "tag": "vless-xhttp-reality",
        "listen": "8.8.8.8",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": [{"email": "Phone", "id": "00000000-0000-4000-8000-000000000001"}], "decryption": "none"},
        "streamSettings": {
            "network": "xhttp", "security": "reality",
            "realitySettings": {"privateKey": "SERVER_PRIVATE", "serverNames": ["example.org"], "shortIds": ["12345678"]},
            "xhttpSettings": {"path": "/test", "mode": "stream-one"}
        }
    }],
    "dns": {"queryStrategy": "UseIPv4"},
    "outbounds": [{"tag": "direct", "streamSettings": {"sockopt": {"domainStrategy": "ForceIPv4"}}}, {"tag": "block", "protocol": "blackhole"}],
    "routing": {"rules": [{"type": "field", "ip": ["geoip:ru"], "outboundTag": "block"}]}
    }


class IPv6IngressTests(unittest.TestCase):
    def test_xray_ipv6_probe_uses_real_ipv6_socket(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch.object(ipv6.socket, "socket", return_value=connection) as sock:
            ipv6.wait_for_ipv6_listener("2606:4700:4700::1234", 443, retries=1)
        sock.assert_called_once_with(ipv6.socket.AF_INET6, ipv6.socket.SOCK_STREAM)
        connection.connect.assert_called_once_with(("2606:4700:4700::1234", 443, 0, 0))

    def test_xray_ipv6_probe_failure_preserves_fail_closed_behavior(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.connect.side_effect = OSError("network unavailable")
        with patch.object(ipv6.socket, "socket", return_value=connection):
            with patch.object(ipv6.time, "sleep") as pause:
                with self.assertRaisesRegex(RuntimeError, "original config will be restored"):
                    ipv6.wait_for_ipv6_listener("2606:4700:4700::1234", 443,
                                                retries=2, interval=0.01)
        self.assertEqual(pause.call_count, 1)
        self.assertEqual(connection.connect.call_count, 2)

    def test_detect_assigned_global_ipv6(self):
        def fake_run(*args):
            if args[3] == "route":
                return json.dumps([{"dev": "eth0", "prefsrc": "2606:4700:4700::1234"}])
            return json.dumps([{"addr_info": [{"local": "2606:4700:4700::1234", "scope": "global", "flags": []}]}])
        with patch.object(ipv6, "run", side_effect=fake_run):
            self.assertEqual(ipv6.detect_ipv6(), "2606:4700:4700::1234")

    def test_reject_tentative_address(self):
        def fake_run(*args):
            if args[3] == "route":
                return json.dumps([{"dev": "eth0", "src": "2606:4700:4700::1234"}])
            return json.dumps([{"addr_info": [{"local": "2606:4700:4700::1234",
                                              "scope": "global", "flags": ["tentative"]}]}])
        with patch.object(ipv6, "run", side_effect=fake_run):
            self.assertEqual(ipv6.detect_ipv6(), "")

    def test_xray_ipv6_mirror_preserves_original_and_ipv4_only_outbound(self):
        data = sample()
        original = copy.deepcopy(data)
        self.assertTrue(ipv6.add_ipv6_mirror(data, "2606:4700:4700::1234"))
        self.assertEqual(data["inbounds"][0], original["inbounds"][0])
        self.assertEqual(data["outbounds"], original["outbounds"])
        self.assertEqual(data["dns"], original["dns"])
        self.assertEqual(data["routing"]["rules"][0]["ip"], ["::/0"])
        self.assertEqual(data["routing"]["rules"][1:], original["routing"]["rules"])
        self.assertFalse(ipv6.add_ipv6_mirror(data, "2606:4700:4700::1234"))
        self.assertEqual(len(pm.xray_inbounds(data)), 2)
        self.assertEqual(data["inbounds"][0]["settings"]["clients"],
                         data["inbounds"][1]["settings"]["clients"])
        self.assertTrue(data["inbounds"][1]["streamSettings"]["sockopt"]["v6only"])

    def test_xray_mismatched_mirror_fails_closed(self):
        data = sample()
        ipv6.add_ipv6_mirror(data, "2606:4700:4700::1234")
        data["inbounds"][1]["settings"]["clients"][0]["id"] = "DIFFERENT"
        with self.assertRaisesRegex(RuntimeError, "differ"):
            pm.xray_inbounds(data)

    def test_ipv6_link_correctly_brackets_ipv6_endpoint(self):
        data = sample()
        ipv6.add_ipv6_mirror(data, "2606:4700:4700::1234")
        with patch.object(pm, "public_ipv6", return_value="2606:4700:4700::1234"):
            with patch.object(pm, "command", return_value="PublicKey: " + "X" * 43):
                link = pm.xray_link(data, "Phone", "ipv6")
                yaml = pm.xray_yaml(data, "Phone", "ipv6")
        self.assertIn("@[2606:4700:4700::1234]:443", link)
        self.assertTrue(link.endswith("#Phone-IPv6"))
        self.assertIn('- name: Phone-IPv6\n  type: vless\n  server: "2606:4700:4700::1234"', yaml)
        self.assertIn("    support-x25519mlkem768: true\n", yaml)

    def test_awg_variant_does_not_change_existing_file_or_client_key(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "Phone.conf").write_text(
                "[Interface]\nPrivateKey = ORIGINAL_CLIENT_KEY\n"
                "Address = 10.88.32.2/32\nDNS = 10.88.32.1\n\n"
                "[Peer]\nPublicKey = SERVER_PUBLIC\nEndpoint = 8.8.8.8:43321\n"
                "AllowedIPs = 0.0.0.0/0, ::/0\n")
            server = base / "awg0.conf"
            server.write_text("[Interface]\nListenPort = 43321\nPrivateKey = PRIVATE\n")
            before = (base / "Phone.conf").read_bytes()
            with patch.object(pm, "AWG_FILES", base), patch.object(pm, "AWG", server):
                with patch.object(pm, "awg_endpoint", return_value="[2606:4700:4700::1234]"):
                    result = pm.awg_client_config("Phone", "ipv6")
                self.assertIn("Endpoint = [2606:4700:4700::1234]:43321", result)
                self.assertIn("PrivateKey = ORIGINAL_CLIENT_KEY", result)
                self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", result)
                self.assertEqual((base / "Phone.conf").read_bytes(), before)

    def test_profile_manager_exposes_both_families_without_creating_new_client(self):
        data = sample()
        ipv6.add_ipv6_mirror(data, "2606:4700:4700::1234")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "config.json"
            target.write_text(json.dumps(data))
            with patch.object(pm, "XRAY", target):
                with patch.object(pm, "public_ipv6", return_value="2606:4700:4700::1234"):
                    with patch.object(pm, "command", return_value="PublicKey: " + "Z" * 43):
                        with redirect_stdout(StringIO()) as out:
                            pm.get_profile("xray", "Phone", "text", "both")
            self.assertIn("@8.8.8.8:443", out.getvalue())
            self.assertIn("@[2606:4700:4700::1234]:443", out.getvalue())
            self.assertEqual(json.loads(target.read_text()), data)

    def test_xray_rename_and_delete_update_both_inbounds(self):
        data = sample()
        ipv6.add_ipv6_mirror(data, "2606:4700:4700::1234")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_file = base / "config.json"
            config_file.write_text(json.dumps(data))
            (base / "Phone.uuid").write_text("00000000-0000-4000-8000-000000000001\n")
            (base / "Phone.ipv6.png").write_bytes(b"existing-qr")
            def fake_atomic(path, data, original):
                path.write_text(json.dumps(data))
            def test_open(path, mode="r"):
                if path == "/run/vps-installer/xray-profiles.lock":
                    path = base / "profile.lock"
                return open(path, mode)
            with patch.object(pm, "XRAY", config_file):
                with patch.object(pm, "XRAY_FILES", base):
                    with patch.object(pm, "atomic_json", side_effect=fake_atomic):
                        with patch.object(pm, "command", return_value=""):
                            with patch.object(editor, "open", side_effect=test_open, create=True):
                                editor.xray_change("rename", "Phone", "Tablet")
                            renamed = json.loads(config_file.read_text())
                            for inbound in renamed["inbounds"]:
                                self.assertEqual(inbound["settings"]["clients"][0]["email"], "Tablet")
                                self.assertEqual(inbound["settings"]["clients"][0]["id"],
                                                 "00000000-0000-4000-8000-000000000001")
                            self.assertTrue((base / "Tablet.ipv6.png").is_file())
                            self.assertFalse((base / "Phone.ipv6.png").exists())
                            with patch.object(editor, "open", side_effect=test_open, create=True):
                                editor.xray_change("delete", "Tablet", None)
                            removed = json.loads(config_file.read_text())
                            for inbound in removed["inbounds"]:
                                self.assertEqual(inbound["settings"]["clients"], [])
                            self.assertFalse((base / "Tablet.uuid").exists())



if __name__ == "__main__":
    unittest.main()
