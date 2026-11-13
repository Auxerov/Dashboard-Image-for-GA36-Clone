#!/usr/bin/env bash
# ==============================================================================
# GA36 Clone / R36S Direct SD Card File Deployer & System Healer
# Automatically configures instant USB PC Link, early boot services & custom splash
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "============================================================"
echo " GA36 Clone SD Card Direct File Deployer & Healer"
echo "============================================================"
echo -e "${NC}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OVERLAY_DIR="${REPO_DIR}/sdcard_overlay"

# Check mounted locations for STORAGE partition (partition 8)
STORAGE_MOUNT=""
for mount in $(lsblk -n -o MOUNTPOINT 2>/dev/null | grep -v "^$"); do
    if [ -d "$mount/pc_link" ] || [ -d "$mount/.config/emuelec" ] || [ -d "$mount/.config/profile.d" ]; then
        STORAGE_MOUNT="$mount"
        break
    fi
done

# If not mounted, try finding an unmounted partition with label STORAGE
if [ -z "$STORAGE_MOUNT" ]; then
    DEV_CAND=$(lsblk -rn -o NAME,LABEL 2>/dev/null | grep -i "STORAGE" | awk '{print $1}')
    if [ -n "$DEV_CAND" ]; then
        DEV_PATH="/dev/$DEV_CAND"
        echo -e "${YELLOW}[*] Found STORAGE partition device: $DEV_PATH (unmounted). Mounting to /tmp/ga36_storage...${NC}"
        sudo mkdir -p /tmp/ga36_storage
        sudo mount "$DEV_PATH" /tmp/ga36_storage 2>/dev/null || mount "$DEV_PATH" /tmp/ga36_storage 2>/dev/null || true
        if [ -d "/tmp/ga36_storage/.config" ]; then
            STORAGE_MOUNT="/tmp/ga36_storage"
        fi
    fi
fi

if [ -z "$STORAGE_MOUNT" ]; then
    echo -e "${YELLOW}Could not automatically detect the 'STORAGE' partition of the SD card.${NC}"
    echo "Please enter the mount directory of the STORAGE partition (e.g. /media/user/STORAGE): "
    read -rp "Mount path: " STORAGE_MOUNT
fi

if [ ! -d "$STORAGE_MOUNT" ]; then
    echo -e "${RED}[ERROR] Directory '$STORAGE_MOUNT' does not exist!${NC}"
    exit 1
fi

SUDO_CMD=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO_CMD="sudo"
fi

echo -e "${GREEN}[1/4] Copying dashboard files -> ${STORAGE_MOUNT}...${NC}"
$SUDO_CMD mkdir -p "${STORAGE_MOUNT}/pc_link"
$SUDO_CMD mkdir -p "${STORAGE_MOUNT}/.config/profile.d"
$SUDO_CMD mkdir -p "${STORAGE_MOUNT}/.config/system.d"
$SUDO_CMD mkdir -p "${STORAGE_MOUNT}/.config/splash"

$SUDO_CMD cp -vf "${REPO_DIR}/device/ga36_agent.py" "${STORAGE_MOUNT}/pc_link/r36s_agent.py"
$SUDO_CMD cp -vf "${REPO_DIR}/device/usb_gadget_setup.sh" "${STORAGE_MOUNT}/pc_link/usb_gadget_setup.sh"
$SUDO_CMD cp -vf "${REPO_DIR}/device/90-pc_link.conf" "${STORAGE_MOUNT}/.config/profile.d/90-pc_link.conf"
$SUDO_CMD cp -vf "${REPO_DIR}/device/splash/splash.png" "${STORAGE_MOUNT}/pc_link/splash.png" 2>/dev/null || true
$SUDO_CMD cp -vf "${REPO_DIR}/device/splash/splash.raw" "${STORAGE_MOUNT}/pc_link/splash.raw" 2>/dev/null || true
$SUDO_CMD cp -vf "${REPO_DIR}/device/splash/bootlogo.bmp" "${STORAGE_MOUNT}/pc_link/bootlogo.bmp" 2>/dev/null || true

