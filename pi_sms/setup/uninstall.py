"""Uninstall helper script for pi-sms."""

import subprocess
from pathlib import Path


def stop_and_disable_service() -> None:
    """Stop and disable the systemd service."""
    service_name = "pi-sms"
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            subprocess.run(["systemctl", "stop", service_name], check=False)
            print(f"Stopped {service_name} service")

        result = subprocess.run(
            ["systemctl", "is-enabled", "--quiet", service_name],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            subprocess.run(["systemctl", "disable", service_name], check=False)
            print(f"Disabled {service_name} service")
    except Exception as e:
        print(f"Warning: Could not stop/disable service: {e}")


def reload_systemd() -> None:
    """Reload systemd daemon."""
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "reset-failed"], check=False)
    except Exception as e:
        print(f"Warning: Could not reload systemd: {e}")


def find_pip_command() -> str:
    """Find the correct pip command to use for uninstall.

    Returns:
        The pip command path (venv pip if in venv, or 'pip' for system-wide)
    """
    try:
        import pi_sms

        package_path = Path(pi_sms.__file__).parent
        if "site-packages" in str(package_path) or "dist-packages" in str(package_path):
            path_parts = package_path.parts
            for i, part in enumerate(path_parts):
                if part in ("site-packages", "dist-packages") and i >= 2:
                    venv_root = Path(*path_parts[: i - 2])
                    venv_pip = venv_root / "bin" / "pip"
                    if venv_pip.exists():
                        return str(venv_pip)
                    break
    except ImportError:
        pass

    return "pip"


def main() -> None:
    """Main uninstall helper function."""
    print("Preparing to uninstall pi-sms...")
    print("Stopping and disabling systemd service...")

    stop_and_disable_service()
    reload_systemd()

    config_path = Path("/etc/pi-sms/config.yaml")
    if config_path.exists():
        print(f"\nConfiguration will be preserved at {config_path}")
        print("  (Following Debian FHS best practices)")
    else:
        print("\nReady for uninstall")

    pip_cmd = find_pip_command()
    print(f"\nNow run: {pip_cmd} uninstall pi-sms")
    print("The systemd service file will be removed automatically.")
    print(
        "\nThe modem network profile ('pi-sms-modem') is left in place; "
        "remove it with: sudo nmcli connection delete pi-sms-modem"
    )


if __name__ == "__main__":
    main()
