"""Tests for pi/protocol.py — binary serial protocol parser."""

from __future__ import annotations

import pytest

from pi.protocol import (
    CMD_CLEAR,
    CMD_IMAGE,
    CMD_PING,
    CMD_SLEEP,
    ERROR_DISPLAY,
    ERROR_MALFORMED,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN_CMD,
    MAGIC,
    HEADER_SIZE,
    MAX_PAYLOAD_DEFAULT,
    Frame,
    FrameParser,
    ProtocolError,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _build_frame(cmd: int, payload: bytes = b"") -> bytes:
    """Build a raw on-the-wire frame for testing."""
    length = len(payload)
    return MAGIC + bytes([cmd]) + length.to_bytes(3, "big") + payload


def _image_payload(width: int = 100, height: int = 80, fmt: int = 0) -> bytes:
    """Create a minimal image payload with the given dimensions and format."""
    header = width.to_bytes(2, "big") + height.to_bytes(2, "big") + bytes([fmt])
    pixel_count = width * height * (4 if fmt == 1 else 3)
    return header + b"\xAB" * pixel_count


# ── Frame static helpers ─────────────────────────────────────────────────────


class TestFrameHelpers:
    def test_ack_bytes(self):
        assert Frame.ack() == b"\xa0\x00\x00\x00\x00\x00"

    def test_error_bytes(self):
        for code in (ERROR_UNKNOWN_CMD, ERROR_MALFORMED, ERROR_DISPLAY, ERROR_TIMEOUT):
            got = Frame.error(code)
            assert got == b"\xee" + bytes([code]) + b"\x00\x00\x00\x00"
            assert len(got) == 6

    def test_error_default_structure(self):
        got = Frame.error(ERROR_UNKNOWN_CMD)
        assert got[0] == 0xEE
        assert got[1] == ERROR_UNKNOWN_CMD
        assert got[2:] == b"\x00\x00\x00\x00"


# ── complete single-frame parsing ────────────────────────────────────────────


class TestCompleteFrames:
    def test_image_frame(self):
        payload = _image_payload()
        parser = FrameParser()
        frames = parser.feed(_build_frame(CMD_IMAGE, payload))
        assert len(frames) == 1
        assert frames[0].cmd == CMD_IMAGE
        assert frames[0].payload == payload

    def test_clear_frame(self):
        parser = FrameParser()
        frames = parser.feed(_build_frame(CMD_CLEAR))
        assert len(frames) == 1
        assert frames[0].cmd == CMD_CLEAR
        assert frames[0].payload == b""

    def test_sleep_frame(self):
        parser = FrameParser()
        frames = parser.feed(_build_frame(CMD_SLEEP))
        assert len(frames) == 1
        assert frames[0].cmd == CMD_SLEEP
        assert frames[0].payload == b""

    def test_ping_frame(self):
        parser = FrameParser()
        frames = parser.feed(_build_frame(CMD_PING))
        assert len(frames) == 1
        assert frames[0].cmd == CMD_PING
        assert frames[0].payload == b""

    def test_frame_is_dataclass(self):
        cmd = CMD_IMAGE
        payload = b"some bytes"
        f = Frame(cmd=cmd, payload=payload)
        assert f.cmd == cmd
        assert f.payload == payload
        # Ensure it's actually a dataclass (repr, eq, etc.)
        assert repr(f) == f"Frame(cmd={cmd}, payload={payload!r})"


# ── partial-read buffering ───────────────────────────────────────────────────


class TestPartialRead:
    def test_header_only_then_payload(self):
        """Feed just the header first, then the payload in a second call."""
        full = _build_frame(CMD_PING, b"hello")
        # header before the last byte of payload
        header_part = full[:HEADER_SIZE + 3]
        payload_rest = full[HEADER_SIZE + 3:]

        parser = FrameParser()
        frames = parser.feed(header_part)
        assert frames == []  # incomplete

        frames = parser.feed(payload_rest)
        assert len(frames) == 1
        assert frames[0].cmd == CMD_PING
        assert frames[0].payload == b"hello"

    def test_two_byte_at_a_time(self):
        """Feed one or two bytes at a time to exercise incremental buffering."""
        raw = _build_frame(CMD_CLEAR)
        parser = FrameParser()
        frames: list[Frame] = []
        for i in range(0, len(raw), 2):
            frames.extend(parser.feed(raw[i : i + 2]))
        assert len(frames) == 1
        assert frames[0].cmd == CMD_CLEAR

    def test_partial_magic_only(self):
        """Incomplete magic (single 0xED) is rejected and discarded."""
        parser = FrameParser()
        frames = parser.feed(b"\xed")
        assert frames == []
        # The single 0xED should still be buffered; subsequent 0xED + frame works
        frames = parser.feed(b"\xed" + _build_frame(CMD_PING)[2:])
        assert len(frames) == 1
        assert frames[0].cmd == CMD_PING


# ── incomplete magic rejection ───────────────────────────────────────────────


class TestIncompleteMagic:
    def test_single_byte_discarded_when_no_magic_completes(self):
        """A lone 0xED followed by non-magic bytes gets discarded."""
        parser = FrameParser()
        # 0xED 0x00 is not the full magic; should be discarded as garbage
        frame_garbage_then_valid = b"\xed\x00" + _build_frame(CMD_PING)
        frames = parser.feed(frame_garbage_then_valid)
        assert len(frames) == 1
        assert frames[0].cmd == CMD_PING

    def test_random_garbage_before_magic(self):
        garbage = b"\xff" * 10
        raw = garbage + _build_frame(CMD_SLEEP)
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 1
        assert frames[0].cmd == CMD_SLEEP

    def test_only_garbage_no_magic(self):
        parser = FrameParser()
        frames = parser.feed(b"\x01\x02\x03\x04\x05")
        assert frames == []


# ── oversized frame rejection ────────────────────────────────────────────────


class TestOversizedFrames:
    def test_payload_exceeds_max(self):
        parser = FrameParser(max_payload=100)
        payload = b"A" * 101
        with pytest.raises(ProtocolError, match="Payload too large"):
            parser.feed(_build_frame(CMD_IMAGE, payload))

    def test_payload_at_exactly_max_is_ok(self):
        parser = FrameParser(max_payload=100)
        payload = b"B" * 100
        frames = parser.feed(_build_frame(CMD_IMAGE, payload))
        assert len(frames) == 1
        assert frames[0].payload == payload

    def test_default_max_payload(self):
        assert MAX_PAYLOAD_DEFAULT == 16 * 1024 * 1024

    def test_oversized_rejected_before_resync(self):
        """Oversized frame raises even if garbage precedes it."""
        parser = FrameParser(max_payload=10)
        payload = b"C" * 11
        with pytest.raises(ProtocolError):
            parser.feed(b"\x00" * 5 + _build_frame(CMD_IMAGE, payload))


# ── resync after garbage ─────────────────────────────────────────────────────


class TestResync:
    def test_garbage_then_valid_frame(self):
        garbage = b"\xde\xad\xbe\xef"
        raw = garbage + _build_frame(CMD_PING)
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 1
        assert frames[0].cmd == CMD_PING

    def test_multiple_garbages_then_frame(self):
        garbage = b"\x00" * 50 + b"\xff\xee" + b"\x01" * 20
        raw = garbage + _build_frame(CMD_CLEAR)
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 1
        assert frames[0].cmd == CMD_CLEAR

    def test_magic_appearing_in_payload_not_confused(self):
        """Payload containing 0xED 0xED should not trigger false resyncs."""
        payload = MAGIC + b"\x01" * 10
        raw = _build_frame(CMD_IMAGE, payload)
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 1
        assert frames[0].cmd == CMD_IMAGE
        assert frames[0].payload == payload

    def test_multiple_valid_frames_in_one_feed(self):
        raw = (
            _build_frame(CMD_PING)
            + _build_frame(CMD_CLEAR)
            + _build_frame(CMD_SLEEP)
        )
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 3
        assert [f.cmd for f in frames] == [CMD_PING, CMD_CLEAR, CMD_SLEEP]

    def test_mixed_garbage_and_multiple_frames(self):
        raw = (
            b"\x00\x00"
            + _build_frame(CMD_PING)
            + b"\xff"
            + _build_frame(CMD_CLEAR)
        )
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 2
        assert frames[0].cmd == CMD_PING
        assert frames[1].cmd == CMD_CLEAR


# ── edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_feed(self):
        parser = FrameParser()
        frames = parser.feed(b"")
        assert frames == []

    def test_zero_length_payload(self):
        # Payload length = 0 explicitly encoded in header
        raw = MAGIC + bytes([CMD_PING]) + (0).to_bytes(3, "big")
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 1
        assert frames[0].cmd == CMD_PING
        assert frames[0].payload == b""

    def test_payload_length_correctly_parsed(self):
        length = 0x123456  # ~1.2 MB as a big-endian test
        raw = MAGIC + bytes([CMD_IMAGE]) + length.to_bytes(3, "big")
        # Append exactly that many bytes as payload
        raw += b"X" * length
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 1
        assert len(frames[0].payload) == length

    def test_new_parser_has_empty_buffer(self):
        parser = FrameParser()
        # Feed nothing, feed nothing, should remain empty
        assert parser.feed(b"") == []
        assert parser.feed(b"") == []

    def test_payload_with_all_zeroes(self):
        payload = b"\x00" * 256
        raw = _build_frame(CMD_IMAGE, payload)
        parser = FrameParser()
        frames = parser.feed(raw)
        assert len(frames) == 1
        assert frames[0].payload == payload
