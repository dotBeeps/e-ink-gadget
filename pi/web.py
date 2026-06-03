"""
Flask web application for the e-ink gadget control interface.

Provides a REST API for uploading, viewing, and displaying images on
the e-ink display, as well as gallery management.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image

from pi.renderer import prepare_image

_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")

DISPLAY_WIDTH = 600
DISPLAY_HEIGHT = 400


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

    # Ensure the gallery directory exists
    os.makedirs(gallery_dir, exist_ok=True)

    # ── helpers ────────────────────────────────────────────────────────────

    def _gallery_path() -> str:
        return app.config["gallery_dir"]

    def _get_display():
        return app.config["display"]

    def _get_lock() -> Lock:
        return app.config["display_lock"]

    def _list_images() -> list[dict]:
        """Return sorted list of PNG images in the gallery directory."""
        gdir = _gallery_path()
        images: list[dict] = []
        try:
            for entry in os.scandir(gdir):
                if entry.is_file() and entry.name.lower().endswith(".png"):
                    stat = entry.stat()
                    images.append(
                        {
                            "name": entry.name,
                            "url": f"/image/{entry.name}",
                            "size_bytes": stat.st_size,
                            "modified": datetime.fromtimestamp(
                                stat.st_mtime, tz=timezone.utc
                            ).isoformat(),
                        }
                    )
        except FileNotFoundError:
            pass
        # Sort by modified time descending (newest first)
        images.sort(key=lambda i: i["modified"], reverse=True)
        return images

    def _parse_bg_color(bg: Optional[str]) -> tuple[int, int, int]:
        """Parse a hex RRGGBB string into an (R, G, B) tuple.

        Raises:
            ValueError: If the string is not a valid hex colour.
        """
        if bg is None:
            return (255, 255, 255)  # default white
        if not _COLOR_RE.match(bg):
            raise ValueError(f"Invalid background colour: {bg!r}")
        return (int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16))

    # ── routes ─────────────────────────────────────────────────────────────

    @app.route("/", methods=["GET"])
    def index():
        if request.accept_mimetypes.best == "text/html" or "text/html" in (
            request.accept_mimetypes.values()
        ):
            return render_template("index.html")
        images = _list_images()
        return jsonify(
            {
                "status": "ok",
                "display": "connected",
                "gallery_count": len(images),
            }
        )

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

        # Convert to RGBA (preserves alpha, handles grayscale/palette)
        image = image.convert("RGBA")

        # Resize to fit within 600x400 (thumbnail, maintaining aspect ratio)
        image.thumbnail((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)

        # Save as PNG with UUID filename
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
        return jsonify({"images": images})

    @app.route("/display/<path:filename>", methods=["POST"])
    def display_image(filename: str):
        gdir = _gallery_path()
        filepath = os.path.join(gdir, filename)

        # Security: prevent path traversal
        real_path = os.path.realpath(filepath)
        real_gdir = os.path.realpath(gdir)
        if not real_path.startswith(real_gdir):
            return jsonify({"error": "Invalid filename"}), 400

        if not os.path.isfile(real_path):
            return jsonify({"error": "File not found"}), 404

        # Parse optional background colour
        bg_color = request.args.get("bg")
        try:
            bg_rgb = _parse_bg_color(bg_color)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            # Load the image, composite onto background, prepare, display
            img = Image.open(real_path).convert("RGBA")

            # Composite onto background colour
            bg = Image.new("RGBA", img.size, bg_rgb + (255,))
            composite = Image.alpha_composite(bg, img).convert("RGB")

            prepared = prepare_image(composite)

            lock = _get_lock()
            disp = _get_display()
            with lock:
                disp.display_image(prepared)

            return jsonify({"status": "ok", "displayed": filename})
        except Exception as exc:
            return jsonify({"error": f"Display failed: {exc}"}), 500

    @app.route("/image/<path:filename>", methods=["GET"])
    def serve_image(filename: str):
        gdir = _gallery_path()
        filepath = os.path.join(gdir, filename)

        real_path = os.path.realpath(filepath)
        real_gdir = os.path.realpath(gdir)
        if not real_path.startswith(real_gdir):
            return jsonify({"error": "Invalid filename"}), 400

        if not os.path.isfile(real_path):
            return jsonify({"error": "File not found"}), 404

        return send_file(real_path, mimetype="image/png")

    @app.route("/image/<path:filename>", methods=["DELETE"])
    def delete_image(filename: str):
        gdir = _gallery_path()
        filepath = os.path.join(gdir, filename)

        real_path = os.path.realpath(filepath)
        real_gdir = os.path.realpath(gdir)
        if not real_path.startswith(real_gdir):
            return jsonify({"error": "Invalid filename"}), 400

        if not os.path.isfile(real_path):
            return jsonify({"error": "File not found"}), 404

        os.remove(real_path)
        return jsonify({"status": "ok", "deleted": filename})

    @app.route("/clear", methods=["POST"])
    def clear():
        lock = _get_lock()
        disp = _get_display()
        try:
            with lock:
                disp.clear()
            return jsonify({"status": "ok"})
        except Exception as exc:
            return jsonify({"error": f"Clear failed: {exc}"}), 500

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"status": "ok"})

    return app
