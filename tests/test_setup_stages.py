"""Regression checks for separated setup stages and safe SSH handover."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = (ROOT / "lib/system.sh").read_text()
VPN = (ROOT / "lib/vpn.sh").read_text()
INSTALLER = (ROOT / "install.sh").read_text()


class SetupStagesTests(unittest.TestCase):
    def test_defence_stage_does_not_run_vpn_installers(self):
        body = SYSTEM.split("vps_defence_customization() {", 1)[1]
        self.assertNotIn("install_xray", body)
        self.assertNotIn("install_amnezia", body)
        self.assertIn('finish_install "$port"', body)
        self.assertIn('summary_screen', body)
        self.assertNotIn('disconnect_current_ssh_session', body)

    def test_vpn_stage_requires_completed_base(self):
        preflight = VPN.split("vpn_installation_preflight() {", 1)[1].split(
            "\nxray_installation() {", 1)[0]
        self.assertIn('state_get INSTALL_COMPLETE', preflight)
        self.assertIn('state_get BASE_CONFIGURED', preflight)
        self.assertIn("vpn_installation_preflight || return 1", VPN)
        xray = VPN.split("xray_installation() {", 1)[1].split(
            "\namnezia_installation() {", 1)[0]
        awg = VPN.split("amnezia_installation() {", 1)[1]
        self.assertIn("xray_already_present", xray)
        self.assertNotIn("install_amnezia", xray)
        self.assertIn("awg_already_present", awg)
        self.assertNotIn("install_xray", awg)

    def test_existing_xray_offers_ipv6_without_reinstalling(self):
        xray = VPN.split("xray_installation() {", 1)[1].split(
            "\n amnezia_installation() {", 1)[0]
        if "\namnezia_installation() {" in xray:
            xray = xray.split("\namnezia_installation() {", 1)[0]
        self.assertIn('ok "Xray is already installed and active. Configuration and keys preserved."\n      offer_xray_ipv6 || return 1', xray)
        self.assertIn('  install_xray || return 1\n  offer_xray_ipv6 || return 1', xray)

    def test_awg_live_port_check_not_ss_gate(self):
        awg = VPN.split("offer_awg_ipv6() {", 1)[1].split(
            "\nvpn_installation_preflight() {", 1)[0]
        self.assertIn('live_port="$(awg show awg0 listen-port)"', awg)
        self.assertIn('[[ "$live_port" != "$port" ]]', awg)
        self.assertIn('state_set AWG_IPV6 yes', awg)
        self.assertNotIn('No IPv6 profile will be offered.', awg)
        self.assertIn('external IPv6 AmneziaWG handshake', awg)

    def test_ssh_security_without_forced_disconnect(self):
        setup = SYSTEM.split("configure_ssh() {", 1)[1].split(
            "\nprepare_fail2ban() {", 1)[0]
        self.assertIn("PermitRootLogin no", setup)
        self.assertNotIn("PermitRootLogin prohibit-password", setup)
        finish = SYSTEM.split("finish_install() {", 1)[1].split(
            "\nsummary_screen() {", 1)[0]
        self.assertIn("ufw --force delete allow 22/tcp", finish)
        self.assertNotIn("pkill -TERM", SYSTEM)
        self.assertNotIn("setsid -f", SYSTEM)

    def test_awg_resumption_preserves_its_private_key(self):
        body = VPN.split("install_amnezia() {", 1)[1].split(
            "\nvpn_installation_preflight() {", 1)[0]
        self.assertIn("existing_awg=1", body)
        self.assertIn("if (( existing_awg == 0 )); then", body)
        self.assertIn('private="$(awg genkey)"', body)
        self.assertIn("Existing server keys and client profiles will be preserved.", body)

    def test_menu_returns_without_exit_after_stages(self):
        self.assertIn("1) vps_defence_customization || warn", INSTALLER)
        self.assertIn("2) xray_installation || warn", INSTALLER)
        self.assertIn("3) amnezia_installation || warn", INSTALLER)
        self.assertIn("4) profile_menu ;;", INSTALLER)
        self.assertIn("0) exit 0 ;;", INSTALLER)


if __name__ == "__main__":
    unittest.main()
