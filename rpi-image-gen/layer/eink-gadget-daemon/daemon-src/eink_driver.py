"""Thin wrapper around the vendored Waveshare EPD driver.

Provides a mockable interface with error handling for the
Waveshare 4.0" Spectra 6 e-ink display (epd4in0e).
"""

# Import is deferred to __init__ so the vendored module's platform-detection
# side effects (epdconfig) don't fire at import time.


class DisplayError(Exception):
    """Raised when a display operation fails."""


class EInkDisplay:
    """Wrapper around epd4in0e.EPD with initialization-gated operations.

    Usage:
        display = EInkDisplay()
        display.init()
        display.clear()
        display.display_image(my_pil_image)
        display.sleep()
    """

    def __init__(self) -> None:
        epd4in0e = self._import_epd()
        self._epd = epd4in0e.EPD()
        self._initialized = False

    @staticmethod
    def _import_epd():
        """Lazy-import the vendored driver module."""
        from pi.vendor.waveshare_epd import epd4in0e  # noqa: PLC0415

        return epd4in0e

    def init(self) -> None:
        """Initialize the display hardware.

        Raises DisplayError if the underlying hardware init fails.
        """
        try:
            result = self._epd.init()
            if result != 0:
                raise DisplayError(
                    f"EPD init returned non-zero: {result}"
                )
            self._initialized = True
        except DisplayError:
            raise
        except Exception as e:
            raise DisplayError(f"EPD init failed: {e}") from e

    def _require_initialized(self) -> None:
        """Guard: raise DisplayError if not yet initialized."""
        if not self._initialized:
            raise DisplayError("Display not initialized; call init() first")

    def clear(self) -> None:
        """Clear the display to white.

        Raises DisplayError if not initialized or hardware fails.
        """
        self._require_initialized()
        try:
            self._epd.Clear()
        except Exception as e:
            raise DisplayError(f"EPD clear failed: {e}") from e

    def display_image(self, image_buffer) -> None:
        """Render a PIL Image to the display.

        The image is converted to the display's native format via
        getbuffer() and then sent to the panel.

        Raises DisplayError if not initialized or hardware fails.
        """
        self._require_initialized()
        try:
            buf = self._epd.getbuffer(image_buffer)
            self._epd.display(buf)
        except Exception as e:
            raise DisplayError(f"EPD display_image failed: {e}") from e

    def sleep(self) -> None:
        """Put the display into deep sleep mode.

        Raises DisplayError if not initialized or hardware fails.
        """
        self._require_initialized()
        try:
            self._epd.sleep()
        except Exception as e:
            raise DisplayError(f"EPD sleep failed: {e}") from e
