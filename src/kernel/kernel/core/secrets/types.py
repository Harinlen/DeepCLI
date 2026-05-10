"""Secret storage types — error hierarchy and data classes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class SecretError(Exception):
    """Base class for SecretManager errors."""


class SecretNotFoundError(SecretError):
    """Referenced secret does not exist in the store."""


class SecretDatabaseError(SecretError):
    """Database corruption or I/O error."""


# ---------------------------------------------------------------------------
# OAuth token bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthToken:
    """Full OAuth token bundle for an MCP server.

    Stored across two tables: ``secrets`` (access_token as ``value``)
    and ``oauth_tokens`` (refresh/expiry/client_config metadata).
    """

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    client_config: dict[str, Any] = field(default_factory=dict)
