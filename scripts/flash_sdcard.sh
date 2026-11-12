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

# Check mounted locations
STORAGE_MOUNT=""
for mount in $(lsblk -n -o MOUNTPOINT 2>/dev/null | grep -v "^$"); do
    if [ -d "$mount/pc_link" ] || [ -d "$mount/.config/emuelec" ] || [ -d "$mount/.config/profile.d" ]; then
        STORAGE_MOUNT="$mount"
        break
    fi
done

if [ -z "$STORAGE_MOUNT" ]; then
    echo -e "${YELLOW}SD kartın 'STORAGE' (ext4) bölümü otomatik bulunamadı.${NC}"
    echo "Lütfen SD kartın STORAGE bölümünün bağlı olduğu dizini girin (Örn: /media/user/STORAGE): "
    read -rp "Dizin: " STORAGE_MOUNT
fi

if [ ! -d "$STORAGE_MOUNT" ]; then
    echo -e "${RED}[HATA] '$STORAGE_MOUNT' dizini mevcut değil!${NC}"
    exit 1
fi

echo -e "${GREEN}[1/3] Dashboard dosyaları kopyalanıyor -> ${STORAGE_MOUNT}...${NC}"
mkdir -p "${STORAGE_MOUNT}/pc_link"
mkdir -p "${STORAGE_MOUNT}/.config/profile.d"
mkdir -p "${STORAGE_MOUNT}/.config/system.d"

cp -v "${OVERLAY_DIR}/storage/pc_link/r36s_agent.py" "${STORAGE_MOUNT}/pc_link/"
cp -v "${OVERLAY_DIR}/storage/pc_link/usb_gadget_setup.sh" "${STORAGE_MOUNT}/pc_link/"
cp -v "${OVERLAY_DIR}/storage/.config/profile.d/90-pc_link.conf" "${STORAGE_MOUNT}/.config/profile.d/"
ln -sf /dev/null "${STORAGE_MOUNT}/.config/system.d/emustation.service" 2>/dev/null || true

chmod +x "${STORAGE_MOUNT}/pc_link/"*.py "${STORAGE_MOUNT}/pc_link/"*.sh

echo -e "${GREEN}[2/3] EmulationStation titreme önleme maskesi uygulanıyor...${NC}"
# In case system.d needs direct mask
mkdir -p "${STORAGE_MOUNT}/.config/system.d"
ln -sf /dev/null "${STORAGE_MOUNT}/.config/system.d/emustation.service" 2>/dev/null || true

echo -e "${GREEN}[3/3] Değişiklikler diske yazılıyor (sync)...${NC}"
sync

echo ""
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD} [BAŞARILI] GA36 SD Kartı Dashboard Moduna Geçirildi!${NC}"
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo "Kartı konsolunuza takıp USB kablosuyla bilgisayarınıza bağlayın."
echo "Ardından bilgisayarınızda './install.sh start' çalıştırmanız yeterlidir."
echo -e "${GREEN}============================================================${NC}"
