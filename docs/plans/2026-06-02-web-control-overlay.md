# Web Control Overlay — Ethernet-over-USB + Flask UI

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add Ethernet-over-USB (g_ether in configfs) alongside existing g_serial, and run a Flask web server inside display_daemon.py serving upload, gallery, control, and background-color compositing.

**Architecture:** One `display_daemon.py` process. Main thread runs the serial loop (ttyGS0). Background thread runs Flask on 0.0.0.0:8080. One `EInkDisplay` instance shared behind `threading.Lock()`. Uploads save images with alpha preserved. Background color compositing happens at display time.

**Tech Stack:** Python 3, Flask, configfs (RNDIS/ECM), PIL/Pillow for transparency compositing + dithering.

**Key invariant:** POST /upload preserves original alpha transparency. GET /display/<name>?bg=RRGGBB composites the chosen background before sending to the display. Stored images remain transparent.

---

### Phase A: Gadget configfs rewrite (serial + ethernet)

#### Task A1: Rewrite `gadget-setup.sh` for configfs with serial + ethernet dual functions

**Objective:** Replace the kernel-module approach (`g_serial`) with configfs creating both `acm.usb1` (serial) and `ecm.usb0` (ethernet) functions.

**Files:**
- Modify: `pi/setup/gadget-setup.sh`

**Step 1: Rewrite the setup script**

Replace the entire script with this configfs version:

