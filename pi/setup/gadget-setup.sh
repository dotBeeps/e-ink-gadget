#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# gadget-setup.sh — RPi 4 USB Gadget configuration (configfs)
#
# Configures a Raspberry Pi 4 for USB gadget mode using configfs.
# Creates a composite gadget with ACM (serial) + ECM (ethernet) functions.
# Idempotent — safe to run multiple times.
# ---------------------------------------------------------------------------
set -euo pipefail

# ── helpers ────────────────────────────────────────────────────────────────
info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()    { printf '\033[1;32m[ OK ]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
err()   { printf '\033[1;31m[ERR]\033[0m  %s\n' "$*" >&2; }

# ── config file detection ──────────────────────────────────────────────────
if [[ -f /boot/firmware/config.txt ]]; then
    CONFIG_FILE=/boot/firmware/config.txt
elif [[ -f /boot/config.txt ]]; then
    CONFIG_FILE=/boot/config.txt
else
    err "Could not find /boot/firmware/config.txt or /boot/config.txt"
    exit 1
fi
info "Using config file: ${CONFIG_FILE}"

# ── 1. Enable dwc2 overlay ────────────────────────────────────────────────
if grep -qx 'dtoverlay=dwc2' "${CONFIG_FILE}" 2>/dev/null; then
    ok "dtoverlay=dwc2 already present in ${CONFIG_FILE}"
else
    echo 'dtoverlay=dwc2' >> "${CONFIG_FILE}"
    ok "Added dtoverlay=dwc2 to ${CONFIG_FILE}"
fi

# ── 2. Enable SPI (dtparam=spi=on) ────────────────────────────────────────
if grep -q '^dtparam=spi=' "${CONFIG_FILE}" 2>/dev/null; then
    if grep -q '^dtparam=spi=on' "${CONFIG_FILE}" 2>/dev/null; then
        ok "dtparam=spi=on already set in ${CONFIG_FILE}"
    else
        warn "dtparam=spi=* is set but not 'on' — edit ${CONFIG_FILE} manually if SPI is needed"
    fi
else
    echo 'dtparam=spi=on' >> "${CONFIG_FILE}"
    ok "Added dtparam=spi=on to ${CONFIG_FILE}"
fi

# ── 3. Kernel modules: dwc2 + libcomposite (NOT g_serial) ─────────────────
MODULES_FILE=/etc/modules

for mod in dwc2 libcomposite; do
    if grep -qx "${mod}" "${MODULES_FILE}" 2>/dev/null; then
        ok "Module '${mod}' already in ${MODULES_FILE}"
    else
        echo "${mod}" >> "${MODULES_FILE}"
        ok "Added module '${mod}' to ${MODULES_FILE}"
    fi
done

# ── 4. Load modules now (if not already loaded) ───────────────────────────
modprobe dwc2 2>/dev/null || warn "dwc2 module not available (expected before reboot)"
modprobe libcomposite 2>/dev/null || warn "libcomposite module not available (expected before reboot)"

# Remove old g_serial module — it grabs the UDC at boot and blocks configfs
if lsmod | grep -q '^g_serial'; then
    info "Detaching g_serial module (old serial gadget)"
    # Unbind any existing gadget from UDC first
    for gadget_dir in /sys/kernel/config/usb_gadget/*/; do
        [[ -d "$gadget_dir" ]] || continue
        udc_file="${gadget_dir}UDC"
        if [[ -f "$udc_file" ]] && [[ -n "$(cat "$udc_file" 2>/dev/null)" ]]; then
            echo '' > "$udc_file" 2>/dev/null || true
        fi
    done
    rmmod g_serial 2>/dev/null || warn "Could not rmmod g_serial — may need reboot"
    ok "g_serial module removed"
    sleep 1
fi

# Remove g_serial from /etc/modules so it doesn't return on reboot
MODULES_FILE=/etc/modules
if grep -qx 'g_serial' "${MODULES_FILE}" 2>/dev/null; then
    info "Removing g_serial from ${MODULES_FILE}"
    sed -i '/^g_serial$/d' "${MODULES_FILE}"
    ok "Removed g_serial from ${MODULES_FILE}"
fi

# ── 5. Configfs USB gadget setup ──────────────────────────────────────────
GADGET_PATH=/sys/kernel/config/usb_gadget/eink

# Tear down existing gadget (idempotent)
if [[ -d "${GADGET_PATH}" ]]; then
    info "Tearing down existing eink gadget at ${GADGET_PATH}"

    # Stop eink-gadget service so nothing holds /dev/ttyGS[0-9]*
    if systemctl is-active --quiet eink-gadget 2>/dev/null; then
        info "Stopping eink-gadget service to release serial port"
        systemctl stop eink-gadget 2>/dev/null || true
        sleep 1
    fi

    # Kill any remaining process holding ttyGS[0-9]*
    for tty in /dev/ttyGS[0-9]*; do
        [[ -e "$tty" ]] || continue
        for pid in $(fuser "$tty" 2>/dev/null); do
            info "Killing PID $pid holding $tty"
            kill "$pid" 2>/dev/null || true
        done
    done
    sleep 0.5

    # Unbind from UDC if bound
    if [[ -f "${GADGET_PATH}/UDC" ]] && [[ -n "$(cat "${GADGET_PATH}/UDC" 2>/dev/null)" ]]; then
        echo '' > "${GADGET_PATH}/UDC" 2>/dev/null || \
            warn "Could not unbind UDC — will attempt rm -rf anyway"
        ok "Unbind command sent"
    fi

    # Aggressive teardown: rm -rf the whole configfs tree for this gadget
    # configfs allows rm -rf on non-UDC-bound gadgets; after unbind above
    # this should succeed. We remove configs first to force the unbind.
    if [[ -L "${GADGET_PATH}/configs/c.1" ]]; then
        rm -f "${GADGET_PATH}/configs/c.1/"* 2>/dev/null || true
        rmdir "${GADGET_PATH}/configs/c.1" 2>/dev/null || true
    fi
    for func in acm.usb1 ecm.usb0; do
        if [[ -d "${GADGET_PATH}/functions/${func}" ]]; then
            rmdir "${GADGET_PATH}/functions/${func}" 2>/dev/null || true
        fi
    done
    # Final: remove any remaining children and the gadget itself
    find "${GADGET_PATH}" -depth -delete 2>/dev/null || true

    ok "Existing gadget torn down"
fi

# Only proceed with configfs setup if configfs is mounted
if [[ ! -d /sys/kernel/config/usb_gadget ]]; then
    warn "configfs not available — gadget will be created after reboot via /etc/modules"
    info "Skipping configfs gadget creation (will be done on next boot)"
else
    info "Creating configfs USB gadget at ${GADGET_PATH}"

    # Create gadget directory
    mkdir -p "${GADGET_PATH}"

    # Vendor & product IDs (Linux Foundation, Multifunction Composite Gadget)
    echo '0x1d6b' > "${GADGET_PATH}/idVendor"
    echo '0x0104' > "${GADGET_PATH}/idProduct"

    # String descriptors
    mkdir -p "${GADGET_PATH}/strings/0x409"
    echo 'eink-gadget-serial' > "${GADGET_PATH}/strings/0x409/serialnumber"
    echo 'Obryn'              > "${GADGET_PATH}/strings/0x409/manufacturer"
    echo 'E-Ink Gadget Display' > "${GADGET_PATH}/strings/0x409/product"

    # ── ACM (serial) function ─────────────────────────────────────────────
    mkdir -p "${GADGET_PATH}/functions/acm.usb1"
    ok "Created acm.usb1 function"

    # ── ECM (ethernet) function ───────────────────────────────────────────
    mkdir -p "${GADGET_PATH}/functions/ecm.usb0"
    echo '00:dd:dc:eb:6d:a1' > "${GADGET_PATH}/functions/ecm.usb0/host_addr"
    echo '00:dd:dc:eb:6d:a2' > "${GADGET_PATH}/functions/ecm.usb0/dev_addr"
    ok "Created ecm.usb0 function"

    # ── Configuration ─────────────────────────────────────────────────────
    mkdir -p "${GADGET_PATH}/configs/c.1/strings/0x409"
    echo 'E-Ink Gadget Composite (ACM+ECM)' > "${GADGET_PATH}/configs/c.1/strings/0x409/configuration"

    # Bind functions to configuration
    ln -sf "${GADGET_PATH}/functions/acm.usb1" "${GADGET_PATH}/configs/c.1/"
    ln -sf "${GADGET_PATH}/functions/ecm.usb0" "${GADGET_PATH}/configs/c.1/"
    ok "Bound acm.usb1 and ecm.usb0 to config c.1"

    # ── Enable gadget (bind to UDC) ───────────────────────────────────────
    UDC_DEVICE=$(find /sys/class/udc/ -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | head -1)
    if [[ -n "${UDC_DEVICE}" ]]; then
        # Ensure the UDC file exists (it's created when the gadget is set up properly)
        if [[ -f "${GADGET_PATH}/UDC" ]]; then
            if ! echo "${UDC_DEVICE}" > "${GADGET_PATH}/UDC" 2>/dev/null; then
                # UDC is busy — reload dwc2 to force-release it
                warn "UDC busy — reloading dwc2 driver"
                for gadget_dir in /sys/kernel/config/usb_gadget/*/; do
                    [[ -d "$gadget_dir" ]] || continue
                    udc_file="${gadget_dir}UDC"
                    if [[ -f "$udc_file" ]] && [[ -n "$(cat "$udc_file" 2>/dev/null)" ]]; then
                        echo '' > "$udc_file" 2>/dev/null || true
                    fi
                done
                rmmod dwc2 2>/dev/null || true
                sleep 1
                modprobe dwc2 2>/dev/null
                sleep 1
                # Try binding again
                if echo "${UDC_DEVICE}" > "${GADGET_PATH}/UDC" 2>/dev/null; then
                    ok "Bound gadget to UDC device: ${UDC_DEVICE} (after dwc2 reload)"
                else
                    warn "Still could not bind UDC — a reboot is required"
                fi
            else
                ok "Bound gadget to UDC device: ${UDC_DEVICE}"
            fi
        else
            warn "UDC file not found in gadget config — binding skipped"
        fi
    else
        warn "No UDC device found — gadget will be enabled after reboot"
    fi

    ok "Configfs USB gadget created and enabled"
fi

# ── 6. udev rule for serial gadget symlink ────────────────────────────────
UDEV_RULES_FILE=/etc/udev/rules.d/99-eink-gadget.rules
# Match any ACM gadget serial device (ttyGS0, ttyGS1, etc.)
UDEV_RULE='SUBSYSTEM=="tty", KERNEL=="ttyGS[0-9]*", SYMLINK+="eink-gadget"'

if [[ -f "${UDEV_RULES_FILE}" ]] && grep -q 'ttyGS\[0-9\]' "${UDEV_RULES_FILE}" 2>/dev/null; then
    ok "Udev rule already present: ${UDEV_RULES_FILE}"
else
    echo "${UDEV_RULE}" > "${UDEV_RULES_FILE}"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
    ok "Created udev rule ${UDEV_RULES_FILE} and reloaded rules"
fi

# ── 7. Configure usb0 interface (ECM) ─────────────────────────────────────
# This runs on every invocation to ensure the IP is set
if ip link show usb0 &>/dev/null; then
    ip addr show usb0 | grep -q '192.168.7.2/24' 2>/dev/null || {
        ip addr add 192.168.7.2/24 dev usb0 2>/dev/null || true
        ok "Assigned 192.168.7.2/24 to usb0"
    }
    ip link set usb0 up 2>/dev/null || true
    ok "usb0 interface is up"
else
    warn "usb0 interface not available yet (will be configured after reboot)"
fi

# ── 8. Install apt packages ───────────────────────────────────────────────
APT_PACKAGES=(python3-pip python3-pil python3-serial python3-flask)

info "Installing apt packages: ${APT_PACKAGES[*]}"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${APT_PACKAGES[@]}"
ok "Apt packages installed"

# ── 9. Install pip packages (with --break-system-packages for Bookworm) ───
PIP_PACKAGES=(spidev gpiozero)

info "Installing pip packages: ${PIP_PACKAGES[*]}"
if python3 -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    # Bookworm / newer Python — need --break-system-packages
    pip3 install --break-system-packages "${PIP_PACKAGES[@]}"
else
    pip3 install "${PIP_PACKAGES[@]}"
fi
ok "Pip packages installed"

# ── 10. Create gallery directory ─────────────────────────────────────────
GALLERY_DIR=/var/lib/e-ink-gadget/gallery
if [[ ! -d "${GALLERY_DIR}" ]]; then
    mkdir -p "${GALLERY_DIR}"
    chmod 0755 "${GALLERY_DIR}" 2>/dev/null || true
    ok "Created ${GALLERY_DIR}"
else
    ok "Gallery directory ${GALLERY_DIR} already exists"
    chmod 0755 "${GALLERY_DIR}" 2>/dev/null || true
fi

# ── 11. Done ──────────────────────────────────────────────────────────────
cat <<'DONE'

  ┌──────────────────────────────────────────────────────────┐
  │                    Setup Complete                         │
  ├──────────────────────────────────────────────────────────┤
  │                                                          │
  │  Next steps (requires reboot):                           │
  │                                                          │
  │    1. sudo reboot                                        │
  │                                                          │
  │    After reboot, verify:                                 │
  │      ls -l /dev/eink-gadget                              │
  │      ip addr show usb0                                   │
  │                                                          │
  │    2. Deploy the software:                               │
  │      make deploy-rpi                                     │
  │                                                          │
  │    3. Install & start the daemon:                        │
  │      make install-service                                │
  │                                                          │
  │    4. Check status:                                      │
  │      sudo systemctl status eink-gadget                   │
  │      journalctl -u eink-gadget -f                        │
  │                                                          │
  └──────────────────────────────────────────────────────────┘

DONE
