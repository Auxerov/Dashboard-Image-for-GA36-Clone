#!/usr/bin/env bash
# ==============================================================================
# GA36 Clone / R36S SD Card Full Image Backup & Shrink Utility
# Creates a clean, compressed flashable .img.xz backup of the SD Card
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
echo " GA36 Clone / R36S Full Disk Image Backup Utility"
echo "============================================================"
echo -e "${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] This script requires 'sudo' privileges to access raw block devices (SD cards).${NC}"
    echo "Please execute: sudo ./scripts/backup_image.sh"
    exit 1
fi

TARGET_DEV="$1"

if [ -z "$TARGET_DEV" ]; then
    echo -e "${YELLOW}[SCAN] Scanning connected storage block devices...${NC}"
    echo ""
    lsblk -d -o NAME,SIZE,MODEL,TRAN,RM
    echo ""
    read -rp "Please enter the target SD card device name (e.g. sdc or mmcblk0): " INPUT_DEV
    TARGET_DEV="/dev/${INPUT_DEV#/dev/}"
fi

if [ ! -b "$TARGET_DEV" ]; then
    echo -e "${RED}[ERROR] The specified device '$TARGET_DEV' is not a valid block device!${NC}"
    exit 1
fi

# Sanity check: prevent backing up system root disk
ROOT_DEV=$(findmnt -n -o SOURCE / | sed 's/p[0-9]*$//; s/[0-9]*$//')
if [ "$TARGET_DEV" = "$ROOT_DEV" ]; then
    echo -e "${RED}[WARNING] '$TARGET_DEV' is your primary PC system drive! Operation aborted.${NC}"
    exit 1
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_DIR="${PWD}/backups"
mkdir -p "$OUTPUT_DIR"
RAW_IMG="${OUTPUT_DIR}/GA36_Dashboard_Backup_${TIMESTAMP}.img"
XZ_IMG="${RAW_IMG}.xz"

echo -e "${GREEN}[1/4] Checking and unmounting partitions on ${TARGET_DEV}...${NC}"
for part in "${TARGET_DEV}"* "${TARGET_DEV}"p*; do
    if [ -b "$part" ]; then
        umount "$part" 2>/dev/null || true
    fi
done

echo -e "${GREEN}[2/4] Dumping SD Card disk image -> ${RAW_IMG}${NC}"
echo -e "${CYAN}Depending on the card capacity and speed, this may take a few minutes...${NC}"

if command -v pv >/dev/null 2>&1; then
    pv -tpreb "$TARGET_DEV" | dd of="$RAW_IMG" bs=4M conv=fsync status=none
else
    dd if="$TARGET_DEV" of="$RAW_IMG" bs=4M status=progress conv=fsync
fi

echo -e "${GREEN}[3/4] Compressing image (XZ Ultra)...${NC}"
if command -v pixz >/dev/null 2>&1; then
    pixz -9 "$RAW_IMG" "$XZ_IMG"
    rm -f "$RAW_IMG"
elif command -v xz >/dev/null 2>&1; then
    xz -T0 -9 -v "$RAW_IMG"
else
    echo -e "${YELLOW}'xz' not found, falling back to gzip...${NC}"
    gzip -9 "$RAW_IMG"
    XZ_IMG="${RAW_IMG}.gz"
fi

echo ""
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD} [SUCCESS] Image Backup Created Successfully!${NC}"
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo -e " Location: ${CYAN}${XZ_IMG}${NC}"
echo -e " Size    : $(du -h "$XZ_IMG" | cut -f1)"
echo ""
echo "You can flash this image to any SD card with BalenaEtcher or Rufus."
echo "To publish as a GitHub Release:"
echo -e "  ${YELLOW}gh release create v1.0.0 \"$XZ_IMG\" --title \"GA36 Clone Cyber Dashboard OS\"${NC}"
echo -e "${GREEN}============================================================${NC}"