```bash
#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# gadget-setup.sh — RPi 4 USB Gadget configuration (configfs)
#
# Configures both SERIAL (g_serial equivalent) and ETHERNET (g_ether)
# over the same USB cable via configfs. Idempotent — safe to run multiple
# times.
# ---------------------------------------------------------------------------
set -euo pipefail

# ── helpers ────────────────────────────────────────────────────────────────
info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()    { printf '\033[1;32m[ OK ]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
err()   { printf '\033[1;31m[ERR]\033[0m  %s\n' "$*" >&2; }

# ── configfs setup ─────────────────────────────────────────────────────────
info "Configuring USB gadget via configfs..."

# Enable dwc2 + SPI
if [[ -f /boot/firmware/config.txt ]]; then
    CONFIG_FILE=/boot/firmware/config.txt
elif [[ -f /boot/config.txt ]]; then
    CONFIG_FILE=/boot/config.txt
else
    err "Could not find /boot/firmware/config.txt or /boot/config.txt"
    exit 1
fi

for line in 'dtoverlay=dwc2' 'dtparam=spi=on'; do
    grep -qx "$line" "$CONFIG_FILE" 2>/dev/null || echo "$line" >> "$CONFIG_FILE"
done
ok "config.txt overlays ensured"

# Load modules
for mod in dwc2 libcomposite; do
    modprobe "$mod" 2>/dev/null || true
done
ok "dwc2 + libcomposite loaded"

# ── Tear down existing gadget if present ───────────────────────────────────
GADGET_PATH=/sys/kernel/config/usb_gadget/eink
if [[ -d $GADGET_PATH ]]; then
    # Unbind if bound
    if [[ -f $GADGET_PATH/UDC ]] && [[ -s $GADGET_PATH/UDC ]]; then
        info "Tearing down existing gadget..."
        echo "" > "$GADGET_PATH/UDC" 2>/dev/null || true
    fi
    # Remove symlinked configs, functions, strings
    for cfg in "$GADGET_PATH"/configs/c.*; do
        [[ -d "$cfg" ]] && find "$cfg" -maxdepth 1 -lname '*' -delete 2>/dev/null || true
    done
    rm -rf "$GADGET_PATH"/configs/c.* 2>/dev/null || true
    rm -rf "$GADGET_PATH"/functions/* 2>/dev/null || true
    rm -rf "$GADGET_PATH" 2>/dev/null || true
    ok "Previous gadget torn down"
fi

# ── Create gadget structure ───────────────────────────────────────────────
mkdir -p "$GADGET_PATH"
cd "$GADGET_PATH"

# Vendor/product IDs (standard Linux gadget)
echo 0x1d6b > idVendor  # Linux Foundation
echo 0x0104 > idProduct # Multifunction Composite Gadget

# Strings (serial number etc.)
mkdir -p strings/0x409
echo "eink-gadget-serial" > strings/0x409/serialnumber
echo "Obryn" > strings/0x409/manufacturer
echo "E-Ink Gadget Display" > strings/0x409/product

# ── Function 1: ACM serial (replaces g_serial) ────────────────────────────
mkdir -p functions/acm.usb1
ok "Serial function (acm.usb1) created"

# ── Function 2: ECM ethernet ──────────────────────────────────────────────
mkdir -p functions/ecm.usb0
# MAC addresses for the ethernet link
echo "00:dd:dc:eb:6d:a1" > functions/ecm.usb0/host_addr
echo "00:dd:dc:eb:6d:a2" > functions/ecm.usb0/dev_addr
ok "Ethernet function (ecm.usb0) created"

# ── Config (bind both functions) ──────────────────────────────────────────
mkdir -p configs/c.1
mkdir -p configs/c.1/strings/0x409
echo "Conf 1: Serial + Ethernet" > configs/c.1/strings/0x409/configuration

# Bind functions into config
ln -s functions/acm.usb1 configs/c.1/
ln -s functions/ecm.usb0 configs/c.1/
ok "Functions bound to config"

# ── Enable gadget ─────────────────────────────────────────────────────────
# Find the UDC (USB Device Controller)
UDC=$(ls /sys/class/udc/ | head -1)
if [[ -z "$UDC" ]]; then
    err "No UDC found — ensure dwc2 overlay is active and rebooted"
    exit 1
fi
echo "$UDC" > UDC
ok "Gadget enabled (UDC: $UDC)"

# ── Serial symlink ────────────────────────────────────────────────────────
UDEV_RULES_FILE=/etc/udev/rules.d/99-eink-gadget.rules
UDEV_RULE='SUBSYSTEM=="tty", KERNEL=="ttyGS[0-9]*", SYMLINK+="eink-gadget"'
if [[ ! -f "$UDEV_RULES_FILE" ]] || ! grep -q 'ttyGS' "$UDEV_RULES_FILE" 2>/dev/null; then
    echo "$UDEV_RULE" > "$UDEV_RULES_FILE"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
    ok "Udev rule created"
else
    ok "Udev rule already present"
fi

# ── Configure usb0 network ────────────────────────────────────────────────
info "Configuring usb0 interface (192.168.7.2)..."
sleep 1  # wait for interface to appear
if ip addr show usb0 &>/dev/null; then
    ip addr add 192.168.7.2/24 dev usb0 2>/dev/null || true
    ip link set usb0 up 2>/dev/null || true
    ok "usb0 configured at 192.168.7.2"
else
    warn "usb0 not yet available — will be configured on boot via systemd-networkd"
fi

# ── Apt/pip packages ──────────────────────────────────────────────────────
APT_PACKAGES=(python3-pip python3-pil python3-serial python3-flask)

info "Installing apt packages: ${APT_PACKAGES[*]}"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${APT_PACKAGES[@]}"
ok "Apt packages installed"

PIP_PACKAGES=(spidev gpiozero)

info "Installing pip packages: ${PIP_PACKAGES[*]}"
if python3 -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    pip3 install --break-system-packages "${PIP_PACKAGES[@]}"
else
    pip3 install "${PIP_PACKAGES[@]}"
fi
ok "Pip packages installed"

# ── Create gallery directory ──────────────────────────────────────────────
GALLERY_DIR=/home/pi/eink-gadget/gallery
mkdir -p "$GALLERY_DIR"
chown pi:pi "$GALLERY_DIR" 2>/dev/null || true
ok "Gallery directory: $GALLERY_DIR"

cat <<'DONE'

  ┌──────────────────────────────────────────────────────────┐
  │                    Setup Complete                         │
  ├──────────────────────────────────────────────────────────┤
  │                                                          │
  │  Next steps (requires reboot):                           │
  │                                                          │
  │    1. sudo reboot                                        │
  │                                                          │
  │    After reboot, verify:                                 │
  │      ls -l /dev/eink-gadget   # serial                   │
  │      ip addr show usb0        # ethernet (192.168.7.2)   │
  │                                                          │
  │    2. Deploy the software:                               │
  │      make deploy-rpi                                     │
  │                                                          │
  │    3. Install & start the daemon:                        │
  │      make install-service                                │
  │                                                          │
  │    4. Access the web UI:                                 │
  │      curl http://192.168.7.2:8080                        │
  │                                                          │
  └──────────────────────────────────────────────────────────┘

DONE
```

**Verification:** On the Pi, run `sudo bash pi/setup/gadget-setup.sh`. Expect: `dwc2` + `libcomposite` loaded, configfs gadget created, `usb0` at `192.168.7.2`, `/dev/eink-gadget` symlink.

**Step 2: Commit**
```bash
git add pi/setup/gadget-setup.sh
git commit -m "feat: rewrite gadget setup for configfs serial+ethernet dual function"
```

---

### Phase B: Web server foundation

#### Task B1: Add Flask background thread to `display_daemon.py`

**Objective:** Start Flask in a background thread alongside the serial loop, sharing one `EInkDisplay` protected by `threading.Lock()`.

