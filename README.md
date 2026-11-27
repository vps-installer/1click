# Universal VPS Installer

**Language:** [English](README.md) | [Русский](README.ru.md)

An interactive VPN server installer for a fresh **Ubuntu 24.04 LTS** VPS.

Automates VPS preparation, VPN protocol installation, network filtering, and client profile creation.

## Components and configuration

- Ubuntu system updates and essential utilities
- OpenSSH Server
- UFW
- Fail2ban
- fzf
- Xray (VLESS + XHTTP + REALITY)
- AmneziaWG
- Unbound
- nftables
- GeoBlock with automatic updates of Russian IP network lists

**All management takes place in an interactive command-line menu.** No extra web dashboards are required. After installation, you can reopen the server and client profile manager at any time using:

```bash
sudo vps-installer
```

You can also manage the installed components directly using standard Linux commands.

## Why Russian traffic is blocked over the VPN

Some apps and system services continue contacting servers in Russia even while a VPN is enabled—for synchronization, analytics, notifications, and other background tasks.

As a result, those services may learn the public IP address of the foreign VPN server.

To limit these connections, the installer configures filtering at several levels:

- **Xray:** blocks Russian domains and IP addresses using DNS, routing, and outbound connection rules.
- **AmneziaWG:** blocks Russian IPv4 networks using nftables, DNS filtering, and routing restrictions.
- **Fail-closed:** rejects traffic that does not match the permitted rules.

**The goal is to prevent connections to Russian resources through the foreign VPN exit point and reduce the chance of exposing its IP address.**

These rules do not block users located in Russia from connecting to the VPN server itself.

Not a cure-all, but better than nothing...

## IPv4 and IPv6 client connectivity

The installer supports **incoming client connections over both IPv4 and IPv6** for Xray and AmneziaWG. A VPS's public IPv6 address can therefore be used as an alternative VPN connection endpoint when the client's network supports IPv6.

- **Automatic IPv6 discovery:** the installer checks for a globally routable IPv6 address assigned to the VPS and a usable IPv6 route. If one is found, the Xray or AmneziaWG installation menu offers to enable incoming IPv6 connections, including when revisiting an already installed component. IPv6 access is enabled only with user confirmation.
- **Xray:** creates a separate IPv6 listener on TCP port 443 for VLESS + XHTTP + REALITY, using the same settings and client identifiers as the IPv4 listener. The original IPv4 listener is preserved. The configuration, service status, and local IPv6 TCP socket are checked; if activation fails, the original configuration is restored.
- **AmneziaWG:** the server can accept client connections on its UDP port via its public IPv6 address. An alternative client configuration uses this address as the `Endpoint`. Keys and other settings remain unchanged, and the saved `.conf` file is not overwritten. Connectivity must be verified from an external client.
- **Client profiles:** when creating or viewing a profile, you can select **IPv4** or **IPv6** as the connection address. IPv6 uses the existing client keys/UUIDs—there is no need to create a second client. VLESS links, configurations, and QR codes are generated for the selected address.
- **Validation:** before enabling IPv6, the installer checks UFW settings and the relevant incoming port. An IPv6 profile variant will not be generated if IPv6 ingress is disabled or the address is no longer assigned to the VPS.

**Important: IPv6 is used for client connections *to* the VPN server, not for outbound VPN traffic *from* the server to IPv6 destinations.** Xray retains its IPv4-only outbound policy. AmneziaWG does not enable IPv6 forwarding through the tunnel and drops that traffic using GeoBlock rules. Russian IPv4 network filtering and the other restrictions remain in place.

IPv6 connectivity requires IPv6 availability both on the VPS and on the client's network. This does not guarantee that the client device cannot bypass the VPN or leak IPv6 traffic: routing and Android/VPN client behavior must be checked separately.

## Client profile management

The interactive menu lets you:

- Create individual Xray and AmneziaWG profiles.
- View existing profiles without changing their keys.
- Obtain VLESS connection links and AmneziaWG configuration files.
- Display QR codes directly in the SSH terminal.
- Save QR codes as PNG images.
- Obtain a YAML configuration for Clash/Mihomo-compatible clients.

## Installation

On a fresh Ubuntu 24.04 LTS VPS, run as `root`:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/vps-installer/1click/main/install.sh)"
```

After the initial setup, reconnect over SSH using the administrator account you created. To reopen the menu and manage client profiles:

```bash
sudo vps-installer
```

## Limitations

- Only **Ubuntu 24.04 LTS** is supported.

**Project status: actively developed.** Bugs and functionality changes are possible.
