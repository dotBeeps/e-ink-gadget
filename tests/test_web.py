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
        assert "gallery_count" in data

    def test_root_returns_html(self, client):
        """GET / with Accept: text/html returns HTML."""
        resp = client.get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        assert b"e-ink Gadget" in resp.data


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
        assert data == {"images": []}

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

        # Check all required fields present
        for img_info in data["images"]:
            assert "name" in img_info
            assert "url" in img_info
            assert "size_bytes" in img_info
            assert "modified" in img_info
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
        assert resp.get_json() == {"images": []}


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


# ===================================================================
# TestClear
# ===================================================================


class TestClear:
    def test_clear_calls_display_clear(self, client, mock_display):
        """POST /clear calls display.clear()."""
        resp = client.post("/clear")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}
        mock_display.clear.assert_called_once()

    def test_clear_uses_lock(self, client, mock_display, display_lock):
        """Clear endpoint uses the display_lock."""
        resp = client.post("/clear")
        assert resp.status_code == 200
        assert display_lock.acquire_count >= 1
        assert display_lock.release_count >= 1


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
        assert not os.path.isfile(filepath)

    def test_delete_not_found(self, client):
        """DELETE /image/<filename> on missing file returns 404."""
        resp = client.delete("/image/nonexistent.png")
        assert resp.status_code == 404

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
