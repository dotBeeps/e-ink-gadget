#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# gadget-usb.sh — Minimal USB gadget configfs setup for boot
#
# This is the boot-time subset of gadget-setup.sh — does ONLY the USB
# gadget configuration, no package installs.  Called by the systemd service
# eink-gadget-setup.service.
# ---------------------------------------------------------------------------
set -euo pipefail

info()  { printf '%s\n' "$*"; }

# ── Remove old g_serial module if still present at boot ───────────────────
if lsmod 2>/dev/null | grep -q '^g_serial'; then
    info "Detaching g_serial module"
    for gd in /sys/kernel/config/usb_gadget/*/; do
        [[ -d "$gd" ]] || continue
        if [[ -f "$gd/UDC" ]] && [[ -n "$(cat "$gd/UDC" 2>/dev/null)" ]]; then
            echo '' > "$gd/UDC" 2>/dev/null || true
        fi
    done
    rmmod g_serial 2>/dev/null || true
    sleep 1
fi

# Ensure dwc2 + libcomposite are loaded
modprobe dwc2 2>/dev/null || true
modprobe libcomposite 2>/dev/null || true
sleep 0.5

# ── Configfs USB gadget setup ─────────────────────────────────────────────
GADGET_PATH=/sys/kernel/config/usb_gadget/eink

# Tear down existing gadget (idempotent)
if [[ -d "${GADGET_PATH}" ]]; then
    info "Tearing down existing eink gadget"
    if [[ -f "${GADGET_PATH}/UDC" ]] && [[ -n "$(cat "${GADGET_PATH}/UDC" 2>/dev/null)" ]]; then
        echo '' > "${GADGET_PATH}/UDC" 2>/dev/null || true
    fi
    # Remove symlinks in configs
    for f in "${GADGET_PATH}"/configs/c.1/*; do
        [[ -L "$f" ]] && rm -f "$f" 2>/dev/null || true
    done
    rmdir "${GADGET_PATH}/configs/c.1/strings/0x409" 2>/dev/null || true
    rmdir "${GADGET_PATH}/configs/c.1" 2>/dev/null || true
    for func in acm.usb1 ecm.usb0; do
        rmdir "${GADGET_PATH}/functions/${func}" 2>/dev/null || true
    done
    rmdir "${GADGET_PATH}/strings/0x409" 2>/dev/null || true
    find "${GADGET_PATH}" -depth -delete 2>/dev/null || true
fi

# configfs might not be mounted yet at early boot
if [[ ! -d /sys/kernel/config/usb_gadget ]]; then
    info "configfs not available yet, skipping"
    exit 0
fi

info "Creating configfs USB gadget"

mkdir -p "${GADGET_PATH}"
echo '0x1d6b' > "${GADGET_PATH}/idVendor"
echo '0x0104' > "${GADGET_PATH}/idProduct"

mkdir -p "${GADGET_PATH}/strings/0x409"
echo 'eink-gadget-serial'  > "${GADGET_PATH}/strings/0x409/serialnumber"
echo 'Obryn'               > "${GADGET_PATH}/strings/0x409/manufacturer"
echo 'E-Ink Gadget Display' > "${GADGET_PATH}/strings/0x409/product"

# ACM serial
mkdir -p "${GADGET_PATH}/functions/acm.usb1"

# ECM ethernet
mkdir -p "${GADGET_PATH}/functions/ecm.usb0"
echo '00:dd:dc:eb:6d:a1' > "${GADGET_PATH}/functions/ecm.usb0/host_addr"
echo '00:dd:dc:eb:6d:a2' > "${GADGET_PATH}/functions/ecm.usb0/dev_addr"

# Config
mkdir -p "${GADGET_PATH}/configs/c.1/strings/0x409"
echo 'E-Ink Gadget Composite (ACM+ECM)' > "${GADGET_PATH}/configs/c.1/strings/0x409/configuration"

ln -sf "${GADGET_PATH}/functions/acm.usb1" "${GADGET_PATH}/configs/c.1/"
ln -sf "${GADGET_PATH}/functions/ecm.usb0" "${GADGET_PATH}/configs/c.1/"

# Bind to UDC
UDC_DEVICE=$(find /sys/class/udc/ -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | head -1)
if [[ -n "${UDC_DEVICE}" ]]; then
    echo "${UDC_DEVICE}" > "${GADGET_PATH}/UDC" 2>/dev/null || \
        { info "Could not bind UDC, will retry on next service start"; exit 0; }
    info "Gadget bound to UDC: ${UDC_DEVICE}"
else
    info "No UDC found yet"
    exit 0
fi

# Configure usb0
sleep 1
if ip link show usb0 &>/dev/null; then
    ip addr add 192.168.7.2/24 dev usb0 2>/dev/null || true
    ip link set usb0 up 2>/dev/null || true
    info "usb0 configured at 192.168.7.2"
else
    info "usb0 not available yet"
fi
