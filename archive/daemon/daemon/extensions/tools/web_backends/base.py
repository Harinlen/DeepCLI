"""Base types and shared constants for ``web_search`` backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

_USER_AGENT = "Mustang/0.1 (+https://github.com/haoleiye/mustang)"
_TIMEOUT_SECS = 10.0


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One search hit returned by a backend."""

    title: str
    url: str
    snippet: str


class SearchBackend(ABC):
    """Abstract base class for a web-search provider."""

    name: str

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]: ...

