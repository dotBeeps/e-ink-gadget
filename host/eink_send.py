#!/usr/bin/env python3
"""CLI tool to send images and commands to the e-ink gadget over USB serial.

Usage:
    eink_send.py image.png
    eink_send.py --clear
    eink_send.py --sleep
    eink_send.py --ping
    eink_send.py image.png --device /dev/ttyACM0 --baud 115200
"""

from __future__ import annotations

import argparse
import sys
from PIL import Image
from serial import Serial, SerialException


# ── Protocol constants (standalone copy — no pi/ dependency) ────────────────

MAGIC = b"\xed\xed"

CMD_IMAGE = 0x01
CMD_CLEAR = 0x02
CMD_SLEEP = 0x03
CMD_PING = 0x04

RESP_ACK_PREFIX = 0xA0
RESP_ERROR_PREFIX = 0xEE

ERROR_NAMES = {
    0x01: "UNKNOWN_CMD",
    0x02: "MALFORMED",
    0x03: "DISPLAY",
    0x04: "TIMEOUT",
}

DISPLAY_WIDTH = 600
DISPLAY_HEIGHT = 400


# ── helpers ──────────────────────────────────────────────────────────────────


def send_frame(ser: Serial, cmd: int, payload: bytes = b"") -> None:
    """Send one protocol frame and check the response."""
    length = len(payload)
    frame = MAGIC + bytes([cmd]) + length.to_bytes(3, "big") + payload
    ser.write(frame)

    # Read 6-byte response
    resp = ser.read(6)
    if len(resp) < 6:
        print("Warning: short response from device", file=sys.stderr)
        return

    if resp[0] == RESP_ACK_PREFIX:
        return  # success

    if resp[0] == RESP_ERROR_PREFIX:
        code = resp[1]
        name = ERROR_NAMES.get(code, f"UNKNOWN({code:#04x})")
        print(f"Device error: {name}", file=sys.stderr)
        sys.exit(1)

    print(f"Warning: unexpected response byte: {resp[0]:#04x}", file=sys.stderr)


def send_image(ser: Serial, image_path: str) -> None:
    """Open an image, convert to RGB24, resize, and send as IMAGE frame."""
    img = Image.open(image_path)
    img = img.convert("RGB")
    img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT))

    # Build payload: width(2B BE) + height(2B BE) + fmt(1B, 0x00=RGB24) + raw RGB
    payload = (
        DISPLAY_WIDTH.to_bytes(2, "big")
        + DISPLAY_HEIGHT.to_bytes(2, "big")
        + bytes([0x00])  # fmt: RGB24
        + img.tobytes()
    )
    send_frame(ser, CMD_IMAGE, payload)
    print(f"Sent {image_path!r} ({len(payload)} byte payload)")


def send_clear(ser: Serial) -> None:
    """Send a CLEAR command."""
    send_frame(ser, CMD_CLEAR)
    print("Sent CLEAR command")


def send_sleep(ser: Serial) -> None:
    """Send a SLEEP command."""
    send_frame(ser, CMD_SLEEP)
    print("Sent SLEEP command")


def send_ping(ser: Serial) -> None:
    """Send a PING command and report result."""
    send_frame(ser, CMD_PING)
    print("PING OK — device responded with ACK")


# ── main ────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send images and commands to the e-ink gadget over USB serial.",
    )
    parser.add_argument(
        "image",
        nargs="?",
        default=None,
        help="Path to an image file to send (resized to 600×400 RGB24)",
    )
    parser.add_argument(
        "--device",
        default="/dev/ttyACM0",
        help="Serial device path (default: /dev/ttyACM0)",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=921600,
        help="Serial baud rate (default: 921600)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Send CLEAR command instead of an image",
    )
    parser.add_argument(
        "--sleep",
        action="store_true",
        help="Send SLEEP command instead of an image",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="Send PING command instead of an image",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    # Determine what to do
    command_modes = [args.clear, args.sleep, args.ping]
    if sum(command_modes) > 1:
        print("error: specify at most one of --clear, --sleep, --ping", file=sys.stderr)
        sys.exit(1)

    has_image = args.image is not None
    has_command = any(command_modes)

    if not has_image and not has_command:
        print("error: provide an image path or one of --clear/--sleep/--ping", file=sys.stderr)
        sys.exit(1)

    if has_image and has_command:
        print("error: provide an image path OR a command flag, not both", file=sys.stderr)
        sys.exit(1)

    # Open serial and send
    try:
        ser = Serial(args.device, args.baud, timeout=60)
    except SerialException as exc:
        print(
            f"error: cannot open serial device {args.device!r}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        if args.ping:
            send_ping(ser)
        elif args.clear:
            send_clear(ser)
        elif args.sleep:
            send_sleep(ser)
        elif args.image:
            send_image(ser, args.image)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
