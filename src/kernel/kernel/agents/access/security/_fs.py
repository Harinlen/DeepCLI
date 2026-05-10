"""Filesystem helper shared by the token and password modules.

The auth subsystem writes two kinds of secrets to disk (the kernel
auth token and the scrypt password hash).  Both need exactly the
same write discipline:

- the resulting file must be mode ``0o600`` from the moment its
  bytes hit the disk, never "created with the umask default and
  then ``chmod``-ed" — the latter has a window where another local
  process could slurp the file;
- the replacement must be atomic, so a crash mid-write leaves
  either the previous file or no file at all, never a truncated
  one;
- a leftover ``.tmp`` from a prior crash must not block the next
  rewrite forever.

Pulling this into one helper means a bug fix (e.g. on Windows, or
a change to ``fsync`` behavior) happens in exactly one place and
cannot drift between the two callers.  Both callers are in the
auth package, so the helper is deliberately private — no
downstream subsystem should need to write sensitive state files
without going through its own write path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_0600(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically, mode ``0o600``.

    The sequence:

    1. Ensure the parent directory exists.  We create it with mode
       ``0o700`` so a fresh ``~/.mustang/state/`` is never
       world-readable even for a single syscall.  An existing
       directory is trusted as-is so we do not clobber an
       operator's deliberate choice.
    2. Create a sibling ``<path>.tmp`` with
       ``O_CREAT | O_EXCL | O_WRONLY`` and explicit mode ``0o600``
       so the bytes never exist on disk with a wider mode.  The
       explicit mode is applied by the kernel before any data is
       written, and the umask does not affect us because we pass
       the mode directly to ``os.open``.
    3. Write ``content``, flush, and ``fsync`` so the bytes reach
       stable storage before we rename over the target.
    4. :func:`os.replace` to swing the final name atomically.  On
       POSIX this swaps inodes in a single step so concurrent
       readers see either the old contents or the new, never a
       truncated in-between.

    A leftover ``<path>.tmp`` from a prior crash is removed before
    the new write begins — refusing to reuse the name via
    ``O_EXCL`` would otherwise wedge the daemon until a human
    noticed.  If the write itself fails mid-way we clean up the
    ``.tmp`` so the next attempt starts from a known-clean state.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    if tmp_path.exists():
        logger.warning("auth: stale %s found — removing before rewrite", tmp_path)
        tmp_path.unlink()

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(tmp_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        # Best-effort cleanup so we don't leave a half-written tmp
        # around for the next startup to trip over.
        tmp_path.unlink(missing_ok=True)
        raise

    os.replace(tmp_path, path)
