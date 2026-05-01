"""Protocol-layer error hierarchy.

All error codes are taken verbatim from the ACP ``ErrorCode`` enum
(``references/acp/protocol/schema.md``).  We do **not** invent new
numeric codes — if a situation doesn't map to one of these, it falls
through to :data:`INTERNAL_ERROR` (-32603).

The ``ProtocolError`` base class is already declared in
:mod:`kernel.routes.stack` (used by the transport loop).  The
subclasses here extend it with a ``code`` attribute so the ACP codec
can serialise them into JSON-RPC error frames without knowing the
specific exception type.
"""

from __future__ import annotations

from kernel.routes.stack import ProtocolError

# ---------------------------------------------------------------------------
# Standard JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------

PARSE_ERROR: int = -32700
"""JSON could not be parsed at all."""

INVALID_REQUEST: int = -32600
"""Structurally invalid JSON-RPC frame, or a method sent before
``initialize`` completed."""

METHOD_NOT_FOUND: int = -32601
"""Method name not in REQUEST_DISPATCH or NOTIFICATION_DISPATCH."""

INVALID_PARAMS: int = -32602
"""Params failed Pydantic validation for the target method."""

INTERNAL_ERROR: int = -32603
"""Catch-all for unexpected server-side failures.  The specific cause
is logged at ERROR level but never included in the wire response."""

# ---------------------------------------------------------------------------
# ACP-specific error codes
# ---------------------------------------------------------------------------

RESOURCE_NOT_FOUND: int = -32002
"""ACP: session / tool / resource does not exist."""

REQUEST_CANCELLED: int = -32800
"""RFD: a JSON-RPC request was cancelled via ``$/cancel_request``."""


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class ParseError(ProtocolError):
    """Raised when the incoming frame is not valid JSON."""

    code: int = PARSE_ERROR


class InvalidRequest(ProtocolError):
    """Raised for structurally invalid requests or premature method calls."""

    code: int = INVALID_REQUEST


class MethodNotFound(ProtocolError):
    """Raised when the method string is not in any dispatch table."""

    code: int = METHOD_NOT_FOUND


class InvalidParams(ProtocolError):
    """Raised when Pydantic validation of ``params`` fails.

    The original ``ValidationError`` message is intentionally **not**
    forwarded to the client — it may contain raw param values.  Only
    a generic description is sent on the wire; the full error is logged
    at DEBUG level for operator use.
    """

    code: int = INVALID_PARAMS


class InternalError(ProtocolError):
    """Catch-all for unexpected failures in the protocol or session layer."""

    code: int = INTERNAL_ERROR


class ResourceNotFoundError(ProtocolError):
    """Raised when a session / tool / resource ID is unknown."""

    code: int = RESOURCE_NOT_FOUND


class RequestCancelled(ProtocolError):
    """Raised when a JSON-RPC request is cancelled."""

    code: int = REQUEST_CANCELLED


__all__ = [
    # constants
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "RESOURCE_NOT_FOUND",
    "REQUEST_CANCELLED",
    # exceptions
    "InternalError",
    "InvalidParams",
    "InvalidRequest",
    "MethodNotFound",
    "ParseError",
    "ProtocolError",
    "ResourceNotFoundError",
    "RequestCancelled",
]
