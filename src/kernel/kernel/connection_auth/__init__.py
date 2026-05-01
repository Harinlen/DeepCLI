"""Auth subsystem — connection-level authentication (AuthN).

Public surface:

- :class:`ConnectionAuthenticator` — the subsystem itself, loaded by
  the kernel lifespan.  Exposes
  :meth:`~ConnectionAuthenticator.authenticate` as the single entry
  point for transport-layer verification.
- :class:`AuthContext` — immutable identity descriptor produced on
  successful authentication and flowed down to protocol / session
  layers.
- :class:`AuthError` — the only exception type raised by
  :meth:`ConnectionAuthenticator.authenticate`.

Scope is deliberately narrow: this subsystem answers **"who is this
connection?"** and nothing else.  Provider API keys, MCP OAuth, and
per-tool permission decisions live in ``CredentialStore`` /
``ToolAuthorizer`` / future MCP OAuth — see
``docs/kernel/subsystems/connection_authenticator.md`` and
``docs/architecture/decisions.md`` D22 for the split.

ConnectionAuthenticator currently has no user-configurable settings
and does not bind any ConfigManager section — the listening port is
a process-level CLI argument (``python -m kernel --port N``), not an
auth concern, and everything else (token / password hash) lives in
``~/.mustang/state/`` under the kernel's runtime state dir.

Module internals (``token``, ``password``, ``connection_authenticator``)
are not re-exported: callers should always go through
:class:`ConnectionAuthenticator` so on-disk state and in-memory
caches never drift.
"""

from __future__ import annotations

from kernel.connection_auth.connection_authenticator import (
    AuthError,
    ConnectionAuthenticator,
)
from kernel.connection_auth.context import AuthContext

__all__ = [
    "AuthContext",
    "AuthError",
    "ConnectionAuthenticator",
]
