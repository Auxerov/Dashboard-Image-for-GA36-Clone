#!/usr/bin/env bash
# ==============================================================================
# GA36 Clone & R36S PC Dashboard - Master All-in-One Installer & Control Suite
# Repository: https://github.com/Auxerov/Dashboard-Image-for-GA36-Clone
# ==============================================================================

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="ga36-dashboard.service"
LEGACY_SERVICE_NAME="r36s-companion.service"
PYTHON_SCRIPT="${REPO_DIR}/ga36_dashboard.py"
UDEV_RULE_FILE="/etc/udev/rules.d/99-ga36-dashboard.rules"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

print_banner() {
    echo -e "${CYAN}${BOLD}"
    echo "============================================================"
    echo "  ⚡ GA36 Clone PC Cyber Dashboard - Master Suite v3.5"
    echo "============================================================"
    echo -e "${NC}"
}

check_root_for_packages() {
    if command -v sudo >/dev/null 2>&1; then
        SUDO_CMD="sudo"
    else
        SUDO_CMD=""
    fi
}

install_dependencies() {
    echo -e "${GREEN}[1/4] Checking dependencies (Python3, Serial, Pillow, psutil)...${NC}"
    check_root_for_packages

    if command -v apt-get >/dev/null 2>&1; then
        echo -e "${CYAN}APT package manager detected (Debian/Ubuntu/Mint)...${NC}"
        $SUDO_CMD apt-get update -qq || true
        $SUDO_CMD apt-get install -y python3 python3-serial python3-pil python3-psutil >/dev/null 2>&1 || true
    elif command -v pacman >/dev/null 2>&1; then
        echo -e "${CYAN}Pacman package manager detected (Arch/Manjaro)...${NC}"
        $SUDO_CMD pacman -Sy --noconfirm python python-pyserial python-pillow python-psutil >/dev/null 2>&1 || true
    elif command -v dnf >/dev/null 2>&1; then
        echo -e "${CYAN}DNF package manager detected (Fedora/RHEL)...${NC}"
        $SUDO_CMD dnf install -y python3 python3-pyserial python3-pillow python3-psutil >/dev/null 2>&1 || true
    elif command -v zypper >/dev/null 2>&1; then
        echo -e "${CYAN}Zypper package manager detected (openSUSE)...${NC}"
        $SUDO_CMD zypper in -y python3 python3-pyserial python3-Pillow python3-psutil >/dev/null 2>&1 || true
    fi

    # Fallback to pip if missing
    if ! python3 -c "import serial, PIL, psutil" 2>/dev/null; then
        echo -e "${YELLOW}Some Python packages are missing, installing via pip...${NC}"
        python3 -m pip install --user --upgrade pyserial pillow psutil >/dev/null 2>&1 || \
        pip install --user --upgrade pyserial pillow psutil >/dev/null 2>&1 || \
        pip install --break-system-packages --user pyserial pillow psutil >/dev/null 2>&1 || true
    fi

    if python3 -c "import serial, PIL, psutil" 2>/dev/null; then
        echo -e "${GREEN}✓ Required Python dependencies are satisfied!${NC}"
    else
        echo -e "${RED}[WARNING] Python libraries could not be auto-installed. Install manually: pip install pyserial pillow psutil${NC}"
    fi
}

install_udev_rules() {
    echo -e "${GREEN}[2/4] Configuring USB Device Permissions (udev rules)...${NC}"
    check_root_for_packages

    RULE_CONTENT='# GA36 Clone & R36S CDC-ACM USB Serial Permissions
KERNEL=="ttyACM[0-9]*", MODE="0666", GROUP="dialout"
KERNEL=="ttyGS[0-9]*", MODE="0666", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1d6b", ATTRS{idProduct}=="0104", MODE="0666", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="18d1", ATTRS{idProduct}=="0001", MODE="0666", GROUP="dialout"
'

    echo "$RULE_CONTENT" | $SUDO_CMD tee "$UDEV_RULE_FILE" >/dev/null
    $SUDO_CMD udevadm control --reload-rules 2>/dev/null || true
    $SUDO_CMD udevadm trigger 2>/dev/null || true

    # Also add user to dialout/uucp if needed
    if [ -n "$USER" ]; then
        $SUDO_CMD usermod -aG dialout "$USER" 2>/dev/null || true
        $SUDO_CMD usermod -aG uucp "$USER" 2>/dev/null || true
    fi
    echo -e "${GREEN}✓ USB udev rules installed (/dev/ttyACM* accessible without root).${NC}"
}

