"""Tests for the image cache (Step 5.6)."""

from __future__ import annotations

import base64
from pathlib import Path

from daemon.providers.base import ImageContent
from daemon.sessions.image_cache import ImageCache


def _make_image(color: bytes = b"\x89PNG_payload") -> ImageContent:
    return ImageContent(
        media_type="image/png",
        data_base64=base64.b64encode(color).decode("ascii"),
    )


class TestImageCache:
    def test_store_and_load(self, tmp_path: Path) -> None:
        cache = ImageCache(tmp_path)
        img = _make_image()
        sha = cache.store(img)
        assert img.source_sha256 == sha
        assert cache.has(sha, "image/png")
        restored = cache.load(sha, "image/png")
        assert restored == img.data_base64

    def test_store_is_idempotent(self, tmp_path: Path) -> None:
        cache = ImageCache(tmp_path)
        img1 = _make_image(b"same-bytes")
        img2 = _make_image(b"same-bytes")
        sha1 = cache.store(img1)
        sha2 = cache.store(img2)
        assert sha1 == sha2
        assert len(list(tmp_path.iterdir())) == 1

    def test_load_miss_raises(self, tmp_path: Path) -> None:
        cache = ImageCache(tmp_path)
        import pytest

        with pytest.raises(FileNotFoundError):
            cache.load("deadbeef" * 8, "image/png")

    def test_eviction(self, tmp_path: Path) -> None:
        # Cap at 200 bytes — each write should force eviction.
        cache = ImageCache(tmp_path, max_bytes=200)
        # Raw payloads are 80+ bytes each after b64 decode.
        for i in range(5):
            cache.store(_make_image(f"payload-{i}".encode() * 10))
        # Total size must stay under (or around) cap after eviction.
        remaining = sum(p.stat().st_size for p in tmp_path.iterdir())
        assert remaining <= 200
