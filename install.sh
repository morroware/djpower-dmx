#!/bin/bash
# ============================================
# DMX Controller - Automated Installer
# For Raspberry Pi (tested on Pi 5 / Bookworm)
# ============================================
set -e

INSTALL_DIR="/opt/dmx"
SERVICE_NAME="dmx"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ------------------------------------------
# Pre-flight checks
# ------------------------------------------
if [ "$EUID" -ne 0 ]; then
    err "This script must be run as root.  Try:  sudo ./install.sh"
    exit 1
fi

echo ""
echo "========================================"
echo "  DMX Controller Installer"
echo "  DJPOWER H-IP20V Fog Machine"
echo "========================================"
echo ""
info "Install directory : ${INSTALL_DIR}"
info "Service user      : ${RUN_USER}"
info "Systemd unit      : ${SERVICE_FILE}"
echo ""

# ------------------------------------------
# 1. System packages
# ------------------------------------------
info "Updating package list..."
apt-get update -qq

info "Installing system dependencies..."
apt-get install -y -qq \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    libusb-1.0-0-dev \
    libgpiod-dev \
    git > /dev/null 2>&1
ok "System packages installed"

# ------------------------------------------
# 2. FTDI udev rules (non-root USB access)
# ------------------------------------------
UDEV_RULES="/etc/udev/rules.d/99-ftdi-dmx.rules"
if [ ! -f "$UDEV_RULES" ]; then
    info "Creating FTDI udev rules..."
    cat > "$UDEV_RULES" <<'UDEV'
# ENTTEC Open DMX USB / FTDI devices — allow non-root access
SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="6001", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="6014", MODE="0666", GROUP="plugdev"
UDEV
    udevadm control --reload-rules
    udevadm trigger
    ok "udev rules installed (unplug/replug the ENTTEC adapter)"
else
    ok "udev rules already exist"
fi

# Add user to plugdev group
if ! id -nG "$RUN_USER" | grep -qw plugdev; then
    usermod -aG plugdev "$RUN_USER"
    ok "Added ${RUN_USER} to plugdev group"
fi

# ------------------------------------------
# 3. Copy application files
# ------------------------------------------
info "Installing application to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"
cp -f "$SCRIPT_DIR"/app.py       "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR"/index.html   "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR"/start.sh     "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR"/requirements.txt "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/start.sh"
chown -R "$RUN_USER":"$RUN_USER" "$INSTALL_DIR"
ok "Application files copied"

# ------------------------------------------
# 4. Python virtual environment & packages
# ------------------------------------------
info "Setting up Python virtual environment..."
sudo -u "$RUN_USER" python3 -m venv "$INSTALL_DIR/venv"

info "Installing Python dependencies..."
sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
ok "Python dependencies installed"

# ------------------------------------------
# 5. Systemd service
# ------------------------------------------
info "Creating systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=DMX Controller - DJPOWER H-IP20V
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/app.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Give access to USB and GPIO
SupplementaryGroups=plugdev gpio

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
ok "Systemd service created and enabled"

# ------------------------------------------
# 6. Start the service
# ------------------------------------------
info "Starting DMX controller service..."
systemctl restart "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service is running"
else
    warn "Service may not have started (ENTTEC adapter might not be plugged in)"
    warn "Check logs with:  sudo journalctl -u ${SERVICE_NAME} -f"
fi

# ------------------------------------------
# Done
# ------------------------------------------
echo ""
echo "========================================"
echo -e "  ${GREEN}Installation complete!${NC}"
echo "========================================"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status  ${SERVICE_NAME}   # Check status"
echo "    sudo systemctl restart ${SERVICE_NAME}   # Restart"
echo "    sudo systemctl stop    ${SERVICE_NAME}   # Stop"
echo "    sudo journalctl -u ${SERVICE_NAME} -f    # View logs"
echo ""

# Detect IP for convenience
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "$IP" ]; then
    echo -e "  Web interface: ${CYAN}http://${IP}:5000${NC}"
else
    echo "  Web interface: http://<your-pi-ip>:5000"
fi

echo ""
echo "  The service starts automatically on boot."
echo "  Plug in the ENTTEC Open DMX USB adapter and"
echo "  connect your DJPOWER H-IP20V fog machine."
echo ""
