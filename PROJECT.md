# e-ink-gadget

> E-Ink USB Gadget Display — turn a Raspberry Pi 4 + Waveshare 4-inch Spectra 6
> e-Paper into a single-cable USB appliance display.

**Status:** Phase 1-4 complete (code + tests), awaiting hardware verification (Phase 5).

## Architecture

```
Host Computer                    RPi 4 (USB gadget)              E-Ink Display
┌──────────────┐    USB-C     ┌─────────────────────┐    SPI    ┌──────────────┐
│ eink-send    │────────────►│ display_daemon.py   │──────────►│ Spectra 6 E6 │
│ (CLI tool)   │  /dev/ttyACM0  │                     │  GPIO 8,10, │  600×400     │
│              │◄────────────│ /dev/ttyGS0          │◄──11,17,24,│  6-color     │
│              │  ACK/status │ 921600 baud          │──25         │  19s refresh │
└──────────────┘              └─────────────────────┘              └──────────────┘
```

## Files

```
pi/
├── protocol.py          # Binary serial protocol parser (Frame, FrameParser)
├── renderer.py          # Floyd-Steinberg dithering to 6-color palette
├── eink_driver.py       # Thin wrapper around vendored epd4in0e.EPD
├── display_daemon.py    # Main daemon: serial → frame → render → display
├── vendor/waveshare_epd/ # Vendored Waveshare Spectra 6 driver
│   ├── epd4in0e.py      # EPD class: init, getbuffer, display, Clear, sleep
│   └── epdconfig.py     # SPI/GPIO hardware abstraction (spidev + gpiozero)
└── setup/
    ├── gadget-setup.sh  # One-shot RPi 4 USB gadget config (idempotent)
    └── eink-gadget.service # systemd unit for auto-start

host/
├── eink_send.py         # CLI tool: send images to display over USB serial
└── eink-send            # Shell wrapper

tests/
├── test_protocol.py     # 28 tests
├── test_renderer.py     # 25 tests
├── test_eink_driver.py  # 14 tests
└── test_display_daemon.py # 13 tests
```

## Invariants

- Spectra 6 supports only FULL refreshes (~19s) — no partial updates
- Display holds image indefinitely with zero power after rendering
- 6-color palette: BLACK, WHITE, YELLOW, RED, BLUE, GREEN
- Driver internal resolution: 400×600 (portrait); renderer accepts 600×400 landscape
- Protocol: binary length-prefixed frames with magic bytes (0xED 0xED)
- Baud rate: 921600 (settled 2025-06-02)
- Image format: raw RGB24 sent over serial; PNG decompression happens on the Pi
- Power: user handles (may need powered USB hub for RPi 4)

## Commands

```bash
# Run all tests (80 tests)
make test

# Deploy to Pi
make deploy-rpi PI_HOST=raspberrypi.local

# Install systemd service on Pi
make install-service PI_HOST=raspberrypi.local

# Clean artifacts
make clean
```

## Decisions

| Decision | Date | Status |
|---|---|---|
| Baud rate: 921600 (not 115200) | 2025-06-02 | Settled |
| PNG decompression on Pi (not host) | 2025-06-02 | Settled |
| g_serial over g_ether | 2025-06-02 | Settled |
| Power sourcing: user-managed | 2025-06-02 | Settled |

## Next Step

Hardware verification (Phase 5): flash RPi 4, attach display, run gadget-setup.sh,
deploy, and test with eink-send --ping / --clear / image.png.