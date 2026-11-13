#!/usr/bin/env bash
# ==============================================================================
# Automatic SD Card Healer & ROM Purger Daemon
# Watches for SD card insertion and immediately applies all fixes hands-free
# ==============================================================================

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Watching for SD Card insertion..."

while true; do
    # Check if storage partition exists
    STORAGE_DEV=$(lsblk -rn -o NAME,LABEL 2>/dev/null | grep -i "STORAGE" | awk '{print $1}' | head -n 1)
    GAMES_DEV=$(lsblk -rn -o NAME,FSTYPE,SIZE 2>/dev/null | grep -E "sd[a-z]1" | grep -i "vfat" | awk '{print $1}' | head -n 1)

    if [ -n "$STORAGE_DEV" ] || [ -d "/run/media/auxerov/ec39b8c7-7b8c-4e36-880d-b9d188babd91" ]; then
        echo "[!] SD Card Detected! Starting automatic heal & ROM purge..."
        
        # 1. Run flash_sdcard.sh with auto-mounts
        bash "${REPO_DIR}/scripts/flash_sdcard.sh"
        
        echo ""
        echo "============================================================"
        echo " [DONE] AUTOMATIC HEAL COMPLETE! ALL ROMS PURGED!"
        echo " Safely remove the SD card and insert into the console."
        echo "============================================================"
        break
    fi
    sleep 0.5
done
