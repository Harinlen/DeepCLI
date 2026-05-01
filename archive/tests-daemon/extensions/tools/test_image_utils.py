"""Tests for the image_utils module (Step 5.6)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from PIL import Image

from daemon.extensions.tools.image_utils import (
    detect_mime,
    estimate_image_tokens,
    extension_for_mime,
    read_image_as_base64,
)


def _make_png(path: Path, size: tuple[int, int] = (64, 64)) -> None:
    Image.new("RGB", size, (255, 0, 0)).save(path, "PNG")


def _make_jpeg(path: Path, size: tuple[int, int] = (64, 64)) -> None:
    Image.new("RGB", size, (0, 255, 0)).save(path, "JPEG")


class TestDetectMime:
    def test_png(self, tmp_path: Path) -> None:
        p = tmp_path / "img.png"
        _make_png(p)
        assert detect_mime(p) == "image/png"

    def test_jpeg(self, tmp_path: Path) -> None:
        p = tmp_path / "img.jpg"
        _make_jpeg(p)
        assert detect_mime(p) == "image/jpeg"

    def test_not_an_image(self, tmp_path: Path) -> None:
        p = tmp_path / "txt.txt"
        p.write_text("hello")
        assert detect_mime(p) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert detect_mime(tmp_path / "nope.png") is None


class TestEstimateImageTokens:
    def test_small(self) -> None:
        # 750 px → 1 token; floor at 1
        assert estimate_image_tokens(1, 1) == 1

    def test_1024_square(self) -> None:
        # 1024*1024/750 ≈ 1398
        tokens = estimate_image_tokens(1024, 1024)
        assert 1390 <= tokens <= 1410


class TestExtensionForMime:
    def test_known(self) -> None:
        assert extension_for_mime("image/png") == "png"
        assert extension_for_mime("image/jpeg") == "jpg"
        assert extension_for_mime("image/webp") == "webp"
        assert extension_for_mime("image/gif") == "gif"

    def test_unknown(self) -> None:
        assert extension_for_mime("image/tiff") == "bin"


class TestReadImageAsBase64:
    def test_small_png_not_resized(self, tmp_path: Path) -> None:
        p = tmp_path / "img.png"
        _make_png(p, size=(64, 64))
        img = read_image_as_base64(p)
        assert img.media_type == "image/png"
        assert img.data_base64
        assert img.source_sha256 is not None
        assert img.source_path == str(p)
        # base64 round-trips
        raw = base64.b64decode(img.data_base64)
        assert raw.startswith(b"\x89PNG")

    def test_large_png_downsampled(self, tmp_path: Path) -> None:
        p = tmp_path / "big.png"
        _make_png(p, size=(3000, 3000))  # ~12k tokens raw
        img = read_image_as_base64(p, max_tokens=1500)
        # Verify the encoded image is smaller than pixel-wise original
        raw = base64.b64decode(img.data_base64)
        reopened = Image.open(__import__("io").BytesIO(raw))
        # Should have shrunk to roughly sqrt(1500/12000) * 3000 ≈ 1060
        assert reopened.width < 3000
        assert reopened.width > 900  # not wildly smaller

    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "not_image.txt"
        p.write_text("hi")
        with pytest.raises(ValueError, match="unsupported"):
            read_image_as_base64(p)

    def test_too_large_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "img.png"
        _make_png(p)
        # Patch the hard cap very low
        import daemon.extensions.tools.image_utils as mod

        monkeypatch.setattr(mod, "_MAX_BYTES", 10)
        with pytest.raises(ValueError, match="too large"):
            read_image_as_base64(p)
