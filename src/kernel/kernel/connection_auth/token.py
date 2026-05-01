"""Token file I/O — generate and load the kernel auth token.

The whole "is this request from a legitimate local user?" check
hangs on one POSIX guarantee:

    can read ``~/.mustang/state/auth_token`` (mode 0600, owner
    ``$USER``)
        ⇒ same UID as the kernel process
        ⇒ authentic local user

Writing the file with the right mode at the right moment is
delegated to :func:`kernel.connection_auth._fs.atomic_write_0600`, which is
shared with the password hash persistence path.  This module only
owns the "what does a token file look like" concerns: generation
and the load-or-create semantics used at startup.

Token generation uses :func:`secrets.token_urlsafe` with 32
entropy bytes (~256 bits of entropy, ~43 base64 chars).  That is
comfortably beyond any brute force concern even if an attacker
somehow obtained thousands of guesses per second on loopback.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from kernel.connection_auth._fs import atomic_write_0600

logger = logging.getLogger(__name__)


def generate_token() -> str:
    """Return a fresh URL-safe random token.

    Not persisted by this function — callers decide whether to
    write it out.  Kept as a standalone helper because rotation
    (:meth:`kernel.connection_auth.ConnectionAuthenticator.rotate_token`) needs a new
    token without touching disk first.
    """
    # 32 random bytes → 43 characters of urlsafe base64, 256 bits
    # of entropy.  Inline because this is the only caller.
    return secrets.token_urlsafe(32)


def load_or_create_token(path: Path) -> str:
    """Read the token at ``path`` or create it atomically if missing.

    Called from :meth:`kernel.connection_auth.ConnectionAuthenticator.startup`.  The
    behavioral contract is documented in the subsystem spec:

    - existing file → read and return unchanged, so clients with a
      cached token are not invalidated by a plain daemon restart
    - missing file → generate a new token, write it ``0o600``,
      return it

    Returns
    -------
    str
        The token as stored on disk.
    """
    existing = _read_if_present(path)
    if existing is not None:
        return existing

    token = generate_token()
    atomic_write_0600(path, token)
    logger.info("auth: created new token file at %s", path)
    return token


def _read_if_present(path: Path) -> str | None:
    """Return the stripped file contents, or ``None`` if missing.

    A zero-byte file is treated as "not present" — we would rather
    regenerate than authenticate with an empty string.  Any other
    I/O error propagates, because a partially-readable token file
    is a bad enough state that silently regenerating could hide
    filesystem corruption from the operator.
    """
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        logger.warning("auth: token file %s was empty — regenerating", path)
        return None
    return content