**Files:**
- Modify: `pi/display_daemon.py`

**Add these imports and the web module import at the top:**

```python
import threading
```

**Modify `run_daemon()` to accept and share the display instance:**

The daemon will create the display once, lock it, then spawn Flask before starting the serial loop.

**Step 1: Add Flask to `run_daemon`**

Replace the `run_daemon` function with:

```python
def run_daemon(
    serial_device: str = "/dev/ttyGS0",
    serial_baud: int = 921600,
    gallery_dir: str = "/home/pi/eink-gadget/gallery",
) -> None:
    """Run the display daemon: serial reader + Flask web server."""
    import serial  # noqa: PLC0415
    from pi.web import create_app  # noqa: PLC0415

    display = EInkDisplay()
    display.init()
    display.clear()
    logger.info("Display initialised and cleared")

    display_lock = threading.Lock()

    # Start Flask in background thread
    app = create_app(
        display=display,
        display_lock=display_lock,
        gallery_dir=gallery_dir,
    )

    def flask_thread_func() -> None:
        app.run(host="0.0.0.0", port=8080, threaded=True, debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=flask_thread_func, daemon=True)
    flask_thread.start()
    logger.info("Flask web server started on 0.0.0.0:8080")

    parser = FrameParser()

    with serial.Serial(serial_device, serial_baud, timeout=0.1) as ser:
        logger.info("Serial port %s opened at %d baud", serial_device, serial_baud)

        while True:
            try:
                data = ser.read(4096)
            except Exception:
                logger.exception("Serial read error")
                continue

            if not data:
                continue

            try:
                frames = parser.feed(data)
            except Exception:
                logger.exception("Frame parsing error; resetting parser")
                parser = FrameParser()
                continue

            for frame in frames:
                response = _process_frame_with_lock(frame, display, display_lock)

                # If we just processed a SLEEP command, re-init before next frame
                if frame.cmd == CMD_SLEEP:
                    with display_lock:
                        try:
                            display.init()
                            logger.info("Display re-initialised after sleep")
                        except DisplayError:
                            logger.exception("Display re-init after sleep failed")
                            response = Frame.error(ERROR_DISPLAY)

                try:
                    ser.write(response)
                except Exception:
                    logger.exception("Serial write error")
```

**Note:** `_process_frame_with_lock` wraps `process_frame` with the lock. Add it:

```python
def _process_frame_with_lock(
    frame: Frame, display: EInkDisplay, lock: threading.Lock
) -> bytes:
    """Process a frame while holding the display lock."""
    with lock:
        return process_frame(frame, display)
```

**Verification:** Start the daemon. Observe: `Flask web server started on 0.0.0.0:8080` log line, and `curl http://localhost:8080` returns `{"status": "ok", "display": "eink-4.0-spectra-6", "gallery_count": 0}`.

**Step 2: Commit**
```bash
git add pi/display_daemon.py
git commit -m "feat: add Flask background thread to display_daemon"
```

---

### Phase C: Web API endpoints

#### Task C1: Create `pi/web.py` — Flask app with upload, gallery, display, and control endpoints

**Objective:** Create the Flask application with all API endpoints. Handles:
- `GET /` — Status JSON
- `POST /upload` — Upload image (PNG/JPG), preserve alpha, store original
- `GET /gallery` — List stored images (JSON or HTML)
- `POST /display/<name>` — Display a gallery image with optional bg color
- `POST /clear` — Clear display
- `GET /ping` — Ping display
- `GET /image/<name>` — Serve the stored image file directly

**Files:**
- Create: `pi/web.py`
- Test: `tests/test_web.py`

**Step 1: Write failing tests for the web endpoints**

