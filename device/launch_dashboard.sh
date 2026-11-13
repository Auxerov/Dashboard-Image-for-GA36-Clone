#!/bin/bash
# ==============================================================================
# GA36 Clone Dedicated Dashboard Launcher
# 100% EmulationStation Replacement - Zero retro UI, direct dashboard display
# ==============================================================================

# 1. Immediately paint custom cyber splash to framebuffer
if [ -f /storage/pc_link/splash.raw ]; then
    cat /storage/pc_link/splash.raw > /dev/fb0 2>/dev/null || true
fi

# 2. Ensure USB gadget is enabled (force device role & CDC ACM serial)
if [ -f /storage/pc_link/usb_gadget_setup.sh ]; then
    /bin/bash /storage/pc_link/usb_gadget_setup.sh >> /storage/usb_boot.log 2>&1
fi

# 3. Ensure companion agent daemon is running
if ! pgrep -f "r36s_agent.py" >/dev/null 2>&1; then
    SERIAL_DEV="/dev/ttyGS0"
    for dev in /dev/ttyGS0 /dev/ttygserial /dev/ttyGS* /dev/ttyACM*; do
        [ -e "$dev" ] && SERIAL_DEV="$dev" && break
    done
    python3 /storage/pc_link/r36s_agent.py --daemon --dev "$SERIAL_DEV" --port 8888 >> /storage/usb_boot.log 2>&1 &
fi

# 4. Stay active to satisfy systemd
while true; do
    sleep 3600
done
