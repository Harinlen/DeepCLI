"""Web search backends — pluggable implementations for ``web_search``.

Built-in options:

- ``brave`` — Brave Search API (requires ``BRAVE_API_KEY``).
- ``google`` — Google Custom Search JSON API (requires
  ``GOOGLE_API_KEY`` + ``GOOGLE_CSE_ID``).
- ``duckduckgo`` — HTML scrape of DuckDuckGo's lite HTML UI; no
  API key but results are less structured and may break when DDG
  changes its page.

The active backend is chosen via the ``tools.web_search.backend``
config field.  When unset, we use DuckDuckGo by default.
"""

from daemon.extensions.tools.web_backends.base import SearchBackend, SearchResult
from daemon.extensions.tools.web_backends.brave import BraveBackend
from daemon.extensions.tools.web_backends.duckduckgo import DuckDuckGoBackend
from daemon.extensions.tools.web_backends.google import GoogleBackend
from daemon.extensions.tools.web_backends.selector import select_backend

__all__ = [
    "BraveBackend",
    "DuckDuckGoBackend",
    "GoogleBackend",
    "SearchBackend",
    "SearchResult",
    "select_backend",
]
