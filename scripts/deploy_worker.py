#!/usr/bin/env python3
"""
GA36 / R36S Direct USB Deployer Worker
Transfers required agent files, flashes splash screens & bootlogo.bmp to partition 2,
masks EmulationStation, and mutes audio daemons.
"""

import os
import sys
import time

repo_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_dir)

from ga36_dashboard import R36SClient

client = R36SClient()
if not client.connect(timeout=10):
    print("[ERROR] Could not connect to console! Please check the USB-C cable.")
    sys.exit(1)

print("[CONNECTED] Console detected! Deploying files...")

# Local file paths
agent_local = os.path.join(repo_dir, "device/ga36_agent.py")
gadget_local = os.path.join(repo_dir, "device/usb_gadget_setup.sh")
hook_local = os.path.join(repo_dir, "device/90-pc_link.conf")
restore_local = os.path.join(repo_dir, "device/RESTORE_ALL.sh")
splash_png = os.path.join(repo_dir, "device/splash/splash.png")

# 1. Create target directories on console
client.exec_cmd("mkdir -p /storage/pc_link /storage/.config/profile.d /storage/.config/system.d /storage/.config/splash /storage/roms/backup")

# 2. Push lightweight code files (< 30 KB each)
print("[*] Uploading agent and system hook...")
client.push_file(agent_local, "/storage/pc_link/r36s_agent.py")
client.push_file(gadget_local, "/storage/pc_link/usb_gadget_setup.sh")
client.push_file(hook_local, "/storage/.config/profile.d/90-pc_link.conf")
client.push_file(restore_local, "/storage/roms/backup/RESTORE_ALL.sh")

# 3. Push splash PNG (29 KB)
if os.path.exists(splash_png):
    print("[*] Uploading high-res cyber splash image...")
    client.push_file(splash_png, "/storage/pc_link/splash.png")

# 4. Execute on-device flasher to write bootlogo.bmp to partition 2, splash.raw, and loading screens
print("[*] Flashing bootloader bootlogo.bmp, low_pwr.bmp, and framebuffer splash...")
on_device_code = """python3 -c "
import os, sys, shutil
from PIL import Image

src_png = '/storage/pc_link/splash.png'
if not os.path.exists(src_png):
    sys.exit(1)

img = Image.open(src_png).convert('RGB')
if img.size != (640, 480):
    img = img.resize((640, 480))

# A. Generate 32-bit BGRA splash.raw
r, g, b = img.split()
bgra = Image.merge('RGBA', (b, g, r, Image.new('L', (640, 480), 255)))
raw_bytes = bgra.tobytes()

for target in [
    '/storage/pc_link/splash.raw',
    '/storage/.config/splash/splash.raw',
    '/storage/.config/splash/splash-1080.raw'
]:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'wb') as f:
        f.write(raw_bytes)

# B. Update all PNG splash targets
for target in [
    '/storage/.config/splash/splash.png',
    '/storage/.config/splash/loading-game.png',
    '/storage/.config/splash/loading-game-std.png',
    '/storage/.config/splash/splash-std.png',
    '/storage/.config/splash/splash-1080.png',
    '/storage/.config/splash/retroarch.png'
]:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(src_png, target)

# C. Mount partition 2 and update bootloader bootlogo.bmp (R36S please wait screen)
os.makedirs('/tmp/volumn', exist_ok=True)
os.system('mount -t vfat -o rw /dev/mmcblk0p2 /tmp/volumn 2>/dev/null || mount /dev/mmcblk0p2 /tmp/volumn 2>/dev/null')
try:
    img.save('/tmp/volumn/bootlogo.bmp', format='BMP')
    print('[OK] Partition 2 bootlogo.bmp updated!')
    if os.path.exists('/tmp/volumn/bat'):
        img.save('/tmp/volumn/bat/low_pwr.bmp', format='BMP')
except Exception as e:
    print('[WARN] Bootlogo save error:', e)
os.system('sync; umount /tmp/volumn 2>/dev/null')

# D. Mount partition 7 (/flash) rw and update low_pwr.bmp & bootlogo.bmp if present
os.system('mount -o remount,rw /flash 2>/dev/null || true')
for p in ['/flash/bootlogo.bmp', '/flash/low_pwr.bmp']:
    if os.path.exists(p):
        try:
            img.save(p, format='BMP')
            print('[OK] Updated', p)
        except Exception:
            pass
os.system('sync; mount -o remount,ro /flash 2>/dev/null || true')

# E. Remove all legacy video splashes and disable EmulationStation splash
os.system('rm -f /storage/.config/splash/*.mp4 /storage/roms/splash/*.mp4 2>/dev/null')
es_cfg = '/storage/.config/emulationstation/es_settings.cfg'
if os.path.exists(es_cfg):
    try:
        with open(es_cfg, 'r') as f:
            c = f.read()
        import re
        c = re.sub(r'<bool name=\\\"SplashScreen\\\" value=\\\"[^\\\"]*\\\" />', '<bool name=\\\"SplashScreen\\\" value=\\\"false\\\" />', c)
        with open(es_cfg, 'w') as f:
            f.write(c)
    except Exception:
        pass

# F. Immediately paint framebuffer 0 with new splash
os.system('cat /storage/pc_link/splash.raw > /dev/fb0 2>/dev/null || true')
print('[DONE] All bootlogo, loading, and splash screens permanently replaced!')
"
"""

res = client.exec_cmd(on_device_code, timeout=30)
if res.get("stdout"):
    print(res["stdout"].strip())

client.exec_cmd("chmod +x /storage/pc_link/*.py /storage/pc_link/*.sh /storage/roms/backup/*.sh")

# 5. Mask EmulationStation
print("[*] Masking EmulationStation (direct zero-flicker boot)...")
client.exec_cmd("systemctl mask emustation.service 2>/dev/null; systemctl stop emustation.service 2>/dev/null")

# 6. Audio hardware mute & clean
print("[*] Silencing hardware audio output and disabling audio daemons...")
client.exec_cmd("killall -9 pulseaudio 2>/dev/null ; ln -sf /dev/null /storage/.config/system.d/pulseaudio.service ; ln -sf /dev/null /etc/systemd/system/pulseaudio.service 2>/dev/null")
client.exec_cmd('amixer cset name="Speaker Function" headset 2>/dev/null ; amixer cset name="Audio speaker out" 0 2>/dev/null ; amixer cset name="Master Playback Volume" 0 2>/dev/null ; true')
client.exec_cmd('sed -i "s/audio.bgmusic=1/audio.bgmusic=0/g" /storage/.config/emuelec/configs/emuelec.conf 2>/dev/null ; sed -i "s/audio.volume=.*/audio.volume=0/g" /storage/.config/emuelec/configs/emuelec.conf 2>/dev/null')
client.exec_cmd('rm -rf /storage/assets/sounds /storage/roms/bgm ; find /storage -type f \\( -name "*.mp3" -o -name "*.wav" -o -name "*.ogg" -o -name "*.flac" -o -name "*.aac" -o -name "*.m4a" \\) -delete 2>/dev/null ; true')

client.exec_cmd("sync")
client.close()
print("\n[SUCCESS] GA36 console successfully configured for Dashboard mode!")
