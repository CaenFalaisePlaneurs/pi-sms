#!/bin/bash
# Uninstall script for pi-sms
# Usage: sudo bash uninstall.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

INSTALL_DIR="/opt/pi-sms"
CONFIG_DIR="/etc/pi-sms"
SERVICE_NAME="pi-sms"
MODEM_PROFILE="pi-sms-modem"

echo -e "${RED}========================================${NC}"
echo -e "${RED}  pi-sms Uninstaller${NC}"
echo -e "${RED}========================================${NC}"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Note: This script requires sudo privileges.${NC}"
    echo "Please run with: sudo bash uninstall.sh"
    exit 1
fi

echo -e "${YELLOW}This will completely remove the pi-sms installation.${NC}"
echo ""
echo "The following will be removed:"
echo "  - Systemd service ($SERVICE_NAME)"
echo "  - Modem network profile ($MODEM_PROFILE)"
echo "  - Installation directory ($INSTALL_DIR)"
echo ""
echo -e "${GREEN}The following will be preserved:${NC}"
echo "  - Configuration directory ($CONFIG_DIR) - following Debian best practices"
echo ""
read -p "Are you sure you want to continue? (yes/NO) " -r
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "Uninstallation cancelled."
    exit 0
fi

echo -e "${GREEN}[1/4]${NC} Stopping and disabling systemd service..."
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
    echo "Service stopped"
else
    echo "Service was not running"
fi
if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl disable "$SERVICE_NAME"
    echo "Service disabled"
else
    echo "Service was not enabled"
fi

echo -e "${GREEN}[2/4]${NC} Removing systemd service file..."
if [ -f "/etc/systemd/system/$SERVICE_NAME.service" ]; then
    rm -f "/etc/systemd/system/$SERVICE_NAME.service"
    systemctl daemon-reload
    systemctl reset-failed 2>/dev/null || true
    echo "Service file removed"
else
    echo "Service file not found (may have been already removed)"
fi

echo -e "${GREEN}[3/4]${NC} Removing modem network profile..."
if nmcli connection show "$MODEM_PROFILE" > /dev/null 2>&1; then
    nmcli connection delete "$MODEM_PROFILE"
    echo "Network profile removed"
else
    echo "Network profile not found (may have been already removed)"
fi

echo -e "${GREEN}[4/4]${NC} Removing installation directory..."
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "Installation directory removed"
else
    echo "Installation directory not found"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Uninstallation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
if [ -d "$CONFIG_DIR" ]; then
    echo -e "${GREEN}Your configuration has been preserved at:${NC}"
    echo "  $CONFIG_DIR/config.yaml"
    echo ""
    echo "Following Debian best practices, configuration files are not removed."
    echo "If you reinstall, your existing configuration will be used."
fi
