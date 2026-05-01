"""Domain-level security filter for outbound HTTP tools.

Blocks requests to domains that should never be fetched by an LLM
tool — internal metadata endpoints, loopback addresses, and an
operator-maintained blocklist.

The blocklist is intentionally shipped **empty**.  Operators add
entries via :func:`add_blocked_domain` or by editing the module-level
``_BLOCKED_DOMAINS`` set at startup (e.g. from a config file).

Usage::

    from daemon.extensions.tools.domain_filter import check_domain
    if (err := check_domain(url)):
        return ToolResult(output=err, is_error=True)
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# Operator-maintained blocklist — empty by default.
# Call ``add_blocked_domain()`` to populate at runtime.
_BLOCKED_DOMAINS: set[str] = set()


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
    """Validate *url*'s host against security rules.

    Returns an error message string when the request should be
    blocked, or ``None`` when the host is allowed.

    Checks (in order):

    1. **Loopback / link-local / private IPs** — prevents SSRF
       against cloud metadata endpoints (169.254.x.x) and local
       services (127.0.0.1, ::1, 10.x, 192.168.x, etc.).
    2. **Operator blocklist** — exact match on the lowercase
       hostname (no wildcard / glob).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().strip()

    if not host:
        return "Rejected: URL has no host"

    # --- IP-literal check ---
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

    # --- Hostname special cases ---
    if host == "localhost":
        return "Rejected: localhost"

    # --- Operator blocklist ---
    if host in _BLOCKED_DOMAINS:
        return f"Rejected: domain {host!r} is blocked"

    return None


__all__ = [
    "add_blocked_domain",
    "check_domain",
    "get_blocked_domains",
    "remove_blocked_domain",
]
