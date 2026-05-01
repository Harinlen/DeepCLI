"""SessionGrantCache — per-session ``allow_always`` memory.

Scope is strictly per-session + in-memory; sub-agents get a fresh empty
cache (aligned with Claude Code ``runAgent.ts:470-479``).  Cross-session
persistence is **not** provided — users who want a grant to outlive the
session write a corresponding rule into ``config.yaml``, going through
``RuleStore`` instead.

Signature algorithm (§11.3):

    sha256(f"{tool.name}:{canonical_json(input)}").hexdigest()

Exact-match only — two Bash calls that differ by a single flag produce
different signatures, each requiring its own allow_always.  Aligned
with CC's "exact command string" policy.
"""

from __future__ import annotations

import hashlib
import orjson
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kernel.tools.tool import Tool


@dataclass(frozen=True)
class GrantEntry:
    """One cached ``allow_always`` grant."""

    signature: str
    granted_at: datetime


class SessionGrantCache:
    """In-memory per-session cache.

    Keyed by ``session_id``; each session holds a set of
    ``signature -> GrantEntry`` mappings.  Thread-safe for use from the
    async event loop + the occasional lifespan teardown.
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, GrantEntry]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_session_open(self, session_id: str) -> None:
        with self._lock:
            self._cache.setdefault(session_id, {})

    def on_session_close(self, session_id: str) -> None:
        with self._lock:
            self._cache.pop(session_id, None)

    # ------------------------------------------------------------------
    # Grant / query
    # ------------------------------------------------------------------

    def grant(self, *, session_id: str, tool: Tool, tool_input: dict[str, Any]) -> GrantEntry:
        """Store a grant for this specific ``(tool, input)`` pair.

        Destructive guard is *not* checked here — that protection lives
        at the ``PermissionAsk.suggestions`` construction step (see
        ``docs/plans/landed/tool-authorizer.md`` § 3.3 and § 11.2).
        If an ``allow_always`` response reached us, the UI must have
        offered the button, so the tool is by definition non-destructive.
        """
        signature = compute_signature(tool, tool_input)
        entry = GrantEntry(signature=signature, granted_at=datetime.now(timezone.utc))
        with self._lock:
            bucket = self._cache.setdefault(session_id, {})
            bucket[signature] = entry
        return entry

    def check(
        self, *, session_id: str, tool: Tool, tool_input: dict[str, Any]
    ) -> GrantEntry | None:
        """Return a cached grant for this call or ``None``."""
        signature = compute_signature(tool, tool_input)
        with self._lock:
            bucket = self._cache.get(session_id)
            if bucket is None:
                return None
            return bucket.get(signature)

    def clear(self) -> None:
        """Drop all grants across all sessions (test helper)."""
        with self._lock:
            self._cache.clear()


def compute_signature(tool: Tool, tool_input: dict[str, Any]) -> str:
    """Canonicalize + hash a ``(tool, input)`` pair.

    Uses ``orjson.dumps(..., option=OPT_SORT_KEYS)`` to get a stable
    canonical form regardless of dict iteration order.  orjson always
    produces compact output (no whitespace).
    Non-serialisable values are replaced with ``repr(...)`` — caller
    inputs are expected to be JSON-serialisable (LLM output is JSON),
    so this is a defensive fallback, not a common path.
    """
    try:
        canonical = orjson.dumps(tool_input, option=orjson.OPT_SORT_KEYS)
    except (TypeError, orjson.JSONEncodeError):
        canonical = repr(sorted(tool_input.items())).encode("utf-8")
    payload = tool.name.encode("utf-8") + b":" + canonical
    return hashlib.sha256(payload).hexdigest()


__all__ = ["GrantEntry", "SessionGrantCache", "compute_signature"]
