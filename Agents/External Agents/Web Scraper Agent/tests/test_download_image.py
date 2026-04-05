"""Tests for tools/download_image.py -- image processing logic."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from web_scraper_mcp.tools.download_image import FORMAT_MAP, MIME_MAP, _process_image


def _create_test_image(width: int, height: int, mode: str = "RGB") -> bytes:
    """Create a minimal test image and return its PNG bytes."""
    img = Image.new(mode, (width, height), color=(200, 100, 50))
    buf = io.BytesIO()
    if mode == "RGBA":
        img.save(buf, format="PNG")
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()


class TestProcessImage:

    def test_png_output(self) -> None:
        raw = _create_test_image(800, 600)
        processed, mime = _process_image(raw, "png", max_width=1200)
        assert mime == "image/png"
        img = Image.open(io.BytesIO(processed))
        assert img.format == "PNG"

    def test_jpg_output(self) -> None:
        raw = _create_test_image(800, 600)
        processed, mime = _process_image(raw, "jpg", max_width=1200)
        assert mime == "image/jpeg"
        img = Image.open(io.BytesIO(processed))
        assert img.format == "JPEG"

    def test_webp_output(self) -> None:
        raw = _create_test_image(800, 600)
        processed, mime = _process_image(raw, "webp", max_width=1200)
        assert mime == "image/webp"

    def test_resize_when_wider_than_max(self) -> None:
        raw = _create_test_image(2000, 1000)
        processed, _ = _process_image(raw, "png", max_width=1200)
        img = Image.open(io.BytesIO(processed))
        assert img.width == 1200
        assert img.height == 600  # aspect ratio preserved

    def test_no_resize_when_within_max(self) -> None:
        raw = _create_test_image(800, 600)
        processed, _ = _process_image(raw, "png", max_width=1200)
        img = Image.open(io.BytesIO(processed))
        assert img.width == 800
        assert img.height == 600

    def test_rgba_to_jpeg_converts_to_rgb(self) -> None:
        """JPEG does not support RGBA -- should convert with white background."""
        raw = _create_test_image(100, 100, mode="RGBA")
        processed, mime = _process_image(raw, "jpg", max_width=1200)
        assert mime == "image/jpeg"
        img = Image.open(io.BytesIO(processed))
        assert img.mode == "RGB"

    def test_invalid_image_raises(self) -> None:
        with pytest.raises(Exception):
            _process_image(b"not an image", "png", max_width=1200)

    def test_quality_parameter(self) -> None:
        raw = _create_test_image(400, 400)
        low_q, _ = _process_image(raw, "jpg", max_width=1200, quality=10)
        high_q, _ = _process_image(raw, "jpg", max_width=1200, quality=95)
        # Lower quality should produce smaller output
        assert len(low_q) < len(high_q)


class TestFormatMaps:

    def test_format_map_covers_common_formats(self) -> None:
        assert "jpg" in FORMAT_MAP
        assert "jpeg" in FORMAT_MAP
        assert "png" in FORMAT_MAP
        assert "webp" in FORMAT_MAP

    def test_mime_map_covers_pil_formats(self) -> None:
        assert "JPEG" in MIME_MAP
        assert "PNG" in MIME_MAP
        assert "WEBP" in MIME_MAP
