"""Setup module for pi-sms system configuration."""

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

from .network import ensure_modem_network


def is_docker_environment() -> bool:
    """Check if running in a Docker container.

    Returns:
        True if running in Docker, False otherwise
    """
    if Path("/.dockerenv").exists():
        return True
    try:
        result = subprocess.run(
            ["systemctl", "--version"],
            capture_output=True,
            timeout=1,
            check=False,
        )
        return result.returncode != 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True


def get_current_user() -> str:
    """Get the current username, defaulting to 'pi' for Raspberry Pi compatibility."""
    try:
        user = os.environ.get("USER") or os.environ.get("USERNAME")
        if user:
            return user
        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, AttributeError):
        return "pi"


def find_pi_sms_executable() -> str:
    """Find the pi-sms executable path, preferring a virtual environment if used.

    Returns:
        Path to the pi-sms executable
    """
    try:
        import pi_sms

        package_path = Path(pi_sms.__file__).parent
        if "site-packages" in str(package_path) or "dist-packages" in str(package_path):
            path_parts = package_path.parts
            for i, part in enumerate(path_parts):
                if part in ("site-packages", "dist-packages") and i >= 2:
                    venv_root = Path(*path_parts[: i - 2])
                    venv_bin = venv_root / "bin" / "pi-sms"
                    if venv_bin.exists():
                        return str(venv_bin)
                    break
    except ImportError:
        pass

    venv_python = os.environ.get("VIRTUAL_ENV")
    if venv_python:
        venv_bin = Path(venv_python) / "bin" / "pi-sms"
        if venv_bin.exists():
            return str(venv_bin)

    common_venv_paths = [
        "/opt/pi-sms-venv/bin/pi-sms",
        "/opt/pi-sms/venv/bin/pi-sms",
    ]
    for venv_path in common_venv_paths:
        if Path(venv_path).exists():
            return venv_path

    executable_path = shutil.which("pi-sms")
    if executable_path:
        return executable_path

    return "/usr/bin/pi-sms"


def create_systemd_service() -> bool:
    """Create the systemd service file for pi-sms.

    Returns:
        True if the service file was created/updated, False otherwise
    """
    if is_docker_environment():
        print("Skipping systemd service setup (Docker environment detected)")
        return True

    service_name = "pi-sms"
    service_file_dest = Path(f"/etc/systemd/system/{service_name}.service")
    service_user = get_current_user()

    possible_locations = [
        # Bundled via data_files into the (venv) prefix on a pip install
        Path(sys.prefix) / "etc" / "systemd" / "system" / "pi-sms.service",
        # Running from a source checkout
        Path(__file__).parent.parent.parent / "deploy" / "pi-sms.service",
        service_file_dest,
    ]
    service_file_source = next((loc for loc in possible_locations if loc.exists()), None)

    if service_file_source is None:
        print("Error: Could not find deploy/pi-sms.service file.")
        print(f"Check if it is already installed: sudo ls -la {service_file_dest}")
        return False

    if os.geteuid() != 0:
        print("Error: This command requires sudo privileges.")
        print("Please run this setup with sudo.")
        return False

    try:
        pi_sms_executable = find_pi_sms_executable()
        print(f"Detected pi-sms executable: {pi_sms_executable}")

        service_content = service_file_source.read_text()
        lines = service_content.split("\n")
        updated_lines = []
        exec_start_found = False
        user_found = False

        for line in lines:
            if line.strip().startswith("ExecStart="):
                updated_lines.append(f"ExecStart={pi_sms_executable}")
                exec_start_found = True
            elif line.strip().startswith("User="):
                updated_lines.append(f"User={service_user}")
                user_found = True
            else:
                updated_lines.append(line)

        if not exec_start_found:
            updated_lines.append(f"ExecStart={pi_sms_executable}")
        if not user_found:
            updated_lines.append(f"User={service_user}")

        service_file_dest.parent.mkdir(parents=True, exist_ok=True)
        service_file_dest.write_text("\n".join(updated_lines))
        print(f"Created systemd service file: {service_file_dest}")

        subprocess.run(["systemctl", "daemon-reload"], check=True)
        print("Reloaded systemd daemon")
        return True
    except PermissionError:
        print("Error: Permission denied. This command requires sudo privileges.")
        return False
    except Exception as e:
        print(f"Error creating systemd service: {e}")
        return False