```python
"""Tests for pi.web Flask application."""
import io
import os
import threading
import uuid

import pytest
from PIL import Image
from unittest.mock import MagicMock, patch

from pi.web import create_app


@pytest.fixture
def gallery_dir(tmp_path):
    d = tmp_path / "gallery"
    d.mkdir()
    return str(d)


@pytest.fixture
def display_lock():
    return threading.Lock()


@pytest.fixture
def mock_display():
    d = MagicMock()
    d.init = MagicMock()
    d.clear = MagicMock()
    d.display_image = MagicMock()
    return d


@pytest.fixture
def app(mock_display, display_lock, gallery_dir):
    return create_app(
        display=mock_display,
        display_lock=display_lock,
        gallery_dir=gallery_dir,
    )


@pytest.fixture
def client(app):
    return app.test_client()


class TestStatus:
    def test_root_returns_status_json(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "display" in data
        assert "gallery_count" in data


class TestPing:
    def test_ping_returns_ok(self, client):
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


class TestClear:
    def test_clear_calls_display(self, client, mock_display):
        resp = client.post("/clear")
        assert resp.status_code == 200
        mock_display.clear.assert_called_once()


class TestUpload:
    def test_upload_png_preserves_alpha(self, client, gallery_dir):
        # Create a 2x2 PNG with alpha transparency
        img = Image.new("RGBA", (2, 2), (255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        resp = client.post(
            "/upload",
            data={"file": (buf, "test.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        name = data["name"]

        # Verify stored file has alpha
        stored = Image.open(os.path.join(gallery_dir, name))
        assert stored.mode == "RGBA"
        assert stored.size == (600, 400)

    def test_upload_jpg_converts_to_rgba(self, client, gallery_dir):
        # Create a JPEG (no alpha support)
        img = Image.new("RGB", (100, 100), (0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        resp = client.post(
            "/upload",
            data={"file": (buf, "test.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        name = data["name"]

        stored = Image.open(os.path.join(gallery_dir, name))
        assert stored.mode == "RGBA"  # Converted to RGBA for uniformity
        assert stored.size == (600, 400)

    def test_upload_no_file_returns_400(self, client):
        resp = client.post("/upload")
        assert resp.status_code == 400


class TestGallery:
    def test_gallery_lists_images(self, client, gallery_dir):
        # Create a test image
        img = Image.new("RGBA", (600, 400), (255, 255, 255, 255))
        img.save(os.path.join(gallery_dir, "hello.png"))

        resp = client.get("/gallery")
        data = resp.get_json()
        assert resp.status_code == 200
        assert len(data["images"]) >= 1
        names = [i["name"] for i in data["images"]]
        assert "hello.png" in names

    def test_gallery_empty(self, client):
        resp = client.get("/gallery")
        assert resp.status_code == 200
        assert resp.get_json()["images"] == []


class TestDisplay:
    def test_display_with_bg_color(self, client, gallery_dir, mock_display):
        img = Image.new("RGBA", (600, 400), (255, 0, 0, 128))
        img.save(os.path.join(gallery_dir, "test.png"))

        resp = client.post("/display/test.png?bg=ffffff")
        assert resp.status_code == 200
        # The display should have been called with a composited image
        mock_display.display_image.assert_called_once()

    def test_display_without_bg_color(self, client, gallery_dir, mock_display):
        img = Image.new("RGBA", (600, 400), (255, 0, 0, 128))
        img.save(os.path.join(gallery_dir, "test2.png"))

        resp = client.post("/display/test2.png")
        assert resp.status_code == 200
        mock_display.display_image.assert_called()

    def test_display_nonexistent_returns_404(self, client):
        resp = client.post("/display/nonexistent.png")
        assert resp.status_code == 404

    def test_display_invalid_bg_returns_400(self, client, gallery_dir):
        img = Image.new("RGBA", (600, 400), (255, 0, 0, 128))
        img.save(os.path.join(gallery_dir, "bg-test.png"))

        resp = client.post("/display/bg-test.png?bg=ZZZZZZ")
        assert resp.status_code == 400


class TestDelete:
    def test_delete_image(self, client, gallery_dir):
        path = os.path.join(gallery_dir, "todelete.png")
        img = Image.new("RGBA", (600, 400))
        img.save(path)
        assert os.path.exists(path)

        resp = client.delete("/image/todelete.png")
        assert resp.status_code == 200
        assert not os.path.exists(path)

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/image/ghost.png")
        assert resp.status_code == 404


class TestImageServing:
    def test_serve_image(self, client, gallery_dir):
        img = Image.new("RGBA", (600, 400), (0, 0, 0, 255))
        img.save(os.path.join(gallery_dir, "serve-me.png"))

        resp = client.get("/image/serve-me.png")
        assert resp.status_code == 200
        assert resp.content_type == "image/png"
```

