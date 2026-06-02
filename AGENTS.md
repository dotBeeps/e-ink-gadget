# AGENTS.md — e-ink-gadget

## First Reads

- `PROJECT.md` — purpose, status, architecture, files, invariants, commands
- All source under `pi/` — daemon and libraries (Python with type hints where helpful)
- All tests under `tests/` — pytest with unittest.mock for hardware mocking
- `pi/display_daemon.py` — entry point for the systemd service

## Key Imports

```python
from pi.protocol import FrameParser, Frame, CMD_IMAGE, CMD_CLEAR, CMD_SLEEP, CMD_PING
from pi.renderer import prepare_image, SPECTRA6_PALETTE
from pi.eink_driver import EInkDisplay, DisplayError
from pi.vendor.waveshare_epd import epd4in0e
```

## Data Flow

```
Serial bytes → FrameParser.feed() → Frame objects
                                    ↓
Frame → process_frame() → dispatches on cmd:
  CMD_IMAGE: raw pixels → PIL.Image → prepare_image() → getbuffer() → display()
  CMD_CLEAR: display.clear()
  CMD_SLEEP: display.sleep()
  CMD_PING:  immediate ACK
                                    ↓
                              Response bytes (ACK/ERROR) → serial write
```

## Testing

- All hardware-dependent code uses `unittest.mock.patch` targeting `pi.eink_driver.epd4in0e`
- `eink_driver.py` uses lazy import to avoid `epdconfig` platform detection at import time
- Protocol tests cover partial reads, resync after garbage, oversized frames
- Renderer tests verify palette quantization and dithering on known inputs

## Vendored Driver

`pi/vendor/waveshare_epd/epd4in0e.py` is vendored from:
`https://github.com/waveshareteam/e-Paper/blob/master/E-paper_Separate_Program/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd4in0e.py`

Do not modify without good reason. The driver's `getbuffer()` already handles rotation
and 4-bit nibble packing — our renderer only handles resize + Floyd-Steinberg dithering.

## Git Identity

For commits in this repo: `Obryn 🐉 <obryn-ai@dotbeeps.dev>`