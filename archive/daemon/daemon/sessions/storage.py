"""JSONL transcript storage for a single session.

Owns :class:`TranscriptWriter` — the append-only JSONL writer /
reader bound to one ``{session_id}.jsonl`` file and its companion
``{session_id}.meta.json``.  The Pydantic models used in that meta
file (:class:`SessionMeta`, :class:`ModelUsage`) live in
:mod:`daemon.sessions.meta` and are re-exported below so existing
``from daemon.sessions.storage import SessionMeta`` imports keep
working.

Design invariants:

- The daemon process is the **sole writer** to any given JSONL file.
  Multiple WS clients may share a session, but all writes funnel
  through the daemon's :class:`~daemon.sessions.manager.Session`
  object.  No file-level locking is required.
- Entries are written as single JSON lines terminated by ``\\n``.
  Partial writes are detectable (incomplete JSON) and skipped on
  read.
- ``UserMessageEntry`` is flushed *before* the LLM API call so that
  a daemon crash preserves user input.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from daemon.sessions.entry import BaseEntry, Entry
from daemon.sessions.meta import ModelUsage, SessionMeta

logger = logging.getLogger(__name__)

# Maximum JSONL file size allowed for full read (50 MB).
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024

# Type adapter for parsing the Entry discriminated union from dicts.
_entry_adapter: TypeAdapter[Entry] = TypeAdapter(Entry)


# Re-exported for back-compat with existing imports.
__all__ = [
    "MAX_TRANSCRIPT_BYTES",
    "ModelUsage",
    "SessionMeta",
    "TranscriptWriter",
]


class TranscriptWriter:
    """Append-only JSONL writer and reader for a single session.

    Each instance manages one ``{session_id}.jsonl`` file and its
    companion ``{session_id}.meta.json``.

    Args:
        session_dir: Directory containing all session files
            (e.g. ``~/.mustang/sessions``).
        session_id: Unique session identifier.
        meta: Pre-populated metadata (for new sessions) or ``None``
            to load from disk.
    """

    def __init__(self, session_dir: Path, session_id: str, meta: SessionMeta | None = None) -> None:
        self._dir = session_dir
        self._session_id = session_id
        self._jsonl_path = session_dir / f"{session_id}.jsonl"
        self._meta_path = session_dir / f"{session_id}.meta.json"
        self._last_uuid: str | None = None

        # Ensure the directory exists.
        self._dir.mkdir(parents=True, exist_ok=True)

        # Load or create metadata.
        if meta is not None:
            self._meta = meta
        elif self._meta_path.exists():
            self._meta = SessionMeta.model_validate_json(self._meta_path.read_text())
        else:
            self._meta = SessionMeta(session_id=session_id)

    # -- Public properties -------------------------------------------

    @property
    def meta(self) -> SessionMeta:
        """Current session metadata (in-memory, flushed on mutation)."""
        return self._meta

    @property
    def jsonl_path(self) -> Path:
        """Path to the JSONL transcript file."""
        return self._jsonl_path

    @property
    def last_uuid(self) -> str | None:
        """UUID of the most recently appended entry.

        Used as ``parent_uuid`` for the next entry to maintain the
        linked-list chain.
        """
        return self._last_uuid

    @last_uuid.setter
    def last_uuid(self, value: str | None) -> None:
        """Set the last UUID (used when restoring from a chain)."""
        self._last_uuid = value

    @property
    def meta_path(self) -> Path:
        """Path to the ``.meta.json`` file."""
        return self._meta_path

    # -- Write -------------------------------------------------------

    def append(self, entry: BaseEntry) -> None:
        """Append one entry to the JSONL transcript.

        Automatically sets ``parent_uuid`` to chain with the previous
        entry, updates metadata counters, and flushes the meta file.

        Note:
            The entry's ``parent_uuid`` is set in-place to maintain
            the chain.  Callers should treat entries as consumed
            after passing them to this method.

        Args:
            entry: The entry to persist (modified in-place).
        """
        # Chain linkage: point to the previous entry.
        entry.parent_uuid = self._last_uuid
        self._last_uuid = entry.uuid

        line = entry.model_dump_json() + "\n"
        with open(self._jsonl_path, "a", encoding="utf-8") as fh:
            fh.write(line)

        # Auto-generate title from the first user message.
        if (
            self._meta.title is None
            and hasattr(entry, "content")
            and isinstance(entry.content, str)
        ):
            self._meta.title = entry.content[:80].split("\n", 1)[0]

        # Update metadata.
        self._meta.message_count += 1
        self._meta.updated_at = datetime.now(timezone.utc).isoformat()
        self._flush_meta()

    def update_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> None:
        """Accumulate token usage for a specific model and flush metadata.

        Updates both the session-wide totals and the per-model
        breakdown.  Called after each LLM turn.

        Args:
            model: The model identifier used for this turn (e.g.
                ``"MiniMax-M1"``).  Keyed into ``model_usage``.
            input_tokens: Tokens consumed by the prompt.
            output_tokens: Tokens generated by the model.
            cache_creation_tokens: Tokens used to create a cache entry
                (Anthropic prompt caching).
            cache_read_tokens: Tokens served from cache.
        """
        self._meta.total_input_tokens += input_tokens
        self._meta.total_output_tokens += output_tokens
        # Per-model breakdown.  setdefault avoids mutating a shared default.
        if model not in self._meta.model_usage:
            self._meta.model_usage[model] = ModelUsage()
        mu = self._meta.model_usage[model]
        mu.input_tokens += input_tokens
        mu.output_tokens += output_tokens
        mu.cache_creation_tokens += cache_creation_tokens
        mu.cache_read_tokens += cache_read_tokens
        self._meta.updated_at = datetime.now(timezone.utc).isoformat()
        self._flush_meta()

    # -- Read --------------------------------------------------------

    def read_all(self) -> list[Entry]:
        """Read and parse every entry from the JSONL transcript.

        Returns:
            Ordered list of entries as they appear in the file.

        Raises:
            ValueError: If the file exceeds ``MAX_TRANSCRIPT_BYTES``.
        """
        if not self._jsonl_path.exists():
            return []

        size = self._jsonl_path.stat().st_size
        if size > MAX_TRANSCRIPT_BYTES:
            raise ValueError(
                f"Transcript too large ({size / 1024 / 1024:.1f} MB, "
                f"limit {MAX_TRANSCRIPT_BYTES / 1024 / 1024:.0f} MB). "
                "Run /compact first."
            )

        entries: list[Entry] = []
        with open(self._jsonl_path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data: dict[str, Any] = json.loads(line)
                    entries.append(_entry_adapter.validate_python(data))
                except (json.JSONDecodeError, ValueError):
                    # Malformed JSON or invalid entry schema — skip.
                    logger.warning("Skipping malformed entry at line %d", lineno)
        return entries

    def read_chain(self) -> list[Entry]:
        """Rebuild the conversation chain from the JSONL transcript.

        Walks backwards from the last entry via ``parent_uuid``
        pointers, producing a root-to-leaf ordered list.  Detects
        cycles and skips orphaned entries.

        Returns:
            Linear chain of entries from first to last.
        """
        all_entries = self.read_all()
        if not all_entries:
            return []

        # Build UUID → entry index.
        by_uuid: dict[str, Entry] = {}
        for entry in all_entries:
            by_uuid[entry.uuid] = entry

        # Walk backwards from the last entry.
        chain: list[Entry] = []
        seen: set[str] = set()
        current: Entry | None = all_entries[-1]

        while current is not None:
            if current.uuid in seen:
                logger.warning("Cycle detected in transcript chain at uuid=%s", current.uuid)
                break
            seen.add(current.uuid)
            chain.append(current)
            current = by_uuid.get(current.parent_uuid) if current.parent_uuid else None

        chain.reverse()

        orphaned = len(all_entries) - len(chain)
        if orphaned > 0:
            logger.warning(
                "Transcript has %d orphaned entries (broken parent_uuid chain)",
                orphaned,
            )

        return chain

    def read_tail(self, n_bytes: int = 8192) -> list[Entry]:
        """Read entries from the tail of the JSONL file.

        Useful for session listing — extracts the last few entries
        without parsing the entire file.

        Args:
            n_bytes: Number of bytes to read from the end.

        Returns:
            Entries found in the tail chunk (may be partial at the
            boundary — first entry is skipped if incomplete).
        """
        if not self._jsonl_path.exists():
            return []

        size = self._jsonl_path.stat().st_size
        offset = max(0, size - n_bytes)

        entries: list[Entry] = []
        with open(self._jsonl_path, encoding="utf-8") as fh:
            if offset > 0:
                fh.seek(offset)
                fh.readline()  # Discard partial first line.
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(_entry_adapter.validate_python(data))
                except (json.JSONDecodeError, ValueError):
                    # Partial line at boundary or malformed entry — skip.
                    logger.debug("Skipping malformed entry in tail read")
        return entries

    # -- Delete ------------------------------------------------------

    def delete(self) -> None:
        """Remove transcript and metadata files from disk."""
        for path in (self._jsonl_path, self._meta_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete %s", path)

    # -- Internal ----------------------------------------------------

    def _flush_meta(self) -> None:
        """Write metadata to the ``.meta.json`` file."""
        try:
            self._meta_path.write_text(self._meta.model_dump_json(indent=2))
        except OSError:
            logger.warning("Failed to write session metadata to %s", self._meta_path, exc_info=True)
