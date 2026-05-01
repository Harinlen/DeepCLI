"""File state cache — track file read state for stale-write prevention.

Records a snapshot (mtime + content hash) each time :class:`FileReadTool`
reads a file.  :class:`FileEditTool` and :class:`FileWriteTool` check
the cache before writing to detect external modifications.

One cache per session (attached to :class:`ToolContext`).  Sub-agents
receive a **clone** (independent copy) per D14 isolation — they can
edit files the parent read, but mutations don't propagate back.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default maximum cache entries — generous but bounded.
_MAX_ENTRIES = 500


@dataclass(frozen=True)
class FileState:
    """Snapshot of a file at read time.

    Attributes:
        mtime: ``os.path.getmtime()`` at read time.
        content_hash: SHA-256 hex digest of file content.
        is_partial: ``True`` if only a portion was read (offset/limit).
    """

    mtime: float
    content_hash: str
    is_partial: bool = False


class FileStateCache:
    """Per-session LRU cache of file states.

    Thread-safety: the cache is accessed from a single asyncio loop so
    no locking is needed.

    Args:
        max_entries: Maximum number of file paths to track.
    """

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._cache: OrderedDict[str, FileState] = OrderedDict()
        self._max_entries = max_entries

    # -- Recording ----------------------------------------------------------

    def record_read(self, path: str) -> None:
        """Record that a file was read (full or partial).

        Takes a snapshot of the file's current mtime and content hash.
        Skips silently if the file cannot be stat'd (e.g. transient).

        Args:
            path: Absolute file path.
        """
        try:
            mtime = os.path.getmtime(path)
            content = Path(path).read_bytes()
            content_hash = hashlib.sha256(content).hexdigest()
        except OSError:
            logger.debug("Cannot snapshot file state for %s", path)
            return

        state = FileState(mtime=mtime, content_hash=content_hash)
        self._put(path, state)

    def record_read_partial(self, path: str) -> None:
        """Record a partial read (offset/limit).

        Mtime is still useful for change detection.  The content hash
        covers the *full* file so it can detect external modifications.

        Args:
            path: Absolute file path.
        """
        try:
            mtime = os.path.getmtime(path)
            content = Path(path).read_bytes()
            content_hash = hashlib.sha256(content).hexdigest()
        except OSError:
            logger.debug("Cannot snapshot file state for %s", path)
            return

        state = FileState(mtime=mtime, content_hash=content_hash, is_partial=True)
        self._put(path, state)

    def update_after_write(self, path: str) -> None:
        """Update the cached state after a successful write.

        Called by FileEditTool / FileWriteTool after mutating a file so
        that subsequent edits within the same session don't trigger a
        false-positive stale warning.

        Args:
            path: Absolute file path.
        """
        self.record_read(path)

    # -- Checking -----------------------------------------------------------

    def check_before_write(self, path: str) -> tuple[bool, str]:
        """Validate that a file hasn't been modified since the last read.

        Args:
            path: Absolute file path.

        Returns:
            ``(ok, message)`` — ``ok=True`` means safe to write.
        """
        cached = self._cache.get(path)
        if cached is None:
            # Never read — require a read first (except new-file writes).
            if Path(path).exists():
                return False, (
                    "You must read the file before editing it. "
                    "Use file_read first to see the current contents."
                )
            # New file — no prior state needed.
            return True, ""

        # File was read before — check if it's been modified externally.
        try:
            current_mtime = os.path.getmtime(path)
        except OSError:
            # File was deleted between read and write.
            return False, "File no longer exists. It may have been deleted externally."

        if current_mtime == cached.mtime:
            return True, ""

        # Mtime differs — verify content hash to avoid false positives
        # from tools that preserve content but touch mtime (e.g. git).
        try:
            current_content = Path(path).read_bytes()
            current_hash = hashlib.sha256(current_content).hexdigest()
        except OSError:
            return False, "Cannot verify file integrity — read it again before editing."

        if current_hash == cached.content_hash:
            # Content unchanged despite mtime difference — safe to write.
            return True, ""

        return False, (
            "File was modified externally since your last read. Re-read it before editing."
        )

    # -- Cloning (for sub-agents) -------------------------------------------

    def clone(self) -> FileStateCache:
        """Create an independent copy of this cache.

        Used when spawning sub-agents — the child inherits the parent's
        read history but mutations don't propagate back (D14 isolation).
        """
        cloned = FileStateCache(max_entries=self._max_entries)
        cloned._cache = OrderedDict(self._cache)
        return cloned

    # -- Internals ----------------------------------------------------------

    def _put(self, path: str, state: FileState) -> None:
        """Insert or update a cache entry, evicting LRU if full."""
        if path in self._cache:
            self._cache.move_to_end(path)
        self._cache[path] = state
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, path: str) -> bool:
        return path in self._cache
