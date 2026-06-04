"""
Flask web application for the e-ink gadget control interface.

Provides a REST API for uploading, viewing, composing, and displaying images on
the e-ink display, as well as gallery management.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageEnhance

from pi.renderer import prepare_image

_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")

DISPLAY_WIDTH = 600
DISPLAY_HEIGHT = 400
DISPLAY_STATE_FILENAME = ".display_state.json"
DEFAULT_RENDER_SETTINGS: dict[str, Any] = {
    "background": "ffffff",
    "scale_mode": "fit",
    "crop_x": 0,
    "crop_y": 0,
    "scale": 1.0,
    "rotation": 0,
    "brightness": 0,
    "contrast": 0,
    "saturation": 0,
}
VALID_SCALE_MODES = {"fit", "fill", "stretch", "original"}
PALETTE_NAMES = ["black", "white", "yellow", "red", "blue", "green"]
REFRESH_SECONDS = 19


def create_app(
    *,
    display,
    display_lock: Lock,
    gallery_dir: str = "/home/pi/eink-gadget/gallery",
) -> Flask:
    """Create and configure the Flask application.

    Args:
        display: An object with ``display_image(image: Image.Image)`` and
            ``clear()`` methods.
        display_lock: A ``threading.Lock`` used to serialise display access.
        gallery_dir: Path to the directory where uploaded images are stored.

    Returns:
        A configured Flask application instance.
    """
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "web", "templates"))
    app.config["gallery_dir"] = gallery_dir
    app.config["display"] = display
    app.config["display_lock"] = display_lock

    os.makedirs(gallery_dir, exist_ok=True)

    # ── helpers ────────────────────────────────────────────────────────────

    def _gallery_path() -> str:
        return app.config["gallery_dir"]

    def _get_display():
        return app.config["display"]

    def _get_lock() -> Lock:
        return app.config["display_lock"]

    def _state_path() -> str:
        return os.path.join(_gallery_path(), DISPLAY_STATE_FILENAME)

    def _default_settings() -> dict[str, Any]:
        return dict(DEFAULT_RENDER_SETTINGS)

    def _safe_gallery_file_path(filename: str) -> str | None:
        gdir = _gallery_path()
        real_gdir = os.path.realpath(gdir)
        real_path = os.path.realpath(os.path.join(gdir, filename))
        try:
            if os.path.commonpath([real_gdir, real_path]) != real_gdir:
                return None
        except ValueError:
            return None
        return real_path

    def _list_images() -> list[dict[str, Any]]:
        """Return sorted list of PNG images in the gallery directory."""
        gdir = _gallery_path()
        images: list[dict[str, Any]] = []
        try:
            for entry in os.scandir(gdir):
                if entry.is_file() and entry.name.lower().endswith(".png"):
                    stat = entry.stat()
                    info: dict[str, Any] = {
                        "name": entry.name,
                        "url": f"/image/{entry.name}",
                        "size_bytes": stat.st_size,
                        "modified": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    }
                    try:
                        with Image.open(entry.path) as img:
                            info["width"] = img.width
                            info["height"] = img.height
                    except Exception:
                        info["width"] = None
                        info["height"] = None
                    images.append(info)
        except FileNotFoundError:
            pass
        images.sort(key=lambda i: i["modified"], reverse=True)
        return images

    def _normalize_bg_color(bg: Optional[str]) -> str:
        """Normalize a hex RRGGBB or #RRGGBB string to lowercase RRGGBB."""
        if bg is None or bg == "":
            return "ffffff"
        clean = bg[1:] if bg.startswith("#") else bg
        if not _COLOR_RE.match(clean):
            raise ValueError(f"Invalid background colour: {bg!r}")
        return clean.lower()

    def _parse_bg_color(bg: Optional[str]) -> tuple[int, int, int]:
        """Parse a hex RRGGBB string into an (R, G, B) tuple."""
        clean = _normalize_bg_color(bg)
        return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))

    def _load_display_state() -> dict[str, Any] | None:
        try:
            with open(_state_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        active = data.get("active") if isinstance(data, dict) else None
        if not isinstance(active, dict):
            return None

        filename = active.get("filename")
        if not isinstance(filename, str):
            return None

        real_path = _safe_gallery_file_path(filename)
        if real_path is None or not os.path.isfile(real_path):
            return None

        settings = _default_settings()
        raw_settings = active.get("settings")
        if isinstance(raw_settings, dict):
            settings.update(raw_settings)

        return {
            "filename": filename,
            "displayed_at": active.get("displayed_at"),
            "settings": settings,
        }

    def _save_display_state(filename: str, settings: dict[str, Any]) -> None:
        payload = {
            "active": {
                "filename": filename,
                "displayed_at": datetime.now(timezone.utc).isoformat(),
                "settings": settings,
            }
        }
        tmp_path = _state_path() + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp_path, _state_path())

    def _clear_display_state() -> None:
        try:
            os.remove(_state_path())
        except FileNotFoundError:
            pass

    def _parse_render_settings() -> tuple[dict[str, Any] | None, str | None]:
        settings = _default_settings()

        body: dict[str, Any] = {}
        if request.is_json:
            raw_body = request.get_json(silent=True) or {}
            if isinstance(raw_body, dict):
                body = raw_body

        raw_bg = (
            body.get("background")
            or request.form.get("background")
            or request.args.get("background")
            or request.args.get("bg")
        )
        if raw_bg is not None:
            try:
                settings["background"] = _normalize_bg_color(str(raw_bg))
            except ValueError as exc:
                return None, str(exc)

        for key in (
            "scale_mode",
            "crop_x",
            "crop_y",
            "scale",
            "rotation",
            "brightness",
            "contrast",
            "saturation",
        ):
            if key in body:
                settings[key] = body[key]
            elif key in request.form:
                settings[key] = request.form[key]
            elif key in request.args:
                settings[key] = request.args[key]

        if settings["scale_mode"] not in VALID_SCALE_MODES:
            return None, f"Invalid scale mode: {settings['scale_mode']!r}"

        try:
            settings["crop_x"] = int(settings["crop_x"])
            settings["crop_y"] = int(settings["crop_y"])
            settings["scale"] = float(settings["scale"])
            settings["rotation"] = int(settings["rotation"])
            settings["brightness"] = int(settings["brightness"])
            settings["contrast"] = int(settings["contrast"])
            settings["saturation"] = int(settings["saturation"])
        except (TypeError, ValueError):
            return None, "Invalid numeric render setting"

        settings["scale"] = max(0.1, min(settings["scale"], 8.0))
        settings["rotation"] = round(settings["rotation"] / 90) * 90 % 360
        settings["brightness"] = max(-100, min(settings["brightness"], 100))
        settings["contrast"] = max(-100, min(settings["contrast"], 100))
        settings["saturation"] = max(-100, min(settings["saturation"], 100))
        return settings, None

    def _alpha_composite_cropped(
        canvas: Image.Image, layer: Image.Image, offset: tuple[int, int]
    ) -> None:
        """Composite layer onto canvas, safely cropping when offset is out of bounds."""
        x, y = offset
        dest_left = max(0, x)
        dest_top = max(0, y)
        dest_right = min(canvas.width, x + layer.width)
        dest_bottom = min(canvas.height, y + layer.height)
        if dest_right <= dest_left or dest_bottom <= dest_top:
            return

        src_left = dest_left - x
        src_top = dest_top - y
        src_right = src_left + (dest_right - dest_left)
        src_bottom = src_top + (dest_bottom - dest_top)
        cropped = layer.crop((src_left, src_top, src_right, src_bottom))
        canvas.alpha_composite(cropped, (dest_left, dest_top))

    def _compose_for_display(img: Image.Image, settings: dict[str, Any]) -> Image.Image:
        """Compose an uploaded image onto the 600x400 display canvas."""
        bg_rgb = _parse_bg_color(settings["background"])
        rgba = img.convert("RGBA")
        rotation = int(settings.get("rotation", 0)) % 360
        if rotation:
            rgba = rgba.rotate(-rotation, expand=True)
        mode = settings["scale_mode"]
        zoom = float(settings.get("scale", 1.0))
        canvas = Image.new("RGBA", (DISPLAY_WIDTH, DISPLAY_HEIGHT), bg_rgb + (255,))

        if mode == "stretch":
            layer_size = (
                max(1, round(DISPLAY_WIDTH * zoom)),
                max(1, round(DISPLAY_HEIGHT * zoom)),
            )
            layer = rgba.resize(layer_size, Image.LANCZOS)
        else:
            if mode == "original":
                factor = zoom
            else:
                sx = DISPLAY_WIDTH / rgba.width
                sy = DISPLAY_HEIGHT / rgba.height
                base_factor = min(sx, sy) if mode == "fit" else max(sx, sy)
                factor = base_factor * zoom
            layer_size = (
                max(1, round(rgba.width * factor)),
                max(1, round(rgba.height * factor)),
            )
            layer = rgba.resize(layer_size, Image.LANCZOS)

        x = (DISPLAY_WIDTH - layer.width) // 2 + int(settings.get("crop_x", 0))
        y = (DISPLAY_HEIGHT - layer.height) // 2 + int(settings.get("crop_y", 0))
        _alpha_composite_cropped(canvas, layer, (x, y))
        return canvas.convert("RGB")

    def _apply_color_adjustments(img: Image.Image, settings: dict[str, Any]) -> Image.Image:
        brightness = max(0.0, 1.0 + (int(settings["brightness"]) / 100.0))
        contrast = max(0.0, 1.0 + (int(settings["contrast"]) / 100.0))
        saturation = max(0.0, 1.0 + (int(settings["saturation"]) / 100.0))

        adjusted = ImageEnhance.Brightness(img).enhance(brightness)
        adjusted = ImageEnhance.Contrast(adjusted).enhance(contrast)
        adjusted = ImageEnhance.Color(adjusted).enhance(saturation)
        return adjusted

    def _status_payload() -> dict[str, Any]:
        images = _list_images()
        return {
            "status": "ok",
            "display": "connected",
            "display_width": DISPLAY_WIDTH,
            "display_height": DISPLAY_HEIGHT,
            "palette": PALETTE_NAMES,
            "refresh_seconds": REFRESH_SECONDS,
            "scale_modes": sorted(VALID_SCALE_MODES),
            "default_settings": _default_settings(),
            "active": _load_display_state(),
            "gallery_count": len(images),
        }

    # ── routes ─────────────────────────────────────────────────────────────

    @app.route("/", methods=["GET"])
    def index():
        if request.accept_mimetypes.best == "text/html" or "text/html" in (
            request.accept_mimetypes.values()
        ):
            return render_template("index.html")
        return jsonify(_status_payload())

    @app.route("/upload", methods=["POST"])
    def upload():
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        try:
            image = Image.open(file.stream)
        except Exception:
            return jsonify({"error": "Invalid or unreadable image file"}), 400

        image = image.convert("RGBA")
        image.thumbnail((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)

        filename = f"{uuid.uuid4().hex}.png"
        dest = os.path.join(_gallery_path(), filename)
        image.save(dest, "PNG")

        return jsonify(
            {
                "filename": filename,
                "url": f"/image/{filename}",
                "size": image.size,
            }
        ), 201

    @app.route("/gallery", methods=["GET"])
    def gallery():
        images = _list_images()
        active = _load_display_state()
        active_name = active["filename"] if active else None
        for image in images:
            image["active"] = image["name"] == active_name
        return jsonify({"images": images, "active": active})

    @app.route("/display/<path:filename>", methods=["POST"])
    def display_image(filename: str):
        real_path = _safe_gallery_file_path(filename)
        if real_path is None:
            return jsonify({"error": "Invalid filename"}), 400

        if not os.path.isfile(real_path):
            return jsonify({"error": "File not found"}), 404

        settings, settings_error = _parse_render_settings()
        if settings_error is not None or settings is None:
            return jsonify({"error": settings_error}), 400

        try:
            img = Image.open(real_path).convert("RGBA")
            composite = _compose_for_display(img, settings)
            composite = _apply_color_adjustments(composite, settings)
            prepared = prepare_image(composite)

            lock = _get_lock()
            disp = _get_display()
            with lock:
                disp.display_image(prepared)

            _save_display_state(filename, settings)
            return jsonify(
                {
                    "status": "ok",
                    "displayed": filename,
                    "active": _load_display_state(),
                }
            )
        except Exception as exc:
            return jsonify({"error": f"Display failed: {exc}"}), 500

    @app.route("/image/<path:filename>", methods=["GET"])
    def serve_image(filename: str):
        real_path = _safe_gallery_file_path(filename)
        if real_path is None:
            return jsonify({"error": "Invalid filename"}), 400

        if not os.path.isfile(real_path):
            return jsonify({"error": "File not found"}), 404

        return send_file(real_path, mimetype="image/png")

    @app.route("/image/<path:filename>", methods=["DELETE"])
    def delete_image(filename: str):
        real_path = _safe_gallery_file_path(filename)
        if real_path is None:
            return jsonify({"error": "Invalid filename"}), 400

        if not os.path.isfile(real_path):
            return jsonify({"error": "File not found"}), 404

        active = _load_display_state()
        os.remove(real_path)
        if active and active["filename"] == filename:
            _clear_display_state()
        return jsonify({"status": "ok", "deleted": filename, "active": _load_display_state()})

    @app.route("/clear", methods=["POST"])
    def clear():
        lock = _get_lock()
        disp = _get_display()
        try:
            with lock:
                disp.clear()
            _clear_display_state()
            return jsonify({"status": "ok", "active": None})
        except Exception as exc:
            return jsonify({"error": f"Clear failed: {exc}"}), 500

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"status": "ok"})

    return app