install_systemd_service() {
    echo -e "${GREEN}[3/4] Installing Host Companion Service (systemd user unit)...${NC}"
    mkdir -p "$SYSTEMD_USER_DIR"

    SERVICE_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
    cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=GA36 Clone & R36S PC Cyber Dashboard Service
After=network.target default.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${PYTHON_SCRIPT} dashboard
Restart=always
RestartSec=3
KillMode=process

[Install]
WantedBy=default.target
EOF

    # Stop any legacy r36s service if present
    systemctl --user stop "$LEGACY_SERVICE_NAME" 2>/dev/null || true
    systemctl --user disable "$LEGACY_SERVICE_NAME" 2>/dev/null || true

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user restart "$SERVICE_NAME"
    echo -e "${GREEN}✓ Service enabled and started in background (${SERVICE_NAME}).${NC}"
}

verify_and_report() {
    echo -e "${GREEN}[4/4] Verifying Device Connection & Status...${NC}"
    sleep 1

    PORT=""
    for cand in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2; do
        if [ -e "$cand" ]; then
            PORT="$cand"
            break
        fi
    done

    echo ""
    echo -e "${GREEN}${BOLD}============================================================${NC}"
    echo -e "${GREEN}${BOLD} 🎉 SETUP COMPLETED SUCCESSFULLY!${NC}"
    echo -e "${GREEN}${BOLD}============================================================${NC}"
    if [ -n "$PORT" ]; then
        echo -e " Device Status: ${GREEN}CONNECTED (${PORT})${NC}"
        echo -e " Stream State : ${GREEN}ACTIVE (640x480 Giant Pie Cyber HUD)${NC}"
    else
        echo -e " Device Status: ${YELLOW}Waiting... (Please plug in GA36 via USB-C and power on)${NC}"
    fi
    echo -e " Autostart    : ${GREEN}Automatically starts on user login / system boot${NC}"
    echo -e "${GREEN}${BOLD}============================================================${NC}"
    echo ""
    echo "Useful Commands:"
    echo -e "  ${CYAN}./install.sh status${NC}     -> View service and connection status"
    echo -e "  ${CYAN}./install.sh mode [theme]${NC} -> Change HUD theme (pie, dual, btop)"
    echo -e "  ${CYAN}./install.sh dashboard${NC}  -> Interactive terminal dashboard & controls"
    echo -e "  ${CYAN}./install.sh stop${NC}       -> Stop display stream"
    echo -e "  ${CYAN}./install.sh start${NC}      -> Start display stream"
    echo -e "  ${CYAN}./install.sh logs${NC}       -> Tail live system logs"
    echo -e "  ${CYAN}./install.sh image <file>${NC} -> Send image / wallpaper to console screen"
    echo -e "  ${CYAN}./install.sh battery${NC}    -> Show battery percentage, voltage & charging"
    echo -e "  ${CYAN}./install.sh shell${NC}      -> Open interactive root shell on console"
    echo -e "  ${CYAN}./install.sh restore${NC}    -> Restore console back to stock gaming menu"
    echo -e "  ${CYAN}./install.sh backup${NC}     -> Create compressed .img.xz SD card backup"
    echo -e "  ${CYAN}./install.sh uninstall${NC}  -> Remove PC service and udev rules"
    echo ""
}

cmd_start() {
    echo -e "${CYAN}Starting Dashboard service...${NC}"
    systemctl --user start "$SERVICE_NAME"
    echo -e "${GREEN}✓ Started.${NC}"
}

cmd_stop() {
    echo -e "${YELLOW}Stopping Dashboard service...${NC}"
    systemctl --user stop "$SERVICE_NAME"
    systemctl --user stop "$LEGACY_SERVICE_NAME" 2>/dev/null || true
    echo -e "${GREEN}✓ Stopped.${NC}"
}

cmd_restart() {
    echo -e "${CYAN}Restarting Dashboard service...${NC}"
    systemctl --user restart "$SERVICE_NAME"
    echo -e "${GREEN}✓ Restarted.${NC}"
}

