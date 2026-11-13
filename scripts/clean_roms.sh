#!/usr/bin/env bash
# ==============================================================================
# GA36 / R36S Complete ROM & Media Purger
# Wipes all games, music, and clutter from partition 1 (0001-95D7 / UDISK)
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
echo " GA36 / R36S Complete ROM & Clutter Purger"
echo "============================================================"
echo -e "${NC}"

GAMES_MOUNT=""

# 1. Search mounted partitions for game directories
for mount in $(lsblk -n -o MOUNTPOINT 2>/dev/null | grep -v "^$"); do
    if [ -d "$mount/famicom" ] || [ -d "$mount/nes" ] || [ -d "$mount/psx" ] || [ -d "$mount/gba" ]; then
        GAMES_MOUNT="$mount"
        break
    fi
done

# 2. If unmounted, find partition 1 (usually ~13GB FAT32)
if [ -z "$GAMES_MOUNT" ]; then
    DEV_CAND=$(lsblk -rn -o NAME,FSTYPE,SIZE 2>/dev/null | grep -E "sd[a-z]1" | grep -i "vfat" | awk '{print $1}')
    if [ -n "$DEV_CAND" ]; then
        DEV_PATH="/dev/$DEV_CAND"
        echo -e "${YELLOW}[*] Found FAT32 Games partition at $DEV_PATH. Mounting to /tmp/ga36_games...${NC}"
        sudo mkdir -p /tmp/ga36_games
        sudo mount -o umask=000 "$DEV_PATH" /tmp/ga36_games 2>/dev/null || true
        if [ -d "/tmp/ga36_games/nes" ] || [ -d "/tmp/ga36_games/famicom" ] || [ -d "/tmp/ga36_games/gba" ]; then
            GAMES_MOUNT="/tmp/ga36_games"
        fi
    fi
fi

if [ -z "$GAMES_MOUNT" ]; then
    echo -e "${RED}[!] Could not automatically detect Games partition.${NC}"
    echo "Please enter the mount path of the games partition (e.g. /run/media/auxerov/0001-95D7):"
    read -rp "Mount path: " GAMES_MOUNT
fi

if [ ! -d "$GAMES_MOUNT" ]; then
    echo -e "${RED}[ERROR] Directory '$GAMES_MOUNT' does not exist!${NC}"
    exit 1
fi

echo -e "${YELLOW}[*] Calculating size before purge on ${GAMES_MOUNT}...${NC}"
df -h "$GAMES_MOUNT"

echo -e "${RED}${BOLD}[!] WARNING: Wiping ALL ROMs, game folders, and background music...${NC}"

# Game folders to completely empty/remove
GAME_DIRS=(
    "3do" "alg" "amiga" "amigacd32" "amstradcpc" "amstradgx4000" "arcade" "astrocde"
    "atari2600" "atari5200" "atari7800" "atari800" "atarijaguar" "atarilynx" "atarist" "atarixegs"
    "atomiswave" "bezels" "BGM" "bgmusic" "c128" "c16" "c64" "capcom" "channelf" "coleco"
    "cps1" "cps2" "cps3" "daphne" "doom" "dos" "downloads" "dreamcast" "easyrpg" "famicom"
    "fbneo" "fds" "fmtownsux" "freej2me" "gameandwatch" "gamegear" "gamegearh" "gb" "gba" "gbah"
    "gbc" "gbch" "gbh" "genesis" "genh" "gw" "intellivision" "launchimages" "lowresnx" "mame"
    "mastersystem" "megadrive" "megadrive-japan" "megadrivemsu" "megaduck" "mplayer" "msumd"
    "msx" "msx2" "n64" "n64dd" "naomi" "nds" "neocd" "neogeo" "neogeocd" "nes" "nesh" "ngp" "ngpc"
    "odyssey" "odyssey2" "openbor" "pc" "pc98" "pcengine" "pcenginecd" "pcfx" "pgm2" "pico-8"
    "pokemini" "pokemonmini" "psp" "pspminis" "psx" "satellaview" "saturn" "savestates" "sc-3000"
    "screenshots" "scummvm" "sega32x" "segacd" "sfc" "sg-1000" "sgb" "sgfx" "snes" "snesh"
    "snes-hacks" "snesmsu1" "solarus" "sufami" "supergrafx" "supervision" "tg16" "tg16cd"
    "ti99" "tic-80" "tic80" "turbografx" "turbografxcd" "uzebox" "vectrex" "vic20" "videopac"
    "videos" "virtualboy" "vmu" "wolf" "wonderswan" "wonderswancolor" "x1" "x68000" "zx81" "zxspectrum"
)

SUDO_CMD=""
[ "$(id -u)" -ne 0 ] && SUDO_CMD="sudo"

for dir in "${GAME_DIRS[@]}"; do
    target="${GAMES_MOUNT}/${dir}"
    if [ -d "$target" ]; then
        echo -e "${YELLOW}Purging: ${dir}...${NC}"
        $SUDO_CMD rm -rf "$target"/* 2>/dev/null || true
    fi
done

# Keep empty folder structures or delete them completely
for dir in "${GAME_DIRS[@]}"; do
    target="${GAMES_MOUNT}/${dir}"
    [ -d "$target" ] && $SUDO_CMD rmdir "$target" 2>/dev/null || true
done

# Also delete any loose rom archives or media in root
$SUDO_CMD rm -f "${GAMES_MOUNT}"/*.zip "${GAMES_MOUNT}"/*.7z "${GAMES_MOUNT}"/*.iso "${GAMES_MOUNT}"/*.bin 2>/dev/null || true

echo -e "${GREEN}[*] Flushing disk buffers (sync)...${NC}"
sync

echo ""
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD} [SUCCESS] All ROMs Purged! Current Space on Games Partition:${NC}"
echo -e "${GREEN}${BOLD}============================================================${NC}"
df -h "$GAMES_MOUNT"
echo -e "${GREEN}============================================================${NC}"
