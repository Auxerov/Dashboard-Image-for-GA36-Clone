#!/bin/bash
# -------------------------------------------------------------
# R36S Allwinner A33 USB Gadget & Diagnostics
# -------------------------------------------------------------

LOG="/storage/roms/usb_debug.log"
exec >> "$LOG" 2>&1

echo "============================================================"
echo " R36S USB GADGET STARTUP - $(date)"
echo "============================================================"

echo "Kernel: $(uname -a)"
echo "Cmdline: $(cat /proc/cmdline)"

echo "--- Initial /sys/class/udc ---"
ls -la /sys/class/udc/ 2>/dev/null

echo "--- Searching for USB role and OTG switches ---"
find /sys/ -name "*role*" 2>/dev/null
find /sys/ -name "*udc*" 2>/dev/null
find /sys/ -name "*musb*" 2>/dev/null
find /sys/ -name "*sunxi*usb*" 2>/dev/null

# Force OTG mode to device/peripheral across all possible sysfs interfaces
for r in $(find /sys/ -name "*otg_role*" 2>/dev/null); do
    echo "Writing 2 to $r"
    echo 2 > "$r" 2>/dev/null
done

for r in /sys/class/usb_role/*/role; do
    if [ -f "$r" ]; then
        echo "Writing device to $r"
        echo device > "$r" 2>/dev/null
    fi
done

# Try loading composite and serial modules
modprobe libcomposite 2>/dev/null
modprobe u_serial 2>/dev/null
modprobe usb_f_acm 2>/dev/null
modprobe usb_f_serial 2>/dev/null
modprobe g_serial 2>/dev/null

echo "--- /sys/class/udc after role switch & modprobe ---"
ls -la /sys/class/udc/ 2>/dev/null

# Check UDC
UDC=$(ls /sys/class/udc 2>/dev/null | head -n 1)
echo "Detected UDC: '$UDC'"

CONFIGFS="/sys/kernel/config"
if [ ! -d "$CONFIGFS/usb_gadget" ]; then
    mount -t configfs none "$CONFIGFS" 2>/dev/null
fi

if [ -n "$UDC" ]; then
    GADGET="$CONFIGFS/usb_gadget/r36s"
    if [ ! -d "$GADGET" ]; then
        mkdir -p "$GADGET"
        cd "$GADGET" || exit 1

        echo "0x1d6b" > idVendor
        echo "0x0104" > idProduct
        echo "0x0100" > bcdDevice
        echo "0x0200" > bcdUSB

        mkdir -p strings/0x409
        echo "R36S-0001" > strings/0x409/serialnumber
        echo "R36S" > strings/0x409/manufacturer
        echo "R36S PC Link" > strings/0x409/product

        mkdir -p configs/c.1/strings/0x409
        echo "CDC ACM Serial" > configs/c.1/strings/0x409/configuration
        echo 500 > configs/c.1/MaxPower

        mkdir -p functions/acm.usb0
        ln -s functions/acm.usb0 configs/c.1/

        echo "$UDC" > UDC
        echo "SUCCESS: Bound gadget to $UDC"
    else
        if [ -z "$(cat $GADGET/UDC 2>/dev/null)" ]; then
            echo "$UDC" > "$GADGET/UDC"
            echo "Re-bound gadget to $UDC"
        else
            echo "Gadget already bound to $(cat $GADGET/UDC 2>/dev/null)"
        fi
    fi
else
    echo "WARNING: /sys/class/udc is empty! Trying legacy g_serial fallback..."
    modprobe g_serial 2>/dev/null
fi

echo "--- Serial Devices in /dev ---"
ls -la /dev/ttyGS* /dev/ttygserial* 2>/dev/null

echo "--- Recent kernel dmesg regarding USB ---"
dmesg | grep -i -E "usb|udc|gadget|musb|sunxi|phy|acm" | tail -n 40

echo "============================================================"
sync
