"""File-based local authentication.

Prevents other users on the same machine from connecting to the daemon.
On startup the daemon writes a random token to
``~/.mustang/.auth_token`` (mode 0600).  Clients must present this
token when connecting via WebSocket.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

AUTH_DIR = Path.home() / ".mustang"
AUTH_TOKEN_PATH = AUTH_DIR / ".auth_token"

_TOKEN_BYTES = 32  # 256-bit random token


def ensure_auth_token() -> str:
    """Return the existing auth token, or generate a new one if missing.

    Reuses the token across daemon restarts so clients don't need to
    re-read the file every time.  The token file is created with mode
    ``0600`` so only the current user can read it.
    """
    existing = load_auth_token()
    if existing:
        logger.info("Reusing auth token from %s", AUTH_TOKEN_PATH)
        return existing

    AUTH_DIR.mkdir(parents=True, exist_ok=True)

    token = secrets.token_urlsafe(_TOKEN_BYTES)

    AUTH_TOKEN_PATH.write_text(token, encoding="utf-8")
    AUTH_TOKEN_PATH.chmod(0o600)

    logger.info("Auth token written to %s", AUTH_TOKEN_PATH)
    return token


def load_auth_token() -> str | None:
    """Read the auth token from disk, or ``None`` if missing."""
    if AUTH_TOKEN_PATH.is_file():
        return AUTH_TOKEN_PATH.read_text(encoding="utf-8").strip()
    return None


def verify_token(presented: str, expected: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    return secrets.compare_digest(presented, expected)


def cleanup_auth_token() -> None:
    """Remove the auth token file.

    Only called explicitly (e.g. ``mustang daemon logout``), NOT on
    normal shutdown — the token persists across restarts so clients
    don't break.
    """
    try:
        AUTH_TOKEN_PATH.unlink(missing_ok=True)
        logger.info("Auth token cleaned up")
    except OSError as exc:
        logger.warning("Failed to remove auth token: %s", exc)
