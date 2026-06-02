#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# gadget-setup.sh — RPi 4 USB Gadget configuration
#
# Configures a Raspberry Pi 4 for USB gadget / Ethernet-over-USB / serial
# gadget mode.  Idempotent — safe to run multiple times.
# ---------------------------------------------------------------------------
set -euo pipefail

# ── helpers ────────────────────────────────────────────────────────────────
info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()    { printf '\033[1;32m[ OK ]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
err()   { printf '\033[1;31m[ERR]\033[0m  %s\n' "$*" >&2; }

# ── determine config file ──────────────────────────────────────────────────
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
    # Ensure it is set to 'on'
    if grep -q '^dtparam=spi=on' "${CONFIG_FILE}" 2>/dev/null; then
        ok "dtparam=spi=on already set in ${CONFIG_FILE}"
    else
        warn "dtparam=spi=* is set but not 'on' — edit ${CONFIG_FILE} manually if SPI is needed"
    fi
else
    echo 'dtparam=spi=on' >> "${CONFIG_FILE}"
    ok "Added dtparam=spi=on to ${CONFIG_FILE}"
fi

# ── 3. Kernel modules: dwc2 + g_serial ────────────────────────────────────
MODULES_FILE=/etc/modules

for mod in dwc2 g_serial; do
    if grep -qx "${mod}" "${MODULES_FILE}" 2>/dev/null; then
        ok "Module '${mod}' already in ${MODULES_FILE}"
    else
        echo "${mod}" >> "${MODULES_FILE}"
        ok "Added module '${mod}' to ${MODULES_FILE}"
    fi
done

# ── 4. Udev rule for serial gadget symlink ────────────────────────────────
UDEV_RULES_FILE=/etc/udev/rules.d/99-eink-gadget.rules
UDEV_RULE='SUBSYSTEM=="tty", KERNEL=="ttyGS0", SYMLINK+="eink-gadget"'

if [[ -f "${UDEV_RULES_FILE}" ]] && grep -q 'ttyGS0' "${UDEV_RULES_FILE}" 2>/dev/null; then
    ok "Udev rule already present: ${UDEV_RULES_FILE}"
else
    echo "${UDEV_RULE}" > "${UDEV_RULES_FILE}"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
    ok "Created udev rule ${UDEV_RULES_FILE} and reloaded rules"
fi

# ── 5. Install apt packages ───────────────────────────────────────────────
APT_PACKAGES=(python3-pip python3-pil python3-serial)

info "Installing apt packages: ${APT_PACKAGES[*]}"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${APT_PACKAGES[@]}"
ok "Apt packages installed"

# ── 6. Install pip packages (with --break-system-packages for Bookworm) ───
PIP_PACKAGES=(spidev gpiozero)

info "Installing pip packages: ${PIP_PACKAGES[*]}"
if python3 -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    # Bookworm / newer Python — need --break-system-packages
    pip3 install --break-system-packages "${PIP_PACKAGES[@]}"
else
    pip3 install "${PIP_PACKAGES[@]}"
fi
ok "Pip packages installed"

# ── 7. Done ───────────────────────────────────────────────────────────────
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
