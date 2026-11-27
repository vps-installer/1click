"""VLESS YAML proxy output must use saved config without inventing features."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("profile_manager", ROOT / "lib/profile_manager.py")
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)


def conf():
    return {"inbounds": [{
        "protocol": "vless", "port": 443, "listen": "2001:db8::10",
        "settings": {"clients": [{"email": "SampleClient", "id": "00000000-0000-4000-8000-000000000001"}]},
        "streamSettings": {
            "network": "xhttp", "security": "reality",
            "realitySettings": {
                "privateKey": "SERVER_PRIVATE_MUST_NOT_PRINT",
                "serverNames": ["example.org"], "shortIds": ["1122334455667788"]
            },
            "xhttpSettings": {"path": "/examplepath", "mode": "stream-one"}
        }
    }]}


class XrayYamlTests(unittest.TestCase):
    def test_yaml_matches_exact_template_order_and_values(self):
        with patch.object(pm, "command", return_value="Password (PublicKey): " + "A" * 43):
            result = pm.xray_yaml(conf(), "SampleClient")
        expected = [
            '- name: SampleClient',
            '  type: vless',
            '  server: "2001:db8::10"',
            '  port: 443',
            '  uuid: "00000000-0000-4000-8000-000000000001"',
            '  udp: true',
            '  tls: true',
            '  servername: example.org',
            '  client-fingerprint: chrome',
            '  network: xhttp',
            '  alpn:',
            '    - h2',
            '  encryption: ""',
            '  reality-opts:',
            '    public-key: "' + "A" * 43 + '"',
            '    support-x25519mlkem768: true',
            '    short-id: "1122334455667788"',
            '  xhttp-opts:',
            '    path: "/examplepath"',
            '    mode: stream-one',
        ]
        self.assertEqual(result.splitlines(), expected)
        self.assertTrue(result.endswith("\n"))
        self.assertNotIn("SERVER_PRIVATE_MUST_NOT_PRINT", result)

    def test_client_defaults_are_included_without_optional_server_alpn(self):
        with patch.object(pm, "command", return_value="PublicKey: " + "B" * 43):
            result = pm.xray_yaml(conf(), "SampleClient")
        self.assertIn("  alpn:\n    - h2\n", result)
        self.assertIn("    support-x25519mlkem768: true\n", result)

    def test_unknown_profile_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            pm.xray_yaml(conf(), "Other")

    def test_get_profile_can_print_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps(conf()))
            with patch.object(pm, "XRAY", path):
                with patch.object(pm, "command", return_value="PublicKey: " + "C" * 43):
                    with redirect_stdout(StringIO()) as output:
                        pm.get_profile("xray", "SampleClient", "yaml")
            text = output.getvalue()
            self.assertIn("--- YAML PROXY: SampleClient ---", text)
            self.assertIn("  reality-opts:", text)
            self.assertNotIn("SERVER_PRIVATE_MUST_NOT_PRINT", text)


if __name__ == "__main__":
    unittest.main()
