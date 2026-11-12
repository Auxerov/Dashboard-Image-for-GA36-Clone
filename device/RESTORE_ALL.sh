#!/bin/bash
# -------------------------------------------------------------
# GA36 / R36S Instant System Restore Script
# Restores stock EmulationStation and sound output
# -------------------------------------------------------------

echo "====================================================="
echo " Restoring Stock EmulationStation & Audio..."
echo "====================================================="

if [ -f /storage/roms/backup/system_backup.tar ]; then
    echo "Restoring core emulators, assets, and themes from backup..."
    tar -xf /storage/roms/backup/system_backup.tar -C /
fi

systemctl unmask emustation.service 2>/dev/null
systemctl unmask eventlircd.service 2>/dev/null
systemctl unmask cron.service 2>/dev/null
systemctl unmask udt-headphones.service 2>/dev/null

sed -i "s/audio.bgmusic=0/audio.bgmusic=1/g" /storage/.config/emuelec/configs/emuelec.conf 2>/dev/null
amixer cset name="Audio speaker out" 1 2>/dev/null
amixer cset name="Master Playback Volume" 48 2>/dev/null

sync
echo "Restore complete! Launching EmulationStation..."
systemctl start emustation.service 2>/dev/null
