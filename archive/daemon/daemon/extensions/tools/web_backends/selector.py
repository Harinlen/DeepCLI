"""Backend selection for ``web_search``."""

from __future__ import annotations

import logging

from daemon.extensions.tools.web_backends.base import SearchBackend
from daemon.extensions.tools.web_backends.brave import BraveBackend
from daemon.extensions.tools.web_backends.duckduckgo import DuckDuckGoBackend
from daemon.extensions.tools.web_backends.google import GoogleBackend

logger = logging.getLogger(__name__)


def select_backend(
    preferred: str | None,
    brave_api_key: str | None,
    *,
    google_api_key: str | None = None,
    google_cse_id: str | None = None,
) -> SearchBackend | None:
    """Choose a backend given preference + available credentials."""
    preferred_lower = (preferred or "").strip().lower()

    if preferred_lower == "brave":
        if not brave_api_key:
            logger.warning("web_search backend 'brave' requested but BRAVE_API_KEY is missing")
            return None
        return BraveBackend(brave_api_key)
    if preferred_lower == "google":
        if not google_api_key:
            logger.warning("web_search backend 'google' requested but GOOGLE_API_KEY is missing")
            return None
        if not google_cse_id:
            logger.warning("web_search backend 'google' requested but GOOGLE_CSE_ID is missing")
            return None
        return GoogleBackend(google_api_key, google_cse_id)
    if preferred_lower == "duckduckgo":
        return DuckDuckGoBackend()

    # Auto: prefer DuckDuckGo first.
    return DuckDuckGoBackend()
