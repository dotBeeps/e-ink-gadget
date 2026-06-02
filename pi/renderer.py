"""
Spectra 6 e-Paper color renderer.

Produces a 600x400 PIL Image quantized to the 6-color Spectra 6 palette
using Floyd–Steinberg error-diffusion dithering.

Usage (by daemon):
    prepared = renderer.prepare_image(input_pil_image)   # → PIL.Image
    buf = epd.getbuffer(prepared)
    epd.display(buf)
"""

from __future__ import annotations

from typing import List, Tuple

from PIL import Image

# ---------------------------------------------------------------------------
# Spectra 6 colour palette (BGR ordering — matches the Waveshare driver)
# ---------------------------------------------------------------------------
SPECTRA6_PALETTE: List[Tuple[int, int, int]] = [
    (0, 0, 0),       # BLACK
    (255, 255, 255), # WHITE
    (255, 255, 0),   # YELLOW
    (255, 0, 0),     # RED
    (0, 0, 255),     # BLUE
    (0, 255, 0),     # GREEN
]

# Pre-computed squared lengths for fast distance checks
_PALETTE_SQUARED = [sum(c * c for c in rgb) for rgb in SPECTRA6_PALETTE]

DISPLAY_WIDTH = 600
DISPLAY_HEIGHT = 400


# ---------------------------------------------------------------------------
# Colour-quantisation helpers
# ---------------------------------------------------------------------------

def quantize_to_palette(
    rgb: Tuple[int, int, int],
    palette: List[Tuple[int, int, int]] = SPECTRA6_PALETTE,
) -> Tuple[int, int, int]:
    """Return the palette entry closest (Euclidean distance) to *rgb*."""
    r, g, b = rgb
    best = palette[0]
    best_d2 = _PALETTE_SQUARED[0] + r * r + g * g + b * b - 2 * (
        r * palette[0][0] + g * palette[0][1] + b * palette[0][2]
    )

    for i, (pr, pg, pb) in enumerate(palette[1:], 1):
        d2 = _PALETTE_SQUARED[i] + r * r + g * g + b * b - 2 * (
            r * pr + g * pg + b * pb
        )
        if d2 < best_d2:
            best_d2 = d2
            best = palette[i]

    return best


def _clamp(value: int) -> int:
    """Clamp an integer to [0, 255]."""
    return max(0, min(255, value))


# ---------------------------------------------------------------------------
# Floyd–Steinberg dithering
# ---------------------------------------------------------------------------

def floyd_steinberg_dither(
    image: Image.Image,
    palette: List[Tuple[int, int, int]] = SPECTRA6_PALETTE,
) -> Image.Image:
    """
    Apply Floyd–Steinberg error-diffusion dithering to *image* using
    the given *palette*.  Returns a new RGB ``PIL.Image`` whose every pixel
    is a colour present in *palette*.
    """
    img = image.convert("RGB")
    w, h = img.size
    pixels = img.load()  # type: ignore[assignment]

    # Work in floating-point for error accumulation to avoid integer
    # truncation compounding.
    err: list[list[list[float]]] = [
        [[0.0, 0.0, 0.0] for _ in range(w)] for _ in range(h)
    ]

    out = Image.new("RGB", (w, h))
    out_pixels = out.load()  # type: ignore[assignment]

    for y in range(h):
        for x in range(w):
            # Retrieve the original pixel and add accumulated error
            pr, pg, pb = pixels[x, y]
            r = _clamp(round(pr + err[y][x][0]))
            g = _clamp(round(pg + err[y][x][1]))
            b = _clamp(round(pb + err[y][x][2]))

            # Find the closest palette colour
            quantized = quantize_to_palette((r, g, b), palette)
            out_pixels[x, y] = quantized

            # Compute the error vector
            eq = r - quantized[0]
            eq_g = g - quantized[1]
            eq_b = b - quantized[2]

            # Distribute error to neighbours (Floyd–Steinberg ratios)
            if x + 1 < w:
                err[y][x + 1][0] += eq * (7.0 / 16.0)
                err[y][x + 1][1] += eq_g * (7.0 / 16.0)
                err[y][x + 1][2] += eq_b * (7.0 / 16.0)

            if y + 1 < h:
                if x > 0:
                    err[y + 1][x - 1][0] += eq * (3.0 / 16.0)
                    err[y + 1][x - 1][1] += eq_g * (3.0 / 16.0)
                    err[y + 1][x - 1][2] += eq_b * (3.0 / 16.0)

                err[y + 1][x][0] += eq * (5.0 / 16.0)
                err[y + 1][x][1] += eq_g * (5.0 / 16.0)
                err[y + 1][x][2] += eq_b * (5.0 / 16.0)

                if x + 1 < w:
                    err[y + 1][x + 1][0] += eq * (1.0 / 16.0)
                    err[y + 1][x + 1][1] += eq_g * (1.0 / 16.0)
                    err[y + 1][x + 1][2] += eq_b * (1.0 / 16.0)

    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def prepare_image(image: Image.Image) -> Image.Image:
    """
    Prepare an image for display on the Spectra 6 e-Paper panel.

    1. Resize to 600×400 (LANCZOS) if necessary.
    2. Apply Floyd–Steinberg dithering to the 6-colour Spectra 6 palette.
    3. Return a PIL ``Image`` where every pixel is one of the six palette
       colours — ready for ``epd.getbuffer()``.
    """
    w, h = image.size

    if w != DISPLAY_WIDTH or h != DISPLAY_HEIGHT:
        # Determine target dimensions maintaining aspect ratio, then centre
        # crop to 600×400.
        image = image.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)

    return floyd_steinberg_dither(image, SPECTRA6_PALETTE)
