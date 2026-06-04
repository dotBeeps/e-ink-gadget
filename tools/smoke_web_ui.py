"""Run the Flask web UI locally with mocked display hardware for browser smoke testing."""

from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pi.web import create_app

app = create_app(
    display=MagicMock(),
    display_lock=Lock(),
    gallery_dir="/tmp/eink-gadget-gallery-smoke",
)
app.run(host="127.0.0.1", port=5080, debug=False, use_reloader=False)
