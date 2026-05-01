"""Append-only operation log for the memory store.

Every ``WRITE`` / ``APPEND`` / ``UPDATE`` / ``DELETE`` / ``LINT``
operation appends a single line to ``log.md``; when the file exceeds
``MAX_LOG_LINES`` its contents are appended to ``log.archive.md`` and
``log.md`` is truncated.  The archive is never truncated — history
is preserved indefinitely (user can delete the archive manually if
disk usage ever matters; see batch 3 design doc for rationale).

log.md is not injected into the system prompt.  The LLM reads it via
``file_read`` when it needs to audit recent changes (e.g. during
``/memory lint``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 200
_ARCHIVE_NAME = "log.archive.md"
_VALID_OPS = frozenset({"WRITE", "APPEND", "UPDATE", "DELETE", "LINT"})


class MemoryLog:
    """Append-only operation log with size-triggered archive rotation.

    Not thread-safe — the daemon is the sole writer per docs/D17.

    Args:
        path: Full path to ``log.md``.  Parent directory must exist.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._archive = path.parent / _ARCHIVE_NAME

    @property
    def path(self) -> Path:
        """Path to the current (non-archived) log file."""
        return self._path

    @property
    def archive_path(self) -> Path:
        """Path to the archive file (may not yet exist)."""
        return self._archive

    def append(self, op: str, target: str, note: str = "") -> None:
        """Append a single log entry.

        Args:
            op: One of ``WRITE`` / ``APPEND`` / ``UPDATE`` / ``DELETE``
                / ``LINT``.  Other values are accepted but logged as a
                warning (we do not raise — logging should never break
                the main flow).
            target: Relative path of the affected memory file, or a
                short summary for ``LINT``.
            note: Optional free-form note.
        """
        if op not in _VALID_OPS:
            logger.warning("Unknown memory log op %r (allowed: %s)", op, sorted(_VALID_OPS))

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        # Two-space separators keep lines grep-friendly.
        line = f"{timestamp}  {op:<7} {target}"
        if note:
            line += f" — {note}"
        line += "\n"

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)

        # Check rotation after each append.  Cheap: line-count a small file.
        if self._line_count() > MAX_LOG_LINES:
            self._rotate()

    def read(self) -> str:
        """Return current log.md contents (empty string if missing)."""
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def _line_count(self) -> int:
        """Count lines in log.md without reading the whole file."""
        if not self._path.exists():
            return 0
        count = 0
        with self._path.open("rb") as f:
            for _ in f:
                count += 1
        return count

    def _rotate(self) -> None:
        """Append current log.md to log.archive.md, then truncate log.md.

        Archive is append-only — oldest entries at the bottom of
        previous rotations, newest at the top of the most recent
        rotation block.  Never overwrites.
        """
        current = self._path.read_text(encoding="utf-8")
        if not current:
            return

        # Append to archive; create it if missing.
        with self._archive.open("a", encoding="utf-8") as f:
            f.write(current)

        # Truncate log.md (keep the file, empty its contents).
        self._path.write_text("", encoding="utf-8")
        logger.info("Rotated memory log to %s", self._archive)
