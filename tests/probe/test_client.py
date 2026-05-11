"""Unit tests for probe.client — event parsing and response routing.

These tests exercise the pure-Python parsing helpers and the
ProbeClient._route_response dispatch logic without requiring a live kernel
connection.  All tests are synchronous or use simple asyncio.run(); no
WebSocket is opened.
"""

from __future__ import annotations

import asyncio

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ProbeError,
    ToolCallEvent,
    ToolCallUpdate,
    TurnComplete,
    UserChunk,
    _parse_permission,
    _parse_update,
)


# ---------------------------------------------------------------------------
# _parse_update
# ---------------------------------------------------------------------------


def _update_msg(session_id: str, update: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    }


def test_parse_agent_chunk() -> None:
    msg = _update_msg(
        "sess_1",
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello"}},
    )
    event = _parse_update(msg)
    assert isinstance(event, AgentChunk)
    assert event.text == "hello"


def test_parse_agent_chunk_empty_content() -> None:
    # content field absent — should not crash
    msg = _update_msg("sess_1", {"sessionUpdate": "agent_message_chunk"})
    event = _parse_update(msg)
    assert isinstance(event, AgentChunk)
    assert event.text == ""


def test_parse_user_chunk() -> None:
    msg = _update_msg(
        "sess_1",
        {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "hi"}},
    )
    event = _parse_update(msg)
    assert isinstance(event, UserChunk)
    assert event.text == "hi"


def test_parse_tool_call() -> None:
    msg = _update_msg(
        "sess_1",
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "call_001",
            "title": "Reading file",
            "kind": "read",
            "status": "pending",
        },
    )
    event = _parse_update(msg)
    assert isinstance(event, ToolCallEvent)
    assert event.tool_call_id == "call_001"
    assert event.title == "Reading file"
    assert event.kind == "read"
    assert event.status == "pending"


def test_parse_tool_call_defaults() -> None:
    # kind and status are optional
    msg = _update_msg(
        "sess_1",
        {"sessionUpdate": "tool_call", "toolCallId": "call_x"},
    )
    event = _parse_update(msg)
    assert isinstance(event, ToolCallEvent)
    assert event.kind == "other"
    assert event.status == "pending"


def test_parse_tool_call_update() -> None:
    msg = _update_msg(
        "sess_1",
        {"sessionUpdate": "tool_call_update", "toolCallId": "call_001", "status": "completed"},
    )
    event = _parse_update(msg)
    assert isinstance(event, ToolCallUpdate)
    assert event.tool_call_id == "call_001"
    assert event.status == "completed"


def test_parse_unknown_update_returns_none() -> None:
    # plan, mode_change, etc. are silently skipped
    msg = _update_msg("sess_1", {"sessionUpdate": "plan", "entries": []})
    assert _parse_update(msg) is None


# ---------------------------------------------------------------------------
# _parse_permission
# ---------------------------------------------------------------------------


def test_parse_permission() -> None:
    msg = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "session/request_permission",
        "params": {
            "sessionId": "sess_1",
            "toolCall": {"toolCallId": "call_001"},
            "options": [
                {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
            ],
        },
    }
    event = _parse_permission(msg)
    assert isinstance(event, PermissionRequest)
    assert event.req_id == 5
    assert event.session_id == "sess_1"
    assert event.tool_call_id == "call_001"
    assert len(event.options) == 2
    assert event.options[0]["optionId"] == "allow-once"


def test_parse_permission_no_options() -> None:
    msg = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "session/request_permission",
        "params": {
            "sessionId": "sess_2",
            "toolCall": {"toolCallId": "call_002"},
        },
    }
    event = _parse_permission(msg)
    assert event.options == []


# ---------------------------------------------------------------------------
# ProbeClient._route_response — tested via the event queue
# ---------------------------------------------------------------------------


def _make_client() -> ProbeClient:
    """Return a ProbeClient that is NOT connected (no WebSocket)."""
    return ProbeClient(port=8200)


