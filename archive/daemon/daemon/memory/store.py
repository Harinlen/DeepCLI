"""MemoryStore — sole writer to ``~/.mustang/memory/``.

Owns the on-disk memory directory + an in-RAM cache of parsed
frontmatter.  Every mutation (``write`` / ``append`` / ``delete``)
atomically:

1. Updates the target file on disk.
2. Updates the in-RAM cache.
3. Regenerates ``index.md``.
4. Appends a line to ``log.md`` (via :class:`MemoryLog`).

LLM tools **must** go through this class — `file_edit` on the memory
directory is denied by PermissionEngine (see D17).  This keeps the
RAM cache and the disk in lockstep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from daemon.memory.access_tracker import AccessTracker
from daemon.memory.index_gen import render_index
from daemon.memory.log import MemoryLog
from daemon.memory.markdown_utils import (
    append_to_section,
    parse_memory_file,
    serialize_memory_file,
    split_frontmatter,
)
from daemon.memory.paths import resolve_abs_path, validate_filename, validate_relative
from daemon.memory.schema import (
    GLOBAL_TYPES,
    MemoryFrontmatter,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryType,
)

# Back-compat aliases: these two were historically defined as
# private helpers in this module, and a handful of tests + the
# skills module still import them by the underscore-prefixed name.
_split_frontmatter = split_frontmatter
_append_to_section = append_to_section

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_ROOT = Path("~/.mustang/memory").expanduser()
GLOBAL_TYPE_DIRS: tuple[str, ...] = ("user", "feedback", "project", "reference")
PROJECT_TYPE_DIRS: tuple[str, ...] = ("task", "context")
# Back-compat alias — tests may import the old name.
TYPE_DIRS = GLOBAL_TYPE_DIRS
_INDEX_NAME = "index.md"
_LOG_NAME = "log.md"


class MemoryStoreError(Exception):
    """Base for memory store errors (path safety, missing entries)."""


@dataclass(slots=True)
class _CacheEntry:
    frontmatter: MemoryFrontmatter
    body: str
    size_bytes: int


class MemoryStore:
    """Manages a memory directory tree.

    Args:
        root: Memory root directory.  Defaults to ``~/.mustang/memory``.
        scope: Global (cross-project) or project-local scope.
        allowed_types: Whitelist of allowed memory types.  Defaults to
            the types defined for the given scope.
    """

    def __init__(
        self,
        root: Path | None = None,
        scope: MemoryScope = MemoryScope.GLOBAL,
        allowed_types: frozenset[MemoryType] | None = None,
    ) -> None:
        self._root = (root or DEFAULT_MEMORY_ROOT).expanduser()
        self._scope = scope
        self._allowed_types = allowed_types or (
            GLOBAL_TYPES if scope == MemoryScope.GLOBAL else frozenset()
        )
        self._cache: dict[str, _CacheEntry] = {}  # relative → entry
        self._log = MemoryLog(self._root / _LOG_NAME)
        # Access counting delegated to AccessTracker (Phase 5.7D).
        self._access_tracker = AccessTracker(self._root)

    # -- Public properties -------------------------------------------

    @property
    def root(self) -> Path:
        """Absolute path to the memory root directory."""
        return self._root

    @property
    def scope(self) -> MemoryScope:
        """Global or project scope."""
        return self._scope

    @property
    def allowed_types(self) -> frozenset[MemoryType]:
        """Whitelist of allowed memory types for this store."""
        return self._allowed_types

    @property
    def log(self) -> MemoryLog:
        """The underlying :class:`MemoryLog`."""
        return self._log

    @property
    def type_dirs(self) -> tuple[str, ...]:
        """Type subdirectory names for this store's scope."""
        return tuple(t.value for t in self._allowed_types)

    # -- Lifecycle ---------------------------------------------------

    def load(self) -> None:
        """Scan the memory directory into the RAM cache.

        Creates the root + four type subfolders if missing.  Parse
        errors are logged as warnings and the entry is skipped —
        loading never raises during startup.
        """
        self._cache.clear()
        self._ensure_skeleton()

        for type_name in self.type_dirs:
            type_dir = self._root / type_name
            if not type_dir.is_dir():
                continue
            for md_path in sorted(type_dir.glob("*.md")):
                relative = f"{type_name}/{md_path.name}"
                try:
                    fm, body = self._parse_file(md_path)
                except Exception as exc:
                    logger.warning("Skipping %s: %s", relative, exc)
                    continue
                size = md_path.stat().st_size
                self._cache[relative] = _CacheEntry(frontmatter=fm, body=body, size_bytes=size)

        # Refresh on-disk index to match current state.
        self._write_index()
        # Load access counts (Phase 5.7D).
        self.load_access_counts()

    # -- Read API ----------------------------------------------------

    def records(self, type_filter: MemoryType | None = None) -> list[MemoryRecord]:
        """Return all cached records (optionally filtered by type)."""
        out: list[MemoryRecord] = []
        for relative, entry in self._cache.items():
            if type_filter is not None and entry.frontmatter.type != type_filter:
                continue
            out.append(
                MemoryRecord(
                    relative=relative,
                    path=self._root / relative,
                    frontmatter=entry.frontmatter,
                    size_bytes=entry.size_bytes,
                )
            )
        out.sort(key=lambda r: (r.frontmatter.type.value, r.frontmatter.name.lower()))
        return out

    def read(self, relative: str) -> tuple[MemoryFrontmatter, str]:
        """Return ``(frontmatter, body)`` for an existing entry.

        Raises:
            MemoryStoreError: If the entry does not exist in the cache.
        """
        self._validate_relative(relative)
        entry = self._cache.get(relative)
        if entry is None:
            raise MemoryStoreError(f"No such memory: {relative}")
        return entry.frontmatter, entry.body

    def index_text(self) -> str:
        """Return the current index.md contents (from RAM cache)."""
        return render_index(self.records())

    # -- Write API ---------------------------------------------------

    def write(
        self,
        type: MemoryType,
        filename: str,
        frontmatter: MemoryFrontmatter,
        body: str,
    ) -> Path:
        """Create or overwrite a memory file.

        The frontmatter's ``type`` must match the ``type`` argument
        (the argument wins if they disagree — we rewrite the
        frontmatter to keep RAM and disk consistent).

        Returns:
            Absolute path of the written file.
        """
        self._validate_filename(filename)
        if type not in self._allowed_types:
            raise MemoryStoreError(
                f"Type {type.value!r} is not allowed in {self._scope.value} memory "
                f"(allowed: {', '.join(t.value for t in self._allowed_types)})"
            )
        relative = f"{type.value}/{filename}"

        # Normalise: frontmatter.type must match directory.
        fm_dict = frontmatter.model_dump()
        fm_dict["type"] = type.value
        fm = MemoryFrontmatter(**fm_dict)

        # Path safety: resolve and check containment.
        abs_path = self._abs_path(relative)

        # Serialize.
        text = self._serialize(fm, body)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")

        is_update = relative in self._cache
        self._cache[relative] = _CacheEntry(
            frontmatter=fm, body=body, size_bytes=abs_path.stat().st_size
        )
        self._write_index()
        op = "UPDATE" if is_update else "WRITE"
        self._log.append(op, relative, fm.description[:80])
        return abs_path

    def append(
        self,
        type: MemoryType,
        filename: str,
        section: str,
        bullet: str,
    ) -> None:
        """Append a bullet to ``## <section>`` of an aggregate file.

        Creates the section if missing (appended at end of body).
        Does **not** update frontmatter.description — LLM is expected
        to follow up with ``memory_write`` if the description needs
        refreshing (see instructions).

        Raises:
            MemoryStoreError: File does not exist, is not aggregate,
                section name is empty, or bullet is empty.
        """
        self._validate_filename(filename)
        relative = f"{type.value}/{filename}"
        entry = self._cache.get(relative)
        if entry is None:
            raise MemoryStoreError(f"No such aggregate file: {relative}")
        if entry.frontmatter.kind != MemoryKind.AGGREGATE:
            raise MemoryStoreError(
                f"{relative} has kind={entry.frontmatter.kind.value}, cannot append"
            )
        if not section.strip():
            raise MemoryStoreError("section name cannot be empty")
        if not bullet.strip():
            raise MemoryStoreError("bullet cannot be empty")

        new_body = _append_to_section(entry.body, section.strip(), bullet.strip())

        abs_path = self._abs_path(relative)
        text = self._serialize(entry.frontmatter, new_body)
        abs_path.write_text(text, encoding="utf-8")

        self._cache[relative] = _CacheEntry(
            frontmatter=entry.frontmatter,
            body=new_body,
            size_bytes=abs_path.stat().st_size,
        )
        self._write_index()
        self._log.append("APPEND", relative, f"[{section}] {bullet}"[:80])

    def delete(self, type: MemoryType, filename: str) -> bool:
        """Remove a memory file.

        Returns:
            True if a file was removed, False if it did not exist.
        """
        self._validate_filename(filename)
        relative = f"{type.value}/{filename}"
        if relative not in self._cache:
            return False

        abs_path = self._abs_path(relative)
        if abs_path.exists():
            abs_path.unlink()
        self._cache.pop(relative)
        self._write_index()
        self._log.append("DELETE", relative)
        return True

    # -- Access counting (Phase 5.7D) — delegated to AccessTracker -----

    def record_access(self, relative: str) -> None:
        """Increment the read count for a memory entry."""
        self._access_tracker.record(relative)

    def hot_memories(self, top_n: int = 10) -> list[MemoryRecord]:
        """Return the N most frequently accessed memory records."""
        return self._access_tracker.hot_memories(top_n, self._cache, self._root)

    def load_access_counts(self) -> None:
        """Load access counts from disk (if persisted)."""
        self._access_tracker.load()

    def save_access_counts(self) -> None:
        """Persist access counts to disk."""
        self._access_tracker.save()

    # -- Internal helpers --------------------------------------------

    def _ensure_skeleton(self) -> None:
        """Create the root + type subfolders if missing."""
        self._root.mkdir(parents=True, exist_ok=True)
        for type_name in self.type_dirs:
            (self._root / type_name).mkdir(exist_ok=True)

    def _write_index(self) -> None:
        """Write the current index to disk."""
        index_path = self._root / _INDEX_NAME
        index_path.write_text(render_index(self.records()), encoding="utf-8")

    def _parse_file(self, path: Path) -> tuple[MemoryFrontmatter, str]:
        # Thin wrapper that translates ValueError → MemoryStoreError
        # so callers get a domain-specific exception type.
        try:
            return parse_memory_file(path)
        except ValueError as exc:
            raise MemoryStoreError(str(exc)) from exc

    def _serialize(self, fm: MemoryFrontmatter, body: str) -> str:
        return serialize_memory_file(fm, body)

    # -- Path safety -------------------------------------------------
    # Thin instance-method wrappers that translate ValueError →
    # MemoryStoreError so the caller sees a single domain-specific
    # exception type.  The actual checks live in daemon.memory.paths.

    def _validate_filename(self, filename: str) -> None:
        try:
            validate_filename(filename)
        except ValueError as exc:
            raise MemoryStoreError(str(exc)) from exc

    def _validate_relative(self, relative: str) -> None:
        try:
            validate_relative(relative)
        except ValueError as exc:
            raise MemoryStoreError(str(exc)) from exc

    def _abs_path(self, relative: str) -> Path:
        """Resolve a relative path and verify containment under root."""
        try:
            return resolve_abs_path(self._root, relative)
        except ValueError as exc:
            raise MemoryStoreError(str(exc)) from exc
