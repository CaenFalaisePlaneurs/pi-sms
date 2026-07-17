#!/bin/sh
# One-command installer for pi-sms on Raspberry Pi.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/CaenFalaisePlaneurs/pi-sms/main/scripts/install.sh | sh
#
# Automates the manual steps from the README: installs the base tools, creates a
# virtual environment, installs the package from GitHub, and runs the setup
# (modem network profile, systemd service, config template). It uses the
# packaged config.example.yaml by default; edit /etc/pi-sms/config.yaml
# afterwards and start the service with: sudo systemctl start pi-sms
#
# Overridable via environment variables:
#   REPO_URL  git repository to install from
#   BRANCH    branch to install (default: main)
#   VENV_DIR  virtual environment location (default: $HOME/pi-sms-venv)

set -e

REPO_URL="${REPO_URL:-https://github.com/CaenFalaisePlaneurs/pi-sms.git}"
BRANCH="${BRANCH:-main}"
VENV_DIR="${VENV_DIR:-$HOME/pi-sms-venv}"

echo "========================================"
echo "  pi-sms one-command installer"
echo "========================================"

echo "[1/4] Installing base tools (python3-venv, git, network-manager)..."
sudo apt-get update
sudo apt-get install -y python3-venv git network-manager

echo "[2/4] Creating virtual environment at ${VENV_DIR}..."
python3 -m venv "${VENV_DIR}"

echo "[3/4] Installing pi-sms from ${REPO_URL} (${BRANCH})..."
"${VENV_DIR}/bin/pip" install --upgrade pip
if "${VENV_DIR}/bin/pip" show pi-sms >/dev/null 2>&1; then
  # Already installed: plain "pip install git+URL" treats a same-name package as
  # satisfied and skips reinstalling, even when the underlying commit changed, so
  # force a real reinstall to pick up new code. --no-deps skips unchanged dependencies.
  "${VENV_DIR}/bin/pip" install --upgrade --force-reinstall --no-deps "git+${REPO_URL}@${BRANCH}"
else
  "${VENV_DIR}/bin/pip" install "git+${REPO_URL}@${BRANCH}"
fi

echo "[4/4] Running setup (requires root to write system files)..."
sudo "${VENV_DIR}/bin/python" -m pi_sms.setup.setup

echo ""
echo "========================================"
echo "  Installation complete"
echo "========================================"
echo "Next steps:"
echo "  1. Edit configuration:  sudo nano /etc/pi-sms/config.yaml"
echo "  2. Start the service:   sudo systemctl start pi-sms"
echo "  3. Check status:        sudo systemctl status pi-sms"
