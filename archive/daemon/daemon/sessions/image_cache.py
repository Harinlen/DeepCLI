"""Disk-backed cache for image base64 payloads.

JSONL transcripts must stay small — embedding multi-megabyte base64
strings inline would inflate files by orders of magnitude and slow
down ``read_chain`` walks.  This module writes the raw bytes of each
:class:`ImageContent` to ``~/.mustang/cache/images/{sha256}.{ext}``
and lets the caller empty ``data_base64`` before the JSONL entry is
written.  On session resume, the rebuild layer reads the file back.

The cache is content-addressed, so deduplication is automatic and
concurrent writers are safe (same content → same file).  A modest
LRU eviction keeps the directory bounded.
"""

from __future__ import annotations

import base64
import logging
import threading
from pathlib import Path

from daemon.extensions.tools.image_utils import extension_for_mime
from daemon.providers.base import ImageContent

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 200 * 1024 * 1024  # 200 MB budget for the image cache
_MIN_EVICT_TARGET_RATIO = 0.8  # after eviction, stay under 80% of the cap


class ImageCache:
    """Content-addressed storage for :class:`ImageContent` payloads.

    Args:
        root: Directory to store images in.  Created on first write.
        max_bytes: Soft cap; eviction trims oldest-modified files once
            the directory grows past it.
    """

    def __init__(self, root: Path, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self._root = root
        self._max_bytes = max_bytes
        self._evict_lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    # -- public API ------------------------------------------------

    def store(self, image: ImageContent) -> str:
        """Write *image* to disk and return the resulting SHA-256.

        Mutates ``image.source_sha256`` in place.  Safe to call
        repeatedly: a hit on the filesystem short-circuits the write.
        """
        if image.source_sha256 is None:
            # Callers should have set this via read_image_as_base64,
            # but recompute defensively if not.
            image.source_sha256 = _hash_b64(image.data_base64)

        path = self._path_for(image.source_sha256, image.media_type)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = base64.b64decode(image.data_base64)
            path.write_bytes(raw)
            self._maybe_evict()
        return image.source_sha256

    def load(self, sha256: str, media_type: str) -> str:
        """Return base64 for a cached image; raises :class:`FileNotFoundError`."""
        path = self._path_for(sha256, media_type)
        if not path.exists():
            raise FileNotFoundError(f"image cache miss: {sha256}")
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def has(self, sha256: str, media_type: str) -> bool:
        return self._path_for(sha256, media_type).exists()

    # -- helpers ---------------------------------------------------

    def _path_for(self, sha256: str, media_type: str) -> Path:
        ext = extension_for_mime(media_type)
        return self._root / f"{sha256}.{ext}"

    def _maybe_evict(self) -> None:
        """LRU-by-mtime eviction when the cache exceeds its budget."""
        if not self._evict_lock.acquire(blocking=False):
            return  # Another eviction is in progress.
        try:
            self._do_evict()
        finally:
            self._evict_lock.release()

    def _do_evict(self) -> None:
        if not self._root.exists():
            return
        total = 0
        files: list[tuple[float, int, Path]] = []
        for entry in self._root.iterdir():
            if not entry.is_file():
                continue
            stat = entry.stat()
            total += stat.st_size
            files.append((stat.st_mtime, stat.st_size, entry))
        if total <= self._max_bytes:
            return

        target = int(self._max_bytes * _MIN_EVICT_TARGET_RATIO)
        # Oldest first.
        files.sort(key=lambda t: t[0])
        for _mtime, size, path in files:
            if total <= target:
                break
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not evict %s", path)
                continue
            total -= size
        logger.info("Image cache eviction: now %d bytes (target %d)", total, target)


def _hash_b64(data_b64: str) -> str:
    import hashlib

    return hashlib.sha256(base64.b64decode(data_b64)).hexdigest()


__all__ = ["ImageCache"]
