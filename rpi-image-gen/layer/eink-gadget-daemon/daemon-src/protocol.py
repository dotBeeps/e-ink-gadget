"""Binary serial protocol parser for the e-ink-gadget.

Protocol format (6-byte header + variable payload):
  MAGIC (2 bytes): 0xED 0xED
  CMD   (1 byte):  command identifier
  LEN   (3 bytes): payload length (big-endian, max 16MB)

Commands:
  0x01 IMAGE  — payload: width(2B BE) + height(2B BE) + fmt(1B) + raw pixels
  0x02 CLEAR  — no payload
  0x03 SLEEP  — no payload
  0x04 PING   — no payload

Response frames (6 bytes):
  ACK:   0xA0 0x00 0x00 0x00 0x00 0x00
  ERROR: 0xEE <code> 0x00 0x00 0x00 0x00
"""

from __future__ import annotations

from dataclasses import dataclass

# ── constants ────────────────────────────────────────────────────────────────

MAGIC = b"\xed\xed"
HEADER_SIZE = 6  # 2 magic + 1 cmd + 3 len

CMD_IMAGE = 0x01
CMD_CLEAR = 0x02
CMD_SLEEP = 0x03
CMD_PING = 0x04

RESP_ACK = b"\xa0\x00\x00\x00\x00\x00"
RESP_ERROR_PREFIX = b"\xee"

ERROR_UNKNOWN_CMD = 0x01
ERROR_MALFORMED = 0x02
ERROR_DISPLAY = 0x03
ERROR_TIMEOUT = 0x04

MAX_PAYLOAD_DEFAULT = 16 * 1024 * 1024  # 16 MB


# ── errors ───────────────────────────────────────────────────────────────────


class ProtocolError(ValueError):
    """Raised for protocol violations (oversized payloads, invalid frames)."""


# ── frame dataclass ──────────────────────────────────────────────────────────


@dataclass
class Frame:
    """A parsed protocol frame."""

    cmd: int
    payload: bytes

    @staticmethod
    def ack() -> bytes:
        """Build a 6-byte ACK response."""
        return RESP_ACK

    @staticmethod
    def error(code: int) -> bytes:
        """Build a 6-byte ERROR response with the given error code."""
        return RESP_ERROR_PREFIX + bytes([code]) + b"\x00\x00\x00\x00"


# ── parser ───────────────────────────────────────────────────────────────────


class FrameParser:
    """Buffered binary protocol parser with automatic resync.

    Usage::

        parser = FrameParser()
        for chunk in serial_port_stream():
            for frame in parser.feed(chunk):
                handle(frame)
    """

    def __init__(self, max_payload: int = MAX_PAYLOAD_DEFAULT) -> None:
        self.max_payload = max_payload
        self._buffer = b""

    # ------------------------------------------------------------------

    def feed(self, data: bytes) -> list[Frame]:
        """Feed raw bytes into the parser.

        Returns a list of complete ``Frame`` objects parsed from the data
        (possibly empty).  Partial data is buffered internally.
        """
        self._buffer += data
        frames: list[Frame] = []

        while True:
            if len(self._buffer) < HEADER_SIZE:
                break

            # Seek to next magic byte pair ──────────────────────────
            magic_idx = self._buffer.find(MAGIC)
            if magic_idx == -1:
                # No valid frame header anywhere in buffer → discard all
                self._buffer = b""
                break

            if magic_idx > 0:
                # Discard garbage bytes before the magic
                self._buffer = self._buffer[magic_idx:]

            # Still need a full header?
            if len(self._buffer) < HEADER_SIZE:
                break

            # Parse header ──────────────────────────────────────────
            cmd = self._buffer[2]
            payload_len = int.from_bytes(self._buffer[3:6], "big")

            if payload_len > self.max_payload:
                raise ProtocolError(
                    f"Payload too large: {payload_len} > {self.max_payload}"
                )

            total_len = HEADER_SIZE + payload_len
            if len(self._buffer) < total_len:
                break  # wait for more data

            # Consume one complete frame ────────────────────────────
            payload = self._buffer[HEADER_SIZE:total_len]
            frames.append(Frame(cmd=cmd, payload=payload))
            self._buffer = self._buffer[total_len:]

        return frames
