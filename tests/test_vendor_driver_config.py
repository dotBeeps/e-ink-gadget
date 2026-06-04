"""Tests for configurable vendored Waveshare driver timing knobs."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EPD4IN0E_PATH = REPO_ROOT / "pi" / "vendor" / "waveshare_epd" / "epd4in0e.py"
EPDCONFIG_PATH = REPO_ROOT / "pi" / "vendor" / "waveshare_epd" / "epdconfig.py"


class FakeEpdConfig(types.ModuleType):
    """Tiny fake for epd4in0e's relative epdconfig import."""

    RST_PIN = 17
    DC_PIN = 25
    CS_PIN = 8
    BUSY_PIN = 24

    def __init__(self) -> None:
        super().__init__("waveshare_test.epdconfig")
        self.events: list[tuple[Any, ...]] = []
        self.busy_reads: list[int] = [1]

    def digital_write(self, pin: int, value: int) -> None:
        self.events.append(("digital_write", pin, value))

    def digital_read(self, pin: int) -> int:
        self.events.append(("digital_read", pin))
        if self.busy_reads:
            return self.busy_reads.pop(0)
        return 1

    def delay_ms(self, delaytime: int) -> None:
        self.events.append(("delay_ms", delaytime))

    def spi_writebyte(self, data: list[int]) -> None:
        self.events.append(("spi_writebyte", data))

    def spi_writebyte2(self, data: list[int]) -> None:
        self.events.append(("spi_writebyte2", data))


def load_epd4in0e(fake_epdconfig: FakeEpdConfig):
    """Load epd4in0e.py with a fake relative epdconfig module."""
    package = types.ModuleType("waveshare_test")
    package.__path__ = [str(EPD4IN0E_PATH.parent)]
    sys.modules["waveshare_test"] = package
    sys.modules["waveshare_test.epdconfig"] = fake_epdconfig

    spec = importlib.util.spec_from_file_location(
        "waveshare_test.epd4in0e",
        EPD4IN0E_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["waveshare_test.epd4in0e"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def cleanup_loaded_modules():
    yield
    for name in [
        "waveshare_test.epd4in0e",
        "waveshare_test.epdconfig",
        "waveshare_test",
        "epdconfig_test",
    ]:
        sys.modules.pop(name, None)


def delay_events(fake_epdconfig: FakeEpdConfig) -> list[int]:
    return [cast(int, event[1]) for event in fake_epdconfig.events if event[0] == "delay_ms"]


def test_reset_defaults_to_stock_settle_delay(monkeypatch: pytest.MonkeyPatch):
    """First hardware bring-up should use Waveshare's conservative reset delay."""
    monkeypatch.delenv("EINK_RESET_SETTLE_MS", raising=False)
    fake = FakeEpdConfig()
    epd4in0e = load_epd4in0e(fake)

    epd4in0e.EPD().reset()

    assert delay_events(fake) == [20, 2, 20]


def test_reset_settle_delay_can_be_overridden(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EINK_RESET_SETTLE_MS", "5")
    fake = FakeEpdConfig()
    epd4in0e = load_epd4in0e(fake)

    epd4in0e.EPD().reset()

    assert delay_events(fake) == [5, 2, 5]


def test_booster_delay_defaults_to_stock_and_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    """Default keeps stock 200ms booster padding; env can remove that padding."""
    fake_default = FakeEpdConfig()
    epd4in0e_default = load_epd4in0e(fake_default)
    fake_default.busy_reads = [1, 1, 1]
    epd4in0e_default.EPD().TurnOnDisplay()
    assert delay_events(fake_default).count(200) == 4

    for name in ["waveshare_test.epd4in0e", "waveshare_test.epdconfig", "waveshare_test"]:
        sys.modules.pop(name, None)

    monkeypatch.setenv("EINK_BOOSTER_DELAY_MS", "0")
    fake_fast = FakeEpdConfig()
    epd4in0e_fast = load_epd4in0e(fake_fast)
    fake_fast.busy_reads = [1, 1, 1]
    epd4in0e_fast.EPD().TurnOnDisplay()
    assert delay_events(fake_fast).count(200) == 3


def test_busy_wait_times_out_when_panel_never_releases(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EINK_BUSY_TIMEOUT_MS", "10")
    monkeypatch.setenv("EINK_BUSY_POLL_MS", "5")
    fake = FakeEpdConfig()
    fake.busy_reads = [0, 0, 0]
    epd4in0e = load_epd4in0e(fake)

    with pytest.raises(RuntimeError, match="BUSY timeout after 10 ms"):
        epd4in0e.EPD().ReadBusyH()

    assert delay_events(fake) == [5, 5]


def load_epdconfig(monkeypatch: pytest.MonkeyPatch, raspberry_output: str):
    """Load epdconfig.py while forcing the RaspberryPi implementation path."""
    fake_spidev = types.ModuleType("spidev")

    class FakeSpiDev:
        def __init__(self) -> None:
            self.open = MagicMock()
            self.close = MagicMock()
            self.writebytes = MagicMock()
            self.writebytes2 = MagicMock()
            self.max_speed_hz: int | None = None
            self.mode: int | None = None

    setattr(fake_spidev, "SpiDev", FakeSpiDev)
    monkeypatch.setitem(sys.modules, "spidev", fake_spidev)

    fake_gpiozero = types.ModuleType("gpiozero")

    class FakeOutput:
        value = 0

        def on(self) -> None:
            self.value = 1

        def off(self) -> None:
            self.value = 0

        def close(self) -> None:
            pass

    def make_led(pin: int) -> FakeOutput:
        return FakeOutput()

    def make_button(pin: int, pull_up: bool = False) -> FakeOutput:
        return FakeOutput()

    setattr(fake_gpiozero, "LED", make_led)
    setattr(fake_gpiozero, "Button", make_button)
    monkeypatch.setitem(sys.modules, "gpiozero", fake_gpiozero)

    class FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def communicate(self) -> tuple[str, None]:
            return raspberry_output, None

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    spec = importlib.util.spec_from_file_location("epdconfig_test", EPDCONFIG_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["epdconfig_test"] = module
    spec.loader.exec_module(module)
    return module


def test_spi_speed_defaults_to_stock_4mhz(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EINK_SPI_HZ", raising=False)
    epdconfig = load_epdconfig(monkeypatch, "Raspberry Pi 4")

    assert epdconfig.module_init() == 0

    assert epdconfig.implementation.SPI.max_speed_hz == 4_000_000
    assert epdconfig.implementation.SPI.mode == 0b00


def test_spi_speed_can_be_overridden_to_fast_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EINK_SPI_HZ", "16000000")
    epdconfig = load_epdconfig(monkeypatch, "Raspberry Pi 4")

    assert epdconfig.module_init() == 0

    assert epdconfig.implementation.SPI.max_speed_hz == 16_000_000
    assert epdconfig.implementation.SPI.mode == 0b00
