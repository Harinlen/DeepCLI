"""LRU + TTL cache for MCP resources.

Per-server cache that stores resource contents fetched via the MCP
Resources protocol (``resources/read``).  Avoids repeated requests
for the same resource URI within the TTL window.

Content is always stored as ``str`` — binary resources are expected
to be base64-encoded by the caller before caching.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field

# Default TTL for cached resources (seconds).
DEFAULT_RESOURCE_TTL = 300.0  # 5 minutes

# Default maximum number of cached entries per server.
DEFAULT_MAX_ENTRIES = 200


@dataclass
class CachedResource:
    """A single cached resource entry.

    Attributes:
        uri: Resource URI as reported by the server.
        content: Resource text content (binary → base64).
        mime_type: MIME type if known, else ``None``.
        fetched_at: Monotonic timestamp of the fetch.
        ttl: Time-to-live in seconds.
    """

    uri: str
    content: str
    mime_type: str | None = None
    fetched_at: float = field(default_factory=time.monotonic)
    ttl: float = DEFAULT_RESOURCE_TTL


class ResourceCache:
    """LRU + TTL resource cache for a single MCP server.

    Entries are evicted when they exceed *max_entries* (LRU order)
    or when their TTL has elapsed (checked on ``get``).

    Args:
        max_entries: Maximum number of cached resources.
        default_ttl: Default TTL for new entries (seconds).
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        default_ttl: float = DEFAULT_RESOURCE_TTL,
    ) -> None:
        self._cache: OrderedDict[str, CachedResource] = OrderedDict()
        self._max_entries = max_entries
        self._default_ttl = default_ttl

    def get(self, uri: str) -> CachedResource | None:
        """Retrieve a cached resource if it exists and is fresh.

        Args:
            uri: Resource URI to look up.

        Returns:
            The cached entry, or ``None`` if missing or expired.
        """
        entry = self._cache.get(uri)
        if entry is None:
            return None

        if (time.monotonic() - entry.fetched_at) >= entry.ttl:
            del self._cache[uri]
            return None

        # Move to end (most recently used)
        self._cache.move_to_end(uri)
        return entry

    def put(
        self,
        uri: str,
        content: str,
        mime_type: str | None = None,
        ttl: float | None = None,
    ) -> None:
        """Store a resource in the cache.

        If the cache is full, the least-recently-used entry is
        evicted first.

        Args:
            uri: Resource URI.
            content: Resource text content.
            mime_type: Optional MIME type.
            ttl: Optional TTL override (defaults to *default_ttl*).
        """
        # Remove existing entry to reset position
        self._cache.pop(uri, None)

        # Evict LRU if at capacity
        while len(self._cache) >= self._max_entries:
            self._cache.popitem(last=False)

        self._cache[uri] = CachedResource(
            uri=uri,
            content=content,
            mime_type=mime_type,
            fetched_at=time.monotonic(),
            ttl=ttl if ttl is not None else self._default_ttl,
        )

    def invalidate(self, uri: str) -> bool:
        """Remove a specific entry from the cache.

        Returns:
            ``True`` if the entry existed and was removed.
        """
        try:
            del self._cache[uri]
            return True
        except KeyError:
            return False

    def clear(self) -> None:
        """Remove all entries."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


__all__ = ["CachedResource", "ResourceCache"]
