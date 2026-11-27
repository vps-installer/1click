"""Static and unit validation for first-install customization."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_options", ROOT / "lib/install_options.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class InstallOptionTests(unittest.TestCase):
    def test_valid_sni(self):
        self.assertEqual(mod.validate_sni("open.spotify.com"), "open.spotify.com")
        self.assertEqual(mod.validate_sni("www.example.org"), "www.example.org")

    def test_unsafe_sni_rejected(self):
        for name in ("", "https://example.com", "127.0.0.1", "localhost",
                     "EXAMPLE.COM", "example.com:443", "example.ru", "foo..com",
                     "-bad.example", "bad_.example", "example.com/any"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    mod.validate_sni(name)

    def test_udp_port_validated(self):
        for value in ("1", "43321", "65535"):
            self.assertEqual(mod.validate_port(value), int(value))
        for value in ("0", "65536", "-1", "21.5", "abc", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    mod.validate_port(value)

    def test_random_udp_port_range_and_variation(self):
        ports = [mod.generate_random_udp_port() for _ in range(20)]
        self.assertTrue(all(20000 <= port <= 59999 for port in ports))
        self.assertGreater(len(set(ports)), 1)

    def test_random_udp_port_retries_occupied_port(self):
        with patch.object(mod.secrets, "randbelow", side_effect=[100, 101]):
            with patch.object(mod.socket, "socket") as make_socket:
                # A mocked busy port on the first attempt must trigger a new draw.
                class FakeProbe:
                    def __enter__(self): return self
                    def __exit__(self, *_): pass
                    def bind(self, address): pass
                calls = iter([OSError("busy"), None])
                def probe(*args):
                    error = next(calls)
                    if error:
                        raise error
                    return FakeProbe()
                make_socket.side_effect = probe
                self.assertEqual(mod.generate_random_udp_port(), 20101)

    def test_generated_obfuscation_is_bounded_and_random(self):
        profiles = [mod.generate_awg_obfuscation() for _ in range(15)]
        self.assertGreater(len({tuple(sorted(p.items())) for p in profiles}), 1)
        expected = {"Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
                    "H1", "H2", "H3", "H4", "ContentPaddingAddition"}
        for options in profiles:
            self.assertEqual(set(options), expected)
            self.assertTrue(3 <= options["Jc"] <= 8)
            self.assertTrue(48 <= options["Jmin"] <= 90)
            self.assertTrue(140 <= options["Jmax"] <= 230)
            self.assertLess(options["Jmin"], options["Jmax"])
            for field in ("S1", "S2"):
                self.assertTrue(48 <= options[field] <= 100)
            for field in ("S3", "S4"):
                self.assertTrue(16 <= options[field] <= 48)
            headers = [options[f"H{i}"] for i in range(1, 5)]
            self.assertEqual(len(set(headers)), 4)
            self.assertTrue(all(5 <= h <= 2147483647 for h in headers))
            pmin, pmax = map(int, options["ContentPaddingAddition"].split("-"))
            self.assertTrue(8 <= pmin <= 24 and 40 <= pmax <= 56)

    def test_tls_check_requires_tls13(self):
        class FakeTLS:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def version(self): return "TLSv1.3"
            def selected_alpn_protocol(self): return "h2"
        class FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *_): pass
        context = type("FakeContext", (), {
            "wrap_socket": lambda self, sock, server_hostname: FakeTLS(),
            "set_alpn_protocols": lambda self, names: None
        })()
        with patch.object(mod.ssl, "create_default_context", return_value=context):
            with patch.object(mod.socket, "create_connection", return_value=FakeSocket()):
                self.assertEqual(mod.check_tls("open.spotify.com"), "open.spotify.com")


if __name__ == "__main__":
    unittest.main()
