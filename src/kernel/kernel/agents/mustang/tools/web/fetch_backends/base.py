"""FetchBackend ABC and FetchResult dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Normalised result from any fetch backend."""

    url: str
    """Final URL (after redirects)."""

    content: str
    """Extracted text / Markdown."""

    content_type: str
    """Original Content-Type header value."""

    title: str = ""
    status_code: int = 200
    error: str | None = None
    truncated: bool = False
    raw_length: int = 0
    cached: bool = False
    metadata: dict[str, Any] | None = None


class FetchBackend(ABC):
    """Every fetch backend implements this interface."""

    name: str

    @abstractmethod
    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        """Fetch *url* and return extracted content."""

    def is_available(self) -> bool:
        """Return True if this backend can be used right now."""
        return True


__all__ = ["FetchBackend", "FetchResult"]