def test_route_response_standard_future() -> None:
    """A normal RPC response resolves the pending future."""
    client = _make_client()
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        client._pending[1] = fut
        client._pending_methods[1] = "session/new"
        client._route_response({"jsonrpc": "2.0", "id": 1, "result": {"sessionId": "sess_x"}})
        assert fut.done()
        assert fut.result() == {"sessionId": "sess_x"}
    finally:
        loop.close()


def test_route_response_error_future() -> None:
    """An error response raises ProbeError on the pending future."""
    client = _make_client()
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        client._pending[2] = fut
        client._pending_methods[2] = "session/new"
        client._route_response(
            {"jsonrpc": "2.0", "id": 2, "error": {"code": -32600, "message": "bad request"}}
        )
        assert fut.done()
        exc = fut.exception()
        assert isinstance(exc, ProbeError)
        assert exc.code == -32600
    finally:
        loop.close()


def test_route_response_prompt_resolves_future() -> None:
    """A session/prompt response resolves the pending future (not the queue)."""
    client = _make_client()
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        client._pending[3] = fut
        client._pending_methods[3] = "session/prompt"
        client._route_response({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}})
        assert fut.done()
        assert fut.result() == {"stopReason": "end_turn"}
        # Must NOT put anything in the event queue.
        assert client._events.empty()
    finally:
        loop.close()


def test_route_response_prompt_error_resolves_future_with_exception() -> None:
    """A session/prompt error response sets an exception on the pending future."""
    client = _make_client()
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        client._pending[4] = fut
        client._pending_methods[4] = "session/prompt"
        client._route_response(
            {"jsonrpc": "2.0", "id": 4, "error": {"code": -32000, "message": "oops"}}
        )
        assert fut.done()
        exc = fut.exception()
        assert isinstance(exc, ProbeError)
        assert exc.code == -32000
        assert client._events.empty()
    finally:
        loop.close()


def test_route_response_unknown_id_is_noop() -> None:
    """Responses for unknown IDs don't raise."""
    client = _make_client()
    # Should not raise even though id=99 is not tracked
    client._route_response({"jsonrpc": "2.0", "id": 99, "result": {}})


def test_prompt_sends_client_turn_id_meta() -> None:
    async def _run() -> None:
        client = _make_client()
        sent: list[dict] = []
        client_turn_id = "11111111-1111-4111-8111-111111111111"

        async def _send(message: dict) -> None:
            sent.append(message)
            client._route_response(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"stopReason": "end_turn"},
                }
            )

        client._send = _send  # type: ignore[method-assign]
        events = [
            event
            async for event in client.prompt(
                "sess-1",
                "hello",
                client_turn_id=client_turn_id,
            )
        ]

        assert sent[0]["params"]["_meta"] == {
            "mustang.agent/clientTurnId": client_turn_id,
        }
        assert isinstance(events[-1], TurnComplete)

    asyncio.run(_run())


def test_session_lifecycle_helpers_send_expected_requests() -> None:
    async def _run() -> None:
        client = _make_client()
        sent: list[dict] = []

        async def _send(message: dict) -> None:
            sent.append(message)
            result = {}
            if message["method"] == "session/resume":
                result = {"configOptions": []}
            elif message["method"] == "session/list":
                result = {"sessions": []}
            client._route_response(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": result,
                }
            )

        client._send = _send  # type: ignore[method-assign]

        assert await client.resume_session("sess-1", cwd="/tmp") == {"configOptions": []}
        assert await client.close_session("sess-1") == {}
        assert await client.list_sessions(cwd="/tmp") == {"sessions": []}

        assert [message["method"] for message in sent] == [
            "session/resume",
            "session/close",
            "session/list",
        ]
        assert sent[0]["params"] == {"sessionId": "sess-1", "cwd": "/tmp"}
        assert sent[1]["params"] == {"sessionId": "sess-1"}
        assert sent[2]["params"] == {
            "cursor": None,
            "cwd": "/tmp",
            "meta": {"mustang.agent/includeEmpty": True},
        }

    asyncio.run(_run())
