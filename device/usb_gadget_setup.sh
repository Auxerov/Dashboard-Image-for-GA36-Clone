#!/bin/bash
# -----------------------------------------------------------------------------
# GA36 / R36S Allwinner A33 (Linux 3.4.39 sun8iw5) USB Gadget Setup
# High-reliability, 100% rock-solid boot on every power-cycle & hotplug
# -----------------------------------------------------------------------------

LOG="/storage/usb_debug.log"
exec >> "$LOG" 2>&1

echo "============================================================"
echo " GA36 USB GADGET SETUP - $(date)"
echo "============================================================"
echo "Kernel: $(uname -a 2>/dev/null)"
echo "Cmdline: $(cat /proc/cmdline 2>/dev/null)"

# 1. Force Allwinner sunxi_usb_udc OTG port to Peripheral / Device Mode
UDC_DIR="/sys/bus/platform/devices/sunxi_usb_udc"
[ ! -d "$UDC_DIR" ] && UDC_DIR="/sys/devices/platform/sunxi_usb_udc"

if [ -f "$UDC_DIR/otg_role" ]; then
    echo "Forcing $UDC_DIR/otg_role -> 2 (Device mode)"
    echo 2 > "$UDC_DIR/otg_role" 2>/dev/null
fi

for r in $(find /sys/ -name "*otg_role*" 2>/dev/null); do
    echo 2 > "$r" 2>/dev/null
done

for r in /sys/class/usb_role/*/role 2>/dev/null; do
    [ -f "$r" ] && echo device > "$r" 2>/dev/null
done

# 2. Configure Android Composite Gadget (Native kernel 3.4.39 Allwinner interface)
ANDROID_DIR="/sys/class/android_usb/android0"
if [ -d "$ANDROID_DIR" ]; then
    echo "--- Configuring Android USB Gadget (/sys/class/android_usb/android0) ---"
    # Disable gadget first to allow clean descriptor re-configuration
    echo 0 > "$ANDROID_DIR/enable" 2>/dev/null
    sleep 0.15

    echo "18d1" > "$ANDROID_DIR/idVendor" 2>/dev/null
    echo "0001" > "$ANDROID_DIR/idProduct" 2>/dev/null
    echo "0233" > "$ANDROID_DIR/bcdDevice" 2>/dev/null
    echo "R36S" > "$ANDROID_DIR/iManufacturer" 2>/dev/null
    echo "R36S Handheld" > "$ANDROID_DIR/iProduct" 2>/dev/null
    echo "93970483844df7e0908" > "$ANDROID_DIR/iSerial" 2>/dev/null

    # Critical: Set f_acm instances to 1 so endpoint & interface descriptors exist
    if [ -d "$ANDROID_DIR/f_acm" ]; then
        echo 1 > "$ANDROID_DIR/f_acm/instances" 2>/dev/null
        echo "f_acm/instances set to: $(cat $ANDROID_DIR/f_acm/instances 2>/dev/null)"
    fi

    # Activate CDC ACM Serial function
    echo "acm" > "$ANDROID_DIR/functions" 2>/dev/null
    echo 1 > "$ANDROID_DIR/enable" 2>/dev/null
    sleep 0.3

    echo "Android Gadget enable: $(cat $ANDROID_DIR/enable 2>/dev/null)"
    echo "Android Gadget state: $(cat $ANDROID_DIR/state 2>/dev/null)"
fi

# 3. Modern Linux ConfigFS fallback (if kernel supports configfs)
CONFIGFS="/sys/kernel/config"
if [ ! -d "$ANDROID_DIR" ] && [ -d "$CONFIGFS" ]; then
    mount -t configfs none "$CONFIGFS" 2>/dev/null || true
    if [ -d "$CONFIGFS/usb_gadget" ]; then
        GADGET="$CONFIGFS/usb_gadget/r36s"
        mkdir -p "$GADGET" 2>/dev/null || true
        if [ -d "$GADGET" ]; then
            echo "0x1d6b" > "$GADGET/idVendor" 2>/dev/null
            echo "0x0104" > "$GADGET/idProduct" 2>/dev/null
            mkdir -p "$GADGET/strings/0x409" "$GADGET/configs/c.1/strings/0x409" "$GADGET/functions/acm.usb0" 2>/dev/null
            echo "R36S" > "$GADGET/strings/0x409/manufacturer" 2>/dev/null
            echo "R36S Handheld" > "$GADGET/strings/0x409/product" 2>/dev/null
            ln -sf "$GADGET/functions/acm.usb0" "$GADGET/configs/c.1/" 2>/dev/null
            UDC=$(ls /sys/class/udc 2>/dev/null | head -n 1)
            [ -n "$UDC" ] && echo "$UDC" > "$GADGET/UDC" 2>/dev/null
        fi
    fi
fi

# 4. Check for serial device & apply permissions
SERIAL_DEV=""
for i in $(seq 1 10); do
    for dev in /dev/ttyGS0 /dev/ttygserial /dev/ttyGS* /dev/ttyACM*; do
        if [ -e "$dev" ]; then
            SERIAL_DEV="$dev"
            break 2
        fi
    done
    sleep 0.1
done

if [ -n "$SERIAL_DEV" ]; then
    echo "SUCCESS: Found serial device '$SERIAL_DEV'"
    chmod 666 "$SERIAL_DEV" 2>/dev/null
else
    echo "Fallback: Loading g_serial module..."
    modprobe g_serial 2>/dev/null || true
fi

# 5. Network Interface Setup (if usb0 exists)
if ifconfig usb0 >/dev/null 2>&1; then
    ifconfig usb0 192.168.10.2 netmask 255.255.255.0 up
    echo "usb0 status: $(ifconfig usb0 2>/dev/null | grep 'inet ')"
fi

echo "============================================================"
sync
