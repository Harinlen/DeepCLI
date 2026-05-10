"""Domain-level security filter for outbound HTTP tools.

Blocks requests to domains that should never be fetched by an LLM
tool — internal metadata endpoints, loopback addresses, embedded
credentials, embedded API keys, and an operator-maintained blocklist.

Migrated from ``archive/daemon/daemon/extensions/tools/domain_filter.py``,
enhanced with credential and API-key detection from Claude Code and Hermes.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Operator-maintained blocklist — empty by default.
_BLOCKED_DOMAINS: set[str] = set()

# Regex for common API key prefixes (from Hermes _PREFIX_RE pattern).
# Two patterns:
#   1. Param names like api_key=, token=, secret= followed by a long value
#   2. Key prefixes like sk-, pk- appearing as values (e.g. =sk-abc123...)
_API_KEY_PARAM_RE = re.compile(
    r"(?:api[_-]?key|token|secret|password|bearer)[=:]\s*\S{8,}",
    re.IGNORECASE,
)
_API_KEY_PREFIX_RE = re.compile(
    r"(?:^|[=&?/])(sk-|pk-)\S{8,}",
    re.IGNORECASE,
)


def add_blocked_domain(domain: str) -> None:
    """Add a domain (case-insensitive) to the blocklist."""
    _BLOCKED_DOMAINS.add(domain.lower().strip())


def remove_blocked_domain(domain: str) -> None:
    """Remove a domain from the blocklist (no-op if absent)."""
    _BLOCKED_DOMAINS.discard(domain.lower().strip())


def get_blocked_domains() -> frozenset[str]:
    """Return the current blocklist snapshot (read-only)."""
    return frozenset(_BLOCKED_DOMAINS)


def check_domain(url: str) -> str | None:
    """Validate *url* against security rules.

    Returns an error message string when the request should be
    blocked, or ``None`` when the URL is allowed.
    """
    parsed = urlparse(url)

    # 1. Scheme check
    if parsed.scheme not in {"http", "https"}:
        return f"Rejected: only http(s) URLs allowed, got {parsed.scheme!r}"

    # 2. Embedded credentials (from Claude Code pattern)
    if parsed.username or parsed.password:
        return "Rejected: URL contains embedded credentials"

    host = (parsed.hostname or "").lower().strip()
    if not host:
        return "Rejected: URL has no host"

    # 3. IP-literal check — prevents SSRF
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback:
            return f"Rejected: loopback address ({host})"
        if addr.is_link_local:
            return f"Rejected: link-local address ({host})"
        if addr.is_private:
            return f"Rejected: private address ({host})"
        if addr.is_reserved:
            return f"Rejected: reserved address ({host})"
    except ValueError:
        pass  # Not an IP literal — that's fine, treat as hostname.

    # 4. Hostname special cases
    if host == "localhost":
        return "Rejected: localhost"

    # 5. Operator blocklist
    if host in _BLOCKED_DOMAINS:
        return f"Rejected: domain {host!r} is blocked"

    # 6. Embedded API key detection (from Hermes)
    decoded = unquote(url)
    if _API_KEY_PARAM_RE.search(decoded) or _API_KEY_PREFIX_RE.search(decoded):
        return "Rejected: URL appears to contain an API key or secret"

    return None


__all__ = [
    "add_blocked_domain",
    "check_domain",
    "get_blocked_domains",
    "remove_blocked_domain",
]
