"""Tests for pi_sms.setup.network MAC-based interface provisioning."""

import subprocess
from pathlib import Path

import pytest

from pi_sms.setup import network


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_default_route_interface_parses_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "_run",
        lambda args: _completed("default via 192.168.0.1 dev eth0 proto dhcp metric 100"),
    )

    assert network._default_route_interface() == "eth0"


def test_default_route_interface_returns_none_without_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(network, "_run", lambda args: _completed("", returncode=1))

    assert network._default_route_interface() is None


def test_connection_for_device_matches_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "_run",
        lambda args: _completed("eth0:netplan-eth0\neth1:pi-sms-modem\nlo:lo\n"),
    )

    assert network._connection_for_device("eth0") == "netplan-eth0"


def test_connection_for_device_ignores_unmanaged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "_run", lambda args: _completed("eth0:--\n"))

    assert network._connection_for_device("eth0") is None


def test_ensure_modem_network_fails_without_nmcli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "_nmcli_available", lambda: False)

    assert network.ensure_modem_network() is False


def test_ensure_modem_network_fails_when_modem_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "_nmcli_available", lambda: True)
    monkeypatch.setattr(network, "_ensure_lan_pinned_to_mac", lambda: None)
    monkeypatch.setattr(network, "_find_modem_interface", lambda: None)

    assert network.ensure_modem_network() is False


def test_ensure_modem_network_provisions_by_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return _completed()

    monkeypatch.setattr(network, "_nmcli_available", lambda: True)
    monkeypatch.setattr(network, "_ensure_lan_pinned_to_mac", lambda: None)
    monkeypatch.setattr(network, "_find_modem_interface", lambda: "eth1")
    monkeypatch.setattr(network, "_iface_mac", lambda iface: "0C:5B:8F:27:9A:64")
    monkeypatch.setattr(network, "_connection_exists", lambda name: False)
    monkeypatch.setattr(network, "_run", fake_run)

    assert network.ensure_modem_network() is True

    add_command = next(c for c in commands if "add" in c)
    assert "0C:5B:8F:27:9A:64" in add_command
    assert "ifname" not in add_command
    assert network._MODEM_STATIC_ADDRESS in add_command


def test_ensure_lan_pinned_to_mac_pins_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_pin(connection: str, mac: str) -> bool:
        calls.append((connection, mac))
        return True

    monkeypatch.setattr(network, "_default_route_interface", lambda: "eth0")
    monkeypatch.setattr(network, "_iface_mac", lambda iface: "B8:27:EB:B7:7F:5B")
    monkeypatch.setattr(network, "_connection_for_device", lambda iface: "netplan-eth0")
    monkeypatch.setattr(network, "_pin_connection_to_mac", fake_pin)

    network._ensure_lan_pinned_to_mac()

    assert calls == [("netplan-eth0", "B8:27:EB:B7:7F:5B")]


def test_ensure_lan_pinned_to_mac_skips_without_route(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_pin(connection: str, mac: str) -> bool:
        raise AssertionError("should not pin without a LAN interface")

    monkeypatch.setattr(network, "_default_route_interface", lambda: None)
    monkeypatch.setattr(network, "_pin_connection_to_mac", fail_pin)

    network._ensure_lan_pinned_to_mac()


def test_iface_usb_vendor_reads_sysfs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net_dir = tmp_path / "sys" / "class" / "net"
    usb_dir = tmp_path / "sys" / "usb1"
    usb_dir.mkdir(parents=True)
    (usb_dir / "idVendor").write_text("12d1\n")
    iface_dir = net_dir / "eth1"
    iface_dir.mkdir(parents=True)
    device_link = iface_dir / "device"
    device_link.symlink_to(usb_dir)

    monkeypatch.setattr(network, "_SYSFS_NET", net_dir)

    assert network._iface_usb_vendor("eth1") == "12d1"
