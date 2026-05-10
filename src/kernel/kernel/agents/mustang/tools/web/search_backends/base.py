"""SearchBackend ABC and SearchResult dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One search hit returned by a backend."""

    title: str
    url: str
    snippet: str


class SearchBackend(ABC):
    """Every search backend implements this interface."""

    name: str

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """Search for *query* and return up to *limit* results."""

    def is_available(self) -> bool:
        """Return True if this backend can be used right now."""
        return True


__all__ = ["SearchBackend", "SearchResult"]
