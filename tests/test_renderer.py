"""
Tests for the Spectra 6 e-Paper colour-quantisation / dithering renderer.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from pi.renderer import (
    SPECTRA6_PALETTE,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    floyd_steinberg_dither,
    prepare_image,
    quantize_to_palette,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PALETTE_SET = set(SPECTRA6_PALETTE)


def _all_pixels_in_palette(im: Image.Image) -> bool:
    """Return True if every pixel in *im* is one of the Spectra 6 colours."""
    w, h = im.size
    pixels = im.load()
    for y in range(h):
        for x in range(w):
            if pixels[x, y] not in PALETTE_SET:
                return False
    return True


def _unique_colour_count(im: Image.Image) -> int:
    """Number of distinct colours present in *im*."""
    pixels = im.load()
    colours: set[tuple[int, int, int]] = set()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            colours.add(pixels[x, y])  # type: ignore[arg-type]
    return len(colours)


# ===================================================================
# quantize_to_palette
# ===================================================================


class TestQuantizeToPalette:
    def test_exact_black(self):
        assert quantize_to_palette((0, 0, 0)) == (0, 0, 0)

    def test_exact_white(self):
        assert quantize_to_palette((255, 255, 255)) == (255, 255, 255)

    def test_exact_red(self):
        assert quantize_to_palette((255, 0, 0)) == (255, 0, 0)

    def test_exact_green(self):
        assert quantize_to_palette((0, 255, 0)) == (0, 255, 0)

    def test_exact_blue(self):
        assert quantize_to_palette((0, 0, 255)) == (0, 0, 255)

    def test_exact_yellow(self):
        assert quantize_to_palette((255, 255, 0)) == (255, 255, 0)

    def test_near_white_snaps_to_white(self):
        # Light grey should snap to WHITE
        assert quantize_to_palette((250, 250, 250)) == (255, 255, 255)

    def test_near_black_snaps_to_black(self):
        # Dark grey should snap to BLACK
        assert quantize_to_palette((5, 5, 5)) == (0, 0, 0)

    def test_mid_grey_snaps_to_white_or_black(self):
        # Mid-grey — closer to black (distance sqrt(3*128²) ≈ 221.7)
        # vs white (same distance), so depends on exact proximity.
        # 127 is slightly closer to black (127 vs 128), so black.
        result = quantize_to_palette((127, 127, 127))
        assert result in {(0, 0, 0), (255, 255, 255)}

    def test_orange_snaps_to_yellow(self):
        # Orange-ish (255, 128, 0) — Euclidean distance check:
        #   RED:    (0)² + (128)² + (0)²   = 16384
        #   YELLOW: (0)² + (127)² + (0)²   = 16129  ← closest
        assert quantize_to_palette((255, 128, 0)) == (255, 255, 0)

    def test_deep_orange_snaps_to_red(self):
        # Deep orange (255, 64, 0) — closer to RED
        assert quantize_to_palette((255, 64, 0)) == (255, 0, 0)

    def test_cyan_snaps_to_white_blue_or_green(self):
        # Cyan (0, 255, 255) — equidistant (distance² = 65025) from
        # WHITE, BLUE, and GREEN.  WHITE appears first in the palette
        # so it wins the tiebreak, but all three are valid.
        result = quantize_to_palette((0, 255, 255))
        assert result in {(255, 255, 255), (0, 0, 255), (0, 255, 0)}

    def test_all_palette_colours_return_identity(self):
        for colour in SPECTRA6_PALETTE:
            assert quantize_to_palette(colour) == colour, f"Failed for {colour}"


# ===================================================================
# floyd_steinberg_dither
# ===================================================================


class TestFloydSteinbergDither:
    def test_white_image_stays_all_white(self):
        im = Image.new("RGB", (600, 400), (255, 255, 255))
        result = floyd_steinberg_dither(im)
        assert result.size == (600, 400)
        assert _all_pixels_in_palette(result)
        assert _unique_colour_count(result) == 1
        assert result.getpixel((0, 0)) == (255, 255, 255)

    def test_black_image_stays_all_black(self):
        im = Image.new("RGB", (600, 400), (0, 0, 0))
        result = floyd_steinberg_dither(im)
        assert _all_pixels_in_palette(result)
        assert _unique_colour_count(result) == 1
        assert result.getpixel((0, 0)) == (0, 0, 0)

    def test_solid_red_stays_all_red(self):
        im = Image.new("RGB", (100, 100), (255, 0, 0))
        result = floyd_steinberg_dither(im)
        assert _all_pixels_in_palette(result)
        assert _unique_colour_count(result) == 1

    def test_gray_image_not_solid_block(self):
        """A mid-gray image should produce more than one colour due to
        error diffusion — this proves dithering is actually running."""
        im = Image.new("RGB", (200, 200), (128, 128, 128))
        result = floyd_steinberg_dither(im)
        assert _all_pixels_in_palette(result)
        # With a single colour input, dithering should introduce at least
        # two palette colours (black + white at minimum).
        assert _unique_colour_count(result) >= 2, (
            "Dithered mid-grey should contain multiple palette colours"
        )

    def test_output_only_palette_colours(self):
        """Arbitrary colour image must snap every pixel to the palette."""
        im = Image.new("RGB", (50, 50), (42, 137, 200))
        result = floyd_steinberg_dither(im)
        assert _all_pixels_in_palette(result)

    def test_small_image_handled(self):
        """1×1 image should still work."""
        im = Image.new("RGB", (1, 1), (123, 45, 67))
        result = floyd_steinberg_dither(im)
        assert result.size == (1, 1)
        assert _all_pixels_in_palette(result)

    def test_gradient_introduces_diversity(self):
        """A red→green colour gradient should produce ≥3 palette colours."""
        w, h = 150, 100
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        for x in range(w):
            t = x / (w - 1)
            # Interpolate R from 255→0, G from 0→255 (passes through yellow)
            arr[:, x] = [int(255 * (1 - t)), int(255 * t), 0]
        im = Image.fromarray(arr, "RGB")
        result = floyd_steinberg_dither(im)
        assert _all_pixels_in_palette(result)
        # A red→green sweep should engage RED, YELLOW, and GREEN at minimum
        assert _unique_colour_count(result) >= 3, (
            "Red→green gradient should produce at least 3 palette colours "
            f"after dithering, got {_unique_colour_count(result)}"
        )


# ===================================================================
# prepare_image
# ===================================================================


class TestPrepareImage:
    def test_output_dimensions(self):
        """Output must always be 600×400."""
        for w, h in [(600, 400), (400, 600), (800, 600), (1920, 1080)]:
            im = Image.new("RGB", (w, h), (255, 255, 255))
            result = prepare_image(im)
            assert result.size == (DISPLAY_WIDTH, DISPLAY_HEIGHT), (
                f"Expected ({DISPLAY_WIDTH}, {DISPLAY_HEIGHT}) "
                f"for input ({w}, {h}), got {result.size}"
            )

    def test_white_input_produces_all_white_output(self):
        im = Image.new("RGB", (800, 600), (255, 255, 255))
        result = prepare_image(im)
        assert result.size == (DISPLAY_WIDTH, DISPLAY_HEIGHT)
        pixels = result.load()
        w, h = result.size
        assert all(pixels[x, y] == (255, 255, 255) for y in range(h) for x in range(w))

    def test_only_palette_colours(self):
        """Every output pixel is a Spectra 6 colour."""
        # Random-ish colour image
        arr = np.random.randint(0, 256, (400, 600, 3), dtype=np.uint8)
        im = Image.fromarray(arr, "RGB")
        result = prepare_image(im)
        assert result.size == (DISPLAY_WIDTH, DISPLAY_HEIGHT)
        assert _all_pixels_in_palette(result)

    def test_400x600_portrait_gets_resized(self):
        """A portrait 400×600 should map to 600×400 landscape."""
        im = Image.new("RGB", (400, 600), (255, 0, 0))
        result = prepare_image(im)
        assert result.size == (DISPLAY_WIDTH, DISPLAY_HEIGHT)
        assert _all_pixels_in_palette(result)

    def test_return_type_is_pil_image(self):
        im = Image.new("RGB", (100, 100), (128, 128, 128))
        result = prepare_image(im)
        from PIL import Image as PILImage

        assert isinstance(result, PILImage.Image)
