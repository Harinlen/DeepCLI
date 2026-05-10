"""FileStateCache — shared state for file-reading / file-editing tools.

FileRead records ``(mtime, hash)`` when it returns a file's contents;
FileEdit / FileWrite consult that record before writing.  If the file
has been modified externally since the recorded read, the edit tool
refuses — preventing the LLM from silently overwriting user changes
with a stale view.

The cache is a single in-memory map scoped to the kernel process; all
tool calls inside the process share it, regardless of which session
triggered the read.  Cross-process invalidation is future work (not
needed until we run multi-process kernels).

Aligned with the daemon-era Phase 5.5.3A implementation (TypeScript);
this is the Python port.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileState:
    """Snapshot of a file's state at the time of a FileRead."""

    path: Path
    mtime_ns: int
    sha256_hex: str


def hash_text(text: str) -> str:
    """SHA-256 hex of ``text`` encoded as UTF-8.

    Public helper so tools don't duplicate the encoding choice.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FileStateCache:
    """Thread-safe in-memory cache of ``path -> FileState``.

    Uses ``threading.Lock`` (not asyncio) because the underlying
    ``pathlib.Path.read_text`` / ``os.stat`` calls inside tool
    implementations are synchronous; callers hold the lock only for
    dict mutations, not for I/O.
    """

    def __init__(self) -> None:
        self._states: dict[Path, FileState] = {}
        self._lock = threading.Lock()

    def record(self, path: Path, content: str) -> FileState:
        """Record ``content`` as what the LLM has seen for ``path``.

        Resolves ``path`` to an absolute path before caching so that
        relative-path vs absolute-path reads share an entry.
        """
        resolved = path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
        state = FileState(path=resolved, mtime_ns=mtime_ns, sha256_hex=hash_text(content))
        with self._lock:
            self._states[resolved] = state
        return state

    def verify(self, path: Path) -> FileState | None:
        """Return the cached state for ``path`` or ``None`` if absent.

        FileEdit / FileWrite compare the returned state's mtime_ns +
        sha256_hex against the current on-disk values; if they differ,
        the edit is rejected (external modification detected).
        """
        resolved = path.resolve()
        with self._lock:
            return self._states.get(resolved)

    def invalidate(self, path: Path) -> None:
        """Drop the cache entry for ``path``.

        Called after a successful write so the next read doesn't
        false-positive on the stale hash.
        """
        resolved = path.resolve()
        with self._lock:
            self._states.pop(resolved, None)

    def clear(self) -> None:
        """Drop all cache entries.  Intended for tests only."""
        with self._lock:
            self._states.clear()


__all__ = ["FileState", "FileStateCache", "hash_text"]
