"""AcpCodec — JSON-RPC 2.0 frame ↔ typed ACP message.

Responsibility boundary
-----------------------
* Parse raw UTF-8 JSON strings into one of three typed message shapes:
  ``AcpInboundRequest``, ``AcpInboundNotification``, ``AcpInboundResponse``.
* Serialise outbound typed messages back to JSON strings.
* Map any parse failure to the appropriate ``ProtocolError`` subclass
  so the transport loop can emit a well-formed JSON-RPC error frame and
  keep the connection open.

This codec is **stateless and pure** — no session state, no in-flight
tracking.  A single instance is safe to share across connections.
"""

from __future__ import annotations

import orjson
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from kernel.protocol.interfaces.errors import (
    InvalidRequest,
    ParseError,
    ProtocolError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Typed inbound message shapes
# ---------------------------------------------------------------------------


@dataclass
class AcpInboundRequest:
    """A JSON-RPC 2.0 request (has ``id`` and ``method``)."""

    id: str | int
    method: str
    params: dict[str, Any]
    meta: dict[str, Any] | None = None


@dataclass
class AcpInboundNotification:
    """A JSON-RPC 2.0 notification (has ``method``, no ``id``)."""

    method: str
    params: dict[str, Any]
    meta: dict[str, Any] | None = None


@dataclass
class AcpInboundResponse:
    """A JSON-RPC 2.0 response (has ``id``, no ``method``)."""

    id: str | int
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


AcpMessage = AcpInboundRequest | AcpInboundNotification | AcpInboundResponse


# ---------------------------------------------------------------------------
# Outbound message shapes (used by dispatcher / session handler)
# ---------------------------------------------------------------------------


@dataclass
class AcpOutboundResponse:
    """Successful JSON-RPC 2.0 response."""

    id: str | int
    result: BaseModel


@dataclass
class AcpOutboundError:
    """JSON-RPC 2.0 error response."""

    id: str | int | None
    code: int
    message: str


@dataclass
class AcpOutboundRequest:
    """Outgoing JSON-RPC 2.0 request (kernel → client)."""

    id: str | int
    method: str
    params: BaseModel


@dataclass
class AcpOutboundNotification:
    """Outgoing JSON-RPC 2.0 notification (kernel → client)."""

    method: str
    params: BaseModel


AcpOutbound = AcpOutboundResponse | AcpOutboundError | AcpOutboundRequest | AcpOutboundNotification


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


class AcpCodec:
    """Stateless JSON-RPC 2.0 codec for the ACP protocol stack.

    Implements :class:`kernel.routes.stack.ProtocolCodec`
    (structural typing — no base class needed).
    """

    # ------------------------------------------------------------------
    # Decode
    # ------------------------------------------------------------------

    def decode(self, raw: str) -> AcpMessage:
        """Parse an inbound frame.

        Raises
        ------
        ParseError
            The frame is not valid JSON.
        InvalidRequest
            The frame is valid JSON but not a valid JSON-RPC 2.0 object.
        """
        try:
            obj = orjson.loads(raw)
        except orjson.JSONDecodeError as exc:
            raise ParseError(f"JSON parse error: {exc}") from exc

        if not isinstance(obj, dict):
            raise InvalidRequest("JSON-RPC frame must be a JSON object")

        if obj.get("jsonrpc") != "2.0":
            raise InvalidRequest("Missing or wrong 'jsonrpc' field; expected '2.0'")

        has_id = "id" in obj
        has_method = "method" in obj

        params = obj.get("params") or {}
        if not isinstance(params, dict):
            raise InvalidRequest("'params' must be a JSON object if present")

        meta = params.pop("_meta", None)
        if meta is not None and "meta" not in params:
            params["meta"] = meta

        if has_method and has_id:
            return AcpInboundRequest(
                id=obj["id"],
                method=obj["method"],
                params=params,
                meta=meta,
            )
        elif has_method and not has_id:
            return AcpInboundNotification(
                method=obj["method"],
                params=params,
                meta=meta,
            )
        elif has_id and not has_method:
            return AcpInboundResponse(
                id=obj["id"],
                result=obj.get("result"),
                error=obj.get("error"),
            )
        else:
            raise InvalidRequest(
                "Frame must have 'method' (request/notification) or 'id' (response)"
            )

    # ------------------------------------------------------------------
    # Encode
    # ------------------------------------------------------------------

    def encode(self, msg: AcpOutbound) -> str:
        """Serialise an outbound message to a JSON string.

        Must not raise — if it does, that is a codec bug, not a
        client error.
        """
        # Use by_alias=True so all outgoing field names are camelCase
        # (ACP wire format requirement).
        if isinstance(msg, AcpOutboundResponse):
            frame: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": msg.id,
                "result": _wire_dump(msg.result),
            }
        elif isinstance(msg, AcpOutboundError):
            frame = {
                "jsonrpc": "2.0",
                "id": msg.id,
                "error": {"code": msg.code, "message": msg.message},
            }
        elif isinstance(msg, AcpOutboundRequest):
            frame = {
                "jsonrpc": "2.0",
                "id": msg.id,
                "method": msg.method,
                "params": _wire_dump(msg.params),
            }
        else:  # AcpOutboundNotification
            frame = {
                "jsonrpc": "2.0",
                "method": msg.method,
                "params": _wire_dump(msg.params),
            }
        return orjson.dumps(frame).decode()

    def encode_error(self, error: ProtocolError) -> str:
        """Format a ``ProtocolError`` as a JSON-RPC error frame.

        Used by the transport loop when ``decode`` raises.
        ``id`` is ``null`` because we never parsed a valid id.
        The error message is generic to avoid leaking internal details.
        """
        code = getattr(error, "code", -32603)
        # Use a generic message for -32603 to avoid leaking internals.
        if code == -32603:
            message = "Internal error"
        else:
            message = str(error) or "Protocol error"

        frame = {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": code, "message": message},
        }
        return orjson.dumps(frame).decode()


def _wire_dump(model: BaseModel) -> dict[str, Any]:
    """Dump an ACP model and map Pydantic ``meta`` fields to wire ``_meta``."""
    raw = orjson.loads(model.model_dump_json(exclude_none=True, by_alias=True))
    return _meta_to_wire(raw)


def _meta_to_wire(value: Any) -> Any:
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, child in value.items():
            wire_key = "_meta" if key == "meta" else key
            converted[wire_key] = _meta_to_wire(child)
        return converted
    if isinstance(value, list):
        return [_meta_to_wire(child) for child in value]
    return value
