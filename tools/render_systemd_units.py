#!/usr/bin/env python3
"""Render e-ink-gadget systemd unit templates for a concrete APP_DIR."""

from __future__ import annotations

import argparse
from pathlib import Path

UNIT_TEMPLATES = {
    "eink-gadget.service.in": "eink-gadget.service",
    "eink-gadget-setup.service.in": "eink-gadget-setup.service",
}


def render_units(app_dir: str, template_dir: Path, output_dir: Path) -> list[Path]:
    """Render unit templates into output_dir and return written paths."""
    app_path = Path(app_dir)
    if not app_path.is_absolute():
        raise ValueError(f"APP_DIR must be absolute: {app_dir!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for template_name, unit_name in UNIT_TEMPLATES.items():
        template_path = template_dir / template_name
        content = template_path.read_text(encoding="utf-8")
        if "@APP_DIR@" not in content:
            raise ValueError(f"template missing @APP_DIR@ placeholder: {template_path}")
        rendered = content.replace("@APP_DIR@", app_dir)
        out_path = output_dir / unit_name
        out_path.write_text(rendered, encoding="utf-8")
        written.append(out_path)
    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", required=True, help="Absolute install path")
    parser.add_argument(
        "--template-dir",
        default="pi/setup",
        type=Path,
        help="Directory containing *.service.in templates",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory to write rendered units into",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    for path in render_units(args.app_dir, args.template_dir, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
