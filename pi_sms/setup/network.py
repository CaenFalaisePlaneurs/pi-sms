"""Idempotent NetworkManager provisioning for the E3372 HiLink modem interface.

The E3372 exposes itself as a USB network card hosting a web API at
192.168.8.1. On a Raspberry Pi that already has an onboard NIC, both the modem
and the onboard NIC are USB-attached ethernet devices, so the kernel assigns
their `ethX` names in a race that can differ on every boot.

When the generic LAN profile (DHCP, bound to neither an interface name nor a
MAC) wins that race on the *modem* interface, it pulls a lease from the modem's
own DHCP server and leaves the real onboard NIC with no address — the Pi drops
off the LAN. This was observed live on a Pi with an E3372.

To make the assignment deterministic, this module binds each connection to a
MAC address instead of an interface name:

* the modem connection is bound to the modem's MAC (found via the Huawei USB
  vendor id) with a static, `never-default` address so it is always reachable
  yet never becomes the default route or touches the LAN link;
* the LAN connection is pinned to the onboard NIC's MAC (found via the current
  default route) so it can only ever activate on the real LAN NIC.

MAC binding via `nmcli connection modify` is non-disruptive: it does not tear
down the live connection, it only decides where each profile lands on the next
activation and on boot.
"""

import subprocess
from pathlib import Path

_CONNECTION_NAME = "pi-sms-modem"
_MODEM_STATIC_ADDRESS = "192.168.8.100/24"
_HUAWEI_USB_VENDOR_ID = "12d1"
_SYSFS_NET = Path("/sys/class/net")


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _nmcli_available() -> bool:
    try:
        _run(["nmcli", "--version"])
    except FileNotFoundError:
        return False
    return True


def _iface_usb_vendor(iface: str) -> str | None:
    """Return the lowercased USB vendor id of an interface, or None.

    Walks up the sysfs device tree from the interface because the `idVendor`
    file lives on the USB device node, not on the net device itself.
    """
    device = _SYSFS_NET / iface / "device"
    if not device.exists():
        return None

    node = device.resolve()
    root = Path(node.anchor)
    while node != root:
        vendor_file = node / "idVendor"
        if vendor_file.exists():
            return vendor_file.read_text().strip().lower()
        node = node.parent
    return None


def _iface_mac(iface: str) -> str | None:
    address_file = _SYSFS_NET / iface / "address"
    if not address_file.exists():
        return None
    mac = address_file.read_text().strip()
    return mac.upper() or None


def _find_modem_interface() -> str | None:
    """Return the interface name of the Huawei modem, or None if not present."""
    if not _SYSFS_NET.exists():
        return None
    for iface in sorted(p.name for p in _SYSFS_NET.iterdir()):
        if iface == "lo":
            continue
        if _iface_usb_vendor(iface) == _HUAWEI_USB_VENDOR_ID:
            return iface
    return None


def _default_route_interface() -> str | None:
    """Return the interface carrying the IPv4 default route, or None."""
    result = _run(["ip", "-o", "-4", "route", "show", "default"])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        tokens = line.split()
        if "dev" in tokens:
            return tokens[tokens.index("dev") + 1]
    return None


def _connection_for_device(device: str) -> str | None:
    """Return the active NetworkManager connection name bound to a device."""
    result = _run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "device", "status"])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        dev, _, connection = line.partition(":")
        if dev == device and connection not in ("", "--"):
            return connection
    return None


def _connection_exists(name: str) -> bool:
    result = _run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    return name in result.stdout.splitlines()


def _pin_connection_to_mac(connection: str, mac: str) -> bool:
    """Bind a connection to a MAC and clear any interface-name lock."""
    result = _run(
        [
            "nmcli",
            "connection",
            "modify",
            connection,
            "802-3-ethernet.mac-address",
            mac,
            "connection.interface-name",
            "",
        ]
    )
    if result.returncode != 0:
        print(f"Warning: could not pin '{connection}' to {mac}: {result.stderr.strip()}")
        return False
    return True


def _ensure_lan_pinned_to_mac() -> None:
    """Pin the current LAN connection to the onboard NIC's MAC.

    Prevents the LAN profile from ever activating on the modem interface at
    boot, which is what knocks the Pi off the network.
    """
    lan_iface = _default_route_interface()
    if lan_iface is None:
        print("Warning: no default route found; skipping LAN interface pinning")
        return

    lan_mac = _iface_mac(lan_iface)
    lan_connection = _connection_for_device(lan_iface)
    if lan_mac is None or lan_connection is None:
        print(f"Warning: could not resolve MAC/connection for '{lan_iface}'; skipping LAN pinning")
        return

    if _pin_connection_to_mac(lan_connection, lan_mac):
        print(f"Pinned LAN connection '{lan_connection}' to {lan_iface} ({lan_mac})")


def _ensure_modem_connection(modem_mac: str) -> bool:
    """Create or update the modem connection bound to the modem's MAC."""
    if _connection_exists(_CONNECTION_NAME):
        result = _run(
            [
                "nmcli",
                "connection",
                "modify",
                _CONNECTION_NAME,
                "802-3-ethernet.mac-address",
                modem_mac,
                "connection.interface-name",
                "",
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
            print(f"Error updating network profile: {result.stderr.strip()}")
            return False
        print(f"Updated network profile '{_CONNECTION_NAME}' -> {modem_mac}")
    else:
        result = _run(
            [
                "nmcli",
                "connection",
                "add",
                "type",
                "ethernet",
                "con-name",
                _CONNECTION_NAME,
                "802-3-ethernet.mac-address",
                modem_mac,
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
            f"Created network profile '{_CONNECTION_NAME}' "
            f"({modem_mac} -> {_MODEM_STATIC_ADDRESS})"
        )

    result = _run(["nmcli", "connection", "up", _CONNECTION_NAME])
    if result.returncode != 0:
        print(f"Warning: could not bring up '{_CONNECTION_NAME}' now: {result.stderr.strip()}")
        print("It will be attempted again on next boot (the modem must be plugged in).")
        return True

    print(f"Activated network profile '{_CONNECTION_NAME}'")
    return True


def ensure_modem_network() -> bool:
    """Ensure both the modem and LAN profiles are pinned to their MAC addresses.

    Returns:
        True if provisioning completed (individual non-fatal steps may warn),
        False if `nmcli` is unavailable or the modem could not be provisioned.
    """
    if not _nmcli_available():
        print("Error: nmcli not found. This setup step requires NetworkManager.")
        return False

    _ensure_lan_pinned_to_mac()

    modem_iface = _find_modem_interface()
    if modem_iface is None:
        print("Error: no Huawei modem interface found (is the E3372 plugged in?).")
        return False

    modem_mac = _iface_mac(modem_iface)
    if modem_mac is None:
        print(f"Error: could not read MAC address for modem interface '{modem_iface}'.")
        return False

    print(f"Detected modem interface '{modem_iface}' ({modem_mac})")
    return _ensure_modem_connection(modem_mac)
