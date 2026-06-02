"""
Tests for pi/display_daemon.py — frame processing pipeline.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from pi.display_daemon import process_frame
from pi.eink_driver import DisplayError
from pi.protocol import (
    CMD_CLEAR,
    CMD_IMAGE,
    CMD_PING,
    CMD_SLEEP,
    ERROR_DISPLAY,
    ERROR_MALFORMED,
    ERROR_UNKNOWN_CMD,
    Frame,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _image_payload(
    width: int = 100,
    height: int = 80,
    fmt: int = 0,
    pixel_byte: int = 0xAB,
) -> bytes:
    """Create a minimal image payload with the given dimensions and format.

    fmt=0 → RGB  (3 bytes per pixel)
    fmt=1 → RGBA (4 bytes per pixel)
    """
    header = width.to_bytes(2, "big") + height.to_bytes(2, "big") + bytes([fmt])
    bpp = 4 if fmt == 1 else 3
    pixel_count = width * height * bpp
    return header + bytes([pixel_byte]) * pixel_count


# ── tests ─────────────────────────────────────────────────────────────────────


class TestProcessFrame(unittest.TestCase):
    """Tests for process_frame() — the core dispatch function."""

    def setUp(self):
        self.mock_display = MagicMock()

    # ── PING ──────────────────────────────────────────────────────────────

    def test_ping_returns_ack(self):
        """PING frames should return an ACK response."""
        frame = Frame(cmd=CMD_PING, payload=b"")
        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.ack())
        self.mock_display.assert_not_called()

    # ── CLEAR ─────────────────────────────────────────────────────────────

    def test_clear_calls_display_clear_and_returns_ack(self):
        """CLEAR frames should call display.clear() and return ACK."""
        frame = Frame(cmd=CMD_CLEAR, payload=b"")
        response = process_frame(frame, self.mock_display)
        self.mock_display.clear.assert_called_once_with()
        self.assertEqual(response, Frame.ack())

    def test_clear_display_error_returns_error(self):
        """DisplayError during clear should return an ERROR response."""
        self.mock_display.clear.side_effect = DisplayError("clear failed")
        frame = Frame(cmd=CMD_CLEAR, payload=b"")
        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_DISPLAY))

    # ── SLEEP ─────────────────────────────────────────────────────────────

    def test_sleep_calls_display_sleep_and_returns_ack(self):
        """SLEEP frames should call display.sleep() and return ACK."""
        frame = Frame(cmd=CMD_SLEEP, payload=b"")
        response = process_frame(frame, self.mock_display)
        self.mock_display.sleep.assert_called_once_with()
        self.assertEqual(response, Frame.ack())

    def test_sleep_display_error_returns_error(self):
        """DisplayError during sleep should return an ERROR response."""
        self.mock_display.sleep.side_effect = DisplayError("sleep failed")
        frame = Frame(cmd=CMD_SLEEP, payload=b"")
        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_DISPLAY))

    # ── IMAGE (RGB) ───────────────────────────────────────────────────────

    @patch("pi.display_daemon.prepare_image")
    def test_image_rgb_calls_prepare_and_display(self, mock_prepare):
        """IMAGE frame with RGB data should prepare and display the image."""
        prepared = MagicMock()
        mock_prepare.return_value = prepared

        payload = _image_payload(width=16, height=12, fmt=0, pixel_byte=0x80)
        frame = Frame(cmd=CMD_IMAGE, payload=payload)

        response = process_frame(frame, self.mock_display)

        # Verify prepare_image was called with a PIL Image of the right size
        mock_prepare.assert_called_once()
        call_img = mock_prepare.call_args[0][0]
        self.assertEqual(call_img.size, (16, 12))
        self.assertEqual(call_img.mode, "RGB")

        self.mock_display.display_image.assert_called_once_with(prepared)
        self.assertEqual(response, Frame.ack())

    @patch("pi.display_daemon.prepare_image")
    def test_image_rgba_converts_to_rgb(self, mock_prepare):
        """IMAGE frame with RGBA data should convert to RGB before rendering."""
        prepared = MagicMock()
        mock_prepare.return_value = prepared

        payload = _image_payload(width=8, height=6, fmt=1, pixel_byte=0xCC)
        frame = Frame(cmd=CMD_IMAGE, payload=payload)

        response = process_frame(frame, self.mock_display)

        # Verify prepare_image received an RGB image (converted from RGBA)
        mock_prepare.assert_called_once()
        call_img = mock_prepare.call_args[0][0]
        self.assertEqual(call_img.size, (8, 6))
        self.assertEqual(call_img.mode, "RGB")

        self.mock_display.display_image.assert_called_once_with(prepared)
        self.assertEqual(response, Frame.ack())

    # ── IMAGE (error cases) ───────────────────────────────────────────────

    def test_image_too_short_payload_returns_error(self):
        """IMAGE frame with fewer than 5 bytes of payload returns error."""
        frame = Frame(cmd=CMD_IMAGE, payload=b"\x00\x01\x02\x03")  # 4 bytes
        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_MALFORMED))
        self.mock_display.display_image.assert_not_called()

    def test_image_wrong_data_size_rgb_returns_error(self):
        """IMAGE frame with mismatched RGB pixel data size returns error."""
        # width=10, height=10, RGB → expects 300 bytes, send only 200
        payload = _image_payload(width=10, height=10, fmt=0, pixel_byte=0xAA)
        # Truncate pixel data
        truncated = payload[:5] + payload[5:205]  # keep header + 200 bytes
        frame = Frame(cmd=CMD_IMAGE, payload=truncated)
        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_MALFORMED))
        self.mock_display.display_image.assert_not_called()

    def test_image_wrong_data_size_rgba_returns_error(self):
        """IMAGE frame with mismatched RGBA pixel data size returns error."""
        # width=10, height=10, RGBA → expects 400 bytes, send only 300
        payload = _image_payload(width=10, height=10, fmt=1, pixel_byte=0xBB)
        truncated = payload[:5] + payload[5:305]
        frame = Frame(cmd=CMD_IMAGE, payload=truncated)
        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_MALFORMED))
        self.mock_display.display_image.assert_not_called()

    @patch("pi.display_daemon.prepare_image")
    def test_image_display_error_returns_error(self, mock_prepare):
        """DisplayError during image display should return an ERROR response."""
        mock_prepare.return_value = MagicMock()
        self.mock_display.display_image.side_effect = DisplayError(
            "display failed"
        )

        payload = _image_payload(width=4, height=4, fmt=0)
        frame = Frame(cmd=CMD_IMAGE, payload=payload)

        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_DISPLAY))

    @patch("pi.display_daemon.prepare_image")
    def test_image_generic_exception_returns_malformed(self, mock_prepare):
        """Non-DisplayError exceptions during image processing return ERROR."""
        mock_prepare.side_effect = ValueError("bad image data")

        payload = _image_payload(width=4, height=4, fmt=0)
        frame = Frame(cmd=CMD_IMAGE, payload=payload)

        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_MALFORMED))

    # ── UNKNOWN COMMAND ───────────────────────────────────────────────────

    def test_unknown_command_returns_error(self):
        """An unrecognised command should return ERROR_UNKNOWN_CMD."""
        frame = Frame(cmd=0xFF, payload=b"")
        response = process_frame(frame, self.mock_display)
        self.assertEqual(response, Frame.error(ERROR_UNKNOWN_CMD))
        self.mock_display.assert_not_called()


if __name__ == "__main__":
    unittest.main()
