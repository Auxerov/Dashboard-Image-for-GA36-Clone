#!/bin/bash
# -------------------------------------------------------------
# GA36 / R36S Direct Custom Start Hook
# Executed automatically by EmuELEC autostart
# -------------------------------------------------------------

# 1. Paint custom boot logo instantly
if [ -f /storage/pc_link/splash.raw ]; then
    cat /storage/pc_link/splash.raw > /dev/fb0 2>/dev/null || true
fi

# 2. Run USB Gadget Setup
if [ -f /storage/pc_link/usb_gadget_setup.sh ]; then
    /bin/bash /storage/pc_link/usb_gadget_setup.sh >> /storage/usb_boot.log 2>&1
fi

# 3. Launch Agent Daemon if not running
if ! pgrep -f "r36s_agent.py" >/dev/null 2>&1; then
    SERIAL_DEV="/dev/ttyGS0"
    for dev in /dev/ttyGS0 /dev/ttygserial /dev/ttyGS* /dev/ttyACM*; do
        [ -e "$dev" ] && SERIAL_DEV="$dev" && break
    done
    python3 /storage/pc_link/r36s_agent.py --daemon --dev "$SERIAL_DEV" --port 8888 >> /storage/usb_boot.log 2>&1 &
fi
