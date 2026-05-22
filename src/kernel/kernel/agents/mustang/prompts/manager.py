"""PromptManager — bootstrap service for prompt text loading and rendering.

Scans ``default/`` at startup, loads every ``.txt`` file into memory,
then overlays any user override directories on top.  Later layers win:
a file with the same key in a user dir replaces the default.

Lookup order (highest priority first):
  1. ``<project>/.mustang/prompts/`` — project-level user overrides
  2. ``~/.mustang/prompts/``         — global user overrides
  3. ``<package>/prompts/default/``  — built-in defaults (read-only)

Key derivation: relative path from the root directory, ``.txt``
suffix stripped, forward-slash separated.  Example::

    default/orchestrator/system.txt  →  "orchestrator/system"
"""

from __future__ import annotations

import logging
from pathlib import Path

from kernel.agents.mustang.prompts.declarations import (
    PromptDeclarationImportReport,
    PromptDeclarationRecord,
    PromptDeclarationStore,
)

logger = logging.getLogger(__name__)

# Package-relative location of the shipped prompt files.
_DEFAULT_DIR = Path(__file__).resolve().parent / "default"


class PromptLoadError(Exception):
    """Raised when the defaults directory is missing or a file cannot be read."""


class PromptKeyError(KeyError):
    """Raised when a requested prompt key does not exist."""


class PromptManager:
    """Bootstrap service: loads and manages all prompt text files.

    Instantiate once during kernel lifespan, call :meth:`load`, then
    use :meth:`get` / :meth:`render` from any subsystem.

    Args:
        defaults_dir: Root directory of built-in prompt ``.txt`` files.
            Defaults to the ``default/`` directory shipped with this
            package.
        user_dirs: Additional directories loaded after ``defaults_dir``,
            in order.  Files with the same key override earlier entries.
            Missing directories are silently skipped.  Pass global user
            dir first, project-local dir last so project wins.
    """

    def __init__(
        self,
        defaults_dir: Path | None = None,
        user_dirs: list[Path] | None = None,
        resource_home: Path | None = None,
    ) -> None:
        self._defaults_dir = defaults_dir or _DEFAULT_DIR
        self._user_dirs: list[Path] = user_dirs or []
        self._resource_home = resource_home
        self._store: dict[str, str] = {}
        self._declaration_record: PromptDeclarationRecord | None = None
        self._declaration_import_report: PromptDeclarationImportReport | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load built-in defaults then overlay user override directories.

        Must be called once at startup.  Populates the internal store
        so that subsequent :meth:`get` / :meth:`render` calls require
        no file I/O.

        Raises:
            PromptLoadError: If the defaults directory does not exist
                or any file cannot be read.
        """
        self._load_resource_store_declarations()
        self._load_dir(self._defaults_dir, required=True)
        for user_dir in self._user_dirs:
            self._load_dir(user_dir, required=False)

    def _load_dir(self, root: Path, *, required: bool) -> None:
        """Scan *root* for ``.txt`` files and merge into the store.

        Args:
            root: Directory to scan.
            required: When ``True``, raise :exc:`PromptLoadError` if
                *root* does not exist.  When ``False``, skip silently.
        """
        if not root.is_dir():
            if required:
                raise PromptLoadError(f"Prompt defaults directory not found: {root}")
            return

        count = 0
        for path in sorted(root.rglob("*.txt")):
            key = path.relative_to(root).with_suffix("").as_posix()
            try:
                self._store[key] = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PromptLoadError(f"Failed to read prompt file {path}: {exc}") from exc
            count += 1

        label = "defaults" if required else "user overrides"
        logger.info("PromptManager: loaded %d %s from %s", count, label, root)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get(self, key: str) -> str:
        """Return the raw prompt text for *key* (no template rendering).

        Args:
            key: Slash-separated identifier, e.g.
                ``"orchestrator/system"``.

        Raises:
            PromptKeyError: If *key* is not loaded.
        """
        try:
            return self._store[key]
        except KeyError:
            raise PromptKeyError(key) from None

    def render(self, key: str, **kwargs: object) -> str:
        """Return prompt text with ``{placeholder}`` values filled in.

        Uses :meth:`str.format` — placeholders must match *kwargs*
        keys exactly.

        Args:
            key: Prompt key (same as :meth:`get`).
            **kwargs: Values for template placeholders.

        Raises:
            PromptKeyError: If *key* is not loaded.
            KeyError: If a placeholder in the template has no matching
                kwarg.
        """
        template = self.get(key)
        return template.format(**kwargs)

    def keys(self) -> list[str]:
        """Return all loaded prompt keys, sorted alphabetically."""
        return sorted(self._store)

    def has(self, key: str) -> bool:
        """Return ``True`` if *key* is loaded."""
        return key in self._store

    @property
    def declaration_record(self) -> PromptDeclarationRecord | None:
        """Current ResourceStore-backed global prompt declaration, when enabled."""
        return self._declaration_record

    @property
    def declaration_import_report(self) -> PromptDeclarationImportReport | None:
        """Last legacy prompt declaration import report, if ResourceStore-backed."""
        return self._declaration_import_report

    def _load_resource_store_declarations(self) -> None:
        if self._resource_home is None:
            return
        declaration_store = PromptDeclarationStore.open(self._resource_home)
        try:
            record = declaration_store.read_global()
            global_dir = self._resource_home / "prompts"
            if record is None:
                self._declaration_import_report = declaration_store.import_legacy_user_prompts(
                    global_dir,
                    actor="system",
                )
                record = declaration_store.read_global()
                if record is None:
                    record = declaration_store.write_global(
                        [],
                        expected_revision=None,
                        actor="system",
                    )
            else:
                self._declaration_import_report = declaration_store.import_legacy_user_prompts(
                    global_dir,
                    actor="system",
                    dry_run=True,
                )
            self._declaration_record = record
        finally:
            declaration_store.close()
