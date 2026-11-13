#!/usr/bin/env bash
# ==============================================================================
# GA36 Clone / R36S Direct USB Deployer
# Injects the Dashboard agent & boot hooks into a USB-connected console
# ==============================================================================

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_SCRIPT="${REPO_DIR}/ga36_dashboard.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "============================================================"
echo " GA36 Clone Direct USB Mod Deployer"
echo "============================================================"
echo -e "${NC}"

echo -e "${GREEN}[1/3] Scanning for connected console...${NC}"
python3 -c "
import sys, os
sys.path.append('${REPO_DIR}')
from ga36_dashboard import R36SClient

client = R36SClient()
if not client.connect(timeout=10):
    print('[ERROR] Could not connect to console! Please check the USB-C cable.')
    sys.exit(1)

print('[CONNECTED] Console detected! Deploying files...')

# 1. Push agent files
agent_local = '${REPO_DIR}/device/ga36_agent.py'
gadget_local = '${REPO_DIR}/device/usb_gadget_setup.sh'
hook_local = '${REPO_DIR}/device/90-pc_link.conf'
restore_local = '${REPO_DIR}/device/RESTORE_ALL.sh'
splash_png = '${REPO_DIR}/device/splash/splash.png'
splash_raw = '${REPO_DIR}/device/splash/splash.raw'
bootlogo_bmp = '${REPO_DIR}/device/splash/bootlogo.bmp'

client.exec_cmd('mkdir -p /storage/pc_link /storage/.config/profile.d /storage/.config/system.d /storage/.config/splash /storage/roms/backup')

client.push_file(agent_local, '/storage/pc_link/r36s_agent.py')
client.push_file(gadget_local, '/storage/pc_link/usb_gadget_setup.sh')
client.push_file(hook_local, '/storage/.config/profile.d/90-pc_link.conf')
client.push_file(restore_local, '/storage/roms/backup/RESTORE_ALL.sh')

# Push splashes & custom boot branding
print('[*] Installing custom cyber boot splash and loading screens...')
if os.path.exists(splash_png):
    client.push_file(splash_png, '/storage/pc_link/splash.png')
    client.push_file(splash_png, '/storage/.config/splash/splash.png')
    client.push_file(splash_png, '/storage/.config/splash/loading-game.png')
    client.push_file(splash_png, '/storage/.config/splash/loading-game-std.png')
    client.push_file(splash_png, '/storage/.config/splash/splash-std.png')
    client.push_file(splash_png, '/storage/.config/splash/splash-1080.png')
    client.push_file(splash_png, '/storage/.config/splash/retroarch.png')
if os.path.exists(splash_raw):
    client.push_file(splash_raw, '/storage/pc_link/splash.raw')
    client.push_file(splash_raw, '/storage/.config/splash/splash.raw')
    client.push_file(splash_raw, '/storage/.config/splash/splash-1080.raw')

# Remove any old video splashes (like es_start.mp4)
client.exec_cmd('rm -f /storage/.config/splash/*.mp4 /storage/roms/splash/*.mp4 2>/dev/null')

# Mount boot partition and update bootlogo.bmp if available
if os.path.exists(bootlogo_bmp):
    client.exec_cmd('mkdir -p /tmp/volumn && mount /dev/mmcblk0p2 /tmp/volumn 2>/dev/null || true')
    client.push_file(bootlogo_bmp, '/tmp/volumn/bootlogo.bmp')
    client.exec_cmd('umount /tmp/volumn 2>/dev/null || true')

client.exec_cmd('chmod +x /storage/pc_link/*.py /storage/pc_link/*.sh /storage/roms/backup/*.sh')

# 2. Mask EmulationStation
print('[2/3] Masking EmulationStation (direct zero-flicker boot)...')
client.exec_cmd('systemctl mask emustation.service 2>/dev/null; systemctl stop emustation.service 2>/dev/null')

# 3. Audio hardware mute & clean
print('[3/3] Disabling audio daemons and silencing hardware output...')
client.exec_cmd('killall -9 pulseaudio 2>/dev/null ; ln -sf /dev/null /storage/.config/system.d/pulseaudio.service ; ln -sf /dev/null /etc/systemd/system/pulseaudio.service 2>/dev/null')
client.exec_cmd('amixer cset name=\"Speaker Function\" headset 2>/dev/null ; amixer cset name=\"Audio speaker out\" 0 2>/dev/null ; amixer cset name=\"Master Playback Volume\" 0 2>/dev/null ; true')
client.exec_cmd('sed -i \"s/audio.bgmusic=1/audio.bgmusic=0/g\" /storage/.config/emuelec/configs/emuelec.conf 2>/dev/null ; sed -i \"s/audio.volume=.*/audio.volume=0/g\" /storage/.config/emuelec/configs/emuelec.conf 2>/dev/null')
client.exec_cmd('rm -rf /storage/assets/sounds /storage/roms/bgm ; find /storage -type f \\( -name \"*.mp3\" -o -name \"*.wav\" -o -name \"*.ogg\" -o -name \"*.flac\" -o -name \"*.aac\" -o -name \"*.m4a\" \\) -delete 2>/dev/null ; true')

client.exec_cmd('sync')
client.close()
print('\n[SUCCESS] GA36 console successfully configured for Dashboard mode!')
"

echo -e "${GREEN}${BOLD}Console is ready! You can now start with './install.sh start'.${NC}"
