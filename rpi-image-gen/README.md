# e-ink-gadget — rpi-image-gen Integration

Build a ready-to-boot Raspberry Pi 4 image with everything preconfigured:
SSH (key-only), USB serial gadget, and the e-ink display daemon auto-starting.

## Prerequisites

- Linux build machine (Debian/Ubuntu recommended)
- `mmdebstrap`, `bdebstrap`, `zstd`, `python3-yaml`
- Your SSH public key

## Quick Build

```bash
# 1. Clone rpi-image-gen
git clone https://github.com/raspberrypi/rpi-image-gen
cd rpi-image-gen

# 2. Copy our layers and config into it
cp -r /path/to/e-ink-gadget/rpi-image-gen/layer/* layer/
cp /path/to/e-ink-gadget/rpi-image-gen/config/eink-gadget.yaml config/

# 3. Build the image (replace SSH key and username)
./rpi-image-gen build \
  -c eink-gadget.yaml \
  -D IGconf_device_user1=dot \
  -D IGconf_ssh_pubkey_only=y \
  -D "IGconf_ssh_pubkey_user1=$(cat ~/.ssh/id_ed25519.pub)"

# 4. Flash to SD card
zstdcat deploy/eink-gadget-*.img.zst | sudo dd of=/dev/mmcblk0 bs=4M status=progress
```

## What the Image Includes

| Feature | How |
|---|---|
| **OS** | Debian Trixie (arm64) |
| **SSH** | Enabled, public-key only, your key in authorized_keys |
| **User** | `dot` (or whatever you set `IGconf_device_user1` to) in sudo, spi, gpio groups |
| **USB gadget** | `dwc2` overlay + `g_serial` module, appears as `/dev/ttyACM0` on host |
| **SPI** | Enabled for e-Paper display |
| **Display daemon** | Installed at `/opt/e-ink-gadget`, auto-starts via systemd at boot |
| **Baud rate** | 921600 |

## After First Boot

1. Connect USB-C cable from Pi to your computer
2. `/dev/ttyACM0` appears on the host
3. Test: `eink-send --ping` (using the host CLI from this repo)

## Layer Structure

```
rpi-image-gen/
├── config/
│   └── eink-gadget.yaml          # device=pi4, image-rpios, layer=eink-gadget-top
└── layer/
    ├── eink-gadget-top.yaml      # Requires: trixie-minbase + usb + daemon
    ├── eink-gadget-usb.yaml      # dwc2 overlay, g_serial, SPI, udev rule
    ├── eink-gadget-daemon.yaml   # Python deps, daemon source, systemd service
    └── eink-gadget-daemon/       # Supporting files
        ├── eink-gadget.service
        └── daemon-src/           # pi/ source files
```

## Customizing

- **Different SSH key:** change `IGconf_ssh_pubkey_user1`
- **Password login instead of key-only:** omit `IGconf_ssh_pubkey_only=y` (defaults to password auth enabled)
- **Set password:** add `-D IGconf_device_user1pass='yourpass'`
- **Different username:** change `IGconf_device_user1`
- **WiFi:** `trixie-minbase` already includes `iwd` — add a WiFi config layer if needed