cmd_status() {
    print_banner
    systemctl --user status "$SERVICE_NAME" --no-pager
    echo ""
    if ls /dev/ttyACM* >/dev/null 2>&1; then
        echo -e "Detected USB Ports: ${GREEN}$(ls /dev/ttyACM*)${NC}"
    else
        echo -e "${YELLOW}No active USB serial port found (ensure USB-C cable is connected).${NC}"
    fi
}

cmd_logs() {
    journalctl --user -u "$SERVICE_NAME" -f
}

cmd_dashboard() {
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    trap 'systemctl --user start "$SERVICE_NAME" 2>/dev/null || true' EXIT
    python3 "$PYTHON_SCRIPT" dashboard
}

cmd_image() {
    if [ -z "$1" ]; then
        echo -e "${RED}[ERROR] No image file specified! Usage: ./install.sh image /path/to/image.png${NC}"
        exit 1
    fi
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    python3 "$PYTHON_SCRIPT" image "$1"
    systemctl --user start "$SERVICE_NAME" 2>/dev/null || true
}

cmd_battery() {
    python3 "$PYTHON_SCRIPT" battery
}

cmd_shell() {
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    python3 "$PYTHON_SCRIPT" shell
    systemctl --user start "$SERVICE_NAME" 2>/dev/null || true
}

cmd_mode() {
    python3 "$PYTHON_SCRIPT" mode "$@"
}

cmd_focus() {
    python3 "$PYTHON_SCRIPT" focus "$@"
}

cmd_gallery() {
    python3 "$PYTHON_SCRIPT" gallery "$@"
}

cmd_restore() {
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    python3 "$PYTHON_SCRIPT" restore
}

cmd_uninstall() {
    print_banner
    echo -e "${YELLOW}Removing GA36 Dashboard PC components...${NC}"
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
    systemctl --user daemon-reload

    check_root_for_packages
    if [ -f "$UDEV_RULE_FILE" ]; then
        $SUDO_CMD rm -f "$UDEV_RULE_FILE"
        $SUDO_CMD udevadm control --reload-rules 2>/dev/null || true
    fi
    echo -e "${GREEN}✓ Service and udev rules cleaned up.${NC}"
}

# --- Main Dispatcher ---
ACTION="${1:-install}"
shift || true

case "$ACTION" in
    install|"")
        print_banner
        install_dependencies
        install_udev_rules
        install_systemd_service
        verify_and_report
        ;;
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart
        ;;
    status)
        cmd_status
        ;;
    logs|log)
        cmd_logs
        ;;
    dashboard|dash|live)
        cmd_dashboard
        ;;
    image|img)
        cmd_image "$@"
        ;;
    battery|pwr)
        cmd_battery
        ;;
    shell|term)
        cmd_shell
        ;;
    restore)
        cmd_restore
        ;;
    mode|theme)
        cmd_mode "$@"
        ;;
    focus)
        cmd_focus "$@"
        ;;
    gallery|gif|art)
        cmd_gallery "$@"
        ;;
    backup)
        sudo "${REPO_DIR}/scripts/backup_image.sh" "$@"
        ;;
    uninstall|remove)
        cmd_uninstall
        ;;
    help|--help|-h)
        print_banner
        echo "Usage: ./install.sh [COMMAND]"
        echo ""
        echo "Commands:"
        echo "  install        -> Full automated setup & start (Default)"
        echo "  start          -> Start background display service"
        echo "  stop           -> Stop background display service"
        echo "  restart        -> Restart service"
        echo "  status         -> Check service and USB connection status"
        echo "  logs           -> Follow live system logs"
        echo "  dashboard      -> Open interactive terminal dashboard & controls"
        echo "  mode [theme]   -> Switch HUD mode (pie, dual, btop, gallery or next)"
        echo "  focus [target] -> Focus single metric view (all, cpu, gpu, ram, net or cycle)"
        echo "  gallery        -> Manage & list media files in ~/Pictures/GA36_Gallery"
        echo "  image <file>   -> Push custom image / wallpaper to console screen"
        echo "  battery        -> Display battery and power status"
        echo "  shell          -> Open interactive root shell on console"
        echo "  restore        -> Restore stock gaming menu on console"
        echo "  backup         -> Create .img.xz backup of SD card"
        echo "  uninstall      -> Remove service and udev configuration"
        ;;
    *)
        echo -e "${RED}[ERROR] Unknown command: '$ACTION'${NC}"
        echo "For help run: ./install.sh --help"
        exit 1
        ;;
esac
