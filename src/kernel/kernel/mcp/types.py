"""MCP type definitions — connection states, tool/resource schemas, errors.

Mirrors Claude Code ``services/mcp/types.ts``.  The five-state
connection union (Connected / Failed / NeedsAuth / Pending / Disabled)
drives MCPManager's internal bookkeeping; ToolManager and the rest of
the kernel only see ``ConnectedServer`` via the public API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kernel.mcp.client import McpClient


# ── Connection state machine ────────────────────────────────────────
#
#              connect
#   ┌────────► Pending ◄──── reconnect
#   │          ┌──┴──┐
#   │     success    fail / auth
#   │      ▼           ▼         ▼
#  Connected      Failed     NeedsAuth
#   │              │
#   │ error        │ health check → reconnect
#   └──► Failed ───┘
#
#   Disabled  (policy-gated, no action)


@dataclass(frozen=True)
class ConnectedServer:
    """Active MCP server connection.

    Attributes:
        name: Server identifier from config.
        client: Live JSON-RPC session.
        capabilities: Server-declared capabilities from ``initialize``.
        server_info: Optional ``{name, version}`` from the server.
        instructions: Server-provided instructions metadata.
    """

    name: str
    client: McpClient
    capabilities: dict[str, Any] = field(default_factory=dict)
    server_info: dict[str, Any] | None = None
    instructions: str | None = None


@dataclass(frozen=True)
class FailedServer:
    """Connection attempt failed.

    Attributes:
        name: Server identifier.
        error: Human-readable failure description.
    """

    name: str
    error: str = ""


@dataclass(frozen=True)
class PendingServer:
    """Reconnection in progress.

    Attributes:
        name: Server identifier.
        reconnect_attempt: Current attempt number (1-based).
        max_reconnect_attempts: Upper bound before giving up.
    """

    name: str
    reconnect_attempt: int = 0
    max_reconnect_attempts: int = 5


@dataclass(frozen=True)
class NeedsAuthServer:
    """Awaiting user authorization (e.g. OAuth consent).

    Attributes:
        name: Server identifier.
        server_url: Remote URL for OAuth discovery.
    """

    name: str
    server_url: str = ""


@dataclass(frozen=True)
class DisabledServer:
    """Server disabled by policy (allowed/denied rules).

    Attributes:
        name: Server identifier.
    """

    name: str


MCPServerConnection = (
    ConnectedServer | FailedServer | PendingServer | NeedsAuthServer | DisabledServer
)
"""Union of all connection states.  Mirrors CC ``MCPServerConnection``."""


# ── MCP tool / resource definitions ─────────────────────────────────


@dataclass(frozen=True)
class McpToolDef:
    """Tool definition returned by ``tools/list``.

    Attributes:
        name: Original tool name from the MCP server (not prefixed).
        description: Human-readable description (may be long).
        input_schema: JSON Schema for the tool's input.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpToolResult:
    """Result from ``tools/call``.

    Attributes:
        content: List of content blocks (text, image, resource).
        is_error: Whether the server flagged this as an error result.
        meta: Optional ``_meta`` dict from the server response.
    """

    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class McpResourceDef:
    """Resource definition returned by ``resources/list``.

    Attributes:
        uri: Resource URI.
        name: Human-readable name.
        description: Optional description.
        mime_type: Optional MIME type.
    """

    uri: str
    name: str = ""
    description: str = ""
    mime_type: str | None = None


@dataclass(frozen=True)
class McpResourceResult:
    """Result from ``resources/read``.

    Attributes:
        contents: List of content entries (text or blob).
    """

    contents: list[dict[str, Any]] = field(default_factory=list)


# ── Errors ───────────────────────────────────────────────────────────


class McpError(Exception):
    """Base MCP protocol error.

    Attributes:
        code: JSON-RPC error code (``None`` for non-protocol errors).
        message: Human-readable error description.
    """

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TransportClosed(Exception):
    """Transport EOF or connection reset — unrecoverable at transport level."""


class McpAuthError(McpError):
    """Server returned 401 — credentials missing or expired.

    Attributes:
        server_name: Which server rejected the request.
        challenge: Value of the ``WWW-Authenticate`` header, if present.
    """

    def __init__(
        self,
        server_name: str,
        message: str = "authentication required",
        *,
        challenge: str | None = None,
    ) -> None:
        super().__init__(message)
        self.server_name = server_name
        self.challenge = challenge


class McpSessionExpiredError(McpError):
    """Server returned 404 + JSON-RPC code -32001 (session not found).

    Mirrors CC's ``isMcpSessionExpiredError()`` detection:
    HTTP 404 combined with JSON-RPC error code -32001 signals that
    the server dropped the session and the client must reconnect
    with a fresh session ID.
    """

    #: The JSON-RPC error code that MCP servers use for session expiry.
    SESSION_EXPIRED_CODE: int = -32001

    def __init__(self, server_name: str) -> None:
        super().__init__(
            f"MCP session expired on {server_name!r}",
            code=self.SESSION_EXPIRED_CODE,
        )
        self.server_name = server_name


class McpToolCallError(McpError):
    """Tool call returned ``isError: true`` in the result.

    Attributes:
        tool_name: Which tool failed.
        meta: Optional ``_meta`` dict from the error response.
    """

    def __init__(
        self,
        tool_name: str,
        message: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.meta = meta
