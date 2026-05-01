"""Path-safety helpers for the memory store.

Centralises filename + relative-path validation so the rules are
stated in one place rather than scattered across :class:`MemoryStore`
methods.  Every filename reaching disk goes through
:func:`validate_filename` and every relative path goes through
:func:`validate_relative`, which together guarantee:

- filenames are plain ``.md`` basenames with no path separators, no
  leading dot, and no ``..`` traversal attempts;
- relative paths always have the shape ``<type_dir>/<filename>``
  where ``type_dir`` is one of the four approved subdirectories.

:func:`resolve_abs_path` additionally performs a resolved
containment check so symlinks cannot escape the memory root.
"""

from __future__ import annotations

from pathlib import Path

from daemon.memory.schema import MemoryType


def _type_dirs() -> frozenset[str]:
    """The four approved top-level subdirectories of the memory root."""
    return frozenset(t.value for t in MemoryType)


def validate_filename(filename: str) -> None:
    """Reject illegal filenames before they reach disk.

    Raises:
        ValueError: The filename is empty, contains path separators,
            starts with ``.``, contains ``..``, or doesn't end in
            ``.md``.
    """
    if not filename or "/" in filename or "\\" in filename:
        raise ValueError(f"Illegal filename: {filename!r}")
    if filename.startswith(".") or ".." in filename:
        raise ValueError(f"Illegal filename: {filename!r}")
    if not filename.endswith(".md"):
        raise ValueError(f"Filename must end with .md: {filename!r}")


def validate_relative(relative: str) -> None:
    """Check a ``<type>/<filename>`` relative path.

    Raises:
        ValueError: Structure is wrong, type-dir is unknown, or the
            filename portion fails :func:`validate_filename`.
    """
    parts = relative.split("/")
    if len(parts) != 2 or parts[0] not in _type_dirs():
        raise ValueError(f"Illegal relative path: {relative!r}")
    validate_filename(parts[1])


def resolve_abs_path(root: Path, relative: str) -> Path:
    """Resolve ``<root>/<relative>`` and verify containment.

    Guards against symlink escapes (``resolve()`` follows links, then
    we confirm the result is still under a resolved ``root``).

    Raises:
        ValueError: Relative path fails validation or the resolved
            path escapes the memory root.
    """
    validate_relative(relative)
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path escapes memory root: {relative!r}") from exc
    return candidate


__all__ = ["resolve_abs_path", "validate_filename", "validate_relative"]
