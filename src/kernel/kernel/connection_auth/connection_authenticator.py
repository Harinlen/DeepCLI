"""ConnectionAuthenticator — the kernel's connection-auth entry point.

Every WebSocket / HTTP request arriving at the kernel is validated
here before it ever reaches the protocol layer.  The design
rationale lives in ``docs/kernel/subsystems/connection_authenticator.md``;
this module only implements it.  The short version:

- Kernel always binds ``127.0.0.1`` → any connecting socket is
  either a local client or a local reverse proxy.
- Two credential types: ``token`` (local file, proves filesystem
  access on the same machine) and ``password`` (scrypt hash,
  manually set by the operator, used by remote clients through a
  reverse proxy).
- Verification is transport-agnostic: callers pass in the raw
  credential and its type, we decide whether it matches what we
  have on disk.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from kernel.connection_auth._fs import atomic_write_0600
from kernel.connection_auth.context import AuthContext
from kernel.connection_auth.password import (
    delete_hash,
    hash_password,
    load_hash,
    verify_password,
)
from kernel.connection_auth.token import generate_token, load_or_create_token
from kernel.module_table import KernelModuleTable
from kernel.subsystem import Subsystem

logger = logging.getLogger(__name__)

_TOKEN_FILENAME = "auth_token"
_PASSWORD_FILENAME = "auth_password.hash"


class AuthError(Exception):
    """Authentication failed.

    Raised by :meth:`ConnectionAuthenticator.authenticate` for every failure
    mode — invalid credential, unsupported credential type, or
    password auth disabled while a password credential was supplied.
    The message is a fixed ``"authentication failed"`` string;
    callers must not parse it, and leaking specific reasons back to
    the client is an information-disclosure vector we deliberately
    avoid.  Actual reasons are logged at ``debug`` level inside the
    manager and never reach the exception surface.
    """

    def __init__(self) -> None:
        super().__init__("authentication failed")


class ConnectionAuthenticator(Subsystem):
    """Connection-level authentication interface (AuthN).

    Scope is deliberately narrow: decide **who** a freshly accepted
    WebSocket / HTTP connection is.  Provider API keys, MCP OAuth,
    and per-tool authorization all live in other subsystems
    (``CredentialStore``, ``ToolAuthorizer``) — this class only
    owns the transport → protocol boundary check.

    The transport layer calls :meth:`authenticate` after ``accept``
    and before entering the protocol layer.  Different transports
    extract credentials differently (WebSocket query param, HTTP
    header, ...) but all of them funnel through the same method
    here so verification policy lives in exactly one place.

    The subsystem keeps both the current token and the password
    hash in memory for fast verification.  CLI-triggered rotation
    (:meth:`rotate_token`, :meth:`set_password`,
    :meth:`clear_password`) updates memory and disk together; any
    concurrent verification always sees a consistent state because
    the in-memory value is what ``authenticate`` reads.
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        # Paths are pure computation — no I/O — so resolving them in
        # the constructor keeps ``rotate_token`` / ``set_password``
        # / ``clear_password`` from needing "did startup run?"
        # guards.  The secrets themselves (``_token`` /
        # ``_password_hash``) still come from disk in ``startup``.
        state_dir = module_table.state_dir
        self._token_path: Path = state_dir / _TOKEN_FILENAME
        self._password_path: Path = state_dir / _PASSWORD_FILENAME
        self._token: str | None = None
        self._password_hash: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Load on-disk secrets into memory.

        ConnectionAuthenticator does **not** bind any ConfigManager section: it
        has no user-tunable knobs at the moment, and the listening
        port belongs to the process-startup layer, not to "who can
        connect".  If a real auth config field shows up later
        (session timeout, rate limit, ...) this is where the
        ``bind_section`` call would be introduced.
        """
        self._token = load_or_create_token(self._token_path)
        self._password_hash = load_hash(self._password_path)

        logger.info(
            "ConnectionAuthenticator started: password_auth=%s",
            "enabled" if self._password_hash is not None else "disabled",
        )

    async def shutdown(self) -> None:
        """No-op — every mutation persists synchronously.

        Token / password writes go through ``os.replace`` during
        ``rotate_token`` / ``set_password`` / ``clear_password``,
        so nothing is left in memory to flush.  Shutdown only logs
        that the subsystem is down.
        """
        logger.info("ConnectionAuthenticator: shutdown complete")

    # ------------------------------------------------------------------
    # Primary API — transport → protocol boundary
    # ------------------------------------------------------------------

    async def authenticate(
        self,
        *,
        connection_id: str,
        credential: str,
        credential_type: Literal["token", "password"],
        remote_addr: str,
    ) -> AuthContext:
        """Verify a credential and produce an :class:`AuthContext`.

        Parameters
        ----------
        connection_id:
            Opaque id generated by the transport layer when the
            socket was accepted — typically ``uuid4().hex``.  We do
            **not** generate it here because the same id needs to
            appear in transport-side logs from before authentication
            succeeded; requiring it as an input keeps the
            correlation unambiguous.
        credential:
            The raw token or password as supplied by the client.
            Never stored on the returned context, never logged.
        credential_type:
            Which check to run.  ``"token"`` compares against the
            in-memory token with :func:`secrets.compare_digest`;
            ``"password"`` re-hashes with the parameters recorded
            in the stored hash and compares derived keys.
        remote_addr:
            ``host:port`` of the peer, from the transport layer.
            Recorded on the returned context for diagnostic logging
            only — it is **not** a locality signal, see the
            subsystem doc for why.

        Returns
        -------
        AuthContext
            Frozen identity descriptor bound to this connection.

        Raises
        ------
        AuthError
            On any verification failure.  The exception message is
            the fixed string ``"authentication failed"``; specific
            reasons are only written to the debug log.
        """
        if credential_type == "token":
            # ``_token is None`` would mean ``authenticate`` was
            # called before ``startup`` finished — a lifecycle bug,
            # never a legitimate runtime state.  Assert so the bug
            # surfaces loudly instead of masquerading as "everyone's
            # token is wrong" in operator dashboards.
            assert self._token is not None, (
                "ConnectionAuthenticator.authenticate called before startup()"
            )
            ok = secrets.compare_digest(credential, self._token)
        elif credential_type == "password":
            # ``_password_hash is None`` is a legitimate runtime
            # state — it simply means password auth is disabled on
            # this kernel — so we treat it as "credential rejected"
            # rather than a bug.  Operators see the distinction in
            # debug logs.
            if self._password_hash is None:
                logger.debug("auth: password credential but password auth disabled")
                ok = False
            else:
                ok = verify_password(credential, self._password_hash)
        else:
            # Static typing keeps this branch unreachable via the
            # declared Literal, but transports are untrusted input
            # and might pass whatever string arrives on the wire.
            logger.debug(
                "auth: unknown credential_type=%r from %s",
                credential_type,
                remote_addr,
            )
            ok = False

        if not ok:
            raise AuthError()

        return AuthContext(
            connection_id=connection_id,
            credential_type=credential_type,
            remote_addr=remote_addr,
            authenticated_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def has_password(self) -> bool:
        """Return ``True`` iff a password hash is currently loaded.

        Used by the ``/`` root HTTP endpoint to advertise which
        credential types this kernel instance accepts, so clients
        that cannot read the local token file know whether asking
        the user for a password has any chance of working.
        """
        return self._password_hash is not None

    # ------------------------------------------------------------------
    # CLI-triggered mutations
    # ------------------------------------------------------------------

    def rotate_token(self) -> None:
        """Generate a fresh token, persist it, update memory.

        Existing WebSocket connections are **not** disconnected —
        this is a deliberate choice documented in the subsystem
        spec.  Their sockets remain live; only their cached token
        string is now stale, so their *next* connect attempt will
        fail until they re-read the file.
        """
        new_token = generate_token()
        atomic_write_0600(self._token_path, new_token)
        self._token = new_token
        logger.info("auth: token rotated")

    def set_password(self, plaintext: str) -> None:
        """Hash ``plaintext`` and persist it, enabling password auth.

        Overwrites any existing hash atomically.  The plaintext is
        consumed locally here and not propagated — the caller
        (typically the ``mustang auth set-password`` CLI command)
        is the only layer that ever sees it.
        """
        serialized = hash_password(plaintext)
        atomic_write_0600(self._password_path, serialized)
        self._password_hash = serialized
        logger.info("auth: password set (hash persisted)")

    def clear_password(self) -> None:
        """Remove the password hash, disabling password auth.

        Safe to call when no password is currently set — the file
        deletion is idempotent and the in-memory state is already
        ``None``.
        """
        delete_hash(self._password_path)
        self._password_hash = None
        logger.info("auth: password cleared")
