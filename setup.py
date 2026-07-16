"""Setup script for pi-sms package with data_files for systemd service."""

from pathlib import Path

from setuptools import setup

# This setup.py is used only for data_files (systemd service + example config)
# Package metadata comes from pyproject.toml
# Note: pip uninstall will automatically remove files listed in data_files
# We cannot hook into pip uninstall to stop/disable the service automatically,
# but the service file will be removed, and config will be preserved (not in data_files)

service_file = Path(__file__).parent / "deploy" / "pi-sms.service"
config_example = Path(__file__).parent / "config.example.yaml"
data_files = []
if service_file.exists():
    service_file_rel = service_file.relative_to(Path(__file__).parent)
    data_files = [("etc/systemd/system", [str(service_file_rel)])]
if config_example.exists():
    config_example_rel = config_example.relative_to(Path(__file__).parent)
    data_files.append(("usr/share/pi-sms", [str(config_example_rel)]))

# Minimal setup() call - metadata comes from pyproject.toml
setup(
    name="pi-sms",  # Must match pyproject.toml
    data_files=data_files,
)
