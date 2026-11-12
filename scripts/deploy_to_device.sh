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

echo -e "${GREEN}[1/3] Cihaz taranıyor...${NC}"
python3 -c "
import sys, os
sys.path.append('${REPO_DIR}')
from ga36_dashboard import R36SClient

client = R36SClient()
if not client.connect(timeout=10):
    print('[HATA] Konsola bağlanılamadı! Lütfen USB kablosunu kontrol edin.')
    sys.exit(1)

print('[BAĞLANDI] Konsol tespit edildi! Dosyalar yükleniyor...')

# 1. Push agent files
agent_local = '${REPO_DIR}/device/ga36_agent.py'
gadget_local = '${REPO_DIR}/device/usb_gadget_setup.sh'
hook_local = '${REPO_DIR}/device/90-pc_link.conf'
restore_local = '${REPO_DIR}/device/RESTORE_ALL.sh'

client.exec_cmd('mkdir -p /storage/pc_link /storage/.config/profile.d /storage/.config/system.d /storage/roms/backup')

client.push_file(agent_local, '/storage/pc_link/r36s_agent.py')
client.push_file(gadget_local, '/storage/pc_link/usb_gadget_setup.sh')
client.push_file(hook_local, '/storage/.config/profile.d/90-pc_link.conf')
client.push_file(restore_local, '/storage/roms/backup/RESTORE_ALL.sh')

client.exec_cmd('chmod +x /storage/pc_link/*.py /storage/pc_link/*.sh /storage/roms/backup/*.sh')

# 2. Mask emustation
print('[2/3] EmulationStation maskeleniyor (titremesiz doğrudan açılış)...')
client.exec_cmd('systemctl mask emustation.service 2>/dev/null; systemctl stop emustation.service 2>/dev/null')

# 3. Audio mute
print('[3/3] Menü müzikleri ve ses çıkışı kapatılıyor...')
client.exec_cmd('sed -i \"s/audio.bgmusic=1/audio.bgmusic=0/g\" /storage/.config/emuelec/configs/emuelec.conf 2>/dev/null; amixer cset name=\"Audio speaker out\" 0 2>/dev/null; amixer cset name=\"Master Playback Volume\" 0 2>/dev/null')

client.exec_cmd('sync')
client.close()
print('\n[BAŞARILI] GA36 konsolu doğrudan Dashboard moduna geçirildi!')
"

echo -e "${GREEN}${BOLD}Cihaz hazır! Artık './install.sh start' ile kullanabilirsiniz.${NC}"