def get_config_example_content() -> str | None:
    """Get the content of config.example.yaml from the package or repository."""
    possible_locations = [
        # Bundled via data_files into the (venv) prefix on a pip install
        Path(sys.prefix) / "usr" / "share" / "pi-sms" / "config.example.yaml",
        # Running from a source checkout
        Path(__file__).parent.parent.parent / "config.example.yaml",
        Path("/usr/share/pi-sms/config.example.yaml"),
    ]
    for location in possible_locations:
        if location.exists():
            return location.read_text()
    return None


def create_config_template() -> bool:
    """Create the configuration file template if it doesn't exist.

    Returns:
        True if the config was created or already exists, False on error
    """
    config_dir = Path("/etc/pi-sms")
    config_file = config_dir / "config.yaml"

    if config_file.exists():
        print(f"Configuration file already exists: {config_file}")
        return True

    config_content = get_config_example_content()
    if config_content is None:
        print("Error: Could not find config.example.yaml file.")
        return False

    if os.geteuid() != 0:
        print("Error: This command requires sudo privileges.")
        print("Please run this setup with sudo.")
        return False

    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file.write_text(config_content)
        print(f"Created configuration template: {config_file}")
        print(f"Please edit {config_file} (Trello key/token/list_id) before starting the service")
        return True
    except PermissionError:
        print("Error: Permission denied. This command requires sudo privileges.")
        return False
    except Exception as e:
        print(f"Error creating config file: {e}")
        return False


def enable_service() -> bool:
    """Enable the systemd service to start on boot.

    The service is intentionally not started here: the config template still
    holds placeholder Trello credentials at this point, so starting is left as
    an explicit step once the operator has edited the config.

    Returns:
        True if the service was enabled, False otherwise
    """
    if is_docker_environment():
        print("Skipping systemd service management (Docker environment detected)")
        return True

    service_name = "pi-sms"

    if os.geteuid() != 0:
        print("Error: This command requires sudo privileges.")
        print("Please run with sudo.")
        return False

    try:
        subprocess.run(["systemctl", "enable", service_name], check=True)
        print(f"Enabled {service_name} service (will start on boot)")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error managing service: {e}")
        return False
    except PermissionError:
        print("Error: Permission denied. This command requires sudo privileges.")
        return False


def main() -> None:
    """Main setup function."""
    print("=========================================")
    print("  pi-sms Setup")
    print("=========================================")
    print()

    print("[1/4] Setting up modem network profile...")
    if not ensure_modem_network():
        print("Failed to set up modem network profile")
        sys.exit(1)

    print("\n[2/4] Setting up systemd service...")
    if not create_systemd_service():
        print("Failed to create systemd service")
        sys.exit(1)

    print("\n[3/4] Setting up configuration...")
    if not create_config_template():
        print("Failed to create configuration template")
        sys.exit(1)

    print("\n[4/4] Enabling service...")
    if not enable_service():
        print("Failed to enable service")
        sys.exit(1)

    print()
    print("=========================================")
    print("  Setup Complete!")
    print("=========================================")
    print()
    print("Next steps:")
    print("1. Edit configuration: sudo nano /etc/pi-sms/config.yaml")
    print("2. Start the service:   sudo systemctl start pi-sms")
    print("3. Check service status: sudo systemctl status pi-sms")
    print("4. View logs: sudo journalctl -u pi-sms -f")


if __name__ == "__main__":
    main()
