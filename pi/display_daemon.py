"""
Main display daemon for the e-ink gadget.

Receives frames over serial (USB gadget), renders images via the
Spectra 6 display driver, and returns ACK/ERROR responses.
"""

from __future__ import annotations

import logging
import threading

from PIL import Image

from pi.web import create_app

from pi.eink_driver import DisplayError, EInkDisplay
from pi.protocol import (
    CMD_CLEAR,
    CMD_IMAGE,
    CMD_PING,
    CMD_SLEEP,
    ERROR_DISPLAY,
    ERROR_MALFORMED,
    ERROR_UNKNOWN_CMD,
    Frame,
    FrameParser,
)
from pi.renderer import prepare_image

logger = logging.getLogger(__name__)

# Image format constants (from the protocol spec)
_FMT_RGB = 0
_FMT_RGBA = 1

# Header size within the image payload: width(2) + height(2) + fmt(1)
_IMAGE_HEADER_SIZE = 5


# ── frame processing ──────────────────────────────────────────────────────────


def process_frame(frame: Frame, display: EInkDisplay) -> bytes:
    """Process one parsed frame and return the response bytes.

    Args:
        frame: A parsed protocol Frame.
        display: An initialised EInkDisplay instance.

    Returns:
        A 6-byte ACK or ERROR response.
    """
    cmd = frame.cmd

    if cmd == CMD_PING:
        return Frame.ack()

    if cmd == CMD_CLEAR:
        try:
            display.clear()
            return Frame.ack()
        except DisplayError:
            logger.exception("Display clear failed")
            return Frame.error(ERROR_DISPLAY)

    if cmd == CMD_SLEEP:
        try:
            display.sleep()
            return Frame.ack()
        except DisplayError:
            logger.exception("Display sleep failed")
            return Frame.error(ERROR_DISPLAY)

    if cmd == CMD_IMAGE:
        return _process_image(frame, display)

    logger.warning("Unknown command: 0x%02x", cmd)
    return Frame.error(ERROR_UNKNOWN_CMD)


def _process_image(frame: Frame, display: EInkDisplay) -> bytes:
    """Decode and display an IMAGE frame payload."""
    payload = frame.payload

    if len(payload) < _IMAGE_HEADER_SIZE:
        logger.warning(
            "IMAGE payload too short: %d bytes (need at least %d)",
            len(payload),
            _IMAGE_HEADER_SIZE,
        )
        return Frame.error(ERROR_MALFORMED)

    width = int.from_bytes(payload[0:2], "big")
    height = int.from_bytes(payload[2:4], "big")
    fmt = payload[4]

    raw_pixels = payload[_IMAGE_HEADER_SIZE:]

    if fmt == _FMT_RGBA:
        expected = width * height * 4
        mode = "RGBA"
    else:
        expected = width * height * 3
        mode = "RGB"

    if len(raw_pixels) != expected:
        logger.warning(
            "IMAGE pixel data size mismatch: got %d, expected %d "
            "(width=%d, height=%d, fmt=%s)",
            len(raw_pixels),
            expected,
            width,
            height,
            "RGBA" if fmt == _FMT_RGBA else "RGB",
        )
        return Frame.error(ERROR_MALFORMED)

    try:
        img = Image.frombytes(mode, (width, height), raw_pixels)
        if fmt == _FMT_RGBA:
            img = img.convert("RGB")

        prepared = prepare_image(img)
        display.display_image(prepared)
        return Frame.ack()
    except DisplayError:
        logger.exception("Display IMAGE failed")
        return Frame.error(ERROR_DISPLAY)
    except Exception:
        logger.exception("IMAGE processing failed")
        return Frame.error(ERROR_MALFORMED)


def _process_frame_with_lock(
    frame: Frame,
    display: EInkDisplay,
    lock: threading.Lock,
) -> bytes:
    """Process one frame while holding the display lock.

    Args:
        frame: A parsed protocol Frame.
        display: An initialised EInkDisplay instance.
        lock: A threading.Lock that protects display access.

    Returns:
        A 6-byte ACK or ERROR response.
    """
    with lock:
        return process_frame(frame, display)


# ── daemon loop ───────────────────────────────────────────────────────────────


def run_daemon(
    serial_device: str = "/dev/ttyGS0",
    baud: int = 921600,
) -> None:
    """Run the display daemon main loop.

    Args:
        serial_device: Serial device path (USB gadget).
        baud: Baud rate for the serial connection.
    """
    import serial  # noqa: PLC0415

    display = EInkDisplay()
    display.init()
    display.clear()
    logger.info("Display initialised and cleared")

    display_lock = threading.Lock()

    # Start the Flask web app in a background daemon thread
    app = create_app(
        display=display,
        display_lock=display_lock,
        gallery_dir="/home/pi/eink-gadget/gallery",
    )
    flask_thread = threading.Thread(
        target=app.run,
        kwargs={
            "host": "0.0.0.0",
            "port": 8080,
            "threaded": True,
            "debug": False,
            "use_reloader": False,
        },
        daemon=True,
    )
    flask_thread.start()
    logger.info("Flask web app started on http://0.0.0.0:8080")

    parser = FrameParser()

    with serial.Serial(serial_device, baud, timeout=0.1) as ser:
        logger.info("Serial port %s opened at %d baud", serial_device, baud)

        while True:
            try:
                data = ser.read(4096)
            except Exception:
                logger.exception("Serial read error")
                continue

            if not data:
                continue

            try:
                frames = parser.feed(data)
            except Exception:
                logger.exception("Frame parsing error; resetting parser")
                parser = FrameParser()
                continue

            for frame in frames:
                response = _process_frame_with_lock(frame, display, display_lock)

                # If we just processed a SLEEP command, re-init before next frame
                if frame.cmd == CMD_SLEEP:
                    with display_lock:
                        try:
                            display.init()
                            logger.info("Display re-initialised after sleep")
                        except DisplayError:
                            logger.exception("Display re-init after sleep failed")
                            response = Frame.error(ERROR_DISPLAY)

                try:
                    ser.write(response)
                except Exception:
                    logger.exception("Serial write error")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_daemon()