# Create guaranteed boot hooks for EmuELEC & LibreELEC
cat << 'EOF' | $SUDO_CMD tee "${STORAGE_MOUNT}/.config/custom_start.sh" >/dev/null
#!/bin/bash
# GA36 PC Link Early Boot Hook (custom_start.sh)
if mkdir /tmp/.r36s_link_lock 2>/dev/null; then
    (
        if [ -f /storage/pc_link/splash.raw ]; then
            cat /storage/pc_link/splash.raw > /dev/fb0 2>/dev/null || true
        fi
        if [ -f /storage/pc_link/usb_gadget_setup.sh ]; then
            /bin/bash /storage/pc_link/usb_gadget_setup.sh >> /storage/usb_boot.log 2>&1
        fi
        SERIAL_DEV="/dev/ttyGS0"
        for i in $(seq 1 15); do
            for cand in /dev/ttyGS0 /dev/ttygserial /dev/ttyGS* /dev/ttyACM*; do
                [ -e "$cand" ] && SERIAL_DEV="$cand" && break 2
            done
            sleep 0.2
        done
        if [ -f /storage/pc_link/r36s_agent.py ]; then
            python3 /storage/pc_link/r36s_agent.py --daemon --dev "$SERIAL_DEV" --port 8888 >> /storage/usb_boot.log 2>&1 &
        fi
    ) >/dev/null 2>&1 &
fi
EOF

$SUDO_CMD cp -vf "${STORAGE_MOUNT}/.config/custom_start.sh" "${STORAGE_MOUNT}/.config/autostart.sh" 2>/dev/null || true
$SUDO_CMD chmod +x "${STORAGE_MOUNT}/.config/custom_start.sh" "${STORAGE_MOUNT}/.config/autostart.sh" 2>/dev/null || true

# Clean up any broken systemd locks or deadlocks
$SUDO_CMD rm -f "${STORAGE_MOUNT}/.config/system.d/ga36-link.service" 2>/dev/null || true
$SUDO_CMD rm -f "${STORAGE_MOUNT}/.config/system.d/basic.target.wants/ga36-link.service" 2>/dev/null || true
$SUDO_CMD rm -f "${STORAGE_MOUNT}/storage/pc_link/launch_dashboard.sh" 2>/dev/null || true

# Copy splash to all game loading and system splash screens
for target in "${STORAGE_MOUNT}/.config/splash/loading-game.png" \
              "${STORAGE_MOUNT}/.config/splash/loading-game-std.png" \
              "${STORAGE_MOUNT}/.config/splash/splash-std.png" \
              "${STORAGE_MOUNT}/.config/splash/splash-1080.png" \
              "${STORAGE_MOUNT}/.config/splash/retroarch.png"; do
    $SUDO_CMD cp -vf "${REPO_DIR}/device/splash/splash.png" "$target" 2>/dev/null || true
done
[ -f "${REPO_DIR}/device/splash/splash.raw" ] && $SUDO_CMD cp -vf "${REPO_DIR}/device/splash/splash.raw" "${STORAGE_MOUNT}/.config/splash/splash.raw" 2>/dev/null || true
[ -f "${REPO_DIR}/device/splash/splash.raw" ] && $SUDO_CMD cp -vf "${REPO_DIR}/device/splash/splash.raw" "${STORAGE_MOUNT}/.config/splash/splash-1080.raw" 2>/dev/null || true

$SUDO_CMD chmod +x "${STORAGE_MOUNT}/pc_link/"*.py "${STORAGE_MOUNT}/pc_link/"*.sh

echo -e "${GREEN}[2/4] Configuring zero-retro UI (Replacing EmulationStation with Dashboard Agent)...${NC}"
# CRITICAL: Never mask emustation to /dev/null because systemd autostart deadlocks on it.
# Instead, replace emustation.service with our lightweight dashboard agent unit.
cat << 'EOF' | $SUDO_CMD tee "${STORAGE_MOUNT}/.config/system.d/emustation.service" >/dev/null
[Unit]
Description=GA36 Dedicated PC Dashboard Agent
After=basic.target

