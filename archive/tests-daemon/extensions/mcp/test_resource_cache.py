"""Tests for the MCP resource cache."""

from __future__ import annotations

from unittest.mock import patch

from daemon.extensions.mcp.resource_cache import ResourceCache


class TestResourceCache:
    """Tests for ResourceCache LRU + TTL behavior."""

    def test_put_and_get(self) -> None:
        cache = ResourceCache()
        cache.put("file://a.txt", "hello")

        entry = cache.get("file://a.txt")
        assert entry is not None
        assert entry.content == "hello"
        assert entry.uri == "file://a.txt"

    def test_get_miss(self) -> None:
        cache = ResourceCache()
        assert cache.get("file://missing") is None

    def test_ttl_expiry(self) -> None:
        """Expired entries return None and are removed."""
        cache = ResourceCache(default_ttl=0.01)
        cache.put("file://a.txt", "old")

        # Simulate time passing
        entry = cache._cache["file://a.txt"]
        with patch("time.monotonic", return_value=entry.fetched_at + 1.0):
            assert cache.get("file://a.txt") is None
        assert len(cache) == 0

    def test_lru_eviction(self) -> None:
        """Oldest entries are evicted when at capacity."""
        cache = ResourceCache(max_entries=3)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")
        assert len(cache) == 3

        # Adding a 4th should evict "a" (oldest)
        cache.put("d", "4")
        assert len(cache) == 3
        assert cache.get("a") is None
        assert cache.get("b") is not None

    def test_lru_access_order(self) -> None:
        """Accessing an entry moves it to most-recently-used."""
        cache = ResourceCache(max_entries=3)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")

        # Access "a" — makes it most recently used
        cache.get("a")

        # Adding "d" should evict "b" (now oldest)
        cache.put("d", "4")
        assert cache.get("a") is not None
        assert cache.get("b") is None

    def test_put_replaces_existing(self) -> None:
        """Re-putting same URI replaces the entry."""
        cache = ResourceCache()
        cache.put("file://a", "old")
        cache.put("file://a", "new")

        entry = cache.get("file://a")
        assert entry is not None
        assert entry.content == "new"
        assert len(cache) == 1

    def test_invalidate(self) -> None:
        cache = ResourceCache()
        cache.put("file://a", "hello")
        assert cache.invalidate("file://a") is True
        assert cache.get("file://a") is None
        assert cache.invalidate("file://a") is False

    def test_clear(self) -> None:
        cache = ResourceCache()
        cache.put("a", "1")
        cache.put("b", "2")
        cache.clear()
        assert len(cache) == 0

    def test_mime_type(self) -> None:
        cache = ResourceCache()
        cache.put("file://a", "text", mime_type="text/plain")
        entry = cache.get("file://a")
        assert entry is not None
        assert entry.mime_type == "text/plain"

    def test_custom_ttl(self) -> None:
        cache = ResourceCache(default_ttl=300.0)
        cache.put("file://a", "text", ttl=1.0)
        entry = cache.get("file://a")
        assert entry is not None
        assert entry.ttl == 1.0
