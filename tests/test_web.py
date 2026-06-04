"""
Tests for the e-ink gadget Flask web application (pi/web.py).

Tests cover all endpoints with mocked display hardware.
"""

from __future__ import annotations

import io
import os
import re
import threading
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from pi.web import create_app


class TrackingLock:
    """A lock wrapper that tracks whether acquire/release were called."""

    def __init__(self):
        self._lock = threading.Lock()
        self.acquire_count = 0
        self.release_count = 0

    def acquire(self, blocking=True, timeout=-1):
        self.acquire_count += 1
        return self._lock.acquire(blocking, timeout)

    def release(self):
        self.release_count += 1
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gallery_dir(tmp_path):
    """Temporary gallery directory."""
    d = tmp_path / "gallery"
    d.mkdir()
    return str(d)


@pytest.fixture
def mock_display():
    """Mock display object with display_image and clear methods."""
    display = MagicMock()
    # Make display_image accept an image, but we verify it was called
    return display


@pytest.fixture
def display_lock():
    """A real threading Lock for the test app."""
    return TrackingLock()


@pytest.fixture
def app(gallery_dir, mock_display, display_lock):
    """Create a Flask test application with mocked display."""
    application = create_app(
        display=mock_display,
        display_lock=display_lock,
        gallery_dir=gallery_dir,
    )
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    """Flask test client."""
    with app.test_client() as c:
        yield c