[Service]
Type=simple
Environment=HOME=/storage
ExecStartPre=-/bin/bash /storage/pc_link/usb_gadget_setup.sh
ExecStart=/usr/bin/python3 /storage/pc_link/r36s_agent.py --daemon --dev /dev/ttyGS0 --port 8888
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

$SUDO_CMD rm -f "${STORAGE_MOUNT}/.r36s_link_lock" 2>/dev/null || true

# Flash bootloader partition (partition 2 - Volumn) with custom bootlogo.bmp
FLASHED_BOOT=0
for mount in $(lsblk -n -o MOUNTPOINT 2>/dev/null | grep -v "^$"); do
    if [ -f "$mount/bootlogo.bmp" ] || [ -d "$mount/bat" ]; then
        echo -e "${GREEN}[*] Found Boot Partition (Volumn) at ${mount}, flashing bootlogo.bmp...${NC}"
        $SUDO_CMD cp -vf "${REPO_DIR}/device/splash/bootlogo.bmp" "${mount}/bootlogo.bmp"
        if [ -d "${mount}/bat" ]; then
            $SUDO_CMD cp -vf "${REPO_DIR}/device/splash/bootlogo.bmp" "${mount}/bat/low_pwr.bmp" 2>/dev/null || true
        fi
        FLASHED_BOOT=1
    fi
    if [ -f "$mount/low_pwr.bmp" ] && [ -f "$mount/SYSTEM" ]; then
        echo -e "${GREEN}[*] Found ARKOS system partition at ${mount}, flashing low_pwr.bmp...${NC}"
        $SUDO_CMD cp -vf "${REPO_DIR}/device/splash/bootlogo.bmp" "${mount}/low_pwr.bmp"
    fi
done

# If partition 2 wasn't mounted, try mounting it
if [ "$FLASHED_BOOT" -eq 0 ]; then
    VOL_CAND=$(lsblk -rn -o NAME,LABEL 2>/dev/null | grep -i -E "Volumn|bootloader" | awk '{print $1}')
    if [ -n "$VOL_CAND" ]; then
        sudo mkdir -p /tmp/ga36_volumn
        sudo mount "/dev/$VOL_CAND" /tmp/ga36_volumn 2>/dev/null || mount "/dev/$VOL_CAND" /tmp/ga36_volumn 2>/dev/null || true
        if [ -f "/tmp/ga36_volumn/bootlogo.bmp" ] || [ -d "/tmp/ga36_volumn/bat" ]; then
            echo -e "${GREEN}[*] Mounted /dev/$VOL_CAND, flashing bootlogo.bmp...${NC}"
            sudo cp -vf "${REPO_DIR}/device/splash/bootlogo.bmp" "/tmp/ga36_volumn/bootlogo.bmp"
            [ -d "/tmp/ga36_volumn/bat" ] && sudo cp -vf "${REPO_DIR}/device/splash/bootlogo.bmp" "/tmp/ga36_volumn/bat/low_pwr.bmp" 2>/dev/null || true
            sync
            sudo umount /tmp/ga36_volumn 2>/dev/null || umount /tmp/ga36_volumn 2>/dev/null || true
        fi
    fi
fi

echo -e "${GREEN}[4/5] Purging all ROMs and media files from games partition...${NC}"
if [ -f "${REPO_DIR}/scripts/clean_roms.sh" ]; then
    bash "${REPO_DIR}/scripts/clean_roms.sh" || true
fi

echo -e "${GREEN}[5/5] Flushing all filesystem buffers (sync)...${NC}"
sync

echo ""
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD} [SUCCESS] GA36 SD Card Healed & Configured!${NC}"
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo "1. Safely eject the card from your PC."
echo "2. Insert it into your console and turn it ON."
echo "3. Connect the USB cable to the OTG port."
echo "4. Run: ./install.sh start"
echo -e "${GREEN}============================================================${NC}"
