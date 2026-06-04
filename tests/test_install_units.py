"""Tests for install-time systemd unit rendering."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from render_systemd_units import render_units  # noqa: E402


def test_render_units_substitutes_absolute_app_dir(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    out_dir = tmp_path / "units"

    written = render_units(
        "/opt/e-ink-gadget",
        repo / "pi" / "setup",
        out_dir,
    )

    assert sorted(path.name for path in written) == [
        "eink-gadget-setup.service",
        "eink-gadget.service",
    ]

    daemon_unit = (out_dir / "eink-gadget.service").read_text(encoding="utf-8")
    setup_unit = (out_dir / "eink-gadget-setup.service").read_text(encoding="utf-8")

    assert "@APP_DIR@" not in daemon_unit
    assert "@APP_DIR@" not in setup_unit
    assert "WorkingDirectory=/opt/e-ink-gadget" in daemon_unit
    assert "EnvironmentFile=-/etc/e-ink-gadget/eink-gadget.env" in daemon_unit
    assert "--gallery-dir ${EINK_GALLERY_DIR}" in daemon_unit
    assert "ExecStart=/bin/bash /opt/e-ink-gadget/pi/setup/gadget-usb.sh" in setup_unit


def test_render_units_rejects_relative_app_dir(tmp_path):
    repo = Path(__file__).resolve().parents[1]

    with pytest.raises(ValueError, match="APP_DIR must be absolute"):
        render_units("relative/path", repo / "pi" / "setup", tmp_path)
