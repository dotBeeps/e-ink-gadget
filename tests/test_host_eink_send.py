"""Tests for the host-side eink_send serial CLI helpers."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

fake_serial = types.ModuleType("serial")
fake_serial.Serial = object
fake_serial.SerialException = Exception
sys.modules.setdefault("serial", fake_serial)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import eink_send  # noqa: E402


class FakeSerial:
    def __init__(self, response: bytes):
        self.response = response
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def read(self, size: int) -> bytes:
        assert size == 6
        return self.response


def test_send_frame_ack_succeeds():
    ser = FakeSerial(b"\xa0\x00\x00\x00\x00\x00")

    eink_send.send_frame(ser, eink_send.CMD_PING)

    assert ser.writes == [eink_send.MAGIC + bytes([eink_send.CMD_PING]) + b"\x00\x00\x00"]


def test_send_frame_short_response_exits_nonzero(capsys):
    ser = FakeSerial(b"")

    with pytest.raises(SystemExit) as excinfo:
        eink_send.send_frame(ser, eink_send.CMD_PING)

    assert excinfo.value.code == 1
    assert "short response" in capsys.readouterr().err


def test_send_frame_unexpected_response_exits_nonzero(capsys):
    ser = FakeSerial(b"\x99\x00\x00\x00\x00\x00")

    with pytest.raises(SystemExit) as excinfo:
        eink_send.send_frame(ser, eink_send.CMD_PING)

    assert excinfo.value.code == 1
    assert "unexpected response byte" in capsys.readouterr().err


def test_send_frame_device_error_exits_nonzero(capsys):
    ser = FakeSerial(b"\xee\x02\x00\x00\x00\x00")

    with pytest.raises(SystemExit) as excinfo:
        eink_send.send_frame(ser, eink_send.CMD_PING)

    assert excinfo.value.code == 1
    assert "MALFORMED" in capsys.readouterr().err
