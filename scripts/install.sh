#!/bin/bash
# One-command installation script for pi-sms on Raspberry Pi
# Usage: curl -fsSL https://raw.githubusercontent.com/nmassart/pi-sms/main/scripts/install.sh | sudo bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

INSTALL_DIR="/opt/pi-sms"
CONFIG_DIR="/etc/pi-sms"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
SERVICE_USER="${SUDO_USER:-$USER}"
REPO_URL="${REPO_URL:-https://github.com/nmassart/pi-sms.git}"
BRANCH="${BRANCH:-main}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  pi-sms Installer${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Note: This script requires sudo privileges.${NC}"
    echo "Please run with: sudo bash install.sh"
    exit 1
fi

echo -e "${GREEN}[1/6]${NC} Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Found Python ${PYTHON_VERSION}"
if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo -e "${RED}Error: Python 3.11 or higher is required. Found ${PYTHON_VERSION}${NC}"
    exit 1
fi

echo -e "${GREEN}[2/6]${NC} Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv git network-manager > /dev/null 2>&1 || {
    echo -e "${RED}Error: Failed to install system dependencies${NC}"
    exit 1
}
echo "System dependencies installed"

echo -e "${GREEN}[3/6]${NC} Setting up installation directory..."
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}Directory $INSTALL_DIR already exists.${NC}"
    read -p "Do you want to update the existing installation? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Installation cancelled."
        exit 0
    fi
    cd "$INSTALL_DIR"
    if [ -d ".git" ]; then
        git pull origin "$BRANCH" || echo "Warning: Could not pull latest changes"
    fi
else
    mkdir -p "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    if [ -n "$REPO_URL" ] && [ "$REPO_URL" != "https://github.com/nmassart/pi-sms.git" ]; then
        git clone -b "$BRANCH" "$REPO_URL" . || {
            echo -e "${RED}Error: Failed to clone repository${NC}"
            exit 1
        }
    else
        echo -e "${YELLOW}Warning: REPO_URL not set. Please copy project files manually to $INSTALL_DIR${NC}"
        exit 1
    fi
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
echo "Installation directory ready"

echo -e "${GREEN}[4/6]${NC} Creating Python virtual environment..."
if [ -d "$INSTALL_DIR/venv" ]; then
    echo "Virtual environment already exists, skipping..."
else
    sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/venv"
    echo "Virtual environment created"
fi

echo -e "${GREEN}[5/6]${NC} Installing Python dependencies..."
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip setuptools wheel -q
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR" -q
echo "Python dependencies installed"

echo -e "${GREEN}[6/6]${NC} Running pi-sms setup (modem network, systemd service, config)..."
"$INSTALL_DIR/venv/bin/pi-sms-setup"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Edit configuration: sudo nano $CONFIG_FILE"
echo "2. Check status: sudo systemctl status pi-sms"
echo "3. View logs: sudo journalctl -u pi-sms -f"