Run tests: `pytest tests/test_web.py -v` — expect all FAIL (module doesn't exist yet).

**Step 2: Create `pi/web.py`**

```python
"""Flask web server for the e-ink gadget control interface.

Runs in a background thread inside display_daemon.py.
Provides: upload, gallery, display (with bg compositing), clear, ping, delete.
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any

from flask import Flask, jsonify, request, send_file
from PIL import Image

from pi.renderer import prepare_image

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
MAX_UPLOAD_MB = 16
VALID_HEX_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")


def create_app(
    *,
    display: Any,
    display_lock: Any,
    gallery_dir: str = "/home/pi/eink-gadget/gallery",
) -> Flask:
    """Create the Flask application with all routes."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    os.makedirs(gallery_dir, exist_ok=True)

    @app.route("/")
    def status() -> Any:
        try:
            count = len([
                f for f in os.listdir(gallery_dir)
                if f.lower().endswith(tuple(s + "." for s in ("png",)))
                # just all files really
            ])
        except OSError:
            count = 0
        return jsonify({
            "status": "ok",
            "display": "eink-4.0-spectra-6",
            "gallery_count": count,
        })

    @app.route("/ping")
    def ping() -> Any:
        return jsonify({"status": "ok"})

    @app.route("/clear", methods=["POST"])
    def clear_display() -> Any:
        with display_lock:
            display.clear()
        return jsonify({"status": "ok", "action": "clear"})

    @app.route("/upload", methods=["POST"])
    def upload() -> Any:
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({"error": f"Unsupported format: {ext}"}), 400

        try:
            img = Image.open(file.stream)
        except Exception:
            return jsonify({"error": "Could not decode image"}), 400

        # Convert to RGBA if not already
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Resize to fit within 600x400, preserving aspect ratio
        img.thumbnail((600, 400), Image.LANCZOS)

        # Generate a safe unique name
        safe_name = f"{uuid.uuid4().hex[:12]}.{ext}" if ext != "png" else f"{uuid.uuid4().hex[:12]}.png"
        if ext not in ("png",):
            # Always store as PNG to support alpha
            safe_name = f"{uuid.uuid4().hex[:12]}.png"

        path = os.path.join(gallery_dir, safe_name)
        img.save(path, "PNG")

        return jsonify({"status": "ok", "name": safe_name, "size": img.size})

    @app.route("/gallery")
    def gallery() -> Any:
        try:
            files = sorted(
                os.listdir(gallery_dir),
                key=lambda f: os.path.getmtime(os.path.join(gallery_dir, f)),
                reverse=True,
            )
            images = []
            for f in files:
                fp = os.path.join(gallery_dir, f)
                stat = os.stat(fp)
                images.append({
                    "name": f,
                    "url": f"/image/{f}",
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                })
            return jsonify({"images": images})
        except OSError:
            return jsonify({"images": []})

    @app.route("/image/<path:filename>")
    def serve_image(filename: str) -> Any:
        path = os.path.join(gallery_dir, filename)
        if not os.path.isfile(path):
            return jsonify({"error": "Not found"}), 404
        return send_file(path, mimetype="image/png")

    @app.route("/display/<path:filename>", methods=["POST"])
    def display_image(filename: str) -> Any:
        path = os.path.join(gallery_dir, filename)
        if not os.path.isfile(path):
            return jsonify({"error": "Not found"}), 404

        bg_color = request.args.get("bg")
        if bg_color and not VALID_HEX_COLOR.match(bg_color):
            return jsonify({"error": "Invalid bg color (need 6 hex digits)"}), 400

        try:
            img = Image.open(path).convert("RGBA")
        except Exception:
            return jsonify({"error": "Could not decode image"}), 400

        # Composite transparent areas onto the chosen background
        if bg_color:
            bg_r = int(bg_color[0:2], 16)
            bg_g = int(bg_color[2:4], 16)
            bg_b = int(bg_color[4:6], 16)
            bg = Image.new("RGB", img.size, (bg_r, bg_g, bg_b))
            # Paste using alpha mask
            bg.paste(img, mask=img.split()[3])
            composite = bg.convert("RGB")
        else:
            # No bg specified — fill alpha areas with white
            composite = Image.new("RGB", img.size, (255, 255, 255))
            composite.paste(img, mask=img.split()[3])

        prepared = prepare_image(composite)

        with display_lock:
            display.display_image(prepared)

        return jsonify({"status": "ok", "displayed": filename, "bg": bg_color})

    @app.route("/image/<path:filename>", methods=["DELETE"])
    def delete_image(filename: str) -> Any:
        path = os.path.join(gallery_dir, filename)
        if not os.path.isfile(path):
            return jsonify({"error": "Not found"}), 404

        os.remove(path)
        return jsonify({"status": "ok", "deleted": filename})

    return app
```

Wait, I need to clean up the upload logic — the safe name generation is messy. Let me fix:

```python
    @app.route("/upload", methods=["POST"])
    def upload() -> Any:
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        # Always store as PNG (supports alpha)
        try:
            img = Image.open(file.stream)
        except Exception:
            return jsonify({"error": "Could not decode image"}), 400

        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Resize to fit within 600x400, preserving aspect ratio
        img.thumbnail((600, 400), Image.LANCZOS)

        safe_name = f"{uuid.uuid4().hex[:12]}.png"
        path = os.path.join(gallery_dir, safe_name)
        img.save(path, "PNG")

        return jsonify({"status": "ok", "name": safe_name, "size": img.size})
```

That's cleaner. All uploads become RGBA PNGs, resized to fit 600x400.

**Step 3: Run tests**

`pytest tests/test_web.py -v` — expect all PASS.

**Step 4: Commit**
```bash
git add pi/web.py tests/test_web.py
git commit -m "feat: add Flask web API with upload/gallery/display/control/delete"
```

---

### Phase D: Web UI

#### Task D1: Create HTML interface served by Flask

**Objective:** Add a single-page HTML UI that serves as the control interface. Serves from `/` when the request is `text/html`, or serves JSON for API calls. Upload tab, gallery tab, controls tab.

**Files:**
- Modify: `pi/web.py` (add static/template serving)
- Create: `pi/web/templates/index.html`

**Step 1: Create the template**

Create `pi/web/templates/index.html` — I'll write the complete HTML with inline CSS/JS for simplicity (no build step needed on Pi). The page has:
- Tab navigation: Upload | Gallery | Controls
- Upload: file input, color picker, upload button, preview showing 6-color palette approximation
- Gallery: grid of thumbnails with Display/Delete buttons, bg color picker at top applying to all displays
- Controls: Clear button, Ping button, status indicator

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>E-Ink Gadget</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#1a1a1a;color:#e0e0e0;max-width:640px;margin:0 auto;padding:16px}
h1{text-align:center;margin-bottom:16px;font-size:1.4rem;color:#fff}
h2{font-size:1.1rem;margin-bottom:8px;color:#ccc}
.tabs{display:flex;gap:4px;margin-bottom:16px}
.tab{flex:1;padding:10px;text-align:center;background:#333;border:none;color:#aaa;cursor:pointer;font-size:.9rem;border-radius:6px 6px 0 0}
.tab.active{background:#555;color:#fff}
.tab-content{display:none;background:#2a2a2a;padding:20px;border-radius:0 0 8px 8px}
.tab-content.active{display:block}
input[type=file]{width:100%;padding:8px;background:#333;color:#e0e0e0;border:1px solid #444;border-radius:4px;margin-bottom:10px}
input[type=color]{width:60px;height:36px;border:none;border-radius:4px;cursor:pointer;vertical-align:middle}
button{background:#4a9eff;color:#fff;border:none;padding:10px 16px;border-radius:6px;cursor:pointer;font-size:.9rem;margin:4px}
button:hover{background:#3d8be6}
button.danger{background:#c0392b}
button.danger:hover{background:#a93226}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.gallery-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-top:12px}
.gallery-item{background:#333;border-radius:6px;overflow:hidden;padding:6px;text-align:center}
.gallery-item img{width:100%;height:auto;border-radius:4px;background:#555}
.gallery-item .name{font-size:.7rem;color:#888;margin:4px 0;word-break:break-all}
.bg-control{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.bg-control label{font-size:.85rem;color:#aaa}
#status{display:inline-block;padding:6px 12px;border-radius:6px;font-size:.85rem;margin-bottom:12px}
#status.ok{background:#1a6b1a;color:#8f8}
#status.err{background:#6b1a1a;color:#f88}
.preview{width:120px;height:80px;border:1px solid #444;border-radius:4px;margin:10px 0;background:#555}
label.small{font-size:.8rem;color:#aaa}
</style>
</head>
<body>
<h1>🖥 E-Ink Gadget</h1>
<div class="tabs">
  <button class="tab active" data-tab="upload">Upload</button>
  <button class="tab" data-tab="gallery">Gallery</button>
  <button class="tab" data-tab="control">Control</button>
</div>

<div id="tab-upload" class="tab-content active">
  <h2>Upload Image</h2>
  <div class="bg-control">
    <label>Background color:</label>
    <input type="color" id="upload-bg" value="#ffffff">
  </div>
  <input type="file" id="upload-file" accept="image/*">
  <div class="btn-row">
    <button onclick="uploadImage()">Upload</button>
    <button onclick="uploadAndDisplay()">Upload & Display</button>
  </div>
  <canvas class="preview" id="preview"></canvas>
  <div id="upload-status" class="small"></div>
</div>

<div id="tab-gallery" class="tab-content">
  <h2>Gallery</h2>
  <div class="bg-control">
    <label>Display with background:</label>
    <input type="color" id="gallery-bg" value="#ffffff">
    <button onclick="loadGallery()">Refresh</button>
  </div>
  <div class="gallery-grid" id="gallery-grid"></div>
</div>

<div id="tab-control" class="tab-content">
  <h2>Control</h2>
  <div id="status" class="ok">Checking...</div>
  <div class="btn-row">
    <button onclick="api('POST','/clear')">Clear Display</button>
    <button onclick="api('GET','/ping')">Ping</button>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

// Tabs
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('tab-' + t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'gallery') loadGallery();
    if (t.dataset.tab === 'control') checkStatus();
  });
});

async function api(method, url, data) {
  const opts = {method, headers:{}};
  if (data && !(data instanceof FormData)) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(data); }
  else if (data instanceof FormData) { opts.body = data; }
  const resp = await fetch(url, opts);
  const json = await resp.json();
  if (!resp.ok) throw new Error(json.error || 'Request failed');
  return json;
}

function hexColor(el) { return el.value.slice(1); }

// Upload
$('upload-file').addEventListener('change', e => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    const img = new Image();
    img.onload = () => {
      const c = $('preview');
      c.width = 120; c.height = 80;
      const ctx = c.getContext('2d');
      ctx.fillStyle = hexColor($('upload-bg'));
      ctx.fillRect(0,0,120,80);
      const scale = Math.min(120/img.width, 80/img.height);
      const w = img.width*scale, h = img.height*scale;
      ctx.drawImage(img, (120-w)/2, (80-h)/2, w, h);
    };
    img.src = ev.target.result;
  };
  reader.readAsDataURL(file);
});

async function uploadImage() {
  const file = $('upload-file').files[0];
  if (!file) return alert('Select a file first');
  try {
    const fd = new FormData();
    fd.append('file', file);
    const result = await api('POST', '/upload', fd);
    $('upload-status').textContent = 'Uploaded: ' + result.name;
    loadGallery();
  } catch(e) { alert(e.message); }
}

async function uploadAndDisplay() {
  const file = $('upload-file').files[0];
  if (!file) return alert('Select a file first');
  try {
    const fd = new FormData();
    fd.append('file', file);
    const result = await api('POST', '/upload', fd);
    const bg = hexColor($('upload-bg'));
    await api('POST', '/display/' + result.name + '?bg=' + bg);
    $('upload-status').textContent = 'Uploaded & displayed: ' + result.name;
    loadGallery();
  } catch(e) { alert(e.message); }
}

// Gallery
async function loadGallery() {
  try {
    const data = await api('GET', '/gallery');
    const grid = $('gallery-grid');
    grid.innerHTML = '';
    if (data.images.length === 0) {
      grid.innerHTML = '<p style="color:#666;grid-column:1/-1;text-align:center">No images yet</p>';
      return;
    }
    for (const img of data.images) {
      const div = document.createElement('div');
      div.className = 'gallery-item';
      div.innerHTML = `
        <img src="${img.url}" alt="${img.name}">
        <div class="name">${img.name}</div>
        <div class="btn-row" style="justify-content:center">
          <button class="small-btn" onclick="displayImage('${img.name}')">Display</button>
          <button class="small-btn danger" onclick="deleteImage('${img.name}')">Delete</button>
        </div>
      `;
      grid.appendChild(div);
    }
  } catch(e) { console.error(e); }
}

async function displayImage(name) {
  const bg = hexColor($('gallery-bg'));
  try {
    await api('POST', '/display/' + name + '?bg=' + bg);
  } catch(e) { alert(e.message); }
}

async function deleteImage(name) {
  if (!confirm('Delete ' + name + '?')) return;
  try {
    await api('DELETE', '/image/' + name);
    loadGallery();
  } catch(e) { alert(e.message); }
}

// Status
async function checkStatus() {
  try {
    const data = await api('GET', '/');
    const s = $('status');
    s.className = 'ok';
    s.textContent = `OK — ${data.gallery_count} images in gallery`;
  } catch(e) {
    const s = $('status');
    s.className = 'err';
    s.textContent = 'Error: ' + e.message;
  }
}
checkStatus();
</script>
</body>
</html>
```

**Step 2: Modify `pi/web.py` to serve the HTML template**

Add to the `create_app` function (after the `app` creation):

```python
    @app.route("/")
    def status_or_ui() -> Any:
        accept = request.headers.get("Accept", "")
        if "text/html" in accept or request.args.get("ui"):
            from flask import render_template  # noqa: PLC0415
            return render_template("index.html")
        # API response (JSON)
        try:
            count = len(os.listdir(gallery_dir))
        except OSError:
            count = 0
        return jsonify({
            "status": "ok",
            "display": "eink-4.0-spectra-6",
            "gallery_count": count,
        })
```

Replace the old `@app.route("/")` with this one.

Also need to add the templates directory. Flask looks in `templates/` relative to the app root, but since `pi/web.py` is not at the project root, we need to configure it:

```python
import os
template_dir = os.path.join(os.path.dirname(__file__), "web", "templates")
app = Flask(__name__, template_folder=template_dir)
```

**Verification:** Start daemon, visit `http://192.168.7.2:8080` from a browser on a machine connected via USB ethernet. See the UI with Upload/Gallery/Control tabs.

**Step 3: Commit**
```bash
git add pi/web.py pi/web/templates/index.html
git commit -m "feat: add single-page HTML UI for upload/gallery/control"
```

---

### Phase E: Integration and verification

#### Task E1: Verify existing tests still pass

**Objective:** Ensure the serial protocol and existing functionality are unchanged.

Run:
```bash
make test
```

Expected: 80 tests pass (28 protocol + 25 renderer + 14 driver + 13 daemon).

#### Task E2: Add new web tests to test suite

**Objective:** Ensure `make test` includes the new web tests.

Add `tests/test_web.py` to whatever discovery pattern `make test` uses. If using pytest with auto-discovery (`pytest tests/`), it should work already.

Run: `make test`

Expected: ~108 tests pass (80 existing + ~28 web tests).

**Step: Commit**
```bash
git commit --allow-empty -m "chore: verify all tests pass after web overlay addition"
```

---

### Phase F: Documentation updates

#### Task F1: Update `PROJECT.md`

**Objective:** Document the new architecture with the ethernet overlay.

Update the Architecture diagram, Files list, Invariants, Decisions table, and Next Step.

```markdown
## Architecture

```
Host Computer                    RPi 4 (USB gadget)                    E-Ink Display
┌──────────────┐    USB-C     ┌─────────────────────────┐    SPI    ┌──────────────┐
│ eink-send    │────────────►│ display_daemon.py       │──────────►│ Spectra 6 E6 │
│ (CLI tool)   │  /dev/ttyACM0│ ├─ serial loop (ttyGS0) │  GPIO     │  600×400     │
│              │◄────────────│ │   (binary protocol)   │           │  6-color     │
└──────────────┘              │ │                         │           └──────────────┘
                              │ ├─ Flask HTTP (:8080)    │
┌──────────────┐    USB-C     │ │   /upload (preserves   │
│ web browser  │────────────►│ │   alpha, resizes)      │
│ (any OS)     │  RNDIS ECM  │ │   /gallery (JSON/HTML) │
│              │  usb0       │ │   /display/<name>?bg=   │
│              │  192.168.7.2│ │   /clear /ping /delete  │
└──────────────┘              │ └─ display driver (lock) │
                              └─────────────────────────┘
```

## Files

[Add `pi/web.py`, `pi/web/templates/index.html`, `tests/test_web.py` to the tree]

## Invariants

[Add:]
- Gallery images stored as RGBA PNGs at max 600×400 with transparency preserved
- Background color compositing happens at display time, not upload time
- Display access is serialized via threading.Lock() (serial + web share one display)
- Gadget uses configfs with dual functions: acm.usb1 (serial) + ecm.usb0 (ethernet)
- Gadget IP: 192.168.7.2, accessible via http://192.168.7.2:8080
- Gallery stored at /home/pi/eink-gadget/gallery/

## Decisions

| Decision | Date | Status |
|---|---|---|
| g_serial over g_ether | 2025-06-02 | **Superseded** → configfs dual (serial + ethernet) |
| configfs dual function (serial + ethernet) | 2026-06-02 | Settled |
| Flask web UI in same process | 2026-06-02 | Settled |
| Alpha preserved at upload, bg at display time | 2026-06-02 | Settled |
| Gallery storage: /home/pi/eink-gadget/gallery/ | 2026-06-02 | Settled |
```

**Step: Commit**
```bash
git add PROJECT.md
git commit -m "docs: update PROJECT.md for web control overlay"
```

---

### Acceptance Checklist

- [ ] Gadget setup creates both ttyGS0 (serial) and usb0 (ethernet) simultaneously
- [ ] Host sees both /dev/ttyACM0 and a RNDIS network adapter
- [ ] Pi is reachable at 192.168.7.2
- [ ] `curl http://192.168.7.2:8080/` returns JSON status
- [ ] `curl http://192.168.7.2:8080/` with Accept: text/html returns the UI
- [ ] `POST /upload` accepts PNG/JPG, stores as RGBA PNG preserving alpha
- [ ] `POST /display/<name>?bg=ff0000` composites red bg and displays
- [ ] `POST /display/<name>` (no bg) defaults to white background
- [ ] `GET /gallery` returns list of stored images
- [ ] `DELETE /image/<name>` removes image from gallery
- [ ] Serial protocol (eink-send) continues to work unchanged
- [ ] All tests pass (80 existing + ~28 new web tests)
- [ ] Threading lock prevents concurrent display access
- [ ] UI works in browser: upload, gallery, control tabs functional
