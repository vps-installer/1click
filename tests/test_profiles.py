"""Non-destructive tests for configuration parsing and link generation."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "lib" / "profile_manager.py"
spec = importlib.util.spec_from_file_location("profile_manager", MODULE_PATH)
profile_manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile_manager)


def config(port=443):
    return {
        "inbounds": [{
            "tag": "test-inbound",
            "protocol": "vless",
            "port": port,
            "listen": "203.0.113.10",
            "settings": {"clients": [{"email": "Phone", "id": "00000000-0000-4000-8000-000000000001"}]},
            "streamSettings": {
                "network": "xhttp", "security": "reality",
                "realitySettings": {
                    "privateKey": "dummy-private",
                    "serverNames": ["open.spotify.com"],
                    "shortIds": ["aabbccdd"]
                },
                "xhttpSettings": {"path": "/foo", "mode": "stream-one"}
            }
        }]
    }


class ProfileTests(unittest.TestCase):
    def test_missing_installer_state_is_accepted_for_legacy_vps(self):
        from pathlib import Path
        with patch.object(profile_manager, "STATE", Path("/nonexistent/vps-installer-settings-test.json")):
            self.assertEqual(profile_manager.state(), {})

    def test_awg_endpoint_uses_public_interface_if_installer_state_missing(self):
        from pathlib import Path
        route = "1.1.1.1 via 203.0.113.1 dev eth0 src 8.8.4.4 uid 0\n    cache"
        with patch.object(profile_manager, "STATE", Path("/nonexistent/vps-installer-settings-test.json")):
            with patch.object(profile_manager, "command", return_value=route) as cmd:
                self.assertEqual(profile_manager.awg_endpoint(), "8.8.4.4")
        cmd.assert_called_once_with("ip", "-4", "route", "get", "1.1.1.1")

    def test_awg_endpoint_prefers_recorded_public_ipv4(self):
        with patch.object(profile_manager, "state", return_value={"PUBLIC_IPV4": "8.8.8.8"}):
            with patch.object(profile_manager, "command") as cmd:
                self.assertEqual(profile_manager.awg_endpoint(), "8.8.8.8")
        cmd.assert_not_called()

    def test_awg_endpoint_does_not_write_private_ip_to_profile(self):
        for source in (
            "1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.3 uid 0",
            "1.1.1.1 via 192.168.1.1 dev ens3 src 192.168.1.4 uid 0",
        ):
            with self.subTest(source=source):
                with patch.object(profile_manager, "state", return_value={}):
                    with patch.object(profile_manager, "command", return_value=source):
                        with self.assertRaisesRegex(RuntimeError, "possibly NAT"):
                            profile_manager.awg_endpoint()

    def test_xray_wildcard_listener_falls_back_to_public_route_source(self):
        conf = config()
        conf["inbounds"][0]["listen"] = "0.0.0.0"
        with patch.object(profile_manager, "state", return_value={}):
            with patch.object(profile_manager, "command",
                              side_effect=["PublicKey: ABC_def123",
                                           "1.1.1.1 via 8.8.8.1 dev eth0 src 8.8.4.4 uid 0"]):
                link = profile_manager.xray_link(conf, "Phone")
        self.assertIn("@8.8.4.4:443", link)

    def test_inbound_matches_numeric_port(self):
        self.assertEqual(profile_manager.xray_inbound(config())["tag"], "test-inbound")

    def test_inbound_matches_string_port(self):
        self.assertEqual(profile_manager.xray_inbound(config("443"))["tag"], "test-inbound")

    def test_inbound_error_does_not_show_private_key(self):
        broken = config()
        broken["inbounds"][0]["streamSettings"]["network"] = "tcp"
        with self.assertRaisesRegex(RuntimeError, "Expected one") as e:
            profile_manager.xray_inbound(broken)
        self.assertNotIn("dummy-private", str(e.exception))

    def test_vless_link_from_actual_config(self):
        with patch.object(profile_manager, "command", return_value="PublicKey: ABC_def123"):
            link = profile_manager.xray_link(config(), "Phone")
        self.assertTrue(link.startswith("vless://00000000-0000-4000-8000-000000000001@203.0.113.10:443"))
        self.assertIn("security=reality", link)
        self.assertIn("type=xhttp", link)
        self.assertIn("pbk=ABC_def123", link)
        self.assertIn("path=%2Ffoo", link)
        self.assertTrue(link.endswith("#Phone"))

    def test_xray_client_key_labels(self):
        key = "A" * 43
        samples = [
            f"Public key: {key}",
            f"PublicKey: {key}",
            f"Password: {key}",
            f"PrivateKey: {'Z'*43}\nPassword (PublicKey): {key}\nHash32: {'Y'*43}",
            f"PrivateKey: {'Z'*43}\nPassword (PublicKey): {key}",
        ]
        for sample in samples:
            with self.subTest(sample=sample.splitlines()[-1].split(":")[0]):
                self.assertEqual(profile_manager.reality_client_key(sample), key)

    def test_xray_rejects_hash32_as_public_key(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot find REALITY client key"):
            profile_manager.reality_client_key("PrivateKey: " + "Z" * 43 + "\nHash32: " + "Y" * 43)

    def test_vless_link_with_current_xray_password_label(self):
        with patch.object(profile_manager, "command", return_value="PrivateKey: " + "Z"*43 + "\nPassword (PublicKey): " + "A"*43 + "\nHash32: " + "Y"*43):
            url = profile_manager.xray_link(config(), "Phone")
        self.assertIn("pbk=" + "A"*43, url)

    def test_saved_vless_details_show_current_values(self):
        from io import StringIO
        from contextlib import redirect_stdout
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps(config()))
            with patch.object(profile_manager, "XRAY", path):
                with patch.object(profile_manager, "command", return_value="Password (PublicKey): " + "B"*43):
                    with redirect_stdout(StringIO()) as output:
                        profile_manager.connection_details("xray", "Phone")
        details = output.getvalue()
        self.assertIn("UUID: 00000000-0000-4000-8000-000000000001", details)
        self.assertIn("Server: 203.0.113.10", details)
        self.assertIn("REALITY public key: " + "B"*43, details)
        self.assertIn("XHTTP path: /foo", details)
        self.assertNotIn("dummy-private", details)

    def test_saved_awg_details_show_client_private_key(self):
        from io import StringIO
        from contextlib import redirect_stdout
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            conf = Path(td) / "Moto.conf"
            conf.write_text("[Interface]\nPrivateKey = secret-key-value\nAddress = 10.88.32.2/32\nDNS = 10.88.32.1\nMTU = 1280\nJc = 5\nJmin = 64\nJmax = 190\n[Peer]\nEndpoint = 203.0.113.10:43321\nPublicKey = server-pub\nAllowedIPs = 0.0.0.0/0\n")
            with patch.object(profile_manager, "AWG_FILES", Path(td)):
                with redirect_stdout(StringIO()) as output:
                    profile_manager.connection_details("awg", "Moto")
            details = output.getvalue()
            self.assertIn("Endpoint: 203.0.113.10:43321", details)
            self.assertIn("Client address: 10.88.32.2/32", details)
            self.assertIn("Private key: secret-key-value", details)
            self.assertNotIn("Private key: (hidden", details)

    def test_compact_awg_qr_preserves_all_configuration_values(self):
        original = (
            "# comment is not needed for importing\n"
            "[Interface]\n"
            "PrivateKey = CLIENT_KEY\n"
            "Address = 10.88.32.2/32\n"
            "DNS = 10.88.32.1\n"
            "MTU = 1280\n"
            "HeaderProtectionKey = HEAD_KEY\n"
            "Jc = 5\n\n"
            "[Peer]\n"
            "PublicKey = SERVER_KEY\n"
            "Endpoint = 203.0.113.10:43321\n"
            "AllowedIPs = 0.0.0.0/0, ::/0\n"
            "PersistentKeepalive = 25\n"
        )
        compact = profile_manager.compact_awg_qr_payload(original)
        self.assertLess(len(compact.encode()), len(original.encode()))
        self.assertNotIn(" = ", compact)
        self.assertNotIn("# comment", compact)
        def parse(data):
            section = None
            parsed = {}
            for line in data.splitlines():
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    section = line
                else:
                    key, value = line.split("=", 1)
                    parsed[(section, key.strip())] = value.strip()
            return parsed
        self.assertEqual(parse(compact), parse(original))
        self.assertIn("PrivateKey=CLIENT_KEY", compact)
        self.assertIn("HeaderProtectionKey=HEAD_KEY", compact)
        self.assertIn("AllowedIPs=0.0.0.0/0, ::/0", compact)

    def test_compact_awg_rejects_unknown_line_instead_of_dropping(self):
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            profile_manager.compact_awg_qr_payload("[Interface]\nUnknown row\n[Peer]\n")

    def test_awg_qr_uses_same_compact_payload_for_terminal_and_png(self):
        from io import StringIO
        from contextlib import redirect_stdout
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            p = Path(td) / "Moto.conf"
            p.write_text("[Interface]\nPrivateKey = CLIENT_KEY\n\n[Peer]\nPublicKey = SERVER_KEY\n")
            png_payloads = []
            def fake_png(kind, name, payload):
                png_payloads.append(payload)
                return Path(td) / "Moto.png"
            with patch.object(profile_manager, "AWG_FILES", Path(td)):
                with patch.object(profile_manager, "save_client_qr_png", side_effect=fake_png):
                    with patch.object(profile_manager, "command", return_value="QR_VISIBLE") as cmd:
                        with redirect_stdout(StringIO()) as output:
                            profile_manager.get_profile("awg", "Moto", "qr")
            self.assertEqual(len(png_payloads), 1)
            self.assertEqual(png_payloads[0], "[Interface]\nPrivateKey=CLIENT_KEY\n[Peer]\nPublicKey=SERVER_KEY\n")
            self.assertEqual(cmd.call_args.args[-1], png_payloads[0])
            self.assertEqual(cmd.call_args.args[-2:], ("2", png_payloads[0]))
            self.assertIn("QR_VISIBLE", output.getvalue())
            self.assertEqual(p.read_text(), "[Interface]\nPrivateKey = CLIENT_KEY\n\n[Peer]\nPublicKey = SERVER_KEY\n")

    def test_large_awg_qr_is_still_printed_and_png_saved(self):
        from io import StringIO
        from contextlib import redirect_stdout
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            config = Path(td) / "Moto.conf"
            config.write_text("[Interface]\nPrivateKey = dummy\n[Peer]\nPublicKey = dummy_server\n")
            qr = "##" * 100 + "\n"
            with patch.object(profile_manager, "AWG_FILES", Path(td)):
                with patch.object(profile_manager, "save_client_qr_png", return_value=Path(td) / "Moto.png"):
                    with patch.object(profile_manager, "command", return_value=qr) as command:
                        with redirect_stdout(StringIO()) as output:
                            profile_manager.get_profile("awg", "Moto", "qr")
            rendered = output.getvalue()
            self.assertIn(qr, rendered)
            self.assertIn("Moto.png", rendered)
            self.assertNotIn("too large", rendered)
            self.assertIn("ANSIUTF8", command.call_args.args)

    def test_qrencode_stdout_is_not_stripped(self):
        from types import SimpleNamespace
        raw = "   \n  \x1b[40;37;1m██\x1b[0m  \n   \n"
        with patch.object(profile_manager.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0, stderr="", stdout=raw)):
            self.assertEqual(
                profile_manager.command("qrencode", "-t", "ANSIUTF8",
                                        preserve_whitespace=True),
                raw,
            )
            self.assertNotEqual(
                profile_manager.command("qrencode", "-t", "ANSIUTF8"), raw
            )

    def test_xray_qr_uses_full_margin_and_exact_share_link(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "xray.json"
            import json
            path.write_text(json.dumps(config()))
            link = "vless://00000000-0000-4000-8000-000000000001@203.0.113.10:443?type=xhttp&security=reality#Phone"
            calls = []
            def fake_command(*args, **kwargs):
                calls.append((args, kwargs))
                if args[:2] == ("qrencode", "-t"):
                    return "  \nQR\n  \n"
                return "Password (PublicKey): " + "A" * 43
            with patch.object(profile_manager, "XRAY", path):
                with patch.object(profile_manager, "xray_link", return_value=link):
                    with patch.object(profile_manager, "XRAY_FILES", Path(td)):
                        with patch.object(profile_manager, "save_client_qr_png", return_value=Path(td) / "Phone.png") as png:
                            with patch.object(profile_manager, "command", side_effect=fake_command):
                                with redirect_stdout(StringIO()) as output:
                                    profile_manager.get_profile("xray", "Phone", "both")
            self.assertEqual(png.call_args.args[2], link)
            qr_args, kwargs = calls[-1]
            self.assertEqual(qr_args[-1], link)
            self.assertEqual(qr_args[qr_args.index("-m") + 1], "4")
            self.assertTrue(kwargs["preserve_whitespace"])
            self.assertIn("  \nQR\n  \n", output.getvalue())
            self.assertIn(link, output.getvalue())

    def test_xray_png_has_four_module_margin_and_larger_pixels(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            args_seen = []
            def fake_command(*args, **kwargs):
                args_seen.append(args)
                Path(args[args.index("-o") + 1]).write_bytes(b"fake-png")
                return ""
            with patch.object(profile_manager, "XRAY_FILES", Path(td)):
                with patch.object(profile_manager, "command", side_effect=fake_command):
                    profile_manager.save_client_qr_png("xray", "Phone", "vless://test")
            args = args_seen[0]
            self.assertEqual(args[args.index("-m") + 1], "4")
            self.assertEqual(args[args.index("-s") + 1], "6")

    def test_scannable_png_has_owner_only_permissions(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            def fake_command(*args, input=None):
                assert args[:4] == ("qrencode", "-t", "PNG", "-l")
                Path(args[args.index("-o") + 1]).write_bytes(b"fake-png")
                return ""
            with patch.object(profile_manager, "AWG_FILES", Path(td)):
                with patch.object(profile_manager, "command", side_effect=fake_command):
                    path = profile_manager.save_client_qr_png("awg", "Moto", "test-config")
            self.assertEqual(path.name, "Moto.png")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_client_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            profile_manager.xray_link(config(), "Missing")

    def test_awg_value_only_uses_interface(self):
        server = "[Interface]\nPrivateKey = server_secret\nListenPort = 43321\n[Peer]\nPrivateKey = peer_secret\n"
        self.assertEqual(profile_manager.awg_value(server, "PrivateKey"), "server_secret")

    def test_client_name_validation(self):
        for name in ["Android-04", "Phone_1", "moto"]:
            self.assertIsNotNone(profile_manager.CLIENT_NAME.fullmatch(name))
        for name in ["", "../sneaky", "a b", "bad\nline", "x" * 49]:
            self.assertIsNone(profile_manager.CLIENT_NAME.fullmatch(name))


if __name__ == "__main__":
    unittest.main()
