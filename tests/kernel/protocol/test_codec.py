"""Unit tests for AcpCodec — JSON-RPC 2.0 decode / encode."""

from __future__ import annotations

import json

import pytest

from kernel.protocol.acp.codec import (
    AcpCodec,
    AcpInboundNotification,
    AcpInboundRequest,
    AcpInboundResponse,
    AcpOutboundError,
    AcpOutboundNotification,
    AcpOutboundResponse,
)
from kernel.protocol.interfaces.errors import InvalidRequest, ParseError


@pytest.fixture
def codec() -> AcpCodec:
    return AcpCodec()


# ---------------------------------------------------------------------------
# decode — happy paths
# ---------------------------------------------------------------------------


class TestDecodeRequest:
    def test_basic_request(self, codec: AcpCodec) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": 1},
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundRequest)
        assert msg.id == 1
        assert msg.method == "initialize"
        assert msg.params["protocolVersion"] == 1

    def test_string_id(self, codec: AcpCodec) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "abc",
                "method": "session/new",
                "params": {"cwd": "/tmp", "mcpServers": []},
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundRequest)
        assert msg.id == "abc"

    def test_meta_extracted(self, codec: AcpCodec) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/prompt",
                "params": {
                    "sessionId": "s1",
                    "prompt": [],
                    "_meta": {"traceparent": "00-abc-def-01"},
                },
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundRequest)
        assert msg.meta == {"traceparent": "00-abc-def-01"}
        assert msg.params["meta"] == {"traceparent": "00-abc-def-01"}
        assert "_meta" not in msg.params

    def test_empty_params(self, codec: AcpCodec) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/list",
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundRequest)
        assert msg.params == {}


class TestDecodeNotification:
    def test_basic_notification(self, codec: AcpCodec) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": "s1"},
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundNotification)
        assert msg.method == "session/cancel"

    def test_unknown_notification_decoded(self, codec: AcpCodec) -> None:
        """Unknown notifications should decode successfully; dispatcher ignores them."""
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "$/cancel_request",
                "params": {"requestId": 5},
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundNotification)


class TestDecodeResponse:
    def test_success_response(self, codec: AcpCodec) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "result": {"outcome": "allow_once"},
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundResponse)
        assert msg.id == 10
        assert msg.result == {"outcome": "allow_once"}
        assert msg.error is None

    def test_error_response(self, codec: AcpCodec) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "error": {"code": -32002, "message": "Not found"},
            }
        )
        msg = codec.decode(raw)
        assert isinstance(msg, AcpInboundResponse)
        assert msg.error == {"code": -32002, "message": "Not found"}


# ---------------------------------------------------------------------------
# decode — error paths
# ---------------------------------------------------------------------------


class TestDecodeErrors:
    def test_not_json(self, codec: AcpCodec) -> None:
        with pytest.raises(ParseError):
            codec.decode("not json {{{")

    def test_not_object(self, codec: AcpCodec) -> None:
        with pytest.raises(InvalidRequest):
            codec.decode("[1, 2, 3]")

    def test_wrong_jsonrpc_version(self, codec: AcpCodec) -> None:
        with pytest.raises(InvalidRequest):
            codec.decode(json.dumps({"jsonrpc": "1.0", "id": 1, "method": "x"}))

    def test_missing_jsonrpc(self, codec: AcpCodec) -> None:
        with pytest.raises(InvalidRequest):
            codec.decode(json.dumps({"id": 1, "method": "x"}))

    def test_no_method_no_id(self, codec: AcpCodec) -> None:
        with pytest.raises(InvalidRequest):
            codec.decode(json.dumps({"jsonrpc": "2.0"}))

    def test_params_not_object(self, codec: AcpCodec) -> None:
        with pytest.raises(InvalidRequest):
            codec.decode(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": [1, 2, 3],
                    }
                )
            )


# ---------------------------------------------------------------------------
# encode
# ---------------------------------------------------------------------------


class TestEncode:
    def test_encode_response(self, codec: AcpCodec) -> None:
        from pydantic import BaseModel

        class MyResult(BaseModel):
            session_id: str

        msg = AcpOutboundResponse(id=1, result=MyResult(session_id="abc"))
        raw = codec.encode(msg)
        obj = json.loads(raw)
        assert obj["jsonrpc"] == "2.0"
        assert obj["id"] == 1
        assert obj["result"]["session_id"] == "abc"

    def test_encode_error(self, codec: AcpCodec) -> None:
        msg = AcpOutboundError(id=2, code=-32601, message="Method not found")
        raw = codec.encode(msg)
        obj = json.loads(raw)
        assert obj["error"]["code"] == -32601
        assert obj["id"] == 2

    def test_encode_notification(self, codec: AcpCodec) -> None:
        from pydantic import BaseModel

        class Params(BaseModel):
            value: int

        msg = AcpOutboundNotification(method="session/update", params=Params(value=42))
        raw = codec.encode(msg)
        obj = json.loads(raw)
        assert "id" not in obj
        assert obj["method"] == "session/update"
        assert obj["params"]["value"] == 42

    def test_encode_meta_as_wire_meta(self, codec: AcpCodec) -> None:
        from kernel.protocol.acp.schemas.updates import AgentMessageChunk, SessionUpdateNotification
        from kernel.protocol.acp.schemas.content import AcpTextBlock

        msg = AcpOutboundNotification(
            method="session/update",
            params=SessionUpdateNotification(
                session_id="s1",
                update=AgentMessageChunk(
                    content=AcpTextBlock(text="hi"),
                    meta={"mustang.agent/example": True},
                ),
                meta={"traceparent": "00-abc-def-01"},
            ),
        )
        obj = json.loads(codec.encode(msg))
        assert obj["params"]["_meta"] == {"traceparent": "00-abc-def-01"}
        assert obj["params"]["update"]["_meta"] == {"mustang.agent/example": True}
        assert "meta" not in obj["params"]
        assert "meta" not in obj["params"]["update"]

    def test_encode_error_on_parse_error(self, codec: AcpCodec) -> None:
        exc = ParseError("bad json")
        raw = codec.encode_error(exc)
        obj = json.loads(raw)
        assert obj["id"] is None
        assert obj["error"]["code"] == -32700

    def test_encode_error_internal_generic_message(self, codec: AcpCodec) -> None:
        """Internal errors MUST NOT leak the specific message."""
        from kernel.protocol.interfaces.errors import InternalError

        exc = InternalError("secret db password revealed")
        raw = codec.encode_error(exc)
        obj = json.loads(raw)
        assert obj["error"]["message"] == "Internal error"
        assert "secret" not in raw
