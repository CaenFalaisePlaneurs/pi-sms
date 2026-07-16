"""Idempotent NetworkManager provisioning for the E3372 HiLink modem interface.

The E3372 exposes itself as a USB network card (typically `eth1`) hosting a
web API at 192.168.8.1. This module creates a static, isolated connection
profile for that interface so the modem is reachable on every boot without
ever becoming the default route or otherwise disrupting the Pi's LAN link
(`eth0`), which is how the daemon and the operator's SSH session reach it.

This exact profile shape (`ipv4.method manual`, `ipv4.never-default yes`,
`ipv6.method disabled`) was validated live against a Pi with an E3372 in a
prior debugging session: bringing eth1 up via plain DHCP (`nmcli device
connect eth1`) let the modem's DHCP server hijack the default route and took
eth0 off the LAN entirely. The static, never-default profile avoids that
failure mode.
"""

import subprocess

_CONNECTION_NAME = "pi-sms-modem"
_MODEM_INTERFACE = "eth1"
_MODEM_STATIC_ADDRESS = "192.168.8.100/24"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _connection_exists() -> bool:
    result = _run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    return _CONNECTION_NAME in result.stdout.splitlines()


def ensure_modem_network() -> bool:
    """Ensure the isolated eth1 profile for the modem exists and is active.

    Returns:
        True if the profile is present and activation was attempted without
        error, False if `nmcli` is unavailable or a command failed.
    """
    try:
        _run(["nmcli", "--version"])
    except FileNotFoundError:
        print("Error: nmcli not found. This setup step requires NetworkManager.")
        return False

    if _connection_exists():
        print(f"Network profile '{_CONNECTION_NAME}' already exists")
    else:
        result = _run(
            [
                "nmcli",
                "connection",
                "add",
                "type",
                "ethernet",
                "ifname",
                _MODEM_INTERFACE,
                "con-name",
                _CONNECTION_NAME,
                "ipv4.method",
                "manual",
                "ipv4.addresses",
                _MODEM_STATIC_ADDRESS,
                "ipv4.never-default",
                "yes",
                "ipv4.ignore-auto-dns",
                "yes",
                "ipv6.method",
                "disabled",
                "connection.autoconnect",
                "yes",
            ]
        )
        if result.returncode != 0:
            print(f"Error creating network profile: {result.stderr.strip()}")
            return False
        print(
            f"Created network profile '{_CONNECTION_NAME}' ({_MODEM_INTERFACE} -> {_MODEM_STATIC_ADDRESS})"
        )

    result = _run(["nmcli", "connection", "up", _CONNECTION_NAME])
    if result.returncode != 0:
        print(f"Warning: could not bring up '{_CONNECTION_NAME}' now: {result.stderr.strip()}")
        print(f"It will be attempted again on next boot ({_MODEM_INTERFACE} must be plugged in).")
        return True

    print(f"Activated network profile '{_CONNECTION_NAME}'")
    return True
