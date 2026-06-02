"""Tests for pi/eink_driver.py."""

import unittest
from unittest.mock import MagicMock, patch

import PIL.Image

from pi.eink_driver import DisplayError, EInkDisplay

# The vendored epd4in0e module has platform-detection side effects at import
# time (epdconfig.py), so we use create=True to mock it before first import.
# waveshare_epd/__init__.py is empty, so epd4in0e is not yet a package attribute.
PATCH_PATH = "pi.vendor.waveshare_epd.epd4in0e"


class TestEInkDisplayInit(unittest.TestCase):
    """Tests for construction and initialization."""

    @patch(PATCH_PATH, create=True)
    def test_constructor_creates_epd(self, mock_epd_module):
        """__init__ creates an EPD instance but does not init hardware."""
        mock_epd = MagicMock()
        mock_epd_module.EPD.return_value = mock_epd

        display = EInkDisplay()

        mock_epd_module.EPD.assert_called_once_with()
        self.assertFalse(display._initialized)
        mock_epd.init.assert_not_called()

    @patch(PATCH_PATH, create=True)
    def test_init_calls_epd_init_success(self, mock_epd_module):
        """init() calls epd.init() and sets initialized flag on success."""
        mock_epd = MagicMock()
        mock_epd.init.return_value = 0
        mock_epd_module.EPD.return_value = mock_epd

        display = EInkDisplay()
        display.init()

        mock_epd.init.assert_called_once_with()
        self.assertTrue(display._initialized)

    @patch(PATCH_PATH, create=True)
    def test_init_raises_display_error_on_non_zero(self, mock_epd_module):
        """init() raises DisplayError when epd.init() returns non-zero."""
        mock_epd = MagicMock()
        mock_epd.init.return_value = -1
        mock_epd_module.EPD.return_value = mock_epd

        display = EInkDisplay()
        with self.assertRaises(DisplayError):
            display.init()
        self.assertFalse(display._initialized)

    @patch(PATCH_PATH, create=True)
    def test_init_wraps_exceptions(self, mock_epd_module):
        """init() wraps arbitrary exceptions into DisplayError."""
        mock_epd = MagicMock()
        mock_epd.init.side_effect = OSError("SPI bus error")
        mock_epd_module.EPD.return_value = mock_epd

        display = EInkDisplay()
        with self.assertRaises(DisplayError):
            display.init()
        self.assertFalse(display._initialized)


class TestEInkDisplayGuard(unittest.TestCase):
    """Operations without init raise DisplayError."""

    @patch(PATCH_PATH, create=True)
    def test_clear_without_init_raises(self, mock_epd_module):
        """clear() raises DisplayError if init() not called."""
        mock_epd_module.EPD.return_value = MagicMock()
        display = EInkDisplay()

        with self.assertRaises(DisplayError):
            display.clear()

    @patch(PATCH_PATH, create=True)
    def test_display_image_without_init_raises(self, mock_epd_module):
        """display_image() raises DisplayError if init() not called."""
        mock_epd_module.EPD.return_value = MagicMock()
        display = EInkDisplay()

        with self.assertRaises(DisplayError):
            display.display_image(PIL.Image.new("RGB", (400, 600)))

    @patch(PATCH_PATH, create=True)
    def test_sleep_without_init_raises(self, mock_epd_module):
        """sleep() raises DisplayError if init() not called."""
        mock_epd_module.EPD.return_value = MagicMock()
        display = EInkDisplay()

        with self.assertRaises(DisplayError):
            display.sleep()


class TestEInkDisplayOperations(unittest.TestCase):
    """Tests for display operations after init."""

    def setUp(self):
        self._patcher = patch(PATCH_PATH, create=True)
        self.mock_epd_module = self._patcher.start()
        self.mock_epd = MagicMock()
        self.mock_epd.init.return_value = 0
        self.mock_epd_module.EPD.return_value = self.mock_epd

        self.display = EInkDisplay()
        self.display.init()

    def tearDown(self):
        self._patcher.stop()

    def test_clear_calls_epd_clear(self):
        """clear() calls epd.Clear() with default color."""
        self.display.clear()
        self.mock_epd.Clear.assert_called_once_with()

    def test_clear_wraps_exceptions(self):
        """clear() wraps epd.Clear() errors into DisplayError."""
        self.mock_epd.Clear.side_effect = RuntimeError("GPIO timeout")
        with self.assertRaises(DisplayError):
            self.display.clear()

    def test_display_image_calls_getbuffer_and_display(self):
        """display_image() calls getbuffer then display with the result."""
        img = PIL.Image.new("RGB", (400, 600))
        fake_buf = [0x11] * (400 * 600 // 2)
        self.mock_epd.getbuffer.return_value = fake_buf

        self.display.display_image(img)

        self.mock_epd.getbuffer.assert_called_once_with(img)
        self.mock_epd.display.assert_called_once_with(fake_buf)

    def test_display_image_wraps_getbuffer_exceptions(self):
        """display_image() wraps getbuffer errors into DisplayError."""
        img = PIL.Image.new("RGB", (400, 600))
        self.mock_epd.getbuffer.side_effect = ValueError("bad image")

        with self.assertRaises(DisplayError):
            self.display.display_image(img)

    def test_display_image_wraps_display_exceptions(self):
        """display_image() wraps display errors into DisplayError."""
        img = PIL.Image.new("RGB", (400, 600))
        self.mock_epd.getbuffer.return_value = [0x11] * 10
        self.mock_epd.display.side_effect = OSError("SPI write failed")

        with self.assertRaises(DisplayError):
            self.display.display_image(img)

    def test_sleep_calls_epd_sleep(self):
        """sleep() calls epd.sleep()."""
        self.display.sleep()
        self.mock_epd.sleep.assert_called_once_with()

    def test_sleep_wraps_exceptions(self):
        """sleep() wraps epd.sleep() errors into DisplayError."""
        self.mock_epd.sleep.side_effect = RuntimeError("power down failed")
        with self.assertRaises(DisplayError):
            self.display.sleep()


if __name__ == "__main__":
    unittest.main()