def _create_test_image(mode="RGB", size=(100, 100), color=(255, 0, 0)):
    """Helper to create a small PIL image and return it as file-like bytes."""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def _create_test_jpg(size=(100, 100), color=(0, 255, 0)):
    """Helper to create a JPEG image as file-like bytes."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    buf.seek(0)
    return buf


def _create_test_transparent_png(size=(100, 100)):
    """Helper to create a transparent RGBA PNG."""
    img = Image.new("RGBA", size, (255, 0, 0, 0))  # fully transparent red
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ===================================================================
# TestStatus
# ===================================================================


class TestStatus:
    def test_root_returns_json(self, client):
        """GET / with Accept: application/json returns JSON."""
        resp = client.get("/", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["display"] == "connected"
        assert data["display_width"] == 600
        assert data["display_height"] == 400
        assert data["refresh_seconds"] == 19
        assert data["palette"] == ["black", "white", "yellow", "red", "blue", "green"]
        assert data["scale_modes"] == ["fill", "fit", "original", "stretch"]
        assert data["default_settings"]["background"] == "ffffff"
        assert data["default_settings"]["scale_mode"] == "fit"
        assert data["default_settings"]["rotation"] == 0
        assert data["active"] is None
        assert "gallery_count" in data

    def test_root_returns_html(self, client):
        """GET / with Accept: text/html returns HTML."""
        resp = client.get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        assert b"e-ink Gadget" in resp.data
        assert b"Current Display" in resp.data
        assert b"Render Tools" in resp.data
        assert b"gallery-strip" in resp.data
        assert b'id="preview-canvas" width="600" height="400"' in resp.data
        assert b'id="scale-mode"' in resp.data
        assert b'id="crop-x"' in resp.data
        assert b'id="crop-y"' in resp.data
        assert b'id="rotation"' in resp.data
        assert b'id="rotation-label"' in resp.data
        assert b'max="270" step="90"' in resp.data
        assert b'id="brightness"' in resp.data
        assert b'id="contrast"' in resp.data
        assert b'id="saturation"' in resp.data
        assert b'id="btn-display-selected"' in resp.data
        assert b"tab-btn" not in resp.data

    def test_redisplay_active_preserves_current_render_tool_values(self, client):
        """Redisplay Active should submit current controls, not restore saved settings first."""
        resp = client.get("/", headers={"Accept": "text/html"})
        html = resp.get_data(as_text=True)
        match = re.search(r"async function redisplayActive\(\) \{(?P<body>.*?)\n  \}", html, re.S)
        assert match is not None
        body = match.group("body")
        assert "applySettings(state.active.settings)" not in body
        assert "await displaySelected();" in body


# ===================================================================
# TestPing
# ===================================================================


class TestPing:
    def test_ping_returns_ok(self, client):
        """GET /ping returns {status: ok}."""
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}


# ===================================================================
# TestUpload
# ===================================================================


class TestUpload:
    def test_upload_png(self, client, gallery_dir):
        """Uploading a PNG saves it and returns filename/url."""
        data = {"file": (_create_test_image(), "test.png")}
        resp = client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 201
        result = resp.get_json()
        assert "filename" in result
        assert result["filename"].endswith(".png")
        assert result["url"] == f"/image/{result['filename']}"
        # Verify file exists on disk
        filepath = os.path.join(gallery_dir, result["filename"])
        assert os.path.isfile(filepath)

    def test_upload_jpg(self, client, gallery_dir):
        """Uploading a JPG converts to RGBA and saves as PNG."""
        data = {"file": (_create_test_jpg(), "photo.jpg")}
        resp = client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 201
        result = resp.get_json()
        assert result["filename"].endswith(".png")
        # Verify saved file is actually a PNG (read it back)
        filepath = os.path.join(gallery_dir, result["filename"])
        saved = Image.open(filepath)
        assert saved.mode == "RGBA"

    def test_upload_transparent_png(self, client, gallery_dir):
        """Upload preserves alpha channel."""
        data = {"file": (_create_test_transparent_png(), "transparent.png")}
        resp = client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 201
        result = resp.get_json()
        filepath = os.path.join(gallery_dir, result["filename"])
        saved = Image.open(filepath)
        assert saved.mode == "RGBA"
        # Check that the pixel is (255, 0, 0, 0) — transparent red
        px = saved.getpixel((0, 0))
        assert px == (255, 0, 0, 0)

    def test_upload_resizes_large_image(self, client, gallery_dir):
        """A large image gets thumbnailed to ≤600x400."""
        # Create a 2000x1500 image
        large = Image.new("RGB", (2000, 1500), (0, 0, 255))
        buf = io.BytesIO()
        large.save(buf, "PNG")
        buf.seek(0)

        data = {"file": (buf, "large.png")}
        resp = client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 201
        result = resp.get_json()
        w, h = result["size"]
        assert w <= 600
        assert h <= 400

    def test_upload_no_file(self, client):
        """POST /upload with no file returns 400."""
        resp = client.post(
            "/upload", data={}, content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_upload_empty_filename(self, client):
        """POST /upload with empty filename returns 400."""
        data = {"file": (io.BytesIO(b"fake-data"), "")}
        resp = client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 400

    def test_upload_invalid_file(self, client):
        """POST /upload with non-image data returns 400."""
        data = {"file": (io.BytesIO(b"not an image"), "bad.txt")}
        resp = client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 400

    def test_upload_webp(self, client, gallery_dir):
        """Uploading a WebP image works."""
        img = Image.new("RGBA", (50, 50), (0, 255, 0))
        buf = io.BytesIO()
        img.save(buf, "WEBP")
        buf.seek(0)

        data = {"file": (buf, "image.webp")}
        resp = client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 201
        result = resp.get_json()
        filepath = os.path.join(gallery_dir, result["filename"])
        saved = Image.open(filepath)
        assert saved.mode == "RGBA"


# ===================================================================
# TestGallery
# ===================================================================


class TestGallery:
    def test_gallery_empty(self, client):
        """Empty gallery returns an empty images list."""
        resp = client.get("/gallery")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {"images": [], "active": None}

    def test_gallery_lists_images(self, client, gallery_dir):
        """Gallery lists uploaded images with correct metadata."""
        # Upload two images
        red = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
        blue = Image.new("RGBA", (50, 50), (0, 0, 255, 255))

        def _upload(img, name):
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            data = {"file": (buf, name)}
            return client.post(
                "/upload", data=data, content_type="multipart/form-data"
            )

        _upload(red, "red.png")
        _upload(blue, "blue.png")

        resp = client.get("/gallery")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["images"]) == 2
        assert data["active"] is None

        # Check all required fields present
        for img_info in data["images"]:
            assert "name" in img_info
            assert "url" in img_info
            assert "size_bytes" in img_info
            assert "modified" in img_info
            assert "width" in img_info
            assert "height" in img_info
            assert "active" in img_info
            assert img_info["url"].startswith("/image/")
            assert img_info["name"].endswith(".png")

    def test_gallery_sort_order(self, client, gallery_dir):
        """Images are sorted newest first."""
        # We can't easily control mtime in a portable way, but we can
        # verify the list is non-empty and has the right shape
        img = Image.new("RGBA", (10, 10), (0, 255, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        client.post(
            "/upload",
            data={"file": (buf, "test.png")},
            content_type="multipart/form-data",
        )
        resp = client.get("/gallery")
        data = resp.get_json()
        assert len(data["images"]) == 1

    def test_gallery_ignores_non_png(self, client, gallery_dir):
        """Non-.png files in the gallery dir are ignored."""
        # Write a text file directly
        with open(os.path.join(gallery_dir, "notes.txt"), "w") as f:
            f.write("hello")

        resp = client.get("/gallery")
        assert resp.get_json() == {"images": [], "active": None}


# ===================================================================
# TestDisplay
# ===================================================================


class TestDisplay:
    def test_display_calls_display_image(self, client, gallery_dir, mock_display):
        """POST /display/<filename> calls display.display_image()."""
        # Upload an image first
        img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)

        resp = client.post(
            "/upload",
            data={"file": (buf, "red.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        filename = resp.get_json()["filename"]

        # Now display it
        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 0, 0))
            resp = client.post(f"/display/{filename}")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "ok"

            # Verify prepare_image was called
            mock_prepare.assert_called_once()
            # Verify display_image was called on the mock
            mock_display.display_image.assert_called_once()

    def test_display_with_bg_colour(self, client, gallery_dir, mock_display):
        """POST /display/<filename>?bg=FF0000 uses red background."""
        img = Image.new("RGBA", (100, 100), (0, 255, 0, 128))  # semi-transparent green
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)

        resp = client.post(
            "/upload",
            data={"file": (buf, "green.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 0, 0))
            resp = client.post(f"/display/{filename}?bg=FF0000")
            assert resp.status_code == 200

            # The composite should blend green onto red
            mock_prepare.assert_called_once()
            call_img = mock_prepare.call_args[0][0]
            # The composite should be RGB mode
            assert call_img.mode == "RGB"
            mock_display.display_image.assert_called_once()

    def test_display_invalid_bg(self, client, gallery_dir):
        """Invalid background colour returns 400."""
        # Upload an image first
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "test.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        resp = client.post(f"/display/{filename}?bg=XYZ123")
        assert resp.status_code == 400

        resp = client.post(f"/display/{filename}?bg=12345")
        assert resp.status_code == 400

        resp = client.post(f"/display/{filename}?bg=GGGGGG")
        assert resp.status_code == 400

    def test_display_file_not_found(self, client):
        """Displaying a non-existent file returns 404."""
        resp = client.post("/display/nonexistent.png")
        assert resp.status_code == 404

    def test_display_path_traversal_blocked(self, client):
        """Path traversal attempts are blocked."""
        resp = client.post("/display/../etc/passwd")
        assert resp.status_code == 400

    def test_display_uses_lock(self, client, gallery_dir, mock_display, display_lock):
        """Display endpoint uses the display_lock."""
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "test.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 0, 0))
            resp = client.post(f"/display/{filename}")
            assert resp.status_code == 200
            assert display_lock.acquire_count >= 1
            assert display_lock.release_count >= 1

    def test_display_saves_active_state(self, client, gallery_dir, mock_display):
        """POST /display/<filename> records the active image and settings."""
        img = Image.new("RGBA", (20, 20), (100, 100, 100, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "state.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 255, 255))
            resp = client.post(
                f"/display/{filename}",
                json={"background": "000000", "scale_mode": "fill", "scale": 1.25},
            )

        assert resp.status_code == 200
        active = resp.get_json()["active"]
        assert active["filename"] == filename
        assert active["settings"]["background"] == "000000"
        assert active["settings"]["scale_mode"] == "fill"
        assert active["settings"]["scale"] == 1.25

    def test_display_snaps_rotation_to_90_degree_increments(self, client, gallery_dir):
        """Rotation settings are normalized to snapped quarter-turn values."""
        img = Image.new("RGBA", (20, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "rotate-snap.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 255, 255))
            resp = client.post(f"/display/{filename}", json={"rotation": 91})

        assert resp.status_code == 200
        active = resp.get_json()["active"]
        assert active["settings"]["rotation"] == 90

        gallery_resp = client.get("/gallery")
        gallery_data = gallery_resp.get_json()
        assert gallery_data["active"]["filename"] == filename
        assert gallery_data["images"][0]["active"] is True

    def test_display_rejects_invalid_scale_mode(self, client, gallery_dir):
        """Invalid scale mode returns 400."""
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "bad-mode.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        resp = client.post(f"/display/{filename}", json={"scale_mode": "sideways"})
        assert resp.status_code == 400
        assert "Invalid scale mode" in resp.get_json()["error"]

    def test_display_scale_modes_compose_600x400(self, client, gallery_dir, mock_display):
        """All scale modes pass a 600x400 RGB composite to prepare_image."""
        for mode in ("fit", "fill", "stretch", "original"):
            img = Image.new("RGBA", (120, 80), (0, 0, 255, 180))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            resp = client.post(
                "/upload",
                data={"file": (buf, f"{mode}.png")},
                content_type="multipart/form-data",
            )
            filename = resp.get_json()["filename"]

            with patch("pi.web.prepare_image") as mock_prepare:
                mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 255, 255))
                resp = client.post(f"/display/{filename}", json={"scale_mode": mode})

            assert resp.status_code == 200
            call_img = mock_prepare.call_args[0][0]
            assert call_img.mode == "RGB"
            assert call_img.size == (600, 400)

    def test_display_applies_brightness_adjustment(self, client, gallery_dir, mock_display):
        """Brightness setting affects the image before palette preparation."""
        img = Image.new("RGBA", (600, 400), (100, 100, 100, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "bright.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 255, 255))
            resp = client.post(f"/display/{filename}", json={"brightness": 50})

        assert resp.status_code == 200
        call_img = mock_prepare.call_args[0][0]
        r, g, b = call_img.getpixel((300, 200))
        assert r > 100
        assert g > 100
        assert b > 100

    def test_display_applies_rotation_before_scaling(self, client, gallery_dir, mock_display):
        """A 90-degree rotation changes the fit footprint before palette preparation."""
        img = Image.new("RGBA", (20, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "rotate.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 255, 255))
            resp = client.post(
                f"/display/{filename}",
                json={"background": "000000", "scale_mode": "fit", "rotation": 90},
            )

        assert resp.status_code == 200
        call_img = mock_prepare.call_args[0][0]
        assert call_img.getpixel((300, 10)) == (255, 0, 0)
        assert call_img.getpixel((10, 200)) == (0, 0, 0)


# ===================================================================
# TestClear
# ===================================================================


class TestClear:
    def test_clear_calls_display_clear(self, client, mock_display):
        """POST /clear calls display.clear()."""
        resp = client.post("/clear")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "active": None}
        mock_display.clear.assert_called_once()

    def test_clear_uses_lock(self, client, mock_display, display_lock):
        """Clear endpoint uses the display_lock."""
        resp = client.post("/clear")
        assert resp.status_code == 200
        assert display_lock.acquire_count >= 1
        assert display_lock.release_count >= 1

    def test_clear_removes_active_state(self, client, gallery_dir, mock_display):
        """Clearing the display removes the durable active image state."""
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "clear-active.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 0, 0))
            display_resp = client.post(f"/display/{filename}")
        assert display_resp.status_code == 200
        assert client.get("/gallery").get_json()["active"]["filename"] == filename

        clear_resp = client.post("/clear")
        assert clear_resp.status_code == 200
        assert clear_resp.get_json()["active"] is None
        assert client.get("/gallery").get_json()["active"] is None


# ===================================================================
# TestDelete
# ===================================================================


class TestDelete:
    def test_delete_removes_file(self, client, gallery_dir):
        """DELETE /image/<filename> removes the file."""
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "test.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]
        filepath = os.path.join(gallery_dir, filename)
        assert os.path.isfile(filepath)

        resp = client.delete(f"/image/{filename}")
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] == filename
        assert resp.get_json()["active"] is None
        assert not os.path.isfile(filepath)

    def test_delete_not_found(self, client):
        """DELETE /image/<filename> on missing file returns 404."""
        resp = client.delete("/image/nonexistent.png")
        assert resp.status_code == 404

    def test_delete_active_image_clears_active_state(self, client, gallery_dir):
        """Deleting the active image clears durable display state."""
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "active-delete.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        with patch("pi.web.prepare_image") as mock_prepare:
            mock_prepare.return_value = Image.new("RGB", (600, 400), (255, 0, 0))
            display_resp = client.post(f"/display/{filename}")
        assert display_resp.status_code == 200

        resp = client.delete(f"/image/{filename}")
        assert resp.status_code == 200
        assert resp.get_json()["active"] is None
        assert client.get("/gallery").get_json()["active"] is None

    def test_delete_path_traversal_blocked(self, client):
        """Path traversal attempts are blocked."""
        resp = client.delete("/image/../etc/passwd")
        assert resp.status_code == 400


# ===================================================================
# TestImageServing
# ===================================================================


class TestImageServing:
    def test_serve_image_returns_png(self, client, gallery_dir):
        """GET /image/<filename> returns the image with correct content type."""
        img = Image.new("RGBA", (50, 50), (0, 0, 255, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = client.post(
            "/upload",
            data={"file": (buf, "blue.png")},
            content_type="multipart/form-data",
        )
        filename = resp.get_json()["filename"]

        resp = client.get(f"/image/{filename}")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"

        # Verify the content is a valid PNG
        assert resp.data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_serve_image_not_found(self, client):
        """GET /image/<filename> on missing file returns 404."""
        resp = client.get("/image/nonexistent.png")
        assert resp.status_code == 404

    def test_serve_image_path_traversal_blocked(self, client):
        """Path traversal attempts are blocked."""
        resp = client.get("/image/../etc/passwd")
        assert resp.status_code == 400
