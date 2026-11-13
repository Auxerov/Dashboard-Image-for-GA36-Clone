#!/usr/bin/env bash
# ==============================================================================
# GA36 Clone / R36S Direct SD Card File Deployer
# Automatically copies the Dashboard boot hook & agent files to an SD card
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
echo " GA36 Clone SD Card Direct File Deployer"
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
        mkdir -p /tmp/ga36_storage
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

echo -e "${GREEN}[1/3] Copying dashboard files -> ${STORAGE_MOUNT}...${NC}"
mkdir -p "${STORAGE_MOUNT}/pc_link"
mkdir -p "${STORAGE_MOUNT}/.config/profile.d"
mkdir -p "${STORAGE_MOUNT}/.config/system.d"
mkdir -p "${STORAGE_MOUNT}/.config/splash"

cp -v "${REPO_DIR}/device/ga36_agent.py" "${STORAGE_MOUNT}/pc_link/r36s_agent.py"
cp -v "${REPO_DIR}/device/usb_gadget_setup.sh" "${STORAGE_MOUNT}/pc_link/usb_gadget_setup.sh"
cp -v "${REPO_DIR}/device/90-pc_link.conf" "${STORAGE_MOUNT}/.config/profile.d/90-pc_link.conf"
cp -v "${OVERLAY_DIR}/storage/.config/splash/"* "${STORAGE_MOUNT}/.config/splash/" 2>/dev/null || true
cp -v "${REPO_DIR}/device/splash/splash.png" "${STORAGE_MOUNT}/pc_link/splash.png" 2>/dev/null || true
cp -v "${REPO_DIR}/device/splash/splash.raw" "${STORAGE_MOUNT}/pc_link/splash.raw" 2>/dev/null || true
cp -v "${REPO_DIR}/device/splash/bootlogo.bmp" "${STORAGE_MOUNT}/pc_link/bootlogo.bmp" 2>/dev/null || true

# Also copy splash to all game loading screens
for target in "${STORAGE_MOUNT}/.config/splash/loading-game.png" \
              "${STORAGE_MOUNT}/.config/splash/loading-game-std.png" \
              "${STORAGE_MOUNT}/.config/splash/splash-std.png" \
              "${STORAGE_MOUNT}/.config/splash/splash-1080.png" \
              "${STORAGE_MOUNT}/.config/splash/retroarch.png"; do
    cp -v "${REPO_DIR}/device/splash/splash.png" "$target" 2>/dev/null || true
done

# Flash bootloader partition (partition 2 - Volumn) with custom bootlogo.bmp
FLASHED_BOOT=0
for mount in $(lsblk -n -o MOUNTPOINT 2>/dev/null | grep -v "^$"); do
    if [ -f "$mount/bootlogo.bmp" ] || [ -d "$mount/bat" ]; then
        echo -e "${GREEN}[*] Found Boot Partition (Volumn) at ${mount}, flashing bootlogo.bmp...${NC}"
        cp -v "${REPO_DIR}/device/splash/bootlogo.bmp" "${mount}/bootlogo.bmp"
        if [ -d "${mount}/bat" ]; then
            cp -v "${REPO_DIR}/device/splash/bootlogo.bmp" "${mount}/bat/low_pwr.bmp" 2>/dev/null || true
        fi
        FLASHED_BOOT=1
    fi
    if [ -f "$mount/low_pwr.bmp" ] && [ -f "$mount/SYSTEM" ]; then
        echo -e "${GREEN}[*] Found ARKOS system partition at ${mount}, flashing low_pwr.bmp...${NC}"
        cp -v "${REPO_DIR}/device/splash/bootlogo.bmp" "${mount}/low_pwr.bmp"
    fi
done

# If partition 2 wasn't mounted, try mounting it
if [ "$FLASHED_BOOT" -eq 0 ]; then
    VOL_CAND=$(lsblk -rn -o NAME,LABEL 2>/dev/null | grep -i -E "Volumn|bootloader" | awk '{print $1}')
    if [ -n "$VOL_CAND" ]; then
        mkdir -p /tmp/ga36_volumn
        sudo mount "/dev/$VOL_CAND" /tmp/ga36_volumn 2>/dev/null || mount "/dev/$VOL_CAND" /tmp/ga36_volumn 2>/dev/null || true
        if [ -f "/tmp/ga36_volumn/bootlogo.bmp" ] || [ -d "/tmp/ga36_volumn/bat" ]; then
            echo -e "${GREEN}[*] Mounted /dev/$VOL_CAND, flashing bootlogo.bmp...${NC}"
            cp -v "${REPO_DIR}/device/splash/bootlogo.bmp" "/tmp/ga36_volumn/bootlogo.bmp"
            [ -d "/tmp/ga36_volumn/bat" ] && cp -v "${REPO_DIR}/device/splash/bootlogo.bmp" "/tmp/ga36_volumn/bat/low_pwr.bmp" 2>/dev/null || true
            sync
            sudo umount /tmp/ga36_volumn 2>/dev/null || umount /tmp/ga36_volumn 2>/dev/null || true
        fi
    fi
fi

chmod +x "${STORAGE_MOUNT}/pc_link/"*.py "${STORAGE_MOUNT}/pc_link/"*.sh

echo -e "${GREEN}[2/3] Applying zero-flicker and silent boot masks...${NC}"
ln -sf /dev/null "${STORAGE_MOUNT}/.config/system.d/emustation.service" 2>/dev/null || true
ln -sf /dev/null "${STORAGE_MOUNT}/.config/system.d/pulseaudio.service" 2>/dev/null || true

# Remove old lock file if left over
rm -f "${STORAGE_MOUNT}/.r36s_link_lock" 2>/dev/null || true

echo -e "${GREEN}[3/3] Flushing changes to disk (sync)...${NC}"
sync

echo ""
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD} [SUCCESS] GA36 SD Card Configured for Dashboard Mode!${NC}"
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo "Safely eject the card, insert it into your console, and power on."
echo "Then simply run: ./install.sh start"
echo -e "${GREEN}============================================================${NC}"
